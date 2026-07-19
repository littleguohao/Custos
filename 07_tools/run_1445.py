# -*- coding: utf-8 -*-
"""One-shot 14:45 pipeline: calendar check -> runtime gate -> close review -> digest.

stdout is a machine-consumed protocol (see the summary lines below) and is
kept byte-compatible; observability goes to 06_logs/{date}_1445_run_log.json
instead — every run (completed / closed / calendar_failed / failed) leaves
one behind.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import time
from datetime import date

from paths import BASE  # strategy_team/
from pipeline_kit import check_trading_day, log_stage, now_iso, run_stage, write_run_log

TOOLS = BASE / "07_tools"
LOG_DIR = BASE / "06_logs"

# Module-level aliases kept for tests and readability; implementation lives in pipeline_kit.
_now_iso = now_iso
_log_stage = log_stage


def _write_run_log(target: str, status: str, started_at: str, t0: float, stages: list[dict]):
    return write_run_log(LOG_DIR, "1445", target, status, started_at, t0, stages)


def _stage(cmd: list[str], name: str) -> dict:
    """Run a stage quietly: runner stdout is a machine-consumed protocol, so
    the stage echo ([RUN] header, subprocess output) is suppressed; only the
    summary lines below are printed."""
    with contextlib.redirect_stdout(io.StringIO()):
        r = run_stage(cmd, name, required=False)
    r["out"] = (r["stdout"] + r["stderr"]).strip()
    return r


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

    # 1. Trading calendar check
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
        print(f"【14:45尾盘报告失败｜{target}】交易日历检查失败：{str(e)[:200]}")
        return 1
    stages_log.append(_log_stage("calendar", {"ok": True, "returncode": 0, "timeout": False,
                                              "stdout": cal_buf.getvalue()},
                                 c_started, _now_iso(), time.time() - c_t0,
                                 note=f"is_trading_day={cal.get('is_trading_day')}"))
    if not cal.get("is_trading_day", False):
        _write_run_log(target, "closed", run_started, t0, stages_log)
        print(f"今日确认休市，14:45报告不生成（{target}）")
        return 0

    # 2. Collect holding quotes via mootdx (replaces LLM tdx_quotes calls)
    s_started = _now_iso()
    s_t0 = time.time()
    r = _stage(["uv", "run", "python", str(TOOLS / "collect_holding_quotes.py"), "--date", target,
                "--session", "intraday"], "collect_holding_quotes intraday")
    stages_log.append(_log_stage("collect_holding_quotes", r, s_started, _now_iso(), time.time() - s_t0))
    if not r["ok"]:
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【14:45尾盘报告失败｜{target}】行情采集失败：{r['out'][:300]}")
        return 1

    # 2a. Intraday market snapshot via TQ-Local HTTP (best-effort, WARN on failure)
    s_started = _now_iso()
    s_t0 = time.time()
    r = _stage(["uv", "run", "python", str(TOOLS / "market_timing" / "collect_intraday_snapshot.py"),
                "--date", target], "collect_intraday_snapshot")
    stages_log.append(_log_stage("collect_intraday_snapshot", r, s_started, _now_iso(), time.time() - s_t0,
                                 note="best-effort，失败不中断"))
    if not r["ok"]:
        print(f"[WARN] 盘中快照采集失败（忽略，不中断）：{r['out'][:200]}", file=sys.stderr)

    # 3. Runtime gate
    s_started = _now_iso()
    s_t0 = time.time()
    r = _stage(["uv", "run", "python", str(TOOLS / "runtime_gate.py"), "--date", target,
                "--require-trading-day"], "runtime_gate")
    stages_log.append(_log_stage("runtime_gate", r, s_started, _now_iso(), time.time() - s_t0))
    if not r["ok"]:
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【14:45尾盘报告失败｜{target}】运行门控失败：{r['out'][:300]}")
        return 1

    # 4. Close review (strict + digest)
    s_started = _now_iso()
    s_t0 = time.time()
    r = _stage(["uv", "run", "python", str(TOOLS / "close_review" / "review_core.py"), "--date", target,
                "--strict", "--emit-digest"], "close_review")
    stages_log.append(_log_stage("close_review", r, s_started, _now_iso(), time.time() - s_t0))
    if not r["ok"]:
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【14:45尾盘报告失败｜{target}】close_review校验失败：{r['out'][:500]}")
        return 1

    _write_run_log(target, "completed", run_started, t0, stages_log)

    # 5. Print digest (last section of output starting with 【14:45)
    lines = r["out"].split("\n")
    digest_start = 0
    for i, line in enumerate(lines):
        if line.startswith("【14:45"):
            digest_start = i
            break
    print("\n".join(lines[digest_start:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
