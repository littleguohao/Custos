# -*- coding: utf-8 -*-
"""Calculate MFE/MAE for current holdings using mootdx Reader daily bars."""
from __future__ import annotations
import json, os, sys
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
POSITIONS = BASE / "01_data" / "trades" / "current_positions.json"
OUT = BASE / "01_data" / "holdings" / f"{date.today().strftime('%Y-%m-%d')}_mfe_mae.json"

def main():
    from mootdx.reader import Reader

    TDX_ROOT = os.environ.get("TDX_ROOT", r"E:\new_tdx64")
    reader = Reader.factory(market="std", tdxdir=TDX_ROOT)
    positions = json.loads(POSITIONS.read_text(encoding="utf-8"))

    results = []
    for pos in positions:
        code = str(pos["代码"])
        name = pos["名称"]
        cost = float(pos.get("单位成本", 0))
        hold_days = int(pos.get("持仓天数", 0))
        qty = float(pos.get("持有数量", 0))

        # Determine market
        is_bj = code.startswith("920") or code.startswith("8") or code.startswith("4")
        if code.startswith("6"):
            symbol = f"sh{code}"
        elif is_bj:
            symbol = f"bj{code}"
        else:
            symbol = f"sz{code}"

        try:
            df = None
            # All stocks: try local_tdx vipdoc first (supports BJ)
            if is_bj:
                # BJ stocks: use local_tdx direct parser (mootdx Reader misroutes 920xxx)
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "07_tools" / "local_tdx"))
                import local_tdx_data as ltd
                df = ltd.read_vipdoc_daily(code)
                if df is not None and len(df) > 0:
                    df = df.reset_index(drop=True)
                else:
                    # Fallback to online bars
                    from mootdx.quotes import Quotes
                    client = Quotes.factory(market="std", quiet=True)
                    df = client.bars(symbol=code, frequency=9, count=hold_days + 10)
                    if df is not None and len(df) > 0:
                        df = df.reset_index()
            else:
                df = reader.daily(symbol=symbol)
                if df is not None and len(df) > 0:
                    df = df.reset_index()
                else:
                    # Fallback to online bars for any stock
                    from mootdx.quotes import Quotes
                    client = Quotes.factory(market="std", quiet=True)
                    df = client.bars(symbol=code, frequency=9, count=hold_days + 10)
                    if df is not None and len(df) > 0:
                        df = df.reset_index()

            if df is None or len(df) == 0:
                results.append({"code": code, "name": name, "mfe": None, "mae": None, "error": "no data"})
                print(f"[WARN] {code} {name}: no data")
                continue

            # Normalize date column name
            if "datetime" in df.columns:
                df = df.rename(columns={"datetime": "date"})

            # Take last hold_days rows
            df = df.tail(hold_days) if hold_days > 0 else df.tail(30)

            highs = df["high"].astype(float)
            lows = df["low"].astype(float)

            mfe_pct = (highs.max() / cost - 1) * 100 if cost > 0 else None
            mae_pct = (lows.min() / cost - 1) * 100 if cost > 0 else None
            mfe_idx = highs.idxmax()
            mae_idx = lows.idxmin()
            date_col = "date" if "date" in df.columns else df.index.name or "index"
            mfe_date = str(df.loc[mfe_idx, date_col] if date_col in df.columns else mfe_idx)[:10]
            mae_date = str(df.loc[mae_idx, date_col] if date_col in df.columns else mae_idx)[:10]

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
