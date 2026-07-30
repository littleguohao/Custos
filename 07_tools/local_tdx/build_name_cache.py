# -*- coding: utf-8 -*-
"""一次性构建股票名称缓存(01_data/market/stock_name_map.json)。

背景:mootdx 在线接口持续失败('>' NoneType,2026-07 起),formula_screen 的 _load_name_map
在线+缓存均失败 → ST 硬排除失效(st_filter=unavailable)。TQ-Local 的 get_stock_info 单点可用,
本脚本对 vipdoc 全宇宙逐只取名称写入缓存,之后 _load_name_map 走 cache 自愈(ST 过滤恢复)。
用法(需 TdxW 运行)::
    uv run python 07_tools/local_tdx/build_name_cache.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
for _p in (str(TOOLS), str(TOOLS / "local_tdx")):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import local_tdx_data  # noqa: E402
import tq_sector  # noqa: E402
from paths import BASE  # noqa: E402

CACHE = BASE / "01_data" / "market" / "stock_name_map.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TQ-Local 逐只取名称 → 股票名称缓存")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只(排障用)")
    ap.add_argument("--out", default=str(CACHE))
    args = ap.parse_args(argv)

    if not tq_sector.is_tdxw_running():
        print("[ERR] TdxW.exe 未运行")
        return 1
    codes = local_tdx_data.list_local_vipdoc_codes()
    if args.limit:
        codes = codes[:args.limit]
    print(f"[INFO] universe {len(codes)} 只,逐只取名称…", file=sys.stderr)
    tq = tq_sector._import_tq()
    tq.initialize(str(Path(__file__).resolve()))
    name_map: dict[str, str] = {}
    failed = 0
    t0 = time.monotonic()
    try:
        for i, c in enumerate(codes, 1):
            try:
                info = tq.get_stock_info(local_tdx_data.normalize_code(c)) or {}
                name = str(info.get("Name") or "").strip()
                if name:
                    name_map[c] = name
                else:
                    failed += 1
            except Exception:  # noqa: BLE001
                failed += 1
            if i % 500 == 0:
                rate = i / max(time.monotonic() - t0, 0.1)
                eta = (len(codes) - i) / max(rate, 0.1)
                print(f"[INFO] {i}/{len(codes)} 命中 {len(name_map)} 失败 {failed} "
                      f"({rate:.0f}只/s, ETA {eta / 60:.1f}min)", file=sys.stderr, flush=True)
    finally:
        tq.close()
    out = Path(args.out)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(name_map, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)                     # 原子落盘
    print(f"[OK] 名称缓存 {len(name_map)} 条(失败 {failed}) → {out} "
          f"({time.monotonic() - t0:.0f}s)")
    return 0 if name_map else 2


if __name__ == "__main__":
    raise SystemExit(main())
