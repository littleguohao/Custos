# -*- coding: utf-8 -*-
"""One-off script to apply manual macro/AMV inputs to a specific date's market_timing_input.json."""
import json
from pathlib import Path

# Usage: python apply_manual_inputs.py --date 2026-07-09
import sys
target_date = "2026-07-09"
for i, arg in enumerate(sys.argv):
    if arg == "--date" and i + 1 < len(sys.argv):
        target_date = sys.argv[i + 1]

p = Path(__file__).resolve().parent.parent / "01_data" / "market" / f"{target_date}_market_timing_input.json"
d = json.loads(p.read_text(encoding="utf-8"))
d.setdefault("macro_policy", {}).update({
    "monetary_policy": "宽松",
    "fiscal_policy": "积极",
    "credit_environment": "稳定",
    "regulation_environment": "中性",
    "policy_summary": "当前按用户输入判断为双宽政策：货币政策宽松、财政政策积极；信用与监管暂按稳定/中性处理。"
})
d.setdefault("amv_0", {}).update({
    "amv_change_pct": None,
    "amv_zone": "空头",
    "note": "用户确认活跃市值处于空头区间；若后续补充具体跌幅，可按 0AMV < -2.3% 规则精确评分。"
})
d.setdefault("data_quality", {}).setdefault("notes", []).append("人工输入：宏观为双宽政策；0AMV 活跃市值处于空头区间。")
d.setdefault("data_quality", {}).setdefault("sources", []).append("manual_user_input")
p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print(p)
