# -*- coding: utf-8 -*-
"""买弱指纹；R2：recall 100% 但期望 −0.42%/笔，劣于无差别进场 +0.96%

2026-08-06 从 `screening/enrich_candidates.py` 抽出（**零行为变化**，逐字搬）。
抽出的动因：因子实现必须**全项目唯一一份**，其他模块通过调用访问 ——
内联在 1723 行的选股链主流程里，既无法单独回测，也无法防止别处再写一份。
"""

from __future__ import annotations

from typing import Any
import numpy as np
from custos.core.indicators import j_series as _j_canonical


FACTOR: dict[str, Any] = {
    "id": "b1_pullback_fit",
    "name": "买弱指纹",
    "kind": "selector",
    "status": "needs_work",
    "evidence": "governance/research/R2_selection_price_volume.md",
    "note": "买弱指纹；R2：recall 100% 但期望 −0.42%/笔，劣于无差别进场 +0.96%",
    "min_bars": 1,
    "live_use": "none",  # v0.50（#37 阶段 A）：已证伪，live 停止逐票计算（原 evidence_only 落盘也下线）
    "stage": "debug",  # v0.50：live 链不再引用（enrich 停算）；研究侧 backtest_factors 仍可直接调
}

B1PB_TREND_MA = 60  # 趋势未破：收盘 > MA60（-1% 容差）
B1PB_MA10_BAND = (
    -12.0,
    1.0,
)  # 短线回踩：收盘距 MA10 落在此带（下方回踩，且不明显站上MA5）
B1PB_PRIOR_GAIN = 25.0  # 前有涨幅：波段起涨 ≥25%
B1PB_PULL_BAND = (4.0, 35.0)  # 回调温和：回调深度 4~35%
B1PB_VOL_MA5_MAX = 1.3  # 缩量企稳：B1日量 ≤1.3×近5日均量
B1PB_JMIN_MAX = 13.0  # J超卖重置：回调段最低 J ≤13
B1PB_BODY_MAX = 4.5  # 小实体企稳：B1日实体 ≤4.5%
B1PB_HIT_MIN = 6  # 命中门槛：7项中 ≥6 项


def compute_b1_pullback_fit(df, precomputed=None) -> dict[str, Any]:
    """完美B1「缩量回踩超卖企稳」买弱指纹评分（0-7）。来源：10只确认赢家(后续大涨)反标。

    与 technical_score(买强) 正交——专抓「上升趋势中缩量回踩到均线、J超卖、企稳」的买弱点。
    ⚠️ 全市场回测已证伪(2026-07):周线交易模拟+0AMV做多+成本下期望 -0.42%/笔，劣于无差别进场
    baseline 的 +0.96%/笔 —— **作进场过滤有害**(排除了做多区间的突破赢家)。仅作描述性证据落盘、
    **绝不作买入依据**、不驱动分层。真正的 edge 在「0AMV择时 + 止损/BBI移动止盈」,不在此形态。
    绝不 raise。

    ``precomputed``：evaluate_trades 逐股预计算的全序列（见
    ``research/backtest_factors._precompute_b1_pullback_series``），给了就做点查询
    （O(n²)→O(n)），None（默认）走原逐切片路径。两路逐位一致：rolling().mean() 与
    J 序列（RSV→EWM→EWM 递归）从第 0 根起算，前缀末点与全序列同位点是同一串浮点运算；
    ⚠️ 只对「从第 0 根开始的前缀切片」有效。逐位等价由
    tests/test_scorer_precompute_equivalence.py 逐 bar 钉住。
    """
    try:
        n = len(df)
        if n < 20:
            return {"available": False, "score": 0, "max_score": 7, "hit": False}
        if precomputed is not None:
            i = n - 1
            c = precomputed["c"].iloc[:n]
            v = precomputed["v"].iloc[:n]
            op = precomputed["op"].iloc[:n]
            ma5_last = precomputed["ma5"][i]
            ma10_last = precomputed["ma10"][i]
            # n<60 时旧路径窗口是 min(60, n)（=全量均值），全序列 rolling(60) 同位点是 NaN
            # ⇒ 按旧表达式在 ≤59 根前缀上现算（O(60)，每股最多前 59 根触发一次）。
            ma60_last = (
                precomputed["ma60"][i]
                if n >= B1PB_TREND_MA
                else c.rolling(min(B1PB_TREND_MA, n)).mean().iloc[-1]
            )
            jser = precomputed["j"].iloc[:n]
        else:
            c = df["close"].astype(float).reset_index(drop=True)
            v = df["volume"].astype(float).reset_index(drop=True)
            op = df["open"].astype(float).reset_index(drop=True)
            ma5_last = c.rolling(5).mean().iloc[-1]
            ma10_last = c.rolling(10).mean().iloc[-1]
            ma60_last = c.rolling(min(B1PB_TREND_MA, n)).mean().iloc[-1]
            jser = _j_canonical(df, fill_na=50.0)
        look = min(45, n)
        hi_i = int(c.iloc[-look:].values.argmax()) + (n - look)  # 波段高点
        up_win = c.iloc[max(0, hi_i - 40) : hi_i + 1]
        lo_before = float(up_win.min()) if len(up_win) else float(c.iloc[hi_i])
        up_gain = (float(c.iloc[hi_i]) / lo_before - 1) * 100 if lo_before else 0.0
        pull_days = (n - 1) - hi_i
        pull_depth = (
            (float(c.iloc[hi_i]) - float(c.iloc[-1])) / float(c.iloc[hi_i]) * 100
            if c.iloc[hi_i]
            else 0.0
        )
        j_min = (
            float(jser.iloc[-(pull_days + 1) :].min())
            if pull_days > 0
            else float(jser.iloc[-1])
        )
        vma5 = float(v.iloc[-5:].mean())
        vol_ratio = float(v.iloc[-1]) / vma5 if vma5 > 0 else 9.0
        body = (
            abs(float(c.iloc[-1]) - float(op.iloc[-1])) / float(c.iloc[-2]) * 100
            if (n >= 2 and c.iloc[-2])
            else 9.0
        )
        d_ma5 = (
            (float(c.iloc[-1]) / float(ma5_last) - 1) * 100
            if not np.isnan(ma5_last)
            else 99.0
        )
        d_ma10 = (
            (float(c.iloc[-1]) / float(ma10_last) - 1) * 100
            if not np.isnan(ma10_last)
            else 99.0
        )
        d_ma60 = (
            (float(c.iloc[-1]) / float(ma60_last) - 1) * 100
            if not np.isnan(ma60_last)
            else -99.0
        )
        comp = {
            "trend_intact": bool(d_ma60 > -1.0),
            "pullback_below_ma10": bool(
                B1PB_MA10_BAND[0] <= d_ma10 < B1PB_MA10_BAND[1] and d_ma5 < 1.5
            ),
            "prior_gain": bool(up_gain >= B1PB_PRIOR_GAIN),
            "pullback_healthy": bool(
                B1PB_PULL_BAND[0] <= pull_depth <= B1PB_PULL_BAND[1]
            ),
            "volume_dryup": bool(vol_ratio <= B1PB_VOL_MA5_MAX),
            "j_oversold_reset": bool(j_min <= B1PB_JMIN_MAX),
            "quiet_candle": bool(body <= B1PB_BODY_MAX),
        }
        score = sum(1 for x in comp.values() if x)
        return {
            "available": True,
            "score": score,
            "max_score": 7,
            "hit": bool(score >= B1PB_HIT_MIN),
            "components": comp,
            "detail": {
                "prior_gain_pct": round(up_gain, 1),
                "pullback_depth_pct": round(pull_depth, 1),
                "dist_ma10_pct": round(d_ma10, 1),
                "dist_ma60_pct": round(d_ma60, 1),
                "vol_vs_ma5": round(vol_ratio, 2),
                "j_min_pullback": round(j_min, 1),
                "body_pct": round(body, 1),
            },
        }
    except Exception:  # noqa: BLE001 —— 坏数据不中断
        return {"available": False, "score": 0, "max_score": 7, "hit": False}
