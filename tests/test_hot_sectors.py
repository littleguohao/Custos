# -*- coding: utf-8 -*-
"""v0.529 热门细分行业（TODO #125）：近 10 日进当日涨幅前十的次数排序；旁路隔离。"""

from __future__ import annotations

from custos.pipeline.screening import hot_sectors as hs


def _world():
    dates = [f"2026-11-{d:02d}" for d in range(1, 13)]
    inds = {f"I{k:02d}": [f"{k:02d}{m:04d}" for m in range(6)] for k in range(15)}
    stock_ind = {c: ind for ind, cs in inds.items() for c in cs}
    daily: dict[str, dict[str, float]] = {}
    for ind, cs in inds.items():
        k = int(ind[1:])
        for c in cs:
            # I00 天天最强；I14 只在最后两天进前十；其余按序号递减
            daily[c] = {
                d: (20.0 - k if not (k == 14 and d >= "2026-11-11") else 30.0)
                for d in dates
            }
    return dates, stock_ind, daily


def test_rank_counts_top10_hits_and_sorts_desc():
    dates, stock_ind, daily = _world()
    res = hs.rank_hot(daily, stock_ind, dates[-10:])
    hits = {h["industry"]: h["hits"] for h in res["hot"]}
    assert hits["I00"] == 10 and hits["I14"] == 2 and "I13" not in hits
    assert [h["hits"] for h in res["hot"]] == sorted(
        (h["hits"] for h in res["hot"]), reverse=True
    )
    assert next(h for h in res["hot"] if h["industry"] == "I14")["last_rank"] == 1


def test_min_members_excludes_tiny_industries():
    dates, stock_ind, daily = _world()
    stock_ind["999999"] = "TINY"
    daily["999999"] = {d: 99.0 for d in dates}
    res = hs.rank_hot(daily, stock_ind, dates[-10:])
    assert "TINY" not in {h["industry"] for h in res["hot"]}


def test_hot_for_window_and_bypass():
    dates, stock_ind, daily = _world()
    res = hs.hot_for(
        "2026-11-12",
        stock_industry=stock_ind,
        loader=lambda codes, n: daily,
        index_dates=dates,
    )
    assert res["dates"] == dates[-10:]

    def boom(codes, n):
        raise OSError("vipdoc gone")

    out = hs.hot_for(
        "2026-11-12", stock_industry=stock_ind, loader=boom, index_dates=dates
    )
    assert out["hot"] == [] and out["error"] == "OSError"
    assert "无数据" in "\n".join(hs.render_lines(out))
