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


def _mainline_fingerprint_section(candidates: list[dict]) -> list[str]:
    """🧭 当日主线指纹:候选池板块族密度榜(情境感知,**不做进场过滤**)。best-effort,数据缺失则整段跳过。"""
    codes = [str(c.get("code", "")) for c in candidates if c.get("code")]
    if not codes:
        return []
    try:
        import sector_mainstream as sm  # noqa: PLC0415,E402
        mpath = STOCK_POOL_DIR.parent / "market" / "sector_members.json"
        members = json.loads(mpath.read_text(encoding="utf-8"))
        code2secs = sm.invert_members(members, exclude_types=True)
        fp = sm.mainline_fingerprint(codes, code2secs, sizes=sm.sector_sizes(members), top_k=8)
    except Exception:  # noqa: BLE001
        return []
    top = fp.get("top") or []
    if not top:
        return []
    out = ["## 🧭 当日主线指纹（候选池板块族密度榜）", "",
           f"> 当日候选 {fp['n']} 只（有板块 {fp['n_classified']}）；前5板块占归属 "
           f"{(fp.get('top5_count_share') or 0) * 100:.0f}%。**密度榜=情境感知**（看清当前主线在哪、"
           f"共振候选是否踩在主线上），**非进场过滤**——回测已证「跟随主流」非机械 edge，仅辅助主观研判。", "",
           "| 板块 | 候选数 | 板块规模 | 密度(候选/规模) | 占归属 |",
           "|---|---:|---:|---:|---:|"]
    for r in top:
        out.append(f"| {r['name']} | {r['n']} | {r.get('size') or '-'} "
                   f"| {_fmt(r.get('density'))} | {_fmt(round((r.get('share') or 0) * 100, 1), '%')} |")
    out.append("")
    return out


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
    # 🧭 当日主线指纹:候选池板块族密度榜(情境感知,非进场 gate)——置于最前,先看当前主线全貌
    lines += _mainline_fingerprint_section(candidates)
    # 🐂 基本面牛股候选(共振观察区)：基本面优 + 板块有利 + 技术强(市场做多时即可买🐂)。单独列出持续观察。
    # 分层受限(D/C等)的共振标的不是"可买",而是**重点研究观察对象**——单列 🔍 区,不被分层埋没。
    watch_all = [c for c in candidates
                 if (c.get("fundamental_quality") or {}).get("tier") == "优"
                 and (c.get("resonance_4leg") or {}).get("sector")
                 and (c.get("resonance_4leg") or {}).get("technical")]
    _watch_key = lambda c: ((c.get("resonance_4leg") or {}).get("aligned", 0),
                            (c.get("score_detail") or {}).get("total") or 0)
    watch = sorted((c for c in watch_all if c.get("bucket") in ("A", "B")), key=_watch_key, reverse=True)
    watch_capped = sorted((c for c in watch_all if c.get("bucket") not in ("A", "B")),
                          key=_watch_key, reverse=True)
    lines.append("## 🐂 基本面牛股候选（共振观察区）")
    lines.append("")
    lines.append("> 基本面优 + 板块相位有利 + 技术强 = 三面已共振；再叠 0AMV做多即为可买牛股候选（🐂）。单独列出供持续观察（基本面为当前快照、非回测验证，仅辅助）。")
    lines.append("")
    if not watch:
        lines.append("（今日无基本面牛股候选）")
        lines.append("")
    else:
        lines.append("| 代码 | 名称 | 板块 | 基本面 | 4面共振 | 技术分 | 资金意图 | 分层 | 建议止损位 | 标记 |")
        lines.append("|---|---|---|---|---|---:|---|---|---:|---|")
        for c in watch:
            r4 = c.get("resonance_4leg") or {}
            if not r4.get("bull_candidate"):
                mark = "待0AMV做多"
            elif c.get("bucket") == "A":
                mark = "🐂可买"
            else:
                mark = "🐂观察价位(B)"               # 四腿命中但分层 B:next_step=观察价位,非直接可买
            lines.append(
                f"| {c.get('code')} | {c.get('name')}"
                f" | {c.get('sector', '未知')}"
                f" | {(c.get('fundamental_quality') or {}).get('tier', '-')}"
                f" | {r4.get('label', '-')}"
                f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))}"
                f" | {(c.get('capital_intent') or {}).get('level', '-')}"
                f" | {c.get('bucket', '-')}"
                f" | {_fmt((c.get('stop_loss_ref') or {}).get('price'))}"
                f" | {mark} |"
            )
        lines.append("")
    if watch_capped:
        lines.append("## 🔍 共振成立但分层受限（重点研究观察·非可买）")
        lines.append("")
        lines.append("> 以下标的同样三面/四面共振成立，但被风控降档/硬封（分层 C=长期跟踪 / D=回避）——**不是可买信号，是重点研究观察对象**："
                     "若研究确认受限因素解除或误判，是潜在的最强候选。持续跟踪，不进入买入计划。")
        lines.append("")
        lines.append("| 代码 | 名称 | 板块 | 基本面 | 4面共振 | 技术分 | 分层 | 受限因素 | 建议止损位 |")
        lines.append("|---|---|---|---|---|---:|---|---|---:|")
        for c in watch_capped:
            r4 = c.get("resonance_4leg") or {}
            flags = "、".join(c.get("risk_flags") or []) or "-"
            lines.append(
                f"| {c.get('code')} | {c.get('name')}"
                f" | {c.get('sector', '未知')}"
                f" | {(c.get('fundamental_quality') or {}).get('tier', '-')}"
                f" | {r4.get('label', '-')}"
                f" | {_fmt((c.get('score_detail') or {}).get('technical_score'))}"
                f" | {c.get('bucket', '-')}"
                f" | {flags}"
                f" | {_fmt((c.get('stop_loss_ref') or {}).get('price'))} |"
            )
        lines.append("")
    # 得分 Top5：按总分降序（跨分层），供快速浏览当日最强候选
    top5 = sorted(candidates,
                  key=lambda c: ((c.get("score_detail") or {}).get("total") or 0),
                  reverse=True)[:5]
    if top5:
        lines.append("## 得分 Top 5")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 总分 | 技术分 | S** | 建议 | 分层 | 公式命中 | 风险标记 |")
        lines.append("|---:|---|---|---:|---:|---:|---|---|---|---|")
        for i, c in enumerate(top5, 1):
            detail = c.get("score_detail") or {}
            lines.append(
                f"| {i} | {c.get('code')} | {c.get('name')}"
                f" | {_fmt(detail.get('total'))}"
                f" | {_fmt(detail.get('technical_score'))}"
                f" | {_fmt(c.get('s_star'))}"
                f" | {c.get('suggestion') or '-'}"
                f" | {c.get('bucket', '-')}"
                f" | {'、'.join(c.get('formula_hits') or []) or '-'}"
                f" | {'、'.join(c.get('risk_flags') or []) or '-'} |"
            )
        lines.append("")
    for bucket in ("A", "B", "C", "D"):
        rows = [c for c in candidates if c.get("bucket") == bucket]
        lines.append(f"## {bucket} 池（{counts.get(bucket, 0)} 只）")
        lines.append("")
        if not rows:
            lines.append("（空）")
            lines.append("")
            continue
        lines.append(
            "| 代码 | 名称 | 公式命中 | 模式标签 | 波浪 | CZ标签 | 技术分 | 贴合 | 资金意图 | 板块 | 板块状态 | 交易属性 | 共振 | 基本面 | 4面共振 | 分层 | 建议止损位 | next_step |"
        )
        lines.append("|---|---|---|---|---|---|---:|---:|---|---|---|---|---|---|---|---|---|---|")
        for c in rows:
            tags = "、".join(
                PATTERN_LABELS[t] for t, hit in (c.get("patterns") or {}).items() if hit
            ) or "-"
            wave = WAVE_LABELS.get((c.get("wave") or {}).get("wave_type"), "-")
            shf = c.get("sector_heat_filter") or {}
            res = c.get("resonance") or {}
            detail = c.get("score_detail") or {}
            stop = (c.get("stop_loss_ref") or {}).get("price")
            fit = (c.get("score_detail") or {}).get("factor_contrib", {}).get("perfect_b1_fit")
            cap_intent = (c.get("capital_intent") or {}).get("level", "-")
            fq = c.get("fundamental_quality") or {}
            fq_disp = (fq.get("tier", "-") or "-") + ("⚠三无" if fq.get("sanwu") else "")
            r4 = c.get("resonance_4leg") or {}
            r4_disp = (r4.get("label", "-") or "-") + ("🐂" if r4.get("bull_candidate") else "")
            lines.append(
                f"| {c.get('code')} | {c.get('name')}"
                f" | {'、'.join(c.get('formula_hits') or []) or '-'}"
                f" | {tags}"
                f" | {wave}"
                f" | {_cz_tags(c)}"
                f" | {_fmt(detail.get('technical_score'))}"
                f" | {_fmt(fit)}"
                f" | {cap_intent}"
                f" | {c.get('sector', '未知')}"
                f" | {shf.get('sector_state', '未知')}"
                f" | {c.get('trade_style', '-')}"
                f" | {res.get('resonance_level', '-')}"
                f" | {fq_disp}"
                f" | {r4_disp}"
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
