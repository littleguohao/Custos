# -*- coding: utf-8 -*-
"""09:05 one-shot premarket report pipeline.

stdout is a machine-consumed protocol (see the summary lines below) and is
kept byte-compatible; observability goes to 06_logs/{date}_0905_run_log.json
instead — every run (completed / closed / calendar_failed / failed) leaves
one behind.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from datetime import date

from paths import BASE
from pipeline_kit import check_trading_day, log_stage, md_to_digest, now_iso, run_stage, warn, write_run_log

TOOLS = BASE / "07_tools"
PLANS = BASE / "03_daily_plans"
LOG_DIR = BASE / "06_logs"

# Module-level aliases kept for tests and readability; implementation lives in pipeline_kit.
_now_iso = now_iso
_log_stage = log_stage


def _write_run_log(target: str, status: str, started_at: str, t0: float, stages: list[dict]):
    return write_run_log(LOG_DIR, "0905", target, status, started_at, t0, stages)


def _stage(cmd: list[str], name: str) -> dict:
    """Run a stage quietly: runner stdout is a machine-consumed protocol, so
    the stage echo ([RUN] header, subprocess output) is suppressed; only the
    summary lines below are printed."""
    with contextlib.redirect_stdout(io.StringIO()):
        r = run_stage(cmd, name, required=False)
    r["out"] = (r["stdout"] + r["stderr"]).strip()
    return r


def _check_0850_status(target: str) -> tuple[bool, str]:
    """Decide whether the 09:05 pipeline may reuse 08:50 discovery artifacts.

    Returns (reuse_discovery, note): reuse only when 06_logs/{date}_0850_run_log.json
    exists with status == "completed". Otherwise fall back to full collection;
    the note explains why (recorded in the run log, warned on stderr — stdout
    protocol unchanged).
    """
    path = LOG_DIR / f"{target}_0850_run_log.json"
    if not path.exists():
        return False, "0850_log_missing, fallback to full collection"
    try:
        status = json.loads(path.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError):
        return False, "0850_log_unreadable, fallback to full collection"
    if status == "completed":
        return True, ""
    return False, f"0850_status={status}, fallback to full collection"


def _daily_pipeline_cmd(target: str, reuse_discovery: bool) -> list[str]:
    cmd = ["uv", "run", "python", str(TOOLS / "daily_pipeline.py"), "--date", target,
           "--session-type", "premarket"]
    if reuse_discovery:
        cmd.append("--reuse-discovery")
    return cmd


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
        print(f"【盘前日报失败｜{target}】日历检查失败：{str(e)[:200]}")
        return 1
    stages_log.append(_log_stage("calendar", {"ok": True, "returncode": 0, "timeout": False,
                                              "stdout": cal_buf.getvalue()},
                                 c_started, _now_iso(), time.time() - c_t0,
                                 note=f"is_trading_day={cal.get('is_trading_day')}"))
    if not cal.get("is_trading_day", False):
        _write_run_log(target, "closed", run_started, t0, stages_log)
        print(f"今日休市，盘前日报不生成（{target}）")
        return 0

    # 2. Daily pipeline (premarket; reuse 08:50 discovery only when it completed)
    s_started = _now_iso()
    s_t0 = time.time()
    reuse_discovery, fallback_note = _check_0850_status(target)
    if fallback_note:
        warn(fallback_note)
    r = _stage(_daily_pipeline_cmd(target, reuse_discovery), "daily_pipeline premarket")
    stages_log.append(_log_stage("daily_pipeline premarket", r, s_started, _now_iso(), time.time() - s_t0,
                                 note=fallback_note))
    if not r["ok"]:
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【盘前日报失败｜{target}】daily_pipeline失败：{r['out'][:500]}")
        return 1

    # 3. Read generated report and convert to text digest
    d_started = _now_iso()
    d_t0 = time.time()
    report_path = PLANS / f"{target}_daily_report.md"
    if not report_path.exists():
        stages_log.append(_log_stage("report_digest", {"ok": False, "returncode": None, "timeout": False},
                                     d_started, _now_iso(), time.time() - d_t0,
                                     note=f"报告文件未生成：{report_path}"))
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【盘前日报失败｜{target}】报告文件未生成：{report_path}")
        return 1

    digest = md_to_digest(report_path.read_text(encoding="utf-8"))
    stages_log.append(_log_stage("report_digest", {"ok": True, "returncode": 0, "timeout": False},
                                 d_started, _now_iso(), time.time() - d_t0,
                                 note=f"report={report_path}；digest_chars={len(digest)}"))
    _write_run_log(target, "completed", run_started, t0, stages_log)

    print(f"【盘前日报｜{target}】")
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
