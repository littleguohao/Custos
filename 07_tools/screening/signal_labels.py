# -*- coding: utf-8 -*-
"""研究因子的**信号标注层**（三态：hit / miss / unavailable）。

设计边界（owner 2026-08-04 裁定）::

    A. 纯标注（本模块）    不改分层、不改 next_step、不筛候选  → 风险≈0，直接上线
    B. 加分/减分            改 total → 改 A/B/C/D → 改"可买"清单 → **必须先回测**
    C. 封顶/否决            同上                                 → **必须先回测**

⚠️ **这些因子已在跨窗终审中被否决**（`research/R6_hypothesis_H1_dual_axis.md` 与 `R7_hypothesis_H2_b1b2b3.md`，2026-08-03）：
b1_dual 系、B2/异动系、`j_low_qsx_weekly` 的 edge 只存在于 2025-2026 单一 regime，
跨区间不成立；`surge_strict_then_b1` 跨 seed 方向翻转、跨区间零信号。
**所以标注不是交易依据，尤其不得据标注数决定仓位**——那个推论（"标注多⇒确信度高"）
已被证伪。

标注层保留的理由：① A 类改动不改分层/next_step，**不会造成损失**；② 作为**观察记录**，
便于积累"这些形态在什么行情下出现"的直觉、也便于复盘被否决的因子在实盘里长什么样；
③ 负向标注（出货形态）本就是既有证据层信息。若日后某因子通过跨窗终审，再单独升级为加分。

**为什么必须三态而非二态**：`min_list_days=60`，而 `qsx_gt_dks` 需要 ≥120 根（DKS=MA114）、
`surge_then_b1` 需要 ≥200 根（9个月新高）。大量候选会**算不出来**。若把"算不出来"显示成
"未命中"，读者会误以为"这票不符合这个条件"，而实际是"不知道"——这正是本次审计反复
出现的失效模式（缺数据伪装成好数据）。故命中率分母用**可评估数**而非总数。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

_TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(_TOOLS), str(_TOOLS / "screening"), str(_TOOLS / "market_timing")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_FACTORS_DIR = str(Path(__file__).resolve().parents[1] / "factors")
if _FACTORS_DIR not in sys.path:
    sys.path.insert(0, _FACTORS_DIR)   # 因子层：见 factors/__init__.py


HIT, MISS, NA = "hit", "miss", "unavailable"

# 标注定义：key → (中文名, 表格缩写, 方向)。方向 +1=正向、-1=负向。
SIGNAL_META: dict[str, tuple[str, str, int]] = {
    "qsx_gt_dks":           ("长期多头结构(QSX>DKS)", "QD", +1),
    "weekly_j_low":         ("周线B1(周J<13)", "W", +1),
    "rsi_strong":           ("RSI强势区间", "RS", +1),
    "rsi_deep_oversold":    ("RSI深水区", "RD", +1),
    "rsi_ideal_b1":         ("RSI理想B1(强势+深水)", "R★", +1),
    "rsi_bull_div":         ("RSI底背离", "RV", +1),
    "b2":                   ("B2确认(B1后放量涨4%)", "B2", +1),
    "bottom_surge":         ("底部异动(巨量点火)", "SG", +1),
    "surge_then_b1":        ("异动后的B1", "SB", +1),
    "breakout_pullback_b1": ("突破回踩型B1", "PB", +1),
    "main_rally":           ("主升始发点", "MR", +1),
    "distribution_risk":    ("主力出货形态", "⚠出货", -1),
}
POSITIVE = [k for k, v in SIGNAL_META.items() if v[2] > 0]
NEGATIVE = [k for k, v in SIGNAL_META.items() if v[2] < 0]


def _state(available: bool, hit: bool) -> str:
    return (HIT if hit else MISS) if available else NA


def compute_signals(df: pd.DataFrame, code: str = "", *,
                    daily_j: Optional[float] = None,
                    weekly_j_low: Optional[bool] = None,
                    weekly_j_available: Optional[bool] = None,
                    zx: Optional[dict] = None,
                    distribution: Optional[dict] = None,
                    platform_pullback: Optional[dict] = None) -> dict[str, Any]:
    """算出全部标注（三态）。**尽量复用调用方已算好的结果**，绝不 raise。

    可注入项都是 enrich 的 compute_metrics 已经算过的：``daily_j``（kdj）、
    ``weekly_j_low``（weekly_j_state）、``zx``（zhixing_state，含 qsx_gt_dks）、
    ``distribution``（detect_distribution）、``platform_pullback``。
    不注入时本模块自己算——但那会白付一次 resample（2.3ms）与若干次 kdj。
    """
    out: dict[str, Any] = {}

    def put(key: str, available: bool, hit: bool, **detail):
        out[key] = {"state": _state(available, hit), **detail}

    # ---- 复用型（零增量成本）----
    if zx is not None:
        put("qsx_gt_dks", bool(zx.get("available")), bool(zx.get("qsx_gt_dks")),
            qsx=zx.get("qsx"), dks=zx.get("dks"))
    else:
        try:
            from technical_monitor import zhixing_state
            z = zhixing_state(df)
            put("qsx_gt_dks", bool(z.get("available")), bool(z.get("qsx_gt_dks")))
        except Exception:  # noqa: BLE001
            put("qsx_gt_dks", False, False, reason="zhixing_unavailable")

    if weekly_j_low is not None:
        avail = weekly_j_available if weekly_j_available is not None else True
        put("weekly_j_low", bool(avail), bool(weekly_j_low))
    else:
        try:
            from enrich_candidates import weekly_j_state
            w = weekly_j_state(df)
            put("weekly_j_low", bool(w.get("weekly_j_available")), bool(w.get("weekly_j_low")),
                weekly_j=w.get("weekly_j"))
        except Exception:  # noqa: BLE001
            put("weekly_j_low", False, False, reason="weekly_unavailable")

    if distribution is not None:
        lvl = str(distribution.get("risk_level") or "none")
        put("distribution_risk", bool(distribution.get("available")),
            lvl in ("high", "watch"), risk_level=lvl,
            hits=distribution.get("hits"))
    else:
        put("distribution_risk", False, False, reason="not_provided")

    # ---- RSI 三项（新增计算，约 1.9ms/票）----
    try:
        from rsi_state import rsi_divergence, rsi_regime
        reg = rsi_regime(df)
        avail = bool(reg.get("available"))
        put("rsi_strong", avail, reg.get("state") == "strong",
            regime=reg.get("state"), rsi=reg.get("rsi"))
        put("rsi_deep_oversold", avail, bool(reg.get("deep_oversold")), rsi=reg.get("rsi"))
        put("rsi_ideal_b1", avail,
            bool(reg.get("state") == "strong" and reg.get("deep_oversold")))
        div = rsi_divergence(df)
        put("rsi_bull_div", bool(div.get("available")), bool(div.get("bullish")),
            cur_rsi=div.get("cur_rsi"), prior_rsi=div.get("prior_rsi"))
    except Exception as exc:  # noqa: BLE001
        for k in ("rsi_strong", "rsi_deep_oversold", "rsi_ideal_b1", "rsi_bull_div"):
            put(k, False, False, reason=f"rsi_error:{type(exc).__name__}")

    # ---- B2 / 底部异动（新增计算，约 2.0ms/票）----
    try:
        from b2_surge_factor import _j_series, detect_b2, detect_bottom_surge, detect_surge_then_b1
        js = _j_series(df)
        b2 = detect_b2(df, code, j_series=js)
        put("b2", bool(b2.get("available")), bool(b2.get("hit")),
            gain_pct=b2.get("gain_pct"), b1_bars_ago=b2.get("b1_bars_ago"))
        sg = detect_bottom_surge(df, code)
        put("bottom_surge", bool(sg.get("available")), bool(sg.get("hit")),
            vol_ratio=sg.get("vol_ratio_ma20"), bars_ago=sg.get("bars_ago"),
            strict=sg.get("strict_hit"))
        sb = detect_surge_then_b1(df, code)
        put("surge_then_b1", bool(sb.get("available")), bool(sb.get("hit")))
    except Exception as exc:  # noqa: BLE001
        for k in ("b2", "bottom_surge", "surge_then_b1"):
            put(k, False, False, reason=f"surge_error:{type(exc).__name__}")

    # ---- 突破回踩型 B1（复用 platform_pullback + daily_j）----
    try:
        from b1_dual_factor import detect_breakout_pullback_b1
        if platform_pullback is not None and daily_j is not None:
            ph = float(platform_pullback.get("platform_high") or 0.0)
            close = float(df["close"].astype(float).iloc[-1])
            hit = bool(ph and close >= ph * 0.98 and daily_j < 13.0)
            put("breakout_pullback_b1", True, hit, platform_high=ph or None)
        else:
            r = detect_breakout_pullback_b1(df, code)
            put("breakout_pullback_b1", bool(r.get("available")), bool(r.get("hit")),
                platform_high=r.get("platform_high"))
    except Exception as exc:  # noqa: BLE001
        put("breakout_pullback_b1", False, False,
            reason=f"platform_error:{type(exc).__name__}")

    # ---- 主升始发点（新增计算，约 3.8ms/票）----
    try:
        from main_rally_factor import detect_main_rally_start
        mrr = detect_main_rally_start(df, code)
        put("main_rally", bool(mrr.get("available")), bool(mrr.get("hit")),
            flow_ratio=mrr.get("flow_ratio"), rsi7=mrr.get("rsi7"), cci=mrr.get("cci"),
            conditions_met=mrr.get("conditions_met"))
    except Exception as exc:  # noqa: BLE001
        put("main_rally", False, False, reason=f"main_rally_error:{type(exc).__name__}")

    return {**out, "summary": summarize_signals(out)}


def summarize_signals(signals: dict[str, Any]) -> dict[str, Any]:
    """汇总：正向命中数 / **可评估数**（分母排除 unavailable）。

    分母用可评估数而非总数：新股因数据不足只能评估 4 项、命中 3 项，应显示 3/4 而不是
    3/12 —— 后者会把"数据不足"误读成"质量差"。
    """
    pos_hit = [k for k in POSITIVE if signals.get(k, {}).get("state") == HIT]
    pos_eval = [k for k in POSITIVE if signals.get(k, {}).get("state") in (HIT, MISS)]
    neg_hit = [k for k in NEGATIVE if signals.get(k, {}).get("state") == HIT]
    na = [k for k in SIGNAL_META if signals.get(k, {}).get("state") == NA]
    return {
        "positive_hits": pos_hit, "positive_hit_count": len(pos_hit),
        "positive_evaluable": len(pos_eval),
        "negative_hits": neg_hit, "negative_hit_count": len(neg_hit),
        "unavailable": na, "unavailable_count": len(na),
        "label": f"{len(pos_hit)}/{len(pos_eval)}",
        "abbrs": [SIGNAL_META[k][1] for k in pos_hit],
        "neg_abbrs": [SIGNAL_META[k][1] for k in neg_hit],
    }
