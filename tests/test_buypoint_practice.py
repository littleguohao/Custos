# -*- coding: utf-8 -*-
"""v0.517 买点练习 + 批语规则单一来源。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from custos.research import buypoint_practice as bp
from custos.research import calib_probe as cp
from custos.research import remark_rules as rr


def _df(n=400, seed=0, start="2022-01-03"):
    rng = np.random.default_rng(seed)
    c = 10 * np.cumprod(1 + rng.normal(0, 0.02, n))
    o = c * (1 + rng.normal(0, 0.01, n))
    return pd.DataFrame(
        {
            "date": [str(d)[:10] for d in pd.bdate_range(start, periods=n)],
            "open": o,
            "high": np.maximum(o, c) * 1.01,
            "low": np.minimum(o, c) * 0.99,
            "close": c,
            "volume": rng.lognormal(13, 0.4, n),
        }
    )


def test_tags_and_zero_definition_relative_to_macd_swing():
    assert rr.tags_of("macd刚死叉，等待绿柱缩短") == ["s_dead_cross", "w_green_shrink"]
    assert "w_zero" in rr.tags_of("等待macd 双线回到0轴附近")
    a = rr.arrays(_df())
    j = 300
    amp = rr.zero_amp(a, j)
    assert rr.c_zero(a, j) == (
        abs(a["dif"][j]) <= 0.2 * amp and abs(a["dea"][j]) <= 0.2 * amp
    )
    # 与价格无关：整体放大 10 倍（价格 ×10）结论不变
    big = _df()
    for k in ("open", "high", "low", "close"):
        big[k] = big[k] * 10
    b = rr.arrays(big)
    assert [rr.c_zero(a, k) for k in range(150, 390)] == [
        rr.c_zero(b, k) for k in range(150, 390)
    ]


def test_trigger_reads_only_past_and_forward_respects_limit():
    df = _df()
    a = rr.arrays(df)
    i = 200
    j = rr.first_trigger(a, i, rr.c_fractal, limit=len(df) - 1)
    if j is not None:
        b = rr.arrays(df.iloc[: j + 1])
        assert rr.c_fractal(b, j)  # 只用 ≤j 的数据就能判出
    assert rr.forward(a, len(df) - 5, len(df) - 1) is None


def test_b1_points_inside_segment_with_forward_room():
    df = _df(600, 1, "2022-01-03")
    regime = {d: "做多" for d in df["date"]}
    pts = bp.b1_points(df, regime, "mining_b")
    ds = df["date"].tolist()
    assert pts and all(
        ds[i] >= "2022-01-01" and i >= 120 and i + 20 < len(df) for i in pts
    )
    assert bp.b1_points(df, {}, "mining_b") == []  # 非做多不出题


def test_review_rules_trigger_abandon_accuracy():
    df = _df(500, 2)
    loader = lambda code, seg: df  # noqa: E731
    a = rr.arrays(df)
    rows = []
    for k, i in enumerate(range(150, 450, 10), 1):
        judge, note = (
            ("买", "可以试试")
            if k % 3 == 0
            else (
                ("等", "等待底分型和双线回到0轴附近")
                if k % 3 == 1
                else ("不买", "放量下跌")
            )
        )
        rows.append(
            {
                "id": str(k),
                "段": "mining_b",
                "代码": "600001",
                "日期": df["date"][i],
                bp.JUDGE: judge,
                bp.NOTE: note,
            }
        )
    rows.append(
        {
            "id": "99",
            "段": "mining_b",
            "代码": "600001",
            "日期": df["date"][200],
            bp.JUDGE: "",
            bp.NOTE: "",
        }
    )
    rep = bp.review_rows(rows, loader)
    s = rep["summary"]
    assert s["n"] == 30  # 空判断不计
    w = s["wait"]["w_fractal"]
    assert w["triggered"] + w["abandoned"] == w["n"]
    for p in rep["per"]:
        if "w_zero" in p["wait"] and p["wait"]["w_zero"]["day"] is None and p["now"]:
            i = df["date"].tolist().index(p["date"])
            assert all(not rr.c_zero(a, j) for j in range(i + 1, i + 21))
    buy = [p for p in rep["per"] if p["judge"] == "买" and p["now"]]
    assert s["accuracy"]["buy_up"] == [sum(p["now"]["r20"] > 0 for p in buy), len(buy)]
    md = bp.render_md(rep)
    assert "## ③ 等待条件" in md and "放弃正确" in md


def test_label_ui_buypoint_columns():
    ui = cp.label_ui(
        [[1, "mining_b", "600001", "2024-01-02", 5.0, "做多", 9.9]],
        "buypoint",
        "k",
        head=bp.HEAD,
    )
    assert '"买", "不买", "等"' in ui and "判断(买/不买/等)" in ui and "批语" in ui


def test_review_empty_guard(tmp_path):
    (tmp_path / "labels.csv").write_text(
        "\ufeff" + ",".join(bp.HEAD + [bp.JUDGE, bp.NOTE]) + "\n", encoding="utf-8"
    )
    assert (
        bp.main(["--mode", "review", "--dir", str(tmp_path)], loader=lambda c, s: _df())
        == 2
    )
    assert not (tmp_path / "review.md").exists()
