# -*- coding: utf-8 -*-
"""20:30 one-shot post-close review pipeline."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import argparse
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import BASE
from pipeline_kit import check_trading_day, md_to_digest, run_stage

TOOLS = BASE / "07_tools"
REVIEWS = BASE / "04_reviews" / "daily"
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
    print(f"【盘后复盘失败｜{target}】日历检查失败：{str(e)[:200]}")
    sys.exit(1)
if not cal.get("is_trading_day", False):
    print(f"今日休市，盘后复盘不生成（{target}）")
    sys.exit(0)

# 2. Collect postclose holding quotes via mootdx (online bars for today's close)
r = _stage(["uv", "run", "python", str(TOOLS / "collect_holding_quotes.py"), "--date", target,
            "--session", "postclose"], "collect_holding_quotes postclose")
if not r["ok"]:
    print(f"[WARN] collect_holding_quotes postclose failed: {r['out'][:200]}")
else:
    print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'holding quotes collected'}")

# 3. Collect incremental market data (breadth/turnover/limit via mootdx + A50/CNH via Yahoo)
r = _stage(["uv", "run", "python", str(TOOLS / "collect_incremental_market.py"), "--date", target], "collect_incremental_market")
if not r["ok"]:
    print(f"[WARN] collect_incremental_market failed: {r['out'][:200]}")
else:
    print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'incremental market collected'}")

# 3b. Calculate MFE/MAE for holdings
r = _stage(["uv", "run", "python", str(TOOLS / "calc_mfe_mae.py"), "--date", target], "calc_mfe_mae")
if not r["ok"]:
    print(f"[WARN] calc_mfe_mae failed: {r['out'][:200]}")
else:
    print(f"[OK] MFE/MAE calculated")

# 3c. Collect fund flow rank (eastmoney direct API)
r = _stage(["uv", "run", "python", str(TOOLS / "collect_fund_flow.py"), "--date", target], "collect_fund_flow")
if not r["ok"]:
    print(f"[WARN] collect_fund_flow failed: {r['out'][:200]}")
else:
    print(f"[OK] fund flow rank collected")

# 3d. Refresh market indices from vipdoc (ensure a_share_indices + turnover are populated)
r = _stage(["uv", "run", "python", str(TOOLS / "market_timing" / "refresh_market_indices.py"), "--date", target],
           "refresh_market_indices")
if not r["ok"]:
    print(f"[WARN] refresh_market_indices failed: {r['out'][:200]}")
else:
    print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'market indices refreshed'}")

# 4. Merge incremental data into market_timing_input.json + auto-confirm 0AMV quality
#    (best-effort: the script prints the [OK]/[WARN] lines, echoed here verbatim;
#    a hard failure of the script itself only warns and never aborts the pipeline)
r = _stage(["uv", "run", "python", str(TOOLS / "market_timing" / "merge_incremental_market.py"),
            "--date", target], "merge_incremental_market")
if not r["ok"]:
    print(f"[WARN] merge incremental failed: {r['out'][:200]}")
elif r["stdout"]:
    sys.stdout.write(r["stdout"])

# 5. Daily pipeline (postclose)
r = _stage(["uv", "run", "python", str(TOOLS / "daily_pipeline.py"), "--date", target,
            "--session-type", "postclose"], "daily_pipeline postclose")
if not r["ok"]:
    print(f"【盘后复盘失败｜{target}】daily_pipeline失败：{r['out'][:500]}")
    sys.exit(1)

# 6. Final close review
no_trades_flag = []
trades_meta = BASE / "01_data" / "trades" / "_import_meta.json"
if trades_meta.exists():
    meta = json.loads(trades_meta.read_text(encoding="utf-8"))
    if meta.get("no_trades_confirmed_dates", {}).get(target):
        no_trades_flag = ["--no-trades-confirmed"]

r = _stage(["uv", "run", "python", str(TOOLS / "close_review" / "final_close_review.py"), "--date", target]
           + no_trades_flag, "final_close_review")
if not r["ok"]:
    print(f"【盘后复盘失败｜{target}】final_close_review失败：{r['out'][:500]}")
    sys.exit(1)

# 7. Validator
r = _stage(["uv", "run", "python", str(TOOLS / "close_review" / "final_review_validator.py"), "--date", target],
           "final_review_validator")
if not r["ok"]:
    print(f"【盘后复盘失败｜{target}】验证未通过：{r['out'][:500]}")
    sys.exit(1)

# 8. Read generated review and convert to text digest
review_path = REVIEWS / f"{target}_final_review.md"
if not review_path.exists():
    print(f"【盘后复盘失败｜{target}】复盘文件未生成：{review_path}")
    sys.exit(1)

digest = md_to_digest(review_path.read_text(encoding="utf-8"), truncate_note="...(完整复盘见文件)")

print(f"【盘后复盘｜{target}】")
print(digest)
