# -*- coding: utf-8 -*-
"""QN·日线翻倍四要素（骑牛登山体系，规则出处
`governance/strategy/qn/07_doubling_swing.md` §二）。

源规则（经验规律，未回测）——上攻位置必须出现的四要素：
1. **144 日均线走平并明显上翘**；
2. 股价冲高回落至 144 日线 **±10%** 以内；
3. **MACD 双线上零轴**（DIF、DEA 均 > 0）；
4. **过左风**四形式之一：涨停板 / 跳空高开 / 倍量 / 线上阴线。

确定性转译（待回测）：
- 走平上翘 = MA144 近 ``QN_RISE_WIN`` 根上移，且此前 ``QN_FLAT_WIN`` 根
  相对变化 ≤ ``QN_FLAT_PCT``（先走平后上翘）
- 涨停 = 当日涨幅（round-2 显示精度，项目惯例）≥ `code_utils.price_limit_pct(code)`
  − ``QN_LIMIT_TOL``（容差，封板判定；涨跌幅限制口径 v0.33 定案，全项目唯一来源）
- 跳空高开 = 当日最低 > 前日最高（严格缺口）
- 倍量 = 当日量 ≥ 前日 × ``QN_SURGE_MULT``
- 线上阴线 = 阴线且收盘在 MA25 上方（与 qn_ma25_state 同均线口径）

pattern 类；四腿全中 = hit。绝不 raise。
"""

from __future__ import annotations

from typing import Any

from custos.core.code_utils import price_limit_pct
from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays
from custos.core.indicators import macd_series

FACTOR: dict[str, Any] = {
    "id": "qn_ma144_launch",
    "name": "QN·日线翻倍四要素（144 线上翘 + 回踩 + MACD 水上 + 过左风）",
    "kind": "pattern",
    "status": "untested",  # 新实现未回测（骑牛体系口径 + 合成用例）
    "evidence": "",
    "note": "规则出处 governance/strategy/qn/07_doubling_swing.md §二；四要素：MA144 走平上翘/回踩±10%/MACD 双线上零轴/过左风四形式之一（涨停·跳空·倍量·线上阴线）",
    "min_bars": 170,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_MA_WIN = 144  # 待回测：趋势均线窗口（源规则 144 日线，与 v0.7 中长期口径一致）
QN_RISE_WIN = 5  # 待回测：上翘确认根数（近 N 根均线上移）
QN_FLAT_WIN = 20  # 待回测：上翘之前的走平观察窗
QN_FLAT_PCT = 0.02  # 待回测：走平容差（窗内相对变化）
QN_PULLBACK_PCT = 10.0  # 待回测：回踩均线容差 ±%（源规则「正负10%以内」）
QN_LIMIT_TOL = 0.5  # 待回测：涨停判定容差 pp（封板认定）
QN_SURGE_MULT = 2.0  # 待回测：倍量阈值（源规则「倍量」）
QN_MA25_WIN = 25  # 待回测：线上阴线的「线」（25 日线，源规则同一体系）


def _leg_ma144_turn_up(df) -> dict[str, Any]:
    """腿① MA144 走平上翘：近 QN_RISE_WIN 根上移 + 此前 QN_FLAT_WIN 根走平。"""
    ma = df["close"].astype(float).rolling(QN_MA_WIN).mean().to_numpy()
    if ma[-1] != ma[-1]:  # NaN 防御
        return {"hit": False, "reason": "MA144 不可得"}
    rising = bool(ma[-1] > ma[-1 - QN_RISE_WIN])
    flat_before = (
        abs(float(ma[-1 - QN_RISE_WIN] / ma[-1 - QN_RISE_WIN - QN_FLAT_WIN] - 1))
        <= QN_FLAT_PCT
    )
    return {
        "hit": bool(rising and flat_before),
        "rising": rising,
        "flat_before": bool(flat_before),
        "ma144": round(float(ma[-1]), 4),
    }


def _leg_pullback(close, ma_last: float) -> dict[str, Any]:
    """腿② 回踩：收盘在 MA144 ±QN_PULLBACK_PCT% 以内。"""
    dev = (close[-1] / ma_last - 1) * 100 if ma_last else 999.0
    return {"hit": bool(abs(dev) <= QN_PULLBACK_PCT), "dev_ma144_pct": round(dev, 2)}


def _leg_macd_above_zero(df) -> dict[str, Any]:
    """腿③ MACD 双线上零轴（DIF、DEA 均 > 0）。"""
    dif, dea, _h = macd_series(df["close"])
    dv, ev = float(dif.iloc[-1]), float(dea.iloc[-1])
    return {
        "hit": bool(dv > 0 and ev > 0),
        "dif_pos": bool(dv > 0),
        "dea_pos": bool(ev > 0),
    }


def _leg_cross_forms(df, close, open_, vol, code: str) -> dict[str, Any]:
    """腿④ 过左风四形式（任一）：涨停 / 跳空高开 / 倍量 / 线上阴线。"""
    chg = (close[-1] / close[-2] - 1) * 100 if close[-2] else 0.0
    limit_up = bool(round(chg, 2) >= price_limit_pct(code) - QN_LIMIT_TOL)
    gap_up = bool(df["low"].astype(float).iloc[-1] > df["high"].astype(float).iloc[-2])
    surge = bool(vol[-2] and vol[-1] >= vol[-2] * QN_SURGE_MULT)
    ma25 = df["close"].astype(float).rolling(QN_MA25_WIN).mean().iloc[-1]
    online_yin = bool(close[-1] < open_[-1] and close[-1] > ma25)
    forms = {
        "limit_up": limit_up,
        "gap_up": gap_up,
        "surge": surge,
        "online_yin": online_yin,
    }
    return {"hit": any(forms.values()), "forms": forms}


def detect(df, code: str = "") -> dict[str, Any]:
    """日线翻倍四要素：四腿全中 = hit。绝不 raise。"""
    try:
        close, _high, _low, vol = _ohlcv_arrays(df)
        open_ = df["open"].astype(float).to_numpy()
        n = len(df)
        need = QN_MA_WIN + QN_RISE_WIN + QN_FLAT_WIN
        if n < need:
            return {
                "available": False,
                "hit": False,
                "reason": f"少于{need}根K线（{n}）",
            }
        leg_ma = _leg_ma144_turn_up(df)
        ma_last = leg_ma.get("ma144") or 0.0
        legs = {
            "ma144_turn_up": leg_ma,
            "pullback_zone": _leg_pullback(close, ma_last),
            "macd_above_zero": _leg_macd_above_zero(df),
            "cross_forms": _leg_cross_forms(df, close, open_, vol, code),
        }
        hit = all(leg.get("hit") for leg in legs.values())
        return {"available": True, "hit": bool(hit), "legs": legs}
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
