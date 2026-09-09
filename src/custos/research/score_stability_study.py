# -*- coding: utf-8 -*-
"""研究：R29 胜率稳定≥40% + 盈亏比≥2.4 打分重建（预注册 governance/research/R29_score_stability_rebuild.md）。

> ⚠️ **R11 警示**：量级不作数，读数仅供相对排序。**R3 纪律**：单窗证据不作数，
> 须三窗（主窗/跨窗/pre2019）一致才进结论；pre2019 终审窗在 Phase 3 前**不许看**
> （Phase 2 CLI 对 pre2019 输入硬拒绝——反过拟合纪律第 5 条代码化）。

R22/R24 两轮五个候选全部死于 pre2019 终审（Spearman 半窗翻转，死因 = 判别 edge
属近 regime 富集）。R29 的设计原则与前两轮**相反**：权重向「pre2019 不萎缩」的
腿倾斜（macd_bottom_divergence / rsi_bull_div），把 regime 依赖最重的
rsi_deep_oversold 降权直至归零——用窗内量级换跨 regime 生存率（设计依据 =
R24 Phase 1 逐腿三窗 ablation，见预注册页顶部表）。

判据（预注册写死，主窗+跨窗两窗各自全过才算过）：

- **R29-C1**：top-20% 篮子胜率 ≥ 40%（绝对线）且 > V0 篮子胜率
- **R29-C2**：篮子盈亏比 ≥ 2.4（沿用 R22 owner 放宽线）
- **R29-C3**：变体分 vs 收益 Spearman > 0 且前后半窗同正（前两轮的翻车顶线）
- **R29-C4**：强档（≥60）占全样本 ≤ 15%（A 桶离线不可算，如实标注）
- 参考列（不进判定）：C3★ 篮子 margin vs 全样本 margin + Wilson 注记
- **灵敏度**：入选方案每腿权重 ±50% 扰动（负腿同），pass_all（C1∧C2∧C3∧C4）
  不得翻转——翻转即「参数敏感，不可信」
- **终审（一票否决）**：pre2019 untouched 窗 R29-C1 ∧ C2 ∧ C3 同时保持
  （比 R24 终审线「C1+C3★」更严——终审窗必须同样过 40%/2.4）

候选（≤4，简单整数权重；全部为**证据重构形态**：contrib_mult={} ⇒ 现行腿全部
归零，只用 panel 证据腿从零搭；打分机械直接复用
:func:`score_calibration_study.make_candidate_score`，零口径重写）：

- **W1_balanced**：四正腿等权 25（R22 V2 形态的等权版：rsi_deep 40%→25%，
  检验「降 regime 锚」是否够）
- **W2_pre2019_tilt**：向 pre2019 最强的两条腿倾斜（rsi_bull_div /
  macd_bottom_divergence 各 30）；rsi_deep 降到 20%
- **W3_no_deep**：彻底去掉 regime 依赖腿——直接检验「翻车全部来自 rsi_deep」假说
- **W4_tilt_neg**：W2 + 六条三窗负腿各 −5（V3 式负向证据加在稳态核上；
  R24：负腿三窗一致负，取负有证据）

CLI（生产机 Phase 2/3 照抄）::

    uv run python src/custos/research/score_stability_study.py --phase2 \\
        --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n400.rejudged.json \\
                      artifacts/logs/score_variants_study/score_variants_study_s0_n1000_cw.rejudged.json
    uv run python src/custos/research/score_stability_study.py --phase3 \\
        --from-trades artifacts/logs/score_variants_study/score_variants_study_s0_n1000_pre2019.rejudged.json

产物：`artifacts/logs/score_stability_study/r29_phase2.json`（含推荐名单——
机械生成：两窗全过且参数不敏感者进终审）、`r29_phase3_pre2019.json`。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from custos.research import backtest_factors as bf  # noqa: E402
from custos.research import score_calibration_study as scs  # noqa: E402
from custos.research import score_variants_study as svs  # noqa: E402

# 过线判据（预注册，跑数前写死，不许事后改）
WIN_RATE_FLOOR = 0.40  # R29-C1 绝对胜率线
PAYOFF_FLOOR = 2.4  # R29-C2（沿用 R22 owner 放宽线）
STRONG_FRAC_MAX = 0.15  # R29-C4（复用 R24 C5 语义）

# R24 Phase 1 三窗一致为负的六条腿（W4 取负用；同 R24 PHASE2_NEG_LEGS）
R29_NEG_LEGS = (
    "rsi_strong",
    "b1_ignition",
    "volume_contraction",
    "relative_strength_strong",
    "macd_top_divergence",
    "ignition",
)

# 候选方案（≤4，权重简单整数，跑数前写死；与预注册页「候选方案」表逐字一致）
R29_CANDIDATES: dict[str, dict[str, Any]] = {
    "W1_balanced": {
        "desc": "R22 V2 形态的等权版：rsi_deep 占比 40%→25%，检验「降 regime 锚」是否够",
        "contrib_mult": {},  # 空 = 全部现行腿归零（证据重构，见 make_candidate_score 语义）
        "panel_weights": {
            "rsi_deep_oversold": 25,
            "weekly_j_low": 25,
            "rsi_bull_div": 25,
            "macd_bottom_divergence": 25,
        },
    },
    "W2_pre2019_tilt": {
        "desc": "向 pre2019 最强的两条腿倾斜（rsi_bull_div/macd_bottom_divergence 各 30）；"
        "rsi_deep 降到 20%",
        "contrib_mult": {},
        "panel_weights": {
            "rsi_bull_div": 30,
            "macd_bottom_divergence": 30,
            "weekly_j_low": 20,
            "rsi_deep_oversold": 20,
        },
    },
    "W3_no_deep": {
        "desc": "彻底去掉 regime 依赖腿：直接检验「翻车全部来自 rsi_deep」假说",
        "contrib_mult": {},
        "panel_weights": {
            "rsi_bull_div": 40,
            "macd_bottom_divergence": 30,
            "weekly_j_low": 30,
        },
    },
    "W4_tilt_neg": {
        "desc": "W2 + 六条三窗负腿各 −5（V3 式负向证据加在稳态核上；"
        "R24：负腿三窗一致负，取负有证据）",
        "contrib_mult": {},
        "panel_weights": {
            "rsi_bull_div": 30,
            "macd_bottom_divergence": 30,
            "weekly_j_low": 20,
            "rsi_deep_oversold": 20,
            **{k: -5 for k in R29_NEG_LEGS},
        },
    },
}

# 产物路径（相对仓库根；Phase 3 的终审名单从 Phase 2 落盘读——跑数前已定）
PHASE2_OUT = Path("artifacts/logs/score_stability_study/r29_phase2.json")
PHASE3_OUT = Path("artifacts/logs/score_stability_study/r29_phase3_pre2019.json")

ScoreFn = Callable[[dict[str, Any]], int]


# ---------------------------------------------------------------------------
# R29-C1~C4 判据（判据机械全部复用 svs/scs 现成函数，零口径重写）
# ---------------------------------------------------------------------------


def _r29_c1(basket: dict[str, Any], v0_basket: dict[str, Any]) -> dict[str, Any]:
    """R29-C1：篮子胜率 ≥ 40% 且 > V0 篮子胜率（None 安全：任一缺省判 False）。"""
    b_wr = basket.get("win_rate")
    v0_wr = v0_basket.get("win_rate")
    note = None
    if b_wr is None or v0_wr is None:
        passed = False
        note = "篮子或 V0 篮子胜率缺省 ⇒ 判 False（如实标注，不编数）"
    else:
        passed = bool(b_wr >= WIN_RATE_FLOOR and b_wr > v0_wr)
    return {
        "pass": passed,
        "basket_win_rate": b_wr,
        "v0_basket_win_rate": v0_wr,
        "floor": WIN_RATE_FLOOR,
        "note": note,
    }


def _r29_c2(basket: dict[str, Any]) -> dict[str, Any]:
    """R29-C2：篮子盈亏比 ≥ 2.4（篮子无亏单 ⇒ payoff 无定义 ⇒ 判 False 并标注）。"""
    b_payoff = basket.get("payoff_ratio")
    return {
        "pass": bool(b_payoff is not None and b_payoff >= PAYOFF_FLOOR),
        "basket_payoff_ratio": b_payoff,
        "floor": PAYOFF_FLOOR,
        "note": (
            "篮子盈亏比缺省（无亏单，payoff 无定义）⇒ 判 False（如实标注）"
            if b_payoff is None
            else None
        ),
    }


def eval_r29_candidate(
    trades: list[dict[str, Any]], name: str, score_fn: ScoreFn
) -> dict[str, Any]:
    """单候选单窗口：R29-C1~C4 + 篮子指标 + 参考列 C3★（全样本天然基准）。

    R29-C3 = judge 的 C1_spearman_positive（Spearman>0 且前后半窗同正）；
    C3★（judge 的 C3_natural_vs_universe + Wilson 注记）只作参考列，不进判定。
    """
    rep = svs.evaluate_variant(trades, name, score_fn)
    v0_basket = svs.basket_stats(trades, svs.v0_score, svs.TOP_FRAC)
    vd = svs.judge(rep, v0_basket, scs._universe_stats_of(trades))
    c4 = scs.c5_strong_frac(rep["band_stats"], len(trades))
    b = rep["basket_top20_by_variant"]
    c1 = _r29_c1(b, v0_basket)
    c2 = _r29_c2(b)
    c3 = {
        "pass": bool(vd["C1_spearman_positive"]),
        "spearman": rep["corr"].get("spearman"),
        "half_window_consistent": rep["half_window"].get("consistent"),
    }
    return {
        "candidate": name,
        "n_trades": len(trades),
        "corr": rep["corr"],
        "half_window": rep["half_window"],
        "basket": b,
        "basket_margin": vd.get("basket_margin"),
        "universe_margin": vd.get("universe_margin"),
        "C3_star": vd["C3_natural_vs_universe"],
        "C3_star_wilson_overlap": vd.get("wilson_overlap_universe"),
        "R29_C1": c1,
        "R29_C2": c2,
        "R29_C3": c3,
        "R29_C4": c4,
        "pass_all": bool(c1["pass"] and c2["pass"] and c3["pass"] and c4["pass"]),
    }


def sensitivity_scan(
    trades_by_window: dict[str, list[dict[str, Any]]],
    name: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """入选方案每腿权重 ±50% 扰动（负腿同），逐窗查 pass_all 是否翻转。

    翻转 = 扰动后 pass_all 与基线**不一致**；基线本就不过的窗里扰动失败不算
    翻转（那是「本来就不行」，不是参数敏感——与 R24 同语义）。判定对象 =
    pass_all（R29-C1∧C2∧C3∧C4）。contrib_mult 为空 dict ⇒ 无 contrib 扰动
    （R29 四候选全是证据重构形态，扰动只落在 panel 腿上）。
    """
    perturbations: list[tuple[str, float]] = []  # (panel 腿, 扰动后权重)
    for leg, w in spec["panel_weights"].items():
        perturbations += [(leg, round(w * 0.5, 2)), (leg, round(w * 1.5, 2))]

    # 先定基线（未扰动方案在每窗的 pass_all）
    base_fn = scs.make_candidate_score(spec["contrib_mult"], spec["panel_weights"])
    base_pass = {
        label: eval_r29_candidate(trades, name, base_fn)["pass_all"]
        for label, trades in trades_by_window.items()
    }
    flips: list[dict[str, Any]] = []
    n_checks = 0
    for leg, val in perturbations:
        pw = dict(spec["panel_weights"])
        pw[leg] = val
        fn = scs.make_candidate_score(spec["contrib_mult"], pw)
        for label, trades in trades_by_window.items():
            ev = eval_r29_candidate(trades, name, fn)
            n_checks += 1
            if ev["pass_all"] != base_pass[label]:
                flips.append(
                    {
                        "leg": leg,
                        "perturbed_to": val,
                        "window": label,
                        "base_pass": base_pass[label],
                        "basket_win_rate": (ev["basket"] or {}).get("win_rate"),
                        "basket_payoff_ratio": (ev["basket"] or {}).get("payoff_ratio"),
                    }
                )
    return {
        "candidate": name,
        "n_perturbations": len(perturbations),
        "n_checks": n_checks,
        "base_pass_by_window": base_pass,
        "n_flip": len(flips),
        "parameter_sensitive": bool(flips),
        "flips": flips,
    }


# ---------------------------------------------------------------------------
# Phase 2：主窗+跨窗评估 + 灵敏度 + 推荐名单
# ---------------------------------------------------------------------------


def phase2_report(trades_by_window: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Phase 2 总报告：候选评估（逐窗）+ 灵敏度扫描 + 推荐名单（机械生成）。"""
    candidates: dict[str, Any] = {}
    for name, spec in R29_CANDIDATES.items():
        fn = scs.make_candidate_score(spec["contrib_mult"], spec["panel_weights"])
        per_window = {
            label: eval_r29_candidate(trades, name, fn)
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
        "r29_phase": (
            "Phase 2（候选评估 + 灵敏度；调参只用主窗+跨窗，pre2019 终审前不许碰）"
        ),
        "criteria": (
            "R29-C1 篮子胜率≥40%且>V0篮子 / R29-C2 篮子盈亏比≥2.4 / "
            "R29-C3 Spearman>0且半窗同正 / R29-C4 强档占比≤15%（A桶离线不可算）；"
            "灵敏度：每腿 ±50% 扰动 pass_all 不得翻转"
        ),
        "windows": list(trades_by_window),
        "floors": {
            "win_rate": WIN_RATE_FLOOR,
            "payoff": PAYOFF_FLOOR,
            "strong_frac_max": STRONG_FRAC_MAX,
        },
        "candidates": candidates,
        "recommended": [
            n for n, c in candidates.items() if c["recommended_for_phase3"]
        ],
    }


def _pct(x: Optional[float]) -> str:
    return f"{x * 100:.1f}%" if x is not None else "—"


def _pp(x: Optional[float]) -> str:
    return f"{x * 100:+.1f}pp" if x is not None else "—"


def _phase2_window_row(label: str, w: dict[str, Any]) -> str:
    """Phase 2 候选×窗口行：R29-C1~C4 ✓/✗ + 篮子读数 + Spearman。"""
    b = w["basket"]
    return (
        f"  [{label}] C1{'✓' if w['R29_C1']['pass'] else '✗'} "
        f"C2{'✓' if w['R29_C2']['pass'] else '✗'} "
        f"C3{'✓' if w['R29_C3']['pass'] else '✗'} "
        f"C4{'✓' if w['R29_C4']['pass'] else '✗'}(强档 {w['R29_C4']['strong_frac']}) | "
        f"篮子 {_pct(b.get('win_rate'))}/{b.get('payoff_ratio')}/"
        f"margin {_pp(w.get('basket_margin'))} vs 全样本 {_pp(w.get('universe_margin'))} | "
        f"Spearman={w['corr'].get('spearman')}"
    )


def print_phase2(rep: dict[str, Any]) -> None:
    """stdout 中文摘要：候选×窗口判据表 + 灵敏度 + 推荐名单。"""
    print("\n" + "=" * 78)
    print("R29 Phase 2：候选打分方案评估（判据：R29-C1~C4，调参窗 = 主窗+跨窗）")
    print("=" * 78)
    print(
        "⚠️ R11：量级不作数。⚠️ 纪律：pre2019 终审前不许碰（本表不含）；"
        "判据只许预注册的；变体 ≤4。"
    )
    for name, c in rep["candidates"].items():
        print(f"\n── {name}：{c['desc']}")
        for label, w in c["per_window"].items():
            print(_phase2_window_row(label, w))
        s = c["sensitivity"]
        print(
            f"  灵敏度：{s['n_perturbations']} 扰动 × {len(rep['windows'])} 窗，"
            f"pass_all 翻转 {s['n_flip']} 次 ⇒ "
            f"{'⚠️ 参数敏感' if s['parameter_sensitive'] else '稳'}"
        )
        for f in s["flips"][:6]:
            print(
                f"    翻转：{f['leg']}→{f['perturbed_to']} [{f['window']}] "
                f"基线pass={f['base_pass']} 篮子 {_pct(f.get('basket_win_rate'))}"
            )
        print(
            f"  ⇒ {'✅ 推荐进 Phase 3（pre2019 终审）' if c['recommended_for_phase3'] else '❌ 不推荐'}"
        )
    print(f"\n推荐名单：{rep['recommended'] or '（空——全不可行，如实上报）'}")


# ---------------------------------------------------------------------------
# Phase 3：pre2019 untouched 终审（终审线 R29-C1 ∧ C2 ∧ C3，一票否决）
# ---------------------------------------------------------------------------


def phase3_report(
    trades: list[dict[str, Any]],
    finalists: list[str],
    window_label: str = "pre2019",
    phase2_rep: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Phase 3 终审：finalists 在 untouched 窗的 R29-C1~C4 + 终审判定。

    终审线（预注册，一票否决）：**R29-C1 ∧ R29-C2 ∧ R29-C3 同时保持** ⇒ 通过进
    Phase 4；任一失线 ⇒ 如实判负（与 R22/R24 同款结局也是合格产出）。
    ⚠️ C4 不进终审线（终审窗只考胜率稳定 + 盈亏比 + 判别不翻转）。
    ``finalists`` 必须来自 Phase 2 落盘的 recommended（跑数前已定）；
    ``phase2_rep``（可选）用于三窗并排对照（不再重算）。
    """
    candidates: dict[str, Any] = {}
    for name in finalists:
        spec = R29_CANDIDATES[name]
        fn = scs.make_candidate_score(spec["contrib_mult"], spec["panel_weights"])
        ev = eval_r29_candidate(trades, name, fn)
        terminal_pass = bool(
            ev["R29_C1"]["pass"] and ev["R29_C2"]["pass"] and ev["R29_C3"]["pass"]
        )
        candidates[name] = {
            "desc": spec["desc"],
            "eval": ev,
            "terminal_pass": terminal_pass,
        }
    passed = [n for n, c in candidates.items() if c["terminal_pass"]]
    return {
        "r29_phase": (
            "Phase 3（pre2019 untouched 终审——终审窗第一次也是唯一一次读取；"
            "终审线：R29-C1∧C2∧C3 同时保持，一票否决）"
        ),
        "window": window_label,
        "n_trades": len(trades),
        "finalists": list(finalists),
        "candidates": candidates,
        "passed": passed,
        "verdict": "通过" if passed else "证伪",
        "fallback": None
        if passed
        else "如实判负回填 R29 预注册页（与 R22/R24 同款结局也是合格产出）",
        "phase2_reference": phase2_rep,
    }


def _phase3_row(name: str, c: dict[str, Any]) -> str:
    """终审表单行：方案 | C1 | C2 | C3 | C4(参考) | 篮子读数 | 终审判定。"""
    ev = c["eval"]
    b = ev["basket"]
    return (
        f"  {name:<20} {'✓' if ev['R29_C1']['pass'] else '✗'}   "
        f"{'✓' if ev['R29_C2']['pass'] else '✗'}   "
        f"{'✓' if ev['R29_C3']['pass'] else '✗'}    "
        f"{'✓' if ev['R29_C4']['pass'] else '✗'}   "
        f"{_pct(b.get('win_rate'))}/{b.get('payoff_ratio')}/"
        f"{_pp(ev.get('basket_margin'))} vs {_pp(ev.get('universe_margin'))} | "
        f"{'✅ 通过' if c['terminal_pass'] else '❌ 不通过'}"
    )


def _side_by_side_cell(w: dict[str, Any], label: str) -> str:
    """三窗并排单格：篮子 胜率%/盈亏比/margin vs 全样本 margin / Spearman。"""
    b = w.get("basket") or {}
    return (
        f"[{label}] {_pct(b.get('win_rate'))}/{b.get('payoff_ratio')}/"
        f"{_pp(w.get('basket_margin'))} vs {_pp(w.get('universe_margin'))} "
        f"Sp={w['corr'].get('spearman')}"
    )


def print_phase3(rep: dict[str, Any]) -> None:
    """stdout 中文终审表：终审判定 +（有 Phase 2 参照时）三窗并排。"""
    print("\n" + "=" * 78)
    print(f"R29 Phase 3：pre2019 untouched 终审（{rep['n_trades']} 笔）")
    print("=" * 78)
    print(
        "⚠️ 终审线（预注册）：R29-C1（胜率≥40%且>V0）∧ R29-C2（盈亏比≥2.4）∧ "
        "R29-C3（Spearman>0且半窗同正）同时保持，一票否决；C4 不进终审线。"
        "⚠️ R11：量级不作数。"
    )
    print(
        "\n方案 | C1 | C2 | C3 | C4(参考) | 篮子胜率/盈亏比/margin vs 全样本 | 终审判定"
    )
    for name, c in rep["candidates"].items():
        print(_phase3_row(name, c))
        ev = c["eval"]
        hw = ev["half_window"]
        h1 = (hw.get("first_half") or {}).get("spearman")
        h2 = (hw.get("second_half") or {}).get("spearman")
        print(
            f"    Spearman={ev['corr'].get('spearman')}（半窗 {h1}/{h2}"
            f"{'' if hw.get('consistent') else ' ⚠️翻'}），"
            f"强档占比 {ev['R29_C4']['strong_frac']}，"
            f"C3★{'✓' if ev['C3_star'] else '✗'}(参考列)"
        )
    p2 = rep.get("phase2_reference")
    if p2:
        print("\n── 三窗并排（篮子 胜率/盈亏比/margin vs 全样本 margin / Spearman）")
        for name in rep["finalists"]:
            cells = []
            for label in ("主窗", "跨窗"):
                w = (
                    ((p2.get("candidates") or {}).get(name) or {})
                    .get("per_window", {})
                    .get(label)
                )
                if w:
                    cells.append(_side_by_side_cell(w, label))
            cells.append(_side_by_side_cell(rep["candidates"][name]["eval"], "pre2019"))
            print(f"  {name:<20} " + " | ".join(cells))
    print(
        f"\n终审结论：{rep['verdict']}"
        + (f"——通过方案 {rep['passed']}" if rep["passed"] else f"——{rep['fallback']}")
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--phase2",
        action="store_true",
        help="候选方案评估 + 灵敏度扫描（Phase 2）：只用主窗+跨窗"
        "（pre2019 输入**硬拒绝**——终审前不许碰，纪律代码化）",
    )
    ap.add_argument(
        "--phase3",
        action="store_true",
        help="pre2019 untouched 终审（Phase 3）：**只接受** pre2019 单文件输入——"
        "这是终审窗第一次也是唯一一次允许读它（R29 预注册终审线："
        "R29-C1∧C2∧C3 同时保持 ⇒ 通过进 Phase 4；失线 ⇒ 如实判负）",
    )
    ap.add_argument(
        "--from-trades",
        nargs="+",
        default=[],
        help="已落盘的研究 JSON（含 trades 键，含 factor_contrib + panel）；"
        "Phase 2 = 主窗+跨窗两份，Phase 3 = pre2019 一份",
    )
    return ap


def _load_trades(path: str) -> list[dict[str, Any]]:
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    return stored.get("trades") or []


def _phase2_main(paths: list[str]) -> int:
    """Phase 2 驱动：两窗离线评估（pre2019 硬拒绝）→ 落盘 + stdout。"""
    trades_by_window: dict[str, list[dict[str, Any]]] = {}
    for p in paths:
        if "pre2019" in Path(p).name:
            print(
                f"⛔ 反过拟合纪律：Phase 2 调参不许碰 pre2019（{p}）——"
                "它是 untouched 终审窗（R29 Phase 3）",
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
    PHASE2_OUT.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(PHASE2_OUT, rep, big=False)
    print(f"[OK] 写出 {PHASE2_OUT}")
    print_phase2(rep)
    return 0


def _read_phase2_finalists() -> Optional[list[str]]:
    """终审名单 = Phase 2 落盘的 recommended（跑数前已定；None = 不可用）。"""
    if not PHASE2_OUT.is_file():
        print(
            f"⛔ 找不到 Phase 2 落盘 {PHASE2_OUT}——终审名单必须先跑 Phase 2 定下"
            "（终审名单不许临时指定）",
            file=sys.stderr,
        )
        return None
    p2_rep = json.loads(PHASE2_OUT.read_text(encoding="utf-8"))
    finalists = p2_rep.get("recommended") or []
    unknown = [n for n in finalists if n not in R29_CANDIDATES]
    if not finalists or unknown:
        print(
            f"⛔ Phase 2 推荐名单不可用（recommended={finalists}"
            f"{f'，未知候选 {unknown}' if unknown else ''}）——无可终审方案，如实上报",
            file=sys.stderr,
        )
        return None
    return list(finalists)


def _phase3_main(paths: list[str]) -> int:
    """Phase 3 驱动：**只接受** pre2019 单文件输入（与 Phase 2 硬拒绝互为镜像）。"""
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
    finalists = _read_phase2_finalists()  # 守卫先行：名单不可用则不读终审窗文件
    if finalists is None:
        return 2
    trades = _load_trades(p)
    if not trades:
        print(f"⛔ 复用文件无 trades: {p}", file=sys.stderr)
        return 1
    print(
        f"[INFO] 终审窗 {p}（{len(trades)} 笔）——第一次也是唯一一次读取；"
        f"终审名单（Phase 2 落盘）={finalists}",
        file=sys.stderr,
    )
    p2_rep = json.loads(PHASE2_OUT.read_text(encoding="utf-8"))
    rep = phase3_report(trades, finalists, "pre2019", p2_rep)
    PHASE3_OUT.parent.mkdir(parents=True, exist_ok=True)
    bf.write_json_stream(PHASE3_OUT, rep, big=False)
    print(f"[OK] 写出 {PHASE3_OUT}")
    print_phase3(rep)
    return 0  # 证伪也是结论（退出码不区分通过/证伪，verdict 见 JSON/stdout）


def main(argv: Optional[list[str]] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    if args.phase2 and args.phase3:
        ap.error("--phase2 与 --phase3 互斥（调参与终审是两步，不许一次跑）")
    if args.phase2:
        if not args.from_trades:
            ap.error("--phase2 需要 --from-trades <主窗json> <跨窗json>")
        return _phase2_main(args.from_trades)
    if args.phase3:
        if not args.from_trades:
            ap.error("--phase3 需要 --from-trades <pre2019 json>")
        return _phase3_main(args.from_trades)
    ap.error(
        "本工具两个互斥模式：--phase2（主窗+跨窗调参评估）/ "
        "--phase3（pre2019 untouched 终审），均需 --from-trades"
    )
    return 2  # pragma: no cover（ap.error 先抛 SystemExit）


if __name__ == "__main__":
    raise SystemExit(main())
