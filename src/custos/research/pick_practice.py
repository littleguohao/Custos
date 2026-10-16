# -*- coding: utf-8 -*-
"""选股练习台账（TODO #101，owner 2026-10-15）：长期跟踪 owner 每日 1800 后亲选标的的成功率与盈亏比。

用法::

    uv run python -m custos.research pick_practice add 600000 000001      # 日期缺省=今天
    uv run python -m custos.research pick_practice add 600000 --date 2026-10-15 --note "主线回踩"
    uv run python -m custos.research pick_practice report [--month 2026-10] [--out x.json]

口径（写死）：

- **入场 = 选股日 T 之后第一个交易日（T+1）开盘价**——1800 选出来最早只能次日开盘买，
  不能用 T 日收盘；T+1 尚无 K 线 ⇒ ``pending``。
- **出场 = B1 现行规则机械执行**（与 plan_rules_replay 现行版同参）：止损 = 入场价 ×(1−10%)、
  收盘触发；两阳分批止盈 50%；BBI 两连破清仓；跳空低开按开盘成交。引擎单源
  ``backtest_factors.simulate_b1_trade``（它按入场 bar 收盘进场：本工具以 T+1 为入场 bar、
  止损用 ``stop_override`` 锚定开盘价、收益按 close/open 换算到开盘入场口径——收益对出场价
  线性，分批加权同样成立）。T+1 当日不可卖（A 股 T+1），引擎从入场次日起判出场，一致。
- 未出场（到数据末仍持有）⇒ ``open``，按最新收盘盯市单列，**不进胜率/盈亏比**。
- 成本 25bps 往返从已平仓收益直扣。不建对照池（owner 定：只看自己的绝对成功率）。
- **live 跟踪工具，不是研究**：数据在前向 holdout 期内，显式 ``allow_holdout=True``
  （同 plan_shadow_review，v0.361 豁免先例）；读数不作任何研究单元判据。

台账 ``data/trades/pick_practice.jsonl``：append-only，唯一写入口 :func:`append_picks`，
每日首写前快照 ``.bak_YYYYMMDD``；同 (date, code) 幂等不重复。
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from custos.core.paths import PICK_PRACTICE_LEDGER, cn_now

#: B1 现行出场参数（与 plan_rules_replay.LIVE_PARAMS 同口径；止损在本工具里锚定开盘价）
LIVE_STOP_PCT = 10.0
LIVE_EXIT_KW: dict[str, Any] = {
    "scale_out_frac": 0.5,
    "bbi_exit_consec": 2,
    "stop_trigger": "close",
}
COST_BPS = 25.0
BAR_COUNT = 400  # 回看根数（BBI 需 24 根预热；单票持有期远小于此）
_CODE_RE = re.compile(r"^\d{6}$")


# ---------------------------------------------------------------------------
# 台账（append-only，唯一写入口）
# ---------------------------------------------------------------------------


def load_picks(path: Optional[Path] = None) -> list[dict[str, Any]]:
    p = Path(path) if path else PICK_PRACTICE_LEDGER
    if not p.is_file():
        return []
    out = []
    for ln_no, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            print(f"[WARN] pick_practice 第 {ln_no} 行坏行跳过", file=sys.stderr)
            continue
        if isinstance(rec, dict) and rec.get("code") and rec.get("date"):
            out.append(rec)
    return out


def append_picks(
    day: str, codes: list[str], note: str = "", *, path: Optional[Path] = None
) -> list[str]:
    """登记选股（唯一写入口）：校验 6 位代码；同 (date, code) 已存在则跳过。
    返回本次实际新增的代码。每日首写前快照（0AMV 台账事故纪律）。"""
    bad = [c for c in codes if not _CODE_RE.match(c)]
    if bad:
        raise ValueError(f"代码须为 6 位数字: {bad}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise ValueError(f"日期须为 YYYY-MM-DD: {day!r}")
    p = Path(path) if path else PICK_PRACTICE_LEDGER
    have = {(r["date"], r["code"]) for r in load_picks(p)}
    new = [c for c in dict.fromkeys(codes) if (day, c) not in have]
    if not new:
        return []
    if p.is_file():
        bak = p.with_name(f"{p.name}.bak_{cn_now().strftime('%Y%m%d')}")
        if not bak.exists():
            bak.write_bytes(p.read_bytes())
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = cn_now().isoformat(timespec="seconds")
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        for c in new:
            rec = {"date": day, "code": c, "note": note, "recorded_at": stamp}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return new


# ---------------------------------------------------------------------------
# 模拟（引擎单源）
# ---------------------------------------------------------------------------


def simulate_pick(df: Any, day: str) -> dict[str, Any]:
    """单只选股的模拟结果：status ∈ {pending, open, closed}。"""
    from custos.core.indicators import bbi_series  # noqa: PLC0415
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    if df is None or len(df) == 0:
        return {"status": "no_data"}
    dates = df["date"].astype(str).str[:10].tolist()
    e = next((k for k, d in enumerate(dates) if d > day), None)
    if e is None:
        return {"status": "pending"}  # T+1 尚无 K 线
    df = df.reset_index(drop=True)
    open_e = float(df["open"].iloc[e])
    close_e = float(df["close"].iloc[e])
    stop = open_e * (1.0 - LIVE_STOP_PCT / 100.0)
    base = {"entry_date": dates[e], "entry_price": open_e, "stop": stop}
    if not open_e > 0:
        return {**base, "status": "no_data"}
    if close_e <= stop:
        # 入场当日收盘已破止损（日内暴跌 >10%）：T+1 不可卖 ⇒ 次日开盘止损
        if e + 1 >= len(df):
            return {**base, "status": "open", "mark_ret": close_e / open_e - 1.0}
        px = float(df["open"].iloc[e + 1])
        ret = px / open_e - 1.0
        return {
            **base,
            "status": "closed",
            "exit_date": dates[e + 1],
            "reason": "stop",
            "ret": ret - COST_BPS / 1e4,
            "holding": 1,
        }
    tr = bt.simulate_b1_trade(
        df,
        e,
        bbi_series(df["close"]),
        stop_override=stop,
        **LIVE_EXIT_KW,
    )
    ret_open = (
        1.0 + float(tr["ret"])
    ) * close_e / open_e - 1.0  # 收盘入场口径 → 开盘入场
    reason = str(tr.get("reason") or "")
    exit_idx = int(tr.get("exit_idx", len(df) - 1))
    out = {
        **base,
        "exit_date": dates[min(exit_idx, len(df) - 1)],
        "reason": reason,
        "holding": int(tr.get("holding") or 0),
    }
    if reason.split("+")[0].startswith("open_end") or reason.split("+")[0].endswith(
        "_unfillable"
    ):
        return {**out, "status": "open", "mark_ret": ret_open}
    return {**out, "status": "closed", "ret": ret_open - COST_BPS / 1e4}


def _stats(closed: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(closed)
    if not n:
        return {"n": 0}
    rets = [x["ret"] for x in closed]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    payoff = (
        avg_win / abs(avg_loss)
        if avg_win is not None and avg_loss is not None and avg_loss < 0
        else None
    )
    return {
        "n": n,
        "win_rate": len(wins) / n,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff,
        "avg_ret": sum(rets) / n,
        "avg_holding": sum(x["holding"] for x in closed) / n,
    }


def build_report(
    picks: list[dict[str, Any]],
    loader: Callable[[str], Any],
    month: str = "",
) -> dict[str, Any]:
    rows = []
    cache: dict[str, Any] = {}
    for p in picks:
        if month and not str(p["date"]).startswith(month):
            continue
        if p["code"] not in cache:
            cache[p["code"]] = loader(p["code"])
        rows.append({**p, **simulate_pick(cache[p["code"]], str(p["date"]))})
    closed = [r for r in rows if r["status"] == "closed"]
    by_month: dict[str, Any] = {}
    for m in sorted({str(r["date"])[:7] for r in closed}):
        by_month[m] = _stats([r for r in closed if str(r["date"]).startswith(m)])
    return {
        "schema": "pick_practice_report/v1",
        "generated_at": cn_now().isoformat(timespec="seconds"),
        "month_filter": month or None,
        "overall": _stats(closed),
        "by_month": by_month,
        "open": [r for r in rows if r["status"] == "open"],
        "pending": [r for r in rows if r["status"] in ("pending", "no_data")],
        "closed": closed,
        "rule": "入场 T+1 开盘；出场 B1 现行（止损 −10% 收盘触发 / 两阳分批 50% / BBI 两连破）；"
        "成本 25bps；open 不计入统计；live 跟踪，不作研究判据",
    }


def _default_loader(code: str) -> Any:
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    # live 跟踪读近期数据：前向 holdout 豁免（同 plan_shadow_review，v0.361 先例）
    return functools.partial(
        bt._load_one_bars, count=BAR_COUNT, start=None, end=None, allow_holdout=True
    )(code)


def _fmt_pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:+.2f}%"


def _print_report(rep: dict[str, Any]) -> None:
    o = rep["overall"]
    print(f"[选股练习] 已平仓 {o.get('n', 0)} 笔", end="")
    if o.get("n"):
        pr = o["payoff_ratio"]
        print(
            f"｜胜率 {o['win_rate'] * 100:.1f}%｜盈亏比 {'—' if pr is None else f'{pr:.2f}'}"
            f"｜均收 {_fmt_pct(o['avg_ret'])}｜均持 {o['avg_holding']:.1f} 天"
        )
    else:
        print()
    for m, st in rep["by_month"].items():
        pr = st.get("payoff_ratio")
        print(
            f"  {m}: {st['n']} 笔 胜率 {st['win_rate'] * 100:.1f}% "
            f"盈亏比 {'—' if pr is None else f'{pr:.2f}'} 均收 {_fmt_pct(st['avg_ret'])}"
        )
    if rep["open"]:
        print(f"  持有中 {len(rep['open'])} 只：", end="")
        print(
            "，".join(
                f"{r['code']}({_fmt_pct(r.get('mark_ret'))})" for r in rep["open"]
            )
        )
    if rep["pending"]:
        print(
            f"  待入场/缺数据 {len(rep['pending'])} 只：",
            ",".join(r["code"] for r in rep["pending"]),
        )


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="选股练习台账（TODO #101）：add 登记 / report 统计"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="登记当天（或 --date）选中的股票代码")
    a.add_argument("codes", nargs="+", help="6 位股票代码，空格分隔")
    a.add_argument("--date", default=None, help="选股日 YYYY-MM-DD（缺省=今天）")
    a.add_argument("--note", default="", help="备注（选股理由等）")
    r = sub.add_parser("report", help="按 B1 现行规则模拟并统计胜率/盈亏比")
    r.add_argument("--month", default="", help="只统计某月 YYYY-MM")
    r.add_argument("--out", default="", help="另存报告 JSON 路径")
    return ap


def main(argv: Optional[list[str]] = None, *, loader: Optional[Callable] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = _build_parser()
    args = ap.parse_args(argv)
    if args.cmd == "add":
        day = args.date or cn_now().strftime("%Y-%m-%d")
        try:
            new = append_picks(day, list(args.codes), args.note)
        except ValueError as exc:
            ap.error(str(exc))
        skipped = [c for c in args.codes if c not in new]
        print(f"[OK] {day} 登记 {len(new)} 只：{' '.join(new) or '—'}")
        if skipped:
            print(f"[INFO] 已登记过、跳过：{' '.join(skipped)}")
        return 0
    picks = load_picks()
    if not picks:
        print("[FAIL] 台账为空——先 `pick_practice add <代码>`", file=sys.stderr)
        return 2
    rep = build_report(picks, loader or _default_loader, month=args.month)
    _print_report(rep)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"[OK] 写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
