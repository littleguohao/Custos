# -*- coding: utf-8 -*-
"""研究：打分重构——让 TOP20% 赢家在得分上浮现（owner 2026-08-25 批准）。

> ⚠️ **R11 警示**：基准已实现口径为负、vipdoc 宇宙带幸存者偏差——读数仅供
> 相对排序。**R3 纪律**：单窗正 = 不作数，须前后半窗 + 跨窗一致。
> **R21 警示**：画像层富集 ≠ 可交易（rsi_div gate 证伪在先）——变体是否
> 「能选出赢家」以篮子实测为准，不以相关性推断。

## 变体（全部 cand→score 确定性函数，权重 = 简单整数，可回流 scoring.weights 形态）

- **V0** = 现行 live 技术分（对照；由 factor_contrib 重建，与落盘分逐位一致，钉测钉住）
- **V1** = 反向腿取反：R20 反向腿清单的贡献分值变号（macd_top_divergence 现行
  −8 随之变 +8），其余不动，clamp 0-100
- **V2** = 证据重构：只留 R20 正向腿——rsi_deep_oversold **40** / weekly_j_low 20 /
  rsi_bull_div 20 / macd_bottom_divergence 20（上限 100；j_low 是基底恒真不计）
- **V3** = V2 + 反向腿当负向证据：11 条反向腿每条命中 **−5**，clamp 0-100

## 判据（**预注册**，跑前定死，不许事后凑数）

- **C1**：变体分 vs 收益 Spearman > 0（全体）且前后半窗同正
- **C2**：收益 TOP20% 赢家组的变体均分 > bottom-80% 均分
- **C3**：按变体分选 top20% 篮子的**胜率 > V0 篮子胜率**，且**盈亏比 ≥ V0 篮子**
  （「浮现出来」的直接度量：胜率升、盈亏比不塌）
- **C3_relaxed**（owner 放宽线，v0.121，事后并列）：胜率 > V0 且盈亏比 ≥ 2.4
- **C3_natural**（天然基准，v0.128 owner 拍板「和天然胜率盈亏比比较」）：
  变体篮子 margin > V0 篮子 margin，margin = 胜率 − 盈亏平衡胜率 1/(1+盈亏比)，
  零自由参数；Wilson 95% 重叠仅作显著性注记，不改变判定
- **C3_natural_vs_universe**（全样本天然基准，R24 Phase 0，2026-08-28 owner：
  天然=无因子影响的胜率/盈亏比组合）：变体篮子 margin > **全样本** margin；
  与全样本胜率的 Wilson 95% 重叠同样仅作注记——与既有三列**并列第四列，互不覆盖**
- **C4**：跨窗（2022-2024，s1000）C1 符号保持
判定：C1–C3 全过 ⇒ 候选；C4 再过 ⇒ 跨窗确认。C1–C3 任一不过 ⇒ 该变体淘汰。
四个 C3 口径**并列展示、互不覆盖**（出处见报告 criteria_provenance）。
逐腿边际分析（ablation）在姊妹脚本 `score_calibration_study.py`（R24 Phase 0）。

多比较纪律：变体 ≤4、权重简单整数、一轮数据采集（panel+contrib 一次算好，
变体离线求值——交易集与打分无关，baseline scorer 进场只由 j_low gate 决定）。
出场与 v0.118 臂完全一致：stop12 + 保本 0.05 + 分批止盈 0.5 + BBI 连破 2 根 + 25bps。

CLI::

    uv run python src/custos/research/score_variants_study.py                      # 主臂（全历史 400 只）
    uv run python src/custos/research/score_variants_study.py --start 2022-01-01 \\
        --end 2024-12-31 --max-stocks 1000 --tag cw                               # 跨窗臂
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.research import backtest_factors as bf  # noqa: E402
from custos.research import score_return_study as srs  # noqa: E402
from custos.research import winner_factor_study as wfs  # noqa: E402
from custos.research.m2_stop_sweep import _margin  # noqa: E402

# 出场口径 = v0.118 臂（owner 指定，不再作为扫描变量）
ARM_STOP_PCT = 12.0
ARM_BREAKEVEN = 0.05
ARM_SCALE_OUT = 0.5
TOP_FRAC = 0.20  # 赢家组分位（与 v0.118 一致）

# factor_contrib 里的**证据键**（不计分——technical_score 的 evidence_only 列）：
# 重建 V0 时必须排除，否则比落盘分多出 perfect_b1_fit 的分值。
_EVIDENCE_ONLY_CONTRIB = {"perfect_b1_fit"}

# R20 反向腿（稳定富集在输家侧，lift 0.6~0.9）→ 映射到 contrib 键。
# ⚠️ macd_top_divergence 现行已是 −8（负腿），「变号」对它意味着 +8。
_REVERSE_CONTRIB_KEYS = (
    "bbi_above",
    "reversal_k_candidate",
    "volume_contraction",
    "relative_strength_strong",
    "macd_above_water",
    "macd_wm_bar_grow",
    "macd_top_divergence",
    "pullback_shrink",
    "b1_ignition",
)
# 同一清单的 panel 键版（V3 负向证据用；panel 覆盖 contrib 之外的 rsi_strong /
# platform_pullback_b1 两条腿）
_REVERSE_PANEL_KEYS = (
    "bbi_above",
    "reversal_k_candidate",
    "volume_contraction",
    "relative_strength_strong",
    "macd_above_water",
    "macd_wm_bar_grow",
    "macd_top_divergence",
    "pullback_shrink",
    "b1_ignition",
    "rsi_strong",
    "platform_pullback_b1",
)

# V2 正向腿权重（简单整数，上限 100；R20 四臂证据：深水 RSI 最强给大权重）
_V2_WEIGHTS = {
    "rsi_deep_oversold": 40,
    "weekly_j_low": 20,
    "rsi_bull_div": 20,
    "macd_bottom_divergence": 20,
}
_V3_REVERSE_PENALTY = 5  # V3：每条反向腿命中 −5（简单整数）

# C3 放宽线（owner 2026-08-26 拍板）：预注册 C3 要求「篮子盈亏比 ≥ V0」不动
# （候选判定仍以预注册为准）；放宽线 = 篮子胜率 > V0 **且** 盈亏比 ≥ 2.4 绝对
# 下限——胜率大幅提升（V2/V3 主窗 27%→47%、跨窗 29%→54%）下允许盈亏比小幅
# 回落（2.81→2.41）。⚠️ 这是**事后**判据，与预注册并列展示，不 retroactive
# 改写；放宽候选进下一步（strategy_grid / live 提案）前仍须独立窗口复核。
PAYOFF_FLOOR_RELAXED = 2.4

# C3 天然基准 margin（owner 2026-08-28 拍板）：「胜率/盈亏比的标准不能拍脑袋定，
# 要和天然胜率盈亏比比较」——天然基准 = 同基底同出场的 V0 对照臂。零自由参数：
# 篮子 margin = 篮子胜率 − 盈亏平衡胜率（1/(1+篮子盈亏比)），复用 m2_stop_sweep
# 的 _margin/_breakeven_wr（不重写）；变体篮子 margin > V0 篮子 margin ⇒ 过。
# 与预注册 C3（≥V0 盈亏比）、放宽 C3（≥2.4）**三列并列**，互不覆盖。


# ---------------------------------------------------------------------------
# 变体打分（纯函数：只读 trade 的 factor_contrib / panel，无任何行情访问）
# ---------------------------------------------------------------------------


def _clamp(score: float) -> int:
    return int(min(100, max(0, round(score))))


def v0_score(trade: dict[str, Any]) -> int:
    """V0 现行技术分：contrib 求和（排除证据键）clamp——与落盘 tech_score 逐位一致。"""
    contrib = trade.get("factor_contrib") or {}
    return _clamp(
        sum(
            float(v)
            for k, v in contrib.items()
            if k not in _EVIDENCE_ONLY_CONTRIB and isinstance(v, (int, float))
        )
    )


def v1_score(trade: dict[str, Any]) -> int:
    """V1 反向腿取反：V0_raw − 2×（反向腿贡献之和）⇒ 反向腿分值变号。"""
    contrib = trade.get("factor_contrib") or {}
    base = sum(
        float(v)
        for k, v in contrib.items()
        if k not in _EVIDENCE_ONLY_CONTRIB and isinstance(v, (int, float))
    )
    reverse = sum(float(contrib[k]) for k in _REVERSE_CONTRIB_KEYS if k in contrib)
    return _clamp(base - 2 * reverse)


def v2_score(trade: dict[str, Any]) -> int:
    """V2 证据重构：只留 R20 正向腿（unavailable=None 按不命中=0 分，不惩罚数据缺失）。"""
    panel = trade.get("panel") or {}
    return _clamp(sum(w for k, w in _V2_WEIGHTS.items() if panel.get(k) is True))


def v3_score(trade: dict[str, Any]) -> int:
    """V3 = V2 + 反向腿负向证据（每条命中 −5）。"""
    panel = trade.get("panel") or {}
    penalty = _V3_REVERSE_PENALTY * sum(
        1 for k in _REVERSE_PANEL_KEYS if panel.get(k) is True
    )
    return _clamp(v2_score(trade) - penalty)


VARIANTS = {"V0": v0_score, "V1": v1_score, "V2": v2_score, "V3": v3_score}


# ---------------------------------------------------------------------------
# 评估
# ---------------------------------------------------------------------------


def _remap(trades: list[dict[str, Any]], score_fn) -> list[dict[str, Any]]:
    """把变体分塞进 tech_score 键，复用 srs 的全部统计函数（口径零重复）。"""
    return [{**t, "tech_score": score_fn(t)} for t in trades]


def basket_stats(trades: list[dict[str, Any]], score_fn, frac: float) -> dict[str, Any]:
    """按变体分选 top-frac 篮子（得分降序）→ 胜率/盈亏比/均收（「浮现」直接度量）。

    n_win 是原始命中数（Wilson 区间用；win_rate×n 反推会丢精度）。
    """
    ordered = sorted(trades, key=lambda t: score_fn(t), reverse=True)
    import math

    n_top = max(1, math.ceil(len(ordered) * frac)) if ordered else 0
    basket = ordered[:n_top]
    n_win = sum(1 for t in basket if t["ret"] > 0)
    return {**srs.ret_stats(basket), "n": len(basket), "n_win": n_win}


def basket_margin(basket: dict[str, Any]) -> Optional[float]:
    """篮子 margin = 胜率 − 盈亏平衡胜率（m2 的 _margin/_breakeven_wr 口径）。"""
    return _margin(
        {"win": basket.get("win_rate"), "payoff": basket.get("payoff_ratio")}
    )


def wilson_wr_interval(
    wins: int, n: int, z: float = 1.96
) -> tuple[Optional[float], Optional[float]]:
    """胜率 Wilson 95% 区间（两篮子区间是否重叠 = 显著性辅助标注，不改变判定）。"""
    if not n:
        return (None, None)
    p = wins / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return (round(center - half, 4), round(center + half, 4))


def evaluate_variant(
    trades: list[dict[str, Any]], name: str, score_fn
) -> dict[str, Any]:
    """单变体全景：C1 相关性 / C2 赢家组得分 / C3 篮子 / 分档收益表。"""
    remapped = _remap(trades, score_fn)
    top, bottom = srs.split_top_frac(trades, TOP_FRAC)  # 按**收益**切赢家组
    top_scores = [score_fn(t) for t in top]
    bottom_scores = [score_fn(t) for t in bottom]
    return {
        "variant": name,
        "corr": srs.correlations(remapped),  # C1 全体
        "half_window": srs.half_window_check(remapped),  # C1 半窗
        "winner_top20_dist": srs.dist_stats(top_scores),  # C2
        "bottom80_dist": srs.dist_stats(bottom_scores),  # C2
        "basket_top20_by_variant": basket_stats(trades, score_fn, TOP_FRAC),  # C3
        "band_stats": srs.band_stats(remapped),  # 分档收益表（live 30/60 阈）
    }


def _c1_spearman_positive(rep: dict[str, Any]) -> bool:
    """C1：Spearman>0 且前后半窗同正。"""
    sp = rep["corr"].get("spearman")
    hw = rep["half_window"]
    return (
        sp is not None
        and sp > 0
        and hw.get("consistent") is True
        and (hw.get("first_half") or {}).get("spearman") is not None
        and hw["first_half"]["spearman"] > 0
    )


def _wilson_overlap(
    b1: dict[str, Any], b2: dict[str, Any]
) -> tuple[list, list, Optional[bool]]:
    """两方胜率的 Wilson 95% 区间 + 重叠注记（重叠 = 差异不显著，不改判定）。"""
    lo_1, hi_1 = wilson_wr_interval(b1.get("n_win") or 0, b1.get("n") or 0)
    lo_2, hi_2 = wilson_wr_interval(b2.get("n_win") or 0, b2.get("n") or 0)
    overlap = (
        None
        if lo_1 is None or hi_1 is None or lo_2 is None or hi_2 is None
        else not (hi_1 < lo_2 or hi_2 < lo_1)  # 区间相交
    )
    return [lo_1, hi_1], [lo_2, hi_2], overlap


def _c3_columns(
    b: dict[str, Any],
    v0_basket: dict[str, Any],
    universe_stats: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """C3 四口径并列判定 + margin/Wilson 注记读数（互不覆盖）。

    预注册（盈亏比 ≥ V0，v0.119）/ owner 放宽（胜率升 + 盈亏比 ≥ 2.4，v0.121，
    事后并列）/ 天然基准 margin（变体篮子 > V0 篮子，v0.128，零自由参数）/
    全样本天然基准（变体篮子 > **全样本**，R24 Phase 0，2026-08-28 owner：
    天然=无因子影响的胜率/盈亏比组合——「无筛选」才是地板）。
    Wilson 重叠是**显著性辅助注记**（重叠 = 差异不显著，参考用，不改变判定）。
    """
    c3 = b["win_rate"] > v0_basket["win_rate"] and (b["payoff_ratio"] or 0) >= (
        v0_basket["payoff_ratio"] or 0
    )
    # owner 放宽线（2026-08-26）：与预注册 C3 并列，不替代
    c3_relaxed = (
        b["win_rate"] > v0_basket["win_rate"]
        and (b["payoff_ratio"] or 0) >= PAYOFF_FLOOR_RELAXED
    )
    # 天然基准 margin（2026-08-28 owner 拍板）：零自由参数
    m_v = basket_margin(b)
    m_0 = basket_margin(v0_basket)
    c3_natural = m_v is not None and m_0 is not None and m_v > m_0
    # Wilson 95% 区间重叠 ⇒ 胜率差异不显著（辅助注记）
    basket_wil, v0_wil, wilson_overlap = _wilson_overlap(b, v0_basket)
    # 全样本天然基准（R24 Phase 0，2026-08-28 owner：天然=无因子影响组合）——
    # 第四列 C3，与既有三列**并列、互不覆盖**：基准从「V0 篮子」换成「全样本」，
    # 既有三列的输入一个不动。
    u = universe_stats or {}
    m_u = _margin({"win": u.get("win_rate"), "payoff": u.get("payoff_ratio")})
    c3_vs_universe = m_v is not None and m_u is not None and m_v > m_u
    _, u_wil, wilson_overlap_universe = _wilson_overlap(b, u)
    n_u = u.get("n") or 0
    return {
        "C3_basket_wr_up_payoff_kept": c3,
        "C3_relaxed_wr_up_payoff_floor": c3_relaxed,
        "C3_natural_margin": c3_natural,
        "C3_natural_vs_universe": c3_vs_universe,
        "basket_margin": round(m_v, 4) if m_v is not None else None,
        "v0_basket_margin": round(m_0, 4) if m_0 is not None else None,
        "universe_margin": round(m_u, 4) if m_u is not None else None,
        "basket_wr_wilson95": basket_wil,
        "v0_basket_wr_wilson95": v0_wil,
        "universe_wr_wilson95": u_wil,
        "wilson_overlap": wilson_overlap,
        "wilson_overlap_universe": wilson_overlap_universe,
        # 篮子占全样本比例 n/N——C5 候选数约束的读数（top20% 口径下应 ≈0.20）
        "basket_frac_of_universe": (round((b.get("n") or 0) / n_u, 4) if n_u else None),
    }


def judge(
    rep: dict[str, Any],
    v0_basket: dict[str, Any],
    universe_stats: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """按预注册判据 C1–C3 裁决（C4 跨窗在报告层人工对照，不进本函数）。

    C3 四个口径并列（互不覆盖，判定细节见 :func:`_c3_columns`）。
    Wilson 重叠是**显著性辅助注记**（重叠 = 差异不显著，参考用，不改变判定）。

    ``universe_stats`` = 全样本 ret_stats + n_win（build_report 传入）；
    缺省（旧调用）时第四列各键为 None，不影响既有三列。
    """
    c1 = _c1_spearman_positive(rep)
    c2 = (rep["winner_top20_dist"].get("mean") or 0) > (
        rep["bottom80_dist"].get("mean") or 0
    )
    c3_cols = _c3_columns(rep["basket_top20_by_variant"], v0_basket, universe_stats)
    return {
        "C1_spearman_positive": c1,
        "C2_winner_scores_higher": c2,
        **c3_cols,
        "candidate": c1 and c2 and c3_cols["C3_basket_wr_up_payoff_kept"],
        "candidate_relaxed": c1 and c2 and c3_cols["C3_relaxed_wr_up_payoff_floor"],
        "candidate_natural": c1 and c2 and c3_cols["C3_natural_margin"],
        "candidate_vs_universe": c1 and c2 and c3_cols["C3_natural_vs_universe"],
    }


def build_report(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """四变体评估 + 预注册判据裁决（含全样本天然基准第四列 C3）。"""
    variants = {
        name: evaluate_variant(trades, name, fn) for name, fn in VARIANTS.items()
    }
    v0_basket = variants["V0"]["basket_top20_by_variant"]
    # 全样本统计（ret_stats + 原始命中数）——judge 的第四列基准输入
    universe_stats = {
        **srs.ret_stats(trades),
        "n_win": sum(1 for t in trades if t["ret"] > 0),
    }
    verdicts = {
        name: judge(rep, v0_basket, universe_stats)
        if name != "V0"
        else {"note": "对照臂"}
        for name, rep in variants.items()
    }
    return {
        "r11_r3_warning": (
            "⚠️ R11：量级不作数；R3：单窗正不作数（须 C4 跨窗）；"
            "R21：画像富集≠可交易，以篮子实测为准。"
        ),
        "preregistered_criteria": {
            "C1": "Spearman>0 且前后半窗同正",
            "C2": "TOP20% 赢家组变体均分 > bottom-80% 均分",
            "C3": "变体 top20% 篮子胜率 > V0 且盈亏比 ≥ V0（预注册，v0.119）",
            "C3_relaxed": "篮子胜率 > V0 且盈亏比 ≥ 2.4（owner 放宽线，v0.121，事后并列不 retroactive）",
            "C3_natural": "变体篮子 margin > V0 篮子 margin（天然基准，v0.128 owner 拍板；"
            "margin=胜率−盈亏平衡胜率 1/(1+盈亏比)，零自由参数；"
            "Wilson 95% 重叠仅作显著性注记不改判定）",
            "C3_natural_vs_universe": "变体篮子 margin > 全样本 margin（R24 Phase 0，"
            "2026-08-28 owner：天然=无因子影响的胜率/盈亏比组合——「无筛选」地板；"
            "与全样本胜率的 Wilson 95% 重叠仅作显著性注记不改判定）",
            "C4": "跨窗（2022-2024）C1 符号保持",
        },
        "criteria_provenance": (
            "C1/C2/C3=预注册（v0.119）；C3_relaxed=owner 放宽（v0.121，事后并列）；"
            "C3_natural=天然基准 margin（v0.128，owner：「和天然胜率盈亏比比较」）；"
            "C3_natural_vs_universe=全样本天然基准（R24 Phase 0，2026-08-28 owner："
            "天然=无因子影响组合）。四列 C3 并列展示，互不覆盖。"
        ),
        "n_trades": len(trades),
        "overall_stats": srs.ret_stats(trades),
        "universe_stats": universe_stats,
        "exit_reasons": srs.exit_reason_dist(trades),
        "variants": variants,
        "verdicts": verdicts,
    }


def _variant_tags(name: str, vd: dict[str, Any]) -> str:
    """变体判据标签串（V0=对照；Wilson 重叠仅作显著性注记，不改判定）。"""
    if name == "V0":
        return "对照"
    tags = (
        f"{'✓' if vd.get('C1_spearman_positive') else '✗'}"
        f"{'✓' if vd.get('C2_winner_scores_higher') else '✗'}"
        f"{'✓' if vd.get('C3_basket_wr_up_payoff_kept') else '✗'}"
        f"{'✓' if vd.get('C3_relaxed_wr_up_payoff_floor') else '✗'}"
        f"{'✓' if vd.get('C3_natural_margin') else '✗'}"
        f"{'✓' if vd.get('C3_natural_vs_universe') else '✗'}"
        f" ⇒ 预注册{'候选' if vd.get('candidate') else '淘汰'}"
        f"/放宽{'候选' if vd.get('candidate_relaxed') else '淘汰'}"
        f"/天然{'候选' if vd.get('candidate_natural') else '淘汰'}"
        f"/全样本{'候选' if vd.get('candidate_vs_universe') else '淘汰'}"
    )
    if vd.get("wilson_overlap") is True:
        tags += "（Wilson重叠=胜率差异不显著，注记）"
    elif vd.get("wilson_overlap") is False:
        tags += "（Wilson不重叠）"
    if vd.get("wilson_overlap_universe") is False:
        tags += "（vs全样本Wilson不重叠）"
    return tags


def _variant_row(name: str, v: dict[str, Any], rep: dict[str, Any]) -> str:
    """变体对照表单行：Spearman(半窗) | 均分对比 | 篮子指标 | 判据标签。"""
    hw = v["half_window"]
    sp = v["corr"].get("spearman")
    h1 = (hw.get("first_half") or {}).get("spearman")
    h2 = (hw.get("second_half") or {}).get("spearman")
    b = v["basket_top20_by_variant"]
    m = basket_margin(b)
    m_txt = f"{m * 100:+.1f}pp" if m is not None else "—"
    tags = _variant_tags(name, rep["verdicts"].get(name, {}))
    n_all = rep["n_trades"] or 1
    return (
        f"  {name} | {sp}({h1}/{h2}{'' if hw.get('consistent') else ' ⚠️翻'}) | "
        f"{v['winner_top20_dist'].get('mean')} vs {v['bottom80_dist'].get('mean')} | "
        f"{b['win_rate'] * 100:.1f}%/{b['payoff_ratio']}/{b['avg_ret'] * 100:.2f}%/"
        f"n={b['n']}/{n_all}({b['n'] / n_all * 100:.0f}%)/{m_txt} | "
        f"{tags}"
    )


def _band_rows(rep: dict[str, Any]) -> list[str]:
    """分档收益表（live 30/60 阈；变体分档）逐变体一行。"""
    rows = []
    for name, v in rep["variants"].items():
        row = [name]
        for band in ("<30", "30-59", ">=60"):
            st = v["band_stats"].get(band) or {}
            if st.get("n"):
                row.append(
                    f"{band}: {st['avg_ret'] * 100:+.2f}%/{st['win_rate'] * 100:.0f}%/"
                    f"{st['payoff_ratio']}(n={st['n']})"
                )
            else:
                row.append(f"{band}: —")
        rows.append("  " + " | ".join(row))
    return rows


def print_report(rep: dict[str, Any]) -> None:
    """stdout 中文摘要。"""
    print("\n" + "=" * 76)
    print("打分重构研究：哪个变体让 TOP20% 赢家浮现（出场=v0.118 臂 stop12+be05+so05）")
    print("=" * 76)
    print(rep["r11_r3_warning"])
    os_ = rep["overall_stats"]
    print(
        f"\n样本：{rep['n_trades']} 笔；全体均收 {os_['avg_ret'] * 100:.2f}% / "
        f"胜率 {os_['win_rate'] * 100:.1f}% / 盈亏比 {os_['payoff_ratio']}"
    )
    # 全样本 margin = 「无因子天然基准」（R24 判据 C3★ 的地板读数）
    m_u = _margin({"win": os_.get("win_rate"), "payoff": os_.get("payoff_ratio")})
    m_u_txt = f"{m_u * 100:+.1f}pp" if m_u is not None else "—"
    print(f"全样本 margin（无因子天然基准）：{m_u_txt}")
    print(
        "\n── 变体对照：Spearman(半窗) | 赢家top20均分 vs 对照组 | "
        "变体篮子(胜率/盈亏比/均收/n占全样本/margin) | C1/C2/C3预注册/C3放宽/C3天然/C3天然vs全样本"
    )
    for name, v in rep["variants"].items():
        print(_variant_row(name, v, rep))
    print("\n── 分档收益表（live 30/60 阈；变体分档）")
    for row in _band_rows(rep):
        print(row)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-stocks", type=int, default=400, help="宇宙抽样只数")
    ap.add_argument("--seed", type=int, default=0, help="抽样种子")
    ap.add_argument("--start", default="2010-01-01", help="0AMV regime 起点")
    ap.add_argument("--end", default="", help="K线终点（跨窗用；默认全历史）")
    ap.add_argument("--cost-bps", type=float, default=srs.COST_BPS)
    ap.add_argument("--tag", default="", help="落盘文件名标签（如 cw）")
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--from-trades",
        nargs="+",
        default=[],
        help="复用已落盘的研究 JSON（含 trades 键）离线重算变体/判据，不重跑回测；"
        "多个文件 ⇒ 逐份重判并输出跨窗 margin 对照表",
    )
    return ap


def _rejudge_one(path: str, out_arg: str = "") -> dict[str, Any]:
    """离线重判单份落盘 JSON → 新报告（落 .rejudged.json），返回报告。"""
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    trades = stored.get("trades") or []
    if not trades:
        print(f"⛔ 复用文件无 trades: {path}", file=sys.stderr)
        return {}
    print(f"[INFO] 复用 {path}（{len(trades)} 笔）", file=sys.stderr)
    rep = build_report(trades)
    rep["config"] = stored.get("config") or {}
    rep["config"]["rejudged_from"] = str(path)
    rep["trades"] = trades
    out = Path(out_arg) if out_arg else Path(path).with_suffix(".rejudged.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(out, rep, big=len(trades) > 20000)
    print(f"[OK] 写出 {out}（离线重判，{len(trades)} 笔）")
    print_report(rep)
    return rep


def _print_margin_matrix(reps: list[tuple[str, dict[str, Any]]]) -> None:
    """多窗 margin 对照表：变体 × 窗口的篮子 margin（pp）与 Wilson 重叠标记。"""
    print("\n── 跨窗篮子 margin 对照（天然基准口径：margin=胜率−盈亏平衡胜率，pp）")
    header = "变体 | " + " | ".join(label for label, _ in reps)
    print(header)
    for name in VARIANTS:
        row = [name]
        for _label, rep in reps:
            b = rep["variants"][name]["basket_top20_by_variant"]
            m = basket_margin(b)
            cell = f"{m * 100:+.1f}" if m is not None else "—"
            vd = rep["verdicts"].get(name, {})
            if vd.get("wilson_overlap") is False:
                cell += "*"  # 与 V0 篮子胜率 Wilson 95% 不重叠（差异显著）
            row.append(cell)
        print("  " + " | ".join(row))
    print(
        "  注：* = 与 V0 篮子胜率的 Wilson 95% 区间不重叠（差异显著）；无标记 = 重叠/缺数据"
    )


def main(argv: Optional[list] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    if args.from_trades:
        reps: list[tuple[str, dict[str, Any]]] = []
        for f in args.from_trades:
            # 多文件重判时 --out 只许带一个（歧义即错，不静默覆盖）
            rep = _rejudge_one(f, args.out if len(args.from_trades) == 1 else "")
            if rep:
                label = (rep.get("config") or {}).get("rejudged_from") or f
                reps.append((Path(str(label)).name, rep))
        if len(reps) > 1:
            _print_margin_matrix(reps)
        return 0 if reps else 1

    regime = bf.load_amv_regime(since=args.start)
    if not regime:
        ap.error("读不到指南针 0AMV 数据（compass_amv）")
    print(
        f"[INFO] 0AMV regime {len(regime)} 个交易日，做多区间 "
        f"{len(srs.long_intervals(regime))} 段",
        file=sys.stderr,
    )

    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    base = local_tdx_data.list_local_vipdoc_codes()
    codes = bf.sample_codes(base, args.max_stocks, args.seed)
    print(
        f"[INFO] universe=local_vipdoc 共 {len(base)} 只，取 {len(codes)} 只（seed={args.seed}）",
        file=sys.stderr,
    )
    index_df = (
        local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
        .sort_values("date")
        .reset_index(drop=True)
    )

    # 一轮采集：panel_hook 同时产出 factor_contrib + panel，四个变体离线求值
    trades = srs.run_study(
        codes,
        regime,
        index_df,
        cost_bps=args.cost_bps,
        stop_pct=ARM_STOP_PCT,
        breakeven_trigger=ARM_BREAKEVEN,
        scale_out_frac=ARM_SCALE_OUT,
        end=args.end or None,
        trade_hook=wfs.panel_hook,
    )
    if not trades:
        print("⛔ 0 笔交易", file=sys.stderr)
        return 1

    rep = build_report(trades)
    rep["config"] = {
        "signal": "日KDJ J<13 + 0AMV 做多（固定基底）",
        "exit": f"stop{ARM_STOP_PCT} + breakeven {ARM_BREAKEVEN} + scale_out {ARM_SCALE_OUT} + BBI连破2根（=v0.118 臂）",
        "cost_bps": args.cost_bps,
        "top_frac": TOP_FRAC,
        "variants": list(VARIANTS),
        "v2_weights": _V2_WEIGHTS,
        "v3_reverse_penalty": _V3_REVERSE_PENALTY,
        "max_stocks": args.max_stocks,
        "seed": args.seed,
        "start": args.start,
        "end": args.end or None,
        "n_codes": len(codes),
    }
    rep["trades"] = trades

    tag = f"_{args.tag}" if args.tag else ""
    out = (
        Path(args.out)
        if args.out
        else (
            Path("artifacts/logs/score_variants_study")
            / f"score_variants_study_s{args.seed}_n{len(codes)}{tag}.json"
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(out, rep, big=len(trades) > 20000)
    print(f"[OK] 写出 {out}（{len(trades)} 笔）")
    print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
