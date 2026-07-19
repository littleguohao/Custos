# -*- coding: utf-8 -*-
"""08:50 one-shot premarket data collection (except wenda_notice_query which needs LLM tool).

stdout is a machine-consumed protocol (see the summary lines below) and is
kept byte-compatible; observability goes to 06_logs/{date}_0850_run_log.json
instead — every run (completed / closed / calendar_failed) leaves one behind.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from datetime import date, datetime

from paths import BASE
from pipeline_kit import _extract_json, check_trading_day, run_stage

TOOLS = BASE / "07_tools"
LOG_DIR = BASE / "06_logs"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stage(cmd: list[str], name: str) -> dict:
    """Run a stage quietly: runner stdout is a machine-consumed protocol, so
    the stage echo ([RUN] header, subprocess output) is suppressed; only the
    summary lines below are printed."""
    with contextlib.redirect_stdout(io.StringIO()):
        r = run_stage(cmd, name, required=False)
    r["out"] = (r["stdout"] + r["stderr"]).strip()
    return r


def _log_stage(name: str, r: dict, started_at: str, finished_at: str, duration_sec: float,
               note: str = "") -> dict:
    entry = {
        "name": name,
        "ok": bool(r.get("ok", False)),
        "returncode": r.get("returncode"),
        "timeout": bool(r.get("timeout", False)),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": round(duration_sec, 2),
        "stdout_tail": (r.get("stdout") or "")[-1000:],
        "stderr_tail": (r.get("stderr") or "")[-1000:],
    }
    if note:
        entry["note"] = note
    return entry


def _write_run_log(target: str, status: str, started_at: str, t0: float, stages: list[dict]):
    log = {
        "date": target,
        "script": "run_0850",
        "status": status,
        "started_at": started_at,
        "finished_at": _now_iso(),
        "duration_sec": round(time.time() - t0, 2),
        "stages": stages,
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{target}_0850_run_log.json"
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _rss_summary_fragments(results: dict) -> list[str]:
    """Cheap quality dims for the summary line, parsed from stage stdout JSON
    (rss_collect prints {items, sources_ok, sources_failed, ...}; rss_filter
    prints the filter report with selected_count). Anything unparseable is
    silently skipped — the summary prefix contract is never at risk."""
    frags = []
    coll = _extract_json((results.get("rss_collect") or {}).get("stdout", ""))
    items, sok, sfail = coll.get("items"), coll.get("sources_ok"), coll.get("sources_failed")
    if isinstance(items, int) and isinstance(sok, int) and isinstance(sfail, int):
        frags.append(f"rss_items={items}({sok}/{sok + sfail})")
    report = _extract_json((results.get("rss_filter") or {}).get("stdout", ""))
    cand = report.get("selected_count")
    if isinstance(cand, int):
        frags.append(f"rss_candidates={cand}")
    return frags


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    target = args.date

    # Subprocesses rely on project discovery (uv run) from the repo root.
    os.chdir(BASE)

    run_started = _now_iso()
    t0 = time.time()
    stages_log: list[dict] = []

    # 1. Trading calendar
    c_started = _now_iso()
    c_t0 = time.time()
    cal_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(cal_buf):
            cal = check_trading_day(target)
    except RuntimeError as e:
        stages_log.append(_log_stage("calendar", {"ok": False, "returncode": None, "timeout": False,
                                                  "stdout": cal_buf.getvalue(), "stderr": str(e)},
                                     c_started, _now_iso(), time.time() - c_t0,
                                     note=str(e)[:500]))
        _write_run_log(target, "calendar_failed", run_started, t0, stages_log)
        print(f"【08:50预采集失败｜{target}】日历检查失败：{str(e)[:200]}")
        return 1
    stages_log.append(_log_stage("calendar", {"ok": True, "returncode": 0, "timeout": False,
                                              "stdout": cal_buf.getvalue()},
                                 c_started, _now_iso(), time.time() - c_t0,
                                 note=f"is_trading_day={cal.get('is_trading_day')}"))
    if not cal.get("is_trading_day", False):
        _write_run_log(target, "closed", run_started, t0, stages_log)
        print(f"今日休市，08:50预采集跳过（{target}）")
        return 0

    steps = ["calendar=ok"]

    # 2-6. Data collectors (best-effort: rc recorded into steps, never fatal)
    STAGES = [
        (["uv", "run", "python", str(TOOLS / "market_timing" / "market_timing_collector.py"), "--date", target], "market_timing"),
        (["uv", "run", "python", str(TOOLS / "market_timing" / "overseas_market_collector.py"), "--date", target], "overseas"),
        (["uv", "run", "python", str(TOOLS / "news" / "rss_collector.py"), "--date", target], "rss_collect"),
        (["uv", "run", "python", str(TOOLS / "collect_incremental_market.py"), "--date", target], "incremental"),
        (["uv", "run", "python", str(TOOLS / "news" / "rss_filter.py"), "--date", target, "--session-type", "premarket"], "rss_filter"),
    ]
    results: dict[str, dict] = {}
    for cmd, name in STAGES:
        s_started = _now_iso()
        s_t0 = time.time()
        r = _stage(cmd, name)
        results[name] = r
        stages_log.append(_log_stage(name, r, s_started, _now_iso(), time.time() - s_t0))
        steps.append(f"{name}={'ok' if r['ok'] else 'fail'}")

    _write_run_log(target, "completed", run_started, t0, stages_log)
    print(f"【08:50预采集完成｜{target}】{'；'.join(steps + _rss_summary_fragments(results))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
