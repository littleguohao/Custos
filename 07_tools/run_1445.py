# -*- coding: utf-8 -*-
"""One-shot 14:45 pipeline: calendar check -> runtime gate -> close review -> digest."""
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

from paths import BASE  # strategy_team/
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


# 1. Trading calendar check
try:
    with contextlib.redirect_stdout(io.StringIO()):
        cal = check_trading_day(target)
except RuntimeError as e:
    print(f"【14:45尾盘报告失败｜{target}】交易日历检查失败：{str(e)[:200]}")
    sys.exit(1)
if not cal.get("is_trading_day", False):
    print(f"今日确认休市，14:45报告不生成（{target}）")
    sys.exit(0)

# 2. Collect holding quotes via mootdx (replaces LLM tdx_quotes calls)
r = _stage(["uv", "run", "python", str(TOOLS / "collect_holding_quotes.py"), "--date", target,
            "--session", "intraday"], "collect_holding_quotes intraday")
if not r["ok"]:
    print(f"【14:45尾盘报告失败｜{target}】行情采集失败：{r['out'][:300]}")
    sys.exit(1)

# 2b. Update runtime gate with quotes_current flag
gate_path = BASE / "01_data" / "quality" / f"{target}_runtime_gate.json"
if gate_path.exists():
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        gate.setdefault("position_gate", {})["quotes_current"] = True
        gate_path.write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] runtime_gate quotes_current 更新失败：{e}", file=sys.stderr)

# 3. Runtime gate
r = _stage(["uv", "run", "python", str(TOOLS / "runtime_gate.py"), "--date", target,
            "--require-trading-day"], "runtime_gate")
if not r["ok"]:
    print(f"【14:45尾盘报告失败｜{target}】运行门控失败：{r['out'][:300]}")
    sys.exit(1)

# 4. Close review (strict + digest)
r = _stage(["uv", "run", "python", str(TOOLS / "close_review" / "review_core.py"), "--date", target,
            "--strict", "--emit-digest"], "close_review")
if not r["ok"]:
    print(f"【14:45尾盘报告失败｜{target}】close_review校验失败：{r['out'][:500]}")
    sys.exit(1)

# 5. Print digest (last section of output starting with 【14:45)
lines = r["out"].split("\n")
digest_start = 0
for i, line in enumerate(lines):
    if line.startswith("【14:45"):
        digest_start = i
        break
print("\n".join(lines[digest_start:]))
