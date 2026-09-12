# -*- coding: utf-8 -*-
"""scorer_bridge：DSL 表达式 → backtest SCORERS 双参签名的桥（合成数据对拍）。

钉住：构造期 fail-closed（ExprError 上抛）；计算期永不 raise（NaN/inf/空
切片/求值异常 → None）；score 取表达式末行值（手算对拍）；选择器口径
suggestion 恒「可买」（trade-sim 进场判定要求，见 _check_entry）。
"""

from __future__ import annotations

import ast
import hashlib
import time

import pandas as pd
import pytest

from custos.research.evolution import scorer_bridge as sb
from custos.research.evolution.expr_dsl import ExprError
from custos.research.evolution.scorer_bridge import expr_scorer_key, make_expr_scorer


def _df(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000.0] * n,
            "amount": [0.0] * n,
        }
    )


class TestMakeExprScorer:
    def test_hand_computed_last_value(self):
        # ROC(CLOSE,2) 末行 = close[-1]/close[-3]-1，手算对拍
        closes = [100.0, 101.0, 102.0, 105.0, 110.0]
        out = make_expr_scorer("ROC(CLOSE,2)")(_df(closes), "S000")
        assert out is not None
        assert out["score"] == pytest.approx(110.0 / 102.0 - 1.0)
        assert out["suggestion"] == "可买"  # 纯选择器口径（同 alpha101 注释）
        assert out["aux"] == {"expr": "ROC(CLOSE,2)"}
        assert out["components"] == {}

    def test_key_is_sha1_prefix(self):
        want = "expr_" + hashlib.sha1("ROC(CLOSE,5)".encode("utf-8")).hexdigest()[:8]
        assert expr_scorer_key("ROC(CLOSE,5)") == want
        assert expr_scorer_key("ROC(CLOSE,6)") != want  # 表达式差异进键名

    def test_construction_fail_closed(self):
        with pytest.raises(ExprError):
            make_expr_scorer("EMA(CLOSE,5)")  # 非白名单函数，启动期就炸
        with pytest.raises(ExprError):
            make_expr_scorer("")  # 空表达式同样 fail-closed

    def test_nan_tail_returns_none(self):
        # MA(CLOSE,10) 只有 5 行 → 末行 NaN → None（不参与排序）
        assert make_expr_scorer("MA(CLOSE,10)")(_df([100.0] * 5), "S000") is None

    def test_empty_slice_returns_none(self):
        s = make_expr_scorer("ROC(CLOSE,5)")
        assert s(_df([]), "S000") is None
        assert s(None, "S000") is None

    def test_inf_returns_none(self):
        # 末行 REF 值为 0 → 除零 inf → None（不 raise）
        s = make_expr_scorer("1/REF(CLOSE,1)")
        assert s(_df([100.0, 0.0, 101.0]), "S000") is None

    def test_compute_exception_swallowed(self):
        # df 缺 close 列 → evaluate 抛 ExprError → 计算期吞掉返回 None
        s = make_expr_scorer("ROC(CLOSE,5)")
        df = _df([100.0] * 30).drop(columns=["close"])
        assert s(df, "S000") is None

    def test_parse_only_at_construction(self, monkeypatch):
        """perf 钉测：scorer 闭包持有已校验 AST，逐调用不重 parse 字符串。"""
        parse_calls = []
        orig_parse = sb.parse

        def spy_parse(expr):
            parse_calls.append(expr)
            return orig_parse(expr)

        monkeypatch.setattr(sb, "parse", spy_parse)
        s = make_expr_scorer("ROC(CLOSE,2)")
        assert parse_calls == ["ROC(CLOSE,2)"]  # 构造期 parse 一次（fail-closed）

        eval_arg_types = []
        orig_eval = sb.evaluate

        def spy_eval(expr, df):
            eval_arg_types.append(type(expr))
            return orig_eval(expr, df)

        monkeypatch.setattr(sb, "evaluate", spy_eval)
        df = _df([100.0, 101.0, 102.0])
        s(df, "S000")
        s(df, "S000")
        assert parse_calls == ["ROC(CLOSE,2)"]  # 计算期没有重 parse
        assert eval_arg_types == [ast.Expression, ast.Expression]  # 闭包持有 AST


# ---------- 预计算旁路（v0.212 / TODO #75）：逐位等价 + 每股只算一次 + 性能 ----------


def _df_rand(n: int, seed: int = 0) -> pd.DataFrame:
    """确定性随机形态的 bars（固定种子 RandomState，非恒定列）。"""
    import numpy as np

    rng = np.random.RandomState(seed)
    rets = rng.normal(0.0005, 0.02, n)
    closes = list(100 * np.exp(np.cumsum(rets)))
    vols = list(rng.uniform(1e5, 1e7, n))
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": vols,
            "amount": [0.0] * n,
        }
    )


class TestExprPrecompute:
    def test_pointwise_identical_per_bar(self):
        # 逐 bar 直算 vs 预计算点查：逐位一致（含 None 位置，病态表达式）
        from custos.research.evolution.scorer_bridge import expr_scorer_precompute

        expr = "(1-2*TS_RANK(close,5))*TS_RANK(volume,10)"
        df = _df_rand(220)
        scorer = make_expr_scorer(expr)
        pre = expr_scorer_precompute(expr)(df)
        assert pre is not None
        for i in range(len(df)):
            sl = df.iloc[: i + 1]  # 热循环口径：从第 0 根开始的前缀切片
            a = scorer(sl, "X")  # 旧路径：全量重算
            b = scorer(sl, "X", pre=pre)  # 新路径：O(1) 点查询
            if a is None or b is None:
                assert a is None and b is None, f"位置 {i} 不一致: {a} vs {b}"
            else:
                assert a["score"] == b["score"], (
                    f"位置 {i}: {a['score']} != {b['score']}"
                )

    def test_evaluate_called_once_per_stock(self, monkeypatch):
        # spy 断言：预计算每股只算一次全序列；带 pre 的逐 bar 调用零 evaluate
        import custos.research.evolution.scorer_bridge as sb
        from custos.research.evolution.scorer_bridge import expr_scorer_precompute

        calls = []
        real_eval = sb.evaluate

        def spy(tree, df):
            calls.append(len(df))
            return real_eval(tree, df)

        monkeypatch.setattr(sb, "evaluate", spy)
        scorer = make_expr_scorer("ROC(close,5)")
        df = _df_rand(120, seed=1)
        pre = expr_scorer_precompute("ROC(close,5)")(df)
        assert calls == [120]  # 预计算一次
        for i in range(30, len(df)):
            scorer(df.iloc[: i + 1], "X", pre=pre)
        assert calls == [120]  # 逐 bar 零新增
        scorer(df.iloc[:60], "X")  # pre=None 回退旧路径（非前缀切片安全）
        assert calls == [120, 60]

    def test_precompute_failure_falls_back(self):
        # 预计算异常 → None → scorer 回退逐切片重算（行为与旧版一致）
        from custos.research.evolution.scorer_bridge import expr_scorer_precompute

        pre_fn = expr_scorer_precompute("ROC(close,5)")
        assert pre_fn(pd.DataFrame()) is None  # 空 df → None（不 raise）
        scorer = make_expr_scorer("ROC(close,5)")
        df = _df_rand(60, seed=2)
        assert scorer(df, "X", pre=None) is not None  # 旧路径照跑

    def test_precompute_perf_ratio(self):
        # 性能钉测：逐 bar 点查询总耗时 < 逐 bar 直算的 1/20（600 交易日尺度）。
        # 全量套件负载下定时会抖：预热 + best-of-3 取最小值（比阈值防 CI 抖动
        # 更稳的是比率断言本身——两边同负载跑，比率对调度噪声稳健）。
        from custos.research.evolution.scorer_bridge import expr_scorer_precompute

        expr = "(1-2*TS_RANK(close,5))*TS_RANK(volume,10)"
        df = _df_rand(600, seed=3)
        scorer = make_expr_scorer(expr)

        def _direct():
            for i in range(30, len(df)):
                scorer(df.iloc[: i + 1], "X")

        pre = expr_scorer_precompute(expr)(df)

        def _pre_path():
            for i in range(30, len(df)):
                scorer(df.iloc[: i + 1], "X", pre=pre)

        _direct()
        _pre_path()  # 预热（缓存/分支预测）
        t_direct = min(_timeit(_direct) for _ in range(3))
        t_pre = min(_timeit(_pre_path) for _ in range(3))
        assert t_pre < t_direct / 20, (
            f"预计算路径 {t_pre * 1000:.1f}ms vs 直算 {t_direct * 1000:.1f}ms"
        )


def _timeit(fn):
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0
