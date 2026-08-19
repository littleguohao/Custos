# -*- coding: utf-8 -*-
"""#26 验证研究：「近期多次上榜（资金流入）板块里的 J<13 股票，后续表现是否优于未命中的」。

## 问题

`pipeline/market_timing/sector_daily_rank.py` 逐日产出 `data/sectors/daily_rank/{date}.json`
（涨幅榜 gainers_top / 跌幅榜 losers_top）。本脚本**只读这些落盘文件**与本地 vipdoc 日线，
不依赖生产链代码，回答：活跃板块（近 W 个有榜交易日涨幅榜 top 上榜 ≥K 次）里的 J<13 股票，
t → t+H 的 forward 收益是否显著优于 J<13 池内未命中的股票。

## 口径（全部写进输出 JSON，可复现）

- **复权**：**qfq 前复权**（`get_ohlcv_table` 默认）。与回测 `_load_bars_local`、
  live 全链「统一前复权」（owner 2026-08-04 拍板）同口径——未复权会把除权跳空算成
  真实涨跌，污染 forward 收益与 J 值。全研究统一 qfq 一种。
- **J 值**：`core/indicators.j_series`（唯一来源），**全历史算一次按索引 as-of 取值**——
  KDJ 的 EWM 递归是因果序列，全序列第 i 根 == 前缀切片重算的第 i 根（v0.68 预计算模式），
  不逐日前缀重算。阈值默认 `core/b1_thresholds.J_LOW_THRESHOLD`（=13，live 1800 池门槛）。
- **活跃板块**：t 日（含）之前**已落盘**的近 W 个有榜交易日里，gainers_top 上榜 ≥K 次的
  板块。板块代码按 `.` 前缀归一后与 `invert_members` 的反转口径求交（地区/风格板块已被
  `invert_members(exclude_types=True)` 剔除，天然不会命中）。
- **forward 收益**：t 收盘 → t+H 收盘（该股自身交易日序的第 i+H 根；停牌跳过该日）。
  尾部数据不足的 (t, H) 样本**如实剔除并计数**（`tail_excluded`）。
- **分组**：J<13 池内，个股板块族（`invert_members` 反转，exclude_types 与 1800 链同口径）
  ∩ 活跃集非空 → **命中组**；其余 → **未命中组**；全池 = 两者合并。
- **跨窗稳定性**：按 t 把研究窗口切成前/后两半各算一遍（R10 教训：edge 集中在单一
  regime 的方案不算数）。
- **regime 过滤（可选）**：`--amv-regimes 做多,中性` 只统计指定 0AMV regime 的交易日
  （live 状态机口径，`data/market/0amv_regime_history.json`，as-of 取 ≤ 当日最近一条
  `effective_state`；无前置记录记「未知」且必被过滤）。活跃板块的形成不区分 regime，
  过滤只作用于统计日；前/后两半仍按全日历位置划分（被过滤日不贡献样本）。

## 无未来函数

t 日的分组只用 ≤t 的 daily_rank 文件与 ≤t 的 K 线（J 为因果序列 as-of 取值）；
forward 收益是 t 之后的价格，仅作标签不参与分组。

用法::

    uv run python src/custos/research/sector_inflow_study.py --days 120 --window 40 --min-hits 2
    # 排障限定宇宙:
    uv run python src/custos/research/sector_inflow_study.py --codes-file /tmp/codes.txt
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from custos.core.b1_thresholds import J_LOW_THRESHOLD
from custos.core.indicators import j_series
from custos.core.paths import ARTIFACT_LOGS, MARKET_DIR, SECTORS_DIR, cn_today

DEFAULT_RANK_DIR = SECTORS_DIR / "daily_rank"
DEFAULT_MEMBERS = MARKET_DIR / "sector_members.json"
# 加载窗口在研究窗口之外多留的缓冲：J 的 EWM 需要收敛历史 + forward H 根标签。
WARMUP_BARS = 120

GROUPS = ("hit", "miss", "pool")


def _norm_sec(code: Any) -> str:
    """板块代码归一：取 `.` 前缀（"880545.SH" → "880545"），与 members 键格式解耦。"""
    return str(code).split(".")[0]


def load_rank_files(rank_dir: Path) -> dict[str, set[str]]:
    """读 daily_rank 目录 → {date: 当日 gainers_top 板块代码集（归一）}。坏文件跳过并 WARN。"""
    out: dict[str, set[str]] = {}
    d = Path(rank_dir)
    if not d.is_dir():
        print(f"[WARN] daily_rank 目录不存在: {d}", file=sys.stderr)
        return out
    for p in sorted(d.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            date = str(payload.get("date") or p.stem)
            secs = {
                _norm_sec(e.get("code", ""))
                for e in (payload.get("gainers_top") or [])
                if e.get("code")
            }
            secs.discard("")
            out[date] = secs
        except Exception as exc:  # noqa: BLE001 —— 单日坏文件不该废掉整轮研究
            print(f"[WARN] daily_rank 文件不可读 {p.name}: {exc}", file=sys.stderr)
    return out


def iter_active_sets(
    trade_days: list[str],
    rank_by_date: dict[str, set[str]],
    window: int,
    min_hits: int,
) -> Iterable[tuple[str, set[str]]]:
    """逐交易日产出 (t, 活跃板块集)：t（含）之前近 W 个有榜日上榜 ≥min_hits 次的板块。

    滑动计数：随 t 前进而把新落入 ≤t 的榜日加入、把滑出最近 W 个榜日的剔除，
    O(榜日总数 + 窗口交易日数)。只用到 ≤t 的文件，as-of 无未来函数。
    """
    rank_dates = sorted(rank_by_date)
    counts: dict[str, int] = {}
    kept: deque[str] = deque()  # 当前窗口内的榜日（升序，≤ 当前 t）
    ptr = 0
    for t in trade_days:
        while ptr < len(rank_dates) and rank_dates[ptr] <= t:
            d = rank_dates[ptr]
            kept.append(d)
            for s in rank_by_date[d]:
                counts[s] = counts.get(s, 0) + 1
            ptr += 1
        while len(kept) > window:
            old = kept.popleft()
            for s in rank_by_date[old]:
                counts[s] -= 1
                if counts[s] <= 0:
                    del counts[s]
        yield t, {s for s, c in counts.items() if c >= min_hits}


def _stat_block(rets: list[float]) -> dict[str, Any]:
    """一组 forward 收益的统计块；空组只给 {"n": 0}（缺失不得写成 0）。"""
    if not rets:
        return {"n": 0}
    arr = np.asarray(rets, dtype=float)
    return {
        "n": len(rets),
        "win_rate": round(float((arr > 0).mean()), 4),
        "mean": round(float(arr.mean()), 4),
        "median": round(float(np.median(arr)), 4),
        "p25": round(float(np.percentile(arr, 25)), 4),
        "p75": round(float(np.percentile(arr, 75)), 4),
    }


def _new_acc(horizons: list[int]) -> dict[int, dict[str, list[float]]]:
    return {h: {g: [] for g in GROUPS} for h in horizons}


def _accumulate(
    acc: dict[int, dict[str, list[float]]],
    hit: bool,
    fwd: dict[int, float],
) -> None:
    for h, r in fwd.items():
        acc[h]["hit" if hit else "miss"].append(r)
        acc[h]["pool"].append(r)


def _summarize_acc(
    acc: dict[int, dict[str, list[float]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """累加器 → {horizon: {group: 统计块}}，并附 hit−miss 的 mean 差（lift）。"""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for h, groups in acc.items():
        block: dict[str, Any] = {g: _stat_block(groups[g]) for g in GROUPS}
        hit_n, miss_n = block["hit"].get("n"), block["miss"].get("n")
        block["lift_hit_minus_miss_mean"] = (
            round(block["hit"]["mean"] - block["miss"]["mean"], 4)
            if hit_n and miss_n
            else None
        )
        out[str(h)] = block
    return out


# 逐股预处理产物：(date→索引, 收盘序列, J 全序列)。J 是因果 EWM 递归，
# 全序列第 i 根 == 前缀切片重算的第 i 根，故全历史算一次按索引 as-of 取。
_Prepared = tuple[dict[str, int], list[float], list[float]]


def _prepare_bars(bars: dict[str, pd.DataFrame]) -> dict[str, _Prepared]:
    """逐股预处理一次：date→索引、收盘序列、J 全序列（NaN 保留：早期不算 J<13）。"""
    prepared: dict[str, _Prepared] = {}
    for code, raw in bars.items():
        if raw is None or not len(raw):
            continue
        df = raw.sort_values("date").reset_index(drop=True)
        ds = [str(d)[:10] for d in df["date"]]
        idx_of = {d: i for i, d in enumerate(ds)}
        closes = df["close"].astype(float).tolist()
        js = j_series(df).tolist()
        prepared[code] = (idx_of, closes, js)
    return prepared


def _forward_returns(
    closes: list[float],
    i: int,
    horizons: list[int],
    tail_excluded: dict[str, int],
) -> dict[int, float]:
    """单样本的 forward 收益 {h: 收益}；数据不足的 (t,H) 计入 tail_excluded 剔除。"""
    fwd: dict[int, float] = {}
    for h in horizons:
        j = i + h
        if j < len(closes) and closes[i]:
            fwd[h] = closes[j] / closes[i] - 1
        else:
            tail_excluded[str(h)] += 1
    return fwd


def _process_day(
    t: str,
    active: set[str],
    prepared: dict[str, _Prepared],
    code2secs: dict[str, list[str]],
    horizons: list[int],
    j_threshold: float,
    day_half: dict[str, str],
    acc: dict[str, dict[int, dict[str, list[float]]]],
    tail_excluded: dict[str, int],
) -> tuple[int, int]:
    """单日横截面：J<13 池分组（命中/未命中）+ forward 收益累加。返回 (池样本数, 命中数)。"""
    day_n = 0
    day_hit = 0
    for code, (idx_of, closes, js) in prepared.items():
        i = idx_of.get(t)
        if i is None:  # 当日停牌/无数据 → 该股不参与当日分组
            continue
        jv = js[i]
        if not (jv == jv) or jv >= j_threshold:  # NaN 或不在池内
            continue
        secs = {_norm_sec(s) for s in code2secs.get(code, [])}
        hit = bool(secs & active)
        fwd = _forward_returns(closes, i, horizons, tail_excluded)
        half = day_half[t]
        _accumulate(acc["overall"], hit, fwd)
        _accumulate(acc[f"{half}_half"], hit, fwd)
        day_n += 1
        day_hit += int(hit)
    return day_n, day_hit


def _resolve_day_regimes(trade_days: list[str], hist: dict[str, Any]) -> dict[str, str]:
    """逐交易日 as-of 解析 0AMV regime（`0amv_regime_history.json`，live 状态机口径）。

    取 ≤ 当日最近一条记录的 `effective_state`；无任何前置记录 → "未知"。
    """
    dates = sorted(hist)
    out: dict[str, str] = {}
    for d in trade_days:
        i = bisect.bisect_right(dates, d) - 1
        out[d] = (
            str(hist[dates[i]].get("effective_state") or "未知") if i >= 0 else "未知"
        )
    return out


def run_study(
    bars: dict[str, pd.DataFrame],
    rank_by_date: dict[str, set[str]],
    code2secs: dict[str, list[str]],
    trade_days: list[str],
    horizons: list[int],
    window: int = 40,
    min_hits: int = 2,
    j_threshold: float = J_LOW_THRESHOLD,
    day_regime: Optional[dict[str, str]] = None,
    regime_allow: Optional[set[str]] = None,
) -> dict[str, Any]:
    """主研究（纯函数，数据全部注入；测试走合成 fixture 不经 IO）。

    bars: {code6: df[date,high,low,close,...]}（已按目标复权口径，升序与否均可）。
    trade_days: 研究窗口交易日（升序）。返回样本计数/尾部剔除/总体与前后两半统计。
    regime_allow 非 None 时只统计 `day_regime[t] ∈ regime_allow` 的交易日
    （活跃板块形成不受过滤影响；两半仍按全日历位置划分，被过滤日不贡献样本）。
    """
    horizons = sorted(int(h) for h in horizons)
    max_h = max(horizons) if horizons else 0
    mid = len(trade_days) // 2
    day_half = {d: ("first" if i < mid else "second") for i, d in enumerate(trade_days)}

    acc: dict[str, dict[int, dict[str, list[float]]]] = {
        "overall": _new_acc(horizons),
        "first_half": _new_acc(horizons),
        "second_half": _new_acc(horizons),
    }
    tail_excluded = {str(h): 0 for h in horizons}
    per_day: dict[str, dict[str, Any]] = {}
    n_pool_samples = 0
    prepared = _prepare_bars(bars)
    regime_filtered = 0

    for t, active in iter_active_sets(trade_days, rank_by_date, window, min_hits):
        if (
            regime_allow is not None
            and (day_regime or {}).get(t, "未知") not in regime_allow
        ):
            regime_filtered += 1
            continue
        day_n, day_hit = _process_day(
            t,
            active,
            prepared,
            code2secs,
            horizons,
            j_threshold,
            day_half,
            acc,
            tail_excluded,
        )
        n_pool_samples += day_n
        per_day[t] = {
            "n_pool": day_n,
            "n_hit": day_hit,
            "n_active_sectors": len(active),
        }

    return {
        "n_trade_days": len(trade_days),
        "n_pool_samples": n_pool_samples,
        "tail_excluded": tail_excluded,
        "max_horizon": max_h,
        "regime_filter": (
            {"allow": sorted(regime_allow), "days_filtered": regime_filtered}
            if regime_allow is not None
            else None
        ),
        "per_day": per_day,
        "overall": _summarize_acc(acc["overall"]),
        "first_half": _summarize_acc(acc["first_half"]),
        "second_half": _summarize_acc(acc["second_half"]),
    }


def _resolve_universe(codes_file: str) -> list[str]:
    """宇宙：--codes-file 钉死（排障）；默认本地 vipdoc 全宇宙（A 股个股）。"""
    if codes_file:
        p = Path(codes_file)
        if not p.is_file():
            raise SystemExit(f"[ERR] --codes-file 不存在: {p}")
        codes = [
            ln.strip()[:6]
            for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        print(f"[INFO] universe=codes_file({p.name}) {len(codes)} 只", file=sys.stderr)
        return codes
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    codes = local_tdx_data.list_local_vipdoc_codes()
    print(f"[INFO] universe=vipdoc 全宇宙 {len(codes)} 只", file=sys.stderr)
    return codes


def _load_bars(codes: list[str], count: int) -> dict[str, pd.DataFrame]:
    """CLI 用：经 local_tdx 读 qfq 日线（研究/回测/live 统一前复权口径）。"""
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    local_tdx_data.reset_qfq_failure_stats()
    out: dict[str, pd.DataFrame] = {}
    n = 0
    for c in codes:
        n += 1
        try:
            df = local_tdx_data.get_ohlcv_table(c, count=count)  # adjust="qfq" 默认
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 加载 {c} 失败: {exc}", file=sys.stderr)
            continue
        if df is not None and len(df):
            out[str(c)[:6]] = df
        if n % 500 == 0:
            print(f"[load] {n}/{len(codes)}", file=sys.stderr, flush=True)
    stats = local_tdx_data.qfq_failure_stats()
    if stats["count"]:
        print(
            f"[WARN] 前复权失败 {stats['count']}/{len(codes)} 只，按未复权使用",
            file=sys.stderr,
        )
    return out


def _load_code2secs(members_path: Path) -> dict[str, list[str]]:
    """sector_members.json → code6 → [板块代码]（invert_members 反转，剔地区/风格同 1800 链）。"""
    from custos.core.factors import sector_mainstream as sm  # noqa: PLC0415

    p = Path(members_path)
    if not p.is_file():
        print(
            f"[WARN] sector_members 不存在: {p} —— 命中组将恒为空（先跑 "
            "fetch_sector_index_history.py --members）",
            file=sys.stderr,
        )
        return {}
    members = json.loads(p.read_text(encoding="utf-8"))
    return sm.invert_members(members, exclude_types=True)


def _render_text(result: dict[str, Any], horizons: list[int]) -> str:
    lines = [
        f"窗口 {result['window']['start']} ~ {result['window']['end']} "
        f"({result['n_trade_days']} 交易日), J<13 池样本 {result['n_pool_samples']} "
        f"(尾部剔除 {result['tail_excluded']})",
    ]
    for h in horizons:
        blk = result["overall"][str(h)]
        hit, miss = blk["hit"], blk["miss"]
        lift = blk["lift_hit_minus_miss_mean"]
        lines.append(
            f"  H={h}: 命中 n={hit.get('n', 0)} mean {(hit.get('mean') or 0) * 100:+.2f}% "
            f"胜率 {(hit.get('win_rate') or 0) * 100:.1f}% | "
            f"未命中 n={miss.get('n', 0)} mean {(miss.get('mean') or 0) * 100:+.2f}% "
            f"胜率 {(miss.get('win_rate') or 0) * 100:.1f}% | "
            f"lift {('%.2fpp' % (lift * 100)) if lift is not None else '-'}"
        )
        for half in ("first_half", "second_half"):
            hb = result[half][str(h)]
            hl = hb["lift_hit_minus_miss_mean"]
            lines.append(
                f"    {half}: 命中 mean {(hb['hit'].get('mean') or 0) * 100:+.2f}% "
                f"(n={hb['hit'].get('n', 0)}) vs 未命中 "
                f"{(hb['miss'].get('mean') or 0) * 100:+.2f}% (n={hb['miss'].get('n', 0)}) "
                f"lift {('%.2fpp' % (hl * 100)) if hl is not None else '-'}"
            )
    return "\n".join(lines)


def _regime_filter_from_args(
    raw: str, trade_days: list[str]
) -> tuple[Optional[dict[str, str]], Optional[set[str]]]:
    """解析 --amv-regimes：空 → (None, None)（不过滤）；否则读 live regime 历史
    as-of 解析各交易日 regime。文件缺失 fail-closed（SystemExit）。"""
    if not raw.strip():
        return None, None
    allow = {x.strip() for x in raw.split(",") if x.strip()}
    hist_path = MARKET_DIR / "0amv_regime_history.json"
    if not hist_path.is_file():
        raise SystemExit(f"[ERR] --amv-regimes 需要 {hist_path}（live regime 历史）")
    hist = json.loads(hist_path.read_text(encoding="utf-8"))
    day_regime = _resolve_day_regimes(trade_days, hist)
    used = sum(1 for d in trade_days if day_regime[d] in allow)
    print(
        f"[INFO] regime 过滤 {sorted(allow)}：{used}/{len(trade_days)} 交易日入样",
        file=sys.stderr,
    )
    return day_regime, allow


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--days", type=int, default=120, help="研究窗口（交易日数）")
    ap.add_argument(
        "--window", type=int, default=40, help="活跃板块回看窗口 W（有榜交易日）"
    )
    ap.add_argument("--min-hits", type=int, default=2, help="活跃板块上榜次数门槛 K")
    ap.add_argument(
        "--horizons", default="5,10,20", help="forward 收益 horizon，逗号分隔"
    )
    ap.add_argument("--codes-file", default="", help="限定宇宙（每行一个代码，排障用）")
    ap.add_argument("--rank-dir", default=str(DEFAULT_RANK_DIR))
    ap.add_argument("--sector-members", default=str(DEFAULT_MEMBERS))
    ap.add_argument(
        "--j-threshold",
        type=float,
        default=J_LOW_THRESHOLD,
        help="J 池门槛（默认 b1_thresholds.J_LOW_THRESHOLD=13）",
    )
    ap.add_argument(
        "--amv-regimes",
        default="",
        help="只统计指定 0AMV regime 的交易日（逗号分隔，如 做多,中性）；"
        "默认不过滤。regime 取 data/market/0amv_regime_history.json"
        "（live 状态机口径，as-of），文件缺失 fail-closed",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    horizons = sorted({int(x) for x in args.horizons.split(",") if x.strip()})
    if not horizons:
        ap.error("--horizons 为空")
    max_h = max(horizons)

    codes = _resolve_universe(args.codes_file)
    count = args.days + max_h + WARMUP_BARS
    bars = _load_bars(codes, count)
    if not bars:
        print("[ERR] 无任何个股数据（TDX_ROOT 未配？）", file=sys.stderr)
        return 1

    # 研究窗口交易日 = 全部已加载个股日期的并集（本地数据自洽，不依赖外部日历）。
    all_days = sorted(
        {str(d)[:10] for df in bars.values() for d in df["date"].tolist()}
    )
    trade_days = all_days[-args.days :]
    if not trade_days:
        print("[ERR] 研究窗口为空", file=sys.stderr)
        return 1

    day_regime, regime_allow = _regime_filter_from_args(args.amv_regimes, trade_days)

    rank_by_date = load_rank_files(Path(args.rank_dir))
    print(
        f"[INFO] daily_rank 文件 {len(rank_by_date)} 份"
        + (f"（{min(rank_by_date)} ~ {max(rank_by_date)}）" if rank_by_date else ""),
        file=sys.stderr,
    )
    code2secs = _load_code2secs(Path(args.sector_members))

    result = run_study(
        bars,
        rank_by_date,
        code2secs,
        trade_days,
        horizons,
        window=args.window,
        min_hits=args.min_hits,
        j_threshold=args.j_threshold,
        day_regime=day_regime,
        regime_allow=regime_allow,
    )
    result["window"] = {"start": trade_days[0], "end": trade_days[-1]}

    payload: dict[str, Any] = {
        "study": "sector_inflow_study",
        "issue": 26,
        "question": "近期多次上榜(资金流入)板块里的 J<13 股票,后续表现是否优于未命中的",
        "params": {
            "days": args.days,
            "window": args.window,
            "min_hits": args.min_hits,
            "horizons": horizons,
            "j_threshold": args.j_threshold,
            "amv_regimes": args.amv_regimes.strip() or None,
            "adjust": "qfq",
            "universe": (
                f"codes_file({Path(args.codes_file).name})"
                if args.codes_file
                else "vipdoc_full"
            ),
            "n_universe": len(codes),
            "n_bars_loaded": len(bars),
            "rank_dir": args.rank_dir,
            "sector_members": args.sector_members,
            "warmup_bars": WARMUP_BARS,
        },
        **result,
    }
    text = _render_text(payload, horizons)
    print("\n=== #26 板块资金流入 × J<13 对照研究 ===")
    print(text)
    payload["text"] = text

    out = args.out or str(
        ARTIFACT_LOGS / f"sector_inflow_study_{cn_today().isoformat()}.json"
    )
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(f"[OK] 写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
