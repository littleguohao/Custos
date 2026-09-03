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

**为什么必须三态而非二态**：`min_list_days=60`，而 `qsx_resonance_v2` 需要 ≥114 根（DKS=MA114
成形）、`surge_then_b1` 需要 ≥200 根（9个月新高）。大量候选会**算不出来**。若把"算不出来"显示成
"未命中"，读者会误以为"这票不符合这个条件"，而实际是"不知道"——这正是本次审计反复
出现的失效模式（缺数据伪装成好数据）。故命中率分母用**可评估数**而非总数。
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd


HIT, MISS, NA = "hit", "miss", "unavailable"

# 标注定义：key → (中文名, 表格缩写, 方向)。方向 +1=正向、-1=负向。
SIGNAL_META: dict[str, tuple[str, str, int]] = {
    # v0.169（owner）：QG 提至首位；QD（qsx_gt_dks）标注整体撤除（打分链 zhixing 数据键不动）。
    "qsx_resonance_v2": ("QSX共振v2(60根≥2次干净反弹)", "QG", +1),
    "weekly_j_low": ("周线B1(周J<13)", "W", +1),
    "rsi_strong": ("RSI强势区间", "RS", +1),
    "rsi_deep_oversold": ("RSI深水区", "RD", +1),
    "rsi_ideal_b1": ("RSI理想B1(强势+深水)", "R★", +1),
    "rsi_bull_div": ("RSI底背离", "RV", +1),
    "b2": ("B2确认(B1后放量涨4%)", "B2", +1),
    "bottom_surge": ("底部异动(巨量点火)", "SG", +1),
    "surge_then_b1": ("异动后的B1", "SB", +1),
    "breakout_pullback_b1": ("突破回踩型B1", "PB", +1),
    "main_rally": ("主升始发点", "MR", +1),
    "distribution_risk": ("主力出货形态", "⚠出货", -1),
}
POSITIVE = [k for k, v in SIGNAL_META.items() if v[2] > 0]
NEGATIVE = [k for k, v in SIGNAL_META.items() if v[2] < 0]


def _state(available: bool, hit: bool) -> str:
    return (HIT if hit else MISS) if available else NA


def _put(out: dict[str, Any], key: str, available: bool, hit: bool, **detail):
    out[key] = {"state": _state(available, hit), **detail}


# ---- 复用型（零增量成本）----
def _signal_weekly_j_low(
    out: dict[str, Any],
    df: pd.DataFrame,
    weekly_j_low: Optional[bool],
    weekly_j_available: Optional[bool],
):
    if weekly_j_low is not None:
        avail = weekly_j_available if weekly_j_available is not None else True
        _put(out, "weekly_j_low", bool(avail), bool(weekly_j_low))
    else:
        try:
            from custos.pipeline.screening.enrich_candidates import weekly_j_state

            w = weekly_j_state(df)
            _put(
                out,
                "weekly_j_low",
                bool(w.get("weekly_j_available")),
                bool(w.get("weekly_j_low")),
                weekly_j=w.get("weekly_j"),
            )
        except Exception:  # noqa: BLE001
            _put(out, "weekly_j_low", False, False, reason="weekly_unavailable")


def _signal_distribution_risk(out: dict[str, Any], distribution: Optional[dict]):
    if distribution is not None:
        lvl = str(distribution.get("risk_level") or "none")
        _put(
            out,
            "distribution_risk",
            bool(distribution.get("available")),
            lvl in ("high", "watch"),
            risk_level=lvl,
            hits=distribution.get("hits"),
        )
    else:
        _put(out, "distribution_risk", False, False, reason="not_provided")


# ---- RSI 三项（新增计算，约 1.9ms/票）----
def _signal_rsi(out: dict[str, Any], df: pd.DataFrame):
    try:
        from custos.core.factors.rsi_state import rsi_divergence, rsi_regime

        reg = rsi_regime(df)
        avail = bool(reg.get("available"))
        _put(
            out,
            "rsi_strong",
            avail,
            reg.get("state") == "strong",
            regime=reg.get("state"),
            rsi=reg.get("rsi"),
        )
        _put(
            out,
            "rsi_deep_oversold",
            avail,
            bool(reg.get("deep_oversold")),
            rsi=reg.get("rsi"),
        )
        _put(
            out,
            "rsi_ideal_b1",
            avail,
            bool(reg.get("state") == "strong" and reg.get("deep_oversold")),
        )
        div = rsi_divergence(df)
        _put(
            out,
            "rsi_bull_div",
            bool(div.get("available")),
            bool(div.get("bullish")),
            cur_rsi=div.get("cur_rsi"),
            prior_rsi=div.get("prior_rsi"),
        )
    except Exception as exc:  # noqa: BLE001
        for k in ("rsi_strong", "rsi_deep_oversold", "rsi_ideal_b1", "rsi_bull_div"):
            _put(out, k, False, False, reason=f"rsi_error:{type(exc).__name__}")


# ---- B2 / 底部异动（新增计算，约 2.0ms/票）----
def _signal_surge(out: dict[str, Any], df: pd.DataFrame, code: str):
    try:
        from custos.core.factors.b2_surge_factor import (
            _j_series,
            detect_b2,
            detect_bottom_surge,
            detect_surge_then_b1,
        )

        js = _j_series(df)
        b2 = detect_b2(df, code, j_series=js)
        _put(
            out,
            "b2",
            bool(b2.get("available")),
            bool(b2.get("hit")),
            gain_pct=b2.get("gain_pct"),
            b1_bars_ago=b2.get("b1_bars_ago"),
        )
        sg = detect_bottom_surge(df, code)
        _put(
            out,
            "bottom_surge",
            bool(sg.get("available")),
            bool(sg.get("hit")),
            vol_ratio=sg.get("vol_ratio_ma20"),
            bars_ago=sg.get("bars_ago"),
            strict=sg.get("strict_hit"),
        )
        sb = detect_surge_then_b1(df, code)
        _put(out, "surge_then_b1", bool(sb.get("available")), bool(sb.get("hit")))
    except Exception as exc:  # noqa: BLE001
        for k in ("b2", "bottom_surge", "surge_then_b1"):
            _put(out, k, False, False, reason=f"surge_error:{type(exc).__name__}")


# ---- 突破回踩型 B1（复用 platform_pullback + daily_j）----
def _signal_breakout_pullback_b1(
    out: dict[str, Any],
    df: pd.DataFrame,
    code: str,
    platform_pullback: Optional[dict],
    daily_j: Optional[float],
):
    try:
        from custos.core.factors.b1_dual_factor import detect_breakout_pullback_b1

        if platform_pullback is not None and daily_j is not None:
            ph = float(platform_pullback.get("platform_high") or 0.0)
            close = float(df["close"].astype(float).iloc[-1])
            hit = bool(ph and close >= ph * 0.98 and daily_j < 13.0)
            _put(out, "breakout_pullback_b1", True, hit, platform_high=ph or None)
        else:
            r = detect_breakout_pullback_b1(df, code)
            _put(
                out,
                "breakout_pullback_b1",
                bool(r.get("available")),
                bool(r.get("hit")),
                platform_high=r.get("platform_high"),
            )
    except Exception as exc:  # noqa: BLE001
        _put(
            out,
            "breakout_pullback_b1",
            False,
            False,
            reason=f"platform_error:{type(exc).__name__}",
        )


# ---- 主升始发点（新增计算，约 3.8ms/票）----
def _signal_main_rally(out: dict[str, Any], df: pd.DataFrame, code: str):
    try:
        from custos.core.factors.main_rally_factor import detect_main_rally_start

        mrr = detect_main_rally_start(df, code)
        _put(
            out,
            "main_rally",
            bool(mrr.get("available")),
            bool(mrr.get("hit")),
            flow_ratio=mrr.get("flow_ratio"),
            rsi7=mrr.get("rsi7"),
            cci=mrr.get("cci"),
            conditions_met=mrr.get("conditions_met"),
        )
    except Exception as exc:  # noqa: BLE001
        _put(
            out,
            "main_rally",
            False,
            False,
            reason=f"main_rally_error:{type(exc).__name__}",
        )


# ---- QSX 共振 v2（R23 研究因子下沉，仅观察记录）----
def _signal_qsx_resonance_v2(out: dict[str, Any], df: pd.DataFrame):
    """hit 口径 = 成立且未排除（R23 C 臂完整口径：hit & ~excluded）。

    ⚠️ R23：共振计数零筛选价值、「跌破未收复」排除态是全部边际——detail 里
    ``excluded``/``events`` 单独给出，本标注仅作观察记录，非交易依据。
    """
    try:
        from custos.core.factors.qsx_resonance import resonance_v2_snapshot

        snap = resonance_v2_snapshot(df)
        _put(
            out,
            "qsx_resonance_v2",
            bool(snap["available"]),
            bool(snap["hit"]) and not snap["excluded"],
            excluded=snap.get("excluded"),
            events=snap.get("events"),
        )
    except Exception as exc:  # noqa: BLE001
        _put(
            out,
            "qsx_resonance_v2",
            False,
            False,
            reason=f"qsx_resonance_error:{type(exc).__name__}",
        )


def compute_signals(
    df: pd.DataFrame,
    code: str = "",
    *,
    daily_j: Optional[float] = None,
    weekly_j_low: Optional[bool] = None,
    weekly_j_available: Optional[bool] = None,
    distribution: Optional[dict] = None,
    platform_pullback: Optional[dict] = None,
) -> dict[str, Any]:
    """算出全部标注（三态）。**尽量复用调用方已算好的结果**，绝不 raise。

    可注入项都是 enrich 的 compute_metrics 已经算过的：``daily_j``（kdj）、
    ``weekly_j_low``（weekly_j_state）、``distribution``（detect_distribution）、
    ``platform_pullback``。
    不注入时本模块自己算——但那会白付一次 resample（2.3ms）与若干次 kdj。
    """
    out: dict[str, Any] = {}

    _signal_weekly_j_low(out, df, weekly_j_low, weekly_j_available)
    _signal_distribution_risk(out, distribution)
    _signal_rsi(out, df)
    _signal_surge(out, df, code)
    _signal_breakout_pullback_b1(out, df, code, platform_pullback, daily_j)
    _signal_main_rally(out, df, code)
    _signal_qsx_resonance_v2(out, df)

    return {**out, "summary": summarize_signals(out)}


def summarize_signals(signals: dict[str, Any]) -> dict[str, Any]:
    """汇总：正向命中数 / **可评估数**（分母排除 unavailable）。

    分母用可评估数而非总数：新股因数据不足只能评估 4 项、命中 3 项，应显示 3/4 而不是
    3/11 —— 后者会把"数据不足"误读成"质量差"。
    """
    pos_hit = [k for k in POSITIVE if signals.get(k, {}).get("state") == HIT]
    pos_eval = [k for k in POSITIVE if signals.get(k, {}).get("state") in (HIT, MISS)]
    neg_hit = [k for k in NEGATIVE if signals.get(k, {}).get("state") == HIT]
    na = [k for k in SIGNAL_META if signals.get(k, {}).get("state") == NA]
    return {
        "positive_hits": pos_hit,
        "positive_hit_count": len(pos_hit),
        "positive_evaluable": len(pos_eval),
        "negative_hits": neg_hit,
        "negative_hit_count": len(neg_hit),
        "unavailable": na,
        "unavailable_count": len(na),
        "label": f"{len(pos_hit)}/{len(pos_eval)}",
        "abbrs": [SIGNAL_META[k][1] for k in pos_hit],
        "neg_abbrs": [SIGNAL_META[k][1] for k in neg_hit],
    }
