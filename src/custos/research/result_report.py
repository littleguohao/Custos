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


REPORTS: dict[str, dict[str, Any]] = {
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
