# -*- coding: utf-8 -*-
"""08:50 one-shot premarket data collection (except wenda_notice_query which needs LLM tool)."""
from __future__ import annotations

import contextlib
import io
import os
import sys
import argparse
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import BASE
from pipeline_kit import check_trading_day, run_stage

TOOLS = BASE / "07_tools"
ap = argparse.ArgumentParser()
ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
args = ap.parse_args()
target = args.date

# Subprocesses rely on project discovery (uv run) from the repo root.
os.chdir(BASE)


def _stage(cmd: list[str], name: str) -> dict:
    """Run a stage quietly: runner stdout is a machine-consumed protocol, so
    the stage echo ([RUN] header, subprocess output) is suppressed; only the
    summary lines below are printed."""
    with contextlib.redirect_stdout(io.StringIO()):
        r = run_stage(cmd, name, required=False)
    r["out"] = (r["stdout"] + r["stderr"]).strip()
    return r


# 1. Trading calendar
try:
    with contextlib.redirect_stdout(io.StringIO()):
        cal = check_trading_day(target)
except RuntimeError as e:
    print(f"【08:50预采集失败｜{target}】日历检查失败：{str(e)[:200]}")
    sys.exit(1)
if not cal.get("is_trading_day", False):
    print(f"今日休市，08:50预采集跳过（{target}）")
    sys.exit(0)

steps = ["calendar=ok"]

# 2-6. Data collectors (best-effort: rc recorded into steps, never fatal)
STAGES = [
    (["uv", "run", "python", str(TOOLS / "market_timing" / "market_timing_collector.py"), "--date", target], "market_timing"),
    (["uv", "run", "python", str(TOOLS / "market_timing" / "overseas_market_collector.py"), "--date", target], "overseas"),
    (["uv", "run", "python", str(TOOLS / "news" / "rss_collector.py"), "--date", target], "rss_collect"),
    (["uv", "run", "python", str(TOOLS / "collect_incremental_market.py"), "--date", target], "incremental"),
    (["uv", "run", "python", str(TOOLS / "news" / "rss_filter.py"), "--date", target, "--session-type", "premarket"], "rss_filter"),
]
for cmd, name in STAGES:
    r = _stage(cmd, name)
    steps.append(f"{name}={'ok' if r['ok'] else 'fail'}")

print(f"【08:50预采集完成｜{target}】{'；'.join(steps)}")
