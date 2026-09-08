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


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """resample 前置：①date 转 DatetimeIndex 可识别的日期型（生产链本就是 datetime，
    合成数据常是字符串，to_datetime 幂等）②补 amount 列（indicators.resample 聚合它）。"""
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"])
    if "amount" not in x.columns:
        x["amount"] = x["close"].astype(float) * x["volume"].astype(float)
    return x


def _hist_red(df: pd.DataFrame, min_bars: int) -> dict[str, Any]:
    """某一周期 MACD 柱是否红。K 线不足 min_bars → available=False（不误标）。"""
    if len(df) < min_bars:
        return {"available": False, "bars": len(df)}
    _d, _e, hist = macd_series(df["close"])
    h = float(hist.iloc[-1])
    return {"available": True, "red": bool(h > 0), "hist": round(h, 6), "bars": len(df)}


def detect(df, code: str = "") -> dict[str, Any]:
    """三线红状态：hit=日/周/月 MACD 柱全红。绝不 raise。

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
        dfx = _prepare(df)
        legs = {
            "daily": _hist_red(df, 35),
            "weekly": _hist_red(resample(dfx, "W-FRI"), QN_MIN_WEEK_BARS),
            "monthly": _hist_red(resample(dfx, "ME"), QN_MIN_MONTH_BARS),
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
