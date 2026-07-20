# -*- coding: utf-8 -*-
"""0AMV 空头区间"只卖不买 + 反弹减仓"历史回测（纯分析脚本，不触碰任何管线）。

回答的问题：0AMV 空头区间如果只卖不买、且每次反弹都降低仓位，
整个持仓收益会如何（对比实际成交）。

区间定义（用户钦定）：
- 空头区间：某交易日 amv_change_pct <= -2.3 当日**进入**（含当日），
  直到某交易日 amv_change_pct >= +4 的前一日结束。
- 多头区间：amv_change_pct >= +4 当日进入（含当日），
  直到下一个 <= -2.3 的前一日结束。
- 首次触发之前为 neutral（正常交易）。

三个场景：
1. actual            —— 按台账实际成交重放（费用按实际"费用"列）。
2. no_bear_buys      —— 空头区间的买单全部跳过（现金留在手里），
                        后续卖单按调整后的持仓 FIFO 撮合；多头/neutral 照常。
3. rebound_reduce    —— 在 2 的基础上，空头区间内每个反弹日
                        （个股当日收盘 > 前收盘）对每只持仓卖出持仓量的 20%
                        （按当日收盘价，费率取台账实际平均卖出费率）。

口径说明（重要，报告中也注明）：
- 台账逐笔 FIFO 重放。买入批次单位成本 = (成交金额 + 费用) / 数量；
  卖出实现盈亏 = 发生金额（已扣费） - FIFO 消耗的成本。
- 分红/除权除息、组合费用、融券/融券购回（逆回购）：只按"发生金额"计入现金，
  不改变持仓数量（简单口径）。
- 例外（为保持台账重放自洽，必须加数量，否则后续卖单无票可卖）：
  "拆股"（603606 东方电缆 +600 股，0 成本）与
  "转债转入" 0 成本记录（159938 医药卫生ETF +22500 份）按 0 成本批次入库；
  有现金流的转债转入按实际成本入库。
- 卖出数量超过持仓时（跳买场景可能出现）：按可得数量撮合、
  发生金额按比例折算，差额记入 shortfalls 并在报告披露。
- 期末浮存按 end 日（含）之前最后可得收盘价折算。
- 行情用 vipdoc 不复权日线：分红造成的除权缺口会使个券浮盈偏保守，
  但分红现金已计入"其他现金流"，合计口径基本不受影响。
- 最大回撤：组合总权益（现金 + 持仓市值）日频序列的峰谷回撤。
  台账无初始入金记录，权益从 0 起步，故同时给出绝对额（元）
  与相对峰值百分比（峰值 > 0 时才有意义）。

CLI::

    uv run python 07_tools/trades/backtest_0amv_bear_regime.py
    uv run python 07_tools/trades/backtest_0amv_bear_regime.py --start 2020-07-10 --end 2026-07-09
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE  # noqa: E402

BEAR_THRESHOLD = -2.3     # amv_change_pct <= 此值当日进入空头
BULL_THRESHOLD = 4.0      # amv_change_pct >= 此值当日进入多头
REBOUND_SELL_PCT = 0.20   # 反弹日减仓比例

LEDGER_PATH = BASE / "01_data" / "trades" / "master_trade_ledger.csv"
POSITIONS_PATH = BASE / "01_data" / "trades" / "current_positions.json"
REPORT_DIR = BASE / "04_reviews" / "trade_review"

CASH_ONLY_CATEGORIES = ("除权除息", "组合费用", "融券", "融券购回")
# 代码为 000000 的组合费用、131810/204001 逆回购等无个股行情的类别
NON_SECURITY_CODES = {"000000", "131810", "204001"}


# ---------------------------------------------------------------- 数据加载

def load_ledger(path: Path = LEDGER_PATH) -> list[dict[str, Any]]:
    """读取主台账，返回按 日期+时间 升序的 dict 列表（数值已转 float）。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        out.append({
            "date": r["成交日期"],
            "time": r.get("成交时间", ""),
            "code": r["代码"].strip(),
            "name": r["名称"],
            "category": r["交易类别"],
            "qty": float(r["成交数量"] or 0),
            "price": float(r["成交价格"] or 0),
            "amount": float(r["成交金额"] or 0),
            "cash": float(r["发生金额"] or 0),
            "fee": float(r["费用"] or 0),
        })
    out.sort(key=lambda t: (t["date"], t["time"]))
    return out


def build_regime_map(records: list[dict]) -> dict[str, str]:
    """由 0AMV 日线记录构造 date -> regime('bear'/'bull'/'neutral')。

    状态机：neutral 起步；change_pct <= -2.3 当日切 bear；
    change_pct >= +4 当日切 bull；其余日期延续前一状态。
    """
    regime: dict[str, str] = {}
    state = "neutral"
    for rec in records:
        cp = rec.get("change_pct")
        if cp is not None:
            if cp <= BEAR_THRESHOLD:
                state = "bear"
            elif cp >= BULL_THRESHOLD:
                state = "bull"
        regime[rec["date"]] = state
    return regime


def regime_segments(regime_map: dict[str, str], target: str = "bear",
                    since: str = "0000-00-00") -> list[dict[str, Any]]:
    """提取 target 状态的连续区间 [{start, end, days}]，end 为区间最后一天（含）。"""
    days = sorted(d for d in regime_map if d >= since)
    segs: list[dict[str, Any]] = []
    cur: Optional[dict[str, Any]] = None
    for d in days:
        if regime_map[d] == target:
            if cur is None:
                cur = {"start": d, "end": d, "days": 0}
            cur["end"] = d
            cur["days"] += 1
        else:
            if cur is not None:
                segs.append(cur)
                cur = None
    if cur is not None:
        segs.append(cur)
    return segs


def load_price_data(codes: list[str]) -> tuple[dict[str, dict], list[str]]:
    """读取个股 vipdoc 日线，返回 ({code: {dates:[], close_by_date:{}}}, 缺数据代码清单)。"""
    from local_tdx.local_tdx_data import read_vipdoc_daily

    prices: dict[str, dict] = {}
    missing: list[str] = []
    for code in sorted(set(codes)):
        if code in NON_SECURITY_CODES:
            continue
        try:
            df = read_vipdoc_daily(code)
        except Exception:  # noqa: BLE001 —— 缺数据跳过，不报错
            df = None
        if df is None or df.empty:
            missing.append(code)
            continue
        dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                 for d in df["date"]]
        closes = [float(c) for c in df["close"]]
        prices[code] = {
            "dates": dates,
            "close_by_date": dict(zip(dates, closes)),
        }
    return prices, missing


def close_on_or_before(price_entry: dict, day: str) -> Optional[float]:
    """day（含）之前最后可得收盘价；无则 None。"""
    dates = price_entry["dates"]
    lo, hi = 0, len(dates) - 1
    best = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= day:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return price_entry["close_by_date"][dates[best]] if best >= 0 else None


def is_rebound_day(price_entry: dict, day: str) -> bool:
    """反弹日判定：当日收盘 > 前一交易日收盘（均需存在）。"""
    dates = price_entry["dates"]
    closes = price_entry["close_by_date"]
    lo, hi = 0, len(dates) - 1
    idx = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= day:
            idx = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if idx <= 0:
        return False
    return closes[dates[idx]] > closes[dates[idx - 1]]


# ---------------------------------------------------------------- FIFO 账本

class FifoBook:
    """单代码 FIFO 持仓。lot = (qty, unit_cost, tag)，tag 标记买入时区间（bear/other）。"""

    def __init__(self) -> None:
        self.lots: deque = deque()

    @property
    def qty(self) -> float:
        return sum(lot[0] for lot in self.lots)

    @property
    def cost(self) -> float:
        return sum(lot[0] * lot[1] for lot in self.lots)

    def add(self, qty: float, unit_cost: float, tag: str = "other") -> None:
        if qty > 0:
            self.lots.append((qty, unit_cost, tag))

    def consume(self, qty: float) -> list[tuple[float, float, str]]:
        """FIFO 消耗 qty，返回消耗明细 [(qty, unit_cost, tag)]（不足时只消耗可得部分）。"""
        consumed: list[tuple[float, float, str]] = []
        remain = qty
        while remain > 1e-9 and self.lots:
            lot_qty, lot_cost, lot_tag = self.lots[0]
            take = min(lot_qty, remain)
            consumed.append((take, lot_cost, lot_tag))
            remain -= take
            if take >= lot_qty - 1e-9:
                self.lots.popleft()
            else:
                self.lots[0] = (lot_qty - take, lot_cost, lot_tag)
        return consumed


# ---------------------------------------------------------------- 场景重放

SCENARIOS = ("actual", "no_bear_buys", "rebound_reduce")


def run_scenario(trades: list[dict], regime_map: dict[str, str],
                 amv_days: list[str], prices: dict[str, dict],
                 scenario: str, sell_fee_rate: float,
                 start: str, end: str) -> dict[str, Any]:
    """按场景重放台账，返回盈亏/持仓/曲线等结果。"""
    assert scenario in SCENARIOS
    trades_by_date: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if start <= t["date"] <= end:
            trades_by_date[t["date"]].append(t)

    books: dict[str, FifoBook] = defaultdict(FifoBook)
    cash = 0.0
    realized = 0.0          # 已实现盈亏（卖出口径）
    realized_bear = 0.0     # 其中：空头区买入批次的已实现盈亏
    other_cf = 0.0          # 分红/费用/逆回购利息等非买卖现金流
    skipped_buys: list[dict] = []
    rebound_sells: list[dict] = []
    shortfalls: list[dict] = []
    equity_curve: list[dict] = []

    def do_sell(code: str, qty: float, proceeds: float, day: str, kind: str,
                price: float = 0.0) -> None:
        nonlocal cash, realized, realized_bear
        book = books[code]
        available = book.qty
        if available <= 1e-9:
            shortfalls.append({"date": day, "code": code, "want": qty,
                               "available": 0.0, "kind": kind})
            return
        sold = min(qty, available)
        eff_proceeds = proceeds * (sold / qty) if sold < qty - 1e-9 else proceeds
        if sold < qty - 1e-9:
            shortfalls.append({"date": day, "code": code, "want": qty,
                               "available": available, "kind": kind})
        consumed = book.consume(sold)
        cost = sum(q * c for q, c, _ in consumed)
        bear_qty = sum(q for q, _, tag in consumed if tag == "bear")
        bear_cost = sum(q * c for q, c, tag in consumed if tag == "bear")
        pnl = eff_proceeds - cost
        realized += pnl
        if bear_qty > 1e-9:
            bear_proceeds = eff_proceeds * (bear_qty / sold)
            realized_bear += bear_proceeds - bear_cost
        cash += eff_proceeds
        if kind == "rebound":
            rebound_sells.append({"date": day, "code": code, "qty": sold,
                                  "price": price, "proceeds": eff_proceeds,
                                  "pnl": pnl})

    def market_value(day: str) -> tuple[float, float, float]:
        """返回 (市值, 剩余成本, 空头批次浮盈)。"""
        mv = 0.0
        bear_unrealized = 0.0
        remaining_cost = 0.0
        for code, book in books.items():
            if book.qty <= 1e-9:
                continue
            entry = prices.get(code)
            close = close_on_or_before(entry, day) if entry else None
            remaining_cost += book.cost
            for q, c, tag in book.lots:
                px = close if close is not None else c  # 无行情按成本折算
                mv += q * px
                if tag == "bear":
                    bear_unrealized += q * (px - c)
        return mv, remaining_cost, bear_unrealized

    days = [d for d in amv_days if start <= d <= end]
    for day in days:
        regime = regime_map.get(day, "neutral")
        for t in trades_by_date.get(day, []):
            cat = t["category"]
            if cat == "买入":
                if scenario in ("no_bear_buys", "rebound_reduce") and regime == "bear":
                    skipped_buys.append(t)
                    continue
                unit_cost = (t["amount"] + t["fee"]) / t["qty"] if t["qty"] else 0.0
                tag = "bear" if regime == "bear" else "other"
                books[t["code"]].add(t["qty"], unit_cost, tag)
                cash += t["cash"]
            elif cat == "卖出":
                do_sell(t["code"], t["qty"], t["cash"], day, "ledger", t["price"])
            elif cat == "转债转入":
                cost_total = -t["cash"] if t["cash"] < 0 else 0.0
                books[t["code"]].add(t["qty"],
                                     cost_total / t["qty"] if t["qty"] else 0.0)
                cash += t["cash"]
            elif cat == "拆股":
                # 拆股/送转股：0 成本批次入库（发生金额为 0，不影响现金）
                books[t["code"]].add(t["qty"], 0.0)
                cash += t["cash"]
            elif cat in CASH_ONLY_CATEGORIES:
                cash += t["cash"]
                other_cf += t["cash"]
            else:  # 未知类别：保守按现金流处理
                cash += t["cash"]
                other_cf += t["cash"]

        if scenario == "rebound_reduce" and regime == "bear":
            for code in sorted(books):
                book = books[code]
                if book.qty <= 1e-9:
                    continue
                entry = prices.get(code)
                if entry is None or not is_rebound_day(entry, day):
                    continue
                close = close_on_or_before(entry, day)
                qty = book.qty * REBOUND_SELL_PCT
                amount = qty * close
                proceeds = amount * (1 - sell_fee_rate)
                do_sell(code, qty, proceeds, day, "rebound", close)

        mv, _, _ = market_value(day)
        equity_curve.append({"date": day, "cash": round(cash, 2),
                             "market_value": round(mv, 2),
                             "equity": round(cash + mv, 2)})

    mv_end, cost_end, bear_unrealized_end = market_value(end)
    unrealized = mv_end - cost_end
    dd = compute_drawdown([p["equity"] for p in equity_curve],
                          [p["date"] for p in equity_curve])

    positions_end = {code: {"qty": round(b.qty, 4), "cost": round(b.cost, 2)}
                     for code, b in sorted(books.items()) if b.qty > 1e-9}
    return {
        "scenario": scenario,
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "other_cashflow": round(other_cf, 2),
        "total_pnl": round(realized + unrealized + other_cf, 2),
        "end_cash": round(cash, 2),
        "end_market_value": round(mv_end, 2),
        "end_equity": round(cash + mv_end, 2),
        "max_drawdown": dd,
        "positions_end": positions_end,
        "skipped_buys": skipped_buys,
        "rebound_sells": rebound_sells,
        "shortfalls": shortfalls,
        "bear_buys_pnl_realized": round(realized_bear, 2),
        "bear_buys_pnl_unrealized": round(bear_unrealized_end, 2),
    }


def compute_drawdown(equity: list[float], dates: list[str]) -> dict[str, Any]:
    """峰谷最大回撤：绝对额（元）与相对峰值百分比。

    台账无初始入金记录，权益从 0 起步且可能为负；回撤区间内权益一旦
    跌破 0，相对百分比即失去意义，此时 max_dd_pct 置 None 并附 note。
    """
    peak = None
    peak_date = None
    max_dd = 0.0
    max_dd_pct: Optional[float] = None
    trough_date = None
    dd_peak_date = None
    for i, (v, d) in enumerate(zip(equity, dates)):
        if peak is None or v > peak:
            peak, peak_date = v, d
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
            trough_date = d
            dd_peak_date = peak_date
            window_min = min(equity[:i + 1])
            max_dd_pct = dd / peak if (peak and peak > 0 and window_min > 0) else None
    note = None
    if max_dd_pct is None and max_dd > 0:
        note = "权益从 0 起步且回撤区间内跌破 0，相对百分比无意义，以绝对额为准"
    return {
        "max_dd_yuan": round(max_dd, 2),
        "max_dd_pct": round(max_dd_pct * 100, 2) if max_dd_pct is not None else None,
        "peak_date": dd_peak_date,
        "trough_date": trough_date,
        "note": note,
    }


# ---------------------------------------------------------------- 统计汇总

def trade_distribution(trades: list[dict], regime_map: dict[str, str],
                       start: str, end: str) -> dict[str, Any]:
    """买入/卖出按区间（bear/bull/neutral）统计笔数与成交金额。"""
    dist: dict[str, dict] = {
        regime: {"buy_count": 0, "buy_amount": 0.0, "sell_count": 0, "sell_amount": 0.0}
        for regime in ("bear", "bull", "neutral")
    }
    for t in trades:
        if not (start <= t["date"] <= end):
            continue
        regime = regime_map.get(t["date"], "neutral")
        if t["category"] == "买入":
            dist[regime]["buy_count"] += 1
            dist[regime]["buy_amount"] += t["amount"]
        elif t["category"] == "卖出":
            dist[regime]["sell_count"] += 1
            dist[regime]["sell_amount"] += t["amount"]
    for v in dist.values():
        v["buy_amount"] = round(v["buy_amount"], 2)
        v["sell_amount"] = round(v["sell_amount"], 2)
    return dist


def avg_sell_fee_rate(trades: list[dict]) -> float:
    """台账实际平均卖出费率 = 卖出费用合计 / 卖出成交金额合计。"""
    fee = sum(t["fee"] for t in trades if t["category"] == "卖出")
    amount = sum(t["amount"] for t in trades if t["category"] == "卖出")
    return fee / amount if amount else 0.0


def check_positions(positions_end: dict[str, dict],
                    positions_path: Path = POSITIONS_PATH) -> list[dict]:
    """actual 场景期末持仓与 current_positions.json 对账。"""
    ref = json.loads(positions_path.read_text(encoding="utf-8"))
    ref_map = {str(p["代码"]).zfill(6): float(p["持有数量"]) for p in ref}
    codes = sorted(set(ref_map) | set(positions_end))
    rows = []
    for code in codes:
        got = positions_end.get(code, {}).get("qty", 0.0)
        want = ref_map.get(code, 0.0)
        rows.append({"code": code, "replay_qty": round(got, 2),
                     "ledger_ref_qty": want, "diff": round(got - want, 2)})
    return rows


# ---------------------------------------------------------------- 报告

def _wan(x: float) -> str:
    return f"{x / 10000:.2f} 万"


def build_report(result: dict[str, Any]) -> str:
    """生成 Markdown 报告文本。"""
    L: list[str] = []
    a = L.append
    s = result["scenarios"]
    act, nob, reb = s["actual"], s["no_bear_buys"], s["rebound_reduce"]
    rg = result["regime_stats"]

    a("# 0AMV 空头区间「只卖不买 + 反弹减仓」回测报告")
    a("")
    a(f"- 回测区间：{result['start']} ~ {result['end']}")
    a(f"- 生成时间：{result['generated_at']}")
    a(f"- 台账：{result['ledger_path']}（{result['trade_count']} 条）")
    a(f"- 区间定义：amv_change_pct <= {BEAR_THRESHOLD} 当日进入空头；"
      f">= +{BULL_THRESHOLD} 当日进入多头；触发日前为 neutral（边界含当日）")
    a("")

    a("## 1. 空头区间统计（2020 以来）")
    a("")
    a(f"- 空头区间次数：**{rg['bear_count']}** 次")
    a(f"- 空头总天数：**{rg['bear_days']}** 个交易日，"
      f"占同期 {rg['total_days']} 个交易日的 **{rg['bear_ratio_pct']}%**")
    a(f"- 多头总天数：{rg['bull_days']} 天；neutral：{rg['neutral_days']} 天")
    a("")
    a("| # | 开始(触发日) | 结束(含) | 天数 |")
    a("|---|---|---|---|")
    for i, seg in enumerate(rg["bear_segments"], 1):
        a(f"| {i} | {seg['start']} | {seg['end']} | {seg['days']} |")
    a("")

    a("## 2. 交易分布（按区间）")
    a("")
    a("| 区间 | 买入笔数 | 买入金额 | 卖出笔数 | 卖出金额 |")
    a("|---|---|---|---|---|")
    label = {"bear": "空头", "bull": "多头", "neutral": "neutral"}
    for k in ("bear", "bull", "neutral"):
        d = result["trade_distribution"][k]
        a(f"| {label[k]} | {d['buy_count']} | {_wan(d['buy_amount'])} | "
          f"{d['sell_count']} | {_wan(d['sell_amount'])} |")
    a("")

    a("## 3. 三场景对比")
    a("")
    a("| 指标 | actual 实际 | no_bear_buys 空头不买 | +rebound_reduce 反弹再减20% |")
    a("|---|---|---|---|")
    a(f"| 已实现盈亏 | {_wan(act['realized_pnl'])} | {_wan(nob['realized_pnl'])} | {_wan(reb['realized_pnl'])} |")
    a(f"| 期末浮存盈亏 | {_wan(act['unrealized_pnl'])} | {_wan(nob['unrealized_pnl'])} | {_wan(reb['unrealized_pnl'])} |")
    a(f"| 其他现金流(分红/费用/逆回购) | {_wan(act['other_cashflow'])} | {_wan(nob['other_cashflow'])} | {_wan(reb['other_cashflow'])} |")
    a(f"| **合计总盈亏** | **{_wan(act['total_pnl'])}** | **{_wan(nob['total_pnl'])}** | **{_wan(reb['total_pnl'])}** |")
    a(f"| 期末现金 | {_wan(act['end_cash'])} | {_wan(nob['end_cash'])} | {_wan(reb['end_cash'])} |")
    a(f"| 期末持仓市值 | {_wan(act['end_market_value'])} | {_wan(nob['end_market_value'])} | {_wan(reb['end_market_value'])} |")
    def _dd(v: dict) -> str:
        dd = v["max_drawdown"]
        pct = f"{dd['max_dd_pct']}%" if dd["max_dd_pct"] is not None else "n/a(权益曾跌破0)"
        return f"{_wan(dd['max_dd_yuan'])} ({pct}, {dd['peak_date']}→{dd['trough_date']})"

    a(f"| 最大回撤(组合权益口径) | {_dd(act)} | {_dd(nob)} | {_dd(reb)} |")
    a("")
    a("> 注：台账未记录出入金，权益从 0 起步，期末现金为负属口径产物；"
      "三场景之间的**差值**才是可比的。")
    a("")
    diff_nb = nob['total_pnl'] - act['total_pnl']
    diff_rb = reb['total_pnl'] - nob['total_pnl']
    a(f"> 空头不买 vs 实际：总盈亏**减少 {_wan(-diff_nb)}**（空头区买入实际是赚钱的）；"
      if diff_nb < 0 else
      f"> 空头不买 vs 实际：总盈亏**增加 {_wan(diff_nb)}**；")
    a(f"> 反弹再减仓 vs 空头不买：总盈亏**{'减少' if diff_rb < 0 else '增加'} "
      f"{_wan(abs(diff_rb))}**（减仓卖在低位、错过后续修复）。")
    a("")

    a("## 4. 空头区买入复盘（这些买单实际表现）")
    a("")
    bb = result["bear_buy_review"]
    a(f"- 空头区买入：**{bb['count']} 笔**，金额合计 **{_wan(bb['amount'])}**")
    a(f"- 其中已卖出部分实现盈亏：{_wan(bb['realized_pnl'])}")
    a(f"- 仍持有部分浮动盈亏：{_wan(bb['unrealized_pnl'])}")
    a(f"- **合计盈亏：{_wan(bb['total_pnl'])}**")
    if bb["total_pnl"] < 0:
        a(f"- 结论：若空头区不买，可避免损失 **{_wan(-bb['total_pnl'])}**")
    else:
        a(f"- 结论：若空头区不买，将错过收益 **{_wan(bb['total_pnl'])}**")
    a(f"- 同期全部总盈亏（actual）为 {_wan(act['total_pnl'])}，"
      f"空头区买入贡献 **{bb['share_of_actual_total_pnl_pct']}%**")
    a("")

    a("## 5. 反弹减仓明细摘要")
    a("")
    rs = reb["rebound_sells"]
    if rs:
        by_code: dict[str, dict] = {}
        for r in rs:
            d = by_code.setdefault(r["code"], {"count": 0, "qty": 0.0,
                                               "proceeds": 0.0, "pnl": 0.0})
            d["count"] += 1
            d["qty"] += r["qty"]
            d["proceeds"] += r["proceeds"]
            d["pnl"] += r["pnl"]
        a(f"- 反弹减仓共 **{len(rs)} 笔**，涉及 **{len(by_code)} 只**，"
          f"回收现金 {_wan(sum(r['proceeds'] for r in rs))}，"
          f"实现盈亏 {_wan(sum(r['pnl'] for r in rs))}")
        a("")
        a("| 代码 | 次数 | 卖出数量 | 回收现金 | 实现盈亏 |")
        a("|---|---|---|---|---|")
        for code, d in sorted(by_code.items(), key=lambda kv: kv[1]["pnl"]):
            a(f"| {code} | {d['count']} | {d['qty']:.0f} | {_wan(d['proceeds'])} | {_wan(d['pnl'])} |")
    else:
        a("- 回测区间内无反弹减仓触发。")
    a("")

    a("## 6. 自洽性校验（actual 期末持仓 vs current_positions.json）")
    a("")
    a("| 代码 | 重放数量 | 台账持仓 | 差值 |")
    a("|---|---|---|---|")
    for row in result["position_check"]:
        a(f"| {row['code']} | {row['replay_qty']} | {row['ledger_ref_qty']} | {row['diff']} |")
    a("")
    a(f"- 说明：{result['position_check_note']}")
    a("")

    a("## 7. 口径与数据缺口")
    a("")
    a("- 分红/除权除息、组合费用、逆回购（融券/融券购回）：只计现金、不动持仓（简单口径）。")
    a("- 例外：拆股（603606 +600 股）与 0 成本转债转入（159938 +22500 份）按 0 成本批次入库，"
      "否则台账重放不自洽（后续卖单无票可卖）。")
    a("- 行情为 vipdoc 不复权日线，分红除权缺口使个券浮盈偏保守，但分红现金已计入其他现金流。")
    a("- 反弹减仓费率取台账实际平均卖出费率："
      f"{result['sell_fee_rate'] * 100:.4f}%。")
    a("- 台账未记录出入金，现金从 0 起步，故期末现金为负值；三场景差值才是可比的。")
    sf = result.get("shortfalls", {})
    a("- 卖单超持仓截断（shortfall）：actual "
      f"{len(sf.get('actual', []))} 条、no_bear_buys {len(sf.get('no_bear_buys', []))} 条、"
      f"rebound_reduce {len(sf.get('rebound_reduce', []))} 条。"
      "反事实场景中被跳过的买入使后续台账卖单无票可卖，按可得数量撮合、发生金额按比例折算（详见 JSON）。")
    missing = result["missing_price_codes"]
    a(f"- vipdoc 缺数据代码（已跳过）：{missing if missing else '无'}")
    a("")
    return "\n".join(L)


# ---------------------------------------------------------------- 主流程

def run_backtest(start: str, end: str) -> dict[str, Any]:
    from local_tdx.compass_amv import parse_amv_daily

    trades = load_ledger()
    parsed = parse_amv_daily(since="1990-01-01")
    if parsed.get("error"):
        raise RuntimeError(f"0AMV 解析失败: {parsed['error']}")
    records = parsed["records"]
    regime_map = build_regime_map(records)
    amv_days = [r["date"] for r in records]

    trade_codes = [t["code"] for t in trades
                   if t["category"] in ("买入", "卖出", "转债转入", "拆股")]
    prices, missing = load_price_data(trade_codes)
    sell_fee_rate = avg_sell_fee_rate(trades)

    scenarios = {name: run_scenario(trades, regime_map, amv_days, prices,
                                    name, sell_fee_rate, start, end)
                 for name in SCENARIOS}

    days_since_2020 = [d for d in amv_days if d >= "2020-01-01"]
    bear_segs = regime_segments(regime_map, "bear", since="2020-01-01")
    bull_days = sum(1 for d in days_since_2020 if regime_map[d] == "bull")
    bear_days = sum(1 for d in days_since_2020 if regime_map[d] == "bear")
    regime_stats = {
        "bear_count": len(bear_segs),
        "bear_days": bear_days,
        "bull_days": bull_days,
        "neutral_days": len(days_since_2020) - bear_days - bull_days,
        "total_days": len(days_since_2020),
        "bear_ratio_pct": round(bear_days / len(days_since_2020) * 100, 2),
        "bear_segments": bear_segs,
    }

    act = scenarios["actual"]
    bear_buys = [t for t in trades
                 if t["category"] == "买入" and start <= t["date"] <= end
                 and regime_map.get(t["date"]) == "bear"]
    bear_total = round(act["bear_buys_pnl_realized"] + act["bear_buys_pnl_unrealized"], 2)
    bear_review = {
        "count": len(bear_buys),
        "amount": round(sum(t["amount"] for t in bear_buys), 2),
        "realized_pnl": act["bear_buys_pnl_realized"],
        "unrealized_pnl": act["bear_buys_pnl_unrealized"],
        "total_pnl": bear_total,
        "share_of_actual_total_pnl_pct": (
            round(bear_total / act["total_pnl"] * 100, 2) if act["total_pnl"] else None),
    }

    pos_check = check_positions(act["positions_end"])
    bad = [r for r in pos_check if abs(r["diff"]) > 1e-6]
    pos_note = ("重放持仓与台账持仓完全一致。" if not bad else
                f"{len(bad)} 只存在差值（可能源于分红送转/手工调整/台账外操作），见上表。")

    all_shortfalls = {
        name: sc["shortfalls"] for name, sc in scenarios.items()
    }

    result = {
        "start": start,
        "end": end,
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "ledger_path": str(LEDGER_PATH),
        "trade_count": len(trades),
        "sell_fee_rate": sell_fee_rate,
        "amv_identification": parsed.get("identification"),
        "amv_latest_date": records[-1]["date"] if records else None,
        "regime_stats": regime_stats,
        "trade_distribution": trade_distribution(trades, regime_map, start, end),
        "scenarios": scenarios,
        "bear_buy_review": bear_review,
        "position_check": pos_check,
        "position_check_note": pos_note,
        "missing_price_codes": missing,
        "shortfalls": all_shortfalls,
    }
    return result


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="0AMV 空头区间只卖不买/反弹减仓回测")
    ap.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（含），默认台账首笔")
    ap.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（含），默认台账末笔")
    ap.add_argument("--out-dir", default=str(REPORT_DIR), help="报告输出目录")
    ap.add_argument("--json-only", action="store_true", help="只打印 JSON，不写文件")
    args = ap.parse_args(argv)

    trades = load_ledger()
    start = args.start or min(t["date"] for t in trades)
    end = args.end or max(t["date"] for t in trades)

    result = run_backtest(start, end)

    if args.json_only:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = build_report(result)
    (out_dir / "0amv_bear_regime_backtest.md").write_text(md, encoding="utf-8")
    (out_dir / "0amv_bear_regime_backtest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"报告已写入 {out_dir / '0amv_bear_regime_backtest.md'}")
    print(f"数据已写入 {out_dir / '0amv_bear_regime_backtest.json'}")
    a, n, r = (result["scenarios"][k]["total_pnl"] for k in SCENARIOS)
    print(f"total_pnl: actual={a:.0f}  no_bear_buys={n:.0f}  rebound_reduce={r:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
