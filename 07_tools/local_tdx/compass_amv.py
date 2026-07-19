# -*- coding: utf-8 -*-
"""指南针(Compass) 0AMV 活跃市值日线解析器（day.vdat 纯读取器）。

数据文件：``<COMPASS_ROOT>/WavMain/ANALYSE/Data/ChinaStk/Z_SK/day.vdat``
（``COMPASS_ROOT`` 环境变量可覆盖安装目录，默认 ``E:\\Compass``）。

文件格式（已逆向并验证）：

- 文件头 16 字节 GUID + 配置，之后为 28 字节日线记录：
  ``date(uint32 LE, YYYYMMDD) + 6 个 float32 (O/H/L/C/V/A)``。
- 文件内含**多个指数系列**，每个系列由若干 250 条记录的数据块
  首尾相接组成（块间有几十字节间隔、对齐可变），末尾块不足 250 条。
- 鲁棒定位（不硬编码偏移）：扫描全部 28 种对齐找候选数据段
  （日期合法、相邻记录日期严格单调、连续 >= 20 条、OHLC 合理），
  再按日期连续性把段拼接成完整系列链（段间允许字节间隔，
  日期首尾相接不重叠，间隙 <= 15 天；同期间隙并列时取字节距离最近者）。
- 0AMV 识别：① 与真值台账 ``01_data/market/0amv_observations.jsonl``
  最近若干条 confirmed 记录（date→amv_change_pct）比对，
  匹配率 >= 90%（容差 ±0.05）的链；② 无真值可用或无链达标时，
  回退选"结束日期最新且总历史最长"的链。

所有接口结构化返回、绝不 raise；文件缺失或无有效序列时返回带 error 字段的结果。

CLI::

    uv run python 07_tools/local_tdx/compass_amv.py            # summary JSON
    uv run python 07_tools/local_tdx/compass_amv.py --since 2026-07-01
    uv run python 07_tools/local_tdx/compass_amv.py --json     # 全量记录
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE  # noqa: E402,F401  (照 07_tools 惯例统一入口)

DEFAULT_COMPASS_ROOT = Path(r"E:\Compass")
DAY_VDAT_REL = Path("WavMain") / "ANALYSE" / "Data" / "ChinaStk" / "Z_SK" / "day.vdat"

RECORD_SIZE = 28          # date(uint32) + 6 * float32
MIN_RUN = 20              # 候选数据段最少连续条数
OHLC_SANE_RATIO = 0.95    # 序列中 OHLC 关系合理的最低占比
_TOL = 1e-4               # OHLC 关系容差（相对价格量级可忽略）

MAX_CHAIN_GAP_DAYS = 15   # 段间日期间隙上限（跨长假下一块，实测最大 10 天）
TRUTH_PAIRS = 30          # 真值比对取最近 confirmed 记录条数
TRUTH_MIN_PRESENT = 3     # 真值日期至少落在链内 N 条才可判定
TRUTH_MATCH_RATIO = 0.9   # 真值匹配率下限
TRUTH_TOL = 0.05          # change_pct 匹配容差（百分点）
DEFAULT_TRUTH_LEDGER = BASE / "01_data" / "market" / "0amv_observations.jsonl"


def _compass_root(root: Optional[str] = None) -> Path:
    """安装目录优先级：显式参数 > COMPASS_ROOT 环境变量 > 默认 E:\\Compass。"""
    if root:
        return Path(root)
    env = os.environ.get("COMPASS_ROOT")
    if env:
        return Path(env)
    return DEFAULT_COMPASS_ROOT


def _day_vdat_path(root: Optional[str] = None) -> Path:
    return _compass_root(root) / DAY_VDAT_REL


def _valid_date(v: int) -> bool:
    """uint32 是否为合法 YYYYMMDD 日期（1990-2100）。"""
    y, m, d = v // 10000, (v // 100) % 100, v % 100
    if not (1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
        return False
    try:
        _dt.date(y, m, d)
    except ValueError:
        return False
    return True


def _find_candidate_runs(data: bytes) -> list:
    """全对齐扫描，返回所有候选日期序列（每段为 [(offset, date_int), ...]）。

    记录仅保证 2 字节对齐（实测主序列 date 字段偏移 mod 4 != 0），
    故按 2 字节步长扫描全部 28 种对齐。
    """
    unpack = struct.Struct("<I").unpack_from
    groups: dict[int, list] = {}
    for off in range(0, len(data) - 3, 2):
        (v,) = unpack(data, off)
        if _valid_date(v):
            groups.setdefault(off % RECORD_SIZE, []).append((off, v))

    runs = []
    for offs in groups.values():
        cur: list = []
        direction = 0  # +1 递增 / -1 递减
        prev_off, prev_date = -1, 0
        for off, date in offs:
            contiguous = (off - prev_off == RECORD_SIZE) and cur
            step = date - prev_date
            same_dir = direction != 0 and (step > 0) == (direction > 0) and step != 0
            if contiguous and same_dir:
                cur.append((off, date))
            elif contiguous and step != 0:
                direction = 1 if step > 0 else -1
                cur.append((off, date))
            else:
                if len(cur) >= MIN_RUN:
                    runs.append(cur)
                direction = 0
                cur = [(off, date)]
            prev_off, prev_date = off, date
        if len(cur) >= MIN_RUN:
            runs.append(cur)
    return runs


def _ohlc_sane(o: float, h: float, l: float, c: float) -> bool:
    return (
        h >= l - _TOL
        and l - _TOL <= o <= h + _TOL
        and l - _TOL <= c <= h + _TOL
        and c > 0
    )


def _int_to_date(v: int) -> _dt.date:
    return _dt.date(v // 10000, (v // 100) % 100, v % 100)


def _build_segments(data: bytes, runs: list) -> list:
    """候选段过滤（OHLC 合理）并按日期升序整理。

    返回 [ [(off, date_int, o, h, l, c, v, a), ...], ... ]，每段日期升序。
    """
    segments = []
    for run in runs:
        recs = []
        sane = 0
        for off, date in run:
            o, h, l, c, v, a = struct.unpack_from("<6f", data, off + 4)
            recs.append((off, date, o, h, l, c, v, a))
            if _ohlc_sane(o, h, l, c):
                sane += 1
        if sane / len(recs) < OHLC_SANE_RATIO:
            continue
        recs.sort(key=lambda r: r[1])
        segments.append(recs)
    return segments


def _chain_segments(segments: list) -> list:
    """把数据段按日期连续性拼成系列链。

    段 B 接在链尾的条件：B 首日期 > 链尾日期 且间隙 <= MAX_CHAIN_GAP_DAYS。
    多个链同时满足时（不同系列日期历相同）取日期间隙最小、
    并列取字节距离最近者——同一系列在文件中是连续存放的。

    返回 [ [(date_int, o, h, l, c, v, a), ...], ... ]，每链日期升序去重。
    """
    segs = sorted(segments, key=lambda s: (s[0][1], s[0][0]))
    chains = []  # {"segs": [...], "last_date": int, "end_off": int}
    for seg in segs:
        first_date = seg[0][1]
        start_off = min(r[0] for r in seg)
        fd = _int_to_date(first_date)
        best = None
        best_key = None
        for ch in chains:
            if ch["last_date"] >= first_date:
                continue
            gap = (fd - _int_to_date(ch["last_date"])).days
            if gap > MAX_CHAIN_GAP_DAYS:
                continue
            key = (gap, abs(start_off - ch["end_off"]))
            if best_key is None or key < best_key:
                best, best_key = ch, key
        end_off = max(r[0] for r in seg)
        if best is None:
            chains.append({"segs": [seg], "last_date": seg[-1][1], "end_off": end_off})
        else:
            best["segs"].append(seg)
            best["last_date"] = seg[-1][1]
            best["end_off"] = end_off
    out = []
    for ch in chains:
        recs = {}
        for seg in ch["segs"]:
            for _off, date, o, h, l, c, v, a in seg:
                recs.setdefault(date, (date, o, h, l, c, v, a))
        out.append(sorted(recs.values(), key=lambda r: r[0]))
    return out


def _load_truth(truth_path: Optional[str] = None) -> list:
    """从台账读最近 TRUTH_PAIRS 条 confirmed (date, amv_change_pct)，按日期升序。"""
    path = Path(truth_path) if truth_path else DEFAULT_TRUTH_LEDGER
    pairs: dict[str, float] = {}
    try:
        if not path.is_file():
            return []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("quality") != "confirmed":
                continue
            d, p = rec.get("date"), rec.get("amv_change_pct")
            if isinstance(d, str) and isinstance(p, (int, float)):
                pairs[d] = float(p)
    except OSError:
        return []
    return sorted(pairs.items())[-TRUTH_PAIRS:]


def _change_pct_map(chain: list) -> dict:
    """链内 date_iso -> change_pct（首条 None）。"""
    m: dict[str, Optional[float]] = {}
    prev_close = None
    for date, _o, _h, _l, c, _v, _a in chain:
        iso = _int_to_date(date).isoformat()
        m[iso] = round((c / prev_close - 1) * 100, 2) if prev_close else None
        prev_close = c
    return m


def _identify_amv(chains: list, truth: list) -> tuple:
    """识别 0AMV 链，返回 (chain, identification)。

    ① 真值匹配：链内覆盖 >= TRUTH_MIN_PRESENT 条真值日期且匹配率
    >= TRUTH_MATCH_RATIO（容差 ±TRUTH_TOL）者，取匹配数最多者；
    ② 回退：结束日期最新、并列取总历史最长。
    """
    if truth:
        best = None
        best_key = None
        for chain in chains:
            m = _change_pct_map(chain)
            present = [(d, p) for d, p in truth if m.get(d) is not None]
            if len(present) < TRUTH_MIN_PRESENT:
                continue
            matched = sum(1 for d, p in present if abs(m[d] - p) <= TRUTH_TOL)
            if matched / len(present) < TRUTH_MATCH_RATIO:
                continue
            key = (matched, chain[-1][0], len(chain))
            if best_key is None or key > best_key:
                best_key = key
                best = (chain,
                        f"truth_match: {matched}/{len(present)} confirmed pairs within ±{TRUTH_TOL}")
        if best is not None:
            return best
    chain = max(chains, key=lambda c: (c[-1][0], len(c)))
    note = "fallback: latest end + longest history"
    if truth:
        note = "fallback: no chain matched truth; latest end + longest history"
    return chain, note


def _to_records(series: list) -> list:
    """转为输出记录，change_pct 由相邻收盘计算（首条为 None）。"""
    out = []
    prev_close = None
    for date, o, h, l, c, v, a in series:
        d = _dt.date(date // 10000, (date // 100) % 100, date % 100).isoformat()
        change_pct = None
        if prev_close:
            change_pct = round((c / prev_close - 1) * 100, 2)
        out.append({
            "date": d,
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": v,
            "amount": a,
            "change_pct": change_pct,
        })
        prev_close = c
    return out


def parse_amv_daily(since: str = "2024-01-01", root: Optional[str] = None,
                    truth_path: Optional[str] = None) -> dict:
    """解析 0AMV 日线主序列，返回结构化结果（绝不 raise）。

    返回::

        {"source": "compass_day_vdat", "path": str, "count": N,
         "first_date": "...", "latest_date": "...",
         "series_start": "...",       # 0AMV 全历史起点（不受 since 过滤影响）
         "identification": "...",     # 系列识别方式说明
         "records": [{"date", "open", "high", "low", "close",
                      "volume", "amount", "change_pct"}, ...]}

    仅含 date >= since 的记录；失败时附带 ``error`` 字段、records 为空。
    ``truth_path`` 可覆盖真值台账路径（默认 01_data/market/0amv_observations.jsonl）。
    """
    path = _day_vdat_path(root)
    result: dict[str, Any] = {
        "source": "compass_day_vdat",
        "path": str(path),
        "count": 0,
        "first_date": None,
        "latest_date": None,
        "series_start": None,
        "identification": None,
        "records": [],
    }
    try:
        if not path.is_file():
            result["error"] = f"file_not_found: {path}"
            return result
        data = path.read_bytes()
        if len(data) < 16 + RECORD_SIZE * MIN_RUN:
            result["error"] = f"file_too_small: {len(data)} bytes"
            return result
        chains = _chain_segments(_build_segments(data, _find_candidate_runs(data)))
        if not chains:
            result["error"] = "no_valid_series"
            return result
        series, identification = _identify_amv(chains, _load_truth(truth_path))
        records = [r for r in _to_records(series) if r["date"] >= since]
        result["records"] = records
        result["count"] = len(records)
        result["series_start"] = _int_to_date(series[0][0]).isoformat()
        result["identification"] = identification
        if records:
            result["first_date"] = records[0]["date"]
            result["latest_date"] = records[-1]["date"]
        return result
    except Exception as exc:  # noqa: BLE001 —— 绝不 raise 到调用方
        result["error"] = f"parse_failed: {exc}"
        result["records"] = []
        result["count"] = 0
        return result


def latest_amv(root: Optional[str] = None, truth_path: Optional[str] = None) -> dict:
    """返回最新一条记录 + 与前一交易日的 change_pct，供管线调用（绝不 raise）。"""
    parsed = parse_amv_daily(since="1900-01-01", root=root, truth_path=truth_path)
    if parsed.get("error") or not parsed["records"]:
        return {
            "ok": False,
            "error": parsed.get("error", "no_records"),
            "source": parsed["source"],
            "path": parsed["path"],
        }
    last = parsed["records"][-1]
    prev_close = parsed["records"][-2]["close"] if len(parsed["records"]) > 1 else None
    return {
        "ok": True,
        "source": parsed["source"],
        "path": parsed["path"],
        "date": last["date"],
        "close": last["close"],
        "prev_close": prev_close,
        "change_pct": last["change_pct"],
    }


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="指南针 0AMV day.vdat 日线解析")
    ap.add_argument("--since", default="2024-01-01", help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--json", action="store_true", help="输出全量记录而非 summary")
    args = ap.parse_args(argv)

    parsed = parse_amv_daily(since=args.since)
    if args.json:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        return 0 if not parsed.get("error") else 1

    latest = parsed["records"][-1] if parsed["records"] else None
    summary = {
        "source": parsed["source"],
        "path": parsed["path"],
        "count": parsed["count"],
        "series_start": parsed["series_start"],
        "identification": parsed["identification"],
        "first_date": parsed["first_date"],
        "latest_date": parsed["latest_date"],
        "latest_close": latest["close"] if latest else None,
        "latest_change_pct": latest["change_pct"] if latest else None,
    }
    if parsed.get("error"):
        summary["error"] = parsed["error"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not parsed.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
