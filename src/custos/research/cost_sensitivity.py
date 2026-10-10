# -*- coding: utf-8 -*-
"""成本副读数（owner 方法论 review #6，v0.329）：25/50bps 双报 + 翻号标记。

各研究终端的成本固定 25bps，没有考虑流动性与冲击成本。本模块给出**解析式**
副读数（不重跑引擎）：``cost_bps`` 是往返总成本（``evaluate_trades`` 从每笔
``ret`` 直接扣 ``cost_bps/1e4``），所以换成本档 = 逐笔 ``ret`` 平移——

    ret@side = ret@base − (side_bps − base_bps)/1e4

在主读数之外报一份 50bps 下的 margin 块；Δ（变体−参照）或绝对 margin
跟着成本翻号 ⇒ ``flip=True``，报告标「成本敏感」。判读纪律：Δ 口径下
成本对两边同向平移、大部分抵消，翻号罕见；绝对口径（R40 C2 margin>0、
pre2019 终审的薄 margin）才是本副读数的主战场——翻号 ⇒ 结论降权。

口径边界（与主读数一致的已知缺口）：margin = 全候选交易（collect_all），
只报 margin/胜率/盈亏比/n——expectancy_R 的 R 分母（初始止损距离）不随
成本变、分子平移后口径会混，副读数不报。
"""

from __future__ import annotations

import argparse
from typing import Any, Optional

DEFAULT_SIDE_BPS = 50.0


def shift_cost(trades: list[dict[str, Any]], delta_bps: float) -> list[dict[str, Any]]:
    """逐笔 ret 平移 ``delta_bps`` 的**副本**（原 list 不动）。

    delta>0 = 成本变贵（ret 变小）。缺 ``ret`` 键的交易原样保留（n 仍计入，
    summarize 口径与主读数相同）。
    """
    adj = delta_bps / 1e4
    out: list[dict[str, Any]] = []
    for t in trades:
        if t.get("ret") is None:
            out.append(dict(t))
        else:
            out.append(dict(t, ret=t["ret"] - adj))
    return out


def margin_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """margin/胜率/盈亏比/n——与 exit_c5_terminal._margin_of 同公式同形状。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import strategy_grid as sg  # noqa: PLC0415

    tsum = bt.summarize_trades(trades)
    return {
        "margin": sg._margin(
            {"win": tsum.get("win_rate"), "payoff": tsum.get("payoff_ratio")}
        ),
        "win_rate": tsum.get("win_rate"),
        "payoff_ratio": tsum.get("payoff_ratio"),
        "n": tsum.get("n"),
    }


def cost_side_block(
    named_trades: dict[str, list[dict[str, Any]]],
    *,
    base_bps: float,
    side_bps: float = DEFAULT_SIDE_BPS,
    deltas: Optional[dict[str, tuple[str, Optional[str]]]] = None,
) -> dict[str, Any]:
    """成本副读数块：每个具名交易集在 side_bps 下的 margin 块 + 具名 Δ 翻号。

    ``deltas``：{Δ名: (臂A, 臂B)}——Δ = margin(A) − margin(B)；臂B=None
    ⇒ Δ = margin(A) − 0（绝对 margin 判据，如 R40 C2 margin>0）。
    ``flip``：主成本下 Δ>0 与副成本下 Δ>0 不一致（「结论跟着成本翻号」）；
    任一侧 None（空交易集）⇒ flip=False 如实记 None。
    """
    arms = {
        name: margin_block(shift_cost(trades, side_bps - base_bps))
        for name, trades in named_trades.items()
    }
    base_margins = {
        name: margin_block(trades)["margin"] for name, trades in named_trades.items()
    }
    delta_rows: dict[str, dict[str, Any]] = {}
    for dname, (a, b) in (deltas or {}).items():

        def _margins(source: dict[str, Any], arm: str) -> Optional[float]:
            row = source.get(arm) or {}
            return row.get("margin") if isinstance(row, dict) else None

        m_base_a = base_margins.get(a)
        m_base_b = base_margins.get(b) if b is not None else 0.0
        m_side_a = _margins(arms, a)
        m_side_b = _margins(arms, b) if b is not None else 0.0
        d_base = None if m_base_a is None or m_base_b is None else m_base_a - m_base_b
        d_side = None if m_side_a is None or m_side_b is None else m_side_a - m_side_b
        flip = (
            d_base is not None and d_side is not None and (d_base > 0) != (d_side > 0)
        )
        delta_rows[dname] = {"base": d_base, "side": d_side, "flip": flip}
    return {
        "base_bps": base_bps,
        "side_bps": side_bps,
        "arms": arms,
        "deltas": delta_rows,
        "note": "成本副读数（review #6）：side 档=逐笔 ret 平移 (side−base)/1e4 "
        "解析重算（不重跑引擎）；flip=True ⇒ 结论成本敏感、判读降权",
    }


def main(argv: Optional[list[str]] = None) -> int:
    """诊断入口：说明与当前默认成本档（不碰数据）。"""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args(argv)
    print(
        "cost_sensitivity：副读数档 side=50bps（Δ=+25bps 逐笔平移解析重算）；"
        "接线终端在报告带 cost_sensitivity 块，flip=True ⇒ 成本敏感"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
