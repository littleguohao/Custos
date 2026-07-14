# -*- coding: utf-8 -*-
"""Wrapper for C:\\new_tdx64\\PYPlugins\\user\\workflow_B1.py.

B1 is treated as one stock_pool candidate source, not as a direct buy signal.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team")
TDX_SCRIPT = Path(r"C:\new_tdx64\PYPlugins\user\workflow_B1.py")
DEFAULT_OUT = BASE / "01_data" / "stock_pool" / "b1_candidates"
DEFAULT_REF = Path(r"C:\new_tdx64\PYPlugins\user\B1_DATA")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run TDX B1 workflow into strategy_team stock_pool data.")
    ap.add_argument("--pool-type", default="5")
    ap.add_argument("--pool-sector", default="")
    ap.add_argument("--block-type", default="0")
    ap.add_argument("--end-time", default="")
    ap.add_argument("--count", default="120")
    ap.add_argument("--topn", default="20")
    ap.add_argument("--factor-set", default="6", choices=["5", "6", "7", "10"])
    ap.add_argument("--ref-codes", default="")
    ap.add_argument("--ref-file", default="")
    ap.add_argument("--ref-dir", default=str(DEFAULT_REF))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(TDX_SCRIPT),
        "--out-dir", str(out_dir),
        "--end-time", args.end_time,
        "--count", str(args.count),
        "--topn", str(args.topn),
        "--factor-set", args.factor_set,
        "--ref-dir", args.ref_dir,
        "--block-type", str(args.block_type),
    ]
    if args.pool_sector:
        cmd.extend(["--pool-type", "", "--pool-sector", args.pool_sector])
    else:
        cmd.extend(["--pool-type", args.pool_type])
    if args.ref_codes:
        cmd.extend(["--ref-codes", args.ref_codes])
    if args.ref_file:
        cmd.extend(["--ref-file", args.ref_file])

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)
    print("out_dir:", out_dir)


if __name__ == "__main__":
    main()
