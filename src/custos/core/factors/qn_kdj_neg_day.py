# -*- coding: utf-8 -*-
"""QN·KDJ J 负值计数买点（骑牛登山体系，规则出处
`governance/strategy/qn/05_top_bottom_kdj.md`）。

源规则（经验规律，未回测）：
- 前提（跌透）：死叉时 J 值 > 50；下跌顺滑（全程阴线、实体由大到中到小）。
- **J 负值第三天 = 可选买点，第五天 = 极致买点**；横盘超 5 天未新高离场。
- 另腿：**K/D 20 以下金叉**（J 同时低于 20）→ 买入。

确定性转译（待回测）：
- 死叉 = K 自上向下穿越 D（`indicators.kdj_series`，与全项目同口径）
- 负值计数 = 死叉后 J<0 的连续天数（第一根 J<0 记第 1 天）
- 下跌顺滑（简化腿）：死叉至当日全部阴线（收<开）
- KD20 金叉 = 当日 K 上穿 D 且 K、D、J 均 < 20

⚠️ **口径警示**：本模块的 J 用法（负值计数、20 以下金叉）与 B1 反转K 的
`J_LOW_THRESHOLD=13.0` 是两套不同规则，不得混用（qn/05 头部警示同）。

pattern 类；绝不 raise。
"""

from __future__ import annotations

from typing import Any, Optional

from custos.core.indicators import kdj_series

FACTOR: dict[str, Any] = {
    "id": "qn_kdj_neg_day",
    "name": "QN·KDJ J 负值计数（第三天/第五天）与 KD20 金叉",
    "kind": "pattern",
    "status": "untested",  # 新实现未回测（骑牛体系口径 + 合成用例）
    "evidence": "",
    "note": "规则出处 governance/strategy/qn/05_top_bottom_kdj.md；死叉J>50 后 J<0 第 3/5 天=买点；另腿 KD20 以下金叉；⚠️ 与 B1 反转K J<13 不同口径",
    "min_bars": 20,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_CROSS_LOOKBACK = 15  # 待回测：向前找死叉的最大根数
QN_J_CROSS_MIN = 50.0  # 待回测：死叉时 J 的跌透阈值（源规则「死叉 J 值大于 50」）
QN_BUY_DAYS = (3, 5)  # 待回测：负值第 3 天=可选买点、第 5 天=极致买点
QN_KD20_LEVEL = 20.0  # 待回测：KD 金叉低位阈值（源规则「20 以下的 KD 金叉」）


def _find_death_cross(k, d) -> Optional[int]:
    """末根之前最近一次死叉的位置（距末根的根数，0 不算——死叉当日不计入负值计数）。"""
    n = len(k)
    for i in range(n - 2, max(0, n - 1 - QN_CROSS_LOOKBACK), -1):
        if k[i - 1] >= d[i - 1] and k[i] < d[i]:
            return n - 1 - i
    return None


def _neg_day_count(j, cross_bars_ago: int) -> int:
    """死叉之后 J<0 的连续天数（截至末根；中间 J≥0 则计数中断）。"""
    n = len(j)
    start = n - cross_bars_ago  # 死叉后第一根
    cnt = 0
    for i in range(start, n):
        if j[i] < 0:
            cnt += 1
        else:
            cnt = 0
    return cnt


def _smooth_decline(open_, close, cross_bars_ago: int) -> bool:
    """下跌顺滑（简化腿）：死叉至当日全部阴线（收<开）。源规则另有
    「实体由大到中到小」，第一版只钉阴线连续性，实体递减排第二批。"""
    n = len(close)
    start = n - 1 - cross_bars_ago
    return all(close[i] < open_[i] for i in range(start, n))


def _kd20_golden(k, d, j) -> dict[str, Any]:
    """另腿：KD 20 以下金叉（K 上穿 D，且 K/D/J 均 < 20）。"""
    cross_today = bool(k[-1] > d[-1] and k[-2] <= d[-2])
    low = bool(
        k[-1] < QN_KD20_LEVEL and d[-1] < QN_KD20_LEVEL and j[-1] < QN_KD20_LEVEL
    )
    return {
        "hit": bool(cross_today and low),
        "cross_today": cross_today,
        "all_below_20": low,
        "k": round(float(k[-1]), 3),
        "d": round(float(d[-1]), 3),
        "j": round(float(j[-1]), 3),
    }


def detect(df, code: str = "") -> dict[str, Any]:
    """J 负值计数买点 / KD20 金叉。绝不 raise。

    返回键：
        hit            负值第 3/5 天（死叉 J>50 且下跌顺滑）或 KD20 金叉
        neg_day        当前负值计数（0=不在负值区）
        legs           death_cross / neg_count / smooth / kd20_golden 明细
    """
    try:
        n = len(df)
        if n < FACTOR["min_bars"]:
            return {
                "available": False,
                "hit": False,
                "reason": f"少于{FACTOR['min_bars']}根K线（{n}）",
            }
        close = df["close"].astype(float).to_numpy()
        open_ = df["open"].astype(float).to_numpy()
        k, d, j = kdj_series(df)
        kv, dv, jv = k.to_numpy(), d.to_numpy(), j.to_numpy()
        kd20 = _kd20_golden(kv, dv, jv)

        cross_ago = _find_death_cross(kv, dv)
        neg = 0
        cross_ok = False
        smooth = False
        if cross_ago is not None:
            cross_idx = n - 1 - cross_ago
            cross_ok = bool(jv[cross_idx] > QN_J_CROSS_MIN)
            neg = _neg_day_count(jv, cross_ago)
            smooth = _smooth_decline(open_, close, cross_ago)
        day_hit = bool(cross_ok and smooth and neg in QN_BUY_DAYS)
        return {
            "available": True,
            "hit": bool(day_hit or kd20["hit"]),
            "neg_day": neg,
            "buy_day_kind": (
                "极致买点"
                if neg == 5 and day_hit
                else ("可选买点" if day_hit else None)
            ),
            "legs": {
                "death_cross": {
                    "hit": cross_ago is not None,
                    "bars_ago": cross_ago,
                    "j_at_cross": (
                        round(float(jv[n - 1 - cross_ago]), 3)
                        if cross_ago is not None
                        else None
                    ),
                    "j_gt_50": cross_ok,
                },
                "neg_count": {"hit": neg in QN_BUY_DAYS, "days": neg},
                "smooth": {"hit": smooth},
                "kd20_golden": kd20,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
