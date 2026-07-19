# -*- coding: utf-8 -*-
"""确定性周度复盘：脚本产出结构化事实与规则化归因，不含任何 LLM 判断。

用法：
    uv run python 07_tools/close_review/weekly_review.py --date 2026-07-19

--date 默认今天，取其所在 ISO 周，周一~周五为复盘区间。
输出：
    04_reviews/weekly/{iso_year}W{iso_week:02d}_weekly_review.json
    04_reviews/weekly/{iso_year}W{iso_week:02d}_weekly_review.md

归因规则（全部确定性，阈值集中在本文件顶部常量）：
- 计划外交易：成交日 D 的计划 = 最近一份早于 D 的 daily final_review 的
  next_day_plan.holding_plans（向回最多找 10 个自然日）。代码不在计划中 = 计划外；
  找不到任何前置 final_review = 数据缺口（记 unavailable，不算计划外）。
- 止损合规：亏损平仓单的已实现收益率 <= STOP_LOSS_PCT（b1 短线止损线 -7%）
  记为止损偏慢。用已实现收益率近似卖出时浮亏。
- 无交易确认完备性：本周交易日（工作日且不在官方休市区间）中无成交的日子，
  应在 _import_meta.json 的 no_trades_confirmed_dates 中有确认记录。
- 卖飞：卖出单的卖出价 vs 卖出日之后最近的 mfe_mae.json 中同代码的隐含 MFE 价
  （cost * (1 + mfe_pct/100)），且 mfe_date 晚于卖出日；隐含 MFE 价超过卖出价
  (1 + SELL_FLY_PCT) 记为卖飞候选。代码不在后续 mfe_mae 中 = 无法评估，不计命中。
- 持有期分布：平仓单按持有天数 <= SHORT_HOLD_DAYS 分组，统计短持有组的亏损贡献。
- 市场背景：0AMV > 4% = 多头，< -2.3% = 空头（与 final_review 口径一致），
  其余为震荡。统计空头天数占比，及卖出日处于空头状态的平仓单亏损占比。
- 持仓周度表现：价格取 final_review.revalued_positions（含权重）优先、
  holding_quotes 收盘价兜底；贡献估算 = 周初权重 × 周涨跌幅（pp）；
  B1 轨迹取本周各日 b1_holding_state 的 final_priority/final_action。
- 组合轨迹：revalued_positions 合计的每日总仓位/持仓市值；组合周收益 =
  首尾可得日市值比（不含现金/费用）；最大回撤按日市值序列；基准为上证每日涨跌复利。
- 每日建议事后检验：chief_decision 的市场状态/开仓权限分类为偏防守/偏进攻/中性；
  防守+次日跌 = 正确，防守+次日涨 = 失误（进攻对称）；中性不判定；次日行情缺失 = 待验证。
- 归因补充：组合周收益为负且存在建议失误 → [策略] wrong_advice_direction；
  组合周收益为负但有方向的建议全部正确 → [策略环境] adverse_market_environment。

盈亏口径：全台账 FIFO 配对（买入可早于本周），平仓单按 (代码, 卖出日) 聚合；
毛盈亏 = Σ(卖价 - 买入成本) * 数量；净盈亏 = 毛盈亏合计 - 本周全部成交费用。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import BASE  # noqa: E402

# 规则阈值
STOP_LOSS_PCT = -7.0       # b1 短线止损线：已实现亏损超过该值 = 止损偏慢
SELL_FLY_PCT = 0.03        # 后续 MFE 超卖出价 3% 以上 = 卖飞候选
SHORT_HOLD_DAYS = 20       # 短持有分界线（天）
PLAN_LOOKBACK_DAYS = 10    # 向前寻找计划来源 final_review 的最大自然日数
AMV_BULL_PCT = 4.0         # 0AMV 多头阈值
AMV_BEAR_PCT = -2.3        # 0AMV 空头阈值

BUY, SELL = "买入", "卖出"


def iso_week_range(day: str) -> dict:
    """返回 day 所在 ISO 周的周一~周五区间及 ISO 年/周编号。"""
    d = date.fromisoformat(day)
    iso = d.isocalendar()
    monday = d - timedelta(days=d.weekday())
    friday = monday + timedelta(days=4)
    return {
        "iso_year": iso[0],
        "iso_week": iso[1],
        "start": monday.isoformat(),
        "end": friday.isoformat(),
    }


def week_dates(week: dict) -> list[str]:
    start = date.fromisoformat(week["start"])
    return [(start + timedelta(days=i)).isoformat() for i in range(5)]


# ---------------------------------------------------------------- 输入加载

def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def parse_ledger(path: Path) -> list[dict] | None:
    """解析成交台账。只保留买入/卖出；转债转入等非交易类别忽略。"""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        side = (raw.get("交易类别") or "").strip()
        if side not in (BUY, SELL):
            continue
        try:
            rows.append({
                "date": (raw.get("成交日期") or "").strip(),
                "time": (raw.get("成交时间") or "").strip(),
                "code": (raw.get("代码") or "").strip(),
                "name": (raw.get("名称") or "").strip(),
                "side": side,
                "qty": float(raw.get("成交数量") or 0),
                "price": float(raw.get("成交价格") or 0),
                "amount": float(raw.get("成交金额") or 0),
                "fee": float(raw.get("费用") or 0),
            })
        except ValueError:
            continue
    rows.sort(key=lambda r: (r["date"], r["time"], 0 if r["side"] == BUY else 1))
    return rows


def fifo_pair(trades: list[dict]) -> list[dict]:
    """全台账 FIFO 配对，返回平仓单列表（按 (代码, 卖出日) 聚合）。

    每单字段：code/name/sell_date/sell_qty/avg_sell_price/avg_buy_cost/
    first_buy_date/avg_buy_date/hold_days/gross_pnl/pnl_pct。
    持有天数 = 卖出日 - 数量加权平均买入日（自然日）。
    """
    open_lots: dict[str, list[list]] = {}  # code -> [[qty, price, date], ...]
    sells_by_key: dict[tuple, dict] = {}
    order: list[tuple] = []
    for t in trades:
        if t["side"] == BUY:
            open_lots.setdefault(t["code"], []).append([t["qty"], t["price"], t["date"]])
            continue
        lots = open_lots.setdefault(t["code"], [])
        remaining = t["qty"]
        matched_cost = 0.0
        matched_qty = 0.0
        buy_dates: list[tuple[str, float]] = []
        while remaining > 1e-9 and lots:
            lot = lots[0]
            take = min(remaining, lot[0])
            matched_cost += take * lot[1]
            matched_qty += take
            buy_dates.append((lot[2], take))
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-9:
                lots.pop(0)
        key = (t["code"], t["date"])
        if key not in sells_by_key:
            sells_by_key[key] = {
                "code": t["code"], "name": t["name"], "sell_date": t["date"],
                "sell_qty": 0.0, "sell_amount": 0.0, "sell_fee": 0.0,
                "matched_cost": 0.0, "matched_qty": 0.0, "buy_dates": [],
            }
            order.append(key)
        agg = sells_by_key[key]
        agg["sell_qty"] += t["qty"]
        agg["sell_amount"] += t["amount"]
        agg["sell_fee"] += t["fee"]
        agg["matched_cost"] += matched_cost
        agg["matched_qty"] += matched_qty
        agg["buy_dates"].extend(buy_dates)
    closings = []
    for key in order:
        agg = sells_by_key[key]
        qty = agg["sell_qty"]
        mqty = agg["matched_qty"]
        avg_sell = agg["sell_amount"] / qty if qty else 0.0
        avg_cost = agg["matched_cost"] / mqty if mqty else None
        sell_d = date.fromisoformat(agg["sell_date"])
        if agg["buy_dates"]:
            total = sum(q for _, q in agg["buy_dates"])
            avg_buy_ordinal = sum(date.fromisoformat(d).toordinal() * q for d, q in agg["buy_dates"]) / total
            hold_days = sell_d.toordinal() - round(avg_buy_ordinal)
            first_buy = min(d for d, _ in agg["buy_dates"])
        else:
            hold_days = None
            first_buy = None
        gross = (avg_sell * mqty - agg["matched_cost"]) if mqty else None
        pnl_pct = (gross / agg["matched_cost"] * 100) if (gross is not None and agg["matched_cost"]) else None
        closings.append({
            "code": agg["code"], "name": agg["name"], "sell_date": agg["sell_date"],
            "sell_qty": qty, "avg_sell_price": round(avg_sell, 4),
            "avg_buy_cost": round(avg_cost, 4) if avg_cost is not None else None,
            "first_buy_date": first_buy, "hold_days": hold_days,
            "gross_pnl": round(gross, 2) if gross is not None else None,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "unmatched_qty": round(qty - mqty, 4),
        })
    return closings


def load_amv_regimes(path: Path) -> dict[str, dict] | None:
    """0AMV 历史 -> {date: {amv_change_pct, regime}}，同日取最后一条记录。"""
    if not path.exists():
        return None
    regimes: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        day = rec.get("date")
        pct = rec.get("amv_change_pct")
        if not day or pct is None:
            continue
        pct = float(pct)
        regime = "多头" if pct > AMV_BULL_PCT else ("空头" if pct < AMV_BEAR_PCT else "震荡")
        regimes[day] = {"amv_change_pct": pct, "regime": regime}
    return regimes


def trading_days_of_week(base: Path, days: list[str]) -> dict[str, bool | None]:
    """本周各日是否交易日：工作日且不在官方休市区间 = True；年份未登记 = None。"""
    cfg = load_json(base / "00_governance" / "CN_TRADING_CALENDAR.json", {})
    official = (cfg.get("official_years") or {})
    result = {}
    for day in days:
        d = date.fromisoformat(day)
        if d.weekday() >= 5:
            result[day] = False
            continue
        year_cfg = official.get(str(d.year))
        if year_cfg is None:
            result[day] = None
            continue
        closed = any(r["start"] <= day <= r["end"] for r in year_cfg.get("closed_ranges", []))
        result[day] = not closed
    return result


def find_plan_for_day(base: Path, day: str) -> tuple[dict | None, str | None]:
    """找 day 的交易计划 = 最近一份早于 day 的 final_review 的 next_day_plan。

    返回 (holding_plans, 来源日期)；找不到返回 (None, None)。
    """
    cursor = date.fromisoformat(day) - timedelta(days=1)
    for _ in range(PLAN_LOOKBACK_DAYS):
        path = base / "04_reviews" / "daily" / f"{cursor.isoformat()}_final_review.json"
        if path.exists():
            review = load_json(path, {})
            plan = (review.get("next_day_plan") or {}).get("holding_plans")
            if plan is not None:
                return plan, cursor.isoformat()
        cursor -= timedelta(days=1)
    return None, None


def load_mfe_after(base: Path, sell_date: str) -> dict[str, dict] | None:
    """卖出日之后最近的 mfe_mae.json -> {code: entry}；无任何文件返回 None。"""
    holdings_dir = base / "01_data" / "holdings"
    if not holdings_dir.exists():
        return None
    candidates = sorted(holdings_dir.glob("*_mfe_mae.json"))
    for path in candidates:
        day = path.name.split("_")[0]
        if day > sell_date:
            data = load_json(path, {})
            return {str(h.get("code")): h for h in data.get("holdings", [])}
    return None


def load_holding_quote_closes(base: Path, day: str) -> dict[str, float] | None:
    """holding_quotes.json -> {code: close}；文件缺失返回 None。"""
    data = load_json(base / "01_data" / "market" / f"{day}_holding_quotes.json", None)
    if not isinstance(data, dict):
        return None
    out = {}
    for q in data.get("quotes") or []:
        close = q.get("close") if q.get("close") is not None else q.get("price")
        if q.get("code") and close is not None:
            out[str(q["code"])] = float(close)
    return out


def sse_daily_map(base: Path, start: str, end: str) -> dict[str, dict]:
    """扫描 [start, end] 内 market_timing_input，按 latest_date 重建上证收盘价序列。

    采集盘前运行，文件日期 != 数据日期，故以 latest_date(YYYYMMDD) 为键；
    daily_change_pct 多为 None，调用方需用相邻收盘价比值兜底。
    """
    out: dict[str, dict] = {}
    cursor = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while cursor <= last:
        data = load_json(base / "01_data" / "market" / f"{cursor.isoformat()}_market_timing_input.json", None)
        if isinstance(data, dict):
            sse = (data.get("a_share_indices") or {}).get("上证指数") or {}
            latest = str(sse.get("latest_date") or "")
            close = sse.get("latest_close")
            if len(latest) == 8 and close is not None:
                iso = f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"
                prev = out.get(iso) or {}
                out[iso] = {
                    "close": float(close),
                    "chg": sse.get("daily_change_pct") if sse.get("daily_change_pct") is not None else prev.get("chg"),
                }
        cursor += timedelta(days=1)
    return out


def sse_change(sse_map: dict[str, dict], day: str) -> float | None:
    """day 当日上证涨跌幅：优先文件记录值，否则用前一可得收盘价比值。"""
    entry = sse_map.get(day)
    if not entry:
        return None
    if entry.get("chg") is not None:
        return round(float(entry["chg"]), 2)
    prior = [d for d in sorted(sse_map) if d < day]
    if not prior:
        return None
    prev_close = sse_map[prior[-1]]["close"]
    if not prev_close:
        return None
    return round((entry["close"] / prev_close - 1) * 100, 2)


def holding_week_performance(base: Path, days: list[str], daily_reviews: dict,
                             unavailable: list[str]) -> dict:
    """板块1：每只持仓的周度表现。

    价格来源优先级：final_review.revalued_positions(含权重) > holding_quotes(仅收盘价)。
    权重/浮动盈亏字段在 revalued 中为小数(0.185=18.5%)，输出统一转百分数。
    贡献估算 = 周初权重 × 周涨跌幅（组合百分点，pp）。
    """
    per_day: dict[str, dict[str, dict]] = {}
    for d in days:
        entry: dict[str, dict] = {}
        review = daily_reviews.get(d) or {}
        for p in review.get("revalued_positions") or []:
            if p.get("code") and p.get("close") is not None:
                entry[str(p["code"])] = {
                    "close": float(p["close"]), "weight": p.get("position_pct"),
                    "pnl_pct": p.get("pnl_pct"), "cost": p.get("cost"), "name": p.get("name"),
                }
        quotes = load_holding_quote_closes(base, d)
        for code, close in (quotes or {}).items():
            entry.setdefault(code, {"close": close, "weight": None, "pnl_pct": None, "cost": None, "name": None})
        if entry:
            per_day[d] = entry
    codes = sorted({c for e in per_day.values() for c in e})
    if not codes:
        unavailable.append("持仓周度表现：本周无 revalued_positions 与 holding_quotes 数据")
        if not any((base / "01_data" / "holdings" / f"{d}_b1_holding_state.json").exists() for d in days):
            unavailable.append("B1 持仓状态：本周无 b1_holding_state 数据")
        return {"rows": [], "days_with_data": [], "b1_trajectory": {}}
    # 名称兜底：current_positions.json
    names = {}
    for p in load_json(base / "01_data" / "trades" / "current_positions.json", []) or []:
        if p.get("代码"):
            names[str(p["代码"])] = p.get("名称")
    rows = []
    for code in codes:
        series = [(d, per_day[d][code]) for d in days if code in per_day.get(d, {})]
        first_d, first = series[0]
        last_d, last = series[-1]
        change = round((last["close"] / first["close"] - 1) * 100, 2) if first["close"] else None
        # 权重取各自首个/末个有权重值的日期（价格日期与权重日期可能不同，如周初仅有 quotes）
        weights = [(d, e["weight"]) for d, e in series if e.get("weight") is not None]
        w0 = round(weights[0][1] * 100, 2) if weights else None
        w1 = round(weights[-1][1] * 100, 2) if weights else None
        contribution = round(weights[0][1] * change, 2) if (weights and change is not None) else None
        float_pnl = round(last["pnl_pct"] * 100, 2) if last.get("pnl_pct") is not None else None
        name = last.get("name") or first.get("name") or names.get(code)
        rows.append({
            "code": code, "name": name,
            "first_date": first_d, "first_close": first["close"],
            "last_date": last_d, "last_close": last["close"],
            "week_change_pct": change,
            "first_weight_pct": w0, "last_weight_pct": w1,
            "first_weight_date": weights[0][0] if weights else None,
            "last_weight_date": weights[-1][0] if weights else None,
            "float_pnl_pct": float_pnl, "contribution_pp": contribution,
        })
    rows.sort(key=lambda r: (r["contribution_pp"] if r["contribution_pp"] is not None else 0))
    # B1 状态轨迹
    b1_files = {d: load_json(base / "01_data" / "holdings" / f"{d}_b1_holding_state.json", None) for d in days}
    if not any(isinstance(v, list) for v in b1_files.values()):
        unavailable.append("B1 持仓状态：本周无 b1_holding_state 数据")
    trajectory = {}
    for code in codes:
        steps = []
        for d in days:
            data = b1_files[d]
            if not isinstance(data, list):
                continue
            for item in data:
                if str(item.get("code")) == code:
                    steps.append({"date": d, "priority": item.get("final_priority"),
                                  "action": item.get("final_action")})
        trajectory[code] = steps
    return {"rows": rows, "days_with_data": sorted(per_day), "b1_trajectory": trajectory}


def portfolio_trajectory(days: list[str], trading_days: list[str], daily_reviews: dict,
                         sse_map: dict[str, dict], unavailable: list[str]) -> dict:
    """板块2：组合与账户轨迹。

    口径：总仓位/持仓市值 = 当日 revalued_positions 合计（权重分母为当日估算总权益）；
    当日有持仓缺报价（market_value 为空）时标记 partial，周收益与回撤只用完整日计算，
    避免缺报价造成的虚假跳水；基准为上证指数每日涨跌幅复利累计。
    """
    daily = []
    partial_notes = []
    for d in days:
        rp = (daily_reviews.get(d) or {}).get("revalued_positions") or []
        if not rp:
            continue
        priced = [p for p in rp if p.get("market_value") is not None]
        unpriced = sorted(str(p.get("code")) for p in rp if p.get("market_value") is None)
        mv = sum(p["market_value"] for p in priced)
        wt = sum(p.get("position_pct") or 0 for p in priced)
        if unpriced:
            partial_notes.append(f"{d} 缺 {','.join(unpriced)} 报价，当日市值/仓位为部分合计，不参与周收益与回撤")
        daily.append({"date": d, "market_value": round(mv, 2), "total_position_pct": round(wt * 100, 2),
                      "partial": bool(unpriced), "unpriced_codes": unpriced})
    complete = [pt for pt in daily if not pt["partial"]]
    if len(complete) < 2:
        unavailable.append("组合轨迹：本周完整 revalued_positions 不足两日，周收益与回撤 unavailable")
    week_return = None
    max_drawdown = None
    if len(complete) >= 2 and complete[0]["market_value"]:
        week_return = round((complete[-1]["market_value"] / complete[0]["market_value"] - 1) * 100, 2)
        peak = complete[0]["market_value"]
        dd = 0.0
        for pt in complete:
            peak = max(peak, pt["market_value"])
            dd = min(dd, pt["market_value"] / peak - 1)
        max_drawdown = round(dd * 100, 2)
    bench_changes = {d: sse_change(sse_map, d) for d in trading_days}
    known = [c for c in bench_changes.values() if c is not None]
    bench_week = None
    if known:
        acc = 1.0
        for c in known:
            acc *= 1 + c / 100
        bench_week = round((acc - 1) * 100, 2)
    bench_missing = [d for d, c in bench_changes.items() if c is None]
    if trading_days and not known:
        unavailable.append("基准对照：本周上证指数数据缺失")
    return {
        "daily": daily,
        "week_return_pct": week_return,
        "max_drawdown_pct": max_drawdown,
        "benchmark_daily_chg": bench_changes,
        "benchmark_week_pct": bench_week,
        "benchmark_missing_days": bench_missing,
        "partial_notes": partial_notes,
    }


def classify_advice(market_state: str | None, permission: str | None) -> str:
    """建议方向分类：bearish / bullish / neutral（确定性关键词规则）。"""
    text = f"{market_state or ''} {permission or ''}"
    if any(w in text for w in ("进攻", "积极", "偏多")):
        return "bullish"
    if any(w in text for w in ("防守", "谨慎", "偏弱", "禁止")):
        return "bearish"
    return "neutral"


def advice_review(base: Path, trading_days: list[str], sse_map: dict[str, dict],
                  next_trading_day, unavailable: list[str]) -> dict:
    """板块3：每日建议事后检验。

    判定规则：bearish 建议 + 次日下跌 = 正确；bearish + 次日上涨 = 失误；
    bullish 对称；neutral = 不判定；次日行情缺失 = 待验证。
    """
    rows = []
    for d in trading_days:
        chief = load_json(base / "01_data" / "decisions" / f"{d}_chief_decision.json", None)
        if not isinstance(chief, dict):
            rows.append({"date": d, "available": False, "verdict": "unavailable"})
            continue
        direction = classify_advice(chief.get("market_state"), chief.get("new_position_permission"))
        nxt = next_trading_day(d)
        chg = sse_change(sse_map, nxt) if nxt else None
        if direction == "neutral":
            verdict = "中性不判定"
        elif chg is None:
            verdict = "待验证"
        elif (direction == "bearish" and chg < 0) or (direction == "bullish" and chg > 0):
            verdict = "正确"
        else:
            verdict = "失误"
        rows.append({
            "date": d, "available": True,
            "market_state": chief.get("market_state"),
            "market_score": chief.get("market_score"),
            "total_position_range": chief.get("total_position_range"),
            "new_position_permission": chief.get("new_position_permission"),
            "direction": direction, "next_day": nxt, "next_day_sse_chg_pct": chg,
            "verdict": verdict,
        })
    if trading_days and not any(r["available"] for r in rows):
        unavailable.append("每日建议检验：本周无 chief_decision 数据")
    return {"rows": rows}


# ---------------------------------------------------------------- 归因

def build_weekly_review(base: Path, day: str) -> dict:
    week = iso_week_range(day)
    days = week_dates(week)
    unavailable: list[str] = []
    execution_issues: list[dict] = []
    strategy_issues: list[dict] = []

    # --- 台账与周度交易统计 ---
    ledger_path = base / "01_data" / "trades" / "master_trade_ledger.csv"
    all_trades = parse_ledger(ledger_path)
    if all_trades is None:
        unavailable.append(f"成交台账缺失：{ledger_path}")
        all_trades = []
    week_trades = [t for t in all_trades if week["start"] <= t["date"] <= week["end"]]
    buys = [t for t in week_trades if t["side"] == BUY]
    sells = [t for t in week_trades if t["side"] == SELL]
    fee_total = round(sum(t["fee"] for t in week_trades), 2)
    amount_total = round(sum(t["amount"] for t in week_trades), 2)

    # --- FIFO 盈亏 ---
    closings_all = fifo_pair(all_trades)
    closings = [c for c in closings_all if week["start"] <= c["sell_date"] <= week["end"]]
    valued = [c for c in closings if c["gross_pnl"] is not None]
    gross_total = round(sum(c["gross_pnl"] for c in valued), 2)
    net_total = round(gross_total - fee_total, 2)
    wins = [c for c in valued if c["gross_pnl"] > 0]
    losses = [c for c in valued if c["gross_pnl"] < 0]
    win_rate = round(len(wins) / len(valued) * 100, 2) if valued else None
    avg_win = sum(c["gross_pnl"] for c in wins) / len(wins) if wins else None
    avg_loss = abs(sum(c["gross_pnl"] for c in losses)) / len(losses) if losses else None
    pl_ratio = round(avg_win / avg_loss, 2) if (avg_win and avg_loss) else None
    hold_vals = [c["hold_days"] for c in valued if c["hold_days"] is not None]
    avg_hold = round(sum(hold_vals) / len(hold_vals), 1) if hold_vals else None

    # --- 交易日历 ---
    td_map = trading_days_of_week(base, days)
    trading_days = [d for d in days if td_map[d] is True]
    unknown_td = [d for d in days if td_map[d] is None]
    if unknown_td:
        unavailable.append(f"交易日历未覆盖，交易日状态未知：{', '.join(unknown_td)}")

    # --- 每日复盘覆盖 ---
    daily_reviews = {}
    for d in days:
        path = base / "04_reviews" / "daily" / f"{d}_final_review.json"
        if path.exists():
            daily_reviews[d] = load_json(path, {})
    missing_reviews = [d for d in trading_days if d not in daily_reviews]
    if missing_reviews:
        unavailable.append(f"缺少每日复盘：{', '.join(missing_reviews)}")

    # --- 执行维度 1：计划外交易 ---
    plan_checks = []
    for t in week_trades:
        plan, source = find_plan_for_day(base, t["date"])
        if plan is None:
            unavailable.append(f"{t['date']} 无前置 final_review，{t['code']} 计划归属无法判定")
            plan_checks.append({"trade": t, "status": "unknown", "plan_source": None})
            continue
        planned = any(str(p.get("code")) == t["code"] for p in plan)
        plan_checks.append({"trade": t, "status": "planned" if planned else "unplanned", "plan_source": source})
    unplanned = [p for p in plan_checks if p["status"] == "unplanned"]
    known = [p for p in plan_checks if p["status"] != "unknown"]
    unplanned_ratio = round(len(unplanned) / len(known) * 100, 2) if known else None
    if unplanned:
        execution_issues.append({
            "rule": "unplanned_trade",
            "summary": f"计划外交易 {len(unplanned)}/{len(known)} 笔（占比 {unplanned_ratio}%）",
            "evidence": [{"date": p["trade"]["date"], "code": p["trade"]["code"],
                          "name": p["trade"]["name"], "side": p["trade"]["side"],
                          "plan_source": p["plan_source"]} for p in unplanned],
        })

    # --- 执行维度 2：止损合规 ---
    slow_stops = [c for c in losses if c["pnl_pct"] is not None and c["pnl_pct"] <= STOP_LOSS_PCT]
    if slow_stops:
        execution_issues.append({
            "rule": "slow_stop_loss",
            "summary": f"止损偏慢 {len(slow_stops)} 单：已实现亏损超过 {STOP_LOSS_PCT}% 止损线",
            "evidence": [{"code": c["code"], "name": c["name"], "sell_date": c["sell_date"],
                          "pnl_pct": c["pnl_pct"], "hold_days": c["hold_days"]} for c in slow_stops],
        })

    # --- 执行维度 3：无交易确认完备性 ---
    meta_path = base / "01_data" / "trades" / "_import_meta.json"
    meta = load_json(meta_path, None)
    confirmed_no_trade: dict = {}
    if meta is None:
        if any(d not in {t["date"] for t in week_trades} for d in trading_days):
            unavailable.append(f"无交易确认元数据缺失：{meta_path}")
    else:
        confirmed_no_trade = meta.get("no_trades_confirmed_dates") or {}
    traded_dates = {t["date"] for t in week_trades}
    no_trade_days = [d for d in trading_days if d not in traded_dates]
    unconfirmed = [d for d in no_trade_days if not confirmed_no_trade.get(d)]
    if unconfirmed and meta is not None:
        execution_issues.append({
            "rule": "no_trade_confirmation_missing",
            "summary": f"无交易确认缺失 {len(unconfirmed)} 天：{', '.join(unconfirmed)}",
            "evidence": {"no_trade_days": no_trade_days, "confirmed": sorted(confirmed_no_trade)},
        })

    # --- 策略维度 1：卖飞分析 ---
    sell_fly = []
    sell_fly_unevaluated = []
    for c in closings:
        mfe_map = load_mfe_after(base, c["sell_date"])
        if mfe_map is None:
            sell_fly_unevaluated.append({"code": c["code"], "reason": "卖出日之后无 mfe_mae 数据"})
            continue
        entry = mfe_map.get(c["code"])
        if not entry or entry.get("mfe_pct") is None or entry.get("cost") is None:
            sell_fly_unevaluated.append({"code": c["code"], "reason": "后续 mfe_mae 中无该代码"})
            continue
        mfe_date = entry.get("mfe_date")
        implied_mfe_price = entry["cost"] * (1 + entry["mfe_pct"] / 100)
        if mfe_date and mfe_date > c["sell_date"] and implied_mfe_price > c["avg_sell_price"] * (1 + SELL_FLY_PCT):
            sell_fly.append({
                "code": c["code"], "name": c["name"], "sell_date": c["sell_date"],
                "sell_price": c["avg_sell_price"], "implied_mfe_price": round(implied_mfe_price, 4),
                "mfe_date": mfe_date,
                "overshoot_pct": round((implied_mfe_price / c["avg_sell_price"] - 1) * 100, 2),
            })
    if sell_fly:
        strategy_issues.append({
            "rule": "sell_fly",
            "summary": f"卖飞候选 {len(sell_fly)} 单：后续 MFE 超卖出价 {SELL_FLY_PCT * 100:.0f}% 以上",
            "evidence": sell_fly,
        })

    # --- 策略维度 2：亏损单持有期分布 ---
    short_losses = [c for c in losses if c["hold_days"] is not None and c["hold_days"] <= SHORT_HOLD_DAYS]
    long_losses = [c for c in losses if c["hold_days"] is not None and c["hold_days"] > SHORT_HOLD_DAYS]
    total_loss = sum(c["gross_pnl"] for c in losses)
    short_loss_sum = sum(c["gross_pnl"] for c in short_losses)
    short_loss_share = round(short_loss_sum / total_loss * 100, 2) if total_loss else None
    if losses:
        strategy_issues.append({
            "rule": "short_hold_loss_profile",
            "summary": (f"{SHORT_HOLD_DAYS} 天以内平仓的亏损单 {len(short_losses)}/{len(losses)} 笔，"
                        f"贡献亏损 {short_loss_share}%（历史画像：短持有交易贡献主要亏损）"),
            "evidence": {
                "short_hold_loss_count": len(short_losses),
                "long_hold_loss_count": len(long_losses),
                "short_hold_loss_amount": round(short_loss_sum, 2),
                "total_loss_amount": round(total_loss, 2),
                "short_hold_loss_share_pct": short_loss_share,
            },
        })

    # --- 策略维度 3：市场背景 ---
    amv_path = base / "01_data" / "market" / "0amv_observations.jsonl"
    regimes = load_amv_regimes(amv_path)
    bear_days: list[str] = []
    bear_loss_share = None
    if regimes is None:
        unavailable.append(f"0AMV 历史缺失：{amv_path}")
    else:
        observed = [d for d in trading_days if d in regimes]
        bear_days = [d for d in observed if regimes[d]["regime"] == "空头"]
        bear_closings = [c for c in losses if regimes.get(c["sell_date"], {}).get("regime") == "空头"]
        if losses and observed:
            bear_loss = sum(c["gross_pnl"] for c in bear_closings)
            bear_loss_share = round(bear_loss / total_loss * 100, 2) if total_loss else None
        elif not observed:
            unavailable.append("本周交易日无 0AMV 观测记录")
    bear_day_ratio = round(len(bear_days) / len([d for d in trading_days if regimes and d in regimes]) * 100, 2) \
        if regimes and any(d in regimes for d in trading_days) else None

    # --- 风控决策覆盖（事实记录，不归因） ---
    risk_days = [d for d in days if (base / "01_data" / "risk" / f"{d}_risk_decision.json").exists()]
    risk_levels = {}
    for d in risk_days:
        risk_levels[d] = load_json(base / "01_data" / "risk" / f"{d}_risk_decision.json", {}).get("risk_level")

    # --- 板块1：持仓周度表现 ---
    holdings_section = holding_week_performance(base, days, daily_reviews, unavailable)

    # --- 板块2/3 的公共输入：上证收盘价序列（向前多扫 7 天取周初前收盘，向后多扫 7 天取周五次日） ---
    sse_start = (date.fromisoformat(week["start"]) - timedelta(days=7)).isoformat()
    sse_end = (date.fromisoformat(week["end"]) + timedelta(days=7)).isoformat()
    sse_map = sse_daily_map(base, sse_start, sse_end)
    td_extended = trading_days_of_week(
        base, days + [(date.fromisoformat(week["end"]) + timedelta(days=i)).isoformat() for i in range(1, 8)]
    )

    def next_trading_day(day: str) -> str | None:
        cursor = date.fromisoformat(day) + timedelta(days=1)
        for _ in range(7):
            if td_extended.get(cursor.isoformat()):
                return cursor.isoformat()
            cursor += timedelta(days=1)
        return None

    # --- 板块2：组合与账户轨迹 ---
    portfolio = portfolio_trajectory(days, trading_days, daily_reviews, sse_map, unavailable)

    # --- 板块3：每日建议事后检验 ---
    advice = advice_review(base, trading_days, sse_map, next_trading_day, unavailable)

    # --- 归因：建议检验 vs 组合结果 ---
    environment_issues: list[dict] = []
    advice_wrong = [r for r in advice["rows"] if r.get("verdict") == "失误"]
    advice_correct = [r for r in advice["rows"] if r.get("verdict") == "正确"]
    week_ret = portfolio["week_return_pct"]
    if week_ret is not None and week_ret < 0:
        if advice_wrong:
            strategy_issues.append({
                "rule": "wrong_advice_direction",
                "summary": (f"组合周收益 {week_ret}% 为负，且 {len(advice_wrong)} 天操作建议方向失误"
                            f"（{', '.join(r['date'] for r in advice_wrong)}），亏损含可避免部分"),
                "evidence": [{"date": r["date"], "market_state": r["market_state"],
                              "next_day": r["next_day"], "next_day_sse_chg_pct": r["next_day_sse_chg_pct"]}
                             for r in advice_wrong],
            })
        elif advice_correct:
            environment_issues.append({
                "rule": "adverse_market_environment",
                "summary": (f"组合周收益 {week_ret}% 为负，但 {len(advice_correct)} 天有方向的建议全部正确，"
                            "亏损归因于市场环境而非执行/建议失误"),
                "evidence": {"week_return_pct": week_ret,
                             "benchmark_week_pct": portfolio["benchmark_week_pct"],
                             "correct_days": [r["date"] for r in advice_correct]},
            })

    facts = {
        "trade_count": len(week_trades),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "codes": sorted({t["code"] for t in week_trades}),
        "amount_total": amount_total,
        "fee_total": fee_total,
        "closing_count": len(closings),
        "gross_pnl": gross_total,
        "net_pnl": net_total,
        "win_rate_pct": win_rate,
        "profit_loss_ratio": pl_ratio,
        "avg_hold_days": avg_hold,
        "unplanned_ratio_pct": unplanned_ratio,
        "slow_stop_count": len(slow_stops),
        "no_trade_days": no_trade_days,
        "no_trade_unconfirmed": unconfirmed,
        "sell_fly_count": len(sell_fly),
        "sell_fly_unevaluated": sell_fly_unevaluated,
        "short_hold_loss_share_pct": short_loss_share,
        "bear_days": bear_days,
        "bear_day_ratio_pct": bear_day_ratio,
        "bear_loss_share_pct": bear_loss_share,
        "risk_levels": risk_levels,
        "daily_review_days": sorted(daily_reviews),
        "portfolio_week_return_pct": portfolio["week_return_pct"],
        "advice_correct_count": len(advice_correct),
        "advice_wrong_count": len(advice_wrong),
    }

    return {
        "iso_year": week["iso_year"],
        "iso_week": week["iso_week"],
        "range": {"start": week["start"], "end": week["end"]},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trading_days": trading_days,
        "facts": facts,
        "execution_issues": execution_issues,
        "strategy_issues": strategy_issues,
        "environment_issues": environment_issues,
        "unavailable": unavailable,
        "holding_performance": holdings_section,
        "portfolio": portfolio,
        "advice_review": advice,
        "details": {
            "trades": week_trades,
            "closings": closings,
            "plan_checks": [
                {"date": p["trade"]["date"], "code": p["trade"]["code"], "name": p["trade"]["name"],
                 "side": p["trade"]["side"], "status": p["status"], "plan_source": p["plan_source"]}
                for p in plan_checks
            ],
        },
    }


# ---------------------------------------------------------------- Markdown

def fmt_money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "unavailable"


def render_markdown(review: dict) -> str:
    f = review["facts"]
    r = review["range"]

    def or_na(v, suffix: str = "") -> str:
        return f"{v}{suffix}" if v is not None else "unavailable"

    hold_text = f"{f['avg_hold_days']} 天" if f["avg_hold_days"] is not None else "unavailable"
    lines = [
        f"# {review['iso_year']}W{review['iso_week']:02d} 周度复盘（{r['start']} ~ {r['end']}）",
        "",
        f"> 生成时间：{review['generated_at']}",
        f"> 交易日：{len(review['trading_days'])} 天（{', '.join(review['trading_days']) or '无'}）",
        f"> 成交：**{f['trade_count']}** 笔（买 {f['buy_count']} / 卖 {f['sell_count']}）；"
        f"平仓 **{f['closing_count']}** 单",
        f"> 已实现毛盈亏：**{fmt_money(f['gross_pnl'])}**；扣本周费用 {fmt_money(f['fee_total'])} 后净盈亏 "
        f"**{fmt_money(f['net_pnl'])}**",
        f"> 胜率：**{or_na(f['win_rate_pct'], '%')}**；"
        f"盈亏比：{or_na(f['profit_loss_ratio'])}；"
        f"平均持有：{hold_text}",
        "",
        "## 1. 本周概览",
        "",
        f"- 复盘区间：{r['start']} ~ {r['end']}（ISO {review['iso_year']}-W{review['iso_week']:02d}）",
        f"- 成交金额合计：{fmt_money(f['amount_total'])}；费用合计：{fmt_money(f['fee_total'])}",
        f"- 涉及代码：{', '.join(f['codes']) or '无'}",
        f"- 每日复盘覆盖：{', '.join(f['daily_review_days']) or '无'}",
        f"- 风控等级：{json.dumps(f['risk_levels'], ensure_ascii=False) if f['risk_levels'] else 'unavailable'}",
        "",
        "## 2. 持仓周度表现",
        "",
    ]
    hp = review["holding_performance"]
    if hp["rows"]:
        lines += [
            "| 代码 | 名称 | 周初价(日) | 周末价(日) | 周涨跌幅 | 周初权重 | 周末权重 | 浮动盈亏 | 贡献估算 |",
            "|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
        for row in hp["rows"]:
            lines.append(
                f"| {row['code']} | {row['name'] or ''} | {row['first_close']:g}({row['first_date'][5:]}) | "
                f"{row['last_close']:g}({row['last_date'][5:]}) | {or_na(row['week_change_pct'], '%')} | "
                f"{or_na(row['first_weight_pct'], '%')} | {or_na(row['last_weight_pct'], '%')} | "
                f"{or_na(row['float_pnl_pct'], '%')} | {or_na(row['contribution_pp'], 'pp')} |")
        traj = hp["b1_trajectory"]
        if any(traj.values()):
            lines += ["", "### B1 状态/风控标志周轨迹", ""]
            for row in hp["rows"]:
                steps = traj.get(row["code"]) or []
                if steps:
                    text = " → ".join(f"{s['date'][5:]}:{s['priority']} {s['action']}" for s in steps)
                    lines.append(f"- {row['code']} {row['name'] or ''}：{text}")
                else:
                    lines.append(f"- {row['code']} {row['name'] or ''}：`unavailable`（本周无 B1 状态记录）")
    else:
        lines.append("- `unavailable`：本周无持仓价格数据。")
    pf = review["portfolio"]
    lines += ["", "## 3. 组合与账户轨迹", ""]
    if pf["daily"]:
        lines += [
            "| 日期 | 总仓位 | 持仓市值 | 上证当日涨跌 |",
            "|---|---:|---:|---:|",
        ]
        for pt in pf["daily"]:
            chg = pf["benchmark_daily_chg"].get(pt["date"])
            mark = "（部分）" if pt.get("partial") else ""
            lines.append(f"| {pt['date']} | {pt['total_position_pct']}%{mark} | "
                         f"{pt['market_value']:,.2f}{mark} | {or_na(chg, '%')} |")
        lines.append("")
    else:
        lines.append("- `unavailable`：本周无每日组合重估数据。")
    for note in pf.get("partial_notes", []):
        lines.append(f"- 数据注意：{note}")
    lines.append(f"- 组合周收益率估算（完整日持仓市值口径，不含现金/费用）：**{or_na(pf['week_return_pct'], '%')}**")
    lines.append(f"- 周内最大回撤（按日市值序列）：{or_na(pf['max_drawdown_pct'], '%')}")
    bench_note = f"（缺 {', '.join(pf['benchmark_missing_days'])} 的数据）" if pf["benchmark_missing_days"] else ""
    lines.append(f"- 基准对照：上证指数本周 {or_na(pf['benchmark_week_pct'], '%')}{bench_note}")
    lines += ["", "## 4. 交易明细", ""]
    trades = review["details"]["trades"]
    if trades:
        lines += [
            "| 日期 | 代码 | 名称 | 方向 | 数量 | 价格 | 金额 | 费用 |",
            "|---|---|---|---|---:|---:|---:|---:|",
        ]
        for t in trades:
            lines.append(f"| {t['date']} | {t['code']} | {t['name']} | {t['side']} | "
                         f"{t['qty']:g} | {t['price']:g} | {t['amount']:,.2f} | {t['fee']:,.2f} |")
        closings = [c for c in review["details"]["closings"] if c["gross_pnl"] is not None]
        if closings:
            lines += ["", "### 平仓盈亏（FIFO 配对）", "",
                      "| 卖出日 | 代码 | 名称 | 数量 | 均价(卖/买) | 持有天数 | 毛盈亏 | 收益率 |",
                      "|---|---|---|---:|---|---:|---:|---:|"]
            for c in closings:
                lines.append(
                    f"| {c['sell_date']} | {c['code']} | {c['name']} | {c['sell_qty']:g} | "
                    f"{c['avg_sell_price']:g}/{c['avg_buy_cost']:g} | "
                    f"{c['hold_days'] if c['hold_days'] is not None else 'unavailable'} | "
                    f"{c['gross_pnl']:,.2f} | {c['pnl_pct']}% |")
    else:
        lines.append("- 本周无成交记录。")
    lines += ["", "## 5. 执行纪律审计", ""]
    lines.append(f"- 计划外交易占比："
                 f"{f['unplanned_ratio_pct'] if f['unplanned_ratio_pct'] is not None else 'unavailable'}"
                 f"（判定口径见 plan_checks）")
    lines.append(f"- 止损偏慢（亏损超 {STOP_LOSS_PCT}% 线）：{f['slow_stop_count']} 单")
    lines.append(f"- 无交易日：{', '.join(f['no_trade_days']) or '无'}；"
                 f"缺确认：{', '.join(f['no_trade_unconfirmed']) or '无'}")
    if review["details"]["plan_checks"]:
        lines += ["", "| 日期 | 代码 | 名称 | 方向 | 计划归属 | 计划来源 |", "|---|---|---|---|---|---|"]
        for p in review["details"]["plan_checks"]:
            status = {"planned": "计划内", "unplanned": "**计划外**", "unknown": "无法判定"}[p["status"]]
            lines.append(f"| {p['date']} | {p['code']} | {p['name']} | {p['side']} | {status} | "
                         f"{p['plan_source'] or 'unavailable'} |")
    lines += ["", "## 6. 每日建议事后检验", ""]
    advice_rows = [r for r in review["advice_review"]["rows"] if r["available"]]
    if advice_rows:
        lines += [
            "| 日期 | 市场状态 | 评分 | 仓位建议 | 方向 | 次日上证 | 检验结论 |",
            "|---|---|---|---|---|---:|---|",
        ]
        direction_text = {"bearish": "偏防守", "bullish": "偏进攻", "neutral": "中性"}
        for r in advice_rows:
            lines.append(
                f"| {r['date']} | {r['market_state']} | {r['market_score']} | "
                f"{r['total_position_range']} | {direction_text[r['direction']]} | "
                f"{r['next_day'] or 'unavailable'} {or_na(r['next_day_sse_chg_pct'], '%')} | {r['verdict']} |")
    else:
        lines.append("- `unavailable`：本周无每日总控决策数据。")
    lines += ["", "## 7. 策略有效性", ""]
    lines.append(f"- 卖飞候选（后续 MFE 超卖出价 {SELL_FLY_PCT * 100:.0f}%）：{f['sell_fly_count']} 单；"
                 f"无法评估 {len(f['sell_fly_unevaluated'])} 单")
    lines.append(f"- {SHORT_HOLD_DAYS} 天以内持有单亏损贡献：{or_na(f['short_hold_loss_share_pct'], '%')}")
    lines.append(f"- 0AMV 空头天数：{', '.join(f['bear_days']) or '无'}"
                 f"（占比 {or_na(f['bear_day_ratio_pct'], '%')}）；"
                 f"空头期间亏损占比：{or_na(f['bear_loss_share_pct'], '%')}")
    lines += ["", "## 8. 归因汇总（规则命中清单）", ""]
    all_issues = review["execution_issues"] + review["strategy_issues"] + review.get("environment_issues", [])
    if not all_issues:
        lines.append("- 本周无规则命中。")
    for issue in review["execution_issues"]:
        lines.append(f"- [执行] `{issue['rule']}`：{issue['summary']}")
    for issue in review["strategy_issues"]:
        lines.append(f"- [策略] `{issue['rule']}`：{issue['summary']}")
    for issue in review.get("environment_issues", []):
        lines.append(f"- [策略环境] `{issue['rule']}`：{issue['summary']}")
    lines += ["", "## 9. 数据缺口声明", ""]
    if review["unavailable"]:
        for u in review["unavailable"]:
            lines.append(f"- `unavailable`：{u}")
    else:
        lines.append("- 无。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="确定性周度复盘")
    ap.add_argument("--date", default=date.today().isoformat(), help="周内任意日期，默认今天")
    ap.add_argument("--base", default=str(BASE), help="项目根目录（测试用）")
    args = ap.parse_args()
    base = Path(args.base)

    review = build_weekly_review(base, args.date)
    out_dir = base / "04_reviews" / "weekly"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{review['iso_year']}W{review['iso_week']:02d}_weekly_review"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(review), encoding="utf-8")
    print(f"weekly review written: {json_path}")
    print(f"weekly review written: {md_path}")


if __name__ == "__main__":
    main()
