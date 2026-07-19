# -*- coding: utf-8 -*-
"""20:30 one-shot post-close review pipeline.

stdout is a machine-consumed protocol (see the summary lines below) and is
kept byte-compatible; observability goes to 06_logs/{date}_2030_run_log.json
instead — every run (completed / closed / calendar_failed / failed) leaves
one behind. Intermediate collection stages are best-effort (WARN, never
abort): their failures are recorded as ok=false stage entries but do not set
the run status to failed; only hard stages (daily_pipeline /
final_close_review / final_review_validator / missing review file) do.
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
from pipeline_kit import check_trading_day, log_stage, md_to_digest, now_iso, run_stage, write_run_log

TOOLS = BASE / "07_tools"
REVIEWS = BASE / "04_reviews" / "daily"
LOG_DIR = BASE / "06_logs"

# Module-level aliases kept for tests and readability; implementation lives in pipeline_kit.
_now_iso = now_iso
_log_stage = log_stage


def _write_run_log(target: str, status: str, started_at: str, t0: float, stages: list[dict]):
    return write_run_log(LOG_DIR, "2030", target, status, started_at, t0, stages)


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

    def _run_stage(cmd: list[str], name: str, note: str = "") -> dict:
        s_started = _now_iso()
        s_t0 = time.time()
        r = _stage(cmd, name)
        stages_log.append(_log_stage(name, r, s_started, _now_iso(), time.time() - s_t0, note=note))
        return r

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
        print(f"【盘后复盘失败｜{target}】日历检查失败：{str(e)[:200]}")
        return 1
    stages_log.append(_log_stage("calendar", {"ok": True, "returncode": 0, "timeout": False,
                                              "stdout": cal_buf.getvalue()},
                                 c_started, _now_iso(), time.time() - c_t0,
                                 note=f"is_trading_day={cal.get('is_trading_day')}"))
    if not cal.get("is_trading_day", False):
        _write_run_log(target, "closed", run_started, t0, stages_log)
        print(f"今日休市，盘后复盘不生成（{target}）")
        return 0

    # 2. Collect postclose holding quotes via mootdx (online bars for today's close)
    r = _run_stage(["uv", "run", "python", str(TOOLS / "collect_holding_quotes.py"), "--date", target,
                    "--session", "postclose"], "collect_holding_quotes",
                   note="best-effort，失败不中断")
    if not r["ok"]:
        print(f"[WARN] collect_holding_quotes postclose failed: {r['out'][:200]}")
    else:
        print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'holding quotes collected'}")

    # 3. Collect incremental market data (breadth/turnover/limit via mootdx + A50/CNH via Yahoo)
    r = _run_stage(["uv", "run", "python", str(TOOLS / "collect_incremental_market.py"), "--date", target],
                   "collect_incremental_market", note="best-effort，失败不中断")
    if not r["ok"]:
        print(f"[WARN] collect_incremental_market failed: {r['out'][:200]}")
    else:
        print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'incremental market collected'}")

    # 3b. Calculate MFE/MAE for holdings
    r = _run_stage(["uv", "run", "python", str(TOOLS / "calc_mfe_mae.py"), "--date", target],
                   "calc_mfe_mae", note="best-effort，失败不中断")
    if not r["ok"]:
        print(f"[WARN] calc_mfe_mae failed: {r['out'][:200]}")
    else:
        print(f"[OK] MFE/MAE calculated")

    # 3c. Collect fund flow rank (eastmoney direct API)
    r = _run_stage(["uv", "run", "python", str(TOOLS / "collect_fund_flow.py"), "--date", target],
                   "collect_fund_flow", note="best-effort，失败不中断")
    if not r["ok"]:
        print(f"[WARN] collect_fund_flow failed: {r['out'][:200]}")
    else:
        print(f"[OK] fund flow rank collected")

    # 3d. Refresh market indices from vipdoc (ensure a_share_indices + turnover are populated)
    r = _run_stage(["uv", "run", "python", str(TOOLS / "market_timing" / "refresh_market_indices.py"),
                    "--date", target], "refresh_market_indices", note="best-effort，失败不中断")
    if not r["ok"]:
        print(f"[WARN] refresh_market_indices failed: {r['out'][:200]}")
    else:
        print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'market indices refreshed'}")

    # 3e. Sync Compass 0AMV into ledger + amv_0day (best-effort: 解析失败/锁文件仅 WARN,
    #     人工 15:15 输入路径不受影响; merge 阶段的 amv_0day 自动确认随之生效)
    r = _run_stage(["uv", "run", "python", str(TOOLS / "market_timing" / "sync_compass_amv.py"),
                    "--date", target], "sync_compass_amv", note="best-effort，失败不中断")
    if not r["ok"]:
        print(f"[WARN] sync_compass_amv failed: {r['out'][:200]}")
    elif r["stdout"]:
        sys.stdout.write(r["stdout"])

    # 4. Merge incremental data into market_timing_input.json + auto-confirm 0AMV quality
    #    (best-effort: the script prints the [OK]/[WARN] lines, echoed here verbatim;
    #    a hard failure of the script itself only warns and never aborts the pipeline)
    r = _run_stage(["uv", "run", "python", str(TOOLS / "market_timing" / "merge_incremental_market.py"),
                    "--date", target], "merge_incremental_market", note="best-effort，失败不中断")
    if not r["ok"]:
        print(f"[WARN] merge incremental failed: {r['out'][:200]}")
    elif r["stdout"]:
        sys.stdout.write(r["stdout"])

    # 5. Daily pipeline (postclose)
    r = _run_stage(["uv", "run", "python", str(TOOLS / "daily_pipeline.py"), "--date", target,
                    "--session-type", "postclose"], "daily_pipeline")
    if not r["ok"]:
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【盘后复盘失败｜{target}】daily_pipeline失败：{r['out'][:500]}")
        return 1

    # 6. Final close review
    no_trades_flag = []
    trades_meta = BASE / "01_data" / "trades" / "_import_meta.json"
    if trades_meta.exists():
        meta = json.loads(trades_meta.read_text(encoding="utf-8"))
        if meta.get("no_trades_confirmed_dates", {}).get(target):
            no_trades_flag = ["--no-trades-confirmed"]

    r = _run_stage(["uv", "run", "python", str(TOOLS / "close_review" / "final_close_review.py"),
                    "--date", target] + no_trades_flag, "final_close_review")
    if not r["ok"]:
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【盘后复盘失败｜{target}】final_close_review失败：{r['out'][:500]}")
        return 1

    # 7. Validator
    r = _run_stage(["uv", "run", "python", str(TOOLS / "close_review" / "final_review_validator.py"),
                    "--date", target], "final_review_validator")
    if not r["ok"]:
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【盘后复盘失败｜{target}】验证未通过：{r['out'][:500]}")
        return 1

    # 8. Read generated review and convert to text digest
    review_path = REVIEWS / f"{target}_final_review.md"
    if not review_path.exists():
        stages_log.append(_log_stage("review_digest",
                                     {"ok": False, "returncode": None, "timeout": False},
                                     _now_iso(), _now_iso(), 0.0,
                                     note=f"复盘文件未生成：{review_path}"))
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【盘后复盘失败｜{target}】复盘文件未生成：{review_path}")
        return 1

    stages_log.append(_log_stage("review_digest", {"ok": True, "returncode": 0, "timeout": False},
                                 _now_iso(), _now_iso(), 0.0,
                                 note=f"review={review_path.name}"))
    digest = md_to_digest(review_path.read_text(encoding="utf-8"), truncate_note="...(完整复盘见文件)")

    _write_run_log(target, "completed", run_started, t0, stages_log)

    print(f"【盘后复盘｜{target}】")
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
