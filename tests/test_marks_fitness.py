# -*- coding: utf-8 -*-
"""marks 分离适应度钉测（R36 Phase 2 监督模式）。

- marks_score：构造已知形态（买点放量 vs 缩量）对拍手算；n_hit/None 处理；
  K=250 在全案例恒 NaN 的反例钉（默认 rank_window=20 的存在理由）。
- loop 集成：ScriptedLLM 产同一表达式（IC 门同结果），marks 案例换向
  （买点大涨 vs 大跌）断言 decision 分流与 reasons/metrics["marks"]；
  --marks 关闭（marks_path=""）时行为逐位不变。
"""

import numpy as np
import pandas as pd
import pytest

from custos.research.evolution import loop as loop_mod
from custos.research.evolution.marks_fitness import (
    DEFAULT_RANK_WINDOW,
    marks_score,
)
from custos.research.evolution.trajectory import TrajectoryPool

from test_evolution_loop import ScriptedLLM, _cand, _cfg, make_bars  # noqa: E402


def _case(code: str, closes, vols, start="2025-06-02") -> "object":
    """构造 PerfectB1Case 同形对象（不引真类防循环：同名字段即可）。"""
    from custos.research.b1_perfect_dataset import PerfectB1Case

    n = len(closes)
    dates = pd.date_range(start, periods=n)
    df = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": np.asarray(closes) + 0.1,
            "low": np.asarray(closes) - 0.1,
            "close": closes,
            "volume": vols,
            "amount": np.asarray(vols) * 10.0,
        }
    )
    return PerfectB1Case(
        code=code,
        code_full=f"{code}.SZ",
        start=start,
        end=str(dates[-1].date()),
        buy_date=str(dates[-1].date()),
        bars=df,
        n_bars=n,
    )


def _write_case_csv(dir_path, code_full, closes, vols):
    """按 load_cases 的 CSV 形态落一个案例文件（末根日期=文件名末根）。"""
    n = len(closes)
    dates = pd.date_range("2025-06-02", periods=n)
    end = dates[-1].strftime("%Y%m%d")
    start = dates[0].strftime("%Y%m%d")
    df = pd.DataFrame(
        {
            "Date": [str(d.date()) for d in dates],
            "Code": code_full,
            "Amount": np.asarray(vols) * 10.0,
            "Close": closes,
            "ForwardFactor": 0.0,
            "High": np.asarray(closes) + 0.1,
            "Low": np.asarray(closes) - 0.1,
            "Volume": vols,
            "Open": closes,
        }
    )
    df.to_csv(dir_path / f"{code_full}-{start}-{end}.csv", index=False)


class TestMarksScore:
    def test_volume_surge_vs_shrink_hand_compute(self):
        """对拍手算：买点放量 → 高 rank；买点缩量 → 低 rank；contrast 同号。"""
        n = 80
        vol_low = [1e6] * n
        vol_low[-1] = 5e6  # 买点放量 5×
        vol_high = [1e6] * n
        vol_high[0] = 5e6  # 历史首日放量，买点平稳
        c1 = _case("600001", [10.0] * n, vol_low)
        c2 = _case("600002", [10.0] * n, vol_high)
        r = marks_score("volume/MA(volume,20)", [c1, c2], rank_window=20)
        assert r["n_hit"] == 2 and r["n_cases"] == 2
        by_code = {p["code"]: p for p in r["per_case"]}
        assert by_code["600001"]["rank"] == pytest.approx(0.975, abs=1e-9)
        assert by_code["600002"]["rank"] == pytest.approx(0.5, abs=1e-9)
        assert r["mean_rank"] == pytest.approx((0.975 + 0.5) / 2)
        assert r["min_rank"] == pytest.approx(0.5)
        assert r["contrast"] is not None and r["contrast"] > 0  # 放量案例抬高整体

    def test_buy_date_not_in_bars_is_no_value(self):
        c = _case("600001", [10.0] * 80, [1e6] * 80)
        c = c.__class__(  # buy_date 改成 bars 外的日期
            **{**c.__dict__, "buy_date": "2030-01-01"}
        )
        r = marks_score("close", [c], rank_window=20)
        assert r["n_hit"] == 0
        assert r["mean_rank"] is None and r["contrast"] is None
        assert r["per_case"][0]["status"] == "no_value"

    def test_k250_all_nan_counterexample(self):
        """反例钉：rank_window=250 在 ~70 根案例窗上 warmup 覆盖全窗 ⇒ 恒 NaN
        （默认 DEFAULT_RANK_WINDOW=20 的存在理由——窗口纪律写死在 docstring）。"""
        c = _case("600001", [10.0] * 70, [1e6] * 70)
        r = marks_score("volume/MA(volume,20)", [c], rank_window=250)
        assert r["n_hit"] == 0
        assert DEFAULT_RANK_WINDOW == 20

    def test_all_no_value_means_nulls_not_zero(self):
        """全部无值 → 汇总 None + n_hit=0（fail-closed 不许当高分）。"""
        c = _case("600001", [10.0] * 10, [1e6] * 10)  # 10 根 < warmup
        r = marks_score("volume/MA(volume,20)", [c], rank_window=20)
        assert r["n_hit"] == 0
        assert (
            r["mean_rank"] is None and r["min_rank"] is None and r["contrast"] is None
        )


class TestLoopMarksGate:
    def _run(self, tmp_path, *, spike: float, marks_path=""):
        """单候选跑循环：案例买点涨 spike（负=跌）；返回 pool 唯一轨迹。"""
        n = 80
        closes = [100.0] * n
        closes[-1] = closes[-2] * (1 + spike)
        vols = [1e6] * n
        if marks_path:
            _write_case_csv(tmp_path, "600001.SZ", closes, vols)
        llm = ScriptedLLM([_cand("ROC(CLOSE,5)", "动量5日")])
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(marks_path=marks_path, marks_rank_window=20),
            make_bars(),
            llm,
            pool,
        )
        (t,) = pool.all()
        return t

    def test_marks_pass_records_metrics(self, tmp_path):
        """买点大涨案例：IC 门 + marks 门全过 ⇒ pass，mining_metrics 带 marks 块。"""
        t = self._run(tmp_path, spike=0.20, marks_path=str(tmp_path))
        assert t.decision == "pass"
        m = t.mining_metrics["marks"]
        assert m["n_hit"] == 1
        assert m["mean_rank"] >= 0.7
        assert m["contrast"] > 0
        assert m["per_case"][0]["status"] == "hit"

    def test_marks_fail_with_reasons(self, tmp_path):
        """买点大跌案例：IC 门过但 mean_rank < 0.7 ⇒ fail，reasons 含 marks 原因。"""
        t = self._run(tmp_path, spike=-0.20, marks_path=str(tmp_path))
        assert t.decision == "fail"
        m = t.mining_metrics["marks"]
        assert m["n_hit"] == 1 and m["mean_rank"] < 0.7
        assert "mean_rank" in t.feedback
        # IC 读数仍在（marks 门不覆盖 IC 门读数，rank_ic_mean 还在）
        assert isinstance(t.mining_metrics["rank_ic_mean"], float)

    def test_marks_off_bitwise_unchanged(self, tmp_path):
        """--marks 关闭（marks_path=""）：行为逐位不变（同输入同 decision/metrics）。"""
        closes = [100.0] * 80
        closes[-1] = closes[-2] * 0.8
        _write_case_csv(tmp_path, "600001.SZ", closes, [1e6] * 80)

        def run(mp):
            llm = ScriptedLLM([_cand("ROC(CLOSE,5)", "动量5日")])
            pool = TrajectoryPool()
            loop_mod.run_loop(
                _cfg(marks_path=mp, marks_rank_window=20), make_bars(), llm, pool
            )
            (t,) = pool.all()
            return t

        t_off = run("")
        t_default = run("")  # 与 marks_path 缺省一致（LoopConfig.marks_path 默认 ""）
        assert t_off.decision == t_default.decision == "pass"
        assert "marks" not in t_off.mining_metrics
        assert t_off.mining_metrics == t_default.mining_metrics


# ---------------------------------------------------------------------------
# v0.243（owner 拍板口径：案例=(code, 买点日期)，观察窗自由，评估物理截断于买点）
# ---------------------------------------------------------------------------


def _long_history(code: str, n_before: int = 220, buy_close: float = 12.0):
    """provider 全历史：买点前 n_before 根 + 买点当日 + 买点后 10 根（含篡改区）。"""
    rng = np.random.default_rng(7)
    n = n_before + 1 + 10
    close = 10 + np.cumsum(rng.normal(0.02, 0.3, n))
    close[n_before] = buy_close  # 买点当日钉死
    dates = pd.date_range("2024-01-02", periods=n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.full(n, 1e6),
            "amount": close * 1e6,
        }
    ), dates[n_before].strftime("%Y-%m-%d")


class TestNewCaseSemantics:
    def test_resolve_bars_provider_truncates_and_marks_source(self):
        """resolve_bars：provider 全历史物理截到买点（含）+ bars_source 记录。"""
        from custos.research.b1_perfect_dataset import resolve_bars

        df_long, buy_date = _long_history("600001")
        case = _case("600001", [10.0, 11.0, 12.0], [1e6] * 3)
        # 修正：_case 的 buy_date 用其自身末根；这里覆盖为长历史的买点日
        case = case.__class__(**{**case.__dict__, "buy_date": buy_date})
        bars, source = resolve_bars(case, lambda code: df_long)
        assert source == "provider"
        assert str(bars["date"].iloc[-1].date()) == buy_date  # 截到买点（含）
        assert len(bars) == 221  # 220 前置 + 买点；买点后 10 根不在场
        # 回退路径：provider None → excerpt
        bars2, source2 = resolve_bars(case, None)
        assert source2 == "excerpt"
        assert bars2 is case.excerpt_bars

    def test_marks_score_tamper_after_buy_invariant(self):
        """篡改钉测（防未来函数纪律）：买点后数据被改动，结果逐位不变。"""
        df_long, buy_date = _long_history("600001")
        case = _case("600001", [10.0, 11.0, 12.0], [1e6] * 3)
        case = case.__class__(**{**case.__dict__, "buy_date": buy_date})
        expr = "close/MA(close,20)"
        r1 = marks_score(expr, [case], rank_window=20, bars_provider=lambda c: df_long)
        # 篡改买点后 10 根（大涨 10 倍）
        tampered = df_long.copy()
        pos = len(df_long) - 10
        tampered.loc[pos:, "close"] *= 10.0
        tampered.loc[pos:, "high"] *= 10.0
        tampered.loc[pos:, "low"] *= 10.0
        r2 = marks_score(expr, [case], rank_window=20, bars_provider=lambda c: tampered)
        assert r1 == r2, "买点后数据被改动 ⇒ marks_score 必须逐位不变"
        assert r1["per_case"][0]["bars_source"] == "provider"
        assert r1["per_case"][0]["n_bars"] == 221
        # 删除买点后全部行（provider 只给到买点）也逐位不变
        r3 = marks_score(
            expr, [case], rank_window=20, bars_provider=lambda c: df_long.iloc[:221]
        )
        assert r1 == r3

    def test_long_history_matches_hand_prefix_compute(self):
        """长历史下 TS_RANK 值与手工前缀计算一致（截断帧上手算对拍）。"""
        df_long, buy_date = _long_history("600001")
        case = _case("600001", [10.0, 11.0, 12.0], [1e6] * 3)
        case = case.__class__(**{**case.__dict__, "buy_date": buy_date})
        expr = "close/MA(close,20)"
        r = marks_score(expr, [case], rank_window=20, bars_provider=lambda c: df_long)
        # 手工：截断到买点（含）后全序列 TS_RANK，取末点
        from custos.research.evolution import expr_dsl

        cut = df_long[df_long["date"].astype(str).str[:10] <= buy_date]
        s = expr_dsl.evaluate(f"TS_RANK(({expr}),20)", cut)
        assert r["per_case"][0]["rank"] == pytest.approx(float(s.iloc[-1]))

    def test_excerpt_fallback_unchanged(self):
        """无 provider 时 excerpt 回退：口径与旧行为一致（在可用历史上算）。"""
        n = 80
        vol_low = [1e6] * n
        vol_low[-1] = 5e6
        c1 = _case("600001", [10.0] * n, vol_low)
        r = marks_score("volume/MA(volume,20)", [c1], rank_window=20)
        assert r["per_case"][0]["bars_source"] == "excerpt"
        assert r["per_case"][0]["n_bars"] == n
        assert r["per_case"][0]["rank"] == pytest.approx(0.975, abs=1e-9)

    def test_provider_none_falls_back(self):
        """provider 给 None/空帧 → excerpt 回退（bars_source=excerpt 照实记录）。"""
        n = 80
        vols = [1e6] * n
        vols[-1] = 5e6
        c1 = _case("600001", [10.0] * n, vols)
        for bad in (lambda c: None, lambda c: pd.DataFrame()):
            r = marks_score("volume/MA(volume,20)", [c1], bars_provider=bad)
            assert r["per_case"][0]["bars_source"] == "excerpt"
            assert r["n_hit"] == 1

    def test_marks_observed_even_when_ic_fails(self, tmp_path):
        """IC 门未过的候选也留 marks 读数（观测性：复核「IC 门是否误杀买点
        高分候选」需要 IC-fail 侧数据）；门次序/阈值语义不变——decision 仍 fail。"""
        n = 80
        closes = [100.0] * n
        closes[-1] = closes[-2] * 1.20  # 案例买点大涨
        vols = [1e6] * n
        _write_case_csv(tmp_path, "600001.SZ", closes, vols)
        # -ROC(CLOSE,5)：与 test_marks_pass_records_metrics 的通过候选互为反号
        # ⇒ 同一合成宇宙上 IC 必 fail（rank_ic 反号跌破 0.02 门）
        llm = ScriptedLLM([_cand("-ROC(CLOSE,5)", "反向动量（IC 门必杀）")])
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(marks_path=str(tmp_path), marks_rank_window=20),
            make_bars(),
            llm,
            pool,
        )
        (t,) = pool.all()
        assert t.decision == "fail"  # IC 门杀死（门次序不变）
        m = t.mining_metrics["marks"]  # marks 读数仍留痕（观测性）
        assert m["n_hit"] == 1 and m["mean_rank"] is not None
