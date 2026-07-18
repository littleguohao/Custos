# -*- coding: utf-8 -*-
"""09:05 one-shot premarket report pipeline."""
from __future__ import annotations
import json, re, subprocess, sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import BASE

TOOLS = BASE / "07_tools"
PLANS = BASE / "03_daily_plans"
target = date.today().strftime("%Y-%m-%d")

def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(BASE))
    return r.returncode, (r.stdout + r.stderr).strip()

# 1. Trading calendar
rc, out = run(["uv", "run", "python", str(TOOLS / "trading_calendar.py"), "--check-date", target])
if rc != 0:
    print(f"【盘前日报失败｜{target}】日历检查失败：{out[:200]}")
    sys.exit(1)
m = re.search(r'\{.*\}', out, re.DOTALL)
cal = json.loads(m.group()) if m else {}
if not cal.get("is_trading_day", False):
    print(f"今日休市，盘前日报不生成（{target}）")
    sys.exit(0)

# 2. Daily pipeline (premarket, reuse discovery)
rc, out = run(["uv", "run", "python", str(TOOLS / "daily_pipeline.py"), "--date", target, "--session-type", "premarket", "--reuse-discovery"])
if rc != 0:
    print(f"【盘前日报失败｜{target}】daily_pipeline失败：{out[:500]}")
    sys.exit(1)

# 3. Read generated report and convert to text digest
report_path = PLANS / f"{target}_daily_report.md"
if not report_path.exists():
    print(f"【盘前日报失败｜{target}】报告文件未生成：{report_path}")
    sys.exit(1)

md = report_path.read_text(encoding="utf-8")

# Extract key sections for digest
lines = md.split("\n")
digest_lines = []
in_section = False
for line in lines:
    # Skip empty lines at start
    if not line.strip() and not digest_lines:
        continue
    # Include headers, bullet points, and key content; convert tables to text
    if line.startswith("#"):
        # Convert markdown header to text
        text = line.lstrip("#").strip()
        digest_lines.append(f"\n{text}")
        digest_lines.append("─" * min(len(text) * 2, 40))
        in_section = True
    elif line.startswith("|"):
        # Convert table rows to text
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if cells and not all(set(c) <= set("-: ") for c in cells):
            digest_lines.append(" | ".join(cells))
    elif line.startswith("- ") or line.startswith("• "):
        digest_lines.append(line)
    elif line.strip() and in_section:
        digest_lines.append(line)

digest = "\n".join(digest_lines).strip()
# Cap at 3500 chars for feishu
if len(digest) > 3500:
    digest = digest[:3450] + "\n...(完整报告见文件)"

print(f"【盘前日报｜{target}】")
print(digest)
