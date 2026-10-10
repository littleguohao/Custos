# -*- coding: utf-8 -*-
"""成本副读数（owner 方法论 review #6，v0.329）钉测。

换成本档 = 逐笔 ret 平移（cost_bps 是往返总成本，evaluate_trades 从 ret
直扣）⇒ 副读数解析重算、不重跑引擎；Δ/绝对 margin 翻号 ⇒ flip=True。
"""

from __future__ import annotations

from custos.research.cost_sensitivity import (
    cost_side_block,
    margin_block,
    shift_cost,
)


def _mk(code: str, day: str, ret: float) -> dict:
    return {
        "code": code,
        "entry_date": day,
        "ret": ret,
        "reason": "stop",
        "r_multiple": ret / 0.02,
        "holding": 5,
    }


def test_shift_cost_arithmetic_and_no_mutation():
    tr = [_mk("a", "2022-01-01", 0.01)]
    shifted = shift_cost(tr, 25.0)
    assert shifted[0]["ret"] == 0.01 - 0.0025
    assert tr[0]["ret"] == 0.01  # 原 list 不动
    assert shift_cost(tr, 0.0)[0]["ret"] == 0.01


def test_shift_cost_keeps_trades_without_ret():
    tr = [{"code": "x", "entry_date": "2022-01-01", "reason": "stop"}]
    assert shift_cost(tr, 25.0)[0].get("ret") is None


def test_margin_block_known_values():
    tr = [_mk("a", "2022-01-01", 0.01), _mk("b", "2022-01-02", -0.005)]
    blk = margin_block(tr)
    assert blk["n"] == 2
    assert blk["win_rate"] == 0.5
    assert blk["payoff_ratio"] == 2.0
    # margin = wr − 1/(1+payoff) = 0.5 − 1/3
    assert abs(blk["margin"] - (0.5 - 1 / 3)) < 1e-12


def test_cost_side_block_flip_absolute():
    # 25→50bps（每笔再扣 25bps）：[0.01,−0.005] → [0.0075,−0.0075]
    # margin 0.1667 → 0.0 ⇒ 绝对口径翻号
    tr = [_mk("a", "2022-01-01", 0.01), _mk("b", "2022-01-02", -0.005)]
    blk = cost_side_block({"x": tr}, base_bps=25.0, deltas={"x_vs_0": ("x", None)})
    d = blk["deltas"]["x_vs_0"]
    assert d["base"] > 0 and d["side"] == 0.0 and d["flip"] is True
    assert blk["base_bps"] == 25.0 and blk["side_bps"] == 50.0


def test_cost_side_block_no_flip_when_edge_fat():
    tr = [_mk("a", "2022-01-01", 0.10), _mk("b", "2022-01-02", -0.01)]
    blk = cost_side_block({"x": tr}, base_bps=25.0, deltas={"x_vs_0": ("x", None)})
    assert blk["deltas"]["x_vs_0"]["flip"] is False


def test_cost_side_block_delta_between_arms():
    # 两臂 margin 接近：A 0.1667 / B 0.15 级——成本平移对两边大体抵消，
    # 但构造 B  payoff 不同使 Δ 在 side 档翻号
    a = [_mk("a", "2022-01-01", 0.01), _mk("b", "2022-01-02", -0.005)]
    b = [_mk("c", "2022-01-01", 0.02), _mk("d", "2022-01-02", -0.011)]
    blk = cost_side_block(
        {"A": a, "B": b}, base_bps=25.0, deltas={"A_minus_B": ("A", "B")}
    )
    d = blk["deltas"]["A_minus_B"]
    assert d["base"] is not None and d["side"] is not None
    assert isinstance(d["flip"], bool)
    assert set(blk["arms"]) == {"A", "B"}


def test_cost_side_block_empty_trades():
    blk = cost_side_block({"e": []}, base_bps=25.0, deltas={"e_vs_0": ("e", None)})
    assert blk["arms"]["e"]["n"] == 0
    assert blk["arms"]["e"]["margin"] is None
    d = blk["deltas"]["e_vs_0"]
    assert d["base"] is None and d["side"] is None and d["flip"] is False


def test_cost_side_block_same_cost_no_flip():
    tr = [_mk("a", "2022-01-01", 0.01), _mk("b", "2022-01-02", -0.005)]
    blk = cost_side_block(
        {"x": tr}, base_bps=25.0, side_bps=25.0, deltas={"x_vs_0": ("x", None)}
    )
    d = blk["deltas"]["x_vs_0"]
    assert d["base"] == d["side"] and d["flip"] is False
