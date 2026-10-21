# -*- coding: utf-8 -*-
"""跑数结果摘要（回填协议，owner 2026-10-17）：按**预注册写死的字段清单**从产物 JSON 机械提取读数，
生成 ``governance/research/results/<单元>/<tag>.json``（入库）+ 可粘贴进研究单元回填区的 markdown 块。

用法（生产机，每跑完一条命令）::

    uv run python -m custos.research result_digest <kind> <产物.json> --tag <tag> --write [--backfill]

- ``--write``：写 results/ 摘要 JSON；``--backfill``：同时把 markdown 块插到对应研究单元
  「回填区」标题下（最新在上）；不加 ⇒ 只打印预览。
- 机械判决、所有数字**只来自产物**；回填块首尾带 ``<!-- digest:… -->`` 标记，
  ``tests/test_result_digest.py`` 会从 results/ JSON 重渲染逐字比对——手改数字测试必红。
- 实质读数 / 判读写在块**外面**；单元头部状态（判毕）与 TODO 闭环留给复核后再改。

字段清单 ``SPECS`` 即各单元回填模板（改清单 = 改预注册，跑数前才许改）；路径支持 ``*`` 展开
dict 键，末尾 ``?`` = 可选（缺失不报错，如预览模式没有判定段块）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

from custos.core.paths import RESEARCH_DIR

DIGEST_VERSION = 1
RESULTS_DIR = RESEARCH_DIR / "results"

_PROV = [
    "provenance.git_sha?",
    "provenance.git_dirty?",
    "provenance.universe_rule?",
    "provenance.n_codes?",
    "provenance.universe_sha256?",
    "provenance.data_last_date?",
    "provenance.cmdline?",
    "window_usage?",
]

#: R40 / R45（bear_regime_study --trade-dist）top 交易集分布画像字段
_BEAR_DIST = [
    "verdict",
    "top.exit",
    "top.extended.*.portfolio",
    "top.extended.*.n_unfillable",
    "top.extended.*.dist.n",
    "top.extended.*.dist.win_rate",
    "top.extended.*.dist.payoff_ratio",
    "top.extended.*.dist.margin",
    "top.extended.*.dist.expectancy",
    "top.extended.*.dist.quantiles",
    "top.extended.*.dist.big_win_share",
    "top.extended.*.dist.profit_concentration",
    "top.extended.*.dist.buckets",
    "top.extended.*.dist.by_reason",
    "top.extended.*.dist.by_year",
    "top.extended.*.dist.holding",
    *_PROV,
]

#: kind → (单元文档前缀, 字段清单)。单元前缀用于 --backfill 定位文档与 results 子目录。
SPECS: dict[str, tuple[str, list[str]]] = {
    "probe": (
        "TODO91",
        [
            "generated_at",
            "min_bars",
            "holdout_cap",
            "amv_ledger",
            "stocks",
            "sector_index_vipdoc",
            "sector_index_csv?",
        ],
    ),
    "baseline": (
        "TODO92",
        [
            "scorer?",
            "entry_filter?",
            "start?",
            "end?",
            "count?",
            "trade_summary.n",
            "trade_summary.win_rate",
            "trade_summary.payoff_ratio",
            "trade_summary.expectancy",
            "trade_summary.expectancy_R",
            "trade_summary.exit_reasons.open_end?",
            "portfolio.n_taken?",
            "portfolio.total_return?",
            "portfolio.cagr?",
            "portfolio.max_drawdown?",
            "portfolio.return_over_maxdd?",
            "timing?",
        ],
    ),
    "r36_frozen": (
        "R36",
        [
            "verdict",
            "window",
            "config.frozen_tag?",
            "candidate.weights",
            "candidate.delta_median",
            "candidate.gate_rate",
            "candidate.n_taken_median",
            "candidate.n_selected_median",
            "candidate.delta_portfolio_median",
            "candidate.equal_margin_median",
            "config.reading_basis",
            "pool",
            "candidate.per_seed?",  # v0.429：种子 0 带候选 / 等权选中集分布画像与 rdd
            "arms?",  # v0.425：臂状态（ok / gate_fail / no_selected_reading / no_best）——池为何不满
            "line?",
            *_PROV,
        ],
    ),
    "r39": (
        "R39",
        [
            "verdict",
            "windows",
            "n_signals",
            "top.bucketing?",
            "top.mapping?",
            "top.mining?",
            "top.judgment?",
            "uniform_best.profile?",
            "uniform_best.mining?",  # 必报副读数：uniform-best vs pct5_trail08（v0.422 复查补）
            "uniform_best.judgment?",
            "criteria.C1",
            "criteria.C2",
            "criteria.C3",
            "criteria.C4",
            "criteria.judgment_batch?",
            "cost_sensitivity.deltas?",
            *_PROV,
        ],
    ),
    "r40": (
        "R40",
        [
            "verdict",
            "windows",
            "top.gate?",
            "top.exit?",
            "top.mining?",
            "top.judgment?",
            "criteria.C1",
            "criteria.C2",
            "criteria.C3",
            "criteria.C4",
            "pools.q95_mining",
            "pools.q95_judgment",
            "pools.batch_m?",
            "pools.entry_accounting",
            "n_signals_by_gate",
            "cost_sensitivity.deltas?",
            # 必报 reason 分布（风险注记；v0.422 起 --trade-dist 下产出）
            "top.extended.*.dist.by_reason?",
            "top.extended.*.portfolio?",
            "segments.*.n_signals?",
            *_PROV,
        ],
    ),
    "r45": (
        "R45",
        [
            "verdict",
            "windows",
            "top.exit?",
            "top.mining?",
            "top.judgment?",
            "criteria.C1",
            "criteria.C2",
            "criteria.C3",
            "criteria.C4",
            "criteria.r45",
            # v0.422：R45 预注册「必报」（产物 v0.422 起恒报；旧产物缺 ⇒ 拒写）
            "top.extended.*.portfolio",
            "top.extended.*.n_unfillable",
            "top.extended.*.dist.expectancy",
            "top.extended.*.dist.holding",
            "top.extended.*.dist.by_reason",
            "segments.*.n_bear_days?",  # v2 布局才有 segments 块（生产缺省即 v2）
            "segments.*.n_signals?",
            "pools.q95_mining",
            "pools.q95_judgment",
            "pools.batch_m?",
            "pools.entry_accounting",
            "n_signals_by_gate",
            *_PROV,
        ],
    ),
    "r43": (
        "R43",
        [
            "n_qualified_segments",
            "segment_drops",
            "criteria.C4",
            "criteria.verdict_focus?",
            "style.*.n_qualified_segs",
            "style.*.direction_untested",
            "style.*.dims.*.verdict",
            "sector.*.indices.*.verdict?",
            *_PROV,
        ],
    ),
    "r43_judgment": (
        "R43",
        ["mode", "window", "n_real_segs", "candidates", "window_usage?"],
    ),
    "r44_scan": (
        "R44",
        [
            "verdict",
            "windows",
            "accounting",
            "criteria.C1",
            "criteria.C2",
            "criteria.C3",
            "criteria.C4",
            "merged.*.margin",
            "merged.*.win_rate",
            "merged.*.payoff_ratio",
            "merged.*.rdd_gate",
            "merged.*.n_taken",
            "merged.*.stop_share",
            # v0.420：预注册「必报读数」逐窗（v0.413 清单漏收胜率/盈亏比与分窗）
            "readings.*.*.margin",
            "readings.*.*.win_rate",
            "readings.*.*.payoff_ratio",
            "readings.*.*.expectancy",
            "readings.*.*.stop_share",
            "readings.*.*.avg_holding",
            "readings.*.*.rdd_gate",
            "readings.*.*.ret_over_dd",
            "readings.*.*.cagr",
            "readings.*.*.max_drawdown",
            "readings.*.*.n",
            "readings.*.*.n_taken",
            *_PROV,
        ],
    ),
    "r44_dist": (
        "R44",
        [
            "verdict",
            "merged.*.dist.n",
            "merged.*.dist.win_rate",
            "merged.*.dist.payoff_ratio",
            "merged.*.dist.margin",
            "merged.*.dist.avg_win",
            "merged.*.dist.avg_loss",
            "merged.*.dist.median_win",
            "merged.*.dist.median_loss",
            "merged.*.dist.expectancy",
            "merged.*.dist.quantiles",
            "merged.*.dist.big_win_share",
            "merged.*.dist.profit_concentration",
            "merged.*.dist.holding",
            "merged.*.dist.buckets",
            "merged.*.dist.by_reason",
            "merged.*.dist.by_year",
            "merged.*.dist.paired_vs_ref?",
            "readings.*.*.dist.win_rate",
            "readings.*.*.dist.payoff_ratio",
            "readings.*.*.dist.margin",
            "readings.*.*.dist.big_win_share",
            *_PROV,
        ],
    ),
    "r39_dist": (
        "R39",
        [
            "verdict",
            "trade_dist.*.n",
            "trade_dist.*.win_rate",
            "trade_dist.*.payoff_ratio",
            "trade_dist.*.margin",
            "trade_dist.*.avg_win",
            "trade_dist.*.avg_loss",
            "trade_dist.*.expectancy",
            "trade_dist.*.quantiles",
            "trade_dist.*.big_win_share",
            "trade_dist.*.profit_concentration",
            "trade_dist.*.buckets",
            "trade_dist.*.by_reason",
            "trade_dist.*.by_year",
            "trade_dist.*.paired_vs_ref?",
            "trade_dist.*.by_bucket?",
            *_PROV,
        ],
    ),
    "r40_dist": ("R40", _BEAR_DIST),
    "r45_dist": ("R45", _BEAR_DIST),
    "label_dist": (
        "R27",
        [
            "base_gate",
            "exit_filter?",
            "inputs",
            "n_holdout_dropped",
            "skipped",
            "cells.*.n",
            "cells.*.win_rate",
            "cells.*.payoff_ratio",
            "cells.*.margin",
            "cells.*.avg_win",
            "cells.*.avg_loss",
            "cells.*.median_win",
            "cells.*.median_loss",
            "cells.*.expectancy",
            "cells.*.quantiles",
            "cells.*.big_win_share",
            "cells.*.profit_concentration",
            "cells.*.holding",
            "cells.*.buckets",
            "cells.*.by_reason",
            "cells.*.by_year",
            "cells.*.n_exit_in_holdout",
            "cells.*.n_overlap_with_base?",
            "cells.*.vs_base?",
            "cells.*.vs_complement?",
        ],
    ),
    # v0.427 明细 kind：多维读数全量入库，供 result_report 出可视化报告（表格 + mermaid 图）
    "r43_detail": (
        "R43",
        [
            "n_qualified_segments",
            "segment_drops",
            "criteria",
            "style.*.n_qualified_segs",
            "style.*.dims.*.verdict",
            "style.*.dims.*.c4_by_window",
            "style.*.dims.*.windows",
            "sector.*.indices.*.group?",
            "sector.*.indices.*.verdict?",
            "sector.*.indices.*.windows?",
            *_PROV,
        ],
    ),
    "r39_detail": (
        "R39",
        [
            "verdict",
            "windows",
            "n_signals",
            "profiles",
            "bucketings",
            "configs",
            "top",
            "uniform_best",
            "criteria.C1",
            "criteria.C2",
            "criteria.C4",
            *_PROV,
        ],
    ),
    "r47_scan": (
        "R47",
        [
            "verdict",
            "windows",
            "grid",
            "accounting",
            "criteria.C1",
            "criteria.C2",
            "criteria.C3",
            "criteria.C4",
            "merged.*.margin",
            "merged.*.win_rate",
            "merged.*.payoff_ratio",
            "merged.*.rdd_gate",
            "merged.*.n_taken",
            "merged.*.n_big_win",
            "merged.*.stop_share",
            "readings.*.*.margin",
            "readings.*.*.win_rate",
            "readings.*.*.payoff_ratio",
            "readings.*.*.rdd_gate",
            "readings.*.*.n_big_win",
            "readings.*.*.give_back_share",
            "readings.*.*.expectancy",
            "readings.*.*.avg_holding",
            "readings.*.*.cagr",
            "readings.*.*.max_drawdown",
            *_PROV,
        ],
    ),
    "r48": (
        "R48",
        [
            "verdict",
            "candidates",
            "windows",
            "n_trades",
            "base_rates",
            "features.*.verdict",
            "features.*.criteria",
            "features.*.windows",
            "c4_pool.q95",
            "c4_pool.evaluated",
            "descriptive",
            *_PROV,
        ],
    ),
    "r49": (
        "R49",
        [
            "verdict",
            "robust_leads",
            "n_cells",
            "n_trades",
            "base_rates",
            "real_max_stat",
            "leads",
            "liquidity",
            "c4_pool.q95",
            "c4_pool.evaluated",
            "r48_side",
            *_PROV,
        ],
    ),
    "r44_judgment": (
        "R44",
        [
            "verdict",
            "params.stop_buffer_pct",
            "readings.judgment?",
            "criteria.judgment_batch",
            *_PROV,
        ],
    ),
    "r46": (
        "R46",
        [
            "verdict",
            "segments",
            "base",
            "base_equals_live",
            "criteria.C1",
            "criteria.C2",
            "criteria.C3",
            "criteria.C4",
            "top",
            "onoff_subgrid",
            "judgment_batch?",
            *_PROV,
        ],
    ),
}


def _walk(obj: Any, parts: list[str], prefix: str) -> list[tuple[str, Any]]:
    if not parts:
        return [(prefix, obj)]
    head, rest = parts[0], parts[1:]
    if head == "*":
        if not isinstance(obj, dict):
            return []
        out: list[tuple[str, Any]] = []
        for k in sorted(obj):
            out += _walk(obj[k], rest, f"{prefix}.{k}" if prefix else str(k))
        return out
    if isinstance(obj, dict) and head in obj:
        return _walk(obj[head], rest, f"{prefix}.{head}" if prefix else head)
    return []


def extract(kind: str, report: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """按 SPECS[kind] 提取：返回 ({路径: 值}, 缺失的必需路径)。"""
    _unit, paths = SPECS[kind]
    fields: dict[str, Any] = {}
    missing: list[str] = []
    for raw in paths:
        optional = raw.endswith("?")
        path = raw.rstrip("?")
        hits = _walk(report, path.split("."), "")
        if not hits and not optional:
            missing.append(path)
        for p, v in hits:
            fields[p] = v
    return fields, missing


def _fmt(v: Any) -> str:
    if (
        isinstance(v, list)
        and len(v) > 10
        and all(isinstance(x, (int, float)) for x in v)
    ):
        xs = [float(x) for x in v if x == x]
        if not xs:
            return f"len={len(v)}（全 NaN）"
        return (
            f"len={len(v)} min={min(xs):.6g} med={statistics.median(xs):.6g} "
            f"max={max(xs):.6g}（全量见 JSON）"
        )
    s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return s.replace("|", "\\|")


def _table_rows(v: Any) -> Optional[list[dict[str, Any]]]:
    """同构 dict 列表（如探针逐年表）⇒ 返回行，用子表渲染；否则 None。"""
    if (
        isinstance(v, list)
        and v
        and all(isinstance(x, dict) for x in v)
        and len({tuple(sorted(x)) for x in v}) == 1
        and all(not isinstance(y, (dict, list)) for x in v for y in x.values())
    ):
        return v
    return None


def _split_tables(
    prefix: str, v: Any, out: list[tuple[str, list[dict[str, Any]]]]
) -> Any:
    """把嵌套里的同构 dict 列表抽成子表，原位换成「见子表」占位（只影响渲染，不改 JSON）。"""
    rows = _table_rows(v)
    if rows is not None:
        out.append((prefix, rows))
        return f"见子表 {prefix}（{len(rows)} 行）"
    if isinstance(v, dict):
        return {k: _split_tables(f"{prefix}.{k}", x, out) for k, x in v.items()}
    return v


def _derived(digest: dict[str, Any]) -> list[tuple[str, Any]]:
    """派生读数（确定性、只用已提取字段）：baseline 的剔 open_end 期望（runbook 读数口径）。"""
    f = digest["fields"]
    out: list[tuple[str, Any]] = []
    if digest["kind"] == "baseline":
        n, e = f.get("trade_summary.n"), f.get("trade_summary.expectancy")
        oe = f.get("trade_summary.exit_reasons.open_end") or {}
        if isinstance(n, (int, float)) and isinstance(e, (int, float)) and n:
            k = oe.get("n") or 0
            if n - k > 0:
                real = (n * e - k * (oe.get("avg_return") or 0.0)) / (n - k)
                out.append(("派生 expectancy_ex_open_end", round(real, 6)))
    return out


def render_md(digest: dict[str, Any]) -> str:
    """回填块（确定性渲染——守卫测试据此逐字比对）。"""
    rel = digest["path"]
    lines = [
        f"<!-- digest:{rel} -->",
        f"**跑数摘要** `{digest['tag']}`（kind={digest['kind']}，产物 `{digest['source_name']}` "
        f"sha256 `{digest['source_sha256'][:16]}`，digest v{digest['digest_version']}；"
        "数字只来自产物，勿手改）",
        "",
        "| 字段 | 值 |",
        "|---|---|",
    ]
    subs: list[tuple[str, list[dict[str, Any]]]] = []
    for k, v in sorted(digest["fields"].items()):  # 键序与入库 JSON（sort_keys）一致
        lines.append(f"| `{k}` | {_fmt(_split_tables(k, v, subs))} |")
    for k, v in _derived(digest):
        lines.append(f"| {k} | {_fmt(v)} |")
    if digest["missing"]:
        lines.append(f"| ⚠️ 缺失字段 | {', '.join(digest['missing'])} |")
    for name, rows in subs:
        cols = sorted(
            rows[0], key=lambda c: (c not in ("year", "date"), c)
        )  # 年/日期列置首
        lines += [
            "",
            f"子表 `{name}`：",
            "",
            "| " + " | ".join(cols) + " |",
            "|" + "---|" * len(cols),
        ]
        lines += ["| " + " | ".join(_fmt(r[c]) for c in cols) + " |" for r in rows]
    lines.append("<!-- /digest -->")
    return "\n".join(lines)


def build_digest(kind: str, product: Path, tag: str) -> dict[str, Any]:
    raw = product.read_bytes()
    report = json.loads(raw.decode("utf-8-sig"))
    fields, missing = extract(kind, report)
    unit = SPECS[kind][0]
    rel = f"results/{unit}/{tag}.json"
    return {
        "schema": "result_digest/v1",
        "digest_version": DIGEST_VERSION,
        "kind": kind,
        "unit": unit,
        "tag": tag,
        "path": rel,
        "source_name": product.name,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "fields": fields,
        "missing": missing,
    }


#: kind → 回填文档覆盖（体量大的描述性副读数不进单元正文，落 results/ 附录）
DOC_OVERRIDE: dict[str, str] = {
    "r44_dist": "results/R44_trade_dist.md",
    "r39_dist": "results/R39_trade_dist.md",
    "r40_dist": "results/R40_trade_dist.md",
    "label_dist": "results/R27_label_trade_dist.md",
    "r43_detail": "results/R43_detail.md",
    "r39_detail": "results/R39_detail.md",
    "r45_dist": "results/R45_trade_dist.md",
}


def _appendix_doc(rel: str, title: str) -> Path:
    doc = RESEARCH_DIR / rel
    if not doc.exists():
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            f"# {title}\n\n由 `result_digest --backfill` 写入，数字只来自产物；**描述性副读数，不是判据**，"
            "判读写在块外。\n\n### 回填区\n",
            encoding="utf-8",
        )
    return doc


def _unit_doc(unit: str) -> Optional[Path]:
    if not unit.startswith(
        "R"
    ):  # #91 探针 / #92 基线：集中回填到 results/baseline_v2.md
        doc = RESEARCH_DIR / "results" / "baseline_v2.md"
        if not doc.exists():
            doc.parent.mkdir(parents=True, exist_ok=True)
            doc.write_text(
                "# v2 基线与数据覆盖 · 跑数摘要（#91 / #92）\n\n由 `result_digest --backfill` 写入，"
                "数字只来自产物；判读写在块外。\n\n### 回填区\n",
                encoding="utf-8",
            )
        return doc
    hits = sorted(RESEARCH_DIR.glob(f"{unit}_*.md"))
    return hits[0] if hits else None


def backfill(doc: Path, md: str) -> None:
    """插到最后一个「回填区」标题之下（最新在上）；无该标题 ⇒ 追加到文末新建一节。"""
    s = doc.read_text(encoding="utf-8")
    marker = md.splitlines()[0]
    if marker in s:
        raise ValueError(f"{doc.name} 已有同一摘要块（{marker}）——同 tag 不重复回填")
    lines = s.split("\n")
    idx = [i for i, ln in enumerate(lines) if ln.startswith("#") and "回填区" in ln]
    if idx:
        i = idx[-1]
        lines[i + 1 : i + 1] = ["", md, ""]
        doc.write_text("\n".join(lines), encoding="utf-8")
    else:
        doc.write_text(
            s.rstrip("\n") + "\n\n### 回填区（跑数摘要）\n\n" + md + "\n",
            encoding="utf-8",
        )


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="跑数结果摘要（回填协议）：按预注册字段清单机械提取"
    )
    ap.add_argument("kind", choices=sorted(SPECS), help="产物种类（决定字段清单）")
    ap.add_argument("product", help="产物 JSON 路径")
    ap.add_argument(
        "--tag", required=True, help="摘要标签（= 跑数 tag；results/<单元>/<tag>.json）"
    )
    ap.add_argument(
        "--write", action="store_true", help="写 results/ 摘要 JSON（入库）"
    )
    ap.add_argument(
        "--backfill", action="store_true", help="同时把回填块插入研究单元回填区"
    )
    ap.add_argument(
        "--no-report",
        action="store_true",
        help="--write 时不同时生成可视化报告 <tag>_report.md（缺省生成，result_report 通用引擎）",
    )
    ap.add_argument(
        "--allow-missing",
        action="store_true",
        help="必需字段缺失也写（默认拒写，exit 2）",
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = _build_parser()
    args = ap.parse_args(argv)
    if args.backfill and not args.write:
        ap.error("--backfill 须同时 --write（回填块必须有入库的 results/ JSON 撑着）")
    d = build_digest(args.kind, Path(args.product), args.tag)
    md = render_md(d)
    print(md)
    if d["missing"] and not args.allow_missing:
        print(
            f"[FAIL] 必需字段缺失 {d['missing']}——产物不完整？（确认后 --allow-missing）",
            file=sys.stderr,
        )
        return 2
    if args.write:
        out = RESEARCH_DIR / d["path"]
        if out.exists():
            print(
                f"[FAIL] {out} 已存在——同 tag 摘要不覆盖（换 tag 或先核对）",
                file=sys.stderr,
            )
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            + "\n",
            encoding="utf-8",
        )
        print(f"[OK] 写出 {out}", file=sys.stderr)
        if not args.no_report:
            from custos.research import result_report as rr  # noqa: PLC0415

            print(f"[OK] 报告 {rr.write_report(out)}", file=sys.stderr)
    if args.backfill:
        doc = (
            _appendix_doc(
                DOC_OVERRIDE[d["kind"]], f"{d['unit']} 各档交易分布画像（{d['kind']}）"
            )
            if d["kind"] in DOC_OVERRIDE
            else _unit_doc(d["unit"])
        )
        if doc is None:
            print(f"[FAIL] 找不到单元 {d['unit']} 的文档", file=sys.stderr)
            return 2
        backfill(doc, md)
        print(f"[OK] 回填块插入 {doc.name}（状态/判读待复核后再改）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
