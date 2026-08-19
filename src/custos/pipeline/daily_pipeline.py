# -*- coding: utf-8 -*-
r"""strategy_team daily pipeline v1.

Purpose:
Run the daily strategy-team workflow in a stable order.

Default behavior is conservative:
- Reuse existing market input JSON if present, to avoid overwriting manual 0AMV/macro/screener overlays.
- Reuse existing holding mapping if present, to avoid reintroducing manually cleared positions.
- Refresh overseas and scoring/report files.

Typical usage:
uv run python src/custos/pipeline/daily_pipeline.py --date YYYY-MM-DD

Refresh all automated market inputs:
... daily_pipeline.py --date YYYY-MM-DD --refresh-market

Refresh holdings from standardized current positions:
... daily_pipeline.py --date YYYY-MM-DD --refresh-holdings
"""

from __future__ import annotations

import argparse
import json
import sys
import shutil
from pathlib import Path

import sys


from custos.core.paths import (
    DATA,
    HOLDINGS,
    HOLDINGS_DIR,
    LOGS,
    MARKET_DIR,
    MARKET_TIMING,
    PLANS,
    TOOLS,
    daily_report_dir,
)
from custos.core.pipeline_kit import run_stage

PY = sys.executable
# ⚠️ 路径**一律从 paths 导入**，不在这里重拼。
#    2026-08-07 的教训：本地重定义的 `MARKET_TIMING` 在 holdings/ 拆分后成了死路径，
#    而 paths.py 是改过的 —— 本地副本让「唯一来源」形同虚设。


def apply_manual_market(
    date: str, macro: str | None, amv_zone: str | None, amv_pct: float | None
):
    path = MARKET_DIR / f"{date}_market_timing_input.json"
    if not path.exists():
        return {
            "stage": "apply_manual_market",
            "ok": False,
            "message": "market input missing",
        }
    d = json.loads(path.read_text(encoding="utf-8"))
    if macro == "double_wide":
        d.setdefault("macro_policy", {}).update(
            {
                "monetary_policy": "宽松",
                "fiscal_policy": "积极",
                "credit_environment": "稳定",
                "regulation_environment": "中性",
                "policy_summary": "人工输入：当前按双宽政策处理，货币宽松、财政积极。",
            }
        )
    if amv_zone or amv_pct is not None:
        d.setdefault("amv_0", {})["amv_change_pct"] = amv_pct
        d["amv_0"]["quality"] = "confirmed"
        d["amv_0"]["as_of"] = date
        d["amv_0"]["source"] = "user_manual_input"
        if amv_zone:
            d["amv_0"]["amv_zone"] = amv_zone
        elif amv_pct is not None:
            d["amv_0"]["amv_zone"] = (
                "做多" if amv_pct > 4 else ("空头" if amv_pct < -2.3 else "中性")
            )
    dq = d.setdefault("data_quality", {})
    dq.setdefault("sources", []).append("daily_pipeline_manual_args")
    dq.setdefault("notes", []).append(
        f"daily_pipeline manual args: macro={macro}, amv_zone={amv_zone}, amv_pct={amv_pct}"
    )
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"stage": "apply_manual_market", "ok": True, "path": str(path)}


def apply_manual_position_updates(date: str):
    """Remove manually cleared positions from enriched mapping and technical summary."""
    upd = HOLDINGS_DIR / f"{date}_manual_position_updates.json"
    if not upd.exists():
        return {
            "stage": "apply_manual_position_updates",
            "ok": True,
            "message": "no manual updates",
        }
    u = json.loads(upd.read_text(encoding="utf-8"))
    closed = {
        str(x.get("code")): x
        for x in u.get("updates", [])
        if x.get("action") == "已清仓"
    }
    changed = []
    for fname in [
        HOLDINGS_DIR / f"{date}_holding_sector_mapping_enriched.json",
        HOLDINGS_DIR / f"{date}_holding_technical_summary.json",
    ]:
        if not fname.exists():
            continue
        data = json.loads(fname.read_text(encoding="utf-8"))
        active = [x for x in data if str(x.get("code")) not in closed]
        removed = [x for x in data if str(x.get("code")) in closed]
        if removed:
            fname.write_text(
                json.dumps(active, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            archive = fname.with_name(fname.stem + f"_removed_by_pipeline_{date}.json")
            archive.write_text(
                json.dumps(removed, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            changed.append({"file": str(fname), "removed": list(closed)})
    return {"stage": "apply_manual_position_updates", "ok": True, "changed": changed}


def build_gate_cmd(
    date: str, session_type: str, strict_quality: bool = False
) -> list[str]:
    """运行门控命令。

    ⚠️ `--require-quality`(blocked 时 exit 4 → 整条盘后链硬失败)**默认关闭**:
    2026-07-30 曾对 postclose 默认打开,同时又收紧了 as_of 陈旧判定,两者叠加导致
    17:00 盘后复盘直接失败。硬闸须等新的 stale 校准跑过若干交易日、确认 blocked 只在真正
    大面积缺数时出现,再由 `--strict-quality-gate` 显式开启。
    门控结论无论是否开闸都会落盘到 data/quality/{date}_runtime_gate.json,并记进 stage note。
    """
    cmd = [
        str(PY),
        str(TOOLS / "core" / "runtime_gate.py"),
        "--date",
        date,
        "--require-trading-day",
        "--data-session",
        "preclose" if session_type == "premarket" else "postclose",
    ]
    if strict_quality and session_type == "postclose":
        cmd.append("--require-quality")
    return cmd


def gate_status_note(date: str) -> str:
    """把门控结论摘进 stage note——不阻断也要留痕,否则"数据大面积缺失却出了报告"事后无从察觉。"""
    path = DATA / "quality" / f"{date}_runtime_gate.json"
    try:
        g = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "gate_json_unreadable"
    mq = g.get("market_quality") or {}
    stale = [
        c.get("field") for c in (mq.get("checks") or []) if c.get("quality") == "stale"
    ]
    return (
        f"market_quality={mq.get('status')}(score={mq.get('quality_score')}"
        + (f", stale={','.join(x for x in stale if x)}" if stale else "")
        + ")"
        + f", position_gate={(g.get('position_gate') or {}).get('status')}"
    )


def _write_pipeline_log(date: str, stages: list[dict]) -> Path:
    """落盘 pipeline 运行日志。抽成函数是为了让**提前退出路径**(门控阻断)也能留痕。"""
    log = LOGS / f"{date}_daily_pipeline_log.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"date": date, "stages": stages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return log


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument(
        "--refresh-market",
        action="store_true",
        help="refresh market_timing_input from collectors",
    )
    ap.add_argument(
        "--refresh-holdings",
        action="store_true",
        help="refresh holding sector mapping from source workbook",
    )
    ap.add_argument("--macro", choices=["double_wide", "none"], default=None)
    ap.add_argument("--amv-zone", choices=["做多", "中性", "空头"], default=None)
    ap.add_argument("--amv-pct", type=float, default=None)
    ap.add_argument(
        "--reuse-discovery",
        action="store_true",
        help="reuse overseas/RSS files prepared before the formal report window",
    )
    ap.add_argument(
        "--session-type", choices=["premarket", "postclose"], default="premarket"
    )
    ap.add_argument(
        "--strict-quality-gate",
        action="store_true",
        help="postclose 时 market_quality=blocked 则整条链硬失败(exit 4)。"
        "默认关闭:门控只落盘+留痕,不阻断报告生成",
    )
    return ap.parse_args()


def _collect_market_stages(args: argparse.Namespace, stages: list[dict]) -> None:
    """1-3. 市场输入底座 → 人工市场参数 → 海外/RSS 发现采集。"""
    market_input = MARKET_DIR / f"{args.date}_market_timing_input.json"

    # 1. Market input base
    if args.refresh_market or not market_input.exists():
        cmd = [
            str(PY),
            str(MARKET_TIMING / "market_timing_collector.py"),
            "--date",
            args.date,
        ]
        if args.amv_pct is not None:
            cmd += ["--amv", str(args.amv_pct)]
        stages.append(run_stage(cmd, "market_timing_collector"))
    else:
        stages.append(
            {
                "stage": "market_timing_collector",
                "ok": True,
                "skipped": True,
                "reason": "existing input reused",
            }
        )

    # 2. Manual market inputs
    if args.macro or args.amv_zone or args.amv_pct is not None:
        stages.append(
            apply_manual_market(args.date, args.macro, args.amv_zone, args.amv_pct)
        )

    # 3. Overseas market and RSS discovery collectors. The 09:05 production
    # run reuses the 08:50 collection so network waits stay outside rendering.
    if args.reuse_discovery:
        stages.extend(
            [
                {
                    "stage": "overseas_market_collector",
                    "ok": True,
                    "skipped": True,
                    "reason": "08:50 discovery reused",
                },
                {
                    "stage": "rss_collector",
                    "ok": True,
                    "skipped": True,
                    "reason": "08:50 discovery reused",
                },
                {
                    "stage": "rss_filter",
                    "ok": True,
                    "skipped": True,
                    "reason": "08:50 discovery reused",
                },
            ]
        )
    else:
        stages.append(
            run_stage(
                [
                    str(PY),
                    str(TOOLS / "datasource" / "overseas_market_collector.py"),
                    "--date",
                    args.date,
                ],
                "overseas_market_collector",
                required=False,
            )
        )
        stages.append(
            run_stage(
                [
                    str(PY),
                    str(TOOLS / "datasource" / "news" / "rss_collector.py"),
                    "--date",
                    args.date,
                ],
                "rss_collector",
                required=False,
            )
        )
        stages.append(
            run_stage(
                [
                    str(PY),
                    str(TOOLS / "datasource" / "news" / "rss_filter.py"),
                    "--date",
                    args.date,
                    "--session-type",
                    args.session_type,
                ],
                "rss_filter",
                required=False,
            )
        )


def _run_gate_and_scorer(args: argparse.Namespace, stages: list[dict]) -> None:
    """4. amv_state → runtime_gate(可硬阻断) → market_timing_scorer。"""
    # 4. Resolve persistent 0AMV regime before scoring. A locked bearish
    # regime remains bearish until a confirmed daily change is > +4%.
    stages.append(
        run_stage(
            [str(PY), str(MARKET_TIMING / "amv_state.py"), "--date", args.date],
            "amv_state",
        )
    )
    # Runtime guards and market scorer consume the effective regime.
    # 门控结论一律落盘+记 note;是否**硬阻断**由 --strict-quality-gate 决定(默认不阻断,见 build_gate_cmd)。
    gate_stage = run_stage(
        build_gate_cmd(args.date, args.session_type, args.strict_quality_gate),
        "runtime_gate",
        required=False,
    )
    gate_stage["note"] = gate_status_note(args.date)
    stages.append(gate_stage)
    if not gate_stage["ok"]:
        # 门控退出码(3 非交易日 / 4 质量 blocked / 5 持仓 blocked)穿透到 OS 供 cron 消费,
        # 不得被包成无差别的 RuntimeError(exit 1)。
        # 退出前**先把 stage+note 落进 pipeline log**——日志只在函数末尾写,直接 raise 会让
        # 这次阻断连记录都不留(门控留痕的初衷就没了,事后只能翻 stdout)。
        _write_pipeline_log(args.date, stages)
        raise SystemExit(gate_stage["returncode"] or 1)
    stages.append(
        run_stage(
            [
                str(PY),
                str(MARKET_TIMING / "market_timing_scorer.py"),
                "--date",
                args.date,
            ],
            "market_timing_scorer",
        )
    )


def _collect_holdings_mapping(args: argparse.Namespace, stages: list[dict]) -> None:
    """5. 持仓映射刷新(可选)。"""
    # 5. Holdings mapping refresh optional
    enriched = HOLDINGS_DIR / f"{args.date}_holding_sector_mapping_enriched.json"
    if args.refresh_holdings or not enriched.exists():
        # First try local mapper. It may return empty sectors but still creates base mapping.
        stages.append(
            run_stage(
                [
                    str(PY),
                    str(HOLDINGS / "holding_sector_mapper.py"),
                    "--date",
                    args.date,
                ],
                "holding_sector_mapper",
                required=False,
            )
        )
        stages.append(
            {
                "stage": "holding_enrichment",
                "ok": True,
                "skipped": True,
                "reason": "enriched mapping optional; standardized current positions remain authoritative",
            }
        )
    else:
        stages.append(
            {
                "stage": "holding_sector_mapper",
                "ok": True,
                "skipped": True,
                "reason": "existing enriched mapping reused",
            }
        )


def _run_decision_chain(args: argparse.Namespace, stages: list[dict]) -> None:
    """6. 持仓与决策链:技术面 → B1 状态 → 持仓复盘 → 主题 → 风控/板块 → 总控。"""
    # 6. Holding and decision chain. batch_holding_technical falls back to
    # current_positions.json when an enriched mapping is unavailable, so a new
    # trade date must never skip the entire holding/risk/chief chain.
    stages.append(apply_manual_position_updates(args.date))
    stages.append(
        run_stage(
            [
                str(PY),
                str(HOLDINGS / "batch_holding_technical.py"),
                "--date",
                args.date,
            ],
            "batch_holding_technical",
        )
    )
    stages.append(
        run_stage(
            [str(PY), str(HOLDINGS / "b1_holding_state.py"), "--date", args.date],
            "b1_holding_state",
        )
    )
    stages.append(
        run_stage(
            [
                str(PY),
                str(HOLDINGS / "portfolio_review_report.py"),
                "--date",
                args.date,
            ],
            "portfolio_review_report",
        )
    )
    stages.append(
        run_stage(
            [
                str(PY),
                str(MARKET_TIMING / "theme_tracker_report.py"),
                "--date",
                args.date,
            ],
            "theme_tracker_report",
        )
    )
    # Generate risk_decision + sector_state from deterministic pipeline outputs
    stages.append(
        run_stage(
            [
                str(PY),
                str(TOOLS / "pipeline" / "generate_risk_and_sectors.py"),
                "--date",
                args.date,
            ],
            "generate_risk_and_sectors",
        )
    )

    stages.append(
        run_stage(
            [
                str(PY),
                str(MARKET_TIMING / "chief_decision_report.py"),
                "--date",
                args.date,
            ],
            "chief_decision_report",
        )
    )


def _run_session_stages(args: argparse.Namespace, stages: list[dict]) -> None:
    """session 专属 stage:premarket 快照 chief 决策;postclose 跑盘后新闻与执行复盘。"""
    if args.session_type == "premarket":
        chief_source = DATA / "decisions" / f"{args.date}_chief_decision.json"
        chief_snapshot = (
            DATA / "decisions" / f"{args.date}_premarket_chief_decision.json"
        )
        if chief_source.exists():
            shutil.copy2(chief_source, chief_snapshot)
            stages.append(
                {
                    "stage": "snapshot_premarket_chief_decision",
                    "ok": True,
                    "path": str(chief_snapshot),
                }
            )
        else:
            stages.append(
                {
                    "stage": "snapshot_premarket_chief_decision",
                    "ok": False,
                    "reason": "chief decision missing",
                }
            )
    if args.session_type == "postclose":
        stages.append(
            run_stage(
                [
                    str(PY),
                    str(TOOLS / "datasource" / "news" / "postclose_news_digest.py"),
                    "--date",
                    args.date,
                ],
                "postclose_news_digest",
                required=False,
            )
        )
        stages.append(
            run_stage(
                [
                    str(PY),
                    str(TOOLS / "pipeline" / "close_review" / "execution_review.py"),
                    "--date",
                    args.date,
                ],
                "execution_review",
            )
        )
        stages.append(
            run_stage(
                [
                    str(PY),
                    str(TOOLS / "pipeline" / "close_review" / "review_enrichment.py"),
                    "--date",
                    args.date,
                ],
                "review_enrichment",
            )
        )


def _run_report_stages(args: argparse.Namespace, stages: list[dict]) -> None:
    """7. 日报与微信摘要。"""
    stages.append(
        run_stage(
            [str(PY), str(TOOLS / "pipeline" / "daily_report.py"), "--date", args.date],
            "daily_report",
        )
    )
    stages.append(
        run_stage(
            [str(PY), str(MARKET_TIMING / "wechat_summary.py"), "--date", args.date],
            "wechat_summary",
            required=False,
        )
    )

    # 2026-08-12：archive_supporting_reports stage 已废——写方直接落
    # daily_report_dir（日期目录），没有「先写根再归档」的双套结构要收拾。


def _dedupe_data_quality(args: argparse.Namespace, stages: list[dict]) -> None:
    """去除重复 daily run 累积的 data_quality notes/sources。"""
    # De-duplicate repeated data_quality notes/sources produced by repeated daily runs.
    market_file = MARKET_DIR / f"{args.date}_market_timing_input.json"
    if market_file.exists():
        try:
            d = json.loads(market_file.read_text(encoding="utf-8"))
            dq = d.setdefault("data_quality", {})
            for key in ["notes", "sources"]:
                if isinstance(dq.get(key), list):
                    seen = []
                    for item in dq[key]:
                        if item not in seen:
                            seen.append(item)
                    dq[key] = seen
            market_file.write_text(
                json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            stages.append(
                {"stage": "dedupe_data_quality", "ok": False, "error": repr(e)}
            )


def main():
    args = _parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)
    stages: list[dict] = []

    _collect_market_stages(args, stages)
    _run_gate_and_scorer(args, stages)
    _collect_holdings_mapping(args, stages)
    _run_decision_chain(args, stages)
    _run_session_stages(args, stages)
    _run_report_stages(args, stages)
    _dedupe_data_quality(args, stages)

    log = _write_pipeline_log(args.date, stages)
    print(f"\n[DONE] daily pipeline log: {log}")
    print("\nOutputs:")
    # Files generated by build_skill_contracts.py are marked [contracts]
    for p in [
        MARKET_DIR / f"{args.date}_market_timing_input.json",
        daily_report_dir(args.date, PLANS) / f"{args.date}_market_timing_score.md",
        HOLDINGS_DIR / f"{args.date}_holding_technical_summary.json",
        daily_report_dir(args.date, PLANS) / f"{args.date}_portfolio_review.md",
        daily_report_dir(args.date, PLANS) / f"{args.date}_chief_decision.md",
        daily_report_dir(args.date, PLANS) / f"{args.date}_daily_report.md",
        DATA / "sectors" / f"{args.date}_sector_state.json",
        DATA / "risk" / f"{args.date}_risk_decision.json",
        daily_report_dir(args.date, PLANS) / f"{args.date}_wechat_summary.txt",
    ]:
        print(f"- {p} {'OK' if p.exists() else 'MISSING'}")


if __name__ == "__main__":
    main()
