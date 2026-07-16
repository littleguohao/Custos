# -*- coding: utf-8 -*-
"""08:50 one-shot premarket data collection (except wenda_notice_query which needs LLM tool)."""
from __future__ import annotations
import json, subprocess, sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
TOOLS = BASE / "07_tools"
target = date.today().strftime("%Y-%m-%d")

def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(BASE))
    return r.returncode, (r.stdout + r.stderr).strip()

steps = []

# 1. Trading calendar
rc, out = run(["uv", "run", "python", str(TOOLS / "trading_calendar.py"), "--check-date", target])
if rc != 0:
    print(f"【08:50预采集失败｜{target}】日历检查失败：{out[:200]}")
    sys.exit(1)
import re
m = re.search(r'\{.*\}', out, re.DOTALL)
cal = json.loads(m.group()) if m else {}
if not cal.get("is_trading_day", False):
    print(f"今日休市，08:50预采集跳过（{target}）")
    sys.exit(0)
steps.append("calendar=ok")

# 2. Market timing collector
rc, out = run(["uv", "run", "python", str(TOOLS / "market_timing" / "market_timing_collector.py"), "--date", target])
steps.append(f"market_timing={'ok' if rc == 0 else 'fail'}")

# 3. Overseas market collector
rc, out = run(["uv", "run", "python", str(TOOLS / "market_timing" / "overseas_market_collector.py"), "--date", target])
steps.append(f"overseas={'ok' if rc == 0 else 'fail'}")

# 4. RSS collector
rc, out = run(["uv", "run", "python", str(TOOLS / "news" / "rss_collector.py"), "--date", target])
steps.append(f"rss_collect={'ok' if rc == 0 else 'fail'}")

# 5. Incremental market data (A50, CNH, breadth, northbound)
rc, out = run(["uv", "run", "python", str(TOOLS / "collect_incremental_market.py")])
steps.append(f"incremental={'ok' if rc == 0 else 'fail'}")

# 6. RSS filter (premarket)
rc, out = run(["uv", "run", "python", str(TOOLS / "news" / "rss_filter.py"), "--date", target, "--session-type", "premarket"])
steps.append(f"rss_filter={'ok' if rc == 0 else 'fail'}")

print(f"【08:50预采集完成｜{target}】{'；'.join(steps)}")
