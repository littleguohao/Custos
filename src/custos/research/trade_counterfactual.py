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
TOL_PP = 0.5  # |Δ| ≤ 0.5pp 记一致
CATEGORIES = ("止损拖延", "该清未清", "过早卖出", "执行价差", "主动优于规则", "一致")
NEGATIVE = ("止损拖延", "该清未清", "过早卖出", "执行价差")

# ---------------------------------------------------------------------------
# 台账 → 回合
# ---------------------------------------------------------------------------


def parse_ledger(text: str) -> list[dict]:
    """主台账 CSV → 买卖行（同 weekly_review.parse_ledger 口径：只留买入/卖出）。"""
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        side = (raw.get("交易类别") or "").strip()
        if side not in (BUY, SELL):
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
    rows.sort(key=lambda r: (r["date"], r["time"], 0 if r["side"] == BUY else 1))
    return rows


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


def _on_cost(rr: dict[str, Any], avg_cost: float) -> Optional[float]:
    if "unable" in rr or not avg_cost:
        return None
    return (1.0 + rr["ret_close"]) * rr["close_entry"] / avg_cost - 1.0


def classify(
    actual_ret: Optional[float],
    rule_ret: Optional[float],
    ep: dict,
    rr: dict,
    tol_pp: float,
) -> Optional[str]:
    if actual_ret is None or rule_ret is None:
        return None
    d = (actual_ret - rule_ret) * 100
    if abs(d) <= tol_pp:
        return "一致"
    if d > 0:
        return "主动优于规则"
    rule_open = str(rr.get("reason", "")).startswith("open_end")
    a_exit = ep["exit_date"]  # None = 实际仍持有
    r_exit = None if rule_open else rr["exit_date"]
    if a_exit == r_exit:
        return "执行价差"
    later = r_exit is not None and (a_exit is None or a_exit > r_exit)
    if later:
        return "止损拖延" if "stop" in str(rr.get("reason")) else "该清未清"
    return "过早卖出"


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

    eps = [
        e
        for e in build_episodes(rows)
        if (not start or e["entry_date"] >= start)
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
            rret = _on_cost(rr, avg)
            cat = classify(act["ret"], rret, e, rr, tol_pp)
            row[name] = {
                **{
                    k: rr.get(k)
                    for k in ("exit_date", "reason", "holding", "unable")
                    if k in rr
                },
                **({"stop_price": stop} if stop is not None else {}),
                "ret": rret,
                "delta_pp": (act["ret"] - rret) * 100
                if act["ret"] is not None and rret is not None
                else None,
                "delta_amount": (act["ret"] - rret) * act["basis"]
                if act["ret"] is not None and rret is not None
                else None,
                "category": cat,
            }
        out_eps.append(row)
    return {
        "schema": "trade_counterfactual/v1",
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
        "episodes": out_eps,
        "summary": {b: summarize(out_eps, b) for b in ("rule", "plan")},
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
        "rule_closed": _stats([e[basis]["ret"] for e in closed]),
        "total_delta_amount": sum(e[basis]["delta_amount"] for e in ok),
        "total_basis": sum(e["basis"] for e in ok),
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
                    "actual_exit": e["exit_date"],
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
        f"成本 {rep['params']['cost_bps']:g}bps；Δ = 实际 − 规则，负 = 操作更差。live 跟踪读数，不作改规则依据。",
        "",
        "## 总览（已清仓回合）",
        "",
        "| 口径 | 笔数 | 胜率 | 盈亏比 | 平均收益 |",
        "|---|---:|---:|---:|---:|",
    ]
    for lab, st in (("实际", s["actual_closed"]), ("严格按规则", s["rule_closed"])):
        pr = st.get("payoff_ratio")
        lines.append(
            f"| {lab} | {st.get('n', 0)} | {pct(st.get('win_rate'))[1:] if st.get('win_rate') is not None else '—'} | "
            f"{'—' if pr is None else f'{pr:.2f}'} | {pct(st.get('avg_ret'))} |"
        )
    lines += [
        "",
        f"全部回合合计 Δ 金额：**{s['total_delta_amount']:+,.0f} 元**（成本基数 {s['total_basis']:,.0f} 元）",
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
    p = rep["summary"]["plan"]
    lines += [
        "",
        f"副口径（买入计划止损）：可推演 {p['n_episodes']} 个回合，合计 Δ {p['total_delta_amount']:+,.0f} 元；"
        "明细见 JSON `summary.plan`。",
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
    rows = parse_ledger(lp.read_text(encoding="utf-8-sig"))
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
