# -*- coding: utf-8 -*-
"""Wrapper for C:\\new_tdx64\\PYPlugins\\user\\tdxdata_download.py.

Keep original TDX user scripts unchanged; route outputs into strategy_team.
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
TDX_SCRIPT = Path(r"C:\new_tdx64\PYPlugins\user\tdxdata_download.py")
DEFAULT_OUT = BASE / "01_data" / "local_tdx" / "kline_csv"


def main() -> None:
    ap = argparse.ArgumentParser(description="Download TDX local K-line data into strategy_team.")
    ap.add_argument("--code", default="", help="stock code, e.g. 600150.SH")
    ap.add_argument("--start", default="", help="YYYYMMDD")
    ap.add_argument("--end", default="", help="YYYYMMDD")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--pool-type", default="5")
    ap.add_argument("--chunk-size", default="200")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(TDX_SCRIPT),
        "--start", args.start,
        "--end", args.end,
        "--out-dir", str(out_dir),
        "--pool-type", args.pool_type,
        "--chunk-size", str(args.chunk_size),
    ]
    if args.code:
        cmd.extend(["--code", args.code])

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)
    print("out_dir:", out_dir)


if __name__ == "__main__":
    main()
