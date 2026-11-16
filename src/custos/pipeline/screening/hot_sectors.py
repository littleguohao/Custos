# -*- coding: utf-8 -*-
"""热门细分行业（v0.529，owner 2026-11-16，TODO #125）：只作 1800 候选表标注，不进任何判定。

口径（owner 定）：
- 板块统一用通达信「细分行业」（881xxx，``enrich_candidates.build_stock_industry_map`` 同源：每股恰好一个）；
- 每个交易日按细分行业**成员等权平均涨幅**排名（成员当日收盘 / 前收 − 1，前复权相邻比 = 真实涨跌；
  停牌无当日 K 线的成员不计；当日有效成员 < ``MIN_MEMBERS`` 的行业不参与排名）；
- 「最活跃」= 近 ``WINDOW`` 个交易日里进入当日涨幅前 ``TOP_N`` 的次数，按次数从高到低排序（同次数按最近一日排名）。
  只列次数 ≥ 1 的行业。

交易日历取上证指数（999999）末 ``WINDOW`` 根。数据全部本地（vipdoc），失败一律降级为空结果、不打死候选表。
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Optional

WINDOW = 10
TOP_N = 10
MIN_MEMBERS = 5
INDEX_CODE = "999999"


def rank_hot(
    daily: dict[str, dict[str, float]],
    stock_industry: dict[str, str],
    dates: list[str],
    *,
    top_n: int = TOP_N,
    min_members: int = MIN_MEMBERS,
) -> dict[str, Any]:
    """纯函数。``daily = {code: {date: pct}}``；返回 ``{dates, hot: [{industry, hits, days, last_rank}]}``。"""
    hits: dict[str, list[str]] = {}
    last_rank: dict[str, int] = {}
    for d in dates:
        acc: dict[str, list[float]] = {}
        for code, ind in stock_industry.items():
            v = (daily.get(code) or {}).get(d)
            if v is not None and v == v:
                acc.setdefault(ind, []).append(v)
        means = sorted(
            ((sum(v) / len(v), ind) for ind, v in acc.items() if len(v) >= min_members),
            key=lambda t: (-t[0], t[1]),
        )
        for r, (_m, ind) in enumerate(means[:top_n], 1):
            hits.setdefault(ind, []).append(d)
            if d == dates[-1]:
                last_rank[ind] = r
    order = sorted(
        hits, key=lambda ind: (-len(hits[ind]), last_rank.get(ind, 999), ind)
    )
    hot = [
        {
            "industry": ind,
            "hits": len(hits[ind]),
            "days": hits[ind],
            "last_rank": last_rank.get(ind),
        }
        for ind in order
    ]
    return {"dates": dates, "window": len(dates), "top_n": top_n, "hot": hot}


def _load_daily(codes: list[str], n: int) -> dict[str, dict[str, float]]:
    from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

    out: dict[str, dict[str, float]] = {}
    for code in codes:
        try:
            df = local_tdx_data.get_ohlcv_table(code, count=n + 1)
        except Exception:  # noqa: BLE001 — 单票缺数据跳过
            continue
        if df is None or len(df) < 2:
            continue
        c = df["close"].astype(float).to_numpy()
        ds = [str(x)[:10] for x in df["date"]]
        out[code] = {
            ds[k]: (c[k] / c[k - 1] - 1) * 100
            for k in range(1, len(ds))
            if c[k - 1] > 0
        }
    return out


def hot_for(
    date: str,
    *,
    stock_industry: Optional[dict[str, str]] = None,
    loader: Optional[Callable[[list[str], int], dict[str, dict[str, float]]]] = None,
    index_dates: Optional[list[str]] = None,
) -> dict[str, Any]:
    """截至 ``date`` 的热门细分行业。任何异常 ⇒ ``{"hot": [], "error": ...}``（旁路隔离）。"""
    try:
        if stock_industry is None:
            from custos.pipeline.screening.enrich_candidates import (  # noqa: PLC0415
                build_stock_industry_map,
            )

            stock_industry = build_stock_industry_map()
        if not stock_industry:
            return {"hot": [], "error": "细分行业映射缺失"}
        if index_dates is None:
            idx = _load_daily([INDEX_CODE], WINDOW + 5).get(INDEX_CODE) or {}
            index_dates = sorted(idx)
        dates = [d for d in sorted(index_dates) if d <= date][-WINDOW:]
        if not dates:
            return {"hot": [], "error": "交易日历缺失"}
        daily = (loader or _load_daily)(sorted(stock_industry), WINDOW + 1)
        return rank_hot(daily, stock_industry, dates)
    except Exception as exc:  # noqa: BLE001 — 标注旁路，不影响候选表
        print(
            f"[WARN] 热门细分行业计算失败：{type(exc).__name__}: {exc}", file=sys.stderr
        )
        return {"hot": [], "error": f"{type(exc).__name__}"}


def render_lines(res: dict[str, Any], limit: int = 20) -> list[str]:
    hot = res.get("hot") or []
    lines = [
        f"## 🔥 近 {res.get('window', WINDOW)} 个交易日热门细分行业（进当日涨幅前 {res.get('top_n', TOP_N)} 次数）",
        "",
    ]
    if not hot:
        lines += [f"（无数据{('：' + res['error']) if res.get('error') else ''}）", ""]
        return lines
    lines += ["| 细分行业 | 上榜次数 | 最近一日排名 |", "|---|---:|---:|"]
    for h in hot[:limit]:
        lines.append(f"| {h['industry']} | {h['hits']} | {h['last_rank'] or '-'} |")
    lines += [
        "",
        "> 只作标注，不进判定；成员等权平均涨幅排名，有效成员 <5 的行业不参与。",
        "",
    ]
    return lines
