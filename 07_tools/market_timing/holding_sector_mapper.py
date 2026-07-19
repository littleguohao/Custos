# -*- coding: utf-8 -*-
"""Map current holdings to TDX sectors using local TDX block files.

Data source (local_block):
- ``TDX_ROOT/T0002/hq_cache/tdxhy.cfg`` — per-stock TDX industry code
  (T-code) and Shenwan industry code (X-code). Lines look like
  ``1|688114|T0403|||X270302`` (market 0=SZ, 1=SH, 2=BJ).
- ``TDX_ROOT/incon.dat`` — GBK name tables; sections ``#TDXNHY``
  (T-code -> TDX industry name) and ``#TDXRSHY`` (X-code -> SW name).

The classic ``block_gn.dat``/``block_hy.dat`` membership files (what
mootdx ``reader.block`` parses) do not exist in this TDX install, so
concept/style/index/region dimensions are NOT covered locally and are
reported as empty lists with a per-stock ``quality`` note. The old
tqcenter (TQ plugin) path is removed; an opt-in ``--use-tq-fallback``
flag can still query TQ when the local industry lookup misses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE, TDX_ROOT  # noqa: E402
from code_utils import suffix  # noqa: E402

OUT_DIR = BASE / "01_data" / "holdings"
DEFAULT_POSITIONS = BASE / "01_data" / "trades" / "current_positions.json"

HQ_CACHE = TDX_ROOT / "T0002" / "hq_cache"
TDXHY_CFG = HQ_CACHE / "tdxhy.cfg"
INCON_DAT = TDX_ROOT / "incon.dat"

LOCAL_SOURCE = "local_block"
NOT_COVERED_DIMS = ["概念", "风格", "指数", "地区"]


def norm_code(x) -> str:
    # Local semantics: 6-digit zero-padding only, no exchange suffix.
    # Deliberately different from code_utils.norm_code (which appends
    # .SH/.SZ/.BJ); see code_utils.norm_code docstring. Do not merge.
    if pd.isna(x): return ""
    s = str(x).strip()
    if s.endswith(".0"): s = s[:-2]
    return s.zfill(6) if s.isdigit() and len(s) <= 6 else s


def load_tdxhy(path: Path = TDXHY_CFG) -> dict:
    """Parse tdxhy.cfg -> {code: {"tdx": T-code, "sw": X-code}}."""
    mapping = {}
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 3 and parts[1].isdigit():
            mapping[parts[1]] = {
                "tdx": parts[2] or "",
                "sw": parts[5] if len(parts) > 5 else "",
            }
    return mapping


def load_incon_sections(path: Path = INCON_DAT) -> dict:
    """Parse incon.dat -> {section: {code: name}} (GBK, ``#SECTION`` blocks)."""
    text = path.read_text(encoding="gbk", errors="replace")
    sections: dict[str, dict[str, str]] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "######":
            continue
        if line.startswith("#"):
            current = line[1:]
            sections.setdefault(current, {})
            continue
        if current and "|" in line:
            code, _, name = line.partition("|")
            if name:
                sections[current][code] = name
    return sections


def lookup_name(tree: dict, code: str) -> str:
    """Resolve an industry code against a name tree, trimming to parent."""
    code = (code or "").strip()
    while code:
        if code in tree:
            return tree[code]
        code = code[:-2]
    return ""


def init_tq():
    user_path = TDX_ROOT / "PYPlugins" / "user"
    sys.path.insert(0, str(user_path))
    from tqcenter import tq  # type: ignore
    tq.initialize(__file__)
    return tq


def tq_relation(tq, tcode: str) -> list:
    try:
        return tq.get_relation(tcode)
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_POSITIONS), help="standardized current_positions.json")
    ap.add_argument("--date", default="")
    ap.add_argument("--use-tq-fallback", action="store_true",
                    help="opt-in: query tqcenter when local industry lookup misses (default off)")
    args = ap.parse_args()
    source = Path(args.input)
    if source.suffix.lower() == ".json":
        hold = pd.DataFrame(json.loads(source.read_text(encoding="utf-8")))
    else:
        hold = pd.read_excel(source, sheet_name="持仓数据")
    hold.columns = [str(c).strip() for c in hold.columns]
    hold["代码"] = hold["代码"].map(norm_code)
    hold = hold[hold["代码"].ne("") & hold["名称"].notna() & hold["代码"].ne("汇总")].copy()

    tdxhy = load_tdxhy()
    incon = load_incon_sections()
    tdx_names = incon.get("TDXNHY", {})
    sw_names = incon.get("TDXRSHY", {})

    tq = None
    rows = []
    try:
        for _, r in hold.iterrows():
            code = r["代码"]
            tcode = code + suffix(code)
            entry = tdxhy.get(code)
            rel = []
            err = None
            if entry:
                tdx_ind = lookup_name(tdx_names, entry["tdx"])
                sw_ind = lookup_name(sw_names, entry["sw"])
                if tdx_ind:
                    rel.append({"BlockCode": entry["tdx"], "BlockName": tdx_ind,
                                "BlockType": "行业", "Source": "tdxhy.cfg"})
                if sw_ind:
                    rel.append({"BlockCode": entry["sw"], "BlockName": sw_ind,
                                "BlockType": "申万行业", "Source": "tdxhy.cfg"})
            if not rel:
                if args.use_tq_fallback:
                    if tq is None:
                        tq = init_tq()
                    rel = tq_relation(tq, tcode)
                    if rel:
                        src = "tq"
                    else:
                        src = LOCAL_SOURCE
                        err = "local tdxhy.cfg miss; tq fallback returned nothing"
                else:
                    src = LOCAL_SOURCE
                    market = "北交所" if code.startswith(("4", "8", "92")) else "A股"
                    err = f"{market}股票未在本地 tdxhy.cfg 中找到行业记录"
            else:
                src = LOCAL_SOURCE
            industries = [x for x in rel if x.get("BlockType") == "行业"]
            concepts = [x for x in rel if x.get("BlockType") == "概念"]
            styles = [x for x in rel if x.get("BlockType") == "风格"]
            indices = [x for x in rel if x.get("BlockType") == "指数"]
            rows.append({
                "code": code,
                "name": r.get("名称"),
                "tdx_code": tcode,
                "holding_amount": r.get("持有金额"),
                "holding_pnl": r.get("持有盈亏"),
                "holding_pnl_pct": r.get("持有盈亏率"),
                "position_pct": r.get("仓位占比"),
                "holding_days": r.get("持仓天数"),
                "industry": industries[0].get("BlockName") if industries else "",
                "industry_code": industries[0].get("BlockCode") if industries else "",
                "concepts": [x.get("BlockName") for x in concepts],
                "concept_codes": [x.get("BlockCode") for x in concepts],
                "styles": [x.get("BlockName") for x in styles],
                "indices": [x.get("BlockName") for x in indices],
                "relation_error": err,
                "raw_relation": rel,
                "source": src,
                "quality": {
                    "covered": ["行业"] if industries else [],
                    "not_covered": NOT_COVERED_DIMS if src == LOCAL_SOURCE else [],
                    "note": ("本地板块文件(tdxhy.cfg+incon.dat)仅覆盖行业维度;"
                             "概念/风格/指数/地区需TQ或在线数据源" if src == LOCAL_SOURCE
                             else "TQ get_relation 全量维度"),
                },
            })
    finally:
        if tq is not None:
            try: tq.close()
            except Exception as e: print(f"[WARN] tq.close() failed: {e}", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date = args.date or pd.Timestamp.now().strftime("%Y-%m-%d")
    out_json = OUT_DIR / f"{date}_holding_sector_mapping.json"
    out_csv = OUT_DIR / f"{date}_holding_sector_mapping.csv"
    out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(rows).drop(columns=["raw_relation"]).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(out_json)
    print(out_csv)
    print(pd.DataFrame(rows)[["code","name","industry","concepts","position_pct","holding_pnl_pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
