# -*- coding: utf-8 -*-
"""Probe local TDX sector membership by representative stocks.

TQ get_sector_list currently returns mostly sector codes without names. This
utility builds a reverse index: stock code -> sector codes containing it.
TQ access is intentionally serial.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team")
LOCAL_TDX_DIR = BASE / "07_tools" / "local_tdx"
if str(LOCAL_TDX_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_TDX_DIR))

import local_tdx_data as ltd  # noqa: E402

OUT_DIR = BASE / "01_data" / "sectors"

REPRESENTATIVES: dict[str, list[str]] = {
    "AI算力/服务器/液冷": ["000977.SZ", "002281.SZ", "920808.BJ", "000021.SZ"],
    "半导体/芯片/封测/存储": ["603986.SH", "002185.SZ", "600584.SH", "002409.SZ", "600206.SH"],
    "机器人/具身智能": ["002841.SZ", "002414.SZ"],
    "证券/金融": ["601696.SH", "600030.SH", "300059.SZ"],
    "船舶/军工": ["600150.SH", "601989.SH"],
    "燃气/能源": ["605090.SH", "600803.SH"],
    "医疗设备/AI医疗": ["688114.SH", "300760.SZ"],
    "稀土": ["600111.SH", "000831.SZ"],
    "锂电": ["002756.SZ", "300750.SZ"]
}


def norm(s: str) -> str:
    return ltd.normalize_code(s)


def main() -> None:
    reps = sorted({norm(c) for codes in REPRESENTATIVES.values() for c in codes})
    reverse: dict[str, list[str]] = {c: [] for c in reps}
    sector_members_preview: dict[str, Any] = {}

    # Single TQ session: serial but avoids hundreds of initialize/close cycles.
    with ltd.TqSession() as q:
        sectors = list(q.get_sector_list() or [])
        for i, sec in enumerate(sectors, 1):
            try:
                members = [norm(x) for x in (q.get_stock_list_in_sector(sec, block_type=0) or [])]
            except Exception as e:
                sector_members_preview[sec] = {"error": repr(e)}
                continue
            hit = sorted(set(members) & set(reps))
            if hit:
                sector_members_preview[sec] = {"hits": hit, "member_count": len(members)}
                for c in hit:
                    reverse[c].append(sec)
            if i % 50 == 0:
                print(f"scanned {i}/{len(sectors)}")

    theme_hits: dict[str, dict[str, Any]] = {}
    for theme, codes in REPRESENTATIVES.items():
        theme_hits[theme] = {norm(c): reverse.get(norm(c), []) for c in codes}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "sector_count": len(sectors),
        "representatives": REPRESENTATIVES,
        "theme_hits": theme_hits,
        "sector_hits": sector_members_preview,
    }
    p = OUT_DIR / "sector_member_probe.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(p)
    print(json.dumps({"theme_hits": theme_hits}, ensure_ascii=False, indent=2)[:6000])


if __name__ == "__main__":
    main()
