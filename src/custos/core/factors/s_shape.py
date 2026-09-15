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

from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

FACTOR: dict[str, Any] = {
    "id": "s_shape",
    "name": "S 形态综合分（S**）",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "research_ref": ["R2"],  # TODO #73 谱系回填（与 evidence 同源）
    "note": "R2：全市场阈值扫描无 lift；正向择优劣于随机",
    "min_bars": 60,
    # ✅ **定案记录（2026-08-12，v0.50，#37 阶段 A，owner 拍板）**：
    #   原「已知矛盾」（R2 说无 alpha、live 主路径却用它分层）已按 R2 结论消解——
    #   `score_candidates.technical_score` 的 s_shape 主路径**已删除**，技术分层
    #   统一为 patterns 累加路径（60/30）。本因子降为**展示/证据列**
    #   （s_star/s_shape 仍随候选落盘、candidate_table 有 S** 列），
    #   不驱动分层/排序/可见性。
    "live_use": "evidence_only",  # v0.50 定案：展示/证据列，见上方定案记录
    "stage": "release",
}


# 原先的 market_timing 一项已于 2026-08-08 删除：本模块只依赖 src/core
# 与同目录（因子层惯例：同目录扁平 import 的路径由消费方设置，见 factors/__init__.py）。


from custos.core.code_utils import price_limit_pct  # noqa: E402
from custos.core.b1_thresholds import change_in_range  # noqa: E402  反转K涨跌幅判定（live 同口径，round-2）
from custos.core.indicators import amplitude_pct as amplitude_pct_of  # noqa: E402  振幅唯一实现

_kdj_fn: Callable[..., Any] | None  # 导入失败时退 None（调用点有守卫）
_kdj_series_fn: Callable[..., Any] | None  # 同上（v0.236 预计算旁路的全序列版）

try:
    from custos.core.indicators import (  # noqa: E402
        _infer_price_limit,
        kdj as _kdj_fn,
    )
    from custos.core.indicators import kdj_series as _kdj_series_fn  # noqa: E402  # 预计算旁路（v0.236）用
except Exception:  # noqa: BLE001 —— 导入失败时用保守默认涨跌幅
    _kdj_fn = None
    _kdj_series_fn = None  # type: ignore[assignment]  # 旁路守卫同 _kdj_fn

    def _infer_price_limit(code: str, df) -> int:  # type: ignore
        """`technical_monitor` 导入失败时的退路：只按前缀、无数据自纠。

        前缀表仍走 `code_utils.price_limit_pct`（唯一来源）—— 此前这里内联写
        北交所 20%（实际 30%），与主实现同错。
        """
        return int(price_limit_pct(code))


def _kdj_jvals(df) -> tuple[Optional[float], Optional[float]]:
    """(J, J_prev)；kdj 不可用返回 (None, None)。"""
    if _kdj_fn is None:
        return None, None
    r = _kdj_fn(df)
    if not r.get("available"):
        return None, None
    return r.get("j"), r.get("j_prev")


# ===== 待回测参数（部分源自被遮挡幻灯片，取合理猜测）=====
SSHAPE_MIN_BARS = 60  # 计算 S_shape 所需最少 K 线（含 60 日均量/均线）

# 压缩/收敛 VCP 0-20
VCP_LEG = 10  # 每段观察长度（日）
VCP_RANGE_STRONG = 0.5  # 近段日均振幅/前段 ≤0.5 强收敛
VCP_RANGE_MILD = 0.75  # ≤0.75 温和收敛
VCP_VOL_STRONG = 0.6  # 近段/前段均量 ≤0.6 强缩量
VCP_VOL_MILD = 0.8  # ≤0.8 温和缩量

# 枢轴邻近/突破 0-15
PIVOT_BASE_WIN = 20  # 枢轴＝近 N 日（不含当日）最高价
PIVOT_BREAK_PCT = 2.0  # 突破枢轴且超出 ≤2% ＝ 刚突破（最佳）
PIVOT_NEAR_PCT = 3.0  # 收盘距枢轴 ≤3% ＝ 邻近
PIVOT_MID_PCT = 6.0  # ≤6% ＝ 尚可

# 量（20/60日 & 斜率）0-20
VOL_SURGE_RATIO = 1.2  # 当日量/20日均量 ≥1.2 放量

# 口袋妖怪 Pocket Pivot 0-15（O'Neil/Kacher：放量阳突破，量>近10日最大阴量）
POCKET_LOOKBACK = 10
POCKET_RECENT = 3  # 近 N 日内出现即计分

# 上方套牢供给 0-10（越少压力越好）
OVERHEAD_WIN = 60

# 均线结构 0-10（10/20/50 多头 + 低点抬高）
MA_SHORT, MA_MID, MA_LONG = 10, 20, 50


# Δ 催化 0-10
DELTA_LOW_POS_PCT = 15.0  # 低位反包：收盘处于近20日低点上方 ≤此% 视为低位

# P 惩罚（放量阴线）
PEN_BIG_BEAR_FRAC = 0.5  # 大阴＝跌幅 ≥ 涨跌幅制度 ×0.5
PEN_VOL_RATIO = 1.2  # 放量＝量/前5日均量 ≥1.2
PEN_RECOVER_VOL = 0.9  # 收复但量 <0.9× → −10；≥0.9× → −5
PEN_FRONTHIGH_PCT = 5.0  # 前高距收盘 ≤此% → 惩罚减半

# S** → 技术面层级阈值（供分层矩阵用；对齐"可买≥70"，取略宽的 65/40 待回测）。
# ⚠️ 与 score_candidates.TECH_STRONG_FALLBACK/TECH_MID_FALLBACK (60/30) **不是同一套**：
# 这里管 s_shape 主路径，那里管无 s_shape 数据时的 patterns 回退路径。同一个 62 分
# 会分别被判"中"和"强"。两套都待回测，统一成哪一套需策略 owner 拍板（会改 A/B/C/D）。
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
        # ⚠️ 这里的分母是**当日收盘**，与 `indicators.amplitude_pct` 的
        #    「(高−低)/**前收**」**刻意不同**，不是漏改（2026-08-10 清点确认）：
        #    本量只作为 `recent/prior` 的**比值**参与打分，分母口径在比值里基本抵消；
        #    换成前收会改动 VCP 得分（= 改 live 的 S 分），为「形式统一」付这个代价不值。
        #    ⇒ 它不是「当日振幅」这个指标，只是 VCP 压缩度的中间量。
        rng = (high - low) / np.where(close == 0, np.nan, close)
    recent = float(np.nanmean(rng[-VCP_LEG:]))
    prior = float(np.nanmean(rng[-2 * VCP_LEG : -VCP_LEG]))
    range_ratio = (recent / prior) if prior else None
    rv = float(vol[-VCP_LEG:].mean())
    pv = float(vol[-2 * VCP_LEG : -VCP_LEG].mean())
    vol_ratio = (rv / pv) if pv else None
    pts = 0.0
    if range_ratio is not None:
        pts += (
            12.0
            if range_ratio <= VCP_RANGE_STRONG
            else (7.0 if range_ratio <= VCP_RANGE_MILD else 0.0)
        )
    if vol_ratio is not None:
        pts += (
            8.0
            if vol_ratio <= VCP_VOL_STRONG
            else (4.0 if vol_ratio <= VCP_VOL_MILD else 0.0)
        )
    return {
        "points": round(min(pts, 20.0), 1),
        "available": True,
        "range_ratio": round(range_ratio, 3) if range_ratio is not None else None,
        "vol_ratio": round(vol_ratio, 3) if vol_ratio is not None else None,
    }


def compute_pivot(df) -> dict[str, Any]:
    """枢轴邻近/突破 0-15：收盘相对近期枢轴（阻力高点）的位置。"""
    close, high, _, _, _ = _arrays(df)
    n = len(df)
    if n < PIVOT_BASE_WIN + 2:
        return {"points": 0.0, "available": False}
    pivot = float(high[-(PIVOT_BASE_WIN + 1) : -1].max())
    c = float(close[-1])
    if not pivot:
        return {"points": 0.0, "available": False}
    if c >= pivot:
        gain = (c / pivot - 1) * 100
        pts = 15.0 if gain <= PIVOT_BREAK_PCT else 12.0
    else:
        dist = (pivot / c - 1) * 100
        pts = (
            12.0 if dist <= PIVOT_NEAR_PCT else (6.0 if dist <= PIVOT_MID_PCT else 0.0)
        )
    return {
        "points": round(pts, 1),
        "available": True,
        "pivot": round(pivot, 4),
        "close": round(c, 4),
    }


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
        pts += (
            8.0 if today >= ma20 * VOL_SURGE_RATIO else (4.0 if today >= ma20 else 0.0)
        )
    if ma60 and ma20 >= ma60:
        pts += 6.0
    if ma20_prev and ma20 > ma20_prev:
        pts += 6.0
    return {
        "points": round(min(pts, 20.0), 1),
        "available": True,
        "vol_ratio_ma20": round(today / ma20, 3) if ma20 else None,
        "ma20_ge_ma60": bool(ma60 and ma20 >= ma60),
    }


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
        down_vols = [
            vol[k] for k in range(t - POCKET_LOOKBACK, t) if close[k] < close[k - 1]
        ]
        max_down = max(down_vols) if down_vols else 0.0
        ma10_t = float(close[t - 9 : t + 1].mean())
        if (
            close[t] > close[t - 1]
            and max_down
            and vol[t] > max_down
            and close[t] >= ma10_t
        ):
            hit = {
                "bars_ago": n - 1 - t,
                "vol": float(vol[t]),
                "max_down_vol": float(max_down),
            }
            break
    return {
        "points": 15.0 if hit else 0.0,
        "available": True,
        "hit": hit is not None,
        "detail": hit,
    }


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
    return {
        "points": round(10.0 * (1 - frac), 1),
        "available": True,
        "overhead_frac": round(frac, 3),
    }


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
    return {
        "points": round(pts, 1),
        "available": True,
        "bull_stack": bool(bull),
        "higher_low": bool(higher_low),
    }


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
    return {
        "points": round(min(pts, 10.0), 1),
        "available": True,
        "closing_strength": round(closing_strength, 3),
        "change_pct": round(chg, 2),
        "low_engulf": bool(engulf and at_low),
    }


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
        base = vol[max(0, t - 5) : t].mean()
        vr = (vol[t] / base) if base else None
        chg = (close[t] / close[t - 1] - 1) * 100 if close[t - 1] else 0.0
        if (
            close[t] < open_[t]
            and chg <= -big
            and vr is not None
            and vr >= PEN_VOL_RATIO
        ):
            recovered = close[-1] >= high[t]
            if not recovered:
                pen = 15.0
            else:
                cur_base = vol[-6:-1].mean()
                pen = (
                    10.0 if (cur_base and vol[-1] < cur_base * PEN_RECOVER_VOL) else 5.0
                )
            prior_high = (
                float(high[max(0, t - 20) : t].max()) if t > 0 else float(high[t])
            )
            if (
                prior_high
                and close[-1]
                and (prior_high / close[-1] - 1) * 100 <= PEN_FRONTHIGH_PCT
            ):
                pen /= 2
            detail = {
                "bars_ago": n - 1 - t,
                "change_pct": round(chg, 2),
                "recovered": bool(recovered),
                "vol_ratio5": round(vr, 3),
            }
            break
    return {"points": round(pen, 1), "available": True, "detail": detail}


def compute_s_shape(df, code: str = "") -> dict[str, Any]:
    """聚合 S_shape / S** / 建议。K线不足或缺列时 available=False（不 raise）。"""
    try:
        if df is None or len(df) < SSHAPE_MIN_BARS:
            return {
                "available": False,
                "s_star": None,
                "reason": f"少于{SSHAPE_MIN_BARS}根K线",
            }
        components = {
            "compression": compute_vcp(df),  # 0-20
            "pivot": compute_pivot(df),  # 0-15
            "volume": compute_volume_health(df),  # 0-20
            "pocket_pivot": check_pocket_pivot(df),  # 0-15
            "overhead_supply": compute_overhead_supply(df),  # 0-10
            "ma_structure": compute_ma_structure(df),  # 0-10
            "event_risk": {
                "points": 0.0,
                "available": False,
                "note": "个股事件/新闻未接入 enrich，暂不计分（接入前不白送分，"
                "回测证实恒中性无区分度；接入后改为 0-10 实分）",
            },  # 0-10
        }
        s_shape = round(min(100.0, sum(c["points"] for c in components.values())), 1)
        delta = compute_delta_catalyst(df)
        penalty = compute_penalty(df, code)
        s_star = round(
            max(0.0, min(100.0, s_shape + delta["points"] - penalty["points"])), 1
        )
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
        return {
            "available": False,
            "s_star": None,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }


def sstar_level(s_star: Optional[float]) -> str:
    """S** → 技术面层级（强/中/弱），供分层矩阵；阈值待回测。"""
    if s_star is None:
        return "弱"
    return "强" if s_star >= SSTAR_STRONG else ("中" if s_star >= SSTAR_MID else "弱")


# ===== S_reversal（买弱/反转分）——B1 回调买入方向的评分，与突破式 S_shape 相反 =====
# 回测显示短周期低 S_shape(超跌)组跑赢，B1 本就是回调买入。S_reversal 奖励"超跌 + 缩量
# 企稳 + 反转确认"，避免变成接下跌的刀。阈值同样待回测（部分沿用 s_shape 常量）。
REV_MIN_BARS = SSHAPE_MIN_BARS


# ---------- S_reversal 三段评分（2026-08-18 从 compute_s_reversal 内联体抽纯函数，
# 零行为变化；points 与各中间量逐字保留原逻辑，主函数只剩「逐段评分 → 汇总 → 组装」）----------


def _rev_oversold(close, high, n: int, j) -> dict[str, Any]:
    """超跌深度 0-40：J 深度 + 250日回撤 + 低于均线乖离。"""
    j_pts = (
        16.0
        if (j is not None and j < 0)
        else (
            10.0
            if (j is not None and j < 7)
            else (6.0 if (j is not None and j < 13) else 0.0)
        )
    )
    win = min(250, n)
    high_w = float(high[-win:].max())
    dd = (1 - close[-1] / high_w) * 100 if high_w else 0.0
    dd_pts = 12.0 if dd >= 40 else (7.0 if dd >= 25 else (3.0 if dd >= 15 else 0.0))
    ma20 = float(close[-20:].mean())
    dev = (close[-1] / ma20 - 1) * 100 if ma20 else 0.0
    below_pts = (
        12.0 if dev <= -8 else (7.0 if dev <= -4 else (3.0 if dev <= -1 else 0.0))
    )
    return {"points": min(40.0, j_pts + dd_pts + below_pts), "dd": dd, "dev": dev}


def _rev_contraction_stabilize(close, low, vol, n: int) -> dict[str, Any]:
    """缩量企稳 0-30：极致缩量 + 回调段缩量 + 守近20日低。"""
    vol_ma5_prev = float(vol[-6:-1].mean()) if n >= 6 else None
    vr = (vol[-1] / vol_ma5_prev) if vol_ma5_prev else None
    vol20 = vol[-20:]
    pctile = float((vol20 < vol[-1]).mean() * 100) if len(vol20) >= 20 else None
    extreme = bool(vr is not None and vr <= 0.5 and pctile is not None and pctile <= 10)
    shrink_pts = 15.0 if extreme else (8.0 if (vr is not None and vr <= 0.8) else 0.0)
    pull_pts = 10.0 if (n >= 11 and vol[-5:].mean() < vol[-10:-5].mean()) else 0.0
    low20 = float(low[-20:].min()) if n >= 20 else float(low.min())
    hold_pts = 5.0 if (low20 and close[-1] > low20) else 0.0
    return {
        "points": min(30.0, shrink_pts + pull_pts + hold_pts),
        "vr": vr,
        "extreme": extreme,
        "low20": low20,
    }


def _is_reversal_k(close, high, low, n: int, j, extreme: bool) -> bool:
    """反转K：J<13 + 极致缩量 + round-2 涨跌幅命中 + 振幅≤7。

    2026-08-09 对齐 live 口径（enrich_candidates 经 b1_thresholds）：
    振幅分母 low→prev_close，涨跌幅判定改 round-2（change_in_range，2026-08-07 owner 拍板）。
    """
    chg = (close[-1] / close[-2] - 1) * 100 if n >= 2 and close[-2] else 0.0
    # ⚠️ 收敛到 `indicators.amplitude_pct`（全项目唯一实现，2026-08-10）。
    #    算不出时它返回 **None** 而非 0.0 —— 0.0 会被 `<= 7` 判成「振幅很小」，
    #    把「算不出」显示成「符合条件」。本因子下游按数值比较，故保留 0.0 兜底，
    #    但那是**沿用旧行为**，不是说 0.0 语义正确。
    _amp = amplitude_pct_of(high[-1], low[-1], close[-2]) if n >= 2 else None
    amp = _amp if _amp is not None else 0.0
    return bool(
        (j is not None and j < 13) and extreme and change_in_range(chg) and amp <= 7
    )


def _rev_reversal_confirm(
    close, high, low, vol, open_, n: int, j, j_prev, extreme: bool, low20, dd: float
) -> dict[str, Any]:
    """反转确认 0-30：反转K + J拐头 + 低位反包 + 底部巨量。"""
    reversal_k = _is_reversal_k(close, high, low, n, j, extreme)
    rk_pts = 10.0 if reversal_k else 0.0
    jturn_pts = (
        6.0
        if (j is not None and j_prev is not None and j > j_prev and j_prev < 20)
        else 0.0
    )
    prev_bear = bool(n >= 2 and close[-2] < open_[-2])
    engulf = bool(close[-1] > open_[-1] and prev_bear and close[-1] >= open_[-2])
    at_low = bool(low20 and close[-1] <= low20 * 1.15)
    eng_pts = 7.0 if (engulf and at_low) else 0.0
    win = min(250, n)
    vol_ma_w = float(vol[-win:].mean())
    botvol_pts = 7.0 if (dd >= 40 and vol_ma_w and vol[-1] >= vol_ma_w * 2) else 0.0
    return {
        "points": min(30.0, rk_pts + jturn_pts + eng_pts + botvol_pts),
        "reversal_k": reversal_k,
        "j_turn_up": bool(jturn_pts),
    }


def compute_s_reversal(df, code: str = "") -> dict[str, Any]:
    """买弱/反转评分（0-100）：超跌深度(0-40) + 缩量企稳(0-30) + 反转确认(0-30)。

    全部用现有量价因子、只读本地日线、绝不 raise。阈值待回测。
    """
    try:
        if df is None or len(df) < REV_MIN_BARS:
            return {
                "available": False,
                "s_reversal": None,
                "reason": f"少于{REV_MIN_BARS}根K线",
            }
        close, high, low, vol, open_ = _arrays(df)
        n = len(df)
        j, j_prev = _kdj_jvals(df)

        # 三段评分各为纯函数（同模块上方 _rev_*）；汇总与组装口径不变
        ovs = _rev_oversold(close, high, n, j)
        con = _rev_contraction_stabilize(close, low, vol, n)
        rev = _rev_reversal_confirm(
            close,
            high,
            low,
            vol,
            open_,
            n,
            j,
            j_prev,
            con["extreme"],
            con["low20"],
            ovs["dd"],
        )

        s_rev = round(min(100.0, ovs["points"] + con["points"] + rev["points"]), 1)
        suggestion = "强反转候选" if s_rev >= 70 else ("观察" if s_rev >= 60 else "弱")
        return {
            "available": True,
            "s_reversal": s_rev,
            "suggestion": suggestion,
            "max_score": 100,
            "components": {
                "oversold": {
                    "points": round(ovs["points"], 1),
                    "j": j,
                    "drawdown_pct": round(ovs["dd"], 2),
                    "ma20_dev_pct": round(ovs["dev"], 2),
                },
                "contraction_stabilize": {
                    "points": round(con["points"], 1),
                    "vol_ratio5": round(con["vr"], 3)
                    if con["vr"] is not None
                    else None,
                },
                "reversal_confirm": {
                    "points": round(rev["points"], 1),
                    "reversal_k": rev["reversal_k"],
                    "j_turn_up": rev["j_turn_up"],
                },
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "s_reversal": None,
            "error": f"{type(exc).__name__}:{str(exc)[:80]}",
        }


def score(
    df: pd.DataFrame, code: str = "", precomputed: Optional[dict] = None
) -> Optional[dict]:
    """SCORERS 规范入口（v0.218…B2，TODO #67）：横截面排序分 = s_star。

    映射口径与原 ``backtest_factors._sc_s_shape`` 逐字一致（score=s_star、
    suggestion/aux 同字段、components 取各腿 points）——逻辑从研究侧适配层
    上移进因子模块，backtest_factors 侧只剩注册。行为零变化（钉测：
    tests/test_factor_registry.py::TestCanonicalEntryB2）。

    ``precomputed``（v0.236）= ``score_series`` 的逐位置序列（evaluate_trades
    预计算旁路，只对从第 0 根开始的前缀切片有效；两路逐位一致——钉测
    tests/test_s_shape_precompute.py 全位置对拍 + evaluate_trades 端到端）。
    """
    if precomputed is not None:
        r = s_star_from_series(precomputed, df, code)
        if r is None or not r.get("available"):
            return None
        comps = dict(r["components_points"])
        comps["event_risk"] = 0.0  # 恒中性占位（原 components 的同名 points）
        return {
            "score": r["s_star"],
            "suggestion": r["suggestion"],
            "aux": {
                "s_shape": r["s_shape"],
                "delta": r["delta"],
                "penalty": r["penalty"],
            },
            "components": comps,
        }
    r = compute_s_shape(df, code)
    if not r.get("available"):
        return None
    return {
        "score": r["s_star"],
        "suggestion": r["suggestion"],
        "aux": {"s_shape": r["s_shape"], "delta": r["delta"], "penalty": r["penalty"]},
        "components": {
            k: (v or {}).get("points") for k, v in (r.get("components") or {}).items()
        },
    }


# ---------------------------------------------------------------------------
# 预计算旁路（v0.236，TODO #59 性能项：s_shape 家族三键的 O(n²)→O(n)）
# ---------------------------------------------------------------------------

#: score()/s_reversal/invert 三键共享一份逐位置序列（两家族共用同一批数组与
#: KDJ 序列——j 序列一次计算两家复用）。逐位置值全部是 NaN/None 安全的：
#: 前缀长度不足 ⇒ 该位置 NaN（= 逐切片路径的 available=False ⇒ scorer None）。
_SS_LEGS = (
    "compression",
    "pivot",
    "volume",
    "pocket_pivot",
    "overhead_supply",
    "ma_structure",
)


def _series_arrays(df: pd.DataFrame) -> Optional[dict[str, Any]]:
    """预提取数组束（每股一次）：OHLCV + vcp 振幅序列 + |chg| 序列 + KDJ-J 序列。

    全部在完整 df 上一次性计算（KDJ 递归从第 0 根 ⇒ 前缀末点 ≡ 全序列同点）；
    异常/缺依赖 → None（调用方回退逐切片旧路径，行为与旧版逐位一致）。
    """
    try:
        close, high, low, vol, open_ = _arrays(df)
        with np.errstate(divide="ignore", invalid="ignore"):
            # ⚠️ 分母是当日收盘、与 indicators.amplitude_pct 的前收口**刻意不同**——
            #    本量只是 VCP 压缩度 recent/prior 比值的中间量（compute_vcp 同口径，
            #    见 compute_vcp 的口径注释），不是「当日振幅」指标。
            rng = (high - low) / np.where(np.equal(close, 0.0), np.nan, close)  # vcp 用
            prev_close = np.concatenate(([np.nan], close[:-1]))
            abs_chg = np.abs(close / prev_close - 1.0) * 100.0  # 首根 NaN（penalty 用）
        j_arr: Optional[np.ndarray] = None
        if _kdj_series_fn is not None:  # 与 _kdj_fn 同一块 try 导入（同生共死）
            j_arr = _kdj_series_fn(df, fill_na=50.0)[2].to_numpy(dtype=float)
        return {
            "close": close,
            "high": high,
            "low": low,
            "vol": vol,
            "open": open_,
            "rng": rng,
            "abs_chg": abs_chg,
            "j": j_arr,
        }
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # 旁路契约：异常 → None 回退旧路径（_prepare_stock 调用点不捕异常）
        return None


# ---- s_shape 六腿 + Δ 催化的逐位置版（各腿独立 helper 控 locals；
#      窗口元素/顺序/归约函数与上方 compute_* 逐一相同 ⇒ 逐位一致）----


def _vcp_at(i: int, a: dict[str, Any]) -> float:
    """compute_vcp 的第 i 点（nn = i+1 < 40 → 0.0，含守卫同原实现）。"""
    nn = i + 1
    if nn < 2 * VCP_LEG:
        return 0.0
    rng, vol = a["rng"], a["vol"]
    recent = float(np.nanmean(rng[i - VCP_LEG + 1 : i + 1]))
    prior = float(np.nanmean(rng[i - 2 * VCP_LEG + 1 : i - VCP_LEG + 1]))
    range_ratio = (recent / prior) if prior else None
    rv = float(vol[i - VCP_LEG + 1 : i + 1].mean())
    pv = float(vol[i - 2 * VCP_LEG + 1 : i - VCP_LEG + 1].mean())
    vol_ratio = (rv / pv) if pv else None
    pts = 0.0
    if range_ratio is not None:
        pts += (
            12.0
            if range_ratio <= VCP_RANGE_STRONG
            else (7.0 if range_ratio <= VCP_RANGE_MILD else 0.0)
        )
    if vol_ratio is not None:
        pts += (
            8.0
            if vol_ratio <= VCP_VOL_STRONG
            else (4.0 if vol_ratio <= VCP_VOL_MILD else 0.0)
        )
    return round(min(pts, 20.0), 1)


def _pivot_at(i: int, a: dict[str, Any]) -> float:
    """compute_pivot 的第 i 点（nn < 22 → 0.0）。"""
    nn = i + 1
    if nn < PIVOT_BASE_WIN + 2:
        return 0.0
    high, close = a["high"], a["close"]
    pivot = float(high[i - PIVOT_BASE_WIN : i].max())
    c = float(close[i])
    if not pivot:
        return 0.0
    if c >= pivot:
        gain = (c / pivot - 1) * 100
        pts = 15.0 if gain <= PIVOT_BREAK_PCT else 12.0
    else:
        dist = (pivot / c - 1) * 100
        pts = (
            12.0 if dist <= PIVOT_NEAR_PCT else (6.0 if dist <= PIVOT_MID_PCT else 0.0)
        )
    return round(pts, 1)


def _vol_health_at(i: int, a: dict[str, Any]) -> float:
    """compute_volume_health 的第 i 点（nn < 62 → 0.0）。"""
    nn = i + 1
    if nn < 62:
        return 0.0
    vol = a["vol"]
    ma20 = float(vol[i - 19 : i + 1].mean())
    ma60 = float(vol[i - 59 : i + 1].mean())
    ma20_prev = float(vol[i - 24 : i - 4].mean())
    today = float(vol[i])
    pts = 0.0
    if ma20:
        pts += (
            8.0 if today >= ma20 * VOL_SURGE_RATIO else (4.0 if today >= ma20 else 0.0)
        )
    if ma60 and ma20 >= ma60:
        pts += 6.0
    if ma20_prev and ma20 > ma20_prev:
        pts += 6.0
    return round(min(pts, 20.0), 1)


def _pocket_at(i: int, a: dict[str, Any]) -> float:
    """check_pocket_pivot 的第 i 点（nn < 13 → 0.0；命中与否只影响 points）。"""
    nn = i + 1
    if nn < POCKET_LOOKBACK + 3:
        return 0.0
    close, vol = a["close"], a["vol"]
    for t in range(nn - POCKET_RECENT, nn):
        if t < POCKET_LOOKBACK + 1 or t < 10:
            continue
        down_vols = [
            vol[k] for k in range(t - POCKET_LOOKBACK, t) if close[k] < close[k - 1]
        ]
        max_down = max(down_vols) if down_vols else 0.0
        ma10_t = float(close[t - 9 : t + 1].mean())
        if (
            close[t] > close[t - 1]
            and max_down
            and vol[t] > max_down
            and close[t] >= ma10_t
        ):
            return 15.0
    return 0.0


def _overhead_at(i: int, a: dict[str, Any]) -> float:
    """compute_overhead_supply 的第 i 点（nn < 60 → 0.0）。"""
    nn = i + 1
    if nn < OVERHEAD_WIN:
        return 0.0
    close, high, low, vol = a["close"], a["high"], a["low"], a["vol"]
    c = float(close[i])
    tp = (
        high[i - OVERHEAD_WIN + 1 : i + 1]
        + low[i - OVERHEAD_WIN + 1 : i + 1]
        + close[i - OVERHEAD_WIN + 1 : i + 1]
    ) / 3
    seg_v = vol[i - OVERHEAD_WIN + 1 : i + 1]
    total = float(seg_v.sum())
    above = float(seg_v[tp > c].sum()) if total else 0.0
    frac = (above / total) if total else 1.0
    return round(10.0 * (1 - frac), 1)


def _ma_struct_at(i: int, a: dict[str, Any]) -> float:
    """compute_ma_structure 的第 i 点（nn < 52 → 0.0）。"""
    nn = i + 1
    if nn < MA_LONG + 2:
        return 0.0
    close, low = a["close"], a["low"]
    ma_s = float(close[i - MA_SHORT + 1 : i + 1].mean())
    ma_m = float(close[i - MA_MID + 1 : i + 1].mean())
    ma_l = float(close[i - MA_LONG + 1 : i + 1].mean())
    c = float(close[i])
    bull = ma_s > ma_m > ma_l and c >= ma_s
    higher_low = float(low[i - 9 : i + 1].min()) >= float(low[i - 19 : i - 9].min())
    return round((7.0 if bull else 0.0) + (3.0 if higher_low else 0.0), 1)


def _delta_at(i: int, a: dict[str, Any]) -> float:
    """compute_delta_catalyst 的第 i 点（nn < 3 → 0.0）。"""
    nn = i + 1
    if nn < 3:
        return 0.0
    close, high, low, open_ = a["close"], a["high"], a["low"], a["open"]
    rng_ = float(high[i] - low[i])
    closing_strength = (float(close[i] - low[i]) / rng_) if rng_ else 0.0
    chg = (close[i] / close[i - 1] - 1) * 100 if close[i - 1] else 0.0
    prev_bear = close[i - 1] < open_[i - 1]
    engulf = bool(close[i] > open_[i] and prev_bear and close[i] >= open_[i - 1])
    low20 = float(low[i - 19 : i + 1].min()) if nn >= 20 else float(low[: i + 1].min())
    at_low = bool(low20 and close[i] <= low20 * (1 + DELTA_LOW_POS_PCT / 100))
    pts = closing_strength * 5.0
    if chg > 0:
        pts += 2.0
    if engulf and at_low:
        pts += 3.0
    return round(min(pts, 10.0), 1)


def _s_shape_aggregate_at(i: int, a: dict[str, Any], out: dict[str, Any]) -> None:
    """s_shape 家族第 i 点：六腿 + Δ + 聚合分（compute_s_shape 同序汇总）。"""
    nn = i + 1
    out["legs"]["compression"][i] = _vcp_at(i, a)
    out["legs"]["pivot"][i] = _pivot_at(i, a)
    out["legs"]["volume"][i] = _vol_health_at(i, a)
    out["legs"]["pocket_pivot"][i] = _pocket_at(i, a)
    out["legs"]["overhead_supply"][i] = _overhead_at(i, a)
    out["legs"]["ma_structure"][i] = _ma_struct_at(i, a)
    out["delta"][i] = _delta_at(i, a)
    if nn >= SSHAPE_MIN_BARS:
        comp_sum = (
            out["legs"]["compression"][i]
            + out["legs"]["pivot"][i]
            + out["legs"]["volume"][i]
            + out["legs"]["pocket_pivot"][i]
            + out["legs"]["overhead_supply"][i]
            + out["legs"]["ma_structure"][i]
            + 0.0  # event_risk 恒 0（原实现的恒中性占位）
        )
        out["s_shape"][i] = round(min(100.0, comp_sum), 1)
    # 不足 60 根：留 NaN（= 逐切片路径的 available=False）
    # P 惩罚腿**不在序列里**：它依赖 code（涨跌幅制度前缀 + 数据自纠），而预计算
    # 的调用面只有 df——penalty 在点查询时由 ``penalty_from_series`` 在预存数组上
    # 复算（O(1)），s_star 的最终 clamp 也在点查询做。


# ---- s_reversal 三段的逐位置版（compute_s_reversal 逐位置复算）----


def _rev_oversold_at(i: int, a: dict[str, Any], j: Optional[float]) -> float:
    """_rev_oversold 的第 i 点（返回未舍入段分；舍入在点查询组装侧）。"""
    nn = i + 1
    close, high = a["close"], a["high"]
    j_pts = (
        16.0
        if (j is not None and j < 0)
        else (
            10.0
            if (j is not None and j < 7)
            else (6.0 if (j is not None and j < 13) else 0.0)
        )
    )
    win = min(250, nn)
    high_w = float(high[i - win + 1 : i + 1].max())
    dd = (1 - close[i] / high_w) * 100 if high_w else 0.0
    dd_pts = 12.0 if dd >= 40 else (7.0 if dd >= 25 else (3.0 if dd >= 15 else 0.0))
    ma20 = float(close[i - 19 : i + 1].mean())
    dev = (close[i] / ma20 - 1) * 100 if ma20 else 0.0
    below_pts = (
        12.0 if dev <= -8 else (7.0 if dev <= -4 else (3.0 if dev <= -1 else 0.0))
    )
    a["ovs_dd"][i] = dd  # 反转确认段的底部巨量腿要复用 dd（原实现同此传递）
    return min(40.0, j_pts + dd_pts + below_pts)


def _rev_contraction_at(i: int, a: dict[str, Any]) -> float:
    """_rev_contraction_stabilize 的第 i 点；extreme/low20 经 a 带出供确认段复用。"""
    nn = i + 1
    close, low, vol = a["close"], a["low"], a["vol"]
    vol_ma5_prev = float(vol[i - 5 : i].mean()) if nn >= 6 else None
    vr = (vol[i] / vol_ma5_prev) if vol_ma5_prev else None
    vol20 = vol[i - 19 : i + 1]
    pctile = float((vol20 < vol[i]).mean() * 100) if len(vol20) >= 20 else None
    extreme = bool(vr is not None and vr <= 0.5 and pctile is not None and pctile <= 10)
    shrink_pts = 15.0 if extreme else (8.0 if (vr is not None and vr <= 0.8) else 0.0)
    pull_pts = (
        10.0
        if (nn >= 11 and vol[i - 4 : i + 1].mean() < vol[i - 9 : i - 4].mean())
        else 0.0
    )
    low20 = float(low[i - 19 : i + 1].min()) if nn >= 20 else float(low[: i + 1].min())
    hold_pts = 5.0 if (low20 and close[i] > low20) else 0.0
    a["con_extreme"][i] = extreme
    a["con_low20"][i] = low20
    return min(30.0, shrink_pts + pull_pts + hold_pts)


def _reversal_k_at(i: int, a: dict[str, Any], j: Optional[float]) -> bool:
    """_is_reversal_k 的第 i 点（extreme 复用缩量企稳段的传递）。"""
    nn = i + 1
    close, high, low = a["close"], a["high"], a["low"]
    chg = (close[i] / close[i - 1] - 1) * 100 if nn >= 2 and close[i - 1] else 0.0
    _amp = amplitude_pct_of(high[i], low[i], close[i - 1]) if nn >= 2 else None
    amp = _amp if _amp is not None else 0.0
    return bool(
        (j is not None and j < 13)
        and a["con_extreme"][i]
        and change_in_range(chg)
        and amp <= 7
    )


def _engulf_pts_at(i: int, a: dict[str, Any], low20: float) -> float:
    """低位反包腿的第 i 点（_rev_reversal_confirm 内联段的提取）。"""
    nn = i + 1
    close, open_ = a["close"], a["open"]
    prev_bear = bool(nn >= 2 and close[i - 1] < open_[i - 1])
    engulf = bool(close[i] > open_[i] and prev_bear and close[i] >= open_[i - 1])
    at_low = bool(low20 and close[i] <= low20 * 1.15)
    return 7.0 if (engulf and at_low) else 0.0


def _botvol_pts_at(i: int, a: dict[str, Any], dd: float) -> float:
    """底部巨量腿的第 i 点（dd 复用超跌段的传递）。"""
    nn = i + 1
    vol = a["vol"]
    win_r = min(250, nn)
    vol_ma_w = float(vol[i - win_r + 1 : i + 1].mean())
    return 7.0 if (dd >= 40 and vol_ma_w and vol[i] >= vol_ma_w * 2) else 0.0


def _rev_confirm_at(i: int, a: dict[str, Any], j, j_prev) -> float:
    """_rev_reversal_confirm 的第 i 点（extreme/low20/dd 复用前两段的传递）。"""
    low20, dd = a["con_low20"][i], a["ovs_dd"][i]
    rk_pts = 10.0 if _reversal_k_at(i, a, j) else 0.0
    jturn_pts = (
        6.0
        if (j is not None and j_prev is not None and j > j_prev and j_prev < 20)
        else 0.0
    )
    eng_pts = _engulf_pts_at(i, a, low20)
    botvol_pts = _botvol_pts_at(i, a, dd)
    return min(30.0, rk_pts + jturn_pts + eng_pts + botvol_pts)


def _s_rev_aggregate_at(i: int, a: dict[str, Any], out: dict[str, Any]) -> None:
    """s_reversal 家族第 i 点（nn < 60 → 三段全 NaN，= 逐切片路径的 unavailable）。"""
    nn = i + 1
    if nn < REV_MIN_BARS:
        return
    j_arr = a["j"]
    j = round(float(j_arr[i]), 4) if (j_arr is not None and nn >= 12) else None
    j_prev = round(float(j_arr[i - 1]), 4) if (j_arr is not None and nn >= 12) else None
    ovs = _rev_oversold_at(i, a, j)
    con = _rev_contraction_at(i, a)
    rev = _rev_confirm_at(i, a, j, j_prev)
    out["s_rev_parts"]["oversold"][i] = ovs
    out["s_rev_parts"]["contraction_stabilize"][i] = con
    out["s_rev_parts"]["reversal_confirm"][i] = rev
    out["s_rev"][i] = round(min(100.0, ovs + con + rev), 1)


def score_series(df: pd.DataFrame) -> Optional[dict[str, Any]]:
    """逐位置（bar 序）的 s_shape 家族分点序列——evaluate_trades 预计算旁路用。

    等价性依据（同 ``_precompute_kdj_j_series``/``_precompute_rsi_state_series``
    先例）：所有腿都是「前缀 ``df.iloc[:i+1]`` 上的窗口统计/标量判定」；本函数
    在**预提取的 numpy 数组**上逐位置用**同一批归约函数**（np.mean/max/min/
    nanmean——同值同序同长度 ⇒ 逐位一致）复算每个腿的 points，再按原模块相同的
    round/min/max/clamp 次序汇总。KDJ（RSV→EWM→EWM 递归，fill_na=50）从第 0 根
    开始 ⇒ 前缀末点 ≡ 全序列同点（``indicators.kdj`` 内部即 ``kdj_series``）。
    两家族的 per-bar 异常各自隔离（各一个 try——与 compute_s_shape /
    compute_s_reversal 是两个独立 try 函数的原语义对齐）。

    ⚠️ EMA 递归指标的 as-of 重播种陷阱（score_return_study.py:294 判例）：
    本序列只对「从第 0 根开始的前缀」语义成立（evaluate_trades 的切片恒如此）；
    **不得用于 tail 重播种场景**（切一段当中间起点，EWM/rolling 的 warmup
    会从那段的第 0 根重新起跑，与前缀口径逐位不同）。

    返回键：``s_shape``（聚合分，未含 penalty）/``delta``/``legs``（6 腿 points
    数组）/``s_rev``/``s_rev_parts``（三段未舍入数组）/``abs_chg``/``raw``
    （OHLCV 数组引用，penalty 点查询用）——等长 float 数组，NaN = 该位置不可用；
    异常 → None（调用方回退逐切片旧路径，行为与旧版逐位一致）。
    """
    if df is None or len(df) < 1:
        return None
    a = _series_arrays(df)
    if a is None:
        return None
    n = len(a["close"])
    a["ovs_dd"] = np.full(n, np.nan)  # 段间传递带（oversold 的 dd → 确认段复用）
    a["con_extreme"] = np.zeros(n, dtype=bool)
    a["con_low20"] = np.full(n, np.nan)
    out: dict[str, Any] = {
        "s_shape": np.full(n, np.nan),
        "delta": np.full(n, np.nan),
        "legs": {k: np.full(n, np.nan) for k in _SS_LEGS},
        "s_rev": np.full(n, np.nan),
        "s_rev_parts": {
            k: np.full(n, np.nan)
            for k in ("oversold", "contraction_stabilize", "reversal_confirm")
        },
        "abs_chg": a["abs_chg"],
        "raw": {k: a[k] for k in ("close", "high", "low", "vol", "open")},
    }
    for i in range(n):
        try:  # 两家族各自隔离（原实现是两个独立 try 函数）
            _s_shape_aggregate_at(i, a, out)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # 该位置留 NaN（= 逐切片路径 compute_s_shape 的 except 语义）
            pass
        try:
            _s_rev_aggregate_at(i, a, out)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            pass
    return out


def s_star_from_series(
    pre: dict[str, Any], df: pd.DataFrame, code: str = ""
) -> Optional[dict]:
    """点查询：pre（``score_series`` 产出）+ 前缀切片 df → compute_s_shape 同形 dict。

    ``s_shape`` 聚合分与 Δ 催化取序列第 ``len(df)-1`` 点；P 惩罚腿走
    ``penalty_from_series``（compute_penalty 的逐位置版：它依赖 code 的涨跌幅
    制度 ⇒ 不进序列主体，点查询时在预存数组上同值同序复算，O(1)）。最终
    clamp/round 次序与 ``compute_s_shape`` 逐字相同。序列该位置 NaN（前缀
    不足）→ available=False 同形 dict（与逐切片路径的短路返回一致——
    ``score()`` 层转 None）。
    """
    i = len(df) - 1
    ss = float(pre["s_shape"][i]) if i >= 0 else float("nan")
    if np.isnan(ss):
        return {
            "available": False,
            "s_star": None,
            "reason": f"少于{SSHAPE_MIN_BARS}根K线",
        }
    dl = float(pre["delta"][i])
    pen = penalty_from_series(pre, i, code)
    s_star = round(max(0.0, min(100.0, ss + dl - pen)), 1)
    suggestion = "可买" if s_star >= 70 else ("观望" if s_star >= 60 else "不买")
    return {
        "available": True,
        "s_shape": ss,
        "delta": dl,
        "penalty": pen,
        "s_star": s_star,
        "suggestion": suggestion,
        "components_points": {
            k: float(pre["legs"][k][i]) for k in _SS_LEGS
        },  # event_risk 恒 0.0 占位在 score() 组装侧补
    }


def s_rev_from_series(pre: dict[str, Any], i: int) -> Optional[dict]:
    """s_reversal 家族的点查询（无 code 依赖——penalty 腿是 s_shape 独有）。"""
    if i < 0:
        return None
    v = float(pre["s_rev"][i])
    if np.isnan(v):
        return None
    parts = {k: round(float(pre["s_rev_parts"][k][i]), 1) for k in pre["s_rev_parts"]}
    return {
        "s_reversal": v,
        "suggestion": ("强反转候选" if v >= 70 else ("观察" if v >= 60 else "弱")),
        "components_points": parts,
    }


def penalty_from_series(pre: dict[str, Any], i: int, code: str = "") -> float:
    """compute_penalty 的逐位置版（点查询用；同值同序同判定 ⇒ 逐位一致）。

    依赖 code 的涨跌幅制度 ⇒ 不进 ``score_series`` 主体（预计算调用面只有 df），
    点查询时在预存数组上复算：``_infer_price_limit`` 的近 20 根自纠 =
    ``nanmax(abs_chg[i-19..i])``（该腿只在 nn=i+1 ≥ 25 时调用，窗口不含首根
    NaN ⇒ 与原实现的 ``tail(20).dropna().max()`` 逐位一致）。
    """
    a = pre["raw"]
    nn = i + 1  # 前缀长度（原实现的 len(df)）
    if nn < 25:
        return 0.0
    big = _price_limit_series_at(pre, i, code) * PEN_BIG_BEAR_FRAC
    for t in range(nn - 1, max(0, nn - 6), -1):
        pen = _penalty_bar_at(a, i, t, big)
        if pen is not None:
            return round(pen, 1)
    return 0.0


def _price_limit_series_at(pre: dict[str, Any], i: int, code: str) -> int:
    """_infer_price_limit 的第 i 点（近 20 根自纠窗口 = nanmax(abs_chg[i-19..i])）。"""
    nn = i + 1
    limit = int(price_limit_pct(code))
    if nn >= 20:
        max_change = float(np.nanmax(pre["abs_chg"][i - 19 : i + 1]))
        if limit == 10 and max_change > 9.9:
            limit = 20
        if limit == 10 and max_change <= 5.2:
            limit = 5
    return limit


def _penalty_bar_at(a: dict[str, Any], i: int, t: int, big: float) -> Optional[float]:
    """compute_penalty 内层循环的第 t 根：命中放量大阴 → 该笔惩罚分；未命中 → None。

    算术与 compute_penalty 逐字相同（``nn = i+1`` 对应原实现的 ``n``）；
    返回值经 penalty_from_series 统一 round(·, 1)。
    """
    close, high, vol, open_ = a["close"], a["high"], a["vol"], a["open"]
    nn = i + 1
    base = vol[max(0, t - 5) : t].mean()
    vr = (vol[t] / base) if base else None
    chg = (close[t] / close[t - 1] - 1) * 100 if close[t - 1] else 0.0
    if not (
        close[t] < open_[t] and chg <= -big and vr is not None and vr >= PEN_VOL_RATIO
    ):
        return None
    # ⚠️ 不许把 recovered 的否定写成 ``close < high``：NaN 输入下
    # ``not (NaN >= x)`` 与 ``NaN < x`` 不同（前者 True 后者 False）——保持原式。
    recovered = close[nn - 1] >= high[t]
    if not recovered:
        pen = 15.0
    else:
        cur_base = vol[nn - 6 : nn - 1].mean()
        pen = 10.0 if (cur_base and vol[nn - 1] < cur_base * PEN_RECOVER_VOL) else 5.0
    return _halve_near_front_high(a, i, t, pen)


def _halve_near_front_high(a: dict[str, Any], i: int, t: int, pen: float) -> float:
    """前高距收盘 ≤PEN_FRONTHIGH_PCT 时惩罚减半（compute_penalty 内联段的提取）。"""
    nn = i + 1
    high, close = a["high"], a["close"]
    prior_high = float(high[max(0, t - 20) : t].max()) if t > 0 else float(high[t])
    if (
        prior_high
        and close[nn - 1]
        and (prior_high / close[nn - 1] - 1) * 100 <= PEN_FRONTHIGH_PCT
    ):
        pen /= 2
    return pen
