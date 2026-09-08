# -*- coding: utf-8 -*-
"""QN·三线红（日/周/月 MACD 红柱共振，骑牛登山体系，规则出处
`governance/strategy/qn/01_general.md` §二/§五、`qn/08_main_wave_launch.md`）。

源规则（经验规律，未回测）：
- **三线红** = 日线、周线、月线 MACD 柱均为红（>0），多周期共振多头；
  「大必胜」需日/周/月三线 MACD 均红柱。
- 月线 MACD 绿柱时，周线红柱下的日线上涨只是反弹，不当反转主升浪。

口径：
- 周线 = `indicators.resample(df, "W-FRI")`（与 `weekly_j` 及 R 系列周线 gate 同口径，
  含进行中的部分周）；月线 = resample "ME"（pandas 3.x，含进行中的部分月）。
- MACD 柱 = `indicators.macd_series` 的 hist（中式 ×2；符号判断与 ×1 无差异）。
- 月 K 根数不足 ``QN_MIN_MONTH_BARS`` 时月线腿记 available=False（如实标注），
  hit 仍要求三腿全红。

state 类：只判共振状态，不作买卖建议。绝不 raise。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from custos.core.factors._util import resample_ready
from custos.core.indicators import macd_series, resample

FACTOR: dict[str, Any] = {
    "id": "qn_three_red",
    "name": "QN·三线红（日/周/月 MACD 红柱共振）",
    "kind": "state",
    "status": "untested",  # 新实现未回测（骑牛体系口径 + 合成用例）
    "evidence": "",
    "note": "规则出处 governance/strategy/qn/01_general.md §二；日/周/月 MACD 柱全红=多周期共振多头（大必胜前提）；月绿柱下日线上涨只按反弹读",
    "min_bars": 60,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_MIN_WEEK_BARS = 35  # 待回测：周线 MACD 可信所需最少周 K 数
QN_MIN_MONTH_BARS = 20  # 待回测：月线 MACD 可信所需最少月 K 数（≈400+ 交易日）


def _leg_red(
    dif_last: float, dea_last: float, bars: int, min_bars: int
) -> dict[str, Any]:
    """某一周期 MACD 柱是否红（dif/dea 末点给定）。K 线不足 min_bars → available=False。"""
    if bars < min_bars:
        return {"available": False, "bars": bars}
    h = (dif_last - dea_last) * 2  # 中式 ×2，与 macd_series 的 hist 同一算式
    return {"available": True, "red": bool(h > 0), "hist": round(h, 6), "bars": bars}


def _hist_red(df: pd.DataFrame, min_bars: int) -> dict[str, Any]:
    """慢路径：整帧算 MACD 取末点（与 _leg_red 同值——同一递归序列）。"""
    if len(df) < min_bars:
        return {"available": False, "bars": len(df)}
    dif, dea, _hist = macd_series(df["close"])
    return _leg_red(float(dif.iloc[-1]), float(dea.iloc[-1]), len(df), min_bars)


def detect(df, code: str = "", _arr: dict | None = None) -> dict[str, Any]:
    """三线红状态：hit=日/周/月 MACD 柱全红。绝不 raise。

    ``_arr``：研究侧预计算序列（macd_dif/macd_dea + weekly_dif/weekly_dea/
    weekly_bars + monthly_dif/monthly_dea/monthly_bars——周/月键是「截至当日
    前缀 resample」口径的 as-of 逐日值，见 backtest_factors._weekly_macd_step /
    _monthly_gate_arrays），给定时不读 df 任何列。两路逐位一致。

    返回键：
        hit               三线全红（任一腿 available=False 或柱不红 → False）
        daily/weekly/monthly  各周期腿明细（available / red / hist）
        monthly_green     月线绿柱标记（源规则：此时日线上涨只按反弹读）
    """
    try:
        n = len(df)
        if n < FACTOR["min_bars"]:
            return {
                "available": False,
                "hit": False,
                "reason": f"少于{FACTOR['min_bars']}根K线（{n}）",
            }
        if _arr is None:
            dfx = resample_ready(df)
            legs = {
                "daily": _hist_red(df, 35),
                "weekly": _hist_red(resample(dfx, "W-FRI"), QN_MIN_WEEK_BARS),
                "monthly": _hist_red(resample(dfx, "ME"), QN_MIN_MONTH_BARS),
            }
        else:
            i = n - 1
            legs = {
                "daily": _leg_red(_arr["macd_dif"][i], _arr["macd_dea"][i], n, 35),
                "weekly": _leg_red(
                    _arr["weekly_dif"][i],
                    _arr["weekly_dea"][i],
                    int(_arr["weekly_bars"][i]),
                    QN_MIN_WEEK_BARS,
                ),
                "monthly": _leg_red(
                    _arr["monthly_dif"][i],
                    _arr["monthly_dea"][i],
                    int(_arr["monthly_bars"][i]),
                    QN_MIN_MONTH_BARS,
                ),
            }
        all_red = all(leg.get("available") and leg.get("red") for leg in legs.values())
        monthly = legs["monthly"]
        return {
            "available": True,
            "hit": bool(all_red),
            "monthly_green": bool(monthly.get("available") and not monthly.get("red")),
            "legs": legs,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
