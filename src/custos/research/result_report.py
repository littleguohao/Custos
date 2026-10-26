# -*- coding: utf-8 -*-
"""研究结果可视化报告（通用引擎，v0.428；owner 2026-10-20「做成通用模块，以后每个研究结果都能用」）。

从**入库摘要** ``governance/research/results/<单元>/<tag>.json``（``result_digest`` 产出，数字只来自产物）
机械渲染 markdown：判决总览 + 多维表格 + mermaid 图（``xychart-beta`` 柱状 / ``pie``，GitHub / 飞书可渲染）。
报告是摘要 JSON 的**纯函数**：``tests/test_result_report.py`` 逐字重渲染比对，手改即红。

**三层，新研究按需取用**（新终端合入清单第 6 条：登记 digest SPECS 时顺手登记 ``REPORTS``）：

1. **自动兜底**（任何 kind，零配置）：判决 + C1~C4 状态表 → 标量字段表 → 每个「同构子项族」
   （如 ``merged.x5.margin / merged.x7.margin …``）自动透视成一张表（行 = 子项，列 = 叶字段）→
   判决类字段（``*.verdict``）自动出饼图；
2. **声明式规格** ``REPORTS[kind] = [section, ...]``，section 类型：
   - ``{"type": "kv", "title", "paths": [...]}``：指定字段的「字段 | 值」表；
   - ``{"type": "pivot", "title", "rows": "merged.*", "cols": [(叶路径, 列名, 格式)], "sort": (叶路径, desc),
     "limit"}``：通配行透视表（行路径支持多个 ``*``，如 ``style.bear.dims.*``）；
   - ``{"type": "bar", "title", "rows", "series": [(叶路径, 图例, 倍数)], "limit", "sort"}``：柱状图；
   - ``{"type": "pie", "title", "values": "style.*.dims.*.verdict"}``：取值计数饼图；
   - ``{"type": "count", "title", "rows", "by": 分组叶路径, "of": 计数叶路径}``：分组 × 取值计数表；
   - ``{"type": "custom", "fn": callable(d) -> list[str]}``：通用件表达不了的（如 R39 映射矩阵）；
3. 格式：``pp``（×100 + pp）、``pct``（×100 + %）、``num``（±2 位）、``int``、``raw``。

用法（开发机即可跑，不需要行情数据）::

    uv run python -m custos.research result_report governance/research/results/R43/r43_v1_detail.json --write

``result_digest --write`` 缺省同时出报告（``--no-report`` 关）。描述性展示，判决以单元结论段为准。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import re
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
_MISSING = object()

# ---------------------------------------------------------------------------
# 取值：扁平键（digest fields）+ 值内下钻
# ---------------------------------------------------------------------------


def get_path(fields: dict[str, Any], path: str) -> Any:
    """``a.b.c``：先找完全匹配的扁平键；否则找最长前缀键，再在其值里按剩余段下钻。"""
    if path in fields:
        return fields[path]
    parts = path.split(".")
    for k in range(len(parts) - 1, 0, -1):
        head = ".".join(parts[:k])
        if head in fields:
            v: Any = fields[head]
            for seg in parts[k:]:
                if isinstance(v, dict) and seg in v:
                    v = v[seg]
                else:
                    return _MISSING
            return v
    return _MISSING


def expand_rows(fields: dict[str, Any], pattern: str) -> list[str]:
    """通配行路径 → 具体行路径（按出现的扁平键 + 值内 dict 键展开；保持字典序稳定）。"""
    rx = re.compile("^" + re.escape(pattern).replace(r"\*", r"([^.]+)") + r"(?:\.|$)")
    rows: set[str] = set()
    for k in fields:
        m = rx.match(k)
        if m:
            rows.add(k[: m.end()].rstrip("."))
    # 值内展开：最长前缀键是 dict ⇒ 按其键填 *
    pre = pattern.split("*")[0].rstrip(".")
    if pre and not rows:
        base = get_path(fields, pre)
        if isinstance(base, dict) and pattern.count("*") == 1 and pattern.endswith("*"):
            rows = {f"{pre}.{k}" for k in base}
    return sorted(rows, key=_natural)


def _natural(s: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def row_label(pattern: str, row: str) -> str:
    """行标签 = 通配位置上的段（多个 * 用 · 连接）。"""
    pp, rp = pattern.split("."), row.split(".")
    return " · ".join(r for p, r in zip(pp, rp) if p == "*") or row


# ---------------------------------------------------------------------------
# 格式
# ---------------------------------------------------------------------------


def fmt(v: Any, kind: str = "auto") -> str:
    if v is _MISSING or v is None:
        return "—"
    if isinstance(v, bool):
        return "✓" if v else "✗"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return "NaN"
        if kind == "pp":
            return f"{v * 100:+.2f}pp"
        if kind == "pct":
            return f"{v * 100:.1f}%"
        if kind == "num":
            return f"{v:+.2f}"
        if kind == "int":
            return f"{int(v):,}"
        if kind == "auto":
            return f"{v:,}" if isinstance(v, int) else f"{v:.4g}"
    if isinstance(v, str):
        return (f"{VERDICT_ICON[v]} {v}" if v in VERDICT_ICON else v).replace(
            "|", " · "
        )
    s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return (s if len(s) <= 160 else s[:157] + "…").replace("|", " · ")


def _md_table(
    header: list[str], rows: list[list[str]], align_right: Optional[list[bool]] = None
) -> list[str]:
    al = align_right or [False] + [True] * (len(header) - 1)
    return [
        "| " + " | ".join(header) + " |",
        "|" + "".join("---:|" if a else "---|" for a in al),
    ] + ["| " + " | ".join(r) + " |" for r in rows]


# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------


def xychart(
    title: str, xs: list[str], series: list[tuple[str, list[float]]], y_label: str
) -> list[str]:
    vals = [v for _n, ys in series for v in ys]
    if not xs or not vals:
        return []
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    cut = ""
    if min(vals) > 0 and (max(vals) - min(vals)) < 0.3 * max(
        vals
    ):  # 同号差异小：截断纵轴，否则看不出差
        lo, cut = min(vals), "，纵轴未从 0 起"
    elif max(vals) < 0 and (max(vals) - min(vals)) < 0.3 * abs(min(vals)):
        hi, cut = max(vals), "，纵轴未从 0 起"
    pad = (hi - lo) * 0.1 or 1.0
    legend = " / ".join(n for n, _ in series)
    out = [
        "```mermaid",
        "xychart-beta",
        f'    title "{title}（{legend}{cut}）"',
        "    x-axis [" + ", ".join('"' + x.replace('"', "'") + '"' for x in xs) + "]",
        f'    y-axis "{y_label}" {lo - pad:.2f} --> {hi + pad:.2f}',
    ]
    out += [f"    bar [{', '.join(f'{v:.2f}' for v in ys)}]" for _n, ys in series]
    return out + ["```"]


def pie(title: str, counts: dict[str, int]) -> list[str]:
    items = [(k, v) for k, v in counts.items() if v]
    if not items:
        return []
    return (
        ["```mermaid", f"pie title {title}"]
        + [f'    "{k}" : {v}' for k, v in items]
        + ["```"]
    )


# ---------------------------------------------------------------------------
# section 实现
# ---------------------------------------------------------------------------


def _sorted_rows(
    f: dict[str, Any], rows: list[str], sort: Optional[tuple[str, bool]]
) -> list[str]:
    """按叶字段排序（``leaf|abs`` = 按绝对值）；非数值垫底，稳定。"""
    if not sort:
        return rows
    leaf, desc = sort
    use_abs = leaf.endswith("|abs")
    leaf = leaf.removesuffix("|abs")

    def key(r: str) -> tuple[int, float]:
        v = get_path(f, f"{r}.{leaf}")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v:
            return (1, 0.0)
        x = abs(v) if use_abs else float(v)
        return (0, -x if desc else x)

    return sorted(rows, key=key)


def sec_kv(d: dict[str, Any], s: dict[str, Any]) -> list[str]:
    f = d["fields"]
    rows = [
        [f"`{p}`", fmt(get_path(f, p), k)]
        for p, k in (_pk(x) for x in s["paths"])
        if get_path(f, p) is not _MISSING
    ]
    return _md_table(["字段", "值"], rows, [False, False]) if rows else []


def _pk(x: Any) -> tuple[str, str]:
    return (x, "auto") if isinstance(x, str) else (x[0], x[1])


def sec_pivot(d: dict[str, Any], s: dict[str, Any]) -> list[str]:
    f = d["fields"]
    rows = _sorted_rows(f, expand_rows(f, s["rows"]), s.get("sort"))[
        : s.get("limit") or None
    ]
    if not rows:
        return []
    cols = s["cols"]
    body = [
        [row_label(s["rows"], r)]
        + [
            fmt(get_path(f, f"{r}.{c[0]}"), c[2] if len(c) > 2 else "auto")
            for c in cols
        ]
        for r in rows
    ]
    return _md_table([s.get("row_title", "项")] + [c[1] for c in cols], body)


def sec_bar(d: dict[str, Any], s: dict[str, Any]) -> list[str]:
    f = d["fields"]
    rows = _sorted_rows(f, expand_rows(f, s["rows"]), s.get("sort"))[
        : s.get("limit") or None
    ]
    series = []
    for leaf, name, mult in s["series"]:
        ys = [get_path(f, f"{r}.{leaf}") for r in rows]
        series.append(
            (
                name,
                [
                    float(y) * mult
                    if isinstance(y, (int, float)) and not isinstance(y, bool)
                    else 0.0
                    for y in ys
                ],
            )
        )
    return xychart(
        s["title"],
        [row_label(s["rows"], r) for r in rows],
        series,
        s.get("y_label", ""),
    )


def sec_pie(d: dict[str, Any], s: dict[str, Any]) -> list[str]:
    f = d["fields"]
    vals = [v for k, v in f.items() if fnmatch.fnmatchcase(k, s["values"])]
    counts: dict[str, int] = {}
    for v in vals:
        k = fmt(v)
        counts[k] = counts.get(k, 0) + 1
    return pie(s["title"], dict(sorted(counts.items())))


def sec_count(d: dict[str, Any], s: dict[str, Any]) -> list[str]:
    f = d["fields"]
    rows = expand_rows(f, s["rows"])
    tab: dict[str, dict[str, int]] = {}
    for r in rows:
        g = fmt(get_path(f, f"{r}.{s['by']}"))
        v = fmt(get_path(f, f"{r}.{s['of']}"))
        tab.setdefault(g, {}).setdefault(v, 0)
        tab[g][v] += 1
    if not tab:
        return []
    vs = sorted({v for g in tab.values() for v in g})
    return _md_table(
        [s.get("row_title", "分组")] + vs + ["合计"],
        [
            [g] + [str(tab[g].get(v, 0)) for v in vs] + [str(sum(tab[g].values()))]
            for g in sorted(tab)
        ],
    )


SECTIONS: dict[str, Callable[[dict[str, Any], dict[str, Any]], list[str]]] = {
    "kv": sec_kv,
    "pivot": sec_pivot,
    "bar": sec_bar,
    "pie": sec_pie,
    "count": sec_count,
}

# ---------------------------------------------------------------------------
# 自动兜底
# ---------------------------------------------------------------------------


def _verdict_block(f: dict[str, Any]) -> list[str]:
    out: list[str] = []
    v = f.get("verdict")
    if v is not None:
        out += [f"**机械判决：{fmt(v)}**", ""]
    crit = [(k, f[k]) for k in sorted(f) if re.fullmatch(r"criteria\.C\d", k)]
    if crit:
        rows = []
        for k, c in crit:
            c = c if isinstance(c, dict) else {"value": c}
            st = c.get("state", c.get("ok"))
            rule = str(c.get("rule", ""))[:80]
            rows.append([k.split(".")[1], fmt(st), rule.replace("|", " · ") or "—"])
        out += _md_table(["判据", "结果", "规则"], rows, [False, False, False]) + [""]
    return out


def _families(f: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """同构子项族：``P.X.leaf`` 键里 P 相同、X 不同、leaf 集合相同 ⇒ 一张透视表（P.* 为行模式）。"""
    groups: dict[tuple[str, str], set[str]] = {}
    for k in f:
        parts = k.split(".")
        for i in range(1, len(parts) - 1):
            pre, mid, leaf = ".".join(parts[:i]), parts[i], ".".join(parts[i + 1 :])
            groups.setdefault((pre, leaf), set()).add(mid)
    fam: dict[str, set[str]] = {}
    for (pre, leaf), mids in groups.items():
        if len(mids) >= 3 and isinstance(
            get_path(f, f"{pre}.{sorted(mids)[0]}.{leaf}"), (int, float, str, bool)
        ):
            fam.setdefault(pre, set()).add(leaf)
    out = []
    used: set[str] = set()
    for pre in sorted(fam, key=lambda p: (p.count("."), p)):
        if any(pre.startswith(u + ".") for u in used):
            continue
        used.add(pre)
        out.append((pre, sorted(fam[pre])[:10]))
    return out


def auto_sections(d: dict[str, Any]) -> list[str]:
    f = d["fields"]
    L = ["## 判决", ""] + _verdict_block(f)
    fams = _families(f)
    in_fam = {k for pre, _ in fams for k in f if k.startswith(pre + ".")}
    scalars = [
        k
        for k in sorted(f)
        if k not in in_fam
        and not k.startswith(("criteria.C", "provenance."))
        and k != "verdict"
        and isinstance(f[k], (int, float, str, bool))
    ]
    if scalars:
        L += (
            ["## 读数", ""]
            + _md_table(
                ["字段", "值"], [[f"`{k}`", fmt(f[k])] for k in scalars], [False, False]
            )
            + [""]
        )
    for pre, leaves in fams:
        L += (
            [f"## {pre}.*", ""]
            + sec_pivot(d, {"rows": f"{pre}.*", "cols": [(lf, lf) for lf in leaves]})
            + [""]
        )
        verd = [lf for lf in leaves if lf.endswith("verdict")]
        for lf in verd:
            L += sec_pie(
                d, {"title": f"{pre}.* {lf} 分布", "values": f"{pre}.*.{lf}"}
            ) + [""]
    return L


# ---------------------------------------------------------------------------
# 单元规格
# ---------------------------------------------------------------------------


def _r39_profiles(d: dict[str, Any]) -> list[str]:
    f = d["fields"]
    cfgs: list[dict] = f.get("configs") or []
    profiles = list((f.get("profiles") or {}).keys())
    uni: dict[str, dict] = {}
    for c in cfgs:
        m = c.get("mapping") or []
        if m and len(set(m)) == 1 and m[0] not in uni:
            uni[m[0]] = c
    rows = []
    for p in profiles:
        cu = uni.get(p) or {}
        a, b = cu.get("mining") or {}, cu.get("judgment") or {}
        key = str(((f.get("profiles") or {}).get(p) or {}).get("key", ""))
        rows.append(
            [
                p,
                f"`{key.replace('|', ' · ')}`",
                fmt(a.get("margin"), "pp"),
                fmt(a.get("win_rate"), "pct"),
                fmt(a.get("payoff_ratio")),
                fmt(b.get("margin"), "pp"),
                fmt(b.get("win_rate"), "pct"),
                fmt(b.get("payoff_ratio")),
                fmt(a.get("n_taken"), "int"),
            ]
        )
    L = _md_table(
        [
            "出场档",
            "参数",
            "A margin",
            "A 胜率",
            "A 盈亏比",
            "B margin",
            "B 胜率",
            "B 盈亏比",
            "A 组合成交",
        ],
        rows,
        [False, False] + [True] * 7,
    )
    ps = [p for p in profiles if p in uni]
    return (
        L
        + [""]
        + xychart(
            "统一出场档 margin（pp）",
            ps,
            [
                (
                    "A",
                    [
                        float(((uni[p].get("mining") or {}).get("margin") or 0) * 100)
                        for p in ps
                    ],
                ),
                (
                    "B",
                    [
                        float(((uni[p].get("judgment") or {}).get("margin") or 0) * 100)
                        for p in ps
                    ],
                ),
            ],
            "margin pp",
        )
    )


def _r39_matrix(d: dict[str, Any]) -> list[str]:
    f = d["fields"]
    cfgs: list[dict] = f.get("configs") or []
    profiles = list((f.get("profiles") or {}).keys())
    L: list[str] = []
    for bk in sorted({str(c["bucketing"]) for c in cfgs if c.get("bucketing")}):
        sub = [c for c in cfgs if c.get("bucketing") == bk]
        nb = len(sub[0].get("mapping") or []) if sub else 0
        cuts = ((f.get("bucketings") or {}).get(bk) or {}).get("cuts_mining_estimated")
        L += [f"### 分桶 {bk}（{nb} 桶，挖掘 A 切点 {cuts}）", ""]
        if nb == 2:
            grid = {tuple(c["mapping"]): c for c in sub}
            L += [
                "A 窗 margin：行 = 桶 0（低因子值）出场档，列 = 桶 1（高因子值）出场档；**对角线 = 统一出场**。",
                "",
            ]
            body = []
            for p0 in profiles:
                cells = []
                for p1 in profiles:
                    cg = grid.get((p0, p1))
                    v = (
                        fmt(((cg or {}).get("mining") or {}).get("margin"), "pp")
                        if cg
                        else "—"
                    )
                    cells.append(f"**{v}**" if p0 == p1 else v)
                body.append([p0] + cells)
            L += _md_table(["桶0 \\ 桶1"] + profiles, body) + [""]
        ranked = sorted(
            sub, key=lambda c: -(((c.get("mining") or {}).get("margin")) or -9)
        )[:10]
        body = [
            [
                " → ".join(c.get("mapping") or []),
                fmt((c.get("mining") or {}).get("margin"), "pp"),
                fmt((c.get("mining") or {}).get("win_rate"), "pct"),
                fmt((c.get("mining") or {}).get("payoff_ratio")),
                fmt((c.get("judgment") or {}).get("margin"), "pp"),
                fmt((c.get("mining") or {}).get("n_taken_by_bucket")),
                fmt((c.get("judgment") or {}).get("n_taken_by_bucket")),
            ]
            for c in ranked
        ]
        L += (
            [f"{bk} 下 A 窗 margin 前 10 的映射：", ""]
            + _md_table(
                [
                    "映射（按桶）",
                    "A margin",
                    "A 胜率",
                    "A 盈亏比",
                    "B margin",
                    "各桶组合成交 A",
                    "各桶组合成交 B",
                ],
                body,
                [False, True, True, True, True, False, False],
            )
            + [""]
        )
    return L


def _style_sections(dr: str, label: str) -> list[dict[str, Any]]:
    rows = f"style.{dr}.dims.*"
    return [
        {
            "type": "pie",
            "title": f"{label}风格维度判决分布",
            "values": f"style.{dr}.dims.*.verdict",
        },
        {
            "type": "pivot",
            "title": f"{label} · 风格维度（按 A 窗 |t| 排序）",
            "rows": rows,
            "row_title": "维度",
            "sort": ("windows.A.t|abs", True),
            "cols": [
                ("windows.A.mean_spread", "A 价差", "pct"),
                ("windows.A.t", "A t", "num"),
                ("windows.A.frac_same_sign", "A 同号比例", "pct"),
                ("windows.B.mean_spread", "B 价差", "pct"),
                ("windows.B.t", "B t", "num"),
                ("windows.B.frac_same_sign", "B 同号比例", "pct"),
                ("c4_by_window.A", "C4 A"),
                ("c4_by_window.B", "C4 B"),
                ("verdict", "判决"),
            ],
        },
        {
            "type": "bar",
            "title": f"{label} |t| 最大的 12 维：两窗价差（%）",
            "rows": rows,
            "limit": 12,
            "sort": ("windows.A.t|abs", True),
            "y_label": "价差 %",
            "series": [
                ("windows.A.mean_spread", "挖掘A", 100.0),
                ("windows.B.mean_spread", "挖掘B", 100.0),
            ],
        },
        {
            "type": "count",
            "title": f"{label} · 板块层判决（按板块类型）",
            "rows": f"sector.{dr}.indices.*",
            "by": "group",
            "of": "verdict",
            "row_title": "板块类型",
        },
        {
            "type": "pivot",
            "title": f"{label} · A 窗 t 最高的 15 个板块（A 强 B 不跟 = 不可复现）",
            "rows": f"sector.{dr}.indices.*",
            "row_title": "板块",
            "sort": ("windows.A.t", True),
            "limit": 15,
            "cols": [
                ("group", "类型"),
                ("windows.A.mean_excess", "A 段均超额", "pct"),
                ("windows.A.t", "A t", "num"),
                ("windows.B.mean_excess", "B 段均超额", "pct"),
                ("windows.B.t", "B t", "num"),
                ("verdict", "判决"),
            ],
        },
    ]


def _dist_row(label: str, dist: Any, rdd: Any, extra: list[str]) -> list[str]:
    d = dist if isinstance(dist, dict) else {}
    q = d.get("q") or {}
    return [
        label,
        *extra,
        fmt(d.get("n"), "int"),
        fmt(d.get("win_rate"), "pct"),
        fmt(d.get("payoff_ratio")),
        fmt(d.get("avg_win"), "pct"),
        fmt(d.get("avg_loss"), "pct"),
        fmt(q.get("p5"), "pct"),
        fmt(q.get("p50"), "pct"),
        fmt(q.get("p95"), "pct"),
        fmt(d.get("ge_3x_avg_loss"), "pct"),
        fmt(d.get("top5pct_profit_share"), "pct"),
        fmt(rdd),
    ]


_DIST_COLS = [
    "笔数",
    "胜率",
    "盈亏比",
    "均盈",
    "均亏",
    "p5",
    "中位",
    "p95",
    "≥3×均亏占比",
    "前5%赢单毛利占比",
    "收益回撤比",
]


def _r36_frozen(d: dict[str, Any]) -> list[str]:
    """候选 vs 等权 vs 零假设臂：选中集分布画像 + rdd（v0.429；判决仍只看 Δ / 过门 / 判定线）。"""
    f = d["fields"]
    seeds = f.get("candidate.per_seed") or []
    s0 = seeds[0] if seeds else {}
    eq = f.get("candidate.equal_margin_median")
    L: list[str] = []
    if s0.get("top_sel_dist") or s0.get("equal_sel_dist"):
        L += ["候选（adx_gt_60 冻结权重）与等权对照，平分种子 0：", ""]
        L += _md_table(
            ["组", *_DIST_COLS],
            [
                _dist_row(
                    "候选", s0.get("top_sel_dist"), s0.get("top_ret_over_dd"), []
                ),
                _dist_row(
                    "等权", s0.get("equal_sel_dist"), s0.get("equal_ret_over_dd"), []
                ),
            ],
        ) + [""]
    arms = [a for a in (f.get("arms") or []) if isinstance(a, dict)]
    if not arms:
        return L
    counts: dict[str, int] = {}
    for a in arms:
        counts[str(a.get("status"))] = counts.get(str(a.get("status")), 0) + 1
    L += pie("零假设臂状态", counts) + [""]

    def delta(a: dict) -> Optional[float]:
        sm = a.get("sel_margin")
        return (
            sm - eq
            if isinstance(sm, (int, float)) and isinstance(eq, (int, float))
            else None
        )

    ranked = sorted(arms, key=lambda a: (delta(a) is None, -(delta(a) or 0.0)))
    rows = [
        _dist_row(
            f"臂 {a.get('arm')}",
            a.get("sel_dist"),
            a.get("ret_over_dd"),
            [fmt(a.get("status")), fmt(delta(a), "pp")],
        )
        for a in ranked
    ]
    cand = f.get("candidate.delta_median")
    L += [
        f"零假设臂（按 Δ 降序；候选 Δ 中位 = {fmt(cand, 'pp')}；Δ = 臂选中集 margin − 等权中位）：",
        "",
    ]
    L += _md_table(["臂", "状态", "Δ", *_DIST_COLS], rows) + [""]
    ds: list[tuple[str, float]] = [
        (f"臂{a.get('arm')}", v) for a in ranked if (v := delta(a)) is not None
    ][:20]
    if ds:
        L += xychart(
            "Δ 前 20 臂 vs 候选（pp）",
            [x for x, _ in ds] + ["候选"],
            [("Δ", [float(v) * 100 for _, v in ds] + [float(cand or 0) * 100])],
            "Δ pp",
        )
    return L


REPORTS: dict[str, dict[str, Any]] = {
    "r36_frozen": {
        "title": "R36 冻结配置读数 · 结果报告",
        "note": "选中集 = 每日按分数取前 20（不过组合并发）；分布画像为描述性副读数，**判决只看 Δ、过门率、判定线**，"
        "rdd 门不过的臂不因分布好看而回池。",
        "sections": [
            {
                "type": "kv",
                "title": "主读数",
                "paths": [
                    ("candidate.delta_median", "pp"),
                    ("candidate.delta_portfolio_median", "pp"),
                    ("candidate.gate_rate", "pct"),
                    "candidate.n_selected_median",
                    "candidate.n_taken_median",
                    "window",
                    "line",
                ],
            },
            {
                "type": "custom",
                "title": "候选 / 等权 / 零假设臂：分布画像",
                "fn": _r36_frozen,
            },
        ],
    },
    "r43_detail": {
        "title": "R43 区间段风格 / 板块 · 多维结果报告",
        "note": "价差 = 段内「该维度高五分位 − 低五分位」个股超额收益的段均值；t = 跨段 t；同号比例 = 价差与均值同号的段占比。",
        "sections": [
            {
                "type": "kv",
                "title": "样本：合格区间段",
                "paths": [
                    "n_qualified_segments",
                    "segment_drops",
                    "style.bear.n_qualified_segs",
                    "style.bull.n_qualified_segs",
                ],
            },
            *_style_sections("bear", "空头段"),
            *_style_sections("bull", "多头段"),
        ],
    },
    "r39_detail": {
        "title": "R39 因子 × 出场交互 · 多维结果报告",
        "note": "margin = 胜率 − 盈亏平衡胜率（全候选交易口径）；A = 挖掘选型窗，B = 验证窗。",
        "sections": [
            {
                "type": "custom",
                "title": "四个出场档（统一用于所有信号时）",
                "fn": _r39_profiles,
            },
            {"type": "custom", "title": "分桶映射矩阵", "fn": _r39_matrix},
            {
                "type": "kv",
                "title": "结论链",
                "paths": [
                    "top.bucketing",
                    "top.mapping",
                    "uniform_best.profile",
                    ("criteria.C2.d_margin_mining", "pp"),
                    ("criteria.C2.d_margin_judgment", "pp"),
                    ("criteria.C4.q95", "pp"),
                    "criteria.C1.mining.by_bucket",
                    "criteria.C1.judgment.by_bucket",
                    "criteria.C1.min_bucket",
                    "verdict",
                ],
            },
        ],
    },
    "r48": {
        "title": "R48 B1 大单入场特征 · 结果报告",
        "note": "大单 = 单笔 ≥ +20%，大亏 = ≤ −10%；lift = 高档命中率 / 全体命中率；尾部比 = lift_big / lift_loss（>1 = 右尾提升快于大亏放大）。",
        "sections": [
            {
                "type": "pivot",
                "title": "11 个特征 · 判决",
                "rows": "features.*",
                "row_title": "特征",
                "cols": [
                    ("verdict", "判决"),
                    ("criteria.C1.ok", "C1"),
                    ("criteria.C2.ok", "C2"),
                    ("criteria.C3.ok", "C3"),
                    ("criteria.C4.state", "C4"),
                    ("criteria.C2.min_lift_big", "两窗最小 lift", "num"),
                ],
            },
            {
                "type": "pivot",
                "title": "逐窗读数",
                "rows": "features.*.windows.*",
                "row_title": "特征 · 窗",
                "cols": [
                    ("n_hi", "高档笔数", "int"),
                    ("missing_rate", "缺失率", "pct"),
                    ("p_big_hi", "高档大单率", "pct"),
                    ("p_big_all", "全体大单率", "pct"),
                    ("lift_big", "lift 大单", "num"),
                    ("lift_loss", "lift 大亏", "num"),
                    ("tail_ratio", "尾部比", "num"),
                    ("spearman", "五分位单调", "num"),
                    ("year_frac_gt1", "逐年 >1 占比", "pct"),
                ],
            },
            {
                "type": "bar",
                "title": "各特征逐窗 lift 大单",
                "rows": "features.*",
                "y_label": "lift",
                "series": [
                    ("windows.mining_a.lift_big", "挖掘A", 1.0),
                    ("windows.mining_b.lift_big", "挖掘B", 1.0),
                ],
            },
            {
                "type": "kv",
                "title": "零假设与样本",
                "paths": ["c4_pool.q95", "c4_pool.evaluated", "n_trades", "base_rates"],
            },
        ],
    },
    "r49": {
        "title": "R49 B1 高盈亏比交易逆向画像 · 探索报告",
        "note": "探索性：A 发现 → B 复现；lead_robust = 复现且过「整面板挑最好」的簇级置换 q95。线索只供预注册，不是判决。",
        "sections": [
            {
                "type": "kv",
                "title": "总览",
                "paths": [
                    "verdict",
                    "robust_leads",
                    "real_max_stat",
                    "c4_pool.q95",
                    "n_cells",
                    "n_trades",
                    "base_rates",
                ],
            },
            {
                "type": "pivot",
                "title": "线索（A 合格格）",
                "rows": "leads.*",
                "row_title": "#",
                "cols": [
                    ("cell", "格子"),
                    ("status", "状态"),
                    ("stat", "min(lift A, B)", "num"),
                    ("lift_big_a", "lift A", "num"),
                    ("lift_big_b", "lift B", "num"),
                    ("lift_loss_b", "大亏 lift B", "num"),
                    ("n_hi_b", "B 高档笔数", "int"),
                ],
            },
            {
                "type": "pivot",
                "title": "流动性底线 · 挖掘 A",
                "rows": "liquidity.mining_a.*",
                "row_title": "#",
                "cols": [
                    ("floor_yi", "amt20 ≥（亿）", "num"),
                    ("n", "笔数", "int"),
                    ("share", "占比", "pct"),
                    ("p_big", "大单率", "pct"),
                    ("p_loss", "大亏率", "pct"),
                    ("mean_ret_hi", "均收益", "pct"),
                    ("win_rate_hi", "胜率", "pct"),
                    ("payoff_hi", "盈亏比", "num"),
                ],
            },
            {
                "type": "pivot",
                "title": "流动性底线 · 挖掘 B",
                "rows": "liquidity.mining_b.*",
                "row_title": "#",
                "cols": [
                    ("floor_yi", "amt20 ≥（亿）", "num"),
                    ("n", "笔数", "int"),
                    ("share", "占比", "pct"),
                    ("p_big", "大单率", "pct"),
                    ("p_loss", "大亏率", "pct"),
                    ("mean_ret_hi", "均收益", "pct"),
                    ("win_rate_hi", "胜率", "pct"),
                    ("payoff_hi", "盈亏比", "num"),
                ],
            },
        ],
    },
    "r50": {
        "title": "R50 ≥20% 波段逆向匹配 · 探索报告",
        "note": "探索性：召回/精度 lift/时点/漏斗/兑现全为描述性读数；lead_robust = A 合格（n≥1000 ∧ lift>1.2）"
        "且 B 同门槛复现，且 min(lift_A, lift_B) 过 16 对象 max 的簇级置换 q95。线索只供预注册，不是判决。",
        "sections": [
            {
                "type": "kv",
                "title": "总览",
                "paths": [
                    "verdict",
                    "robust_leads",
                    "c4_pool.q95",
                    "c4_pool.evaluated",
                    "n_swings",
                    "swing_bands",
                ],
            },
            {
                "type": "pivot",
                "title": "16 对象 · 线索判决",
                "rows": "objects.*",
                "row_title": "对象",
                "cols": [
                    ("status", "状态"),
                    ("stat", "min(liftA,liftB)", "num"),
                    ("lift_a", "lift A", "num"),
                    ("lift_b", "lift B", "num"),
                ],
            },
            {
                "type": "pivot",
                "title": "逐对象读数 · 挖掘 A",
                "rows": "objects.*",
                "row_title": "对象",
                "cols": [
                    ("windows.mining_a.n_signals", "信号数", "int"),
                    ("windows.mining_a.precision", "精度", "pct"),
                    ("windows.mining_a.baseline", "随机基线", "pct"),
                    ("windows.mining_a.lift", "lift", "num"),
                    ("windows.mining_a.recall", "召回", "pct"),
                    ("windows.mining_a.recall_wide", "宽窗召回", "pct"),
                ],
            },
            {
                "type": "pivot",
                "title": "逐对象读数 · 挖掘 B",
                "rows": "objects.*",
                "row_title": "对象",
                "cols": [
                    ("windows.mining_b.n_signals", "信号数", "int"),
                    ("windows.mining_b.precision", "精度", "pct"),
                    ("windows.mining_b.baseline", "随机基线", "pct"),
                    ("windows.mining_b.lift", "lift", "num"),
                    ("windows.mining_b.recall", "召回", "pct"),
                    ("windows.mining_b.recall_wide", "宽窗召回", "pct"),
                ],
            },
            {
                "type": "pivot",
                "title": "B1 拦截漏斗（波段逐层剩余）",
                "rows": "funnel.*",
                "row_title": "窗",
                "cols": [
                    ("n_swings", "波段数", "int"),
                    ("share_l1", "L1 J<13", "pct"),
                    ("share_l2", "L2 +0AMV", "pct"),
                    ("share_l3", "L3 过观察线", "pct"),
                    ("share_l4", "L4 top-N", "pct"),
                    ("loss.no_j13", "J<13 未触发", "int"),
                    ("loss.amv_not_long", "0AMV 非做多", "int"),
                    ("loss.below_score_line", "打分层", "int"),
                    ("loss.not_top_n", "top-N 未进", "int"),
                ],
            },
            {
                "type": "pivot",
                "title": "兑现（LIVE_PARAMS 重放，L1 起信号的波段）",
                "rows": "realization.*",
                "row_title": "窗",
                "cols": [
                    ("n_pairs", "配对数", "int"),
                    ("n_trades", "成交", "int"),
                    ("n_realized", "已实现", "int"),
                    ("ret_mean", "均收益", "pct"),
                    ("capture_mean", "捕获率 ret/amp", "pct"),
                    ("by_reason", "出场原因"),
                ],
            },
        ],
    },
    "r47_scan": {
        "title": "R47 浮盈保护（保本 × 移动止损）· 结果报告",
        "note": "格名 beX_trY = 保本触发 X% × 移动止损回撤 Y%；be0_tr0 = 现行出场；大单 = 单笔 ≥ +20%；"
        "浮盈回吐 = 持有期最大浮盈 ≥5% 仍亏损离场。",
        "sections": [
            {
                "type": "pivot",
                "title": "16 格（两段合并）",
                "rows": "merged.*",
                "row_title": "格",
                "sort": ("margin", True),
                "cols": [
                    ("margin", "margin", "pp"),
                    ("win_rate", "胜率", "pct"),
                    ("payoff_ratio", "盈亏比"),
                    ("rdd_gate", "过 rdd 门"),
                    ("n_big_win", "大单笔数", "int"),
                    ("stop_share", "止损占比", "pct"),
                    ("n_taken", "组合成交", "int"),
                ],
            },
            {
                "type": "bar",
                "title": "各格合并 margin（pp）",
                "rows": "merged.*",
                "y_label": "margin pp",
                "series": [("margin", "合并", 100.0)],
            },
            {
                "type": "pivot",
                "title": "逐窗：回吐与右尾",
                "rows": "readings.*.*",
                "row_title": "窗 · 格",
                "cols": [
                    ("margin", "margin", "pp"),
                    ("give_back_share", "浮盈回吐占比", "pct"),
                    ("n_big_win", "大单笔数", "int"),
                    ("win_rate", "胜率", "pct"),
                    ("payoff_ratio", "盈亏比"),
                    ("rdd_gate", "过 rdd 门"),
                    ("avg_holding", "平均持有"),
                ],
            },
            {
                "type": "kv",
                "title": "结论链",
                "paths": [
                    "criteria.C2.g_star",
                    ("criteria.C2.merged_delta_margin", "pp"),
                    "criteria.C2.windows",
                    "criteria.C3.neighbors_positive",
                    "criteria.C3.tail",
                    ("criteria.C4.q95", "pp"),
                    "verdict",
                ],
            },
        ],
    },
    "r44_scan": {
        "title": "R44 J 日低点止损宽度 · 结果报告",
        "note": "margin = 胜率 − 盈亏平衡胜率；ref = 现行 pct10；合并 = 挖掘 A∪B。",
        "sections": [
            {
                "type": "pivot",
                "title": "各档（两段合并）",
                "rows": "merged.*",
                "row_title": "档位",
                "cols": [
                    ("margin", "margin", "pp"),
                    ("win_rate", "胜率", "pct"),
                    ("payoff_ratio", "盈亏比"),
                    ("rdd_gate", "过 rdd 门"),
                    ("stop_share", "止损占比", "pct"),
                    ("n_taken", "组合成交", "int"),
                ],
            },
            {
                "type": "bar",
                "title": "各档合并 margin（pp）",
                "rows": "merged.*",
                "y_label": "margin pp",
                "series": [("margin", "合并", 100.0)],
            },
            {
                "type": "pivot",
                "title": "逐窗读数",
                "rows": "readings.*.*",
                "row_title": "窗 · 档",
                "cols": [
                    ("margin", "margin", "pp"),
                    ("win_rate", "胜率", "pct"),
                    ("payoff_ratio", "盈亏比"),
                    ("expectancy", "每笔均收益", "pct"),
                    ("rdd_gate", "过 rdd 门"),
                    ("cagr", "CAGR", "pct"),
                    ("max_drawdown", "最大回撤", "pct"),
                ],
            },
        ],
    },
}


def render(d: dict[str, Any]) -> str:
    spec = REPORTS.get(d.get("kind", ""), {})
    title = spec.get("title") or f"{d.get('unit')} · {d.get('kind')} 结果报告"
    L = [
        f"<!-- report-of:{d['path']} -->",
        f"# {title}（`{d['tag']}`）",
        "",
        "> 由入库摘要机械渲染（`result_report`），数字只来自产物，勿手改；判决以单元结论段为准。"
        + (" " + spec["note"] if spec.get("note") else ""),
        "",
    ]
    if spec.get("sections"):
        L += _verdict_block(d["fields"])
        for s in spec["sections"]:
            body = s["fn"](d) if s["type"] == "custom" else SECTIONS[s["type"]](d, s)
            if body:
                L += [f"## {s['title']}", ""] + body + [""]
    else:
        L += auto_sections(d)
    return "\n".join(L).rstrip() + "\n"


def report_path(json_path: Path) -> Path:
    return json_path.with_name(json_path.stem + "_report.md")


def write_report(json_path: Path) -> Path:
    out = report_path(json_path)
    out.write_text(
        render(json.loads(json_path.read_text(encoding="utf-8"))), encoding="utf-8"
    )
    return out


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="研究结果可视化报告（入库摘要 → 表格 + mermaid 图；任意 kind 可用）"
    )
    ap.add_argument(
        "digest_json",
        nargs="+",
        help="governance/research/results/<单元>/<tag>.json（可多份）",
    )
    ap.add_argument(
        "--write", action="store_true", help="写同目录 <tag>_report.md（入库）"
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    for p in map(Path, args.digest_json):
        if args.write:
            print(f"[OK] 写出 {write_report(p)}")
        else:
            print(render(json.loads(p.read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
