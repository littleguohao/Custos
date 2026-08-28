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
        help="逐腿边际分析模式（当前唯一模式）：读 trades JSON 离线求值，零回测",
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


def _ablation_one(path: str, tag: str = "") -> dict[str, Any]:
    """离线跑单份落盘 JSON 的 ablation → 落 <文件>.ablation.json，返回报告。"""
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    trades = stored.get("trades") or []
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


def main(argv: Optional[list] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    if not args.ablation or not args.from_trades:
        ap.error("本工具当前唯一模式：--ablation --from-trades <json...>")
    reps = [_ablation_one(f, args.tag) for f in args.from_trades]
    return 0 if any(reps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
