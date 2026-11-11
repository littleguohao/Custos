# -*- coding: utf-8 -*-
"""v0.513 0AMV 背景信息（1800 报告头部，只描述）：R51 B 段参考数对账 / 口径同研究 / 旁路隔离。"""

from __future__ import annotations

import json
import pathlib
import random

import pandas as pd

from custos.pipeline.market_timing import amv_context as ac

ROOT = pathlib.Path(__file__).resolve().parents[1]
R51_JSON = ROOT / "governance" / "research" / "results" / "R51" / "r51_v1.json"


def _fields():
    return json.loads(R51_JSON.read_text(encoding="utf-8-sig"))["fields"]


def test_reference_numbers_match_r51_results():
    """常量 = R51 挖掘 B 段产物（研究结论变了 ⇒ 这里红 ⇒ 同步更新）。"""
    f = _fields()
    for d in ("up", "down"):
        ref = ac.R51_B[d]
        w = f"windows.mining_b.{d}"
        assert ref["n_flips"] == f[f"{w}.n_flips"]
        for q, v in ref["duration"].items():
            assert v == f[f"{w}.duration"][q]
        for q, v in ref["idx_fav"].items():
            assert v == f[f"{w}.idx"]["fav"][q]
        for q, v in ref["idx_adv"].items():
            assert v == f[f"{w}.idx"]["adv"][q]
        for n, v in ref["rev"].items():
            assert v == f[f"{w}.reversal"][f"rev{n}"]["rate"]
        tiers = f[f"{w}.rev10_by_position"]["idx_pos"]
        for _lo, _hi, name, n, rate in ref["pos_tiers"]:
            assert (n, rate) == (tiers[name]["n"], tiers[name]["rate"]), name
    for d in ("up", "down"):
        amv = f[f"windows.mining_b.{d}.amv"]["fav"]
        for q, v in ac.R51_B[d]["amv_fav"].items():
            assert v == amv[q], (d, q)


def _world(n=600, seed=0):
    rng = random.Random(seed)
    ds = [str(d)[:10] for d in pd.bdate_range("2023-01-02", periods=n)]
    recs = [{"date": d, "chg": round(rng.gauss(0.1, 2.4), 2)} for d in ds]
    px, close = 3000.0, []
    for d in ds:
        px *= 1 + rng.gauss(0.0003, 0.012)
        close.append((d, round(px, 2)))
    return ds, recs, close


def test_replay_matches_research_state_machine():
    from custos.research import backtest_factors as bt

    _ds, recs, _c = _world()
    ref = bt._amv_regime_from_records(
        [{"date": r["date"], "change_pct": r["chg"]} for r in recs]
    )
    assert {r["date"]: r["state"] for r in ac.replay(recs)} == ref


def test_build_matches_r51_event_readings():
    """当前段读数与 R51 flip_events 的同一事件逐值一致（段内已见部分、位置）。"""
    from custos.research import amv_regime_study as ar

    ds, recs, close = _world()
    day = ds[-1]
    ctx = ac.build(day, recs, close)
    assert ctx is not None
    rrec = [{"date": r["date"], "change_pct": r["chg"]} for r in recs]
    evs = ar.flip_events(ar.replay(rrec, "S0", ds), dict(close), ds[0], day)
    last = evs[-1]
    assert last["date"] == ctx["flip_date"] and last["censored"]
    assert abs(last["idx_fav"] - ctx["idx_fav"]) < 1e-3
    assert abs(last["amv_fav"] - ctx["amv_fav"]) < 1e-3
    assert abs(last["idx_pos"] - ctx["flip_pos"]) < 1e-3
    assert ctx["day_k"] == last["seen_days"]


def test_render_lines_and_no_action_words():
    ds, recs, close = _world()
    ctx = ac.build(ds[-1], recs, close)
    lines = ac.render(ctx, ctx["state"])
    txt = "\n".join(lines)
    assert "只描述、不进判定" in txt and f"第 **{ctx['day_k']}** 天" in txt
    for bad in ("买入", "卖出", "加仓", "减仓", "清仓", "建议"):
        assert bad not in txt
    assert all(x.startswith(">") for x in lines)
    mism = ac.render(ctx, "空头" if ctx["state"] == "做多" else "做多")
    assert any("不一致" in x for x in mism)


def test_small_tier_does_not_report_rate():
    ctx = {
        "state": "做多",
        "dir": "up",
        "flip_date": "2026-01-05",
        "day_k": 3,
        "ledger_last": "2026-01-07",
        "amv_since": 5.0,
        "amv_fav": 6.0,
        "idx_since": 1.0,
        "idx_fav": 1.5,
        "flip_pos": 25.0,
    }
    txt = "\n".join(ac.render(ctx, "做多"))
    assert "≥20% 档" in txt and "n=1，不足 5 不报比例" in txt


def test_bypass_isolation_never_raises():
    def boom():
        raise OSError("vipdoc gone")

    out = ac.context_lines(
        "2026-11-11", "做多", ledger_lines=lambda: [], index_close=boom
    )
    assert len(out) == 1 and "生成失败" in out[0]
    out = ac.context_lines(
        "2026-11-11", "做多", ledger_lines=lambda: [], index_close=lambda: []
    )
    assert "未生成" in out[0]


def test_ledger_confirmed_rules():
    lines = [
        json.dumps(
            {"date": "2026-01-05", "amv_change_pct": 5.0, "quality": "candidate"}
        ),
        json.dumps(
            {
                "date": "2026-01-05",
                "amv_change_pct": 4.5,
                "quality": "confirmed",
                "recorded_at": "a",
            }
        ),
        json.dumps(
            {
                "date": "2026-01-05",
                "amv_change_pct": 3.0,
                "quality": "confirmed",
                "recorded_at": "b",
            }
        ),
        json.dumps(
            {
                "date": "2026-01-06",
                "amv_change_pct": 9.0,
                "quality": "confirmed",
                "superseded": True,
            }
        ),
        "{bad json",
    ]
    assert ac.ledger_confirmed(lines) == [{"date": "2026-01-05", "chg": 3.0}]


def test_wired_into_1800_header_not_into_decisions():
    import inspect

    from custos.pipeline.close_review import final_close_review as fcr

    src = inspect.getsource(fcr.main)
    assert "amv_context.context_lines(" in src
    # 只在报告行里出现，不参与任何判定函数
    for fn in (fcr._buy_point_hits, fcr._habit_hits, fcr.build_payload):
        assert "amv_context" not in inspect.getsource(fn)
