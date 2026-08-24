# -*- coding: utf-8 -*-
"""Calculate MFE/MAE for current holdings using daily bars from the datasource layer.

⚠️ 窗口口径(2026-07-31 修正):MFE/MAE 必须按**入场日**锚定。
此前用券商导出的「持仓天数」(自然日)当 `df.tail(n)` 的 K 线行数——365 自然日只有约 250
个交易日,窗口因此往回多伸约 40%,**吃进入场前的 K 线**,把入场前的高低点算进本笔交易的
最大浮盈/浮亏;数据末尾若不是目标日,锚点还会静默平移。该数字流向 weekly_review 的卖飞
判定(SELL_FLY_PCT),口径错会直接误判"卖飞"。现改为从成交台账 FIFO 回放解析建仓日,
并按日期过滤;解析不出入场日或 K 线未覆盖入场日时**不出数**(fail-closed),给 unable_reason。
"""

from __future__ import annotations
import json, sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import cn_today, HOLDINGS_DIR, TRADES_DIR  # noqa: E402
from custos.core.contracts import require  # noqa: E402
import sys

POSITIONS = TRADES_DIR / "current_positions.json"
LEDGER = TRADES_DIR / "master_trade_ledger.csv"


def resolve_open_entry_dates(trades: list[dict]) -> dict[str, dict]:
    """FIFO 回放全台账,返回每只**仍持有**股票的建仓日。

    口径与 `weekly_review.fifo_pair` 一致(买入压栈、卖出先进先出消耗),区别是这里取
    **剩余未平仓 lot**:FIFO 下最老的未平 lot 才是"当前还持有的那批",其成交日 = 建仓日,
    用来锚定 MFE/MAE 窗口左端。

    返回 {code: {entry_date, avg_buy_date, open_qty, open_lots}}。
    卖出多于买入(台账不完整)时该股按无未平仓处理,不返回 —— 宁可不出数也不给错窗口。
    """
    lots = _replay_lots_fifo(trades)
    out: dict[str, dict] = {}
    for code, book in lots.items():
        summary = _summarize_open_book(book)
        if summary is not None:
            out[code] = summary
    return out


def _replay_lots_fifo(trades: list[dict]) -> dict[str, list[list]]:
    """FIFO 回放台账成 lot 簿:买入压栈、卖出先进先出消耗,返回 {code: [[qty, price, date], ...]}。"""
    lots: dict[str, list[list]] = {}  # code -> [[qty, price, date], ...]
    for t in sorted(
        trades,
        key=lambda r: (
            r.get("date") or "",
            r.get("time") or "",
            0 if r.get("side") == "买入" else 1,
        ),
    ):
        code, qty = str(t.get("code") or ""), float(t.get("qty") or 0)
        if not code or qty <= 0:
            continue
        if t.get("side") == "买入":
            lots.setdefault(code, []).append(
                [qty, float(t.get("price") or 0), t.get("date") or ""]
            )
            continue
        _consume_lots_fifo(lots.setdefault(code, []), qty)
    return lots


def _consume_lots_fifo(book: list[list], qty: float) -> None:
    """卖出按 FIFO 消耗 book 头部的 lot;卖出多于买入时只消耗到空(台账不完整)。"""
    remaining = qty
    while remaining > 1e-9 and book:
        take = min(remaining, book[0][0])
        book[0][0] -= take
        remaining -= take
        if book[0][0] <= 1e-9:
            book.pop(0)


def _summarize_open_book(book: list[list]) -> dict | None:
    """把单只股票的剩余 lot 簿汇总成建仓记录;无有效未平 lot 返回 None(不出数)。"""
    book = [x for x in book if x[0] > 1e-9 and x[2]]
    if not book:
        return None
    total = sum(x[0] for x in book)
    # 数量加权平均买入日只作参考;窗口左端一律用最老未平 lot(最保守、不漏浮亏)
    avg_ord = sum(date.fromisoformat(x[2]).toordinal() * x[0] for x in book) / total
    return {
        "entry_date": min(x[2] for x in book),
        "avg_buy_date": date.fromordinal(round(avg_ord)).isoformat(),
        "open_qty": round(total, 4),
        "open_lots": len(book),
    }


def normalize_date_col(df):
    """把各数据源的日期列统一成 `date`,取不到时返回 None(由调用方 fail-closed)。

    2026-08-24 解耦后数据源统一为 local_tdx_data（get_ohlcv_table / get_online_bars
    都已保证 `date` 列）；保留多候选名归一是防御性的——历史数据源列名不一致
    （`datetime`/Reader 未命名 DatetimeIndex reset 后的 `index`）曾让持仓被误判
    "无数据",**不能**因为列名不同就把持仓判成"无数据"。
    """
    if df is None or len(df) == 0:
        return None
    for cand in ("date", "datetime", "trade_date", "index", "level_0"):
        if cand in df.columns:
            return df if cand == "date" else df.rename(columns={cand: "date"})
    return None


def unable_row(code: str, name: str, reason: str, **extra) -> dict:
    """统一的"不出数"记录。

    键名必须与成功记录一致(`mfe_pct`/`mae_pct`),否则下游读不到:
    `final_close_review` 取 `mfe_map[code]["mfe_pct"]`、`weekly_review.load_mfe_after`
    判 `entry.get("mfe_pct") is None`。异常路径此前落 `{"mfe": None, "mae": None}`,
    键名不一致 ⇒ 失败信息在传导链上直接消失,下游只能当"无该代码"。
    """
    row = {
        "code": code,
        "name": name,
        "mfe_pct": None,
        "mae_pct": None,
        "unable_reason": reason,
    }
    row.update(extra)
    return row


def coverage_summary(rows: list[dict]) -> dict:
    """出数覆盖率。退出码与 stdout 摘要都据此产生 —— 全员不出数不得报成功。"""
    total = len(rows)
    valued = sum(1 for r in rows if r.get("mfe_pct") is not None)
    unable = total - valued
    if total == 0:
        status = "complete"
    elif valued == 0:
        status = "failed"
    elif unable:
        status = "degraded"
    else:
        status = "complete"
    return {
        "total": total,
        "valued": valued,
        "unable": unable,
        "coverage_pct": round(valued / total * 100, 2) if total else None,
        "status": status,
        "unable_codes": [r.get("code") for r in rows if r.get("mfe_pct") is None],
    }


def optional_float(pos: dict, key: str):
    """持仓字段取值:**缺失返回 None,不返回 0**。

    incremental_ledger 增量新建的持仓行只有 代码/名称/持有数量/单位成本,
    最新价/持有盈亏率/持仓天数/市值/仓位占比 都没有(等收盘重估)。用 `.get(key, 0)`
    会把"未重估"写成 0.0,复盘里就是"现价 0 元、浮盈 -100%"。
    """
    v = pos.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_entry_dates(ledger_path: Path = LEDGER) -> dict[str, dict]:
    """读台账并解析建仓日。复用 weekly_review.parse_ledger 的行规范化,避免两套解析漂移。"""
    try:
        from custos.pipeline.close_review import weekly_review as wr  # noqa: PLC0415

        rows = wr.parse_ledger(ledger_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 台账解析失败,MFE/MAE 无法锚定入场日: {exc}")
        return {}
    return resolve_open_entry_dates(rows or [])


def fetch_bars(code: str, bars_needed: int):
    """取 K 线：统一走数据层 local_tdx_data（2026-08-24 解耦，不再直调 mootdx）。

    主路径 `get_ohlcv_table(adjust="qfq")`：本地 vipdoc 优先、内部已含在线回退
    （前复权：MFE/MAE 是持仓期最大浮盈/浮亏，未复权的除权跳空会造出一个根本不
    存在的巨额 MAE——owner 2026-08-04 拍板全链前复权）。BJ 与沪深同口径
    （mootdx Reader 误路由 920xxx 的问题由数据层内部处理）。
    在线兜底 `get_online_bars`：⚠️ 默认被 `_online_quotes_enabled()` 短路返回空
    （实测在线行情不可用，约 10~13s 返回空；设 TDX_ONLINE_QUOTES=1 才启用）——
    与主路径内部的在线回退是同一开关。取不到返回 None/空表，调用方 fail-closed。

    口径变化说明：旧代码在线兜底传 `count=bars_needed`，而 mootdx
    `client.bars()` 没有 count 形参（落入 **kwargs 被静默忽略，实际恒取默认
    800 根）；get_online_bars 的 `offset` 是真生效的（mootdx 上限 800）。
    """
    from custos.datasource.local_tdx import local_tdx_data as ltd

    df = ltd.get_ohlcv_table(code, count=2000, adjust="qfq")
    if df is not None and len(df) > 0:
        return df.reset_index(drop=True)
    df = ltd.get_online_bars(code, frequency=9, offset=bars_needed)
    if df is not None and len(df) > 0:
        df = df.reset_index()
    return df


def calc_window_row(df, pos: dict, entry: dict, entry_date: str, target: str) -> dict:
    """按**入场日**锚定窗口(不能用自然日「持仓天数」当 K 线行数)并算 MFE/MAE。

    窗口为空、缺日期列、单位成本缺失等一律落 unable_row(fail-closed)。
    """
    code = str(pos["代码"])
    name = pos["名称"]
    cost = optional_float(pos, "单位成本") or 0.0
    hold_days = optional_float(pos, "持仓天数")  # 增量新建持仓行没有该字段 → None
    qty = optional_float(pos, "持有数量")

    if df is None or len(df) == 0:
        print(f"[WARN] {code} {name}: no data")
        return unable_row(
            code,
            name,
            "无 K 线数据(本地 vipdoc 与在线 bars 均为空)",
            entry_date=entry_date,
        )

    # Normalize date column name
    df = normalize_date_col(df)

    if df is None:
        print(f"[WARN] {code} {name}: 无日期列，跳过")
        return unable_row(
            code,
            name,
            "K线缺少日期列，无法按入场日锚定窗口",
            entry_date=entry_date,
        )
    df = df.assign(_d=df["date"].astype(str).str[:10])
    df = df[(df["_d"] >= entry_date) & (df["_d"] <= target)]
    if df.empty:
        print(f"[WARN] {code} {name}: K线未覆盖 {entry_date}~{target}，跳过")
        return unable_row(
            code,
            name,
            f"K线未覆盖入场日 {entry_date}~{target}",
            entry_date=entry_date,
        )

    highs = df["high"].astype(float)
    lows = df["low"].astype(float)

    if cost <= 0:
        # 单位成本缺失/为 0(增量新建持仓行、脏数据)时百分比没有分母:
        # 不出数并说明原因,不能落 0% 让下游当"没盈没亏"。
        print(f"[WARN] {code} {name}: 单位成本缺失，跳过")
        return unable_row(
            code,
            name,
            "单位成本缺失或为 0，无法计算 MFE/MAE 百分比",
            entry_date=entry_date,
        )
    mfe_pct = (highs.max() / cost - 1) * 100
    mae_pct = (lows.min() / cost - 1) * 100
    mfe_idx = highs.idxmax()
    mae_idx = lows.idxmin()
    mfe_date = str(df.loc[mfe_idx, "_d"])[:10]
    mae_date = str(df.loc[mae_idx, "_d"])[:10]

    row = {
        "code": code,
        "name": name,
        "cost": cost,
        "hold_days": hold_days,
        "entry_date": entry_date,
        "avg_buy_date": entry.get("avg_buy_date"),
        "window_bars": int(len(df)),
        "mfe_pct": round(mfe_pct, 2) if mfe_pct is not None else None,
        "mfe_date": mfe_date,
        "mae_pct": round(mae_pct, 2) if mae_pct is not None else None,
        "mae_date": mae_date,
        "current_price": optional_float(pos, "最新价"),
        "current_pnl_pct": (lambda v: v * 100 if v is not None else None)(
            optional_float(pos, "持有盈亏率")
        ),
        "position_qty": qty,
        # 增量新建/待重估的持仓行透传状态,报告层才能标"市值盈亏尚未按收盘价重估"
        "snapshot_status": pos.get("snapshot_status"),
    }
    print(
        f"[OK] {code} {name}: MFE={mfe_pct:.1f}% MAE={mae_pct:.1f}% "
        f"(入场 {entry_date}, {len(df)} 根)"
    )
    return row


def process_position(pos: dict, entries: dict[str, dict], target: str) -> dict:
    """单只持仓全流程:锚定入场日 → 取 K 线 → 算 MFE/MAE;失败一律落 unable_row。"""
    code = str(pos["代码"])
    name = pos["名称"]
    hold_days = optional_float(pos, "持仓天数")  # 增量新建持仓行没有该字段 → None

    # 入场日锚点:解析不出就不出数,绝不退回「持仓天数当行数」的旧口径
    entry = entries.get(code) or {}
    entry_date = entry.get("entry_date")
    if not entry_date:
        print(f"[WARN] {code} {name}: 台账无未平仓记录，跳过")
        return unable_row(
            code,
            name,
            "成交台账无该股未平仓记录，无法锚定入场日",
            hold_days=hold_days,
        )
    # 在线兜底取多少根:按自然日跨度换算并留足缓冲(过滤靠日期,多取无害)
    span_days = max(
        (date.fromisoformat(target) - date.fromisoformat(entry_date)).days, 0
    )
    bars_needed = span_days + 30

    try:
        df = fetch_bars(code, bars_needed)
        return calc_window_row(df, pos, entry, entry_date, target)
    except Exception as e:
        print(f"[WARN] {code} {name}: {e}")
        return unable_row(
            code, name, f"计算异常: {e}", entry_date=entry_date, error=str(e)
        )


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=cn_today().strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    target = args.date
    OUT = HOLDINGS_DIR / f"{target}_mfe_mae.json"

    positions = json.loads(POSITIONS.read_text(encoding="utf-8"))
    entries = load_entry_dates()
    if not entries:
        print(
            "[WARN] 台账未解析出任何未平仓建仓日 —— 所有持仓将不出 MFE/MAE(fail-closed)"
        )

    results = [process_position(pos, entries, target) for pos in positions]

    coverage = coverage_summary(results)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 提成变量以便落盘前校验（原为内联字面量）。
    payload = {"date": target, "coverage": coverage, "holdings": results}
    require("mfe_mae", payload)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 摘要行是 run_1700 的判据:降级/失败必须以 [WARN] 开头,否则 runner 照报 [OK]
    tag = "[OK]" if coverage["status"] == "complete" else "[WARN]"
    print(
        f"\n{tag} MFE/MAE {coverage['valued']}/{coverage['total']} 出数"
        f"({coverage['status']}"
        + (
            f"，未出数 {','.join(str(c) for c in coverage['unable_codes'][:10])}"
            if coverage["unable_codes"]
            else ""
        )
        + f") -> {OUT.name}"
    )
    return 0 if coverage["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
