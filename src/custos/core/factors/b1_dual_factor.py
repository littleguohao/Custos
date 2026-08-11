# -*- coding: utf-8 -*-
"""B1 双轴组合因子：长期结构（底子好）× 短期回调（买点到）。

依据 `other/good_b1.pptx` 九个案例的形态统计（末根读数，image6 除外——那张的买点在
图上竖线处、J<13，末根已涨回高位）::

    QSX > DKS（长期多头结构）        8/9
    J ≤ 13（短期深度回调，多个为负）  8/9
    DIF > 0 且 MACD 柱 < 0           7/9
    曾有放量启动段                    9/9（图形观察）
    回调段缩量 / 回踩贴 QSX·DKS       多数

设计（owner 2026-08-03 裁定）：
  - B1 是**单纯的回调买入**，不吃突破。故 s_shape 的三个突破式分项
    （pivot 枢轴突破 / pocket_pivot 口袋妖怪 / compression VCP 收敛）**不进**本因子——
    实测 J=11.5 的超卖形态在这三项上全部得 0 分而它们占 50 分，用它们当技术轴等于
    用"买强分"给"买弱买点"打分。
  - 轴1 **软加权**（不做硬门槛）：熊市里 QSX>DKS 会大面积不满足，硬门槛会让候选枯竭。
  - 出货形态（detect_distribution 五式）作否决层，由 gate 负责，不混进打分。

**已接入选股链，但只作描述性证据**（`signal_labels` 出标签落候选表，
`live_use="evidence_only"`、`stage="release"`）：标注不是交易依据，不驱动分层/gate/排序。
原先这里写「未接入选股链」，2026-08-08 订正 —— 它**在跑**，只是不作决策。
确认赢过无条件基准（三重门槛 + 净值终审）之前不得升级为决策依据——结论#15
（平台突破回踩）的教训正是"形态类信号先问是否赢过无条件基准"。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# 原先的 screening/market_timing 两项已于 2026-08-08 删除：本模块只依赖
# src 根与同目录（s_shape / platform_pullback，扁平 import 惯例见 factors/__init__.py）。

from custos.core.indicators import dks_series as _dks_series, qsx_series as _qsx_series  # noqa: E402
from custos.core.b1_thresholds import J_LOW_THRESHOLD  # noqa: E402  L0 唯一来源；见下方常量区注释

from custos.core.factors.s_shape import compute_ma_structure, compute_overhead_supply, compute_s_reversal  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "b1_dual_factor",
    "name": "B1 双轴（长期结构 × 短期回调）",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R6_hypothesis_H1_dual_axis.md",
    "note": "R6：未过跨窗终审；j_low_qsx_weekly 净值只多 0.019R 却付 90% 召回",
    "min_bars": 120,
    "live_use": "evidence_only",  # signal_labels 出标签落候选表；该模块头部已声明「标注不是交易依据」
    "stage": "release",
}


# ---- 轴1：长期结构 0-100（"这只票底子好不好，值不值得等它回调"）----
STRUCT_QSX_DKS_PTS = 30.0        # QSX>DKS 多头结构（good_b1 8/9）——单项权重最高
STRUCT_MA_PTS = 20.0             # 均线多头 + 低点抬高（复用 s_shape.compute_ma_structure 0-10 → ×2）
STRUCT_OVERHEAD_PTS = 15.0       # 上方套牢少（复用 compute_overhead_supply 0-10 → ×1.5）
STRUCT_VOL_CENTER_PTS = 15.0     # 量能中枢上移（MA20 ≥ MA60）
STRUCT_LAUNCH_PTS = 20.0         # 曾有放量启动段（good_b1 9/9）

# 放量启动段：与 enrich_candidates 的 WAVE_START_CANDLE_* 同口径
LAUNCH_LOOKBACK = 60             # 回看窗
LAUNCH_GAIN_PCT = 5.0            # 启动阳线单日涨幅下限 %
LAUNCH_VOL_MULT = 1.5            # 启动阳线量 / 前 20 日均量下限

# ---- 双轴组合权重（软加权；待回测校准）----
W_STRUCT = 0.40                  # 轴1 长期结构
W_REVERSAL = 0.60                # 轴2 短期回调（B1 是回调买入，买点权重更高）

# 日周线共振加分（owner 2026-08-03 提出）：日线 B1 的同时周线也 B1。
# 周线 J<13 意味着**更大周期的回调也到位**，回调充分度比只有日线低更强。
# 记为独立加分而非并进某个轴，便于回测消融（返回里同时给 score_without_resonance）。
RESONANCE_BONUS_PTS = 12.0       # 待回测
# J_LOW_THRESHOLD 自 2026-08-09 起从 `b1_thresholds`（L0 唯一来源）导入：
# 本因子是 release 标注因子（live 候选表标签），标注应反映 live 口径 ⇒ 跟随 live
# （含 B1_J_LOW env 覆盖）；回测可复现性由测试钉住默认值 13.0
# （tests/test_enrich_b1cz.py::TestReversalKThresholdSingleSource）。

DUAL_MIN_BARS = 120              # DKS 需要 MA114 → 至少 120 根


def detect_launch_segment(df: pd.DataFrame, lookback: int = LAUNCH_LOOKBACK) -> dict[str, Any]:
    """回看窗内是否出现过"放量启动阳线"（涨幅 ≥5% 且量 ≥1.5×前20日均量）。

    good_b1 九例全部有这一段——它是"主力进过场"的证据，把"长期阴跌后的超卖"与
    "启动后的健康回调"区分开。后者才是 B1 要买的。
    """
    n = len(df)
    if n < 25:
        return {"available": False, "hit": False}
    close = df["close"].astype(float).to_numpy()
    vol = df["volume"].astype(float).to_numpy()
    start = max(21, n - lookback)
    best = None
    for t in range(start, n):
        prev_ma20 = float(vol[t - 20:t].mean())
        if not prev_ma20 or not close[t - 1]:
            continue
        gain = (close[t] / close[t - 1] - 1) * 100
        vr = vol[t] / prev_ma20
        if gain >= LAUNCH_GAIN_PCT and vr >= LAUNCH_VOL_MULT:
            if best is None or vr > best["vol_ratio"]:
                best = {"bars_ago": n - 1 - t, "gain_pct": round(gain, 2),
                        "vol_ratio": round(float(vr), 3)}
    return {"available": True, "hit": best is not None, "detail": best}


def compute_long_structure(df: pd.DataFrame) -> dict[str, Any]:
    """轴1：长期结构健康度 0-100。绝不 raise。"""
    n = len(df)
    if n < DUAL_MIN_BARS:
        return {"available": False, "score": None,
                "reason": f"少于{DUAL_MIN_BARS}根K线（DKS 需 MA114）"}
    close = df["close"].astype(float)
    qsx = _qsx_series(close)
    dks = _dks_series(close)
    q, d = float(qsx.iloc[-1]), float(dks.iloc[-1])
    bull_stack = bool(q == q and d == d and q > d)

    ma = compute_ma_structure(df)
    ma_pts = (ma.get("points") or 0.0) / 10.0 * STRUCT_MA_PTS if ma.get("available") else 0.0

    oh = compute_overhead_supply(df)
    oh_pts = (oh.get("points") or 0.0) / 10.0 * STRUCT_OVERHEAD_PTS if oh.get("available") else 0.0

    vol = df["volume"].astype(float).to_numpy()
    ma20, ma60 = float(vol[-20:].mean()), float(vol[-60:].mean())
    vol_center = bool(ma60 and ma20 >= ma60)

    launch = detect_launch_segment(df)
    score = ((STRUCT_QSX_DKS_PTS if bull_stack else 0.0) + ma_pts + oh_pts
             + (STRUCT_VOL_CENTER_PTS if vol_center else 0.0)
             + (STRUCT_LAUNCH_PTS if launch.get("hit") else 0.0))
    return {
        "available": True, "score": round(min(100.0, score), 1),
        "qsx": round(q, 4), "dks": round(d, 4), "qsx_gt_dks": bull_stack,
        "components": {
            "qsx_gt_dks": STRUCT_QSX_DKS_PTS if bull_stack else 0.0,
            "ma_structure": round(ma_pts, 1),
            "overhead_supply": round(oh_pts, 1),
            "vol_center_up": STRUCT_VOL_CENTER_PTS if vol_center else 0.0,
            "launch_segment": STRUCT_LAUNCH_PTS if launch.get("hit") else 0.0,
        },
        "launch": launch,
    }


def detect_weekly_b1_resonance(df: pd.DataFrame,
                               j_threshold: float = J_LOW_THRESHOLD,
                               daily_j: Optional[float] = None,
                               weekly_j: Optional[float] = None) -> dict[str, Any]:
    """日线 B1 + 周线 B1 共振：两个周期的 J 同时 < 阈值。绝不 raise。

    ``daily_j`` / ``weekly_j`` 可注入已算好的值以跳过重算：``resample("W-FRI")`` 占本函数
    4.3ms 中的 2.3ms，而 enrich 的 ``weekly_j_state`` 已经算过一次周线 J（同口径），
    重复算等于白付一次 resample。

    周线 J<13 意味着更大周期的回调也到位——日线可能只是短暂杀跌，周线同时超卖才说明
    整段回调走完。口径与 ``enrich_candidates.weekly_j_state`` 一致（``resample(df,"W-FRI")``
    后取 KDJ(9,3,3) 的 J），由测试钉住两者相等。

    ``date`` 列为字符串时先转 datetime（resample 需要 DatetimeIndex）——不改原 df。
    """
    try:
        from custos.core.indicators import kdj, resample
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "hit": False, "reason": f"dep_missing:{type(exc).__name__}"}
    try:
        if daily_j is None:
            dj = kdj(df)
            if not dj.get("available") or dj.get("j") is None:
                return {"available": False, "hit": False, "reason": "daily_kdj_unavailable"}
            daily_j = float(dj["j"])
        n_weekly = None
        if weekly_j is None:
            d = df
            if not pd.api.types.is_datetime64_any_dtype(df["date"]):
                d = df.copy()
                d["date"] = pd.to_datetime(d["date"])
            weekly = resample(d, "W-FRI")
            wj = kdj(weekly)
            if not wj.get("available") or wj.get("j") is None:
                return {"available": False, "hit": False, "reason": "weekly_kdj_unavailable",
                        "daily_j": round(float(daily_j), 2)}
            weekly_j, n_weekly = float(wj["j"]), int(len(weekly))
        daily_j, week_j = float(daily_j), float(weekly_j)
        daily_low = daily_j == daily_j and daily_j < j_threshold      # NaN 不算满足
        week_low = week_j == week_j and week_j < j_threshold
        return {"available": True, "hit": bool(daily_low and week_low),
                "daily_j": round(daily_j, 2), "weekly_j": round(week_j, 2),
                "daily_j_low": bool(daily_low), "weekly_j_low": bool(week_low),
                "weekly_bars": n_weekly}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "hit": False,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


def compute_b1_dual(df: pd.DataFrame, code: str = "") -> dict[str, Any]:
    """B1 双轴组合分 0-100 = W_STRUCT×长期结构 + W_REVERSAL×短期回调 + 周线共振加分。

    绝不 raise。返回含 ``score_without_resonance`` 便于回测消融共振项。
    """
    try:
        struct = compute_long_structure(df)
        rev = compute_s_reversal(df, code)
        if not struct.get("available") or not rev.get("available"):
            return {"available": False, "score": None,
                    "reason": struct.get("reason") or rev.get("reason") or "unavailable"}
        s1 = float(struct["score"])
        s2 = float(rev["s_reversal"])
        base = W_STRUCT * s1 + W_REVERSAL * s2
        res = detect_weekly_b1_resonance(df)
        bonus = RESONANCE_BONUS_PTS if res.get("hit") else 0.0
        total = round(min(100.0, base + bonus), 1)
        return {
            "available": True, "score": total,
            "score_without_resonance": round(base, 1),
            "long_structure": s1, "short_reversal": s2,
            "qsx_gt_dks": struct["qsx_gt_dks"],
            "weekly_resonance": bool(res.get("hit")),
            "resonance_bonus": bonus,
            "suggestion": "可买" if total >= 70 else ("观望" if total >= 55 else "不买"),
            "struct_detail": struct, "reversal_detail": rev, "resonance_detail": res,
        }
    except Exception as exc:  # noqa: BLE001 —— 坏数据不中断批次
        return {"available": False, "score": None,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}


# ---- 突破回踩型 B1（owner 2026-08-03 提出）----
# "突破前高后回调到 B1 区间，且股价不低于前高" —— 这是 platform_pullback ∩ J<13。
# 结论#15 否决的是平台突破回踩**作独立入场**（净值 3 窗方向随环境摆动），并明确
# "证据层保留"；它测过叠加板块/基本面优/0AMV 各腿，**没测过叠加 J<13**。所以这是
# #15 留下的未测组合，作为**标记**（子集对比）而非独立入场，与该结论不冲突。
def detect_breakout_pullback_b1(df: pd.DataFrame, code: str = "",
                                j_threshold: float = 13.0,
                                ph_tol: float = 0.98) -> dict[str, Any]:
    """突破回踩型 B1：平台突破回踩不破 + 当日进入 B1 区间（J<阈值）。绝不 raise。

    ``ph_tol`` 是"不低于前高"的容差口径，**需策略确认**：
      - ``platform_high`` 由 platform_pullback 按**最高价**摆动高点算出；
      - 严格口径 ``close >= platform_high`` 等于要求收盘超过历史最高价，回踩场景下
        几乎不可达（实测合成用例：平台高 10.465 vs 回踩收盘 10.393）；
      - 默认 0.98 复用 platform_pullback 自身的判定（"当日收盘守在平台高之上 ≥×0.98"），
        即允许 2% 刺破——这更贴合"回踩不破"的原意。
    返回里同时给出 ``close_ge_ph_strict``（严格口径）便于回测对比两种取法。
    """
    try:
        from custos.core.factors.platform_pullback import detect_platform_pullback
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "hit": False, "reason": f"dep_missing:{type(exc).__name__}"}
    try:
        det = detect_platform_pullback(df)
        if not det:
            return {"available": True, "hit": False, "reason": "no_platform_pullback"}
        from custos.core.indicators import kdj
        k = kdj(df)
        if not k.get("available") or k.get("j") is None:
            return {"available": True, "hit": False, "reason": "kdj_unavailable",
                    "platform": det}
        j = float(k["j"])
        in_b1 = j < j_threshold
        close = float(df["close"].astype(float).iloc[-1])
        ph = float(det.get("platform_high") or 0.0)
        above_ph = bool(ph and close >= ph * ph_tol)
        strict = bool(ph and close >= ph)
        return {"available": True, "hit": bool(in_b1 and above_ph),
                "j": round(j, 2), "in_b1_zone": in_b1,
                "close_ge_platform_high": above_ph,
                "close_ge_ph_strict": strict, "ph_tol": ph_tol,
                "platform_high": round(ph, 4) if ph else None,
                "close": round(close, 4), "platform": det}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "hit": False,
                "error": f"{type(exc).__name__}:{str(exc)[:80]}"}
