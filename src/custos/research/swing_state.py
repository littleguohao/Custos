# -*- coding: utf-8 -*-
"""走势三态候选的研究侧入口（calib_probe trend_shape 图集用）。

实现在因子层 ``b1_structure.check_swing_state``；v0.589 起由 MACD 平台 N 结构推出（owner 2026-11-25 定为全项目走势唯一底座），本模块只做薄封装。
🔄 v4（两段直线拟合，SW4_*）v0.589 停用。
🔄 v3（两段直线、转折点任取、最短段 40 根）与 v2（10 根尺度游程）已失效：v3 复测 7/16，v2 复验 6/48。
"""

from __future__ import annotations

from typing import Any

from custos.core.factors.b1_structure import check_swing_state


def swing_state(df: Any) -> dict[str, Any]:
    """{"available", "state", "turn_bars_ago"}——同 ``b1_structure.check_swing_state``。"""
    return check_swing_state(df)
