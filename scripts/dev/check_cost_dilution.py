# -*- coding: utf-8 -*-
"""一次性诊断（#32 观察期）：核对券商「摊薄成本」与台账「移动平均成本」的口径差。

用法（目标机项目根目录）：
    uv run python scripts/dev/check_cost_dilution.py 600150 920808

对每只票打印全部台账行，并给出两种口径的成本：
- 移动平均成本（reconcile 回放口径：卖出不改变剩余持仓单位成本）
- 摊薄成本（券商 App 口径：已实现盈亏摊进剩余持仓，
  cost = (累计买入金额 + 累计费用 - 累计卖出金额) / 剩余股数）
两者与 current_positions.json 里的 actual_cost 对照。
"""

import json
import sys
from pathlib import Path

import pandas as pd

LEDGER = Path("data/trades/master_trade_ledger.csv")
POS = Path("data/trades/current_positions.json")
CREDITS = {"转债转入", "拆股"}


def main(codes):
    df = pd.read_csv(LEDGER, dtype={"代码": str})
    positions = {p["代码"]: p for p in json.loads(POS.read_text(encoding="utf-8"))}
    for code in codes:
        t = df[df["代码"] == code]
        print(f"===== {code}（{len(t)} 行）=====")
        cols = "成交日期 交易类别 成交数量 成交价格 费用".split()
        print(t[cols].to_string())
        qty = 0.0
        ma_cost = 0.0  # 移动平均成本
        buy_amt = sell_amt = fees = 0.0  # 摊薄成本累计
        for _, r in t.iterrows():
            cat = r["交易类别"]
            q = float(r["成交数量"] or 0)
            p = float(r["成交价格"] or 0)
            f = float(r["费用"] or 0) if pd.notna(r["费用"]) else 0.0
            if cat == "买入" or cat in CREDITS:
                ma_cost = (qty * ma_cost + q * p + f) / (qty + q) if qty + q else 0
                qty += q
                buy_amt += q * p
                fees += f
            elif cat == "卖出":
                qty -= q
                sell_amt += q * p
                fees += f
        diluted = (buy_amt - sell_amt + fees) / qty if qty else 0
        actual = positions.get(code, {}).get("单位成本")
        print(f"  剩余数量      : {qty:g}")
        print(f"  移动平均成本  : {ma_cost:.6f}  <- reconcile 回放口径")
        print(f"  摊薄成本      : {diluted:.6f}  <- 券商 App 口径")
        print(f"  actual_cost   : {actual}  <- current_positions.json")
        print()


if __name__ == "__main__":
    main(sys.argv[1:] or ["600150", "920808"])
