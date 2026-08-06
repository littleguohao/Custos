# -*- coding: utf-8 -*-
"""一次性抓取通达信板块/行业指数(880xxx)日线**收盘价**历史 → 落盘缓存,供板块相位(MACD)回测。

需 TdxW(TQ-Local)运行。板块 MACD 相位只需收盘价,故只取 Close(格式已探明:index=日期,列=代码)。
用法:
    uv run python 07_tools/local_tdx/fetch_sector_index_history.py --out 01_data/market/sector_index --start 20180101
输出:每板块一份 {code}.csv(date,close)。只读 TQ、绝不改线上。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import tq_sector  # noqa: E402  复用其 TdxW 探测 + tqcenter 惰性导入

# TQ 的周期串是 "1d"(探针 00_governance/data/TDX_LOCAL_INTERFACES.md「周期串是 1d」:缺省或写错报
# ErrorId=5 periodstr error)。此前默认 "day" → 400+ 板块逐个报错、日复一日刷不到数据。
# 保留候选串按序探测,避免不同 TQ 版本命名差异再把整条链打死。
PERIOD_CANDIDATES = ("1d", "day", "1day", "1440m")
OVERLAP_DAYS = 30                       # 增量刷新时向前重叠的日历日(容忍补数/除权修正)
DEFAULT_MIN_SUCCESS_RATE = 0.6          # 低于此成功率视为大面积失败 → 非零退出
DEFAULT_SLEEP_MS = 0                    # 板块间限速(毫秒);默认 0 保持既有节奏,生产可调


def _suffixed(code: str) -> str:
    """refresh_kline 要求带市场后缀的代码(实测 '880001' 报 Codestr Error,'880001.SH' 成功)。
    880/881 板块指数均为沪市。"""
    c = str(code).strip().upper()
    return c if "." in c else f"{c}.SH"


def _to_close_frame(d, code):
    """get_market_data 返回归一为 [date, close]。

    兼容三种实测形态:①字段键 {Close: df}(index=DatetimeIndex,列=代码)——当前 TQ 版本实际返回;
    ②代码键 {code: df/series};③直接 df。严格校验:日期必须来自 DatetimeIndex 或可解析为日期的列,
    RangeIndex 等数值"日期"拒绝静默落盘。"""
    import pandas as pd
    obj = None
    if isinstance(d, dict):
        obj = d.get(code)
        if obj is None:                      # 字段键形态:{Close: df}
            obj = d.get("Close")
        if obj is None:
            obj = d.get("close")
    else:
        obj = d
    if obj is None:
        return None
    df = obj.to_frame() if hasattr(obj, "to_frame") and not hasattr(obj, "columns") else pd.DataFrame(obj)
    if not len(df):
        return None
    df.columns = [str(c) for c in df.columns]
    cols = list(df.columns)
    c6 = str(code).split(".")[0]
    close_col = next((c for c in cols if c == str(code) or c.split(".")[0] == c6),
                     next((c for c in cols if c.lower() == "close"),
                          cols[0] if len(cols) == 1 else None))
    if close_col is None:
        return None
    if isinstance(df.index, pd.DatetimeIndex):          # 形态①②:日期在 index
        dstr = df.index.strftime("%Y-%m-%d")
    else:                                                # 形态③:日期在首列(数值列拒收)
        date_col = cols[0]
        if pd.api.types.is_numeric_dtype(df[date_col]):
            return None
        dates = pd.to_datetime(df[date_col], errors="coerce")
        dstr = dates.dt.strftime("%Y-%m-%d")
    out = pd.DataFrame({"date": dstr,
                        "close": pd.to_numeric(df[close_col], errors="coerce").values}).dropna()
    return out if len(out) else None


def merge_close_frame(existing_path: Path, new_frame):
    """把新抓到的 [date,close] 并入已有 CSV（按 date 去重，新值优先，升序）。

    增量刷新必须**合并**而不是覆写:每天只拉最近一段就直接写盘会把回测所需的
    2018 年以来深度截断成几十根,板块相位回测随之失真。
    """
    import pandas as pd
    if new_frame is None or not len(new_frame):
        return None
    if existing_path.is_file():
        corrupt = None
        try:
            old = pd.read_csv(existing_path, dtype={"date": str})
            if {"date", "close"}.issubset(old.columns):
                new_frame = pd.concat([old[["date", "close"]], new_frame], ignore_index=True)
            else:
                corrupt = f"缺 date/close 列(实际列: {list(old.columns)})"
        except (OSError, ValueError) as exc:
            corrupt = exc.__class__.__name__
        if corrupt is not None:
            # 不得静默:旧缓存读不动时若只用新窗口落盘,2018 年以来的历史深度会被无声截断。
            # 打印 WARN + 改名隔离损坏文件(保留现场供排查),再以新数据落盘并提示全量重拉。
            from datetime import date as _d  # noqa: PLC0415
            quarantine = existing_path.with_name(
                f"{existing_path.name}.corrupt-{_d.today().strftime('%Y%m%d')}")
            print(f"[WARN] {existing_path.name} 缓存损坏无法合并({corrupt}),"
                  f"已隔离为 {quarantine.name};本次仅用新抓数据落盘,"
                  f"历史深度截断,需不带 --incremental 全量重拉恢复", file=sys.stderr)
            try:
                existing_path.replace(quarantine)
            except OSError as rexc:
                print(f"[WARN] 隔离失败({rexc}),损坏文件将被覆写", file=sys.stderr)
    out = (new_frame.dropna(subset=["date"])
           .drop_duplicates(subset=["date"], keep="last")
           .sort_values("date").reset_index(drop=True))
    return out if len(out) else None


def incremental_start(existing_path: Path, floor: str, overlap_days: int = OVERLAP_DAYS) -> str:
    """增量起点 = 已有 CSV 最后日期 - overlap_days(取不早于 floor);无缓存则用 floor。"""
    from datetime import date as _d, timedelta as _td
    if not existing_path.is_file():
        return floor
    try:
        import pandas as pd
        old = pd.read_csv(existing_path, dtype={"date": str})
        last = str(old["date"].dropna().iloc[-1])[:10]
        start = (_d.fromisoformat(last) - _td(days=overlap_days)).strftime("%Y%m%d")
    except (OSError, ValueError, KeyError, IndexError):
        return floor
    return max(start, floor)


def _describe(d) -> str:
    """描述 get_market_data 实际返回的形态(类型/键/索引),用于探测失败时的诊断输出。

    2026-07-30 排障教训:`1d 返回空数据` 的真因是返回形态是 {'Close': df} 字段键,
    数据其实取到了、只是解析函数不认。失败时只打"空数据"会把人引向周期串,浪费一轮排查。
    """
    try:
        if isinstance(d, dict):
            keys = list(d)[:5]
            inner = d.get(keys[0]) if keys else None
            shape = getattr(inner, "shape", None)
            idx = type(getattr(inner, "index", None)).__name__
            cols = list(getattr(inner, "columns", []) or [])[:5]
            return f"dict(keys={keys}, inner.shape={shape}, index={idx}, cols={cols})"
        return (f"{type(d).__name__}(shape={getattr(d, 'shape', None)}, "
                f"index={type(getattr(d, 'index', None)).__name__})")
    except Exception:  # noqa: BLE001
        return type(d).__name__


def resolve_period(tq, probe_code: str, start: str, wanted: str = "") -> tuple[str, str]:
    """探测可用周期串 → (period, note)。全部失败返回 ("", 原因)。

    只在**第一个板块**上试,避免 400+ 板块各自反复失败(此前 --period day 时就是这样,
    整个 stage 只输出一堆 WARN 后超时)。解析不出数据时把**实际返回形态**一并带回,
    否则"空数据"三个字会把排查引向周期串(真因可能是返回结构变了)。
    """
    cands = ([wanted] if wanted else []) + [p for p in PERIOD_CANDIDATES if p != wanted]
    errs = []
    probe = _suffixed(probe_code)
    import time as _t
    for p in cands:
        try:
            tq.refresh_kline([probe], period=p)
            _t.sleep(1.5)                       # refresh 为异步任务(run_id),稍候再查
            d = tq.get_market_data(field_list=["Close"], stock_list=[probe],
                                   period=p, start_time=start, count=-1)
            if _to_close_frame(d, probe) is not None:
                return p, f"period={p}"
        except Exception as exc:  # noqa: BLE001
            errs.append(f"{p}:{exc}")
            continue
        errs.append(f"{p}:解析不出收盘序列(实际返回 {_describe(d)})")
    return "", "; ".join(errs)


def atomic_write_csv(frame, dest: Path) -> None:
    """原子落盘。tmp 名一律由 dest 派生(``dest.name + ".tmp"``)。

    此前 tmp 用**未加市场后缀**的原始 code 拼名(``880001.csv.tmp``),而 dest 是
    ``880001.SH.csv`` —— 两者不同名,中断留下的残片既不会被下次运行覆盖,也不会被按
    dest 名做的清理发现,只能在缓存目录里越积越多。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(dest)      # 原子落盘:防中断留下截断 CSV(陈旧相位假象)


def write_fetch_status(outdir: Path, total: int, ok: int, failed: list[str],
                       min_rate: float, period: str = "", incremental: bool = False) -> dict:
    """成功率落盘 → ``_fetch_status.json``。

    只靠退出码不够:回测/选股链读的是 CSV 目录本身,必须能从目录里就看出"这批只成功
    3/430"。status 取值 ok / degraded / empty,配合退出码使用。
    """
    rate = (ok / total) if total else 0.0
    status = "ok" if (total and rate >= min_rate) else ("empty" if not ok else "degraded")
    payload = {
        "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total": total,
        "ok": ok,
        "failed": len(failed),
        "failed_codes": failed[:50],
        "success_rate": round(rate, 4),
        "min_success_rate": min_rate,
        "status": status,
        "period": period,
        "incremental": incremental,
    }
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "_fetch_status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓取通达信板块指数(880xxx)收盘历史→CSV缓存")
    ap.add_argument("--out", default=str(TOOLS.parent / "01_data" / "market" / "sector_index"))
    ap.add_argument("--start", default="20180101", help="起始日 YYYYMMDD(TQ 会给到本地实有最早)")
    ap.add_argument("--period", default="", help=f"周期串(缺省自动探测: {', '.join(PERIOD_CANDIDATES)})")
    ap.add_argument("--incremental", action="store_true",
                    help="只拉每个板块缓存末日期前 %d 天起的增量并**合并**进已有 CSV(日常刷新用;"
                         "不加则按 --start 全量重拉)" % OVERLAP_DAYS)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个板块(排障用)")
    ap.add_argument("--members", action="store_true",
                    help="同时抓板块成员(get_stock_list_in_sector)→ sector_members.json(板块相位 gate 需要)")
    ap.add_argument("--min-success-rate", type=float, default=DEFAULT_MIN_SUCCESS_RATE,
                    help=f"落盘成功率低于此值则非零退出(默认 {DEFAULT_MIN_SUCCESS_RATE};"
                         "此前只要有 1 个板块成功就 exit 0，430 个失败 427 个也看不出来)")
    ap.add_argument("--sleep-ms", type=int, default=DEFAULT_SLEEP_MS,
                    help=f"每个板块之间的限速(毫秒,默认 {DEFAULT_SLEEP_MS};400+ 板块串行请求，"
                         "不限速会把 TdxW 打满)")
    args = ap.parse_args(argv)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    if not tq_sector.is_tdxw_running():
        print("[ERR] TdxW.exe 未运行,无法连 TQ")
        return 1
    tq = tq_sector._import_tq()
    tq.initialize(str(Path(__file__).resolve()))
    ok = 0
    total = 0
    members: dict = {}
    st: dict = {}
    try:
        sectors = tq.get_sector_list() or []
        if args.limit:
            sectors = sectors[:args.limit]
        total = len(sectors)
        print(f"[INFO] 板块数: {total}")
        if not sectors:
            print("[ERR] TQ 未返回板块列表")
            return 2
        period, note = resolve_period(tq, sectors[0], args.start, args.period)
        if not period:
            # 快速失败:周期串不被接受时不再逐个板块重试(此前 --period day 会刷 400 条 WARN 后超时)
            print(f"[ERR] 无可用周期串(试过 {', '.join(PERIOD_CANDIDATES)}):{note}", file=sys.stderr)
            print("[ERR] TQ 周期串约定见 00_governance/data/TDX_LOCAL_INTERFACES.md「周期串是 1d，不是 day」")
            return 2
        print(f"[INFO] 使用周期 {period}{'(自动探测)' if not args.period else ''}"
              f"{'; 增量合并模式' if args.incremental else '; 全量重拉'}")
        import time as _t
        failed: list[str] = []
        for i, code in enumerate(sectors):
            code_q = _suffixed(code)          # refresh_kline 必须带市场后缀，否则 Codestr Error
            try:
                dest = outdir / f"{code_q}.csv"
                start = incremental_start(dest, args.start) if args.incremental else args.start
                tq.refresh_kline([code_q], period=period)
                d = tq.get_market_data(field_list=["Close"], stock_list=[code_q],
                                       period=period, start_time=start, count=-1)
                frame = _to_close_frame(d, code_q)
                if frame is None:                 # refresh 异步(run_id)，空数据时稍候重试一次
                    _t.sleep(2.0)
                    d = tq.get_market_data(field_list=["Close"], stock_list=[code_q],
                                           period=period, start_time=start, count=-1)
                    frame = _to_close_frame(d, code_q)
                if args.incremental:
                    frame = merge_close_frame(dest, frame)
                if frame is not None:
                    atomic_write_csv(frame, dest)
                    ok += 1
                else:
                    failed.append(code_q)
                    print(f"[WARN] {code_q}: 解析不出收盘序列", file=sys.stderr)
                if args.members:
                    try:
                        mem = tq.get_stock_list_in_sector(code) or []
                        members[code] = [str(x).split(".")[0][-6:].zfill(6) for x in mem]
                    except Exception as mexc:  # noqa: BLE001
                        print(f"[WARN] members {code}: {mexc}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                failed.append(code_q)
                print(f"[WARN] {code}: {exc}", file=sys.stderr)
            if args.sleep_ms > 0:                 # 串行不改(不引入并发复杂度)，但要限速
                _t.sleep(args.sleep_ms / 1000.0)
            if (i + 1) % 50 == 0:
                print(f"[INFO] {i + 1}/{total}  已落盘 {ok}  失败 {len(failed)}", file=sys.stderr)
        if args.members:
            mpath = outdir.parent / "sector_members.json"
            mpath.write_text(json.dumps(members, ensure_ascii=False), encoding="utf-8")
            print(f"[OK] 成员映射 {len(members)} 板块 → {mpath}")
        st = write_fetch_status(outdir, total, ok, failed, args.min_success_rate,
                               period=period, incremental=args.incremental)
        # 摘要行必须自带成功率:上游 runner 只回显最后一行，"3/430" 不写出来就等于无声失败
        tag = "[OK]" if st["status"] == "ok" else "[WARN]"
        print(f"{tag} 板块指数落盘 {ok}/{total}（成功率 {st['success_rate']:.1%}，"
              f"门槛 {args.min_success_rate:.0%}，status={st['status']}）→ {outdir}")
    finally:
        tq.close()
    return 0 if st.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
