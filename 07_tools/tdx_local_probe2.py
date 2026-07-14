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

out = {"checks": {}}

def run(name, fn):
    try:
        data = fn()
        out["checks"][name] = {
            "ok": True,
            "type": type(data).__name__,
            "length": len(data) if hasattr(data, "__len__") else None,
            "preview": repr(data)[:2000],
        }
    except Exception as e:
        out["checks"][name] = {"ok": False, "error": repr(e)}

try:
    tq.initialize(__file__)
    for code in ["000001.SH", "399006.SZ", "000688.SH", "899050.BJ", "600150.SH"]:
        run(f"snapshot_{code}", lambda c=code: tq.get_market_snapshot(c))
    run("stock_list_default", lambda: tq.get_stock_list())
    run("stock_list_sh", lambda: tq.get_stock_list(market=[1]))
    run("stock_list_sz", lambda: tq.get_stock_list(market=[0]))
    run("stock_list_bj", lambda: tq.get_stock_list(market=[2]))
    run("stock_info_600150", lambda: tq.get_stock_info(["600150.SH"]))
finally:
    try: tq.close()
    except Exception: pass

path=Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team\06_logs\tdx_local_probe2_result.json")
path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(path)
print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:6000])
