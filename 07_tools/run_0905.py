# -*- coding: utf-8 -*-
"""09:05 one-shot premarket report pipeline."""
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
from pipeline_kit import check_trading_day, md_to_digest, run_stage

TOOLS = BASE / "07_tools"
PLANS = BASE / "03_daily_plans"
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
    print(f"【盘前日报失败｜{target}】日历检查失败：{str(e)[:200]}")
    sys.exit(1)
if not cal.get("is_trading_day", False):
    print(f"今日休市，盘前日报不生成（{target}）")
    sys.exit(0)

# 2. Daily pipeline (premarket, reuse discovery)
r = _stage(["uv", "run", "python", str(TOOLS / "daily_pipeline.py"), "--date", target,
            "--session-type", "premarket", "--reuse-discovery"], "daily_pipeline premarket")
if not r["ok"]:
    print(f"【盘前日报失败｜{target}】daily_pipeline失败：{r['out'][:500]}")
    sys.exit(1)

# 3. Read generated report and convert to text digest
report_path = PLANS / f"{target}_daily_report.md"
if not report_path.exists():
    print(f"【盘前日报失败｜{target}】报告文件未生成：{report_path}")
    sys.exit(1)

digest = md_to_digest(report_path.read_text(encoding="utf-8"))

print(f"【盘前日报｜{target}】")
print(digest)
