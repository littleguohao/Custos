#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据覆盖探针（TODO #91）：生产机跑**一条命令**，回答「挖掘 A（2000~2016）
数据能回溯到哪天」。

五块读数（console 逐年表 + JSON 产物，默认 ``artifacts/logs/data_coverage_probe.json``）：

  ① 0AMV 台账：confirmed 记录数 / 最早 / 最晚（复用
    ``backtest_factors._amv_ledger_records`` 的 confirmed/superseded/去重口径；
    台账缺文件 ⇒ 该块 null，不致命）；
  ② vipdoc 个股普查：文件总数、按市场（sh/sz/bj）、按板块组（沪主板 600/601/603/605、
    科创板 688/689、深主板 000/001/002/003、创业板 300/301、北交所=bj 全部）；
    每票算 first_date 与 eligible_date（第 ``--min-bars`` 根 bar 的日期 = as-of
    资格日，缺省 60 与 ``universe.UNIVERSE_MIN_BARS`` 同源；不足 N 根为 None）；
  ③ 逐年聚合（1990 起至 cap 年）：n_active（该年 ≥1 根 bar 的个股数）、
    n_eligible（该年 active 且 eligible_date ≤ 该年 12-31）；
  ④ 板块指数（vipdoc sh 目录 880/881）：总数、逐年 active、first_date p10/p50/p90
    （回答「板块覆盖稀（2011-10 前 198/587）」）；
  ⑤ ``data/market/sector_index/*.csv`` 覆盖（``--sector-filter`` 腿实际消费的数据源，
    读法同 ``sector_phase.load_sector_gate``：pd.read_csv 后取 date 列）；
    目录不存在 ⇒ 该块 null，不致命。

**前向 holdout 纪律**：≥ ``load_window.FORWARD_HOLDOUT_START``（2026-09-05）的 bar
**一律不计入任何统计**（本工具只查历史覆盖，holdout 不读）；输出 JSON 记
``holdout_cap``（= 冻结起点前一天）。

用法（生产机）::

    uv run python -m custos.research data_coverage_probe \
        --tdx-root <通达信根> --out artifacts/logs/data_coverage_probe.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from datetime import timedelta as _timedelta
from pathlib import Path
from typing import Any, Optional

# GBK（cp936）终端/管道打不了 ⚠️/⛔ 等符号 —— 不 reconfigure 会 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from custos.core.paths import LOGS, MARKET_DIR  # noqa: E402
from custos.research import load_window as _lw  # noqa: E402
from custos.research import universe as _universe  # noqa: E402

#: holdout 帽 = 前向冻结起点前一天（2026-09-04；单源 = FORWARD_HOLDOUT_START 派生）。
HOLDOUT_CAP = (
    _date.fromisoformat(_lw.FORWARD_HOLDOUT_START) - _timedelta(days=1)
).isoformat()

#: 逐年聚合起点（A 股开市年）。
FIRST_YEAR = 1990

#: vipdoc .day 二进制布局：每根 bar 32 字节 <IIIIIfII
#: （date_int YYYYMMDD、open/high/low/close 整数分、amount float32、volume、reserved）。
_DAY_DTYPE = np.dtype(
    [
        ("date", "<u4"),
        ("open", "<u4"),
        ("high", "<u4"),
        ("low", "<u4"),
        ("close", "<u4"),
        ("amount", "<f4"),
        ("volume", "<u4"),
        ("reserved", "<u4"),
    ]
)

_MARKETS = ("sh", "sz", "bj")


def _iso(date_int: int) -> str:
    """YYYYMMDD int → 'YYYY-MM-DD'。"""
    d = int(date_int)
    return f"{d // 10000:04d}-{(d // 100) % 100:02d}-{d % 100:02d}"


def _board_of(market: str, code6: str) -> str:
    """个股板块组（统计口径；调用方已用 _is_ashare_stock_file 过滤过）。"""
    if market == "bj":
        return "北交所"
    if market == "sh":
        if code6.startswith(("688", "689")):
            return "科创板"
        return "沪主板"  # 600/601/603/605（前缀集已过滤）
    if code6.startswith(("300", "301")):
        return "创业板"
    return "深主板"  # 000/001/002/003


def _day_date_ints(path: Path) -> np.ndarray:
    """读 .day 文件的 date_int 数组（升序）：date_int==0 的行跳过，尾部不足
    32 字节的残段 numpy 自动截掉。比 read_vipdoc_daily 快——不构造 DataFrame、
    不依赖全局 TDX_ROOT。"""
    arr = np.fromfile(path, dtype=_DAY_DTYPE)
    if not arr.size:
        return np.empty(0, dtype=np.uint32)
    dates = arr["date"]
    return np.sort(dates[dates != 0])


def scan_vipdoc(
    root: Path, *, min_bars: int, holdout_cap: str = HOLDOUT_CAP, quiet: bool = False
) -> dict[str, list[dict[str, Any]]]:
    """普查 vipdoc 三市场日线目录，返回 {"stocks": [...], "sector_index": [...]}。

    每票一行：code/market/board/first_date/eligible_date/n_bars/years（set）。
    个股判定单源 = ``local_tdx_data._is_ashare_stock_file``；板块指数 =
    sh 目录下 880/881 开头。**≥ holdout_cap 后一天的 bar 不进任何计数**。
    """
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    cap_int = int(holdout_cap.replace("-", ""))
    stocks: list[dict[str, Any]] = []
    sector_index: list[dict[str, Any]] = []
    n_seen = 0
    for mkt in _MARKETS:
        d = root / "vipdoc" / mkt / "lday"
        if not d.is_dir():
            continue
        for p in sorted(d.glob(f"{mkt}*.day")):
            code6 = p.stem[len(mkt) :]
            if len(code6) != 6 or not code6.isdigit():
                continue
            is_stock = local_tdx_data._is_ashare_stock_file(mkt, code6)
            is_sector = mkt == "sh" and code6.startswith(("880", "881"))
            if not (is_stock or is_sector):
                continue  # 指数/ETF/债券/B 股不进任何统计
            n_seen += 1
            if not quiet and n_seen % 1000 == 0:
                print(f"[probe] 已处理 {n_seen} 个 vipdoc 文件", file=sys.stderr)
            dates = _day_date_ints(p)
            dates = dates[dates <= cap_int]  # holdout 截断（研究纪律：不读冻结段）
            row: dict[str, Any] = {
                "code": code6,
                "market": mkt,
                "n_bars": int(dates.size),
                "first_date": _iso(dates[0]) if dates.size else None,
                "eligible_date": (
                    _iso(dates[min_bars - 1]) if dates.size >= min_bars else None
                ),
                "years": {int(v // 10000) for v in dates},
            }
            if is_stock:
                row["board"] = _board_of(mkt, code6)
                stocks.append(row)
            else:
                sector_index.append(row)
    return {"stocks": stocks, "sector_index": sector_index}


def _yearly(rows: list[dict[str, Any]], *, cap_year: int) -> list[dict[str, int]]:
    """逐年 n_active（该年 ≥1 根 bar 的条数）。个股行另计 n_eligible 由调用方加。"""
    return [
        {"year": y, "n_active": sum(1 for r in rows if y in r["years"])}
        for y in range(FIRST_YEAR, cap_year + 1)
    ]


def _percentile_dates(first_dates: list[str]) -> dict[str, Optional[str]]:
    """first_date 列表的 p10/p50/p90（按 ordinal 插值取整；空列表 ⇒ 全 None）。"""
    if not first_dates:
        return {"p10": None, "p50": None, "p90": None}
    ords = np.array([_date.fromisoformat(d).toordinal() for d in first_dates])
    return {
        f"p{p}": _date.fromordinal(
            int(round(float(np.percentile(ords, p))))
        ).isoformat()
        for p in (10, 50, 90)
    }


def aggregate_stocks(rows: list[dict[str, Any]], *, cap_year: int) -> dict[str, Any]:
    """个股普查聚合：总数/按市场/按板块组/first_date 极值/逐年 active+eligible。"""
    by_market = {m: sum(1 for r in rows if r["market"] == m) for m in _MARKETS}
    boards = ("沪主板", "科创板", "深主板", "创业板", "北交所")
    by_board = {b: sum(1 for r in rows if r["board"] == b) for b in boards}
    yearly = _yearly(rows, cap_year=cap_year)
    for row in yearly:
        dl = f"{row['year']}-12-31"
        row["n_eligible"] = sum(
            1
            for r in rows
            if row["year"] in r["years"]
            and r["eligible_date"] is not None
            and r["eligible_date"] <= dl
        )
    first_dates = [r["first_date"] for r in rows if r["first_date"]]
    return {
        "n_files": len(rows),
        "by_market": by_market,
        "by_board": by_board,
        "n_eligible": sum(1 for r in rows if r["eligible_date"] is not None),
        "first_date_min": min(first_dates) if first_dates else None,
        "first_date_max": max(first_dates) if first_dates else None,
        "yearly": yearly,
    }


def aggregate_sector_index(
    rows: list[dict[str, Any]], *, cap_year: int
) -> dict[str, Any]:
    """板块指数（vipdoc 880/881）聚合：总数/逐年 active/first_date 分位。"""
    return {
        "n_files": len(rows),
        "first_date_pct": _percentile_dates(
            [r["first_date"] for r in rows if r["first_date"]]
        ),
        "yearly": _yearly(rows, cap_year=cap_year),
    }


def amv_ledger_block(ledger_path: Optional[Path]) -> Optional[dict[str, Any]]:
    """0AMV 台账块：confirmed 记录数 + 最早/最晚。缺文件 ⇒ None + WARN（不致命）。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    path = ledger_path or (MARKET_DIR / "0amv_observations.jsonl")
    if not path.is_file():
        print(f"[WARN] 0AMV 台账缺失: {path}（该块记 null，不致命）", file=sys.stderr)
        return None
    recs = bt._amv_ledger_records("1990-01-01", None, ledger_path=path)
    return {
        "path": str(path),
        "n_confirmed": len(recs),
        "first_date": recs[0]["date"] if recs else None,
        "last_date": recs[-1]["date"] if recs else None,
    }


def sector_index_csv_block(
    index_dir: Path, *, holdout_cap: str = HOLDOUT_CAP
) -> Optional[dict[str, Any]]:
    """``data/market/sector_index/*.csv`` 覆盖块（--sector-filter 腿的消费源）。

    读法对齐 ``sector_phase.load_sector_gate``（pd.read_csv 取 date 列）；目录
    不存在 ⇒ None + WARN（不致命）；单文件损坏/缺 date 列计数进 n_bad 不 raise。
    """
    if not index_dir.is_dir():
        print(
            f"[WARN] 板块指数 CSV 目录不存在: {index_dir}（该块记 null，不致命）",
            file=sys.stderr,
        )
        return None
    n_files = 0
    n_bad = 0
    years: set[int] = set()
    date_min: Optional[str] = None
    date_max: Optional[str] = None
    yearly_sets: dict[int, int] = {}
    for p in sorted(index_dir.glob("*.csv")):
        try:
            df = pd.read_csv(p, dtype={"date": str})
            ds = [
                s
                for s in (str(v)[:10] for v in df["date"].tolist())
                if s <= holdout_cap
            ]
        except Exception:  # noqa: BLE001 — 单文件坏不拖垮整块
            n_bad += 1
            continue
        if not ds:
            continue
        n_files += 1
        lo, hi = min(ds), max(ds)
        date_min = lo if date_min is None or lo < date_min else date_min
        date_max = hi if date_max is None or hi > date_max else date_max
        fys = {int(s[:4]) for s in ds}
        years |= fys
        for y in fys:
            yearly_sets[y] = yearly_sets.get(y, 0) + 1
    return {
        "dir": str(index_dir),
        "n_files": n_files,
        "n_bad": n_bad,
        "date_min": date_min,
        "date_max": date_max,
        "yearly": [
            {"year": y, "n_files": yearly_sets.get(y, 0)}
            for y in range(FIRST_YEAR, int(holdout_cap[:4]) + 1)
        ],
    }


def _print_report(payload: dict[str, Any]) -> None:
    """console 逐年表（年 / 个股 active / 个股 eligible / 板块指数 active）+ 总结行。"""
    stocks = payload["stocks"]
    idx = payload["sector_index_vipdoc"]
    idx_active = {r["year"]: r["n_active"] for r in idx["yearly"]}
    print(
        f"\n=== 数据覆盖逐年表（个股满 {payload['min_bars']} 根 = eligible；"
        f"holdout_cap={payload['holdout_cap']}）==="
    )
    print(f"{'年':<6}{'active':>8}{'eligible':>10}{'板块idx':>9}")
    for row in stocks["yearly"]:
        y = row["year"]
        if not (row["n_active"] or idx_active.get(y)):
            continue  # 全零年份行不打印（2000 前的大段空白没有信息量）
        print(
            f"{y:<6}{row['n_active']:>8}{row['n_eligible']:>10}{idx_active.get(y, 0):>9}"
        )
    b = stocks["by_board"]
    print(
        f"\n个股 {stocks['n_files']} 只（沪主板 {b['沪主板']} / 科创板 {b['科创板']} / "
        f"深主板 {b['深主板']} / 创业板 {b['创业板']} / 北交所 {b['北交所']}），"
        f"最早 {stocks['first_date_min']}，满 {payload['min_bars']} 根 "
        f"{stocks['n_eligible']} 只"
    )
    amv = payload["amv_ledger"]
    if amv is None:
        print("0AMV 台账: 缺失（见 stderr WARN）")
    else:
        print(
            f"0AMV 台账: confirmed {amv['n_confirmed']} 条"
            f"（{amv['first_date']} ~ {amv['last_date']}）"
        )
    pct = idx["first_date_pct"]
    csv = payload["sector_index_csv"]
    csv_txt = (
        "目录缺失"
        if csv is None
        else f"{csv['n_files']} 个（{csv['date_min']} ~ {csv['date_max']}）"
    )
    print(
        f"板块指数: vipdoc 880/881 {idx['n_files']} 只"
        f"（first_date p10/p50/p90 = {pct['p10']} / {pct['p50']} / {pct['p90']}）；"
        f"sector_index csv {csv_txt}"
    )


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="数据覆盖探针（TODO #91）：vipdoc 个股/板块指数 + 0AMV 台账 + "
        "sector_index csv 的历史覆盖普查（研究产物允许 NaN；holdout 段不读）"
    )
    ap.add_argument(
        "--tdx-root",
        default="",
        help="通达信安装根目录（默认 local_tdx_data.TDX_ROOT；生产机显式传）",
    )
    ap.add_argument(
        "--amv-ledger",
        default="",
        help="0AMV 台账 jsonl 路径（默认 data/market/0amv_observations.jsonl）",
    )
    ap.add_argument(
        "--sector-index-dir",
        default=str(MARKET_DIR / "sector_index"),
        help="板块指数 CSV 目录（--sector-filter 腿的数据源；缺 ⇒ 该块 null）",
    )
    ap.add_argument(
        "--min-bars",
        type=int,
        default=_universe.UNIVERSE_MIN_BARS,
        help=f"as-of 资格根数（默认 {_universe.UNIVERSE_MIN_BARS}，"
        "与 universe.UNIVERSE_MIN_BARS 同源）",
    )
    ap.add_argument(
        "--out",
        default=str(LOGS / "data_coverage_probe.json"),
        help="JSON 产物路径（默认 artifacts/logs/data_coverage_probe.json）",
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.min_bars <= 0:
        print("[FAIL] --min-bars 必须是正整数", file=sys.stderr)
        return 2
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    root = Path(args.tdx_root) if args.tdx_root else local_tdx_data.TDX_ROOT
    try:
        local_tdx_data._assert_tdx_root(root)
    except Exception as exc:  # noqa: BLE001 — 配错=护栏非零退出（不落盘）
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    scanned = scan_vipdoc(root, min_bars=args.min_bars)
    stocks_rows = scanned["stocks"]
    idx_rows = scanned["sector_index"]
    if not stocks_rows and not idx_rows:
        # 空结果护栏：vipdoc 里一个可用文件都没有 ⇒ 非零退出且不写产物
        print(
            f"[FAIL] {root}/vipdoc 下 0 个可用日线文件（个股 0 / 板块指数 0）——"
            "空结果不写产物",
            file=sys.stderr,
        )
        return 2

    cap_year = int(HOLDOUT_CAP[:4])
    payload: dict[str, Any] = {
        "tool": "data_coverage_probe",
        "generated_at": _date.today().isoformat(),
        "tdx_root": str(root),
        "min_bars": args.min_bars,
        "holdout_cap": HOLDOUT_CAP,
        "amv_ledger": amv_ledger_block(
            Path(args.amv_ledger) if args.amv_ledger else None
        ),
        "stocks": aggregate_stocks(stocks_rows, cap_year=cap_year),
        "sector_index_vipdoc": aggregate_sector_index(idx_rows, cap_year=cap_year),
        "sector_index_csv": sector_index_csv_block(Path(args.sector_index_dir)),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"[OK] 写出 {out}")
    _print_report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
