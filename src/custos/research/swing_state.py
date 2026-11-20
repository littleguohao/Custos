# -*- coding: utf-8 -*-
"""走势三态候选的研究侧入口（calib_probe trend_shape 图集用）。

v0.557 起实现迁到因子层 ``b1_structure.check_swing_state``（v4，owner 2026-11-20 定用于选股侧提示），本模块只做薄封装。
🔄 v3（两段直线、转折点任取、最短段 40 根）与 v2（10 根尺度游程）已失效：v3 复测 7/16，v2 复验 6/48。
"""

from __future__ import annotations

from typing import Any

from custos.core.factors.b1_structure import check_swing_state


def swing_state(df: Any) -> dict[str, Any]:
    """{"available", "state", "turn_bars_ago"}——同 ``b1_structure.check_swing_state``。"""
    return check_swing_state(df)
