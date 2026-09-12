# -*- coding: utf-8 -*-
"""QN·倍量切起爆K线（骑牛登山体系，规则出处 `governance/strategy/qn/08_main_wave_launch.md`）。

源规则（经验规律；R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08），status=needs_work、live_use=none——不得进 live 链）：
- **倍量切**：当日阳线同时切断 5 日和 10 日均线，且成交量是昨日 2 倍 ——
  均线收拢成本集中后的起爆 K 线，是主升浪启动的核心信号。
- 前提（可选腿）：大级别均线（60/144）走平托底；鬼招手（极度发散）严禁买入。

确定性转译（待回测）：
- 倍量 = 当日量 ≥ 前日量 × ``QN_SURGE_MULT``（2.0）
- 切断 = 前收同时在 MA5 与 MA10 之下，当日收同时站上两线（阳线 = 收 > 开）
- 托底腿（记录不强制）：MA60 与 MA144 均存在且近 ``QN_BASE_FLAT_WIN`` 根
  相对变化 |ma[-1]/ma[-win] − 1| ≤ ``QN_BASE_FLAT_PCT``（走平），或收盘在其上
- 发散警示腿：MA5 与 MA144 间距 ≥ ``QN_DIVERGE_WARN_PCT``（鬼招手近似，供排除）

本因子是 pattern，绝不 raise；腿级明细全落盘供回测消融。
"""

from __future__ import annotations

from typing import Any

from custos.core.factors._util import ohlcv_arrays as _ohlcv_arrays

FACTOR: dict[str, Any] = {
    "id": "qn_volume_surge_cut",
    "name": "QN·倍量切起爆K线",
    "kind": "pattern",
    "status": "needs_work",  # R31 双窗跑数否决（C2 加值未双窗过线，2026-09-08）
    "evidence": "governance/research/R31_qn_factor_validation.md",
    "research_ref": ["R31"],  # TODO #73 谱系回填（与 evidence 同源）
    "note": "规则出处 governance/strategy/qn/08_main_wave_launch.md；阳线倍量×2 同时上穿 MA5/MA10=起爆K线；大级别托底与鬼招手发散为记录腿",
    "min_bars": 30,
    "live_use": "none",
    "stage": "debug",
}

# ---- 待回测参数 ----
QN_SURGE_MULT = 2.0  # 待回测：倍量阈值（源规则「成交量是昨日二倍」）
QN_BASE_FLAT_WIN = 20  # 待回测：大级别均线走平观察窗
QN_BASE_FLAT_PCT = 0.02  # 待回测：走平容差（窗内相对变化）
QN_DIVERGE_WARN_PCT = 0.30  # 待回测：鬼招手近似（MA5 相对 MA144 的间距）


def _leg_surge(vol) -> dict[str, Any]:
    """腿① 倍量：当日量 ≥ 前日 × QN_SURGE_MULT。"""
    ratio = float(vol[-1] / vol[-2]) if vol[-2] else 0.0
    return {"hit": bool(ratio >= QN_SURGE_MULT), "vol_ratio": round(ratio, 3)}


def _leg_cut(close, open_, ma5, ma10) -> dict[str, Any]:
    """腿② 切断：阳线 + 前收在 MA5/MA10 之下、当日收站上两线。"""
    if ma5[-1] != ma5[-1] or ma10[-1] != ma10[-1]:
        return {"hit": False, "reason": "MA 不可得"}
    was_below = bool(close[-2] < ma5[-2] and close[-2] < ma10[-2])
    now_above = bool(close[-1] > ma5[-1] and close[-1] > ma10[-1])
    yang = bool(close[-1] > open_[-1])
    return {
        "hit": bool(yang and was_below and now_above),
        "yang": yang,
        "was_below_ma5_ma10": was_below,
        "now_above_ma5_ma10": now_above,
    }


def _leg_base_support(close, ma60, ma144, n: int) -> dict[str, Any]:
    """腿③ 大级别托底（记录腿，不强制）：MA60/MA144 走平或收盘在其上。
    短样本（n < w + 观察窗）时该均线腿记 available=False 如实标注。"""
    out: dict[str, Any] = {"hit": False}
    for w, ma in ((60, ma60), (144, ma144)):
        key = f"ma{w}"
        if n < w + QN_BASE_FLAT_WIN:
            out[key] = {"available": False}
            continue
        flat = abs(float(ma[-1] / ma[-1 - QN_BASE_FLAT_WIN] - 1)) <= QN_BASE_FLAT_PCT
        above = bool(close[-1] >= ma[-1])
        out[key] = {
            "available": True,
            "flat": bool(flat),
            "price_above": above,
            "hit": bool(flat or above),
        }
    avail = [out[k] for k in ("ma60", "ma144") if out[k].get("available")]
    out["hit"] = bool(avail) and all(x["hit"] for x in avail)
    return out


def _leg_diverge_warn(ma5, ma144, n: int) -> dict[str, Any]:
    """腿④ 鬼招手发散警示（排除用）：MA5 相对 MA144 间距过大。"""
    if n < 144:
        return {"available": False}
    ma5_last = float(ma5[-1])
    ma144_last = float(ma144[-1])
    gap = ma5_last / ma144_last - 1 if ma144_last else 0.0
    return {
        "available": True,
        "hit": bool(gap >= QN_DIVERGE_WARN_PCT),
        "ma5_vs_ma144_gap_pct": round(gap * 100, 2),
    }


def detect(df, code: str = "", _arr: dict | None = None) -> dict[str, Any]:
    """倍量切起爆K线：腿①倍量 + 腿②切断 合成 hit；腿③④ 为记录/排除腿。绝不 raise。

    ``_arr``：研究侧预计算序列（close/open/volume/ma5/ma10/ma60/ma144），
    给定时不读 df 任何列（可无切片占位路径）。两路逐位一致（rolling 从第 0 根
    递归同序）。
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
            close, _high, _low, vol = _ohlcv_arrays(df)
            open_ = df["open"].astype(float).to_numpy()
            c = df["close"].astype(float)
            ma5 = c.rolling(5).mean().to_numpy()
            ma10 = c.rolling(10).mean().to_numpy()
            ma60 = c.rolling(60).mean().to_numpy()
            ma144 = c.rolling(144).mean().to_numpy()
        else:
            close = _arr["close"][:n]
            open_ = _arr["open"][:n]
            vol = _arr["volume"][:n]
            ma5 = _arr["ma5"][:n]
            ma10 = _arr["ma10"][:n]
            ma60 = _arr["ma60"][:n]
            ma144 = _arr["ma144"][:n]
        legs = {
            "surge": _leg_surge(vol),
            "cut": _leg_cut(close, open_, ma5, ma10),
            "base_support": _leg_base_support(close, ma60, ma144, n),
            "diverge_warn": _leg_diverge_warn(ma5, ma144, n),
        }
        hit = bool(legs["surge"]["hit"] and legs["cut"]["hit"])
        if legs["diverge_warn"].get("hit"):
            hit = False  # 鬼招手（极度发散）严禁买入
        return {"available": True, "hit": hit, "legs": legs}
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "hit": False,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }
