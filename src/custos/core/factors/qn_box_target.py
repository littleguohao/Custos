# -*- coding: utf-8 -*-
"""QN·1.3 系数箱体目标位（骑牛登山体系，规则出处
`governance/strategy/qn/02_space_targets.md` §一、`qn/01_general.md` §三）。

源规则（经验规律，未回测）：
- 以一波走势最低价 ×1.3 构建箱体上沿 = 目标压力位；
  向上平移半格（×1.15）后的 50% 位置也是压力位。
- 大波段用 1.3，小波段用 1.26/1.2（混用失真）。
- 到目标位附近若 MACD 死叉/反切/滞涨 → 卖出；本因子**只作空间度量**，
  不判买卖（卖出判定交给持仓链/出场研究）。

确定性转译（待回测）：
- 波段低点 = 近 ``QN_BASE_WIN`` 根最低价的最小值
- 输出：目标位（1.3/1.26/半格 1.15）、当前价距目标幅度、是否进入目标压力区
  （距目标 ≤ ``QN_NEAR_PCT``）、是否已站上 半格位

state 类：空间度量工具。绝不 raise。
"""

from __future__ import annotations

from typing import Any

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays

FACTOR: dict[str, Any] = {
    "id": "qn_box_target",
    "name": "QN·1.3 系数箱体目标位（空间度量）",
    "kind": "state",
    "status": "untested",  # 新实现未回测（骑牛体系口径 + 合成用例）
    "evidence": "",
    "note": "规则出处 governance/strategy/qn/02_space_targets.md §一；波段低点×1.3=目标位、×1.15=半格压力位；只作空间度量不判买卖",
    "min_bars": 60,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_BASE_WIN = 60  # 待回测：波段低点观察窗（源规则「近 20 或 60 日波段低点」取大者）
QN_COEF_BIG = 1.3  # 待回测：大波段系数（源规则 1.3）
QN_COEF_SMALL = 1.26  # 待回测：小波段系数（源规则 1.26/1.2 取 1.26）
QN_COEF_HALF = 1.15  # 待回测：半格压力位（上沿 1.3 平移半格 = 低点 ×1.15）
QN_NEAR_PCT = 3.0  # 待回测：目标压力区容差%（距目标位 ≤ 此值视为进入压力区）


def detect(df, code: str = "") -> dict[str, Any]:
    """1.3 系数箱体目标位度量。绝不 raise。

    返回键：
        hit               进入目标压力区（现价距 1.3 目标 ≤ QN_NEAR_PCT 或已越过）
        base_low          波段低点（参照价）
        target_13/126/115 三档目标/压力位
        dist_target_pct   现价距 1.3 目标的剩余幅度%（负值=已越过）
        above_half_grid   现价是否站上半格（×1.15）压力位
    """
    try:
        close, _high, low, _vol = _ohlcv_arrays(df)
        n = len(df)
        if n < FACTOR["min_bars"]:
            return {
                "available": False,
                "hit": False,
                "reason": f"少于{FACTOR['min_bars']}根K线（{n}）",
            }
        base = float(low[-QN_BASE_WIN:].min())
        last = float(close[-1])
        if base <= 0:
            return {"available": False, "hit": False, "reason": "低点非正"}
        t13 = base * QN_COEF_BIG
        dist = (t13 / last - 1) * 100 if last else 0.0
        return {
            "available": True,
            "hit": bool(dist <= QN_NEAR_PCT),
            "base_low": round(base, 4),
            "target_13": round(t13, 4),
            "target_126": round(base * QN_COEF_SMALL, 4),
            "half_grid_115": round(base * QN_COEF_HALF, 4),
            "dist_target_pct": round(dist, 2),
            "above_half_grid": bool(last >= base * QN_COEF_HALF),
            "rise_from_base_pct": round((last / base - 1) * 100, 2),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
