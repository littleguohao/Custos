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

# 2. Collect postclose holding quotes via mootdx (online bars for today's close)
rc, out = run(["uv", "run", "python", str(TOOLS / "collect_holding_quotes.py"), "--date", target, "--session", "postclose"])
if rc != 0:
    print(f"[WARN] collect_holding_quotes postclose failed: {out[:200]}")
else:
    print(f"[OK] {out.strip().splitlines()[-1] if out.strip() else 'holding quotes collected'}")

# 3. Collect incremental market data (breadth/turnover/limit via mootdx + A50/CNH via Yahoo)
rc, out = run(["uv", "run", "python", str(TOOLS / "collect_incremental_market.py")])
if rc != 0:
    print(f"[WARN] collect_incremental_market failed: {out[:200]}")
else:
    print(f"[OK] {out.strip().splitlines()[-1] if out.strip() else 'incremental market collected'}")

# 3b. Calculate MFE/MAE for holdings
rc, out = run(["uv", "run", "python", str(TOOLS / "calc_mfe_mae.py")])
if rc != 0:
    print(f"[WARN] calc_mfe_mae failed: {out[:200]}")
else:
    print(f"[OK] MFE/MAE calculated")

# 3c. Collect fund flow rank (eastmoney direct API)
rc, out = run(["uv", "run", "python", str(TOOLS / "collect_fund_flow.py")])
if rc != 0:
    print(f"[WARN] collect_fund_flow failed: {out[:200]}")
else:
    print(f"[OK] fund flow rank collected")

# 4. Merge incremental data into market_timing_input.json
incremental_path = BASE / "01_data" / "market" / f"{target}_incremental_market.json"
market_path = BASE / "01_data" / "market" / f"{target}_market_timing_input.json"
if incremental_path.exists() and market_path.exists():
    try:
        inc = json.loads(incremental_path.read_text(encoding="utf-8"))
        mkt = json.loads(market_path.read_text(encoding="utf-8"))
        # Merge breadth into market_quality checks
        breadth = inc.get("breadth", {})
        if "880005" in breadth:
            b = breadth["880005"]
            mkt.setdefault("market_breadth", {
                "quality": "auto",
                "as_of": b.get("date", ""),
                "up_count": b.get("up_count"),
                "down_count": b.get("down_count"),
                "source": "mootdx_reader_880005",
            })
        if "880006" in breadth:
            b6 = breadth["880006"]
            mkt.setdefault("sentiment", {
                "quality": "auto",
                "as_of": b6.get("date", ""),
                "limit_up": b6.get("close"),
                "source": "mootdx_reader_880006",
            })
        # Turnover from 880001 amount
        if "880001" in breadth:
            b1 = breadth["880001"]
            mkt.setdefault("turnover", {
                "quality": "auto",
                "as_of": b1.get("date", ""),
                "value": b1.get("close"),
                "source": "mootdx_reader_880001",
            })
            mkt.setdefault("market_turnover", {
                "quality": "auto",
                "as_of": b1.get("date", ""),
                "value": b1.get("close"),
                "source": "mootdx_reader_880001",
            })
        # Overseas from incremental
        if "a50_futures" in inc:
            mkt.setdefault("overseas_market", {})["a50_change_pct"] = inc["a50_futures"].get("change_pct")
        if "cnh_usd" in inc:
            mkt.setdefault("overseas_market", {})["cnh_change_pct"] = inc["cnh_usd"].get("change_pct")
        # Northbound
        if "northbound" in inc:
            mkt["northbound"] = inc["northbound"]
        market_path.write_text(json.dumps(mkt, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[OK] incremental data merged into market_timing_input.json")
    except Exception as e:
        print(f"[WARN] merge incremental failed: {e}")

# 5. Auto-fix 0AMV quality if amv_0day is set but quality missing
if market_path.exists():
    mkt = json.loads(market_path.read_text(encoding="utf-8"))
    amv = mkt.get("amv_0", {})
    amv_day = mkt.get("amv_0day")
    if amv_day is not None and amv.get("quality") != "confirmed":
        amv["amv_change_pct"] = amv_day
        amv["quality"] = "confirmed"
        if not amv.get("effective_state"):
            amv["effective_state"] = amv.get("amv_zone") or ("空头" if amv_day < -2.3 else "做多" if amv_day > 4 else "中性")
        mkt["amv_0"] = amv
        market_path.write_text(json.dumps(mkt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 0AMV quality auto-set to confirmed (value={amv_day}%, regime={amv['effective_state']})")

# 6. Daily pipeline (postclose)
rc, out = run(["uv", "run", "python", str(TOOLS / "daily_pipeline.py"), "--date", target, "--session-type", "postclose"])
if rc != 0:
    print(f"【盘后复盘失败｜{target}】daily_pipeline失败：{out[:500]}")
    sys.exit(1)

# 7. Final close review
no_trades_flag = []
trades_meta = BASE / "01_data" / "trades" / "_import_meta.json"
if trades_meta.exists():
    meta = json.loads(trades_meta.read_text(encoding="utf-8"))
    if meta.get("no_trades_confirmed_dates", {}).get(target):
        no_trades_flag = ["--no-trades-confirmed"]

rc, out = run(["uv", "run", "python", str(TOOLS / "close_review" / "final_close_review.py"), "--date", target] + no_trades_flag)
if rc != 0:
    print(f"【盘后复盘失败｜{target}】final_close_review失败：{out[:500]}")
    sys.exit(1)

# 8. Validator
rc, out = run(["uv", "run", "python", str(TOOLS / "close_review" / "final_review_validator.py"), "--date", target])
if rc != 0:
    print(f"【盘后复盘失败｜{target}】验证未通过：{out[:500]}")
    sys.exit(1)

# 9. Read generated review and convert to text digest
review_path = REVIEWS / f"{target}_final_review.md"
if not review_path.exists():
    print(f"【盘后复盘失败｜{target}】复盘文件未生成：{review_path}")
    sys.exit(1)

md = review_path.read_text(encoding="utf-8")

# Extract key sections for digest
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
