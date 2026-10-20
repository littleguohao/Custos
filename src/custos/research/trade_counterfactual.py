# -*- coding: utf-8 -*-
"""实盘交易操作反事实复盘（v0.424，owner 2026-10-19）：**同一笔买入，严格按规则操作会怎样**。

只对照**操作**（不涉及 1800 选股）：对主台账每个持仓回合（空仓 → 首次买入 … → 清仓），保持实际的
买入日 / 买入成本不变，用回测引擎同语义（``evaluate_trades`` 信号重放：收盘触发止损、跌停顺延、
分批止盈、BBI 两连破清仓）推演规则会在哪天、以什么收益离场，与实际结果逐笔对比，归因到操作问题。

两个规则口径（同时报）：

- ``rule``（主）：现行出场规则 ``plan_rules_replay.LIVE_PARAMS``（hard_loss −10% 收盘触发 + 分批止盈 0.5 +
  BBI 跌破 2 根清仓；R41 同口径，``loss_reduction −7%`` 减仓引擎表达不了，注明）；
- ``plan``（副）：买入时落盘的计划止损（``position_plans`` 的 stop.price，含 archive）作 stop_override，
  止盈同现行——「照买入那天定的计划执行」；无计划的回合该口径缺。

收益口径：实际 = (卖出净额 + 未卖部分 × 最新收盘 − 买入总额 − 全部费用) / (买入总额 + 买入费用)；
规则 = 引擎净收益（含 ``--cost-bps`` 成本模型）按「买入日收盘 / 实际成本」折回实际成本基准。
Δ = 实际 − 规则（< 0 = 实际操作更差）。差额里混有执行价差（盘中成交价 vs 收盘价、实际费用 vs 成本模型），
同日离场的回合单列「执行价差」。

操作问题归类（主口径，|Δ| ≤ ``--tol`` 记一致）：止损拖延 / 该清未清 / 过早卖出 / 执行价差 / 主动优于规则；
另报回合级标记：亏损补仓（补仓价低于当时持仓成本）、多次分批卖出、非做多期开仓（0AMV 台账）。

纪律：
- **live 跟踪工具，不是研究**：读前向 holdout 期内的 K 线（显式 ``allow_holdout=True``，同 pick_practice /
  plan_shadow_review）。「主动优于规则」类读数**不得**直接拿来改规则——要改须在挖掘段预注册新单元验证；
- 产物含真实持仓与盈亏，只落 ``artifacts/logs/``（gitignore），**不入库、不进 results/**。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import LOGS, POSITION_PLANS_FILE, TRADES_DIR

LEDGER = TRADES_DIR / "master_trade_ledger.csv"
BUY, SELL = "买入", "卖出"
#: 只加股数不加成本的入账类别（同 incremental_ledger.SHARE_CREDIT_CATEGORIES 口径；拆股/送转）
SHARE_CREDIT = ("拆股",)
TOL_PP = 0.5  # |Δ| ≤ 0.5pp 记一致
CATEGORIES = ("止损拖延", "该清未清", "过早卖出", "执行价差", "主动优于规则", "一致")
NEGATIVE = ("止损拖延", "该清未清", "过早卖出", "执行价差")

# ---------------------------------------------------------------------------
# 台账 → 回合
# ---------------------------------------------------------------------------


def parse_ledger(text: str, categories: Optional[dict[str, int]] = None) -> list[dict]:
    """主台账 CSV → 买卖行（+ 拆股入账：只加股数）。``categories`` 提供时计各交易类别行数
    （报告里列出，红利 / 送转等未纳入的类别一眼可见）。"""
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        side = (raw.get("交易类别") or "").strip()
        if categories is not None:
            categories[side] = categories.get(side, 0) + 1
        if side not in (BUY, SELL, *SHARE_CREDIT):
            continue
        try:
            rows.append(
                {
                    "date": (raw.get("成交日期") or "").strip()[:10],
                    "time": (raw.get("成交时间") or "").strip(),
                    "code": (raw.get("代码") or "").strip().zfill(6),
                    "name": (raw.get("名称") or "").strip(),
                    "side": side,
                    "qty": float(raw.get("成交数量") or 0),
                    "price": float(raw.get("成交价格") or 0),
                    "amount": float(raw.get("成交金额") or 0),
                    "fee": float(raw.get("费用") or 0),
                }
            )
        except ValueError:
            continue
    rows.sort(key=lambda r: (r["date"], r["time"], 0 if r["side"] != SELL else 1))
    return rows


#: 场内基金 / 可转债（本地 vipdoc 有日线）：沪 5xx 基金、11x 转债；深 15/16/18 基金、12x 转债
_FUND_BOND_PREFIX = {"SH": ("5", "11"), "SZ": ("15", "16", "18", "12")}


def instrument_of(code: str, name: str) -> str:
    """``stock`` = A 股个股（主口径）；``etf_bond`` = 场内基金 / 转债（v0.431 单列：本地有日线，但 B1 出场规则
    为个股设计——波动、涨跌停、转债 T+0 都不同，读数只作参考）；``other`` = 港股（台账裸码撞 A 股同号，本地无日线，
    TDX 扩展市场只有在线不复权行情）/ 指数等，不推演。"""
    from custos.core.code_utils import is_a_share_trade_row, is_ashare_stock, market_of  # noqa: PLC0415

    if not is_a_share_trade_row({"代码": code, "名称": name}):
        return "other"
    if is_ashare_stock(code):
        return "stock"
    m = market_of(code)
    if m in _FUND_BOND_PREFIX and code.startswith(_FUND_BOND_PREFIX[m]):
        return "etf_bond"
    return "other"


def is_comparable(code: str, name: str) -> bool:
    """只比 A 股**个股**：剔港股（台账裸码撞 A 股同号，见 code_utils.HK_LEDGER_CODES）、ETF、转债、指数——
    出场规则是个股 B1 规则，且港股裸码会取到张冠李戴的 A 股 K 线。"""
    from custos.core.code_utils import is_a_share_trade_row, is_ashare_stock  # noqa: PLC0415

    return is_ashare_stock(code) and is_a_share_trade_row({"代码": code, "名称": name})


def build_episodes(rows: list[dict]) -> list[dict]:
    """逐代码按持仓数量切回合：0 → >0 开始，回到 0 结束；未归零 = open。"""
    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    eps: list[dict] = []
    for code, rs in by_code.items():
        pos = 0.0
        cur: Optional[dict] = None
        for r in rs:
            if (
                r["side"] in SHARE_CREDIT
            ):  # 拆股 / 送转：只加股数，不算买入决策、不开新回合
                if cur is not None:
                    cur.setdefault("credits", []).append(r)
                    pos += r["qty"]
                continue
            if r["side"] == BUY:
                if cur is None:
                    cur = {"code": code, "name": r["name"], "buys": [], "sells": []}
                cost_before = _avg_cost(cur) if cur["buys"] else None
                cur["buys"].append(
                    {**r, "avg_cost_before": cost_before, "pos_before": pos}
                )
                pos += r["qty"]
            else:
                if cur is None:  # 台账起点前的老仓：无买入上下文，跳过
                    continue
                cur["sells"].append(r)
                pos -= r["qty"]
                if pos <= 1e-6:
                    cur["closed"] = True
                    eps.append(cur)
                    cur, pos = None, 0.0
        if cur is not None:
            cur["closed"] = False
            cur["open_qty"] = pos
            eps.append(cur)
    for e in eps:
        e["entry_date"] = e["buys"][0]["date"]
        e["exit_date"] = e["sells"][-1]["date"] if e["closed"] else None
    return sorted(eps, key=lambda e: (e["entry_date"], e["code"]))


def _avg_cost(ep: dict) -> float:
    q = sum(b["qty"] for b in ep["buys"])
    return (sum(b["amount"] + b["fee"] for b in ep["buys"]) / q) if q else 0.0


def first_lot_result(ep: dict, last_close: Optional[float]) -> dict[str, Any]:
    """首笔买入那一手的实际结果（FIFO：卖出先消耗首笔）——与规则推演**同股数、同进场**，
    只差出场（v0.430：此前拿整个回合（含后续补仓）对首笔进场的规则路径，加仓效果被记进出场差）。"""
    b0 = ep["buys"][0]
    q0 = b0["qty"]
    cost0 = b0["amount"] + b0["fee"]
    left, proceeds, exit_date = q0, 0.0, None
    for sv in ep["sells"]:
        if left <= 1e-9:
            break
        take = min(left, sv["qty"])
        frac = take / sv["qty"] if sv["qty"] else 0.0
        proceeds += take * sv["price"] - sv["fee"] * frac
        left -= take
        if left <= 1e-9:
            exit_date = sv["date"]
    if left > 1e-9:
        if last_close is None:
            return {"ret": None, "cost": cost0, "exit_date": None}
        proceeds += left * last_close
    return {
        "ret": (proceeds - cost0) / cost0 if cost0 else None,
        "cost": cost0,
        "pnl": proceeds - cost0,
        "exit_date": exit_date,
    }


def actual_result(ep: dict, last_close: Optional[float]) -> dict[str, Any]:
    buy_amt = sum(b["amount"] for b in ep["buys"])
    buy_fee = sum(b["fee"] for b in ep["buys"])
    sell_net = sum(s["amount"] - s["fee"] for s in ep["sells"])
    mark = 0.0
    if not ep["closed"]:
        if last_close is None:
            return {"ret": None, "basis": buy_amt + buy_fee}
        mark = ep["open_qty"] * last_close
    basis = buy_amt + buy_fee
    return {
        "ret": (sell_net + mark - basis) / basis if basis else None,
        "basis": basis,
        "pnl": sell_net + mark - basis,
        "marked_open": not ep["closed"],
    }


# ---------------------------------------------------------------------------
# 规则推演
# ---------------------------------------------------------------------------


def _bars_since(entry_date: str) -> int:
    d = (date.today() - date.fromisoformat(entry_date)).days
    return int(d * 0.7) + 400  # 交易日 ≈ 0.7 × 自然日 + 预热（BBI / 中大阳线）


def default_loader(code: str, entry_date: str) -> Any:
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    return bt._load_one_bars(
        code, _bars_since(entry_date), None, None, allow_holdout=True
    )


def replay_rule(
    df: Any,
    entry_date: str,
    params: dict[str, Any],
    cost_bps: float,
    stop_override: Optional[float] = None,
) -> dict[str, Any]:
    """买入日收盘进场按规则推演（单信号重放）。返回 {exit_date, reason, ret_close, close_entry} 或 {unable}。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    dates = [str(d)[:10] for d in df["date"]]
    if entry_date not in dates:
        return {"unable": "买入日无 K 线（停牌/数据缺）"}
    i = dates.index(entry_date)
    if i >= len(dates) - 1:
        return {"unable": "买入日后尚无 K 线"}
    sig: dict[str, Any] = {"i": i, "date": entry_date, "score": 0.0}
    if stop_override is not None:
        sig["stop_override"] = float(stop_override)
    trs = bt.evaluate_trades(
        {"X": df},
        signals_in={"X": [sig]},
        cost_bps=cost_bps,
        collect_all=True,
        min_bars=1,
        **params,
    )
    if not trs:
        return {"unable": "引擎未产出交易（数据不足）"}
    tr = trs[0]
    return {
        "exit_date": tr["exit_date"],
        "reason": tr["reason"],
        "ret_close": float(tr["ret"]),
        "close_entry": float(df["close"].iloc[i]),
        "holding": tr.get("holding"),
    }


def _on_cost(
    rr: dict[str, Any], first_price: float, avg_cost: float
) -> Optional[float]:
    """规则收益折回实际成本基准。

    ⚠️ v0.426 修：K 线是**前复权**，``close_entry`` 比当时真实价低（分红 / 送转越多越低），旧式
    ``close_entry / avg_cost`` 把复权缺口当成规则亏损（越老的回合偏差越大）。现以**首笔实际买价**代表
    规则的进场价（复权空间里的路径收益 ``ret_close`` 本身已含分红送转，不受影响），再按实际
    摊薄成本折算——补仓带来的成本差仍记在实际侧。"""
    if "unable" in rr or not avg_cost or not first_price:
        return None
    return (1.0 + rr["ret_close"]) * first_price / avg_cost - 1.0


def classify(
    actual_ret: Optional[float],
    rule_ret: Optional[float],
    ep: dict,
    rr: dict,
    tol_pp: float,
    a_exit: Any = "__episode__",
) -> Optional[str]:
    if actual_ret is None or rule_ret is None:
        return None
    d = (actual_ret - rule_ret) * 100
    if abs(d) <= tol_pp:
        return "一致"
    if d > 0:
        return "主动优于规则"
    rule_open = str(rr.get("reason", "")).startswith("open_end")
    if a_exit == "__episode__":
        a_exit = ep["exit_date"]  # None = 实际仍持有
    r_exit = None if rule_open else rr["exit_date"]
    if a_exit == r_exit:
        return "执行价差"
    later = r_exit is not None and (a_exit is None or a_exit > r_exit)
    if later:
        return "止损拖延" if "stop" in str(rr.get("reason")) else "该清未清"
    return "过早卖出"


def b1_entry(
    df: Any, entry_date: str, regime: Optional[dict[str, str]]
) -> Optional[bool]:
    """买入日是否 B1 合规入场：0AMV 做多 且 买入日 J<13（``bt.j_low_gate`` 同口径）。
    regime 不可用 ⇒ 只看 J；数据不足 ⇒ None。"""
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    dates = [str(d)[:10] for d in df["date"]]
    if entry_date not in dates:
        return None
    i = dates.index(entry_date)
    if i < 30:
        return None
    j_ok = bool(bt.j_low_gate(df.iloc[: i + 1]))
    if regime:
        st = regime.get(entry_date)
        return j_ok and st == "做多"
    return j_ok


def flags_of(ep: dict, regime: Optional[dict[str, str]]) -> list[str]:
    out = []
    if any(
        b["avg_cost_before"] and b["price"] < b["avg_cost_before"]
        for b in ep["buys"][1:]
    ):
        out.append("亏损补仓")
    elif len(ep["buys"]) > 1:
        out.append("分次建仓")
    if len(ep["sells"]) > 1:
        out.append("分批卖出")
    if regime is not None:
        st = regime.get(ep["entry_date"])
        if st is not None and st != "做多":
            out.append(f"非做多期开仓({st})")
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _plan_stop(plans: dict[str, Any], code: str, entry_date: str) -> Optional[float]:
    cands = [plans.get("positions", {}).get(code)] + list(
        (plans.get("archive", {}) or {}).get(code, []) or []
    )
    for p in cands:
        if p and str(p.get("entry_date", ""))[:10] == entry_date:
            price = (p.get("stop") or {}).get("price")
            return float(price) if isinstance(price, (int, float)) else None
    return None


def analyze(
    rows: list[dict],
    *,
    plans: dict[str, Any],
    loader: Callable[[str, str], Any],
    regime: Optional[dict[str, str]],
    cost_bps: float,
    tol_pp: float,
    start: str = "",
    end: str = "",
    period: tuple[str, str] | None = None,
) -> dict[str, Any]:
    from custos.research import plan_rules_replay as prr  # noqa: PLC0415

    all_eps = build_episodes(rows)
    excluded = sorted(
        {
            f"{e['code']} {e['name']}"
            for e in all_eps
            if instrument_of(e["code"], e["name"]) == "other"
        }
    )
    eps = [
        e
        for e in all_eps
        if instrument_of(e["code"], e["name"]) != "other"
        and (not start or e["entry_date"] >= start)
        and (not end or e["entry_date"] <= end)
    ]
    if period is not None:  # 周报/月报口径：期间内清仓的回合 + 期末仍持有的回合
        ps, pe = period
        eps = [
            e
            for e in eps
            if (e["closed"] and ps <= e["exit_date"] <= pe)
            or (not e["closed"] and e["entry_date"] <= pe)
        ]
    out_eps: list[dict[str, Any]] = []
    for e in eps:
        df = None
        try:
            df = loader(e["code"], e["entry_date"])
        except Exception as exc:  # noqa: BLE001
            df = None
            err = f"{type(exc).__name__}: {exc}"
        else:
            err = "K 线加载为空"
        avg = _avg_cost(e)
        row: dict[str, Any] = {
            "code": e["code"],
            "instrument": instrument_of(e["code"], e["name"]),
            "name": e["name"],
            "entry_date": e["entry_date"],
            "exit_date": e["exit_date"],
            "closed": e["closed"],
            "n_buys": len(e["buys"]),
            "n_sells": len(e["sells"]),
            "avg_cost": round(avg, 4),
            "flags": flags_of(e, regime),
        }
        if df is None or not len(df):
            row["unable"] = err
            out_eps.append(row)
            continue
        last_close = float(df["close"].iloc[-1])
        act = actual_result(e, last_close)
        fl = first_lot_result(e, last_close)
        row["b1_entry"] = b1_entry(df, e["entry_date"], regime)
        row["first_lot_ret"] = fl["ret"]
        row["first_lot_cost"] = fl["cost"]
        row["first_lot_exit"] = fl["exit_date"]
        # 加仓 / 补仓部分的盈亏 = 整个回合盈亏 − 首笔那手的盈亏（出场对照之外单列）
        row["add_on_pnl"] = (
            act["pnl"] - fl["pnl"]
            if act.get("pnl") is not None and fl.get("pnl") is not None
            else None
        )
        row["actual_ret"] = act["ret"]
        row["basis"] = act["basis"]
        row["marked_open"] = act.get("marked_open")
        for name, params, stop in (
            ("rule", prr.LIVE_PARAMS, None),
            ("plan", prr.PLAN_PARAMS, _plan_stop(plans, e["code"], e["entry_date"])),
        ):
            if name == "plan" and stop is None:
                row[name] = {"unable": "无买入计划止损"}
                continue
            rr = replay_rule(df, e["entry_date"], params, cost_bps, stop_override=stop)
            b0 = e["buys"][0]
            c0 = (b0["amount"] + b0["fee"]) / b0["qty"] if b0["qty"] else 0.0
            rret = _on_cost(
                rr, b0["price"], c0
            )  # 规则 = 首笔那手按规则出场（同股数同进场）
            fr = fl["ret"]
            cat = classify(fr, rret, e, rr, tol_pp, a_exit=fl["exit_date"])
            row[name] = {
                **{
                    k: rr.get(k)
                    for k in ("exit_date", "reason", "holding", "unable")
                    if k in rr
                },
                **({"stop_price": stop} if stop is not None else {}),
                "ret": rret,
                "delta_pp": (fr - rret) * 100
                if fr is not None and rret is not None
                else None,
                "delta_amount": (fr - rret) * fl["cost"]
                if fr is not None and rret is not None
                else None,
                "category": cat,
            }
        out_eps.append(row)
    return {
        "schema": "trade_counterfactual/v2",  # v2 = 首笔仓位口径 + 加仓盈亏单列 + B1 入场分组
        "tool": "trade_counterfactual",
        "params": {
            "rule": prr.LIVE_PARAMS,
            "cost_bps": cost_bps,
            "tol_pp": tol_pp,
            "start": start or None,
            "end": end or None,
            "period": list(period) if period else None,
        },
        "note": "live 跟踪（读 holdout 期 K 线，allow_holdout）；「主动优于规则」不得直接用于改规则",
        "excluded_instruments": excluded,
        "episodes": out_eps,
        # 主口径只含 A 股个股；场内基金 / 转债单列（B1 规则为个股设计，只作参考）
        "summary": {
            b: summarize(
                [e for e in out_eps if e.get("instrument", "stock") == "stock"], b
            )
            for b in ("rule", "plan")
        },
        "summary_etf_bond": summarize(
            [e for e in out_eps if e.get("instrument") == "etf_bond"], "rule"
        ),
    }


def _stats(rets: list[float]) -> dict[str, Any]:
    if not rets:
        return {"n": 0}
    win = [r for r in rets if r > 0]
    loss = [r for r in rets if r <= 0]
    aw = sum(win) / len(win) if win else None
    al = sum(loss) / len(loss) if loss else None
    return {
        "n": len(rets),
        "win_rate": len(win) / len(rets),
        "avg_win": aw,
        "avg_loss": al,
        "payoff_ratio": aw / abs(al) if aw is not None and al else None,
        "avg_ret": sum(rets) / len(rets),
    }


def summarize(eps: list[dict], basis: str) -> dict[str, Any]:
    ok = [
        e
        for e in eps
        if isinstance(e.get(basis), dict)
        and e[basis].get("ret") is not None
        and e.get("actual_ret") is not None
    ]
    closed = [e for e in ok if e["closed"]]
    cats: dict[str, dict[str, Any]] = {}
    for e in ok:
        c = e[basis]["category"]
        g = cats.setdefault(
            c, {"n": 0, "delta_amount": 0.0, "delta_pp_sum": 0.0, "episodes": []}
        )
        g["n"] += 1
        g["delta_amount"] += e[basis]["delta_amount"]
        g["delta_pp_sum"] += e[basis]["delta_pp"]
        g["episodes"].append(f"{e['code']}@{e['entry_date']}")
    neg_total = sum(g["delta_amount"] for c, g in cats.items() if c in NEGATIVE)
    for c, g in cats.items():
        g["share_of_negative"] = (
            (g["delta_amount"] / neg_total) if c in NEGATIVE and neg_total else None
        )
    flag_cnt: dict[str, int] = {}
    for e in ok:
        for f in e["flags"]:
            k = f.split("(")[0]
            flag_cnt[k] = flag_cnt.get(k, 0) + 1
    return {
        "n_episodes": len(ok),
        "n_closed": len(closed),
        "actual_closed": _stats([e["actual_ret"] for e in closed]),
        "first_lot_closed": _stats(
            [e["first_lot_ret"] for e in closed if e.get("first_lot_ret") is not None]
        ),
        "rule_closed": _stats([e[basis]["ret"] for e in closed]),
        "total_delta_amount": sum(e[basis]["delta_amount"] for e in ok),
        "total_add_on_pnl": sum(e.get("add_on_pnl") or 0.0 for e in ok),
        "n_with_add_on": sum(1 for e in ok if e["n_buys"] > 1),
        "total_basis": sum(e["basis"] for e in ok),
        "total_first_lot_cost": sum(e.get("first_lot_cost") or 0.0 for e in ok),
        "by_entry": {
            lab: {
                "n": len(g),
                "first_lot": _stats(
                    [
                        e["first_lot_ret"]
                        for e in g
                        if e.get("first_lot_ret") is not None
                    ]
                ),
                "rule": _stats([e[basis]["ret"] for e in g]),
                "delta_amount": sum(e[basis]["delta_amount"] for e in g),
                "n_active_better": sum(
                    1 for e in g if e[basis]["category"] == "主动优于规则"
                ),
            }
            for lab, g in (
                ("B1 合规入场", [e for e in closed if e.get("b1_entry") is True]),
                ("非 B1 入场", [e for e in closed if e.get("b1_entry") is False]),
                ("无法判定", [e for e in closed if e.get("b1_entry") is None]),
            )
            if g
        },
        "best": sorted(
            (
                {
                    "episode": f"{e['code']} {e['name']}@{e['entry_date']}",
                    "delta_pp": e[basis]["delta_pp"],
                    "delta_amount": e[basis]["delta_amount"],
                    "rule_exit": e[basis].get("exit_date"),
                    "rule_reason": e[basis].get("reason"),
                    "actual_exit": e.get("first_lot_exit"),
                    "b1_entry": e.get("b1_entry"),
                }
                for e in ok
            ),
            key=lambda x: -x["delta_amount"],
        )[:10],
        "categories": {c: cats[c] for c in CATEGORIES if c in cats},
        "flags": flag_cnt,
        "worst": sorted(
            (
                {
                    "episode": f"{e['code']} {e['name']}@{e['entry_date']}",
                    "category": e[basis]["category"],
                    "delta_pp": e[basis]["delta_pp"],
                    "delta_amount": e[basis]["delta_amount"],
                    "rule_exit": e[basis].get("exit_date"),
                    "rule_reason": e[basis].get("reason"),
                    "actual_exit": e.get("first_lot_exit"),
                }
                for e in ok
            ),
            key=lambda x: x["delta_amount"],
        )[:10],
    }


def render_md(rep: dict[str, Any]) -> str:
    def pct(x: Any) -> str:
        return "—" if x is None else f"{x * 100:+.2f}%"

    s = rep["summary"]["rule"]
    lines = [
        "# 实盘操作反事实复盘（同一笔买入，严格按现行规则操作）",
        "",
        f"> 回合 {s['n_episodes']}（已清仓 {s['n_closed']}）；规则 = 现行出场（−10% 收盘止损 + 分批止盈 + BBI 两连破），"
        f"成本 {rep['params']['cost_bps']:g}bps。**出场对照只比首笔买入那一手**（同股数、同进场，FIFO），"
        "补仓 / 加仓的盈亏单列；Δ = 首笔实际 − 首笔按规则，负 = 出场操作更差。live 跟踪读数，不作改规则依据。",
        "",
        "## 总览（已清仓回合）",
        "",
        "| 口径 | 笔数 | 胜率 | 盈亏比 | 平均收益 |",
        "|---|---:|---:|---:|---:|",
    ]
    for lab, st in (
        ("实际（整个回合，含加仓）", s["actual_closed"]),
        ("实际（首笔那手）", s.get("first_lot_closed") or {}),
        ("首笔严格按规则", s["rule_closed"]),
    ):
        pr = st.get("payoff_ratio")
        lines.append(
            f"| {lab} | {st.get('n', 0)} | {pct(st.get('win_rate'))[1:] if st.get('win_rate') is not None else '—'} | "
            f"{'—' if pr is None else f'{pr:.2f}'} | {pct(st.get('avg_ret'))} |"
        )
    lines += [
        "",
        f"出场差 Δ 合计：**{s['total_delta_amount']:+,.0f} 元**（首笔成本基数 {s.get('total_first_lot_cost', 0):,.0f} 元）；"
        f"加仓 / 补仓部分盈亏（{s.get('n_with_add_on', 0)} 个回合有加仓）：**{s.get('total_add_on_pnl', 0):+,.0f} 元**"
        f"（回合总成本 {s['total_basis']:,.0f} 元）",
        "",
        "## 按入场类型（已清仓）——规则是 B1 出场规则，对非 B1 入场未必适用",
        "",
        "| 入场类型 | 笔数 | 首笔实际 胜率 / 盈亏比 / 均收益 | 首笔按规则 胜率 / 盈亏比 / 均收益 | Δ 金额 | 主动优于规则 笔数 |",
        "|---|---:|---|---|---:|---:|",
    ]

    def _s(st: dict) -> str:
        pr = st.get("payoff_ratio")
        wr = st.get("win_rate")
        return (
            f"{'—' if wr is None else f'{wr * 100:.1f}%'} / {'—' if pr is None else f'{pr:.2f}'} / "
            f"{pct(st.get('avg_ret'))}"
        )

    for lab, g in (s.get("by_entry") or {}).items():
        lines.append(
            f"| {lab} | {g['n']} | {_s(g['first_lot'])} | {_s(g['rule'])} | {g['delta_amount']:+,.0f} | "
            f"{g['n_active_better']} |"
        )
    lines += [
        "",
        "## 操作问题（按 Δ 金额）",
        "",
        "| 类别 | 笔数 | Δ 金额（元） | 占负向差额 | 回合 |",
        "|---|---:|---:|---:|---|",
    ]
    for c, g in sorted(s["categories"].items(), key=lambda kv: kv[1]["delta_amount"]):
        sh = g["share_of_negative"]
        lines.append(
            f"| {c} | {g['n']} | {g['delta_amount']:+,.0f} | {'—' if sh is None else f'{sh:.0%}'} | "
            f"{'、'.join(g['episodes'][:8])}{' …' if len(g['episodes']) > 8 else ''} |"
        )
    if s["flags"]:
        lines += [
            "",
            "回合标记：" + "；".join(f"{k} {v} 笔" for k, v in s["flags"].items()),
        ]
    lines += [
        "",
        "## Δ 最差 10 笔（对照当日复盘）",
        "",
        "| 回合 | 类别 | Δ | Δ 金额 | 规则离场 | 实际离场 |",
        "|---|---|---:|---:|---|---|",
    ]
    for w in s["worst"]:
        lines.append(
            f"| {w['episode']} | {w['category']} | {w['delta_pp']:+.2f}pp | {w['delta_amount']:+,.0f} | "
            f"{w['rule_exit'] or '持有中'}（{w['rule_reason']}） | {w['actual_exit'] or '持有中'} |"
        )
    lines += [
        "",
        "## Δ 最好 10 笔（「主动优于规则」从哪来：分散还是集中）",
        "",
        "| 回合 | Δ | Δ 金额 | 规则离场 | 实际离场 | B1 入场 |",
        "|---|---:|---:|---|---|---|",
    ]
    for w in s.get("best") or []:
        b1 = {True: "是", False: "否"}.get(w.get("b1_entry"), "—")
        lines.append(
            f"| {w['episode']} | {w['delta_pp']:+.2f}pp | {w['delta_amount']:+,.0f} | "
            f"{w['rule_exit'] or '持有中'}（{w['rule_reason']}） | {w['actual_exit'] or '持有中'} | {b1} |"
        )
    pos = [g for c, g in s["categories"].items() if c == "主动优于规则"]
    if pos and pos[0]["delta_amount"] > 0:
        top10 = sum(max(0.0, w["delta_amount"]) for w in (s.get("best") or []))
        lines += [
            "",
            f"前 10 笔贡献「主动优于规则」合计的 {top10 / pos[0]['delta_amount']:.0%}。",
        ]
    p = rep["summary"]["plan"]
    lines += [
        "",
        f"副口径（买入计划止损）：可推演 {p['n_episodes']} 个回合，合计 Δ {p['total_delta_amount']:+,.0f} 元；"
        "明细见 JSON `summary.plan`。",
    ]
    eb = rep.get("summary_etf_bond") or {}
    if eb.get("n_episodes"):
        lines += [
            "",
            "## 场内基金 / 可转债（单列，规则只作参考：B1 出场规则为个股设计）",
            "",
            "| 口径 | 笔数 | 胜率 | 盈亏比 | 平均收益 |",
            "|---|---:|---:|---:|---:|",
        ]
        for lab, st in (
            ("实际（首笔那手）", eb.get("first_lot_closed") or {}),
            ("首笔按规则", eb.get("rule_closed") or {}),
        ):
            wr, pr = st.get("win_rate"), st.get("payoff_ratio")
            lines.append(
                f"| {lab} | {st.get('n', 0)} | {'—' if wr is None else f'{wr * 100:.1f}%'} | "
                f"{'—' if pr is None else f'{pr:.2f}'} | {pct(st.get('avg_ret'))} |"
            )
        lines += [
            "",
            f"出场差 Δ 合计 {eb['total_delta_amount']:+,.0f} 元；分类："
            + "；".join(
                f"{c} {g['n']} 笔 {g['delta_amount']:+,.0f}"
                for c, g in eb["categories"].items()
            ),
        ]
    if rep.get("excluded_instruments"):
        ex = rep["excluded_instruments"]
        lines += [
            "",
            f"未纳入（港股：台账裸码撞 A 股同号、本地无日线；或指数等）{len(ex)} 只："
            + "、".join(ex[:20])
            + (" …" if len(ex) > 20 else ""),
        ]
    if rep.get("ledger_categories"):
        lines += [
            "",
            "台账交易类别（只用 买入 / 卖出 / 拆股；红利等现金入账未计入实际收益）："
            + "；".join(
                f"{k or '空'} {v}" for k, v in sorted(rep["ledger_categories"].items())
            ),
        ]
    unable = [e for e in rep["episodes"] if e.get("unable")]
    if unable:
        lines += [
            "",
            f"⚠️ {len(unable)} 个回合无法推演："
            + "；".join(
                f"{e['code']}@{e['entry_date']}（{e['unable']}）" for e in unable[:10]
            ),
        ]
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="实盘操作反事实复盘：同一笔买入严格按规则操作 vs 实际"
    )
    ap.add_argument("--ledger", default=str(LEDGER), help="主成交台账 CSV")
    ap.add_argument(
        "--plans", default=str(POSITION_PLANS_FILE), help="持仓计划 JSON（副口径）"
    )
    ap.add_argument(
        "--start", default="", help="只看首次买入 ≥ 该日的回合（YYYY-MM-DD）"
    )
    ap.add_argument("--end", default="", help="只看首次买入 ≤ 该日的回合")
    ap.add_argument(
        "--period",
        nargs=2,
        metavar=("START", "END"),
        default=None,
        help="周报/月报口径：只看期间内清仓的回合 + 期末仍持有的回合",
    )
    ap.add_argument(
        "--cost-bps", type=float, default=25.0, help="规则侧成本模型（bps）"
    )
    ap.add_argument("--tol", type=float, default=TOL_PP, help="|Δ| ≤ tol（pp）记一致")
    ap.add_argument(
        "--no-regime", action="store_true", help="不读 0AMV 台账（不标非做多期开仓）"
    )
    ap.add_argument(
        "--out",
        default=str(LOGS / "trade_counterfactual" / "trade_counterfactual.json"),
    )
    return ap


def main(
    argv: Optional[list[str]] = None,
    *,
    loader: Optional[Callable[[str, str], Any]] = None,
    regime: Optional[dict[str, str]] = None,
) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    lp = Path(args.ledger)
    if not lp.exists():
        print(f"[FAIL] 台账不存在：{lp}", file=sys.stderr)
        return 2
    cats: dict[str, int] = {}
    rows = parse_ledger(lp.read_text(encoding="utf-8-sig"), cats)
    pp = Path(args.plans)
    plans = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else {}
    if regime is None and loader is None and not args.no_regime:
        from custos.research import backtest_factors as bt  # noqa: PLC0415

        try:
            regime = bt.load_amv_regime(since="2000-01-01") or None
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 0AMV 台账读不到（{exc}）——不标非做多期开仓", file=sys.stderr)
    rep = analyze(
        rows,
        plans=plans,
        loader=loader or default_loader,
        regime=regime,
        cost_bps=args.cost_bps,
        tol_pp=args.tol,
        start=args.start,
        end=args.end,
        period=tuple(args.period) if args.period else None,
    )
    rep["ledger_categories"] = cats
    if not rep["summary"]["rule"]["n_episodes"]:
        print(
            "[FAIL] 0 个可推演回合（台账为空 / 窗口不对 / K 线全缺）——空结果不写产物",
            file=sys.stderr,
        )
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    md = out.with_suffix(".md")
    md.write_text(render_md(rep), encoding="utf-8")
    print(render_md(rep))
    print(f"[OK] 写出 {out} / {md.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
