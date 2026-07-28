# -*- coding: utf-8 -*-
"""一次性抓取通达信板块/行业指数(880xxx)日线**收盘价**历史 → 落盘缓存,供板块相位(MACD)回测。

需 TdxW(TQ-Local)运行。板块 MACD 相位只需收盘价,故只取 Close(格式已探明:index=日期,列=代码)。
用法:
    uv run python 07_tools/local_tdx/fetch_sector_index_history.py --out 01_data/market/sector_index --start 20180101
输出:每板块一份 {code}.csv(date,close)。只读 TQ、绝不改线上。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import tq_sector  # noqa: E402  复用其 TdxW 探测 + tqcenter 惰性导入


def _to_close_frame(d, code):
    """get_market_data 返回归一为 [date, close]。兼容 dict{code:df/series} 或直接 df。"""
    import pandas as pd
    obj = d.get(code) if isinstance(d, dict) else d
    if obj is None:
        return None
    df = obj.to_frame() if hasattr(obj, "to_frame") and not hasattr(obj, "columns") else pd.DataFrame(obj)
    df = df.reset_index()
    df.columns = [str(c) for c in df.columns]
    date_col = df.columns[0]
    close_col = next((c for c in df.columns[1:] if c), df.columns[-1])
    out = pd.DataFrame({"date": df[date_col].astype(str).str[:10],
                        "close": pd.to_numeric(df[close_col], errors="coerce")}).dropna()
    return out if len(out) else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓取通达信板块指数(880xxx)收盘历史→CSV缓存")
    ap.add_argument("--out", default=str(TOOLS.parent / "01_data" / "market" / "sector_index"))
    ap.add_argument("--start", default="20180101", help="起始日 YYYYMMDD(TQ 会给到本地实有最早)")
    ap.add_argument("--period", default="day")
    args = ap.parse_args(argv)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    if not tq_sector.is_tdxw_running():
        print("[ERR] TdxW.exe 未运行,无法连 TQ")
        return 1
    tq = tq_sector._import_tq()
    tq.initialize(str(Path(__file__).resolve()))
    ok = 0
    total = 0
    try:
        sectors = tq.get_sector_list() or []
        total = len(sectors)
        print(f"[INFO] 板块数: {total}")
        for i, code in enumerate(sectors):
            try:
                tq.refresh_kline([code], period=args.period)
                d = tq.get_market_data(field_list=["Close"], stock_list=[code],
                                       period=args.period, start_time=args.start, count=-1)
                frame = _to_close_frame(d, code)
                if frame is not None:
                    frame.to_csv(outdir / f"{code}.csv", index=False)
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] {code}: {exc}", file=sys.stderr)
            if (i + 1) % 50 == 0:
                print(f"[INFO] {i + 1}/{total}  已落盘 {ok}", file=sys.stderr)
        print(f"[OK] 完成: {ok}/{total} 板块指数落盘到 {outdir}")
    finally:
        tq.close()
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
