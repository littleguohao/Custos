# -*- coding: utf-8 -*-
"""S_shape v3.0 沙漏评分（借鉴 workflow.pptx「常规量化选股工作流」v3.0 模型）。

有界加权评分（每分项封顶，天然 0-100，解决旧 technical_score 无界累加饱和问题）：

    S_shape(0-100) = 压缩/收敛(0-20) + 枢轴邻近/突破(0-15) + 量(20/60日&斜率)(0-20)
                     + 口袋妖怪(0-15) + 上方套牢供给(0-10) + 均线结构(0-10) + 事件风险(0-10)
    S**            = clamp( S_shape + Δ催化(0-10) − P惩罚(放量阴线), 0, 100 )
    建议            = S**>=70 可买 / 60-69 观望 / <60 不买

来源幻灯片部分阈值被遮挡（黄块），下列常量均取**相对合理猜测**并标注"待回测"；
校准前不得视为已定型，实际分量值随候选落盘可复盘。本模块只读本地日线、绝不 raise。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

_TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(_TOOLS), str(_TOOLS / "market_timing")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from technical_monitor import _infer_price_limit, kdj as _kdj_fn  # noqa: E402
except Exception:  # noqa: BLE001 —— 导入失败时用保守默认涨跌幅
    _kdj_fn = None

    def _infer_price_limit(code: str, df) -> int:  # type: ignore
        raw = str(code).strip().upper().split(".")[0]
        return 20 if raw.startswith(("688", "920", "300", "301")) else 10


def _kdj_jvals(df) -> tuple[Optional[float], Optional[float]]:
    """(J, J_prev)；kdj 不可用返回 (None, None)。"""
    if _kdj_fn is None:
        return None, None
    r = _kdj_fn(df)
    if not r.get("available"):
        return None, None
    return r.get("j"), r.get("j_prev")

# ===== 待回测参数（部分源自被遮挡幻灯片，取合理猜测）=====
SSHAPE_MIN_BARS = 60             # 计算 S_shape 所需最少 K 线（含 60 日均量/均线）

# 压缩/收敛 VCP 0-20
VCP_LEG = 10                     # 每段观察长度（日）
VCP_RANGE_STRONG = 0.5           # 近段日均振幅/前段 ≤0.5 强收敛
VCP_RANGE_MILD = 0.75            # ≤0.75 温和收敛
VCP_VOL_STRONG = 0.6             # 近段/前段均量 ≤0.6 强缩量
VCP_VOL_MILD = 0.8               # ≤0.8 温和缩量

# 枢轴邻近/突破 0-15
PIVOT_BASE_WIN = 20              # 枢轴＝近 N 日（不含当日）最高价
PIVOT_BREAK_PCT = 2.0            # 突破枢轴且超出 ≤2% ＝ 刚突破（最佳）
PIVOT_NEAR_PCT = 3.0             # 收盘距枢轴 ≤3% ＝ 邻近
PIVOT_MID_PCT = 6.0             # ≤6% ＝ 尚可

# 量（20/60日 & 斜率）0-20
VOL_SURGE_RATIO = 1.2            # 当日量/20日均量 ≥1.2 放量

# 口袋妖怪 Pocket Pivot 0-15（O'Neil/Kacher：放量阳突破，量>近10日最大阴量）
POCKET_LOOKBACK = 10
POCKET_RECENT = 3                # 近 N 日内出现即计分

# 上方套牢供给 0-10（越少压力越好）
OVERHEAD_WIN = 60

# 均线结构 0-10（10/20/50 多头 + 低点抬高）
MA_SHORT, MA_MID, MA_LONG = 10, 20, 50

# 事件风险 0-10（个股新闻未接入 enrich，取中性占位）
EVENT_NEUTRAL = 5.0

# Δ 催化 0-10
DELTA_LOW_POS_PCT = 15.0         # 低位反包：收盘处于近20日低点上方 ≤此% 视为低位

# P 惩罚（放量阴线）
PEN_BIG_BEAR_FRAC = 0.5          # 大阴＝跌幅 ≥ 涨跌幅制度 ×0.5
PEN_VOL_RATIO = 1.2              # 放量＝量/前5日均量 ≥1.2
PEN_RECOVER_VOL = 0.9           # 收复但量 <0.9× → −10；≥0.9× → −5
PEN_FRONTHIGH_PCT = 5.0          # 前高距收盘 ≤此% → 惩罚减半

# S** → 技术面层级阈值（供分层矩阵用；对齐"可买≥70"，取略宽的 65/40 待回测）
SSTAR_STRONG = 65.0
SSTAR_MID = 40.0


def _arrays(df: pd.DataFrame):
    close = df["close"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    vol = df["volume"].astype(float).to_numpy()
    open_ = df["open"].astype(float).to_numpy()
    return close, high, low, vol, open_


# ---------- 7 个分项检测器（每项含 points 与实际值，绝不 raise）----------

def compute_vcp(df) -> dict[str, Any]:
    """压缩/收敛（VCP）0-20：近段日均振幅与均量相对前段收缩。"""
    close, high, low, vol, _ = _arrays(df)
    n = len(df)
    if n < 2 * VCP_LEG:
        return {"points": 0.0, "available": False}
    with np.errstate(divide="ignore", invalid="ignore"):
        rng = (high - low) / np.where(close == 0, np.nan, close)
    recent = float(np.nanmean(rng[-VCP_LEG:]))
    prior = float(np.nanmean(rng[-2 * VCP_LEG:-VCP_LEG]))
    range_ratio = (recent / prior) if prior else None
    rv = float(vol[-VCP_LEG:].mean())
    pv = float(vol[-2 * VCP_LEG:-VCP_LEG].mean())
    vol_ratio = (rv / pv) if pv else None
    pts = 0.0
    if range_ratio is not None:
        pts += 12.0 if range_ratio <= VCP_RANGE_STRONG else (7.0 if range_ratio <= VCP_RANGE_MILD else 0.0)
    if vol_ratio is not None:
        pts += 8.0 if vol_ratio <= VCP_VOL_STRONG else (4.0 if vol_ratio <= VCP_VOL_MILD else 0.0)
    return {"points": round(min(pts, 20.0), 1), "available": True,
            "range_ratio": round(range_ratio, 3) if range_ratio is not None else None,
            "vol_ratio": round(vol_ratio, 3) if vol_ratio is not None else None}


def compute_pivot(df) -> dict[str, Any]:
    """枢轴邻近/突破 0-15：收盘相对近期枢轴（阻力高点）的位置。"""
    close, high, _, _, _ = _arrays(df)
    n = len(df)
    if n < PIVOT_BASE_WIN + 2:
        return {"points": 0.0, "available": False}
    pivot = float(high[-(PIVOT_BASE_WIN + 1):-1].max())
    c = float(close[-1])
    if not pivot:
        return {"points": 0.0, "available": False}
    if c >= pivot:
        gain = (c / pivot - 1) * 100
        pts = 15.0 if gain <= PIVOT_BREAK_PCT else 12.0
    else:
        dist = (pivot / c - 1) * 100
        pts = 12.0 if dist <= PIVOT_NEAR_PCT else (6.0 if dist <= PIVOT_MID_PCT else 0.0)
    return {"points": round(pts, 1), "available": True,
            "pivot": round(pivot, 4), "close": round(c, 4)}


def compute_volume_health(df) -> dict[str, Any]:
    """量（20/60日 & 斜率）0-20：放量 + 均量多头 + 量能斜率上行。"""
    _, _, _, vol, _ = _arrays(df)
    n = len(df)
    if n < 62:
        return {"points": 0.0, "available": False}
    ma20 = float(vol[-20:].mean())
    ma60 = float(vol[-60:].mean())
    ma20_prev = float(vol[-25:-5].mean())
    today = float(vol[-1])
    pts = 0.0
    if ma20:
        pts += 8.0 if today >= ma20 * VOL_SURGE_RATIO else (4.0 if today >= ma20 else 0.0)
    if ma60 and ma20 >= ma60:
        pts += 6.0
    if ma20_prev and ma20 > ma20_prev:
        pts += 6.0
    return {"points": round(min(pts, 20.0), 1), "available": True,
            "vol_ratio_ma20": round(today / ma20, 3) if ma20 else None,
            "ma20_ge_ma60": bool(ma60 and ma20 >= ma60)}


def check_pocket_pivot(df) -> dict[str, Any]:
    """口袋妖怪 0-15：近日放量阳线，量 > 近10日最大阴量，且收在10日线上。"""
    close, _, _, vol, _ = _arrays(df)
    n = len(df)
    if n < POCKET_LOOKBACK + 3:
        return {"points": 0.0, "available": False, "hit": False}
    hit = None
    for t in range(n - POCKET_RECENT, n):
        if t < POCKET_LOOKBACK + 1 or t < 10:
            continue
        down_vols = [vol[k] for k in range(t - POCKET_LOOKBACK, t) if close[k] < close[k - 1]]
        max_down = max(down_vols) if down_vols else 0.0
        ma10_t = float(close[t - 9:t + 1].mean())
        if close[t] > close[t - 1] and max_down and vol[t] > max_down and close[t] >= ma10_t:
            hit = {"bars_ago": n - 1 - t, "vol": float(vol[t]), "max_down_vol": float(max_down)}
            break
    return {"points": 15.0 if hit else 0.0, "available": True, "hit": hit is not None, "detail": hit}


def compute_overhead_supply(df) -> dict[str, Any]:
    """上方套牢供给 0-10：近窗口内成交在当前价上方的比例越低越好。"""
    close, high, low, vol, _ = _arrays(df)
    n = len(df)
    if n < OVERHEAD_WIN:
        return {"points": 0.0, "available": False}
    c = float(close[-1])
    seg_h = high[-OVERHEAD_WIN:]
    seg_l = low[-OVERHEAD_WIN:]
    seg_c = close[-OVERHEAD_WIN:]
    seg_v = vol[-OVERHEAD_WIN:]
    tp = (seg_h + seg_l + seg_c) / 3
    total = float(seg_v.sum())
    above = float(seg_v[tp > c].sum()) if total else 0.0
    frac = (above / total) if total else 1.0
    return {"points": round(10.0 * (1 - frac), 1), "available": True,
            "overhead_frac": round(frac, 3)}


def compute_ma_structure(df) -> dict[str, Any]:
    """均线结构 0-10：10/20/50 多头排列(+7) + 低点抬高(+3)。"""
    close, _, low, _, _ = _arrays(df)
    n = len(df)
    if n < MA_LONG + 2:
        return {"points": 0.0, "available": False}
    ma_s = float(close[-MA_SHORT:].mean())
    ma_m = float(close[-MA_MID:].mean())
    ma_l = float(close[-MA_LONG:].mean())
    c = float(close[-1])
    bull = ma_s > ma_m > ma_l and c >= ma_s
    low_recent = float(low[-10:].min())
    low_prior = float(low[-20:-10].min())
    higher_low = low_recent >= low_prior
    pts = (7.0 if bull else 0.0) + (3.0 if higher_low else 0.0)
    return {"points": round(pts, 1), "available": True,
            "bull_stack": bool(bull), "higher_low": bool(higher_low)}


def compute_delta_catalyst(df) -> dict[str, Any]:
    """Δ 催化 0-10：收盘强度(0-5) + 当日上涨(+2) + 低位反包(+3)。"""
    close, high, low, _, open_ = _arrays(df)
    n = len(df)
    if n < 3:
        return {"points": 0.0, "available": False}
    rng = float(high[-1] - low[-1])
    closing_strength = (float(close[-1] - low[-1]) / rng) if rng else 0.0
    chg = (close[-1] / close[-2] - 1) * 100 if close[-2] else 0.0
    prev_bear = close[-2] < open_[-2]
    engulf = bool(close[-1] > open_[-1] and prev_bear and close[-1] >= open_[-2])
    low20 = float(low[-20:].min()) if n >= 20 else float(low.min())
    at_low = bool(low20 and close[-1] <= low20 * (1 + DELTA_LOW_POS_PCT / 100))
    pts = closing_strength * 5.0
    if chg > 0:
        pts += 2.0
    if engulf and at_low:
        pts += 3.0
    return {"points": round(min(pts, 10.0), 1), "available": True,
            "closing_strength": round(closing_strength, 3), "change_pct": round(chg, 2),
            "low_engulf": bool(engulf and at_low)}


def compute_penalty(df, code: str = "") -> dict[str, Any]:
    """P 惩罚（放量阴线）：近5日放量大阴 → 未收复−15/收复但缩量−10/放量−5；
    前高距收盘 ≤PEN_FRONTHIGH_PCT 时惩罚减半。返回 points>=0（供 S** 扣减）。"""
    close, high, _, vol, open_ = _arrays(df)
    n = len(df)
    if n < 25:
        return {"points": 0.0, "available": False}
    limit = _infer_price_limit(code, df)
    big = limit * PEN_BIG_BEAR_FRAC
    pen = 0.0
    detail = None
    for t in range(n - 1, max(0, n - 6), -1):
        base = vol[max(0, t - 5):t].mean()
        vr = (vol[t] / base) if base else None
        chg = (close[t] / close[t - 1] - 1) * 100 if close[t - 1] else 0.0
        if close[t] < open_[t] and chg <= -big and vr is not None and vr >= PEN_VOL_RATIO:
            recovered = close[-1] >= high[t]
            if not recovered:
                pen = 15.0
            else:
                cur_base = vol[-6:-1].mean()
                pen = 10.0 if (cur_base and vol[-1] < cur_base * PEN_RECOVER_VOL) else 5.0
            prior_high = float(high[max(0, t - 20):t].max()) if t > 0 else float(high[t])
            if prior_high and close[-1] and (prior_high / close[-1] - 1) * 100 <= PEN_FRONTHIGH_PCT:
                pen /= 2
            detail = {"bars_ago": n - 1 - t, "change_pct": round(chg, 2),
                      "recovered": bool(recovered), "vol_ratio5": round(vr, 3)}
            break
    return {"points": round(pen, 1), "available": True, "detail": detail}


def compute_s_shape(df, code: str = "") -> dict[str, Any]:
    """聚合 S_shape / S** / 建议。K线不足或缺列时 available=False（不 raise）。"""
    try:
        if df is None or len(df) < SSHAPE_MIN_BARS:
            return {"available": False, "s_star": None, "reason": f"少于{SSHAPE_MIN_BARS}根K线"}
        components = {
            "compression": compute_vcp(df),        # 0-20
            "pivot": compute_pivot(df),             # 0-15
            "volume": compute_volume_health(df),    # 0-20
            "pocket_pivot": check_pocket_pivot(df),  # 0-15
            "overhead_supply": compute_overhead_supply(df),  # 0-10
            "ma_structure": compute_ma_structure(df),  # 0-10
            "event_risk": {"points": 0.0, "available": False,
                           "note": "个股事件/新闻未接入 enrich，暂不计分（接入前不白送分，"
                                   "回测证实恒中性无区分度；接入后改为 0-10 实分）"},  # 0-10
        }
        s_shape = round(min(100.0, sum(c["points"] for c in components.values())), 1)
        delta = compute_delta_catalyst(df)
        penalty = compute_penalty(df, code)
        s_star = round(max(0.0, min(100.0, s_shape + delta["points"] - penalty["points"])), 1)
        suggestion = "可买" if s_star >= 70 else ("观望" if s_star >= 60 else "不买")
        return {
            "available": True,
            "s_shape": s_shape,
            "delta": delta["points"],
            "penalty": penalty["points"],
            "s_star": s_star,
            "suggestion": suggestion,
            "max_score": 100,
            "components": components,
            "delta_detail": delta,
            "penalty_detail": penalty,
        }
    except Exception as exc:  # noqa: BLE001 —— 坏数据不中断批次
        return {"available": False, "s_star": None, "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def sstar_level(s_star: Optional[float]) -> str:
    """S** → 技术面层级（强/中/弱），供分层矩阵；阈值待回测。"""
    if s_star is None:
        return "弱"
    return "强" if s_star >= SSTAR_STRONG else ("中" if s_star >= SSTAR_MID else "弱")


# ===== S_reversal（买弱/反转分）——B1 回调买入方向的评分，与突破式 S_shape 相反 =====
# 回测显示短周期低 S_shape(超跌)组跑赢，B1 本就是回调买入。S_reversal 奖励"超跌 + 缩量
# 企稳 + 反转确认"，避免变成接下跌的刀。阈值同样待回测（部分沿用 s_shape 常量）。
REV_MIN_BARS = SSHAPE_MIN_BARS


def compute_s_reversal(df, code: str = "") -> dict[str, Any]:
    """买弱/反转评分（0-100）：超跌深度(0-40) + 缩量企稳(0-30) + 反转确认(0-30)。

    全部用现有量价因子、只读本地日线、绝不 raise。阈值待回测。
    """
    try:
        if df is None or len(df) < REV_MIN_BARS:
            return {"available": False, "s_reversal": None, "reason": f"少于{REV_MIN_BARS}根K线"}
        close, high, low, vol, open_ = _arrays(df)
        n = len(df)
        j, j_prev = _kdj_jvals(df)

        # --- 超跌深度 0-40：J 深度 + 250日回撤 + 低于均线乖离 ---
        j_pts = 16.0 if (j is not None and j < 0) else (10.0 if (j is not None and j < 7) else (6.0 if (j is not None and j < 13) else 0.0))
        win = min(250, n)
        high_w = float(high[-win:].max())
        dd = (1 - close[-1] / high_w) * 100 if high_w else 0.0
        dd_pts = 12.0 if dd >= 40 else (7.0 if dd >= 25 else (3.0 if dd >= 15 else 0.0))
        ma20 = float(close[-20:].mean())
        dev = (close[-1] / ma20 - 1) * 100 if ma20 else 0.0
        below_pts = 12.0 if dev <= -8 else (7.0 if dev <= -4 else (3.0 if dev <= -1 else 0.0))
        oversold = min(40.0, j_pts + dd_pts + below_pts)

        # --- 缩量企稳 0-30：极致缩量 + 回调段缩量 + 守近20日低 ---
        vol_ma5_prev = float(vol[-6:-1].mean()) if n >= 6 else None
        vr = (vol[-1] / vol_ma5_prev) if vol_ma5_prev else None
        vol20 = vol[-20:]
        pctile = float((vol20 < vol[-1]).mean() * 100) if len(vol20) >= 20 else None
        extreme = bool(vr is not None and vr <= 0.5 and pctile is not None and pctile <= 10)
        shrink_pts = 15.0 if extreme else (8.0 if (vr is not None and vr <= 0.8) else 0.0)
        pull_pts = 10.0 if (n >= 11 and vol[-5:].mean() < vol[-10:-5].mean()) else 0.0
        low20 = float(low[-20:].min()) if n >= 20 else float(low.min())
        hold_pts = 5.0 if (low20 and close[-1] > low20) else 0.0
        contraction = min(30.0, shrink_pts + pull_pts + hold_pts)

        # --- 反转确认 0-30：反转K + J拐头 + 低位反包 + 底部巨量 ---
        chg = (close[-1] / close[-2] - 1) * 100 if n >= 2 and close[-2] else 0.0
        amp = (high[-1] / low[-1] - 1) * 100 if low[-1] else 0.0
        reversal_k = bool((j is not None and j < 13) and extreme and abs(chg) <= 2 and amp <= 7)
        rk_pts = 10.0 if reversal_k else 0.0
        jturn_pts = 6.0 if (j is not None and j_prev is not None and j > j_prev and j_prev < 20) else 0.0
        prev_bear = bool(n >= 2 and close[-2] < open_[-2])
        engulf = bool(close[-1] > open_[-1] and prev_bear and close[-1] >= open_[-2])
        at_low = bool(low20 and close[-1] <= low20 * 1.15)
        eng_pts = 7.0 if (engulf and at_low) else 0.0
        vol_ma_w = float(vol[-win:].mean())
        botvol_pts = 7.0 if (dd >= 40 and vol_ma_w and vol[-1] >= vol_ma_w * 2) else 0.0
        reversal_confirm = min(30.0, rk_pts + jturn_pts + eng_pts + botvol_pts)

        s_rev = round(min(100.0, oversold + contraction + reversal_confirm), 1)
        suggestion = "强反转候选" if s_rev >= 70 else ("观察" if s_rev >= 60 else "弱")
        return {
            "available": True,
            "s_reversal": s_rev,
            "suggestion": suggestion,
            "max_score": 100,
            "components": {
                "oversold": {"points": round(oversold, 1), "j": j,
                             "drawdown_pct": round(dd, 2), "ma20_dev_pct": round(dev, 2)},
                "contraction_stabilize": {"points": round(contraction, 1),
                                          "vol_ratio5": round(vr, 3) if vr is not None else None},
                "reversal_confirm": {"points": round(reversal_confirm, 1),
                                     "reversal_k": reversal_k, "j_turn_up": bool(jturn_pts)},
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "s_reversal": None, "error": f"{type(exc).__name__}:{str(exc)[:80]}"}
