# -*- coding: utf-8 -*-
"""R48 B1 大单入场特征（v0.439，TODO #115；预注册 governance/research/R48_tail_entry_features.md）。

交易集 = 0AMV 做多 + J<13 信号 × 现行出场 ``LIVE_PARAMS``（R44 扫描同一套 warm / 段末隔离带 / 段前预热，
``plan_rules_replay._scan_prepare``），只读挖掘 A∪B。大单 = 单笔已实现收益 ≥ +20%；大亏 = ≤ −10%。

7 个特征（信号日 as-of，写死）：
- ``mcap_small``：流通市值最低五分位；``deep_dd``：距 250 日高点回撤最深五分位（R43 ``_own_feature_frame`` 同源）；
- ``limitup_active``：近 60 日涨停 ≥1；
- ``fund_good``：PIT 品质档 = 优——live ``fundamental_quality`` 的「真业绩 + 现金流 + ROE 正」用 PIT 字段重述：
  ``pe_ttm>0``（TTM 净利正）∧ ``cfo_to_np>0``（经营现金流同号为正）∧ ``roe_ttm>0``；PIT 缺 ⇒ 缺失；
- ``tech_high``：V0 as-of 技术分 ≥ 60（warm 产出的 score，live 同源）；
- ``resonance3``：``fund_good ∧ tech_high``（= 1800「可买」，市场腿由基底钉住）；
- ``legs_aligned``：板块相位有利 / 基本面优或中（``pe_ttm>0``）/ 技术强 三腿命中 ≥ 2；任一腿缺 ⇒ 缺失。板块腿 =
  成分快照（``sector_members.json``，前视偏差注记）× 板块指数 as-of 相位（``compute_sector_phase`` 截至信号日、
  末 ``SECTOR_TAIL`` 根近似）。

跑数前修订（v0.443，owner 2026-10-21；R20 赢家画像「超卖更深」的 4 个特征，max-of-7 → max-of-11）：
- ``rsi_deep``：信号日 RSI14 < 25（``rsi_state.DEEP_OVERSOLD``）；
- ``rsi_deep_recent10``：信号日及之前 10 个交易日内 RSI14 最低值 < 25（只判 N=10；N=5/20 描述性必报）；
- ``weekly_j_low``：周线 J < 13（``weekly_j.weekly_j_state``）；``rsi_bottom_div``：RSI 底背离（``rsi_state.rsi_divergence``）。
四者都在 live 同口径的 as-of 帧（信号日前 ``OHLCV_LOAD_BARS`` 根）上算。

判据（逐特征，跑数前写死）：C1 两窗高档 n ≥ 2000 且缺失率 ≤ 50%；C2 两窗 lift_big > 1.2；C3 ①两窗 lift_big/lift_loss > 1
②五分位单调（Spearman ≥ 0.8）/ 二值逐年 lift_big > 1 的年份 ≥ 2/3；C4 簇级置换（特征向量按簇整体换位，逐窗）
max-of-11 的 min(两窗 lift_big)，候选 > ``kit.q95``。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from custos.core.paths import LOGS, RESEARCH_DIR

BIG_WIN = 0.20
BIG_LOSS = -0.10
LIFT_MIN = 1.2
MIN_N_HI = 2000
MAX_MISSING = 0.5
SPEARMAN_MIN = 0.8
YEAR_FRAC = 2 / 3
YEAR_MIN_HI = 50
N_RANDOM = 200
C4_MIN_POOL = 50
SECTOR_TAIL = 400
QUINTILE = {"mcap_small": "mcap_float", "deep_dd": "dist_high250"}  # 检验最低档
BINARY = (
    "limitup_active",
    "fund_good",
    "tech_high",
    "resonance3",
    "legs_aligned",
    "rsi_deep",
    "rsi_deep_recent10",
    "weekly_j_low",
    "rsi_bottom_div",
)
RSI_DEEP = 25.0  # = rsi_state.DEEP_OVERSOLD（钉测对齐）
OSC_N = 10  # 判据档（写死）
OSC_DESC_N = (5, 20)  # 描述性必报档
# 描述性必报（不进判据 / 不进 max-of-k）：回看档 + 深水拆解 + 波动率对照
DESC = (
    "rsi_deep_recent5",
    "rsi_deep_recent20",
    "deep_now",
    "deep_recent_only",
    "vol60",
)
FEATURES = (*QUINTILE, *BINARY)

# ---------------------------------------------------------------------------
# 特征
# ---------------------------------------------------------------------------


def _nan_and(*xs: float) -> float:
    if any(x != x for x in xs):
        return float("nan")
    return 1.0 if all(x > 0 for x in xs) else 0.0


def _lt(x: Any, thr: float) -> float:
    return float("nan") if x is None or x != x else float(x < thr)


def derive(
    raw: dict[str, float],
    score: Optional[float],
    sector_fav: Optional[bool],
    osc: Optional[dict[str, Any]] = None,
) -> dict[str, float]:
    """原始 as-of 值 → 11 特征 + 描述性 5 项（二值 1/0，缺失 NaN；五分位类保留原值，分档在窗内做）。"""
    nan = float("nan")
    o = osc or {}
    now = _lt(o.get("rsi_now"), RSI_DEEP)
    rec = {n: _lt(o.get(f"rsi_min_{n}"), RSI_DEEP) for n in (OSC_N, *OSC_DESC_N)}
    wj = o.get("weekly_j_low")
    dv = o.get("rsi_bottom_div")
    pe, cfo, roe = (
        raw.get("pe_ttm", nan),
        raw.get("cfo_to_np", nan),
        raw.get("roe_ttm", nan),
    )
    fund_good = _nan_and(pe, cfo, roe) if pe == pe else nan
    fund_mid = (1.0 if pe > 0 else 0.0) if pe == pe else nan
    tech = (1.0 if score >= 60 else 0.0) if isinstance(score, (int, float)) else nan
    lu = raw.get("limitup60", nan)
    sec = nan if sector_fav is None else float(bool(sector_fav))
    legs = (
        nan
        if any(x != x for x in (sec, fund_mid, tech))
        else float(sec + fund_mid + tech >= 2)
    )
    return {
        "mcap_small": raw.get("mcap_float", nan),
        "deep_dd": raw.get("dist_high250", nan),
        "limitup_active": nan if lu != lu else float(lu >= 1),
        "fund_good": fund_good,
        "tech_high": tech,
        "resonance3": nan
        if fund_good != fund_good or tech != tech
        else float(fund_good and tech),
        "legs_aligned": legs,
        "rsi_deep": now,
        "rsi_deep_recent10": rec[OSC_N],
        "weekly_j_low": nan if wj is None else float(bool(wj)),
        "rsi_bottom_div": nan if dv is None else float(bool(dv)),
        "rsi_deep_recent5": rec[5],
        "rsi_deep_recent20": rec[20],
        "deep_now": now,
        "deep_recent_only": nan
        if now != now or rec[OSC_N] != rec[OSC_N]
        else float(rec[OSC_N] == 1 and now == 0),
        "vol60": raw.get("vol60", nan),
    }


def default_osc_fn() -> Callable[[str, Any, int], dict[str, Any]]:
    """超卖 4 特征的 as-of 原始量（live 同口径：信号日前 ``OHLCV_LOAD_BARS`` 根帧上算）。"""
    import pandas as pd  # noqa: PLC0415

    from custos.core.factors import rsi_state, weekly_j  # noqa: PLC0415
    from custos.core.indicators import rsi  # noqa: PLC0415
    from custos.pipeline.screening import enrich_candidates as ec  # noqa: PLC0415

    def fn(code: str, df_full: Any, i: int) -> dict[str, Any]:
        pre = df_full.iloc[: i + 1].tail(ec.OHLCV_LOAD_BARS).reset_index(drop=True)
        if not pd.api.types.is_datetime64_any_dtype(pre["date"]):
            pre = pre.assign(date=pd.to_datetime(pre["date"]))
        out: dict[str, Any] = {}
        if len(pre) >= rsi_state.RSI_MIN_BARS:
            r = rsi(pre["close"], rsi_state.RSI_MID)
            out["rsi_now"] = float(r.iloc[-1])
            for n in (OSC_N, *OSC_DESC_N):
                seg = r.iloc[-(n + 1) :].dropna()
                out[f"rsi_min_{n}"] = float(seg.min()) if len(seg) else None
            dv = rsi_state.rsi_divergence(pre, rsi_series=r)
            out["rsi_bottom_div"] = (
                bool(dv.get("bullish")) if dv.get("available") else None
            )
        wj = weekly_j.weekly_j_state(pre)
        out["weekly_j_low"] = (
            bool(wj.get("weekly_j_low")) if wj.get("weekly_j_available") else None
        )
        return out

    return fn


def default_feature_fn() -> Callable[[str, Any], Any]:
    """R43 as-of 特征矩阵同源（股本 / PIT / 权息走 regime_style_study 的默认加载器）。"""
    from custos.core.code_utils import price_limit_pct  # noqa: PLC0415
    from custos.research import regime_style_study as rs  # noqa: PLC0415

    pit = rs._pit_tables(rs._default_pit_records(), [])

    def fn(code: str, df: Any) -> Any:
        return rs._own_feature_frame(
            code,
            df,
            shares_events=rs._default_shares_fn(code),
            pit_table=(pit or {}).get(code),
            xdxr_events=rs._default_xdxr_fn(code),
            limit_pct=price_limit_pct(code),
        )

    return fn


def default_sector_fn() -> Callable[[str, str], Optional[bool]]:
    """板块腿：成分快照 × 板块指数 as-of 相位（末 SECTOR_TAIL 根近似，(板块, 日期) 缓存）。读不到 ⇒ 恒 None。"""
    import pandas as pd  # noqa: PLC0415

    from custos.core.factors import sector_phase as sp  # noqa: PLC0415
    from custos.core.paths import SECTOR_INDEX_DIR, SECTOR_MEMBERS_FILE  # noqa: PLC0415
    from custos.core import paths as paths_mod  # noqa: PLC0415

    try:
        members = json.loads(Path(SECTOR_MEMBERS_FILE).read_text(encoding="utf-8"))
        members = members.get("members", members) if isinstance(members, dict) else {}
    except (OSError, ValueError):
        return lambda code, d: None
    c2s: dict[str, list[str]] = {}
    for sec, codes in members.items():
        for c in codes or []:
            c2s.setdefault(str(c)[-6:].zfill(6), []).append(sec)
    series: dict[str, tuple[list[str], list[float]]] = {}
    cache: dict[tuple[str, str], Optional[bool]] = {}

    def _series(sec: str) -> Optional[tuple[list[str], list[float]]]:
        if sec not in series:
            p = Path(SECTOR_INDEX_DIR) / f"{sec}.csv"
            try:
                df = pd.read_csv(p)
                series[sec] = (
                    df["date"].astype(str).str[:10].tolist(),
                    df["close"].astype(float).tolist(),
                )
            except (OSError, KeyError, ValueError):
                series[sec] = ([], [])
            # 快照污染兜底（v0.454）：在吞异常的 try 之外
            paths_mod.snapshot_gate_dates(series[sec][0], f"板块指数 {p.name}")
        return series[sec] if series[sec][0] else None

    def fn(code: str, d: str) -> Optional[bool]:
        import bisect  # noqa: PLC0415

        out: Optional[bool] = None
        for sec in c2s.get(code, []):
            k = (sec, d)
            if k not in cache:
                s = _series(sec)
                if s is None:
                    cache[k] = None
                else:
                    j = bisect.bisect_right(s[0], d)
                    ph = (
                        sp.compute_sector_phase(s[1][max(0, j - SECTOR_TAIL) : j])
                        if j
                        else {}
                    )
                    cache[k] = bool(ph["favorable"]) if ph.get("available") else None
            v = cache[k]
            if v:
                return True
            if v is False:
                out = False
        return out

    return fn


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


def _spearman_desc(rates: list[float]) -> Optional[float]:
    """档序 q0（检验端）→ q4 的大单率是否单调递减：rank 相关(rate, 4−q)。"""
    r = [x for x in rates]
    if len(r) < 3 or any(x != x for x in r):
        return None
    a = np.argsort(np.argsort(r)).astype(float)
    b = np.arange(len(r))[::-1].astype(float)
    if a.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def hi_mask(vals: np.ndarray, kind: str) -> np.ndarray:
    """高档掩码（与 feature_stats 同口径；缺失 ⇒ False）。"""
    ok = ~np.isnan(vals)
    out = np.zeros(len(vals), dtype=bool)
    if not ok.any():
        return out
    if kind == "quintile":
        cuts = np.nanpercentile(vals[ok], [20, 40, 60, 80])
        out[ok] = np.searchsorted(cuts, vals[ok], side="right") == 0
    else:
        out[ok] = vals[ok] > 0.5
    return out


def _ret_block(r: np.ndarray, hi: np.ndarray) -> dict[str, Any]:
    from custos.research import m2_stop_sweep as m2  # noqa: PLC0415

    if not hi.any():
        return {}
    x = r[hi]
    w, lo = x[x > 0], x[x <= 0]
    payoff = (
        float(w.mean() / -lo.mean()) if len(w) and len(lo) and lo.mean() < 0 else None
    )
    return {
        "win_rate_hi": float((x > 0).mean()),
        "mean_ret_hi": float(x.mean()),
        "payoff_hi": payoff,
        # margin = 胜率 − 盈亏平衡胜率 1/(1+盈亏比)（m2 `_margin` 同口径，项目内 margin 的唯一含义）
        "margin_hi": m2._margin({"win": float((x > 0).mean()), "payoff": payoff}),
        # 高档均收益 − 全体均收益（描述性，非 margin；v0.444 前曾误名 margin_hi）
        "mean_ret_diff": float(x.mean() - r.mean()),
    }


def feature_stats(
    vals: np.ndarray,
    big: np.ndarray,
    loss: np.ndarray,
    year: np.ndarray,
    kind: str,
    ret: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    ok = ~np.isnan(vals)
    n_all = int(ok.sum())
    out: dict[str, Any] = {
        "n_all": n_all,
        "missing_rate": 1 - n_all / len(vals) if len(vals) else None,
    }
    if n_all == 0:
        return {**out, "n_hi": 0}
    v, b, lo, y = vals[ok], big[ok], loss[ok], year[ok]
    if kind == "quintile":
        cuts = np.nanpercentile(v, [20, 40, 60, 80])
        q = np.searchsorted(cuts, v, side="right")  # 0 = 最低档（检验端）
        hi = q == 0
        rates = [
            float(b[q == k].mean()) if (q == k).any() else float("nan")
            for k in range(5)
        ]
        out["quintile_big_rate"] = rates
        out["spearman"] = _spearman_desc(rates)
    else:
        hi = v > 0.5
    pb, pl = float(b.mean()), float(lo.mean())
    n_hi = int(hi.sum())
    pbh = float(b[hi].mean()) if n_hi else float("nan")
    plh = float(lo[hi].mean()) if n_hi else float("nan")
    lift_b = pbh / pb if pb else float("nan")
    lift_l = plh / pl if pl else float("nan")
    out.update(
        {
            "n_hi": n_hi,
            "p_big_all": pb,
            "p_big_hi": pbh,
            "lift_big": lift_b,
            "p_loss_all": pl,
            "p_loss_hi": plh,
            "lift_loss": lift_l,
            "tail_ratio": lift_b / lift_l
            if lift_l and lift_l == lift_l
            else float("nan"),
        }
    )
    if ret is not None:
        out.update(_ret_block(ret[ok], hi))
    if kind != "quintile":
        yrs = []
        for yy in np.unique(y):
            m = y == yy
            hm = hi & m
            if hm.sum() >= YEAR_MIN_HI and b[m].mean() > 0:
                yrs.append(float(b[hm].mean() / b[m].mean()))
        out["year_lifts_n"] = len(yrs)
        out["year_frac_gt1"] = (
            (sum(1 for x in yrs if x > 1) / len(yrs)) if yrs else None
        )
    return out


def _all_stats(
    win: dict[str, Any], perm: Optional[np.ndarray] = None
) -> dict[str, dict[str, Any]]:
    out = {}
    for f in FEATURES:
        v = win["feat"][f] if perm is None else win["feat"][f][perm]
        out[f] = feature_stats(
            v,
            win["big"],
            win["loss"],
            win["year"],
            "quintile" if f in QUINTILE else "binary",
            win["ret"] if perm is None else None,
        )
    return out


def descriptive(win: dict[str, Any], feats: dict[str, Any], w: str) -> dict[str, Any]:
    """描述性必报（不进判据）：回看 N=5/20、深水拆解、vol60 五分位对照、各特征高档 trade_dist 画像。"""
    from custos.research import trade_dist as td  # noqa: PLC0415

    out: dict[str, Any] = {}
    for f in DESC:
        kind = "quintile" if f == "vol60" else "binary"
        out[f] = feature_stats(
            win["feat"][f], win["big"], win["loss"], win["year"], kind, win["ret"]
        )
    for f in FEATURES:
        hi = hi_mask(win["feat"][f], "quintile" if f in QUINTILE else "binary")
        feats[f]["windows"][w]["dist_hi"] = td.compact(
            [t for t, h in zip(win["trades"], hi) if h]
        )
    return out


def _min_lift(per_w: dict[str, dict[str, dict[str, Any]]], f: str) -> Optional[float]:
    xs: list[float] = []
    for w in per_w:
        x = per_w[w][f].get("lift_big")
        if x is None or x != x:
            return None
        xs.append(float(x))
    return min(xs)


def cluster_perm(items: list[dict], rng: random.Random) -> np.ndarray:
    """簇级置换（R46 permute_clusters 同构）：按簇长分层，把各簇的特征向量块整体换位，簇内顺序不变。"""
    from custos.research import criteria_kit as kit  # noqa: PLC0415

    cids = kit.cluster_ids(items)
    members: dict[int, list[int]] = {}
    for idx, c in enumerate(cids):
        members.setdefault(c, []).append(idx)
    for c in members:
        members[c].sort(key=lambda j: items[j].get("i", 0))
    by_len: dict[int, list[int]] = {}
    for c, mem in members.items():
        by_len.setdefault(len(mem), []).append(c)
    perm = np.arange(len(items))
    for _ln, cs in sorted(by_len.items()):
        tgt = cs[:]
        rng.shuffle(tgt)
        for src, dst in zip(cs, tgt):
            perm[members[dst]] = members[src]
    return perm


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def build_window(
    group: dict[str, Any],
    trades: list[dict],
    feature_fn: Callable,
    sector_fn: Callable,
    osc_fn: Optional[Callable] = None,
) -> dict[str, Any]:
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    rec_of = {(r["code"], r["date"]): r for r in group["subset"]}
    frames: dict[str, dict[str, dict[str, float]]] = {}
    items: list[dict[str, Any]] = []
    rets: list[float] = []
    feats: dict[str, list[float]] = {f: [] for f in (*FEATURES, *DESC)}
    raws: list[dict[str, Any]] = []
    kept: list[dict] = []
    for t in trades:
        if bt.is_unrealized_reason(t.get("reason")) or not isinstance(
            t.get("ret"), (int, float)
        ):
            continue
        code, d = t["code"], str(t["entry_date"])[:10]
        rec = rec_of.get((code, d))
        if rec is None:
            continue
        if code not in frames:
            fr = feature_fn(code, group["per_code"][code]["df"])
            frames[code] = (
                {
                    str(row["date"])[:10]: {
                        c: float(row[c])
                        for c in fr.columns
                        if c != "date" and isinstance(row[c], (int, float, np.number))
                    }
                    for _, row in fr.iterrows()
                }
                if fr is not None and len(fr)
                else {}
            )
        raw = frames[code].get(d, {})
        sec = sector_fn(code, d)
        osc = (
            osc_fn(code, group["per_code"][code]["df"], rec.get("i", 0))
            if osc_fn
            else None
        )
        fv = derive(raw, rec.get("score"), sec, osc)
        for f in feats:
            feats[f].append(fv[f])
        raws.append(
            {
                "raw": raw,
                "osc": osc or {},
                "score": rec.get("score"),
                "sector_fav": sec,
                "rec": rec,
            }
        )
        kept.append(t)
        rets.append(float(t["ret"]))
        items.append(
            {"code": code, "i": rec.get("i", 0), "date": d, "_seg": group.get("name")}
        )
    r = np.array(rets)
    return {
        "items": items,
        "big": r >= BIG_WIN,
        "loss": r <= BIG_LOSS,
        "year": np.array([int(it["date"][:4]) for it in items]),
        "feat": {f: np.array(feats[f], dtype=float) for f in feats},
        "ret": r,
        "raw": raws,
        "trades": kept,
        "df_of": {c: group["per_code"][c]["df"] for c in frames},
        "trades_n": len(items),
    }


def judge(per_w: dict[str, dict[str, dict[str, Any]]], f: str) -> dict[str, Any]:
    ws = list(per_w)
    s = {w: per_w[w][f] for w in ws}
    c1 = all(
        s[w].get("n_hi", 0) >= MIN_N_HI
        and (s[w].get("missing_rate") or 0) <= MAX_MISSING
        for w in ws
    )
    c2 = all((s[w].get("lift_big") or 0) > LIFT_MIN for w in ws)
    tail = all((s[w].get("tail_ratio") or 0) > 1 for w in ws)
    if f in QUINTILE:
        stab = all((s[w].get("spearman") or -1) >= SPEARMAN_MIN for w in ws)
    else:
        stab = all((s[w].get("year_frac_gt1") or 0) >= YEAR_FRAC for w in ws)
    return {
        "C1": {"ok": c1},
        "C2": {"ok": c2, "min_lift_big": _min_lift(per_w, f)},
        "C3": {"ok": bool(tail and stab), "tail_ok": tail, "stability_ok": stab},
    }


def _replay_session(fn: Any) -> Any:
    """v0.464：逐笔重放备忘会话（``factor_exit_study.replay_session``，惰性导入防环）。"""
    import functools  # noqa: PLC0415

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        from custos.research import factor_exit_study as _fes  # noqa: PLC0415

        return _fes.replay_session(fn)(*a, **kw)

    return wrapper


@_replay_session
def run_study(
    args: Any,
    *,
    warm_fn: Optional[Callable] = None,
    replay_fn: Optional[Callable] = None,
    feature_fn: Optional[Callable] = None,
    sector_fn: Optional[Callable] = None,
    osc_fn: Optional[Callable] = None,
    keep_windows: bool = False,
) -> dict[str, Any]:
    from custos.research import criteria_kit as kit  # noqa: PLC0415
    from custos.research import plan_rules_replay as prr  # noqa: PLC0415
    from custos.research import provenance as pv  # noqa: PLC0415

    mining_segs, segments, groups, regime = prr._scan_prepare(args, warm_fn)
    feature_fn = feature_fn or default_feature_fn()
    sector_fn = sector_fn or default_sector_fn()
    osc_fn = osc_fn or default_osc_fn()
    wins: dict[str, dict[str, Any]] = {}
    for (name, _s, _e), g in zip(mining_segs, groups):
        g = {**g, "name": name}
        trades, _ = prr._replay_width(
            [g], prr._no_stop, prr.LIVE_PARAMS, regime, args.cost_bps, replay_fn
        )
        wins[name] = build_window(g, trades, feature_fn, sector_fn, osc_fn)
    if not all(w["trades_n"] for w in wins.values()):
        raise RuntimeError("空结果护栏：某挖掘窗 0 笔已实现交易——不落盘")
    real = {w: _all_stats(win) for w, win in wins.items()}

    def _arm(i: int) -> Optional[float]:
        rng = random.Random(f"{args.seed}-t{i}")
        per = {
            w: _all_stats(win, cluster_perm(win["items"], rng))
            for w, win in wins.items()
        }
        vals = [v for f in FEATURES if (v := _min_lift(per, f)) is not None]
        return max(vals) if vals else None

    crits = {f: judge(real, f) for f in FEATURES}
    _dec = [
        kit.verdict_without_c4(
            c1_ok=c["C1"]["ok"], c2_ok=c["C2"]["ok"], c3_ok=c["C3"]["ok"]
        )
        for c in crits.values()
    ]
    pool_blk = kit.assemble_c4_pool_or_skip(
        args.n_random,
        _arm,
        skip_reason=(
            f"全部 {len(FEATURES)} 个特征的 C1~C3 已定结局"
            if all(d is not None for d in _dec)
            else None
        ),
        enabled=not getattr(args, "no_early_stop", False),
    )
    pool = pool_blk["pool"]
    feats: dict[str, Any] = {}
    for f in FEATURES:
        crit = crits[f]
        stat = crit["C2"]["min_lift_big"]
        st = kit.c4_state_of(pool, min_pool=args.c4_min_pool, plan_delta=stat)
        crit["C4"] = {"state": st, "stat": stat}
        feats[f] = {
            "verdict": kit.verdict_four_state(
                c1_ok=crit["C1"]["ok"],
                c2_ok=crit["C2"]["ok"],
                c3_ok=crit["C3"]["ok"],
                c4_state=st,
            ),
            "criteria": crit,
            "windows": {w: real[w][f] for w in real},
        }
    desc = {w: descriptive(win, feats, w) for w, win in wins.items()}
    rep: dict[str, Any] = {
        "schema": "tail_feature_study/v1",
        "unit": "R48",
        "tag": args.tag,
        "verdict": "candidate"
        if any(v["verdict"] == "candidate" for v in feats.values())
        else "no_candidate",
        "candidates": [f for f, v in feats.items() if v["verdict"] == "candidate"],
        "windows": {name: list(seg["span"]) for name, seg in segments.items()},
        "n_trades": {w: win["trades_n"] for w, win in wins.items()},
        "base_rates": {
            w: {"p_big": float(win["big"].mean()), "p_loss": float(win["loss"].mean())}
            for w, win in wins.items()
        },
        "features": feats,
        "descriptive": desc,
        "c4_pool": {
            "q95": kit.q95(pool),
            "pool": pool,
            **{
                k: pool_blk[k]
                for k in ("evaluated", "gate_pass_rate", "target_pool", "early_stop")
            },
        },
        "window_usage": {
            "window": "not_read",
            "k": None,
            "note": "只读挖掘 A∪B——判定段不读、批次门不触发",
        },
        "provenance": pv.build(
            args,
            unit="R48",
            criteria_version="v0.443",
            pre_reg_doc=RESEARCH_DIR / "R48_tail_entry_features.md",
        ),
    }
    if keep_windows:
        rep["_wins"] = wins  # R49 探索复用（不落盘）
    return rep


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="R48 B1 大单入场特征（11 特征逐个检验，只读挖掘段）"
    )
    ap.add_argument("--codes", default="")
    ap.add_argument("--codes-file", default="")
    ap.add_argument("--universe-sample", type=int, default=0)
    ap.add_argument("--universe-local", action="store_true")
    ap.add_argument("--universe-seed", type=int, default=42)
    ap.add_argument(
        "--count",
        type=int,
        default=None,
        help="每股回溯 K 线根数（缺省按挖掘 A 起点推算）",
    )
    ap.add_argument("--cost-bps", type=float, default=25.0)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--n-random", type=int, default=N_RANDOM, help="簇级置换臂数")
    ap.add_argument(
        "--no-early-stop",
        action="store_true",
        help="v0.466：结局已与随机臂池无关时仍跑满臂（默认跳过——结局逐位不变，只省臂阶段）",
    )
    ap.add_argument("--c4-min-pool", type=int, default=C4_MIN_POOL)
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument(
        "--load-workers",
        type=int,
        default=1,
        help="warm 加载并行路数（v0.441，TODO #112：逐股 加载+扫描+打分 线程并行，"
        "产物与串行逐位一致；默认 1=原行为）",
    )
    ap.add_argument(
        "--score-workers",
        type=int,
        default=1,
        help="as-of V0 分批量打分进程数（v0.447 score_pool：多进程 + 跨研究落盘缓存，读数逐位不变）",
    )
    ap.add_argument("--tag", default="r48_v1")
    ap.add_argument("--out-dir", default=str(LOGS / "tail_feature"))
    return ap


def main(argv: Optional[list[str]] = None, **inj: Any) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _build_parser().parse_args(argv)
    args.cmdline = " ".join(sys.argv if argv is None else ["tail_feature_study", *argv])
    if not inj.get("warm_fn"):
        from custos.research import load_window as lw  # noqa: PLC0415
        from custos.research.exit_c5_terminal import check_reach  # noqa: PLC0415

        args.count = lw.resolve_count(args.count, lw.SEG_MINING_A[0])
        check_reach(args.count, lw.SEG_MINING_A[0])
    try:
        rep = run_study(args, **inj)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    out = Path(args.out_dir) / f"_tail_feature__{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str, allow_nan=True),
        encoding="utf-8",
    )
    for f, v in rep["features"].items():
        print(
            f"[R48] {f}: {v['verdict']}  min lift_big={v['criteria']['C2']['min_lift_big']}"
        )
    print(f"[OK] 写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
