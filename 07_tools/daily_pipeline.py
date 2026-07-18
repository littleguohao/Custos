# -*- coding: utf-8 -*-
r"""strategy_team daily pipeline v1.

Purpose:
Run the daily strategy-team workflow in a stable order.

Default behavior is conservative:
- Reuse existing market input JSON if present, to avoid overwriting manual 0AMV/macro/screener overlays.
- Reuse existing holding mapping if present, to avoid reintroducing manually cleared positions.
- Refresh overseas and scoring/report files.

Typical usage:
uv run python strategy_team/07_tools/daily_pipeline.py --date YYYY-MM-DD

Refresh all automated market inputs:
... daily_pipeline.py --date YYYY-MM-DD --refresh-market

Refresh holdings from standardized current positions:
... daily_pipeline.py --date YYYY-MM-DD --refresh-holdings
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import shutil
from pathlib import Path

from paths import BASE

PY = sys.executable
TOOLS = BASE / "07_tools" / "market_timing"
DATA_DIR = BASE / "01_data"
MARKET_DIR = DATA_DIR / "market"
HOLD_DIR = BASE / "01_data" / "holdings"
PLAN_DIR = BASE / "03_daily_plans"
SUPPORT_DIR = PLAN_DIR / "_supporting"
LOG_DIR = BASE / "06_logs"


def run(cmd: list[str], name: str, required: bool = True) -> dict:
    print(f"\n[RUN] {name}")
    print(" ".join(cmd))
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if p.stdout:
        print(p.stdout.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
    if p.stderr:
        print("[stderr]", p.stderr.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
    ok = p.returncode == 0
    if required and not ok:
        raise RuntimeError(f"stage failed: {name}, code={p.returncode}")
    return {"stage": name, "ok": ok, "returncode": p.returncode, "stdout": p.stdout[-4000:], "stderr": p.stderr[-4000:]}


def apply_manual_market(date: str, macro: str | None, amv_zone: str | None, amv_pct: float | None):
    path = MARKET_DIR / f"{date}_market_timing_input.json"
    if not path.exists():
        return {"stage": "apply_manual_market", "ok": False, "message": "market input missing"}
    d = json.loads(path.read_text(encoding="utf-8"))
    if macro == "double_wide":
        d.setdefault("macro_policy", {}).update({
            "monetary_policy": "宽松",
            "fiscal_policy": "积极",
            "credit_environment": "稳定",
            "regulation_environment": "中性",
            "policy_summary": "人工输入：当前按双宽政策处理，货币宽松、财政积极。"
        })
    if amv_zone or amv_pct is not None:
        d.setdefault("amv_0", {})["amv_change_pct"] = amv_pct
        d["amv_0"]["quality"] = "confirmed"
        d["amv_0"]["as_of"] = date
        d["amv_0"]["source"] = "user_manual_input"
        if amv_zone:
            d["amv_0"]["amv_zone"] = amv_zone
        elif amv_pct is not None:
            d["amv_0"]["amv_zone"] = "做多" if amv_pct > 4 else ("空头" if amv_pct < -2.3 else "中性")
    dq = d.setdefault("data_quality", {})
    dq.setdefault("sources", []).append("daily_pipeline_manual_args")
    dq.setdefault("notes", []).append(f"daily_pipeline manual args: macro={macro}, amv_zone={amv_zone}, amv_pct={amv_pct}")
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"stage": "apply_manual_market", "ok": True, "path": str(path)}


def apply_manual_position_updates(date: str):
    """Remove manually cleared positions from enriched mapping and technical summary."""
    upd = HOLD_DIR / f"{date}_manual_position_updates.json"
    if not upd.exists():
        return {"stage": "apply_manual_position_updates", "ok": True, "message": "no manual updates"}
    u = json.loads(upd.read_text(encoding="utf-8"))
    closed = {str(x.get("code")): x for x in u.get("updates", []) if x.get("action") == "已清仓"}
    changed = []
    for fname in [
        HOLD_DIR / f"{date}_holding_sector_mapping_enriched.json",
        HOLD_DIR / f"{date}_holding_technical_summary.json",
    ]:
        if not fname.exists():
            continue
        data = json.loads(fname.read_text(encoding="utf-8"))
        active = [x for x in data if str(x.get("code")) not in closed]
        removed = [x for x in data if str(x.get("code")) in closed]
        if removed:
            fname.write_text(json.dumps(active, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            archive = fname.with_name(fname.stem + f"_removed_by_pipeline_{date}.json")
            archive.write_text(json.dumps(removed, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            changed.append({"file": str(fname), "removed": list(closed)})
    return {"stage": "apply_manual_position_updates", "ok": True, "changed": changed}


def archive_supporting_reports(date: str) -> dict:
    """Keep one formal daily report in the plan root; archive stage reports."""
    target = SUPPORT_DIR / date
    target.mkdir(parents=True, exist_ok=True)
    names = [
        f"{date}_market_timing_score.md",
        f"{date}_portfolio_review.md",
        f"{date}_theme_tracker.md",
        f"{date}_chief_decision.md",
        f"{date}_wechat_summary.txt",
    ]
    moved = []
    for name in names:
        source = PLAN_DIR / name
        if not source.exists():
            continue
        destination = target / name
        source.replace(destination)
        moved.append(str(destination))
    return {"stage": "archive_supporting_reports", "ok": True, "moved": moved}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--refresh-market", action="store_true", help="refresh market_timing_input from collectors")
    ap.add_argument("--refresh-holdings", action="store_true", help="refresh holding sector mapping from source workbook")
    ap.add_argument("--macro", choices=["double_wide", "none"], default=None)
    ap.add_argument("--amv-zone", choices=["做多", "中性", "空头"], default=None)
    ap.add_argument("--amv-pct", type=float, default=None)
    ap.add_argument("--reuse-discovery", action="store_true", help="reuse overseas/RSS files prepared before the formal report window")
    ap.add_argument("--session-type", choices=["premarket", "intraday_1445", "postclose"], default="premarket")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stages = []
    market_input = MARKET_DIR / f"{args.date}_market_timing_input.json"

    # 1. Market input base
    if args.refresh_market or not market_input.exists():
        cmd = [str(PY), str(TOOLS / "market_timing_collector.py"), "--date", args.date]
        if args.amv_pct is not None:
            cmd += ["--amv", str(args.amv_pct)]
        stages.append(run(cmd, "market_timing_collector"))
    else:
        stages.append({"stage": "market_timing_collector", "ok": True, "skipped": True, "reason": "existing input reused"})

    # 2. Manual market inputs
    if args.macro or args.amv_zone or args.amv_pct is not None:
        stages.append(apply_manual_market(args.date, args.macro, args.amv_zone, args.amv_pct))

    # 3. Overseas market and RSS discovery collectors. The 09:05 production
    # run reuses the 08:50 collection so network waits stay outside rendering.
    if args.session_type == "intraday_1445":
        stages.extend([
            {"stage": "overseas_market_collector", "ok": True, "skipped": True, "reason": "intraday reports do not consume news discovery"},
            {"stage": "rss_collector", "ok": True, "skipped": True, "reason": "intraday reports do not consume news discovery"},
            {"stage": "rss_filter", "ok": True, "skipped": True, "reason": "intraday reports do not consume news discovery"},
        ])
    elif args.reuse_discovery:
        stages.extend([
            {"stage": "overseas_market_collector", "ok": True, "skipped": True, "reason": "08:50 discovery reused"},
            {"stage": "rss_collector", "ok": True, "skipped": True, "reason": "08:50 discovery reused"},
            {"stage": "rss_filter", "ok": True, "skipped": True, "reason": "08:50 discovery reused"},
        ])
    else:
        stages.append(run([str(PY), str(TOOLS / "overseas_market_collector.py"), "--date", args.date], "overseas_market_collector", required=False))
        stages.append(run([str(PY), str(BASE / "07_tools" / "news" / "rss_collector.py"), "--date", args.date], "rss_collector", required=False))
        stages.append(run([str(PY), str(BASE / "07_tools" / "news" / "rss_filter.py"), "--date", args.date,
                           "--session-type", args.session_type], "rss_filter", required=False))

    # 4. Resolve persistent 0AMV regime before scoring. A locked bearish
    # regime remains bearish until a confirmed daily change is > +4%.
    stages.append(run([str(PY), str(TOOLS / "amv_state.py"), "--date", args.date], "amv_state"))
    # Runtime guards and market scorer consume the effective regime.
    stages.append(run([str(PY), str(BASE / "07_tools" / "runtime_gate.py"), "--date", args.date, "--require-trading-day"], "runtime_gate"))
    stages.append(run([str(PY), str(TOOLS / "market_timing_scorer.py"), "--date", args.date], "market_timing_scorer"))

    # 5. Holdings mapping refresh optional
    enriched = HOLD_DIR / f"{args.date}_holding_sector_mapping_enriched.json"
    if args.refresh_holdings or not enriched.exists():
        # First try local mapper. It may return empty sectors but still creates base mapping.
        stages.append(run([str(PY), str(TOOLS / "holding_sector_mapper.py"), "--date", args.date], "holding_sector_mapper", required=False))
        stages.append({"stage": "holding_enrichment", "ok": True, "skipped": True, "reason": "enriched mapping optional; standardized current positions remain authoritative"})
    else:
        stages.append({"stage": "holding_sector_mapper", "ok": True, "skipped": True, "reason": "existing enriched mapping reused"})

    # 6. Holding and decision chain. batch_holding_technical falls back to
    # current_positions.json when an enriched mapping is unavailable, so a new
    # trade date must never skip the entire holding/risk/chief chain.
    stages.append(apply_manual_position_updates(args.date))
    stages.append(run([str(PY), str(TOOLS / "batch_holding_technical.py"), "--date", args.date], "batch_holding_technical"))
    stages.append(run([str(PY), str(TOOLS / "b1_holding_state.py"), "--date", args.date], "b1_holding_state"))
    stages.append(run([str(PY), str(TOOLS / "portfolio_review_report.py"), "--date", args.date], "portfolio_review_report"))
    stages.append(run([str(PY), str(TOOLS / "theme_tracker_report.py"), "--date", args.date], "theme_tracker_report"))
    # Generate risk_decision + sector_state from deterministic pipeline outputs
    stages.append(run([str(PY), str(BASE / "07_tools" / "generate_risk_and_sectors.py"), "--date", args.date], "generate_risk_and_sectors"))

    stages.append(run([str(PY), str(TOOLS / "chief_decision_report.py"), "--date", args.date], "chief_decision_report"))
    if args.session_type == "premarket":
        chief_source = DATA_DIR / "decisions" / f"{args.date}_chief_decision.json"
        chief_snapshot = DATA_DIR / "decisions" / f"{args.date}_premarket_chief_decision.json"
        if chief_source.exists():
            shutil.copy2(chief_source, chief_snapshot)
            stages.append({"stage": "snapshot_premarket_chief_decision", "ok": True, "path": str(chief_snapshot)})
        else:
            stages.append({"stage": "snapshot_premarket_chief_decision", "ok": False, "reason": "chief decision missing"})
    if args.session_type == "postclose":
        stages.append(run([str(PY), str(BASE / "07_tools" / "news" / "postclose_news_digest.py"), "--date", args.date], "postclose_news_digest", required=False))
        stages.append(run([str(PY), str(BASE / "07_tools" / "close_review" / "execution_review.py"), "--date", args.date], "execution_review"))
        stages.append(run([str(PY), str(BASE / "07_tools" / "close_review" / "review_enrichment.py"), "--date", args.date], "review_enrichment"))
    stages.append(run([str(PY), str(BASE / "07_tools" / "daily_report.py"), "--date", args.date], "daily_report"))
    stages.append(run([str(PY), str(TOOLS / "wechat_summary.py"), "--date", args.date], "wechat_summary", required=False))

    stages.append(archive_supporting_reports(args.date))

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
            market_file.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            stages.append({"stage": "dedupe_data_quality", "ok": False, "error": repr(e)})

    log = LOG_DIR / f"{args.date}_daily_pipeline_log.json"
    log.write_text(json.dumps({"date": args.date, "stages": stages}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[DONE] daily pipeline log: {log}")
    print("\nOutputs:")
    # Files generated by build_skill_contracts.py are marked [contracts]
    for p in [
        MARKET_DIR / f"{args.date}_market_timing_input.json",
        SUPPORT_DIR / args.date / f"{args.date}_market_timing_score.md",
        HOLD_DIR / f"{args.date}_holding_technical_summary.json",
        SUPPORT_DIR / args.date / f"{args.date}_portfolio_review.md",
        SUPPORT_DIR / args.date / f"{args.date}_chief_decision.md",
        PLAN_DIR / f"{args.date}_daily_report.md",
        DATA_DIR / "sectors" / f"{args.date}_sector_state.json",
        DATA_DIR / "risk" / f"{args.date}_risk_decision.json",
        DATA_DIR / "buy_strategy" / f"{args.date}_buy_plan_normalized.json",  # deprecated, not generated in pure-script mode
        SUPPORT_DIR / args.date / f"{args.date}_wechat_summary.txt",
    ]:
        print(f"- {p} {'OK' if p.exists() else 'MISSING'}")


if __name__ == "__main__":
    main()
