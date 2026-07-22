# -*- coding: utf-8 -*-
"""Screening 链第 4 段：渲染备选表格（candidate_table）。

读 ``01_data/stock_pool/{date}_stock_pool.json``，渲染
``03_daily_plans/_supporting/{date}/{date}_candidate_table.md``，
按 bucket 分组，供日报证据层引用。stock_pool 缺失时输出降级说明，
绝不报错、绝不阻塞主链。

CLI::

    uv run python 07_tools/screening/candidate_table.py --date YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import PLANS, STOCK_POOL_DIR  # noqa: E402

PATTERN_LABELS = {
    "bbi_above": "BBI上",
    "j_low": "低J",
    "volume_contraction": "缩量",
    "reversal_k_candidate": "反转K",
    "relative_strength_strong": "强RS",
}

WAVE_LABELS = {"buildup": "建仓", "rally": "拉升", "sprint": "冲刺"}


def _cz_tags(c: dict) -> str:
    """CZ 标签紧凑拼接：五日/龙头量/底部巨量/撤退。"""
    tags = []
    if (c.get("five_day_entry") or {}).get("hit"):
        tags.append("五日")
    if (c.get("leader_volume") or {}).get("hit"):
        tags.append("龙头量")
    if (c.get("bottom_volume") or {}).get("hit"):
        tags.append("底部巨量")
    if (c.get("volume_sustain") or {}).get("status") == "retreat":
        tags.append("撤退")
    return "、".join(tags) or "-"


def _fmt(v: Any, suffix: str = "") -> str:
    if v is None:
        return "-"
    return f"{v}{suffix}"


def render_table(pool: dict, date: str) -> str:
    lines: list[str] = [
        f"# 公式选股备选池｜{date}",
        "",
        f"> 选股链状态：{pool.get('status', '未知')}"
        + (f"（{pool['degraded_reason']}）" if pool.get("degraded_reason") else "")
        + f"；0AMV：{pool.get('amv_state', '未知')}；市场许可：{pool.get('market_permission', '未知')}",
        "> 本表为证据层候选，不构成买入计划；A/B 池亦须经总控与风控审批。",
        "",
    ]
    counts = pool.get("bucket_counts") or {}
    candidates = pool.get("candidates") or []
    for bucket in ("A", "B", "C", "D"):
        rows = [c for c in candidates if c.get("bucket") == bucket]
        lines.append(f"## {bucket} 池（{counts.get(bucket, 0)} 只）")
        lines.append("")
        if not rows:
            lines.append("（空）")
            lines.append("")
            continue
        lines.append(
            "| 代码 | 名称 | 公式命中 | 模式标签 | 波浪 | CZ标签 | 技术分 | 板块 | 板块状态 | 共振 | 分层 | 建议止损位 | next_step |"
        )
        lines.append("|---|---|---|---|---|---|---:|---|---|---|---|---|---|")
        for c in rows:
            tags = "、".join(
                PATTERN_LABELS[t] for t, hit in (c.get("patterns") or {}).items() if hit
            ) or "-"
            wave = WAVE_LABELS.get((c.get("wave") or {}).get("wave_type"), "-")
            shf = c.get("sector_heat_filter") or {}
            res = c.get("resonance") or {}
            detail = c.get("score_detail") or {}
            stop = (c.get("stop_loss_ref") or {}).get("price")
            lines.append(
                f"| {c.get('code')} | {c.get('name')}"
                f" | {'、'.join(c.get('formula_hits') or []) or '-'}"
                f" | {tags}"
                f" | {wave}"
                f" | {_cz_tags(c)}"
                f" | {_fmt(detail.get('technical_score'))}"
                f" | {c.get('sector', '未知')}"
                f" | {shf.get('sector_state', '未知')}"
                f" | {res.get('resonance_level', '-')}"
                f" | {bucket}"
                f" | {_fmt(stop)}"
                f" | {c.get('next_step', '-')} |"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="screening 链第 4 段：渲染备选表格（证据层）")
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    pool_path = STOCK_POOL_DIR / f"{args.date}_stock_pool.json"
    try:
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pool = None

    if pool is None:
        text = (
            f"# 公式选股备选池｜{args.date}\n\n"
            "> 当日未运行选股链（stock_pool.json 缺失或不可解析）。\n"
        )
        status = "missing_pool"
    else:
        text = render_table(pool, args.date)
        status = pool.get("status", "ok")

    out_dir = PLANS / "_supporting" / args.date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.date}_candidate_table.md"
    out_path.write_text(text, encoding="utf-8")

    print(json.dumps({"date": args.date, "status": status, "output": str(out_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
