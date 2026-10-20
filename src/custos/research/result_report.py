# -*- coding: utf-8 -*-
"""研究结果可视化报告（v0.427，owner 2026-10-20：「更具体、多维度、表格 / 图表展示结论」）。

从**入库摘要** ``governance/research/results/<单元>/<tag>.json``（``result_digest`` 产出，数字只来自产物）
机械渲染 markdown 报告：多维读数表 + mermaid 图（``xychart-beta`` 柱状 / ``pie``，GitHub / 飞书文档可渲染）。
报告是摘要 JSON 的**纯函数**：``tests/test_result_report.py`` 逐字重渲染比对，手改即红。

用法（开发机即可跑，不需要行情数据）::

    uv run python -m custos.research result_report governance/research/results/R43/r43_v1_detail.json --write

写到同目录 ``<tag>_report.md``。描述性展示，判决仍以单元结论段与 ``criteria`` 为准。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import RESEARCH_DIR

VERDICT_ICON = {
    "candidate": "✅",
    "provisional": "🟡",
    "falsified": "❌",
    "untested": "⏸",
}


def _num(x: Any, nd: int = 2, pct: bool = False) -> str:
    if not isinstance(x, (int, float)) or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x * 100:+.{nd}f}%" if pct else f"{x:+.{nd}f}"


def _pp(x: Any) -> str:
    return "—" if not isinstance(x, (int, float)) else f"{x * 100:+.2f}pp"


def _xychart(
    title: str, xs: list[str], series: list[tuple[str, list[float]]], y_label: str
) -> list[str]:
    """mermaid xychart-beta 柱状图（多序列 = 多组 bar；图例写在标题里，xychart 无图例语法）。"""
    vals = [v for _n, ys in series for v in ys]
    if not xs or not vals:
        return []
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    pad = (hi - lo) * 0.1 or 1.0
    legend = " / ".join(n for n, _ in series)
    out = [
        "```mermaid",
        "xychart-beta",
        f'    title "{title}（{legend}）"',
        "    x-axis [" + ", ".join(f'"{x}"' for x in xs) + "]",
        f'    y-axis "{y_label}" {lo - pad:.2f} --> {hi + pad:.2f}',
    ]
    out += [f"    bar [{', '.join(f'{v:.2f}' for v in ys)}]" for _n, ys in series]
    out.append("```")
    return out


def _pie(title: str, counts: dict[str, int]) -> list[str]:
    items = [(k, v) for k, v in counts.items() if v]
    if not items:
        return []
    return (
        ["```mermaid", f"pie title {title}"]
        + [f'    "{k}" : {v}' for k, v in items]
        + ["```"]
    )


def _verdict_counts(vals: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in vals:
        k = f"{VERDICT_ICON.get(v, '')} {v}".strip()
        out[k] = out.get(k, 0) + 1
    return out


def _group(fields: dict[str, Any], prefix: str) -> dict[str, dict[str, Any]]:
    """``style.bear.dims.age.verdict`` 型扁平键 → {'bear.age': {'verdict': ...}}（prefix 之后两段作键）。"""
    out: dict[str, dict[str, Any]] = {}
    for k, v in fields.items():
        if not k.startswith(prefix):
            continue
        parts = k[len(prefix) :].split(".")
        if len(parts) < 2:
            continue
        out.setdefault(parts[0], {})[".".join(parts[1:])] = v
    return out


# ---------------------------------------------------------------------------
# R43
# ---------------------------------------------------------------------------


def render_r43(d: dict[str, Any]) -> str:
    f = d["fields"]
    L = [
        f"# R43 区间段风格 / 板块 · 多维结果报告（`{d['tag']}`）",
        "",
        "> 由入库摘要机械渲染（`result_report`），数字只来自产物；判决以 R43 结论段为准。"
        "价差 = 段内「该维度高五分位 − 低五分位」个股超额收益的段均值；t = 跨段 t 统计量；"
        "同号比例 = 价差与均值同号的段占比。",
        "",
    ]
    segs = {
        k.split(".")[1]: v
        for k, v in f.items()
        if k.startswith("style.") and k.endswith(".n_qualified_segs")
    }
    L += ["## 1. 样本：合格区间段", "", "| 方向 | 挖掘 A | 挖掘 B |", "|---|---:|---:|"]
    for dr in sorted(segs):
        L.append(
            f"| {'空头段' if dr == 'bear' else '多头段'} | {segs[dr].get('A', '—')} | {segs[dr].get('B', '—')} |"
        )
    drops = f.get("segment_drops") or {}
    L += [
        "",
        f"合格段合计 {f.get('n_qualified_segments', '—')}；剔除 跨挖掘段 {drops.get('crossing', '—')}、"
        f"短于 5 日 {drops.get('short', '—')}。",
        "",
    ]
    for dr, label in (("bear", "空头段"), ("bull", "多头段")):
        dims = _group(f, f"style.{dr}.dims.")
        if not dims:
            continue
        L += [
            f"## 2{'a' if dr == 'bear' else 'b'}. {label} · 风格维度（{len(dims)} 维）",
            "",
        ]
        L += _pie(
            f"{label}风格维度判决分布",
            _verdict_counts([v.get("verdict", "") for v in dims.values()]),
        )

        def tA(item: tuple[str, dict]) -> float:
            t = ((item[1].get("windows") or {}).get("A") or {}).get("t")
            return -abs(t) if isinstance(t, (int, float)) else 0.0

        rows = sorted(dims.items(), key=tA)
        L += [
            "",
            "| 维度 | A 价差 | A t | A 同号比例 | B 价差 | B t | B 同号比例 | C4 A/B | 判决 |",
            "|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
        for name, v in rows:
            w = v.get("windows") or {}
            a, b = w.get("A") or {}, w.get("B") or {}
            c4 = v.get("c4_by_window") or {}
            L.append(
                f"| {name} | {_num(a.get('mean_spread'), 2, True)} | {_num(a.get('t'))} | "
                f"{_num(a.get('frac_same_sign'), 0, True).lstrip('+')} | {_num(b.get('mean_spread'), 2, True)} | "
                f"{_num(b.get('t'))} | {_num(b.get('frac_same_sign'), 0, True).lstrip('+')} | "
                f"{c4.get('A', '—')}/{c4.get('B', '—')} | {VERDICT_ICON.get(str(v.get('verdict')), '')} {v.get('verdict')} |"
            )
        top = [
            r
            for r in rows
            if isinstance(
                ((r[1].get("windows") or {}).get("A") or {}).get("t"), (int, float)
            )
        ][:12]
        L += [""] + _xychart(
            f"{label} |t| 最大的 12 维：两窗价差（%）",
            [n for n, _ in top],
            [
                (
                    lab,
                    [
                        float(
                            (
                                ((v.get("windows") or {}).get(w) or {}).get(
                                    "mean_spread"
                                )
                                or 0.0
                            )
                            * 100
                        )
                        for _n, v in top
                    ],
                )
                for lab, w in (("挖掘A", "A"), ("挖掘B", "B"))
            ],
            "价差 %",
        )
        L.append("")
    for dr, label in (("bear", "空头段"), ("bull", "多头段")):
        idx = _group(f, f"sector.{dr}.indices.")
        if not idx:
            continue
        L += [
            f"## 3{'a' if dr == 'bear' else 'b'}. {label} · 板块层（{len(idx)} 个板块指数）",
            "",
        ]
        by_g: dict[str, dict[str, int]] = {}
        for v in idx.values():
            g = str(v.get("group") or "未分组")
            vv = str(v.get("verdict"))
            by_g.setdefault(g, {}).setdefault(vv, 0)
            by_g[g][vv] += 1
        verds = sorted({k for g in by_g.values() for k in g})
        L += [
            "| 板块类型 | "
            + " | ".join(f"{VERDICT_ICON.get(x, '')} {x}" for x in verds)
            + " |",
            "|---|" + "---:|" * len(verds),
        ]
        for g in sorted(by_g):
            L.append(
                f"| {g} | " + " | ".join(str(by_g[g].get(x, 0)) for x in verds) + " |"
            )

        def tAs(item: tuple[str, dict]) -> float:
            t = ((item[1].get("windows") or {}).get("A") or {}).get("t")
            return -t if isinstance(t, (int, float)) else math.inf

        best = sorted(idx.items(), key=tAs)[:15]
        L += [
            "",
            f"A 窗 t 最高的 15 个板块（两窗对照——A 强 B 不跟 = 不可复现）：",
            "",
            "| 板块 | 类型 | A 段均超额 | A t | B 段均超额 | B t | 判决 |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
        for c, v in best:
            w = v.get("windows") or {}
            a, b = w.get("A") or {}, w.get("B") or {}
            L.append(
                f"| {c} | {v.get('group', '—')} | {_num(a.get('mean_excess'), 2, True)} | {_num(a.get('t'))} | "
                f"{_num(b.get('mean_excess'), 2, True)} | {_num(b.get('t'))} | "
                f"{VERDICT_ICON.get(str(v.get('verdict')), '')} {v.get('verdict')} |"
            )
        L.append("")
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# R39
# ---------------------------------------------------------------------------


def render_r39(d: dict[str, Any]) -> str:
    f = d["fields"]
    cfgs: list[dict] = f.get("configs") or []
    profiles = list((f.get("profiles") or {}).keys())
    L = [
        f"# R39 因子 × 出场交互 · 多维结果报告（`{d['tag']}`）",
        "",
        "> 由入库摘要机械渲染（`result_report`），数字只来自产物；判决以 R39 结论段为准。"
        "margin = 胜率 − 盈亏平衡胜率（全候选交易口径）；「A」= 挖掘段选型窗，「B」= 验证窗。",
        "",
    ]
    L += [
        "## 1. 四个出场档（统一用于所有信号时）",
        "",
        "| 出场档 | 参数 | A margin | A 胜率 | A 盈亏比 | B margin | B 胜率 | B 盈亏比 | A 组合成交 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    uni: dict[str, dict] = {}
    for c in cfgs:
        m = c.get("mapping") or []
        if m and len(set(m)) == 1 and m[0] not in uni:
            uni[m[0]] = c
    for p in profiles:
        cu = uni.get(p)
        g = str(((f.get("profiles") or {}).get(p) or {}).get("key", "")).replace(
            "|", " · "
        )  # 表格内禁竖线
        if cu is None:
            L.append(f"| {p} | `{g}` | — | — | — | — | — | — | — |")
            continue
        a, b = cu.get("mining") or {}, cu.get("judgment") or {}
        L.append(
            f"| {p} | `{g}` | {_pp(a.get('margin'))} | {_num(a.get('win_rate'), 1, True).lstrip('+')} | "
            f"{a.get('payoff_ratio', '—')} | {_pp(b.get('margin'))} | {_num(b.get('win_rate'), 1, True).lstrip('+')} | "
            f"{b.get('payoff_ratio', '—')} | {a.get('n_taken', '—')} |"
        )
    L += [""] + _xychart(
        "统一出场档 margin（pp）",
        [p for p in profiles if p in uni],
        [
            (
                "A",
                [
                    float(((uni[p].get("mining") or {}).get("margin") or 0) * 100)
                    for p in profiles
                    if p in uni
                ],
            ),
            (
                "B",
                [
                    float(((uni[p].get("judgment") or {}).get("margin") or 0) * 100)
                    for p in profiles
                    if p in uni
                ],
            ),
        ],
        "margin pp",
    )
    for bk in sorted({str(c["bucketing"]) for c in cfgs if c.get("bucketing")}):
        sub = [c for c in cfgs if c.get("bucketing") == bk]
        nb = len((sub[0].get("mapping") or [])) if sub else 0
        cuts = ((f.get("bucketings") or {}).get(bk) or {}).get("cuts_mining_estimated")
        L += ["", f"## 2. 分桶 {bk}（{nb} 桶，挖掘 A 切点 {cuts}）· 映射矩阵", ""]
        if nb == 2:
            L += [
                "A 窗 margin（pp）：行 = 桶 0（低 ADX）出场档，列 = 桶 1（高 ADX）出场档；对角线 = 统一出场。",
                "",
                "| 桶0 \\ 桶1 | " + " | ".join(profiles) + " |",
                "|---|" + "---:|" * len(profiles),
            ]
            grid = {tuple(c["mapping"]): c for c in sub}
            for p0 in profiles:
                cells = []
                for p1 in profiles:
                    cg = grid.get((p0, p1))
                    v = _pp((cg or {}).get("mining", {}).get("margin")) if cg else "—"
                    cells.append(f"**{v}**" if p0 == p1 else v)
                L.append(f"| {p0} | " + " | ".join(cells) + " |")
        ranked = sorted(
            sub, key=lambda c: -(((c.get("mining") or {}).get("margin")) or -9)
        )[:10]
        L += [
            "",
            f"{bk} 下 A 窗 margin 前 10 的映射：",
            "",
            "| 映射（按桶） | A margin | A 胜率 | A 盈亏比 | B margin | 各桶组合成交 A | 各桶组合成交 B |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
        for c in ranked:
            a, b = c.get("mining") or {}, c.get("judgment") or {}
            L.append(
                f"| {' → '.join(c.get('mapping') or [])} | {_pp(a.get('margin'))} | "
                f"{_num(a.get('win_rate'), 1, True).lstrip('+')} | {a.get('payoff_ratio', '—')} | {_pp(b.get('margin'))} | "
                f"{a.get('n_taken_by_bucket', '—')} | {b.get('n_taken_by_bucket', '—')} |"
            )
    top = f.get("top") or {}
    c1 = f.get("criteria.C1") or {}
    L += [
        "",
        "## 3. 结论链",
        "",
        f"- 选型结果：分桶 `{top.get('bucketing')}`、映射 `{top.get('mapping')}`；uniform-best = `{(f.get('uniform_best') or {}).get('profile')}`。",
        f"- C2 Δ（vs uniform-best）：A {_pp((f.get('criteria.C2') or {}).get('d_margin_mining'))} / "
        f"B {_pp((f.get('criteria.C2') or {}).get('d_margin_judgment'))}；C4 池 q95 = {_pp((f.get('criteria.C4') or {}).get('q95'))}。",
        f"- C1 逐桶组合成交：A {((c1.get('mining') or {}).get('by_bucket'))}、B {((c1.get('judgment') or {}).get('by_bucket'))}"
        f"（桶下限 {c1.get('min_bucket')}）。",
        f"- 机械判决：{VERDICT_ICON.get(str(f.get('verdict')), '')} **{f.get('verdict')}**。",
    ]
    return "\n".join(L).rstrip() + "\n"


RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "r43_detail": render_r43,
    "r39_detail": render_r39,
}


def report_path(json_path: Path) -> Path:
    return json_path.with_name(json_path.stem + "_report.md")


def render(d: dict[str, Any]) -> str:
    fn = RENDERERS.get(d.get("kind", ""))
    if fn is None:
        raise ValueError(
            f"kind {d.get('kind')!r} 没有报告渲染器（支持：{sorted(RENDERERS)}）"
        )
    return f"<!-- report-of:{d['path']} -->\n" + fn(d)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="研究结果可视化报告（入库摘要 → 表格 + mermaid 图）"
    )
    ap.add_argument("digest_json", help="governance/research/results/<单元>/<tag>.json")
    ap.add_argument(
        "--write", action="store_true", help="写同目录 <tag>_report.md（入库）"
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    p = Path(args.digest_json)
    d = json.loads(p.read_text(encoding="utf-8"))
    try:
        md = render(d)
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    if args.write:
        out = report_path(p)
        out.write_text(md, encoding="utf-8")
        print(
            f"[OK] 写出 {out.relative_to(RESEARCH_DIR) if out.is_relative_to(RESEARCH_DIR) else out}"
        )
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
