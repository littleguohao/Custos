# -*- coding: utf-8 -*-
"""20:30 one-shot post-close review pipeline."""
from __future__ import annotations
import json, re, subprocess, sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
TOOLS = BASE / "07_tools"
REVIEWS = BASE / "04_reviews" / "daily"
target = date.today().strftime("%Y-%m-%d")

def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(BASE))
    return r.returncode, (r.stdout + r.stderr).strip()

# 1. Trading calendar
rc, out = run(["uv", "run", "python", str(TOOLS / "trading_calendar.py"), "--check-date", target])
if rc != 0:
    print(f"【盘后复盘失败｜{target}】日历检查失败：{out[:200]}")
    sys.exit(1)
m = re.search(r'\{.*\}', out, re.DOTALL)
cal = json.loads(m.group()) if m else {}
if not cal.get("is_trading_day", False):
    print(f"今日休市，盘后复盘不生成（{target}）")
    sys.exit(0)

# 2. Daily pipeline (postclose)
rc, out = run(["uv", "run", "python", str(TOOLS / "daily_pipeline.py"), "--date", target, "--session-type", "postclose"])
if rc != 0:
    print(f"【盘后复盘失败｜{target}】daily_pipeline失败：{out[:500]}")
    sys.exit(1)

# 3. Final close review
no_trades_flag = []
# Check if no_trades confirmed
trades_meta = BASE / "01_data" / "trades" / "_import_meta.json"
if trades_meta.exists():
    meta = json.loads(trades_meta.read_text(encoding="utf-8"))
    if meta.get("no_trades_confirmed_dates", {}).get(target):
        no_trades_flag = ["--no-trades-confirmed"]

rc, out = run(["uv", "run", "python", str(TOOLS / "close_review" / "final_close_review.py"), "--date", target] + no_trades_flag)
if rc != 0:
    print(f"【盘后复盘失败｜{target}】final_close_review失败：{out[:500]}")
    sys.exit(1)

# 4. Validator
rc, out = run(["uv", "run", "python", str(TOOLS / "close_review" / "final_review_validator.py"), "--date", target])
if rc != 0:
    print(f"【盘后复盘失败｜{target}】验证未通过：{out[:500]}")
    sys.exit(1)

# 5. Read generated review and convert to text digest
review_path = REVIEWS / f"{target}_final_review.md"
if not review_path.exists():
    print(f"【盘后复盘失败｜{target}】复盘文件未生成：{review_path}")
    sys.exit(1)

md = review_path.read_text(encoding="utf-8")

# Extract key sections for digest (same approach as run_0905.py)
lines = md.split("\n")
digest_lines = []
in_section = False
for line in lines:
    if not line.strip() and not digest_lines:
        continue
    if line.startswith("#"):
        text = line.lstrip("#").strip()
        digest_lines.append(f"\n{text}")
        digest_lines.append("─" * min(len(text) * 2, 40))
        in_section = True
    elif line.startswith("|"):
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if cells and not all(set(c) <= set("-: ") for c in cells):
            digest_lines.append(" | ".join(cells))
    elif line.startswith("- ") or line.startswith("• "):
        digest_lines.append(line)
    elif line.strip() and in_section:
        digest_lines.append(line)

digest = "\n".join(digest_lines).strip()
if len(digest) > 3500:
    digest = digest[:3450] + "\n...(完整复盘见文件)"

print(f"【盘后复盘｜{target}】")
print(digest)
