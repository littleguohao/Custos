# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TDX_ROOT = Path(r"C:\new_tdx64")
sys.path.insert(0, str(TDX_ROOT / "PYPlugins" / "user"))

from tqcenter import tq  # type: ignore

out = {
    "tdx_root": str(TDX_ROOT),
    "checks": {}
}

try:
    tq.initialize(__file__)

    def run(name, fn):
        try:
            data = fn()
            s = repr(data)
            out["checks"][name] = {
                "ok": True,
                "type": type(data).__name__,
                "preview": s[:1500],
                "length": len(data) if hasattr(data, "__len__") else None,
            }
        except Exception as e:
            out["checks"][name] = {"ok": False, "error": repr(e)}

    run("market_data_index_daily", lambda: tq.get_market_data(
        field_list=[], stock_list=["000001.SH", "399006.SZ", "000688.SH", "899050.BJ"], period="1d", count=3, dividend_type="none"
    ))
    run("market_snapshot", lambda: tq.get_market_snapshot(stock_list=["000001.SH", "399006.SZ", "000688.SH", "899050.BJ"]))
    run("sector_list", lambda: tq.get_sector_list())
    run("stock_list_A", lambda: tq.get_stock_list(market=[0, 1, 2], stock_type=[]))
    run("scjy_SC36", lambda: tq.get_scjy_value(field_list=["SC36"]))
    run("scjy_candidates", lambda: tq.get_scjy_value(field_list=["SC1", "SC2", "SC3", "SC4", "SC5", "SC6", "SC36"]))
    run("relation_600150", lambda: tq.get_relation("600150.SH"))

finally:
    try:
        tq.close()
    except Exception:
        pass

path = Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team\06_logs\tdx_local_probe_result.json")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(path)
print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:5000])
