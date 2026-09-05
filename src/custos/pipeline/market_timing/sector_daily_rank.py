# -*- coding: utf-8 -*-
"""板块每日涨跌幅榜采集器（#26 第一步）。纯本地数据、确定性、可回填。

输入（全部本地）：
    data/market/sector_index/{code}.csv       板块指数日线（fetch_sector_index_history 落盘，date,close）
    data/market/sector_members.json           板块 → 成员股票
    tdxzs3.cfg（tq_sector.load_sector_names）  板块名称/类型（2=行业 3=地区 4=概念 5=风格 12=细分行业）
    data/market/{date}_fund_flow_rank.json    资金流快照（可选，缺失降级为 null）
    vipdoc 未复权日线                          涨跌停判定（⚠️ 涨跌停按**前收未复权**算，禁用完 qfq 价）

输出（out-dir 默认 data/sectors/daily_rank/）：
    {date}.json         当日涨幅榜/跌幅榜（temp+os.replace 原子写）
    sector_active.json  活跃名单：最近 W 个交易日上榜 ≥K 次的板块（每个产出日按可得历史刷新）

单日模式 ``--date``；回填模式 ``--start/--end`` 逐日历日尝试，无指数数据的日期跳过并计数，
幂等可重跑（输出只依赖输入数据，重写同一份文件）。
"""

from __future__ import annotations

import csv
import sys
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Callable, Optional

from custos.core.code_utils import price_limit_pct
from custos.core.factors.sector_mainstream import invert_members
from custos.core.paths import (
    MARKET_DIR,
    SECTOR_INDEX_DIR,
    SECTOR_MEMBERS_FILE,
    SECTORS_DIR,
    cn_now,
    cn_today,
    read_json,
    write_json_atomic,
)

DEFAULT_INCLUDE_TYPES = (
    "2",
    "4",
)  # 行业 + 概念；地区(3)/风格(5)/细分行业(12) 默认不进宇宙
ACTIVE_FILE = "sector_active.json"
ST_LIMIT_PCT = 5.0  # 主板 ST 股涨跌幅 5%（创业板/科创板 ST 仍 20%，北交所无 ST 制度）

# 个股日线读取入口（⚠️ 返回**未复权** date/close 序列）。测试用假数据 monkeypatch 它。
ReadDaily = Callable[[str], list]


@dataclass
class Ctx:
    """单日计算的全部输入上下文；测试直接构造注入合成数据。"""

    index_dir: Path
    market_dir: Path
    members: dict  # {sector_code: [股票代码]}
    code2secs: dict  # invert_members 反转：股票 → [板块]
    name_map: dict  # {sector_code: {"name","tdx_type"}}
    stock_names: dict  # {股票代码: 名称}（ST 判定用；可为空）
    universe: list  # 板块宇宙（已按 include_types 过滤、排序）
    include_types: tuple = DEFAULT_INCLUDE_TYPES
    read_daily: Optional[ReadDaily] = None  # None → 用 vipdoc 未复权读取
    _index_cache: dict = field(default_factory=dict)  # sector → [(date, close)]
    _bars_cache: dict = field(
        default_factory=dict
    )  # code → ([(date,close)], {date: idx})


# ---------------------------------------------------------------------------
# 数据读取
# ---------------------------------------------------------------------------


def read_close_series(csv_path: Path) -> list:
    """读板块指数 CSV → [(date, close)] 升序。损坏/无列返回 []。"""
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rows = [
                (str(r.get("date") or "")[:10], float(r["close"]))
                for r in csv.DictReader(f)
                if r.get("date") and r.get("close") not in (None, "")
            ]
    except (OSError, ValueError):
        return []
    rows.sort()
    return rows


def _index_series(ctx: Ctx, sector: str) -> list:
    if sector not in ctx._index_cache:
        d = ctx.index_dir
        path = d / f"{sector}.csv"
        if not path.exists():
            # fetch_sector_index_history 落盘的是带后缀名：{code}.SH.csv
            alt = sorted(d.glob(f"{sector}.*.csv"))
            path = alt[0] if alt else path
        ctx._index_cache[sector] = read_close_series(path)
    return ctx._index_cache[sector]


def _read_stock_daily(code: str) -> list:
    """vipdoc 未复权日线 → [(date, close)] 升序。读不到返回 []。

    ⚠️ 必须是**未复权**价：涨跌停幅度按前收未复权计算，用 qfq 价会把
    除权后的涨停误判/漏判。``read_vipdoc_daily`` 直读 .day 原始价，不经过复权。
    """
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    try:
        df = local_tdx_data.read_vipdoc_daily(code)
    except Exception:  # noqa: BLE001 —— 单票缺失不拖垮整榜，记入 missing_stock_bars
        return []
    if df is None or df.empty:
        return []
    rows = sorted(
        (str(d)[:10], float(c)) for d, c in zip(df["date"], df["close"], strict=True)
    )
    return rows


def _stock_bars(ctx: Ctx, code: str) -> tuple:
    """(bars, {date: idx})，按 code 缓存 —— 回填区间每只股票只读一次盘。"""
    if code not in ctx._bars_cache:
        reader = ctx.read_daily or _read_stock_daily
        bars = reader(code) or []
        ctx._bars_cache[code] = (bars, {d: i for i, (d, _c) in enumerate(bars)})
    return ctx._bars_cache[code]


def _load_fund_flow(market_dir: Path, date: str) -> tuple:
    """资金流快照按板块名聚合主力净流入 → (by_name, available)。

    与 ``enrich_candidates._agg_sector_fund_flow`` 同口径（concept + industry 按名
    合并）；它在 screening/（同层）但拖 1900 行依赖，就地实现这几行更干净。
    """
    path = Path(market_dir) / f"{date}_fund_flow_rank.json"
    if not path.exists():
        return {}, False
    data = read_json(path, {})
    sector_rank = data.get("sector_rank") or {}
    by_name: dict = {}
    for item in (sector_rank.get("concept") or []) + (
        sector_rank.get("industry") or []
    ):
        nm = str(item.get("name") or "")
        v = item.get("main_net_inflow")
        if nm and isinstance(v, (int, float)):
            by_name[nm] = by_name.get(nm, 0.0) + v
    return by_name, True


# ---------------------------------------------------------------------------
# 逐日计算
# ---------------------------------------------------------------------------


def discover_universe(index_dir: Path, name_map: dict, include_types) -> list:
    """板块宇宙 = 指数 CSV 存在 ∩ 名称表 tdx_type ∈ include_types（默认 2 行业/4 概念）。"""
    types = set(include_types)
    out = []
    for p in Path(index_dir).glob("*.csv"):
        code = p.name.split(".")[0]
        if str((name_map.get(code) or {}).get("tdx_type") or "") in types:
            out.append(code)
    return sorted(set(out))


def pct_on(series: list, date: str) -> Optional[float]:
    """当日涨跌幅 = close/prev_close − 1（prev = 该板块 CSV 内前一个交易日）。无则 None。"""
    idx = {d: i for i, (d, _c) in enumerate(series)}.get(date)
    if idx is None or idx == 0:
        return None
    prev, close = series[idx - 1][1], series[idx][1]
    return close / prev - 1 if prev > 0 else None


def _limit_pct(code: str, stock_names: dict) -> float:
    """涨跌幅限制：price_limit_pct 为唯一前缀口径来源；主板 ST 收敛到 5%。

    ⚠️ price_limit_pct 不含 ST 规则（ST 是名称状态不是代码属性），这里按
    「名称含 ST 且主板 10% 档 → 5%」补一层；创业板/科创板 ST 仍 20%（base≠10 不动），
    北交所无 ST 制度。
    """
    base = price_limit_pct(code)
    if base == 10.0 and "ST" in str(stock_names.get(code, "")).upper():
        return ST_LIMIT_PCT
    return base


def limit_counts(ctx: Ctx, date: str) -> tuple:
    """涨/跌停家数 → ({sector: [up, down]}, 缺数据股票列表)。

    ⚠️ 每只股票**只算一次**当日涨跌（未复权当日 close vs 前一交易日 close），
    再按 code2secs 聚合到板块 —— 不按板块×成员重复读盘、不重复计数。
    判定：close ≥ round2(前收 × (1 + limit/100)) 为涨停（跌停镜像）。
    """
    uni = set(ctx.universe)
    stocks = sorted({c for c, secs in ctx.code2secs.items() if uni.intersection(secs)})
    counts: dict = {}
    missing: list = []
    for code in stocks:
        bars, pos = _stock_bars(ctx, code)
        idx = pos.get(date)
        if idx is None or idx == 0 or bars[idx - 1][1] <= 0:
            missing.append(code)
            continue
        prev, close = bars[idx - 1][1], bars[idx][1]
        lim = _limit_pct(code, ctx.stock_names) / 100.0
        up = close >= round(prev * (1 + lim), 2)
        down = close <= round(prev * (1 - lim), 2)
        if not (up or down):
            continue
        for sec in ctx.code2secs[code]:
            if sec not in uni:
                continue
            c = counts.setdefault(sec, [0, 0])
            c[0] += int(up)
            c[1] += int(down)
    return counts, missing


def _entry(rank: int, code: str, pct: float, ctx: Ctx, counts: dict, ff: dict) -> dict:
    info = ctx.name_map.get(code) or {}
    name = str(info.get("name") or code)
    up, down = counts.get(code, (0, 0))
    return {
        "rank": rank,
        "code": code,
        "name": name,
        "tdx_type": info.get("tdx_type"),
        "pct": round(pct * 100, 2),  # 百分比：1.23 = +1.23%
        "limit_up_count": up,
        "limit_down_count": down,
        "main_net_inflow": ff.get(name),  # 按板块名精确匹配；缺资金流/未命中 → null
    }


def build_day(date: str, ctx: Ctx, top: int = 10) -> Optional[dict]:
    """单日榜 payload；当日**无任何**板块指数数据 → None（回填模式据此跳过并计数）。"""
    rows, missing_index = [], []
    for sec in ctx.universe:
        pct = pct_on(_index_series(ctx, sec), date)
        if pct is None:
            missing_index.append(sec)
        else:
            rows.append((sec, pct))
    if not rows:
        return None
    counts, missing_stocks = limit_counts(ctx, date)
    ff, ff_ok = _load_fund_flow(ctx.market_dir, date)
    gainers = sorted(rows, key=lambda x: (-x[1], x[0]))[:top]
    losers = sorted(rows, key=lambda x: (x[1], x[0]))[:top]
    return {
        "date": date,
        "generated_at": cn_now().isoformat(timespec="seconds"),
        "universe": {
            "sectors_total": len(ctx.universe),
            "types": sorted(ctx.include_types),
        },
        "gainers_top": [
            _entry(i + 1, sec, pct, ctx, counts, ff)
            for i, (sec, pct) in enumerate(gainers)
        ],
        "losers_top": [
            _entry(i + 1, sec, pct, ctx, counts, ff)
            for i, (sec, pct) in enumerate(losers)
        ],
        "data_quality": {
            "missing_index": missing_index,  # 宇宙内当日无指数数据的板块
            "missing_members": [
                s for s in ctx.universe if not ctx.members.get(s)
            ],  # 宇宙内无成员映射的板块
            "missing_stock_bars": missing_stocks,  # 当日读不到未复权日线的股票
            "fund_flow": "ok" if ff_ok else "missing",
        },
    }


# ---------------------------------------------------------------------------
# 活跃名单
# ---------------------------------------------------------------------------


def refresh_active(out_dir: Path, date: str, window: int, min_hits: int) -> dict:
    """读 out-dir 最近 ``window`` 个 ≤date 的榜文件，上榜 ≥``min_hits`` 次 → sector_active.json。

    每个产出日都按**当时可得**历史刷新：回填模式下逐日推进时，每个日期只统计
    ≤自身的榜文件（同日重跑幂等 —— 输入文件相同则输出相同）。
    """
    files = sorted(
        p
        for p in Path(out_dir).glob("????-??-??.json")
        if p.stem <= date and p.name != ACTIVE_FILE
    )[-window:]
    hits: dict = {}
    last_seen: dict = {}
    info: dict = {}
    for p in files:
        day = read_json(p, {})
        on_board = set()
        for e in (day.get("gainers_top") or []) + (day.get("losers_top") or []):
            code = str(e.get("code") or "")
            if code:
                on_board.add(code)
                info[code] = e
        for code in on_board:  # 同一天涨/跌榜都中也只记 1 次
            hits[code] = hits.get(code, 0) + 1
            last_seen[code] = p.stem
    active = [
        {
            "code": c,
            "name": str(info[c].get("name") or c),
            "tdx_type": info[c].get("tdx_type"),
            "hits": hits[c],
            "last_seen": last_seen[c],
        }
        for c in sorted(hits, key=lambda c: (-hits[c], c))
        if hits[c] >= min_hits
    ]
    payload = {
        "date": date,
        "generated_at": cn_now().isoformat(timespec="seconds"),
        "window": window,
        "min_hits": min_hits,
        "files_used": len(files),
        "active": active,
    }
    # ⚠️ sector_active.json 全仓零读者（v0.158 排查），owner 未拍板去留，
    #    暂随主产物保留 —— 每个产出日照常刷新，但不接入任何下游。
    write_json_atomic(Path(out_dir) / ACTIVE_FILE, payload)
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _calendar_days(start: str, end: str) -> list:
    """[start, end] 逐**日历日**；非交易日/缺数据日由 build_day 返回 None 跳过。"""
    s, e = _date.fromisoformat(start), _date.fromisoformat(end)
    days = []
    while s <= e:
        days.append(s.isoformat())
        s += timedelta(days=1)
    return days


def run_dates(
    dates: list, ctx: Ctx, out_dir: Path, top: int, window: int, min_hits: int
) -> tuple:
    """逐日产出榜 + 刷新活跃名单 → (written, skipped)。幂等：重跑重写同内容文件。"""
    out_dir = Path(out_dir)
    written, skipped = 0, 0
    for d in dates:
        payload = build_day(d, ctx, top=top)
        if payload is None:
            skipped += 1
            continue
        write_json_atomic(out_dir / f"{d}.json", payload)
        refresh_active(out_dir, d, window, min_hits)
        written += 1
    return written, skipped


def _load_sector_name_map() -> dict:
    from custos.datasource.local_tdx import tq_sector  # noqa: PLC0415

    try:
        return tq_sector.load_sector_names() or {}
    except Exception:  # noqa: BLE001
        print("[WARN] 板块名称表(tdxzs.cfg)不可用，宇宙将为空", file=sys.stderr)
        return {}


def normalize_member_keys(members: dict) -> dict:
    """sector_members.json 的键归一为裸码（``880431.SH`` → ``880431``）。

    历史文件键带 .SH 后缀，而宇宙/members.get 全用裸码 —— 不归一则
    data_quality.missing_members 恒为全宇宙、code2secs 与宇宙对不上、
    涨跌停家数恒 0（2026-09-04 榜一猪肉 +6.01% limit_up_count=0 即此 bug）。
    """
    return {str(k).strip().upper().split(".")[0]: v for k, v in (members or {}).items()}


def _load_stock_names() -> dict:
    from custos.datasource.local_tdx import stock_names  # noqa: PLC0415

    names, meta = stock_names.load_cache()
    if not meta.get("available"):
        print(
            f"[WARN] 股票名称缓存不可用({meta.get('reason')})，ST 5% 档失效按 10% 判",
            file=sys.stderr,
        )
    return names


def _default_ctx(args: Namespace) -> Ctx:
    name_map = _load_sector_name_map()
    members = normalize_member_keys(read_json(SECTOR_MEMBERS_FILE, {}))
    include_types = tuple(
        t.strip() for t in str(args.include_types).split(",") if t.strip()
    )
    universe = discover_universe(SECTOR_INDEX_DIR, name_map, include_types)
    return Ctx(
        index_dir=SECTOR_INDEX_DIR,
        market_dir=MARKET_DIR,
        members=members,
        code2secs=invert_members(members, exclude_types=True, name_map=name_map),
        name_map=name_map,
        stock_names=_load_stock_names(),
        universe=universe,
        include_types=include_types,
    )


def _build_parser() -> ArgumentParser:
    ap = ArgumentParser(
        description="板块每日涨跌幅榜采集器（纯本地、可回填）："
        "涨幅/跌幅 top N + 涨跌停家数 + 主力净流入 + 活跃名单"
    )
    ap.add_argument("--date", help="单日模式 YYYY-MM-DD（默认今天）")
    ap.add_argument("--start", help="回填起点 YYYY-MM-DD（与 --end 同用）")
    ap.add_argument("--end", help="回填终点 YYYY-MM-DD（默认今天）")
    ap.add_argument("--top", type=int, default=10, help="涨/跌幅榜各取前 N（默认 10）")
    ap.add_argument("--out-dir", help="输出目录（默认 data/sectors/daily_rank）")
    ap.add_argument(
        "--active-window",
        type=int,
        default=40,
        help="活跃名单统计窗口（交易日，默认 40）",
    )
    ap.add_argument(
        "--active-min-hits", type=int, default=2, help="活跃名单上榜次数门槛（默认 2）"
    )
    ap.add_argument(
        "--include-types",
        default=",".join(DEFAULT_INCLUDE_TYPES),
        help="板块类型白名单（逗号分隔，默认 2,4 = 行业+概念；加 12 纳入细分行业）",
    )
    return ap


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if args.start:
        dates = _calendar_days(args.start, args.end or cn_today().isoformat())
    else:
        dates = [args.date or cn_today().isoformat()]
    out_dir = Path(args.out_dir) if args.out_dir else SECTORS_DIR / "daily_rank"
    ctx = _default_ctx(args)
    if not ctx.universe:
        print("[ERR] 板块宇宙为空（指数 CSV 或名称表缺失）", file=sys.stderr)
        return 2
    written, skipped = run_dates(
        dates, ctx, out_dir, args.top, args.active_window, args.active_min_hits
    )
    print(
        f"[OK] 板块榜产出 {written} 日（跳过无数据 {skipped} 日，"
        f"宇宙 {len(ctx.universe)} 板块）→ {out_dir}"
    )
    return 0 if written else 2


if __name__ == "__main__":
    raise SystemExit(main())
