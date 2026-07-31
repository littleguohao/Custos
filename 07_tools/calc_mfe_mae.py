# -*- coding: utf-8 -*-
"""Calculate MFE/MAE for current holdings using mootdx Reader daily bars.

⚠️ 窗口口径(2026-07-31 修正):MFE/MAE 必须按**入场日**锚定。
此前用券商导出的「持仓天数」(自然日)当 `df.tail(n)` 的 K 线行数——365 自然日只有约 250
个交易日,窗口因此往回多伸约 40%,**吃进入场前的 K 线**,把入场前的高低点算进本笔交易的
最大浮盈/浮亏;数据末尾若不是目标日,锚点还会静默平移。该数字流向 weekly_review 的卖飞
判定(SELL_FLY_PCT),口径错会直接误判"卖飞"。现改为从成交台账 FIFO 回放解析建仓日,
并按日期过滤;解析不出入场日或 K 线未覆盖入场日时**不出数**(fail-closed),给 unable_reason。
"""
from __future__ import annotations
import json, os, sys
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import BASE, TDX_ROOT  # noqa: E402

POSITIONS = BASE / "01_data" / "trades" / "current_positions.json"
LEDGER = BASE / "01_data" / "trades" / "master_trade_ledger.csv"


def resolve_open_entry_dates(trades: list[dict]) -> dict[str, dict]:
    """FIFO 回放全台账,返回每只**仍持有**股票的建仓日。

    口径与 `weekly_review.fifo_pair` 一致(买入压栈、卖出先进先出消耗),区别是这里取
    **剩余未平仓 lot**:FIFO 下最老的未平 lot 才是"当前还持有的那批",其成交日 = 建仓日,
    用来锚定 MFE/MAE 窗口左端。

    返回 {code: {entry_date, avg_buy_date, open_qty, open_lots}}。
    卖出多于买入(台账不完整)时该股按无未平仓处理,不返回 —— 宁可不出数也不给错窗口。
    """
    lots: dict[str, list[list]] = {}          # code -> [[qty, price, date], ...]
    for t in sorted(trades, key=lambda r: (r.get("date") or "", r.get("time") or "",
                                           0 if r.get("side") == "买入" else 1)):
        code, qty = str(t.get("code") or ""), float(t.get("qty") or 0)
        if not code or qty <= 0:
            continue
        if t.get("side") == "买入":
            lots.setdefault(code, []).append([qty, float(t.get("price") or 0), t.get("date") or ""])
            continue
        remaining, book = qty, lots.setdefault(code, [])
        while remaining > 1e-9 and book:
            take = min(remaining, book[0][0])
            book[0][0] -= take
            remaining -= take
            if book[0][0] <= 1e-9:
                book.pop(0)
    out: dict[str, dict] = {}
    for code, book in lots.items():
        book = [x for x in book if x[0] > 1e-9 and x[2]]
        if not book:
            continue
        total = sum(x[0] for x in book)
        # 数量加权平均买入日只作参考;窗口左端一律用最老未平 lot(最保守、不漏浮亏)
        avg_ord = sum(date.fromisoformat(x[2]).toordinal() * x[0] for x in book) / total
        out[code] = {
            "entry_date": min(x[2] for x in book),
            "avg_buy_date": date.fromordinal(round(avg_ord)).isoformat(),
            "open_qty": round(total, 4),
            "open_lots": len(book),
        }
    return out


def normalize_date_col(df):
    """把各数据源的日期列统一成 `date`,取不到时返回 None(由调用方 fail-closed)。

    三个来源列名不一致:local_tdx.read_vipdoc_daily → `date`;mootdx Quotes.bars → `datetime`;
    mootdx Reader.daily() 是 DatetimeIndex,reset_index() 后列名随 index.name 变化(可能是
    `date`/`datetime`/`index`)。这里逐一试,**不能**因为列名不同就把持仓判成"无数据"。
    """
    if df is None or len(df) == 0:
        return None
    for cand in ("date", "datetime", "trade_date", "index", "level_0"):
        if cand in df.columns:
            return df if cand == "date" else df.rename(columns={cand: "date"})
    return None


def load_entry_dates(ledger_path: Path = LEDGER) -> dict[str, dict]:
    """读台账并解析建仓日。复用 weekly_review.parse_ledger 的行规范化,避免两套解析漂移。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "close_review"))
    try:
        import weekly_review as wr  # noqa: PLC0415
        rows = wr.parse_ledger(ledger_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 台账解析失败,MFE/MAE 无法锚定入场日: {exc}")
        return {}
    return resolve_open_entry_dates(rows or [])


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    target = args.date
    OUT = BASE / "01_data" / "holdings" / f"{target}_mfe_mae.json"

    from mootdx.reader import Reader

    reader = Reader.factory(market="std", tdxdir=str(TDX_ROOT))
    positions = json.loads(POSITIONS.read_text(encoding="utf-8"))
    entries = load_entry_dates()
    if not entries:
        print("[WARN] 台账未解析出任何未平仓建仓日 —— 所有持仓将不出 MFE/MAE(fail-closed)")

    results = []
    for pos in positions:
        code = str(pos["代码"])
        name = pos["名称"]
        cost = float(pos.get("单位成本", 0))
        hold_days = int(pos.get("持仓天数", 0))
        qty = float(pos.get("持有数量", 0))

        # 入场日锚点:解析不出就不出数,绝不退回「持仓天数当行数」的旧口径
        entry = entries.get(code) or {}
        entry_date = entry.get("entry_date")
        if not entry_date:
            results.append({"code": code, "name": name, "mfe_pct": None, "mae_pct": None,
                            "hold_days": hold_days,
                            "unable_reason": "成交台账无该股未平仓记录，无法锚定入场日"})
            print(f"[WARN] {code} {name}: 台账无未平仓记录，跳过")
            continue
        # 在线兜底取多少根:按自然日跨度换算并留足缓冲(过滤靠日期,多取无害)
        span_days = max((date.fromisoformat(target) - date.fromisoformat(entry_date)).days, 0)
        bars_needed = span_days + 30

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
                    df = client.bars(symbol=code, frequency=9, count=bars_needed)
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
                    df = client.bars(symbol=code, frequency=9, count=bars_needed)
                    if df is not None and len(df) > 0:
                        df = df.reset_index()

            if df is None or len(df) == 0:
                results.append({"code": code, "name": name, "mfe": None, "mae": None, "error": "no data"})
                print(f"[WARN] {code} {name}: no data")
                continue

            # Normalize date column name
            df = normalize_date_col(df)

            # 按**入场日**锚定窗口(不能用自然日「持仓天数」当 K 线行数)
            if df is None:
                results.append({"code": code, "name": name, "mfe_pct": None, "mae_pct": None,
                                "entry_date": entry_date,
                                "unable_reason": "K线缺少日期列，无法按入场日锚定窗口"})
                print(f"[WARN] {code} {name}: 无日期列，跳过")
                continue
            df = df.assign(_d=df["date"].astype(str).str[:10])
            df = df[(df["_d"] >= entry_date) & (df["_d"] <= target)]
            if df.empty:
                results.append({"code": code, "name": name, "mfe_pct": None, "mae_pct": None,
                                "entry_date": entry_date,
                                "unable_reason": f"K线未覆盖入场日 {entry_date}~{target}"})
                print(f"[WARN] {code} {name}: K线未覆盖 {entry_date}~{target}，跳过")
                continue

            highs = df["high"].astype(float)
            lows = df["low"].astype(float)

            mfe_pct = (highs.max() / cost - 1) * 100 if cost > 0 else None
            mae_pct = (lows.min() / cost - 1) * 100 if cost > 0 else None
            mfe_idx = highs.idxmax()
            mae_idx = lows.idxmin()
            mfe_date = str(df.loc[mfe_idx, "_d"])[:10]
            mae_date = str(df.loc[mae_idx, "_d"])[:10]

            results.append({
                "code": code,
                "name": name,
                "cost": cost,
                "hold_days": hold_days,
                "entry_date": entry_date,
                "avg_buy_date": entry.get("avg_buy_date"),
                "window_bars": int(len(df)),
                "mfe_pct": round(mfe_pct, 2) if mfe_pct is not None else None,
                "mfe_date": mfe_date,
                "mae_pct": round(mae_pct, 2) if mae_pct is not None else None,
                "mae_date": mae_date,
                "current_price": float(pos.get("最新价", 0)),
                "current_pnl_pct": float(pos.get("持有盈亏率", 0)) * 100,
            })
            print(f"[OK] {code} {name}: MFE={mfe_pct:.1f}% MAE={mae_pct:.1f}% "
                  f"(入场 {entry_date}, {len(df)} 根)")
        except Exception as e:
            results.append({"code": code, "name": name, "mfe": None, "mae": None, "error": str(e)})
            print(f"[WARN] {code} {name}: {e}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"date": target, "holdings": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] MFE/MAE -> {OUT.name}")


if __name__ == "__main__":
    main()
