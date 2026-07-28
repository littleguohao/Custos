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
    """get_market_data 返回归一为 [date, close]。兼容 dict{code:df/series} 或直接 df。
    严格校验:日期列必须可解析为日期(防 RangeIndex 被误当日期静默落盘),收盘列优先名为 Close 的列。"""
    import pandas as pd
    obj = d.get(code) if isinstance(d, dict) else d
    if obj is None:
        return None
    df = obj.to_frame() if hasattr(obj, "to_frame") and not hasattr(obj, "columns") else pd.DataFrame(obj)
    df = df.reset_index()
    df.columns = [str(c) for c in df.columns]
    date_col = df.columns[0]
    if pd.api.types.is_numeric_dtype(df[date_col]):
        return None                     # 数值"日期"列(如 RangeIndex 0,1,...)→ 非真日期,拒绝静默落盘
    close_col = next((c for c in df.columns if c.lower() == "close"),
                     next((c for c in df.columns[1:] if c), df.columns[-1]))
    dates = pd.to_datetime(df[date_col], errors="coerce")
    out = pd.DataFrame({"date": dates.dt.strftime("%Y-%m-%d"),
                        "close": pd.to_numeric(df[close_col], errors="coerce")}).dropna()
    return out if len(out) else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓取通达信板块指数(880xxx)收盘历史→CSV缓存")
    ap.add_argument("--out", default=str(TOOLS.parent / "01_data" / "market" / "sector_index"))
    ap.add_argument("--start", default="20180101", help="起始日 YYYYMMDD(TQ 会给到本地实有最早)")
    ap.add_argument("--period", default="day")
    ap.add_argument("--members", action="store_true",
                    help="同时抓板块成员(get_stock_list_in_sector)→ sector_members.json(板块相位 gate 需要)")
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
    members: dict = {}
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
                    tmp = outdir / f"{code}.csv.tmp"
                    frame.to_csv(tmp, index=False)
                    tmp.replace(outdir / f"{code}.csv")   # 原子落盘:防中断留下截断 CSV(陈旧相位假象)
                    ok += 1
                if args.members:
                    try:
                        mem = tq.get_stock_list_in_sector(code) or []
                        members[code] = [str(x).split(".")[0][-6:].zfill(6) for x in mem]
                    except Exception as mexc:  # noqa: BLE001
                        print(f"[WARN] members {code}: {mexc}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] {code}: {exc}", file=sys.stderr)
            if (i + 1) % 50 == 0:
                print(f"[INFO] {i + 1}/{total}  已落盘 {ok}", file=sys.stderr)
        if args.members:
            import json
            mpath = outdir.parent / "sector_members.json"
            mpath.write_text(json.dumps(members, ensure_ascii=False), encoding="utf-8")
            print(f"[OK] 成员映射 {len(members)} 板块 → {mpath}")
        print(f"[OK] 完成: {ok}/{total} 板块指数落盘到 {outdir}")
    finally:
        tq.close()
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
