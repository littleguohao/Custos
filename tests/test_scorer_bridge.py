# -*- coding: utf-8 -*-
"""scorer_bridge：DSL 表达式 → backtest SCORERS 双参签名的桥（合成数据对拍）。

钉住：构造期 fail-closed（ExprError 上抛）；计算期永不 raise（NaN/inf/空
切片/求值异常 → None）；score 取表达式末行值（手算对拍）；选择器口径
suggestion 恒「可买」（trade-sim 进场判定要求，见 _check_entry）。
"""

from __future__ import annotations

import hashlib

import pandas as pd
import pytest

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
