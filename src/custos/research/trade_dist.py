# -*- coding: utf-8 -*-
"""交易集分布画像（描述性副读数，v0.421；owner 2026-10-19「各档盈亏分布 / 高盈亏比占比」）。

**不是判据**：只描述同一信号集在不同出场 / 止损规则下的单笔收益分布，供判读「为什么 margin 这样」。
判决永远只看预注册 C1~C4；本模块读数不得事后升格为判据（要用须新立单元预注册）。

口径：
- 只用**已实现**交易（剔 open_end* / *_unfillable，同 ``bt.summarize_realized``）；
- 一律**收益率口径**（ret，含成本）——R 倍数跨止损宽度不可比（R 的分母就是止损距离）；
- 「高盈亏比交易」= 单笔盈利 ≥ k × 本档平均单笔亏损绝对值（k = 2/3/5），按本档自身亏损尺度衡量；
- 配对（传 ``ref``）：按 (code, entry_date) 与参照档逐信号对齐——同一信号换规则后结果怎么变。
"""

from __future__ import annotations

import statistics
from typing import Any, Optional

import numpy as np

QUANTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)
#: 单笔收益分箱（左开右闭，收益率）
BINS: tuple[tuple[str, float, float], ...] = (
    ("≤-15%", -np.inf, -0.15),
    ("-15~-10%", -0.15, -0.10),
    ("-10~-5%", -0.10, -0.05),
    ("-5~0%", -0.05, 0.0),
    ("0~5%", 0.0, 0.05),
    ("5~10%", 0.05, 0.10),
    ("10~20%", 0.10, 0.20),
    ("20~30%", 0.20, 0.30),
    (">30%", 0.30, np.inf),
)
BIG_WIN_K = (2, 3, 5)
TOP_SHARES = (0.01, 0.05, 0.10)


def _r(x: Optional[float], nd: int = 6) -> Optional[float]:
    return None if x is None or x != x else round(float(x), nd)


def _realized(trades: list[dict]) -> list[dict]:
    from custos.research import backtest_factors as bt  # noqa: PLC0415

    return [
        t
        for t in trades
        if isinstance(t.get("ret"), (int, float))
        and not bt.is_unrealized_reason(t.get("reason"))
    ]


def _wl(rets: np.ndarray) -> dict[str, Any]:
    win, loss = rets[rets > 0], rets[rets <= 0]
    aw = float(win.mean()) if len(win) else None
    al = float(loss.mean()) if len(loss) else None
    payoff = aw / abs(al) if aw is not None and al not in (None, 0.0) else None
    wr = len(win) / len(rets) if len(rets) else None
    be = 1.0 / (1.0 + payoff) if payoff is not None else None
    return {
        "n": int(len(rets)),
        "win_rate": _r(wr),
        "avg_win": _r(aw),
        "avg_loss": _r(al),
        "median_win": _r(float(np.median(win))) if len(win) else None,
        "median_loss": _r(float(np.median(loss))) if len(loss) else None,
        "payoff_ratio": _r(payoff),
        "margin": _r(wr - be) if wr is not None and be is not None else None,
        "expectancy": _r(float(rets.mean())) if len(rets) else None,
    }


def describe(trades: list[dict], ref: Optional[list[dict]] = None) -> dict[str, Any]:
    """单档交易集 → 分布画像（键固定；空集 ⇒ ``{"n": 0}``）。"""
    tr = _realized(trades)
    if not tr:
        return {"n": 0}
    rets = np.array([float(t["ret"]) for t in tr])
    base = _wl(rets)
    al = base["avg_loss"]
    out: dict[str, Any] = {
        **base,
        "quantiles": {f"p{q}": _r(float(np.percentile(rets, q))) for q in QUANTILES},
        "buckets": [
            {
                "bin": name,
                "n": int(((rets > lo) & (rets <= hi)).sum()),
                "share": _r(float(((rets > lo) & (rets <= hi)).mean()), 4),
            }
            for name, lo, hi in BINS
        ],
        "big_win_share": {
            f"ge_{k}x_avg_loss": _r(float((rets >= k * abs(al)).mean()), 4)
            if al
            else None
            for k in BIG_WIN_K
        },
        "profit_concentration": _concentration(rets),
        "holding": {
            "avg_win": _r(_mean([t["holding"] for t in tr if t["ret"] > 0])),
            "avg_loss": _r(_mean([t["holding"] for t in tr if t["ret"] <= 0])),
        },
        "by_reason": _by_reason(tr),
        "by_year": _by_year(tr),
    }
    if ref is not None:
        out["paired_vs_ref"] = _paired(tr, _realized(ref))
    return out


def _mean(xs: list[Any]) -> Optional[float]:
    xs = [float(x) for x in xs if isinstance(x, (int, float))]
    return statistics.fmean(xs) if xs else None


def _concentration(rets: np.ndarray) -> dict[str, Any]:
    """利润集中度：前 1%/5%/10% 的赢单贡献了多少毛利；去掉前 1% 笔后的期望（肥尾依赖度）。"""
    win = np.sort(rets[rets > 0])[::-1]
    gross = float(win.sum())
    out: dict[str, Any] = {}
    for s in TOP_SHARES:
        k = max(1, int(round(len(win) * s))) if len(win) else 0
        out[f"top{int(s * 100)}pct_winners_profit_share"] = (
            _r(float(win[:k].sum()) / gross, 4) if gross > 0 else None
        )
    k1 = max(1, int(round(len(rets) * 0.01)))
    rest = np.sort(rets)[::-1][k1:]
    out["expectancy_ex_top1pct_trades"] = _r(float(rest.mean())) if len(rest) else None
    return out


def _by_reason(tr: list[dict]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict]] = {}
    for t in tr:
        groups.setdefault(str(t.get("reason")), []).append(t)
    n = len(tr)
    rows = []
    for reason in sorted(groups, key=lambda r: -len(groups[r])):
        g = groups[reason]
        rs = np.array([float(t["ret"]) for t in g])
        rows.append(
            {
                "reason": reason,
                "n": len(g),
                "share": _r(len(g) / n, 4),
                "avg_ret": _r(float(rs.mean())),
                "win_rate": _r(float((rs > 0).mean()), 4),
                "avg_holding": _r(_mean([t["holding"] for t in g]), 2),
            }
        )
    return rows


def _by_year(tr: list[dict]) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = {}
    for t in tr:
        groups.setdefault(str(t.get("entry_date"))[:4], []).append(float(t["ret"]))
    rows = []
    for y in sorted(groups):
        b = _wl(np.array(groups[y]))
        rows.append(
            {
                "year": y,
                "n": b["n"],
                "win_rate": b["win_rate"],
                "payoff_ratio": b["payoff_ratio"],
                "margin": b["margin"],
                "expectancy": b["expectancy"],
            }
        )
    return rows


def _paired(tr: list[dict], ref: list[dict]) -> dict[str, Any]:
    """逐信号配对（code, entry_date）：同一信号换规则后的结果迁移。"""
    rmap = {(t["code"], t["entry_date"]): t for t in ref}
    pairs = [
        (t, rmap[(t["code"], t["entry_date"])])
        for t in tr
        if (t["code"], t["entry_date"]) in rmap
    ]
    if not pairs:
        return {"n_matched": 0}
    d = np.array([float(a["ret"]) - float(b["ret"]) for a, b in pairs])
    eps = 1e-9

    def _grp(sel: list[tuple[dict, dict]]) -> dict[str, Any]:
        if not sel:
            return {"n": 0}
        a = np.array([float(x["ret"]) for x, _ in sel])
        b = np.array([float(y["ret"]) for _, y in sel])
        return {
            "n": len(sel),
            "avg_ret_here": _r(float(a.mean())),
            "avg_ret_ref": _r(float(b.mean())),
            "share_here_better": _r(float((a > b + eps).mean()), 4),
        }

    def _stopped(
        t: dict,
    ) -> bool:  # 初始止损出场（trail_stop/breakeven_stop 是止盈/保本类，不算）
        return str(t.get("reason")) == "stop"

    return {
        "n_matched": len(pairs),
        "share_identical": _r(float((np.abs(d) <= eps).mean()), 4),
        "share_better": _r(float((d > eps).mean()), 4),
        "share_worse": _r(float((d < -eps).mean()), 4),
        "delta_ret_mean": _r(float(d.mean())),
        "delta_ret_quantiles": {
            f"p{q}": _r(float(np.percentile(d, q))) for q in (5, 25, 50, 75, 95)
        },
        "flip_ref_loss_to_win": int(
            sum(1 for a, b in pairs if b["ret"] <= 0 < a["ret"])
        ),
        "flip_ref_win_to_loss": int(
            sum(1 for a, b in pairs if a["ret"] <= 0 < b["ret"])
        ),
        # 本档止损出场、参照档没止损：被「过早止损」的信号在参照档里结局如何
        "stopped_here_not_ref": _grp(
            [(a, b) for a, b in pairs if _stopped(a) and not _stopped(b)]
        ),
        # 参照档止损、本档没止损：本档「多扛一段」换来了什么
        "stopped_ref_not_here": _grp(
            [(a, b) for a, b in pairs if _stopped(b) and not _stopped(a)]
        ),
    }
