# -*- coding: utf-8 -*-
"""Calculate MFE/MAE for current holdings using mootdx Reader daily bars."""
from __future__ import annotations
import json, sys
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
POSITIONS = BASE / "01_data" / "trades" / "current_positions.json"
OUT = BASE / "01_data" / "holdings" / f"{date.today().strftime('%Y-%m-%d')}_mfe_mae.json"

def main():
    from mootdx.reader import Reader

    reader = Reader.factory(market="std", tdxdir="C:/new_tdx64")
    positions = json.loads(POSITIONS.read_text(encoding="utf-8"))

    results = []
    for pos in positions:
        code = str(pos["代码"])
        name = pos["名称"]
        cost = float(pos.get("单位成本", 0))
        hold_days = int(pos.get("持仓天数", 0))
        qty = float(pos.get("持有数量", 0))

        # Determine market
        if code.startswith("6"):
            symbol = f"sh{code}"
        elif code.startswith("920") or code.startswith("8"):
            symbol = f"bj{code}"
        else:
            symbol = f"sz{code}"

        try:
            df = reader.daily(symbol=symbol)
            if df is None or len(df) == 0:
                # Try online bars for BJ stocks
                from mootdx.quotes import Quotes
                client = Quotes.factory(market="std", quiet=True)
                df = client.bars(symbol=code, frequency=9, offset=hold_days + 5)
                if df is not None and len(df) > 0:
                    df = df.reset_index()
                else:
                    results.append({"code": code, "name": name, "mfe": None, "mae": None, "error": "no data"})
                    continue

            # Take last hold_days rows
            df = df.tail(hold_days) if hold_days > 0 else df.tail(30)

            highs = df["high"].astype(float)
            lows = df["low"].astype(float)

            mfe_pct = (highs.max() / cost - 1) * 100 if cost > 0 else None
            mae_pct = (lows.min() / cost - 1) * 100 if cost > 0 else None
            mfe_date = str(df.loc[highs.idxmax(), "date"] if "date" in df.columns else highs.index[-1])[:10]
            mae_date = str(df.loc[lows.idxmin(), "date"] if "date" in df.columns else lows.index[-1])[:10]

            results.append({
                "code": code,
                "name": name,
                "cost": cost,
                "hold_days": hold_days,
                "mfe_pct": round(mfe_pct, 2) if mfe_pct is not None else None,
                "mfe_date": mfe_date,
                "mae_pct": round(mae_pct, 2) if mae_pct is not None else None,
                "mae_date": mae_date,
                "current_price": float(pos.get("最新价", 0)),
                "current_pnl_pct": float(pos.get("持有盈亏率", 0)) * 100,
            })
            print(f"[OK] {code} {name}: MFE={mfe_pct:.1f}% MAE={mae_pct:.1f}%")
        except Exception as e:
            results.append({"code": code, "name": name, "mfe": None, "mae": None, "error": str(e)})
            print(f"[WARN] {code} {name}: {e}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"date": date.today().strftime("%Y-%m-%d"), "holdings": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] MFE/MAE -> {OUT.name}")


if __name__ == "__main__":
    main()
