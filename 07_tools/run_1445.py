# -*- coding: utf-8 -*-
"""One-shot 14:45 pipeline: calendar check -> runtime gate -> close review -> digest."""
from __future__ import annotations
import json, subprocess, sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent  # strategy_team/
TOOLS = BASE / "07_tools"
PLANS = BASE / "03_daily_plans"
target = date.today().strftime("%Y-%m-%d")

def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(BASE))
    return r.returncode, (r.stdout + r.stderr).strip()

# 1. Trading calendar check
rc, out = run(["uv", "run", "python", str(TOOLS / "trading_calendar.py"), "--check-date", target])
if rc != 0:
    print(f"【14:45尾盘报告失败｜{target}】交易日历检查失败：{out[:200]}")
    sys.exit(1)
try:
    cal = json.loads(out.split("\n")[-1] if "{" not in out.split("\n")[0] else out)
except Exception:
    # Try to find JSON in output
    import re
    m = re.search(r'\{.*\}', out, re.DOTALL)
    cal = json.loads(m.group()) if m else {}
if not cal.get("is_trading_day", False):
    print(f"今日确认休市，14:45报告不生成（{target}）")
    sys.exit(0)

# 2. Runtime gate
rc, out = run(["uv", "run", "python", str(TOOLS / "runtime_gate.py"), "--date", target, "--require-trading-day"])
if rc != 0:
    print(f"【14:45尾盘报告失败｜{target}】运行门控失败：{out[:300]}")
    sys.exit(1)

# 3. Close review (strict + digest)
rc, out = run(["uv", "run", "python", str(TOOLS / "close_review" / "close_review.py"), "--date", target, "--strict", "--emit-digest"])
if rc != 0:
    print(f"【14:45尾盘报告失败｜{target}】close_review校验失败：{out[:500]}")
    sys.exit(1)

# 4. Print digest (last section of output starting with 【14:45)
lines = out.split("\n")
digest_start = 0
for i, line in enumerate(lines):
    if line.startswith("【14:45"):
        digest_start = i
        break
print("\n".join(lines[digest_start:]))
