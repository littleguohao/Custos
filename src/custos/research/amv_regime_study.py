# -*- coding: utf-8 -*-
"""R51 0AMV 状态机行为研究（探索，只读挖掘 A∪B）。

判据与口径见 ``governance/research/R51_amv_regime_behavior.md``（跑数前写死）：

- ① 段内幅度与持续：每段做多 / 空头的持续天数、段内 0AMV 水平与上证指数的最大顺 / 逆向幅度、段末幅度；
- ②④ 翻转后 5 / 10 / 20 日内翻回的比例；
- ③ 按翻转日位置分层的 10 日反复率（指数 60 日位置 / 前段 0AMV 累计 / 触发当日增量）；
- ⑤ 变体 S1（+「连续 2 日均 >0 且合计 >4%」翻多）：两日型 vs 单日型翻多的反复率与 20 日前向收益。

数据：生产机读 0AMV 台账逐日增量（``bt._amv_ledger_records``，confirmed）；开发机快照只有逐日 regime
（``amv_regime_series.json``，无增量）⇒ 只出指数侧 ①~④，0AMV 侧与 ⑤ 标 ``unavailable``。

本单元不产候选、不入批：⑤ 只判「等效 / 不等效 / 证据不足」（描述性），改状态机须另立单元走判定段。
因此 criteria_kit 四态 / rdd 门 / 零假设两连测不适用（无候选晋级判定）；窗口纪律照常（mining_violation）。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Optional

from custos.core.paths import LOGS, RESEARCH_DIR
from custos.research import load_window as lw

CRITERIA_VERSION = "v0.511"

LONG, SHORT = "做多", "空头"
LONG_PCT, SHORT_PCT = (
    4.0,
    -2.3,
)  # 与 amv_state.AMV_LONG_PCT / AMV_SHORT_PCT 一致（钉测核对）
TWO_DAY_SUM = 4.0
REV_NS = (5, 10, 20)
POS_LOOKBACK = 60
FWD_N = 20
QS = (0.10, 0.25, 0.50, 0.75, 0.90)
BOOT_N, BOOT_SEED = 2000, 0
# ⑤ 判据（R51 写死）
GATE_REV_PP = 10.0
GATE_FWD_PP = 1.0
GATE_MIN_N = 10

# ③ 分层（R51 写死）
IDX_UP_TIERS = (
    (None, 5.0, "<5%"),
    (5.0, 10.0, "5~10%"),
    (10.0, 20.0, "10~20%"),
    (20.0, None, "≥20%"),
)
IDX_DN_TIERS = (
    (-5.0, None, ">−5%"),
    (-10.0, -5.0, "−5~−10%"),
    (-20.0, -10.0, "−10~−20%"),
    (None, -20.0, "≤−20%"),
)
TRIG_UP_TIERS = ((None, 5.0, "4~5%"), (5.0, 7.0, "5~7%"), (7.0, None, "≥7%"))
TRIG_DN_TIERS = ((-3.0, None, "−2.3~−3%"), (-5.0, -3.0, "−3~−5%"), (None, -5.0, "≤−5%"))


# ---------------------------------------------------------------------------
# 状态机重放
# ---------------------------------------------------------------------------


def replay(
    records: list[dict[str, Any]],
    variant: str = "S0",
    calendar: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """台账逐日增量 → 逐日 {date, state, chg, trigger}。

    S0 = 现行（同 ``bt._amv_regime_from_records``，起始中性）；S1 = S0 + 「当日与前一交易日增量均 >0 且
    合计 >4%」翻多（两日须相邻：``calendar`` 给定时以其为交易日历判相邻，否则以记录序相邻）。
    ``trigger``：single（当日 >4% 进 / 维持做多）/ two_day（只满足 S1 新条件）/ short / None。"""
    pos = {d: i for i, d in enumerate(calendar)} if calendar else None
    out: list[dict[str, Any]] = []
    state = "中性"
    prev: Optional[dict[str, Any]] = None
    for r in sorted(records, key=lambda x: str(x.get("date", ""))):
        d = str(r.get("date"))[:10]
        v = r.get("change_pct")
        trig = None
        if v is not None:
            v = float(v)
            if v > LONG_PCT:
                state, trig = LONG, "single"
            elif v < SHORT_PCT:
                state, trig = SHORT, "short"
            elif variant == "S1" and prev is not None and prev["chg"] is not None:
                adjacent = pos is None or (
                    d in pos and prev["date"] in pos and pos[d] - pos[prev["date"]] == 1
                )
                if (
                    adjacent
                    and v > 0
                    and prev["chg"] > 0
                    and v + prev["chg"] > TWO_DAY_SUM
                ):
                    state, trig = LONG, "two_day"
        row = {"date": d, "state": state, "chg": v, "trigger": trig}
        out.append(row)
        prev = row
    return out


def from_regime_series(series: dict[str, str]) -> list[dict[str, Any]]:
    """快照路径：只有 regime ⇒ 逐日 {date, state, chg=None, trigger=None}。"""
    return [
        {"date": d, "state": s, "chg": None, "trigger": None}
        for d, s in sorted((str(k)[:10], str(v)) for k, v in series.items())
    ]


# ---------------------------------------------------------------------------
# 事件与读数
# ---------------------------------------------------------------------------


def _tier(x: Optional[float], tiers: tuple) -> Optional[str]:
    if x is None or x != x:
        return None
    for lo, hi, name in tiers:
        if (lo is None or x >= lo) and (hi is None or x < hi):
            return name
    return None


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0 or a != a or b != b:
        return None
    return round((a / b - 1.0) * 100.0, 4)


def amv_levels(days: list[dict[str, Any]]) -> list[Optional[float]]:
    """逐日增量连乘还原 0AMV 相对水平；缺增量当日 ⇒ 水平延续（只要曾有过读数）。全无增量 ⇒ 全 None。"""
    lv: list[Optional[float]] = []
    cur: Optional[float] = None
    for r in days:
        if r["chg"] is not None:
            cur = (cur if cur is not None else 100.0) * (1.0 + r["chg"] / 100.0)
        lv.append(cur)
    return lv


def flip_events(
    days: list[dict[str, Any]],
    index_close: dict[str, float],
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    """窗内翻转事件（做多 ⇄ 空头；中性起始不算翻转）与其段读数。"""
    dates = [r["date"] for r in days]
    states = [r["state"] for r in days]
    lv = amv_levels(days)
    idx = [index_close.get(d) for d in dates]
    flips = [
        i
        for i in range(1, len(days))
        if states[i] != states[i - 1]
        and states[i] in (LONG, SHORT)
        and states[i - 1] in (LONG, SHORT)
    ]
    events: list[dict[str, Any]] = []
    for k, p in enumerate(flips):
        if not (start <= dates[p] <= end):
            continue
        nxt = flips[k + 1] if k + 1 < len(flips) else None
        censored = nxt is None or dates[nxt] > end
        if nxt is not None and not censored:
            last = nxt - 1
            duration: Optional[int] = nxt - p
        else:
            last = max(i for i in range(p, len(dates)) if dates[i] <= end)
            duration = None
        up = states[p] == LONG
        seg = range(p, last + 1)
        ev: dict[str, Any] = {
            "date": dates[p],
            "dir": "up" if up else "down",
            "trigger": days[p]["trigger"],
            "trigger_chg": days[p]["chg"],
            "censored": censored,
            "duration": duration,
            "seen_days": last - p + 1,
        }
        for name, ser in (("idx", idx), ("amv", lv)):
            base = ser[p]
            vals = [v for v in (ser[i] for i in seg) if v is not None]
            if base is None or not vals:
                ev[f"{name}_fav"] = ev[f"{name}_adv"] = ev[f"{name}_end"] = None
                continue
            hi, lo = _pct(max(vals), base), _pct(min(vals), base)
            ev[f"{name}_fav"], ev[f"{name}_adv"] = (hi, lo) if up else (lo, hi)
            ev[f"{name}_end"] = _pct(ser[last], base) if not censored else None
        # ② ④ N 日反复：前视窗须落在窗内（或翻回已在窗内发生）
        for n in REV_NS:
            known = (p + n < len(dates) and dates[p + n] <= end) or (
                nxt is not None and nxt - p <= n and dates[nxt] <= end
            )
            ev[f"rev{n}"] = bool(nxt is not None and nxt - p <= n) if known else None
        # ③ 位置
        lb = [c for c in idx[max(0, p - POS_LOOKBACK + 1) : p + 1] if c is not None]
        ev["idx_pos"] = (
            (_pct(idx[p], min(lb)) if up else _pct(idx[p], max(lb)))
            if lb and idx[p] is not None
            else None
        )
        prev_start = flips[k - 1] if k > 0 else None
        ev["amv_prior_seg"] = (
            _pct(lv[p - 1], lv[prev_start])
            if prev_start is not None
            and lv[p - 1] is not None
            and lv[prev_start] is not None
            else None
        )
        # ⑤ 前向 20 日
        ev["fwd20"] = (
            _pct(idx[p + FWD_N], idx[p])
            if p + FWD_N < len(dates) and dates[p + FWD_N] <= end
            else None
        )
        events.append(ev)
    return events


def _quantiles(xs: list[float]) -> dict[str, Any]:
    xs = sorted(x for x in xs if x is not None and x == x)
    if not xs:
        return {"n": 0}
    out: dict[str, Any] = {"n": len(xs), "mean": round(sum(xs) / len(xs), 4)}
    for q in QS:
        out[f"p{int(q * 100)}"] = round(
            xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 4
        )
    return out


def _rate(evs: list[dict[str, Any]], key: str) -> dict[str, Any]:
    v = [e[key] for e in evs if e.get(key) is not None]
    return {"n": len(v), "rate": round(sum(v) / len(v), 4) if v else None}


def window_readings(events: list[dict[str, Any]], has_amv: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for d in ("up", "down"):
        evs = [e for e in events if e["dir"] == d]
        unc = [e for e in evs if not e["censored"]]
        blk: dict[str, Any] = {
            "n_flips": len(evs),
            "n_censored": len(evs) - len(unc),
            "duration": _quantiles([e["duration"] for e in unc]),
            "reversal": {f"rev{n}": _rate(evs, f"rev{n}") for n in REV_NS},
        }
        for nm in ("idx", "amv"):
            if nm == "amv" and not has_amv:
                blk["amv"] = "unavailable"
                continue
            blk[nm] = {
                "fav": _quantiles([e[f"{nm}_fav"] for e in evs]),
                "adv": _quantiles([e[f"{nm}_adv"] for e in evs]),
                "end": _quantiles([e[f"{nm}_end"] for e in unc]),
            }
        layers: dict[str, Any] = {}
        specs: list[tuple[str, tuple]] = [
            ("idx_pos", IDX_UP_TIERS if d == "up" else IDX_DN_TIERS)
        ]
        if has_amv:
            # 前段（反向状态）0AMV 累计：空翻多看前段跌幅档，多翻空看前段涨幅档
            specs.append(("amv_prior_seg", IDX_DN_TIERS if d == "up" else IDX_UP_TIERS))
            specs.append(("trigger_chg", TRIG_UP_TIERS if d == "up" else TRIG_DN_TIERS))
        for key, tiers in specs:
            tb: dict[str, Any] = {}
            for _lo, _hi, name in tiers:
                sub = [e for e in evs if _tier(e.get(key), tiers) == name]
                tb[name] = _rate(sub, "rev10")
            layers[key] = tb
        blk["rev10_by_position"] = layers
        out[d] = blk
    return out


def _boot_diff(a: list[float], b: list[float], seed: int) -> Optional[list[float]]:
    if len(a) < 2 or len(b) < 2:
        return None
    rng = random.Random(seed)
    ds = []
    for _ in range(BOOT_N):
        ra = [a[rng.randrange(len(a))] for _ in a]
        rb = [b[rng.randrange(len(b))] for _ in b]
        ds.append(sum(ra) / len(ra) - sum(rb) / len(rb))
    ds.sort()
    return [round(ds[int(0.025 * BOOT_N)], 4), round(ds[int(0.975 * BOOT_N) - 1], 4)]


def two_day_readings(
    s0: list[dict[str, Any]],
    s1: list[dict[str, Any]],
    index_close: dict[str, float],
    start: str,
    end: str,
) -> dict[str, Any]:
    """⑤：S1 两日型 vs 单日型翻多（判据 a/b/c）+ 描述性附报。"""
    evs = [e for e in flip_events(s1, index_close, start, end) if e["dir"] == "up"]
    two = [e for e in evs if e["trigger"] == "two_day"]
    one = [e for e in evs if e["trigger"] == "single"]
    r2, r1 = _rate(two, "rev10"), _rate(one, "rev10")
    f2 = [e["fwd20"] for e in two if e["fwd20"] is not None]
    f1 = [e["fwd20"] for e in one if e["fwd20"] is not None]
    m2 = round(sum(f2) / len(f2), 4) if f2 else None
    m1 = round(sum(f1) / len(f1), 4) if f1 else None
    rv2 = [float(e["rev10"]) * 100 for e in two if e["rev10"] is not None]
    rv1 = [float(e["rev10"]) * 100 for e in one if e["rev10"] is not None]
    rev_diff = (
        round((r2["rate"] - r1["rate"]) * 100, 4)
        if r2["rate"] is not None and r1["rate"] is not None
        else None
    )
    fwd_diff = round(m2 - m1, 4) if m2 is not None and m1 is not None else None
    gate = {
        "a_rev": rev_diff is not None and rev_diff <= GATE_REV_PP,
        "b_fwd": fwd_diff is not None and fwd_diff >= -GATE_FWD_PP,
        "c_n": len(two) >= GATE_MIN_N,
    }
    # 附报：S1 多出的做多日、次日上证收益；两日型比 S0 提前的交易日数
    dates = [r["date"] for r in s1]
    st0 = {r["date"]: r["state"] for r in s0}
    idx = [index_close.get(d) for d in dates]
    nxt = [
        _pct(idx[i + 1], idx[i]) if i + 1 < len(dates) else None
        for i in range(len(dates))
    ]
    in_w = [
        i
        for i, d in enumerate(dates)
        if start <= d <= end and i + 1 < len(dates) and dates[i + 1] <= end
    ]
    extra = [i for i in in_w if s1[i]["state"] == LONG and st0.get(dates[i]) != LONG]
    base = [i for i in in_w if st0.get(dates[i]) == LONG]

    def _mean(ix: list[int]) -> Optional[float]:
        v = [x for x in (nxt[i] for i in ix) if x is not None]
        return round(sum(v) / len(v), 4) if v else None

    pos = {d: i for i, d in enumerate(dates)}
    leads: list[int] = []
    never = 0
    for e in two:
        p = pos[e["date"]]
        q = p
        while q < len(dates) and dates[q] <= end and s1[q]["state"] == LONG:
            if st0.get(dates[q]) == LONG:
                break
            q += 1
        if (
            q < len(dates)
            and dates[q] <= end
            and s1[q]["state"] == LONG
            and st0.get(dates[q]) == LONG
        ):
            leads.append(q - p)
        else:
            never += 1
    return {
        "n_two_day": len(two),
        "n_single": len(one),
        "rev10_two_day": r2,
        "rev10_single": r1,
        "rev10_diff_pp": rev_diff,
        "rev10_diff_ci95": _boot_diff(rv2, rv1, BOOT_SEED),
        "fwd20_two_day": {"n": len(f2), "mean": m2},
        "fwd20_single": {"n": len(f1), "mean": m1},
        "fwd20_diff_pp": fwd_diff,
        "fwd20_diff_ci95": _boot_diff(f2, f1, BOOT_SEED + 1),
        "gate": gate,
        "aux": {
            "extra_long_days": len(extra),
            "extra_next_day_ret_mean": _mean(extra),
            "s0_long_days": len(base),
            "s0_next_day_ret_mean": _mean(base),
            "lead_days_vs_s0": _quantiles([float(x) for x in leads]),
            "s0_never_long_in_segment": never,
        },
        "events": [
            {k: e[k] for k in ("date", "trigger", "trigger_chg", "rev10", "fwd20")}
            for e in two
        ],
    }


def verdict_two_day(per_win: dict[str, dict[str, Any]]) -> str:
    gates = [w["gate"] for w in per_win.values()]
    if any(not g["c_n"] for g in gates):
        return "insufficient"
    if all(g["a_rev"] and g["b_fwd"] for g in gates):
        return "equivalent"
    return "not_equivalent"


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


def run_study(
    args: Any,
    *,
    records: Optional[list[dict[str, Any]]] = None,
    regime_series: Optional[dict[str, str]] = None,
    index_close: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    from custos.research import provenance as pv  # noqa: PLC0415

    segs = [("mining_a", *lw.SEG_MINING_A), ("mining_b", *lw.SEG_MINING_B)]
    for nm, s, e in segs:
        bad = lw.mining_violation(s, e)
        if bad:
            raise RuntimeError(f"amv_regime_study 窗口校验失败 {nm}: {bad}")
    if index_close is None:
        records, regime_series, index_close = _production()
    calendar = sorted(index_close)
    has_amv = bool(records)
    if has_amv:
        assert records is not None  # noqa: S101
        s0 = replay(records, "S0", calendar)
        s1 = replay(records, "S1", calendar)
    elif regime_series:
        s0, s1 = from_regime_series(regime_series), None
    else:
        raise RuntimeError("0AMV 台账与 regime 序列都读不到——不落盘")

    windows: dict[str, Any] = {}
    two_day: dict[str, Any] = {}
    n_events = 0
    for nm, s, e in segs:
        evs = flip_events(s0, index_close, s, e)
        n_events += len(evs)
        windows[nm] = {"span": [s, e], **window_readings(evs, has_amv)}
        if s1 is not None:
            two_day[nm] = two_day_readings(s0, s1, index_close, s, e)
    if n_events == 0:
        raise RuntimeError("空结果护栏：两挖掘窗 0 个翻转事件——不落盘")
    return {
        "unit": "R51",
        "kind": "r51",
        "tag": args.tag,
        "criteria_version": CRITERIA_VERSION,
        "amv_source": "ledger" if has_amv else "regime_series",
        "windows": windows,
        "two_day": two_day if two_day else "unavailable",
        "two_day_verdict": verdict_two_day(two_day) if two_day else "unavailable",
        "window_usage": {
            "window": "not_read",
            "k": None,
            "note": "只读挖掘 A∪B——判定段只用于状态延续不出读数、window_usage 不登记",
        },
        "provenance": pv.build(
            args,
            unit="R51",
            criteria_version=CRITERIA_VERSION,
            pre_reg_doc=RESEARCH_DIR / "R51_amv_regime_behavior.md",
            data_last_date=calendar[-1] if calendar else None,
        ),
    }


def _production() -> tuple[list[dict[str, Any]], dict[str, str], dict[str, float]]:
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415
    from custos.research import backtest_factors as bt  # noqa: PLC0415
    from custos.research import score_return_study as srs  # noqa: PLC0415

    df = local_tdx_data.get_ohlcv_table(srs.INDEX_CODE, count=100000)
    index_close = {
        str(d)[:10]: float(c) for d, c in zip(df["date"], df["close"]) if c == c
    }
    records: list[dict[str, Any]] = []
    series: dict[str, str] = {}
    if bt.is_snapshot_mode():
        series = bt.load_amv_regime(since="1990-01-01")
    else:
        records = bt._amv_ledger_records("0000-00-00", None)
    return records, series, index_close


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R51 0AMV 状态机行为（探索，只读挖掘 A∪B）：段内幅度 / 翻转后反复 / 连续 2 日合计 >4% 变体"
    )
    ap.add_argument("--tag", default="r51_v1")
    ap.add_argument("--out-dir", default=str(LOGS / "amv_regime"))
    return ap


def main(argv: Optional[list[str]] = None, **inj: Any) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    args.cmdline = " ".join(sys.argv if argv is None else ["amv_regime_study", *argv])
    try:
        rep = run_study(args, **inj)
    except (RuntimeError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    out = Path(args.out_dir) / f"_amv_regime__{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str, allow_nan=True),
        encoding="utf-8",
    )
    print(f"[R51] source={rep['amv_source']}  ⑤={rep['two_day_verdict']}")
    print(f"[OK] 写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
