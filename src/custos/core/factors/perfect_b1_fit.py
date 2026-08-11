# -*- coding: utf-8 -*-
"""「完美 B1」指纹拟合度；⚠️ R2：作进场过滤有害（期望 −0.42%/笔），仅描述性

2026-08-06 从 `screening/enrich_candidates.py` 抽出（**零行为变化**，逐字搬）。
抽出的动因：因子实现必须**全项目唯一一份**，其他模块通过调用访问 ——
内联在 1723 行的选股链主流程里，既无法单独回测，也无法防止别处再写一份。
"""

from __future__ import annotations

from typing import Any, Optional

from custos.core.indicators import DKS_MA_WINDOWS, dks_series


from custos.core.indicators import macd  # noqa: E402

# J 门槛与 live 选股链同值；唯一定义在 b1_dual_factor（因子层内），此处引用不另写
from custos.core.factors.b1_dual_factor import J_LOW_THRESHOLD  # noqa: E402

FACTOR: dict[str, Any] = {
    "id": "perfect_b1_fit",
    "name": "「完美 B1」指纹拟合度",
    "kind": "state",
    "status": "candidate",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "「完美 B1」指纹拟合度；⚠️ R2：作进场过滤有害（期望 −0.42%/笔），仅描述性",
    "min_bars": 1,
    "live_use": "evidence_only",  # R2：仅描述性，不作买入依据（落候选表供人看，不驱动分层/gate）
    "stage": "release",
}

FIT_J_DEEP = 0.0  # J<0 → 2 分
FIT_J_MID = 7.0  # J<7 → 1.5 分；J<13 → 1 分
FIT_NEAR_LINE_PCT = 3.0  # 收盘距 QSX 或 DKS ≤3% → 2 分（回踩贴线）
FIT_NEAR_LINE_MAX_PCT = 6.0  # ≤6% → 1 分
FIT_SHRINK_DEEP = 0.5  # 回调段/上涨段均量 ≤0.5 → 2 分
FIT_SHRINK_MID = 0.8  # ≤0.8 → 1 分
FIT_DKS_SLOPE_DAYS = 5  # DKS 上行判断窗口（DKS[t] > DKS[t-N]）


def compute_perfect_b1_fit(
    df, daily_j, zx: dict, pullback: dict, macd_state: Optional[dict] = None
) -> dict[str, Any]:
    """完美 B1 图形贴合度（0-8 梯度分）：J 深度 + 回踩贴线 + 缩量程度 +
    MACD 零轴上 + DKS 上行。每个分量输出实际值（待回测参数见顶部常量）。

    macd_state 可传 check_macd_technics(df) 的结果复用 DIF（避免同一只票把
    12/26/9 三条 EMA 算两遍）；不传或不可用时照旧自己算 macd(df)，结果完全一致。
    """
    comp: dict[str, Any] = {}

    # J 深度：J<0 → 2；J<7 → 1.5；J<13 → 1（图集案例 J 全在 13 以下，多为负）
    if daily_j is None:
        j_pts = 0.0
    elif daily_j < FIT_J_DEEP:
        j_pts = 2.0
    elif daily_j < FIT_J_MID:
        j_pts = 1.5
    elif daily_j < J_LOW_THRESHOLD:
        j_pts = 1.0
    else:
        j_pts = 0.0
    comp["j_depth"] = {"points": j_pts, "daily_j": daily_j}

    # 回踩贴线：收盘距 QSX 或 DKS 的最近偏离 ≤3% → 2；≤6% → 1
    near_pts = 0.0
    line_dist = None
    close_last = float(df["close"].iloc[-1])
    if zx.get("available") and close_last:
        dists = []
        for key in ("qsx", "dks"):
            v = zx.get(key)
            if v:
                dists.append(abs(close_last / float(v) - 1) * 100)
        if dists:
            line_dist = round(min(dists), 2)
            near_pts = (
                2.0
                if line_dist <= FIT_NEAR_LINE_PCT
                else (1.0 if line_dist <= FIT_NEAR_LINE_MAX_PCT else 0.0)
            )
    comp["near_line"] = {"points": near_pts, "min_line_distance_pct": line_dist}

    # 缩量程度：回调段/上涨段均量 ≤0.5 → 2；≤0.8 → 1
    pull_ratio = (
        (pullback.get("detail") or {}).get("pullback_vol_ratio")
        if pullback.get("available")
        else None
    )
    shrink_pts = 0.0
    if pull_ratio is not None:
        shrink_pts = (
            2.0
            if pull_ratio <= FIT_SHRINK_DEEP
            else (1.0 if pull_ratio <= FIT_SHRINK_MID else 0.0)
        )
    comp["shrink_degree"] = {"points": shrink_pts, "pullback_vol_ratio": pull_ratio}

    # MACD 零轴上：DIF>0 → 1（图集多数案例 DIF 在零轴上方）
    m = macd_state if (macd_state or {}).get("available") else macd(df)
    macd_pts = 0.0
    dif_val = None
    if m.get("available"):
        dif_val = m.get("dif")
        macd_pts = 1.0 if (dif_val is not None and dif_val > 0) else 0.0
    comp["macd_above_zero"] = {"points": macd_pts, "dif": dif_val}

    # DKS 上行：DKS[t] > DKS[t-5] → 1（慢线本身走升）
    dks_pts = 0.0
    dks_now = dks_prev = None
    close_s = df["close"].astype(float).reset_index(drop=True)
    if len(close_s) >= max(DKS_MA_WINDOWS) + FIT_DKS_SLOPE_DAYS:
        dks = dks_series(close_s)
        dks_now = float(dks.iloc[-1])
        dks_prev = float(dks.iloc[-1 - FIT_DKS_SLOPE_DAYS])
        dks_pts = 1.0 if dks_now > dks_prev else 0.0
    comp["dks_rising"] = {"points": dks_pts, "dks": dks_now, "dks_prev": dks_prev}

    total = round(sum(c["points"] for c in comp.values()), 2)
    return {"score": total, "max_score": 8, "components": comp}
