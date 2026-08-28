# -*- coding: utf-8 -*-
"""研究：R24 打分校准——逐腿边际分析（ablation，离线纯函数，Phase 0 基建）。

> ⚠️ **R11 警示**：量级不作数，读数仅供相对排序。**R3 纪律**：单窗证据不作数，
> 须三窗（主窗/跨窗/pre2019）一致才进结论；pre2019 的 ablation **只读不用作调参**
> （反过拟合纪律第 5 条：终审前不许看）。

回答三个问题（判据与纪律预注册在 `governance/research/R24_score_calibration.md`）：

- **池内命中率**（`pool_hit_rates`）：每腿在样本中的命中率。>90% 标「无区分度」
  （R23 共振 v1 的 99.2% 教训；预期 j_low 在这档——主池 J<13 硬门槛 ⇒ 每票自带
  +24 保底分，这是「分数偏高」地板效应的主因）。
- **add-one 边际**（`add_one_margins`）：每腿命中子集的 margin − 全样本 margin
  （逐腿独立证据：这条腿命中的票整体比「无筛选」好还是差）。
- **leave-one-out 边际**（`leave_one_out_margins`）：V0 打分（factor_contrib 求和，
  排除 evidence-only 键）去掉该腿分值后重选 top-20% 篮子，篮子 margin 相对 V0
  篮子 margin 的变化——**负值 = 现行表里的负贡献腿**（去掉反而更好）。

腿的两个来源：contrib 腿 = `score_candidates.DEFAULT_TECH_WEIGHTS` 的计分键
（tech_strong/tech_mid_fallback 是阈值不是腿；repair_signals_each/cap 是合成参数，
真正的腿是合并后的 contrib 键 repair_signals）；panel-only 腿 = 29 键面板里
contrib 没有的腿（rsi_strong / platform_pullback_b1 / rsi_deep_oversold 等），
不进 V0 打分 ⇒ 只有命中率与 add-one，没有 LOO。

CLI（生产机 Phase 1 照抄）::

    uv run python src/custos/research/score_calibration_study.py --ablation \\
        --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n400.rejudged.json \\
        artifacts/logs/score_variants_study/score_variants_study_s0_n1000_cw.rejudged.json \\
        artifacts/logs/score_variants_study/score_variants_study_s0_n1000_pre2019.rejudged.json

逐文件（=逐窗口）输出「腿 × {命中率, add-one margin, LOO margin}」证据表
（stdout + 每份输入旁落 `<文件名>.ablation.json`）。窗口标签默认取文件名，
`--tag` 可覆盖（多文件共用一个 tag 时标签会重复，建议逐窗单跑）。
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

from custos.pipeline.screening import score_candidates as sc  # noqa: E402
from custos.research import backtest_factors as bf  # noqa: E402
from custos.research import score_return_study as srs  # noqa: E402
from custos.research import score_variants_study as svs  # noqa: E402
from custos.research import winner_factor_study as wfs  # noqa: E402

# 权重表里的**非腿键**：tech_strong/tech_mid_fallback 是分层阈值；
# repair_signals_each/cap 是「每项分/上限」合成参数——真正的腿是合并后的
# contrib 键 repair_signals（见 score_candidates._b1_bonus_score）。
_NON_LEG_WEIGHT_KEYS = {
    "tech_strong_fallback",
    "tech_mid_fallback",
    "repair_signals_each",
    "repair_signals_cap",
}

# contrib 腿（顺序 = DEFAULT_TECH_WEIGHTS 声明序，展示稳定）：
# 权重表计分键逐键收录；repair_signals_each/cap 是「每项分/上限」合成参数——
# 真正的腿是它们合成的单一 contrib 键 repair_signals（见 _b1_bonus_score），
# 在 each/cap 的位置补录。
CONTRIB_LEG_KEYS: list[str] = []
for _k in sc.DEFAULT_TECH_WEIGHTS:
    if _k in _NON_LEG_WEIGHT_KEYS:
        if _k == "repair_signals_each":
            CONTRIB_LEG_KEYS.append("repair_signals")
        continue
    CONTRIB_LEG_KEYS.append(_k)

# panel-only 腿：29 键面板里 contrib 没有的证据腿（不进 V0 打分 ⇒ 无 LOO）
PANEL_ONLY_LEG_KEYS: list[str] = [
    k for k in wfs.PANEL_KEYS if k not in CONTRIB_LEG_KEYS
]

# 池内命中率 > 此值 ⇒ 标「无区分度」（R23 共振 v1 命中率 99.2% ≈ 无过滤的教训）
NO_DISCRIMINATION_HIT_RATE = 0.90

# ---------------------------------------------------------------------------
# Phase 2：候选方案构造（预注册规则，见 R24 Phase 2 节 / 本文件 Phase2 docstring）
# ---------------------------------------------------------------------------
#
# 构造依据（Phase 1 三窗 ablation 证据，2026-08-28 跑数）：
# ① 池内命中率 >90% ⇒ 归零/移除：j_low（100% 命中、+24 保底分——地板效应主因，
#    **门槛保留、分值归零**）；
# ② add-one margin 三窗一致为正 ⇒ 保留/加权（括号内为主窗/跨窗/pre2019）：
#    rsi_deep_oversold（+33.0/+37.2/+6.3）、weekly_j_low（+8.0/+9.8/+2.5）、
#    macd_bottom_divergence（+2.1/+3.2/+3.4）、rsi_bull_div（+3.0/+3.1/+6.9）；
# ③ add-one margin 三窗一致为负 ⇒ 移除或取负：
#    rsi_strong（−8.8/−14.2/−5.3）、b1_ignition（−7.7/−15.6/−3.4）、
#    volume_contraction（−8.8/−7.6/−3.0）、relative_strength_strong（−3.1/−3.6/−3.3）、
#    macd_top_divergence（−3.8/−6.7/−0.8）、ignition（−1.9/−2.3/−3.5）；
# ④ 三窗翻号的腿不用（zhixing 系/pullback_shrink/platform_pullback_b1/
#    b1_healthy_pullback_pack/macd_above_water 等——pre2019 变号）。
# ⚠️ pre2019 列仅引用 owner 转述的 Phase 1 数字；Phase 2 调参/评估只用主窗+跨窗
#    （CLI 对 pre2019 输入硬拒绝，反过拟合纪律第 5 条代码化）。
PHASE2_NEG_LEGS = (
    "rsi_strong",
    "b1_ignition",
    "volume_contraction",
    "relative_strength_strong",
    "macd_top_divergence",
    "ignition",
)

# 候选方案（≤4，权重简单整数；multiplier 语义见 make_candidate_score）
PHASE2_CANDIDATES: dict[str, dict[str, Any]] = {
    "P0_min_change": {
        "desc": "最小改动臂（对照）：现行分只去负腿/归零地板——j_low 24→0，"
        "contrib 内 5 条三窗负腿归零（rsi_strong 不在 V0 分，本臂无影响）",
        "contrib_mult": {
            "j_low": 0.0,
            "b1_ignition": 0.0,
            "volume_contraction": 0.0,
            "relative_strength_strong": 0.0,
            "macd_top_divergence": 0.0,
            "ignition": 0.0,
        },
        "panel_weights": {},
    },
    "P1_rebuild": {
        "desc": "证据重构：只留三窗一致正腿（=R22 V2 形态的 R24 复验）",
        "contrib_mult": {},  # 空 = 全部现行腿归零（见 make_candidate_score 语义）
        "panel_weights": {
            "rsi_deep_oversold": 40,
            "weekly_j_low": 20,
            "rsi_bull_div": 20,
            "macd_bottom_divergence": 20,
        },
    },
    "P2_rebuild_neg": {
        "desc": "P1 + 三窗负腿取负（每条 −5，=R22 V3 形态的 R24 复验）",
        "contrib_mult": {},
        "panel_weights": {
            "rsi_deep_oversold": 40,
            "weekly_j_low": 20,
            "rsi_bull_div": 20,
            "macd_bottom_divergence": 20,
            **{k: -5 for k in PHASE2_NEG_LEGS},
        },
    },
    "P3_rebuild_leader": {
        "desc": "P1 + leader_volume 20（同样满足规则②：add-one +3.6/+9.0/+1.7 三窗正；"
        "⚠️ 其 LOO 三窗皆负（−0.3/−0.3/−1.2），证据互斥，单列一臂检验）",
        "contrib_mult": {},
        "panel_weights": {
            "rsi_deep_oversold": 40,
            "weekly_j_low": 20,
            "rsi_bull_div": 20,
            "macd_bottom_divergence": 20,
            "leader_volume": 20,
        },
    },
}

# C5 候选数约束（预注册）：强档（≥60）占当日池 ≤15%、A 桶 ≤5%。
# ⚠️ A 桶需要资金意图轴（capital_intent），离线产物没有 ⇒ 只评强档占比一半，
#    A 桶如实标「离线不可算」。
C5_STRONG_FRAC_MAX = 0.15

# Phase 3 终审名单（Phase 2 实跑推荐，2026-08-28 v0.132；P0 两窗全灭已淘汰）
PHASE3_FINALIST_NAMES = ("P1_rebuild", "P2_rebuild_neg", "P3_rebuild_leader")


def make_candidate_score(
    contrib_mult: dict[str, float], panel_weights: dict[str, float]
):
    """候选打分（纯函数 trade→score，与 score_variants_study 变体机制兼容）。

    - contrib 腿：默认按现行分值计入（multiplier 缺省 = 1）；``contrib_mult``
      里的键按倍率缩放（0 = 归零/移除）。
      ⚠️ 特例：**给了 panel_weights 且 contrib_mult 为空 dict** ⇒ 现行腿全部
      不计（证据重构形态——P1/P2/P3 从零搭，不继承任何现行腿）。
    - panel 腿：命中（True）即加对应整数权重（负权重 = 负向证据）；
      unavailable（None）按不命中 = 0 分（不惩罚数据缺失，同 R22 口径）。
    """

    def _cand_score(trade: dict[str, Any]) -> int:
        contrib = trade.get("factor_contrib") or {}
        panel = trade.get("panel") or {}
        total = 0.0
        if contrib_mult or not panel_weights:
            for k, v in contrib.items():
                if (
                    k in CONTRIB_LEG_KEYS
                    and k not in svs._EVIDENCE_ONLY_CONTRIB
                    and isinstance(v, (int, float))
                ):
                    total += float(v) * contrib_mult.get(k, 1.0)
        for k, w in panel_weights.items():
            if panel.get(k) is True:
                total += w
        return svs._clamp(total)

    return _cand_score


def c5_strong_frac(band_stats: dict[str, Any], n_trades: int) -> dict[str, Any]:
    """C5 的可算一半：强档（≥60）占样本比例 ≤ 15%（A 桶离线不可算，如实标注）。"""
    n_strong = (band_stats.get(">=60") or {}).get("n") or 0
    frac = round(n_strong / n_trades, 4) if n_trades else None
    return {
        "strong_frac": frac,
        "pass": (frac is not None and frac <= C5_STRONG_FRAC_MAX),
        "a_bucket": "离线不可算（需资金意图轴；仅评强档占比一半）",
    }


def _universe_stats_of(trades: list[dict[str, Any]]) -> dict[str, Any]:
    return {**srs.ret_stats(trades), "n_win": sum(1 for t in trades if t["ret"] > 0)}


def eval_candidate(trades: list[dict[str, Any]], name: str, score_fn) -> dict[str, Any]:
    """单候选单窗口：C1/C2/C3★（全样本天然基准）/C5 + 篮子指标。"""
    rep = svs.evaluate_variant(trades, name, score_fn)
    v0_basket = svs.basket_stats(trades, svs.v0_score, svs.TOP_FRAC)
    vd = svs.judge(rep, v0_basket, _universe_stats_of(trades))
    c5 = c5_strong_frac(rep["band_stats"], len(trades))
    b = rep["basket_top20_by_variant"]
    return {
        "candidate": name,
        "n_trades": len(trades),
        "corr": rep["corr"],
        "half_window": rep["half_window"],
        "winner_top20_mean": rep["winner_top20_dist"].get("mean"),
        "bottom80_mean": rep["bottom80_dist"].get("mean"),
        "basket": b,
        "basket_margin": vd.get("basket_margin"),
        "universe_margin": vd.get("universe_margin"),
        "C1": vd["C1_spearman_positive"],
        "C2": vd["C2_winner_scores_higher"],
        "C3_star": vd["C3_natural_vs_universe"],
        "C3_star_wilson_overlap": vd.get("wilson_overlap_universe"),
        "C5": c5,
        "pass_all": bool(
            vd["C1_spearman_positive"]
            and vd["C2_winner_scores_higher"]
            and vd["C3_natural_vs_universe"]
            and c5["pass"]
        ),
    }


def sensitivity_scan(
    trades_by_window: dict[str, list[dict[str, Any]]],
    name: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """入选方案每腿权重 ±50% 扰动（归零腿单侧恢复 50%），逐窗查 C3★ 是否翻转。

    翻转（基线过 ⇒ 扰动后不过，或反向）即标「参数敏感」（R24 纪律第 3 条）。
    判定对象 = C3★（margin > 全样本 margin；Wilson 重叠仅注记）。
    """
    perturbations: list[tuple[str, str, float]] = []  # (kind, leg, new_value)
    for leg, mult in spec["contrib_mult"].items():
        if mult == 0.0:
            perturbations.append(("contrib", leg, 0.5))  # 归零腿恢复一半
        else:
            perturbations += [
                ("contrib", leg, mult * 0.5),
                ("contrib", leg, mult * 1.5),
            ]
    for leg, w in spec["panel_weights"].items():
        perturbations += [("panel", leg, w * 0.5), ("panel", leg, w * 1.5)]

    flips: list[dict[str, Any]] = []
    n_checks = 0
    # 先定基线（未扰动方案在每窗的 C3★）——翻转 = 扰动后与基线**不一致**；
    # 基线本就不过的窗里扰动失败不算翻转（那是「本来就不行」，不是敏感）。
    base_fn = make_candidate_score(spec["contrib_mult"], spec["panel_weights"])
    base_c3: dict[str, bool] = {}
    for label, trades in trades_by_window.items():
        rep = svs.evaluate_variant(trades, name, base_fn)
        vd = svs.judge(
            rep,
            svs.basket_stats(trades, svs.v0_score, svs.TOP_FRAC),
            _universe_stats_of(trades),
        )
        base_c3[label] = bool(vd["C3_natural_vs_universe"])
    for kind, leg, val in perturbations:
        cm = dict(spec["contrib_mult"])
        pw = dict(spec["panel_weights"])
        if kind == "contrib":
            cm[leg] = val
        else:
            pw[leg] = round(val, 2)
        fn = make_candidate_score(cm, pw)
        for label, trades in trades_by_window.items():
            rep = svs.evaluate_variant(trades, name, fn)
            vd = svs.judge(
                rep,
                svs.basket_stats(trades, svs.v0_score, svs.TOP_FRAC),
                _universe_stats_of(trades),
            )
            n_checks += 1
            if bool(vd["C3_natural_vs_universe"]) != base_c3[label]:
                flips.append(
                    {
                        "leg": leg,
                        "kind": kind,
                        "perturbed_to": val,
                        "window": label,
                        "base_c3star": base_c3[label],
                        "basket_margin": vd.get("basket_margin"),
                        "universe_margin": vd.get("universe_margin"),
                    }
                )
    return {
        "candidate": name,
        "n_perturbations": len(perturbations),
        "n_checks": n_checks,
        "base_c3star_by_window": base_c3,
        "n_c3star_flip": len(flips),
        "parameter_sensitive": bool(flips),
        "flips": flips,
    }


def phase2_report(trades_by_window: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Phase 2 总报告：候选评估（逐窗）+ 灵敏度扫描 + 推荐名单。"""
    candidates: dict[str, Any] = {}
    for name, spec in PHASE2_CANDIDATES.items():
        fn = make_candidate_score(spec["contrib_mult"], spec["panel_weights"])
        per_window = {
            label: eval_candidate(trades, name, fn)
            for label, trades in trades_by_window.items()
        }
        sens = sensitivity_scan(trades_by_window, name, spec)
        pass_all_windows = all(w["pass_all"] for w in per_window.values())
        candidates[name] = {
            "desc": spec["desc"],
            "per_window": per_window,
            "pass_all_windows": pass_all_windows,
            "sensitivity": sens,
            "recommended_for_phase3": bool(
                pass_all_windows and not sens["parameter_sensitive"]
            ),
        }
    return {
        "r24_phase": "Phase 2（候选构造 + 灵敏度；调参只用主窗+跨窗，pre2019 终审前不许碰）",
        "criteria": (
            "C1 Spearman>0且半窗同正 / C2 赢家均分反超 / C3★ 篮子margin>全样本margin"
            "（Wilson重叠仅注记）/ C5 强档占比≤15%（A桶离线不可算）"
        ),
        "windows": list(trades_by_window),
        "c5_strong_frac_max": C5_STRONG_FRAC_MAX,
        "candidates": candidates,
        "recommended": [
            n for n, c in candidates.items() if c["recommended_for_phase3"]
        ],
    }


def print_phase2(rep: dict[str, Any]) -> None:
    """stdout 中文摘要：候选表 + 灵敏度 + 推荐。"""
    print("\n" + "=" * 78)
    print("R24 Phase 2：候选打分方案评估（判据：C1/C2/C3★/C5，调参窗 = 主窗+跨窗）")
    print("=" * 78)
    print(
        "⚠️ R11：量级不作数。⚠️ 纪律：pre2019 终审前不许碰（本表不含）；"
        "判据只许预注册的；变体 ≤4。"
    )
    for name, c in rep["candidates"].items():
        print(f"\n── {name}：{c['desc']}")
        for label, w in c["per_window"].items():
            b = w["basket"]
            print(
                f"  [{label}] C1{'✓' if w['C1'] else '✗'} C2{'✓' if w['C2'] else '✗'} "
                f"C3★{'✓' if w['C3_star'] else '✗'}"
                f"{'(Wilson重叠)' if w['C3_star_wilson_overlap'] else ''} "
                f"C5{'✓' if w['C5']['pass'] else '✗'}(强档 {w['C5']['strong_frac']}) | "
                f"Spearman={w['corr'].get('spearman')} | "
                f"赢家均分 {w['winner_top20_mean']} vs {w['bottom80_mean']} | "
                f"篮子 {b['win_rate'] * 100:.1f}%/{b['payoff_ratio']}/"
                f"margin {w['basket_margin'] * 100:+.1f}pp vs 全样本 "
                f"{w['universe_margin'] * 100:+.1f}pp"
            )
        s = c["sensitivity"]
        print(
            f"  灵敏度：{s['n_perturbations']} 扰动 × {len(rep['windows'])} 窗，"
            f"C3★ 翻转 {s['n_c3star_flip']} 次 ⇒ "
            f"{'⚠️ 参数敏感' if s['parameter_sensitive'] else '稳'}"
        )
        for f in s["flips"][:6]:
            print(
                f"    翻转：{f['leg']}({f['kind']})→{f['perturbed_to']} "
                f"[{f['window']}] margin {f['basket_margin'] * 100:+.1f}pp"
                f" vs {f['universe_margin'] * 100:+.1f}pp"
            )
        print(
            f"  ⇒ {'✅ 推荐进 Phase 3（pre2019 终审）' if c['recommended_for_phase3'] else '❌ 不推荐'}"
        )
    print(f"\n推荐名单：{rep['recommended'] or '（空——全不可行，如实上报）'}")


# ---------------------------------------------------------------------------
# 腿命中判定（纯函数：只读 factor_contrib / panel）
# ---------------------------------------------------------------------------


def _leg_hit(trade: dict[str, Any], leg: str) -> Optional[bool]:
    """单腿命中判定（三态）。

    contrib 腿：factor_contrib 键出现即命中（contrib 只记非零贡献；负腿如
    macd_top_divergence −8 / volume_yy_bear −5 以负值记录，出现同样算命中）。
    evidence-only 键（perfect_b1_fit）不是腿，不在 CONTRIB_LEG_KEYS 里。
    panel 腿：panel 键为 True；None = unavailable（不可评估，返回 None——
    命中率分母只含可评估样本，同 winner_factor_study 口径）。
    """
    if leg in CONTRIB_LEG_KEYS:
        v = (trade.get("factor_contrib") or {}).get(leg)
        return isinstance(v, (int, float)) and v != 0
    return (trade.get("panel") or {}).get(leg)


def pool_hit_rates(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """每腿在样本中的命中率；>90% 标「无区分度」（no_discrimination=True）。"""
    out: dict[str, dict[str, Any]] = {}
    for leg in CONTRIB_LEG_KEYS + PANEL_ONLY_LEG_KEYS:
        vals = [_leg_hit(t, leg) for t in trades]
        evaluable = [v for v in vals if v is not None]
        n_hit = sum(1 for v in evaluable if v)
        rate = (n_hit / len(evaluable)) if evaluable else None
        out[leg] = {
            "source": "contrib" if leg in CONTRIB_LEG_KEYS else "panel",
            "n_eval": len(evaluable),
            "n_hit": n_hit,
            "hit_rate": round(rate, 4) if rate is not None else None,
            "no_discrimination": (
                rate is not None and rate > NO_DISCRIMINATION_HIT_RATE
            ),
        }
    return out


def add_one_margins(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """每腿命中子集的 margin − 全样本 margin（逐腿独立证据）。

    margin 口径与判据层一致（svs.basket_margin = m2 的 _margin）；子集无亏单
    （payoff 无定义）时 margin 如实为 None。
    """
    m_u = svs.basket_margin(srs.ret_stats(trades))
    out: dict[str, dict[str, Any]] = {}
    for leg in CONTRIB_LEG_KEYS + PANEL_ONLY_LEG_KEYS:
        subset = [t for t in trades if _leg_hit(t, leg)]
        m = svs.basket_margin(srs.ret_stats(subset)) if subset else None
        out[leg] = {
            "n": len(subset),
            "margin": round(m, 4) if m is not None else None,
            "margin_vs_universe": (
                round(m - m_u, 4) if m is not None and m_u is not None else None
            ),
        }
    return out


def leave_one_out_margins(
    trades: list[dict[str, Any]], frac: float = svs.TOP_FRAC
) -> dict[str, dict[str, Any]]:
    """V0 打分逐腿剔除后的篮子 margin − V0 篮子 margin（找现行表的负贡献腿）。

    重建分 = contrib 求和（**排除 evidence-only 键与被剔腿**）clamp 0-100——
    与 svs.v0_score 同口径，只少一条腿。panel-only 腿不进 V0 打分，不在结果里。
    margin_vs_v0 < 0 ⇒ 该腿对现行 top-frac 篮子是负贡献（去掉反而更好）。
    """
    m0 = svs.basket_margin(svs.basket_stats(trades, svs.v0_score, frac))
    out: dict[str, dict[str, Any]] = {}
    for leg in CONTRIB_LEG_KEYS:

        def loo_score(t: dict[str, Any], _leg: str = leg) -> int:
            contrib = t.get("factor_contrib") or {}
            return svs._clamp(
                sum(
                    float(v)
                    for k, v in contrib.items()
                    if k != _leg
                    and k not in svs._EVIDENCE_ONLY_CONTRIB
                    and isinstance(v, (int, float))
                )
            )

        b = svs.basket_stats(trades, loo_score, frac)
        m = svs.basket_margin(b)
        out[leg] = {
            "margin": round(m, 4) if m is not None else None,
            "margin_vs_v0": (
                round(m - m0, 4) if m is not None and m0 is not None else None
            ),
        }
    return out


# ---------------------------------------------------------------------------
# 汇总报告与展示
# ---------------------------------------------------------------------------


def ablation_report(
    trades: list[dict[str, Any]], label: str = "", frac: float = svs.TOP_FRAC
) -> dict[str, Any]:
    """单窗口逐腿证据表：腿 × {命中率, add-one margin, LOO margin}。"""
    hit = pool_hit_rates(trades)
    add1 = add_one_margins(trades)
    loo = leave_one_out_margins(trades, frac)
    legs = []
    for leg in CONTRIB_LEG_KEYS + PANEL_ONLY_LEG_KEYS:
        legs.append(
            {
                "leg": leg,
                "weight": sc.DEFAULT_TECH_WEIGHTS.get(leg),
                **hit[leg],
                "add_one": add1[leg],
                # panel-only 腿不进 V0 打分 ⇒ 无 LOO（None 如实标注）
                "loo": loo.get(leg),
            }
        )
    u_stats = srs.ret_stats(trades)
    m_u = svs.basket_margin(u_stats)
    return {
        "r11_r3_warning": (
            "⚠️ R11：量级不作数；R3：单窗证据不作数（须三窗一致）；"
            "pre2019 的 ablation 只读不用作调参（反过拟合纪律）。"
        ),
        "label": label,
        "n_trades": len(trades),
        "top_frac": frac,
        "no_discrimination_hit_rate": NO_DISCRIMINATION_HIT_RATE,
        "universe_stats": {**u_stats, "margin": m_u},
        "v0_basket_margin": svs.basket_margin(
            svs.basket_stats(trades, svs.v0_score, frac)
        ),
        "legs": legs,
    }


def _pp(x: Optional[float]) -> str:
    return f"{x * 100:+.1f}" if x is not None else "—"


def print_ablation(rep: dict[str, Any]) -> None:
    """stdout 中文证据表。"""
    print("\n" + "=" * 76)
    print(f"R24 逐腿边际分析（ablation）窗口：{rep['label'] or '—'}")
    print("=" * 76)
    print(rep["r11_r3_warning"])
    m_u = (rep.get("universe_stats") or {}).get("margin")
    print(
        f"\n样本：{rep['n_trades']} 笔；全样本 margin（天然基准）{_pp(m_u)}pp；"
        f"V0 篮子 margin {_pp(rep.get('v0_basket_margin'))}pp"
    )
    print("\n腿 | 来源 | 命中率(命中/可评) | add-one vs全样本(pp) | LOO vs V0篮子(pp)")
    for row in rep["legs"]:
        hr = row["hit_rate"]
        hr_txt = (
            f"{hr * 100:.1f}%({row['n_hit']}/{row['n_eval']})"
            if hr is not None
            else "—"
        )
        if row["no_discrimination"]:
            hr_txt += " ⚠️无区分度"
        print(
            f"  {row['leg']:<28}{row['source']:<7}{hr_txt:<28}"
            f"{_pp(row['add_one']['margin_vs_universe']):>8}"
            f"{_pp((row['loo'] or {}).get('margin_vs_v0')):>10}"
        )
    print(
        "\n  读法：命中率>90% = 无区分度（地板效应）；add-one<0 = 该腿命中的票"
        "跑输无筛选全样本；LOO<0 = 现行表里去掉这条腿篮子反而更好（负贡献腿）。"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--ablation",
        action="store_true",
        help="逐腿边际分析模式（Phase 1）：读 trades JSON 离线求值，零回测",
    )
    ap.add_argument(
        "--phase2",
        action="store_true",
        help="候选方案评估 + 灵敏度扫描（Phase 2）：只用主窗+跨窗"
        "（pre2019 输入**硬拒绝**——终审前不许碰，纪律代码化）",
    )
    ap.add_argument(
        "--phase3",
        action="store_true",
        help="pre2019 untouched 终审（Phase 3）：**只接受** pre2019 输入——"
        "这是终审窗第一次也是唯一一次允许读它（R24 预注册终审线："
        "C1 不翻转 且 C3★ 保持 ⇒ 通过进 Phase 4；翻转 ⇒ 如实判负）",
    )
    ap.add_argument(
        "--from-trades",
        nargs="+",
        default=[],
        help="已落盘的研究 JSON（含 trades 键，含 factor_contrib + panel）；"
        "多个文件 ⇒ 逐文件（=逐窗口）各出一份证据表",
    )
    ap.add_argument(
        "--tag",
        default="",
        help="窗口标签（默认取文件名；多文件共用一个 tag 会重复，建议逐窗单跑）",
    )
    return ap


def _load_trades(path: str) -> list[dict[str, Any]]:
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    return stored.get("trades") or []


def _ablation_one(path: str, tag: str = "") -> dict[str, Any]:
    """离线跑单份落盘 JSON 的 ablation → 落 <文件>.ablation.json，返回报告。"""
    trades = _load_trades(path)
    if not trades:
        print(f"⛔ 复用文件无 trades: {path}", file=sys.stderr)
        return {}
    print(f"[INFO] 复用 {path}（{len(trades)} 笔）", file=sys.stderr)
    rep = ablation_report(trades, label=tag or Path(path).stem)
    out = Path(path).with_suffix(".ablation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(out, rep, big=len(trades) > 20000)
    print(f"[OK] 写出 {out}（离线 ablation，{len(trades)} 笔）")
    print_ablation(rep)
    return rep


def _phase2_main(paths: list[str]) -> int:
    """Phase 2 驱动：两窗离线评估（pre2019 硬拒绝）→ 落盘 + stdout。"""
    trades_by_window: dict[str, list[dict[str, Any]]] = {}
    for p in paths:
        if "pre2019" in Path(p).name:
            print(
                f"⛔ 反过拟合纪律：Phase 2 调参不许碰 pre2019（{p}）——"
                "它是 untouched 终审窗（R24 Phase 3）",
                file=sys.stderr,
            )
            return 2
        trades = _load_trades(p)
        if not trades:
            print(f"⛔ 复用文件无 trades: {p}", file=sys.stderr)
            return 1
        label = (
            "主窗"
            if "n400" in Path(p).name
            else ("跨窗" if "cw" in Path(p).name else Path(p).stem)
        )
        trades_by_window[label] = trades
        print(f"[INFO] 复用 {p}（{len(trades)} 笔，标签={label}）", file=sys.stderr)
    rep = phase2_report(trades_by_window)
    out = (
        Path("artifacts/logs/score_variants_study")
        / f"phase2_{'_'.join(sorted(trades_by_window))}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(out, rep, big=False)
    print(f"[OK] 写出 {out}")
    print_phase2(rep)
    return 0


def phase3_report(
    trades: list[dict[str, Any]],
    window_label: str = "pre2019",
    phase2_rep: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Phase 3 终审：P1/P2/P3 在 untouched 窗的 C1/C2/C3★/C5 + 终审判定。

    终审线（预注册，一票否决）：**C1 不翻转 且 C3★ 保持** ⇒ 通过进 Phase 4；
    翻转 ⇒ 如实判负（退回「分位数分层」止血方案）。
    ``phase2_rep``（可选）：Phase 2 落盘报告，用于三窗并排对照（不再重算）。
    """
    candidates: dict[str, Any] = {}
    for name in PHASE3_FINALIST_NAMES:
        spec = PHASE2_CANDIDATES[name]
        fn = make_candidate_score(spec["contrib_mult"], spec["panel_weights"])
        ev = eval_candidate(trades, name, fn)
        terminal_pass = bool(ev["C1"] and ev["C3_star"])
        candidates[name] = {
            "desc": spec["desc"],
            "eval": ev,
            "terminal_pass": terminal_pass,
        }
    passed = [n for n, c in candidates.items() if c["terminal_pass"]]
    return {
        "r24_phase": (
            "Phase 3（pre2019 untouched 终审——终审窗第一次也是唯一一次读取；"
            "终审线：C1 不翻转 且 C3★ 保持，一票否决）"
        ),
        "window": window_label,
        "n_trades": len(trades),
        "candidates": candidates,
        "passed": passed,
        "verdict": "通过" if passed else "证伪",
        "fallback": None
        if passed
        else "退回「分位数分层」止血方案（强=当日池 top15%，治标；R24 预注册既定退路）",
        "phase2_reference": phase2_rep,
    }


def print_phase3(rep: dict[str, Any]) -> None:
    """stdout 中文终审表：三方案终审判定 +（有 Phase 2 参照时）三窗并排。"""
    print("\n" + "=" * 78)
    print(f"R24 Phase 3：pre2019 untouched 终审（{rep['n_trades']} 笔）")
    print("=" * 78)
    print(
        "⚠️ 终审线（预注册）：C1 不翻转 且 C3★（篮子 margin > 全样本 margin）保持，"
        "一票否决。⚠️ R11：量级不作数。"
    )
    print("\n方案 | C1 | C2 | C3★ | C5 | 篮子胜率/盈亏比/margin vs 全样本 | 终审判定")
    for name, c in rep["candidates"].items():
        ev = c["eval"]
        b = ev["basket"]
        print(
            f"  {name:<20} {'✓' if ev['C1'] else '✗'}   "
            f"{'✓' if ev['C2'] else '✗'}   "
            f"{'✓' if ev['C3_star'] else '✗'}    "
            f"{'✓' if ev['C5']['pass'] else '✗'}   "
            f"{b['win_rate'] * 100:.1f}%/{b['payoff_ratio']}/"
            f"{ev['basket_margin'] * 100:+.1f}pp vs {ev['universe_margin'] * 100:+.1f}pp | "
            f"{'✅ 通过' if c['terminal_pass'] else '❌ 不通过'}"
        )
        hw = ev["half_window"]
        h1 = (hw.get("first_half") or {}).get("spearman")
        h2 = (hw.get("second_half") or {}).get("spearman")
        print(
            f"    Spearman={ev['corr'].get('spearman')}（半窗 {h1}/{h2}"
            f"{'' if hw.get('consistent') else ' ⚠️翻'}），"
            f"赢家均分 {ev['winner_top20_mean']} vs {ev['bottom80_mean']}，"
            f"强档占比 {ev['C5']['strong_frac']}"
        )
    p2 = rep.get("phase2_reference")
    if p2:
        print("\n── 三窗并排（篮子 margin pp vs 全样本 margin pp / Spearman）")
        for name in PHASE3_FINALIST_NAMES:
            cells = []
            for label in ("主窗", "跨窗"):
                w = (p2["candidates"][name]["per_window"] or {}).get(label)
                if w:
                    cells.append(
                        f"[{label}] {w['basket_margin'] * 100:+.1f}/"
                        f"{w['universe_margin'] * 100:+.1f} "
                        f"Sp={w['corr'].get('spearman')}"
                    )
            ev = rep["candidates"][name]["eval"]
            cells.append(
                f"[pre2019] {ev['basket_margin'] * 100:+.1f}/"
                f"{ev['universe_margin'] * 100:+.1f} Sp={ev['corr'].get('spearman')}"
            )
            print(f"  {name:<20} " + " | ".join(cells))
    print(
        f"\n终审结论：{rep['verdict']}"
        + (f"——通过方案 {rep['passed']}" if rep["passed"] else f"——{rep['fallback']}")
    )


def _phase3_main(paths: list[str]) -> int:
    """Phase 3 驱动：**只接受** pre2019 输入（与 Phase 2 硬拒绝互为镜像）。"""
    if len(paths) != 1:
        print("⛔ Phase 3 只跑 untouched 终审窗一个输入", file=sys.stderr)
        return 2
    p = paths[0]
    if "pre2019" not in Path(p).name:
        print(
            f"⛔ Phase 3 只接受 pre2019 untouched 窗输入（{p}）；"
            "主窗/跨窗请用 --phase2",
            file=sys.stderr,
        )
        return 2
    trades = _load_trades(p)
    if not trades:
        print(f"⛔ 复用文件无 trades: {p}", file=sys.stderr)
        return 1
    print(
        f"[INFO] 终审窗 {p}（{len(trades)} 笔）——第一次也是唯一一次读取",
        file=sys.stderr,
    )
    # Phase 2 落盘参照（有则并排三窗；没有不拦着终审）
    p2_path = Path("artifacts/logs/score_variants_study/phase2_主窗_跨窗.json")
    p2_rep = (
        json.loads(p2_path.read_text(encoding="utf-8")) if p2_path.is_file() else None
    )
    rep = phase3_report(trades, "pre2019", p2_rep)
    out = Path("artifacts/logs/score_variants_study/phase3_pre2019.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(out, rep, big=False)
    print(f"[OK] 写出 {out}")
    print_phase3(rep)
    return 0  # 证伪也是结论（退出码不区分通过/证伪，verdict 见 JSON/stdout）


def main(argv: Optional[list] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    if args.phase2:
        if not args.from_trades:
            ap.error("--phase2 需要 --from-trades <主窗json> <跨窗json>")
        return _phase2_main(args.from_trades)
    if args.phase3:
        if not args.from_trades:
            ap.error("--phase3 需要 --from-trades <pre2019 json>")
        return _phase3_main(args.from_trades)
    if not args.ablation or not args.from_trades:
        ap.error(
            "本工具三个模式：--ablation（Phase 1）/ --phase2（Phase 2）/ --phase3（Phase 3 终审），均需 --from-trades"
        )
    reps = [_ablation_one(f, args.tag) for f in args.from_trades]
    return 0 if any(reps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
