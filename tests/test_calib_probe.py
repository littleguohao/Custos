"""calib_probe（TODO #121 定义校准探针）：参数改动覆盖绑定与默认参数、取值口径、抽样纪律、产物。"""

import argparse
import random
import numpy as np
import pandas as pd
import pytest

from custos.core import b1_thresholds as bth
from custos.core.factors import weekly_j
from custos.research import calib_probe as cp


def _df(seed=0, n=700, start="2014-01-02"):
    r = np.random.RandomState(seed)
    d = pd.bdate_range(start, periods=n)
    c = 10 * np.exp(np.cumsum(r.normal(0, 0.02, n)))
    return pd.DataFrame(
        {
            "date": d,
            "open": c * (1 + r.normal(0, 0.005, n)),
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": r.uniform(5e5, 2e6, n),
            "amount": c * 1e6,
        }
    )


def test_patch_reaches_bindings_and_defaults_then_restores():
    orig = bth.J_LOW_THRESHOLD
    assert weekly_j.j_below_threshold(12.0) is True
    with cp.patched("custos.core.b1_thresholds", {"J_LOW_THRESHOLD": 5.0}) as n:
        assert n >= 2 and bth.J_LOW_THRESHOLD == 5.0
        assert weekly_j.J_LOW_THRESHOLD == 5.0  # from-import 绑定
        assert weekly_j.j_below_threshold(12.0) is False  # 默认参数也改到
    assert bth.J_LOW_THRESHOLD == orig and weekly_j.j_below_threshold(12.0) is True


def test_field_value_and_hit_rules():
    cand = {
        "patterns": {"j_low": True},
        "wave": {"wave_type": "unknown"},
        "r": {"signals": ["b", "a"]},
    }
    assert cp.field_value(cand, "patterns.j_low") is True
    assert cp.field_value(cand, "r.signals") == "a、b"
    assert cp.field_value(cand, "missing.x") is None
    assert not cp.is_hit("unknown") and not cp.is_hit("none") and not cp.is_hit(0)
    assert cp.is_hit("sprint") and cp.is_hit(2) and cp.is_hit(True)


def test_bar_pool_respects_spans_and_history():
    bars = {"000001|2014": _df()}
    spans = [("2015-01-01", "2015-12-31")]
    pool = cp.bar_pool(bars, spans, 10_000, random.Random(0), jlow_only=False)
    ds = bars["000001|2014"]["date"].astype(str).str[:10]
    assert pool and all(
        "2015-01-01" <= ds[i] <= "2015-12-31" and i >= 260 for _c, i in pool
    )
    jl = cp.bar_pool(bars, spans, 10_000, random.Random(0), jlow_only=True)
    assert jl and set(jl) < set(pool)


def test_svg_chart_only_until_asof():
    df = _df()
    svg = cp.svg_chart(df.iloc[:400], "t")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert (
        str(df["date"].iloc[399])[:10] in svg
        and str(df["date"].iloc[400])[:10] not in svg
    )


def _args(mode, **kw):
    base = dict(
        module="b1_thresholds",
        mode=mode,
        pool="all",
        n_bars=24,
        n_show=2,
        steps=[0.5, 1.5],
        workers=2,
        seed=1,
        tag="t",
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.parametrize("mode", ["sample", "sensitivity"])
def test_run_and_write(mode, tmp_path):
    bars = {"000001|2014": _df(1), "000002|2014": _df(2)}
    idx = _df(9)
    rep = cp.run_study(
        _args(mode),
        bars=bars,
        index_df=idx,
        spans=[("2015-01-01", "2016-12-31")],
    )
    assert rep["n_bars"] == 24 and rep["n_errors"] == 0
    assert set(rep["fields"]) == set(cp.MODULES["b1_thresholds"]["fields"])
    # REVERSAL_CHANGE_PCT 只在导入时派生 MIN/MAX ⇒ 改它改不到判定，必须被标 no_effect
    assert "REVERSAL_CHANGE_PCT" in rep["no_effect_params"]
    d = cp.write_outputs(rep, tmp_path)
    if mode == "sample":
        assert (d / "gallery.html").exists() and (d / "labels.csv").read_text(
            encoding="utf-8-sig"
        ).startswith("id,字段")
    else:
        md = (d / "sensitivity.md").read_text(encoding="utf-8")
        assert "J_LOW_THRESHOLD" in md and "no_effect" in md


def test_empty_pool_guard():
    with pytest.raises(RuntimeError, match="空结果护栏"):
        cp.run_study(
            _args("sample"),
            bars={"000001|2014": _df()},
            index_df=_df(9),
            spans=[("2030-01-01", "2030-12-31")],
        )


def test_every_module_params_resolve():
    for m in cp.MODULES:
        assert cp.param_names(m), m


def test_process_pool_equals_serial():
    bars = {"000001|2014": _df(1), "000002|2014": _df(2)}
    kw = dict(bars=bars, index_df=_df(9), spans=[("2015-01-01", "2016-12-31")])
    a = cp.run_study(_args("sensitivity", workers=1), **kw)
    b = cp.run_study(_args("sensitivity", workers=2), **kw)
    assert (
        a["sensitivity"] == b["sensitivity"]
        and a["no_effect_params"] == b["no_effect_params"]
    )


# ── review 模式（近月 1800 选股池）与新旧版本差异 ─────────────────────────


def test_review_pool_reads_stock_pool_dates(tmp_path):
    import json

    df = _df(3, start="2025-01-02")
    d1, d2 = str(df["date"].iloc[400])[:10], str(df["date"].iloc[450])[:10]
    for d, codes in (
        (d1, ["600001"]),
        (d2, ["600001", "600002"]),
        ("2024-01-02", ["600001"]),
    ):
        (tmp_path / f"{d}_stock_pool.json").write_text(
            json.dumps({"candidates": [{"code": c} for c in codes]}), encoding="utf-8"
        )
    args = argparse.Namespace(since=d1, until=d2, count=100000)
    bars, idx, keys = cp.review_pool(
        args,
        pool_files=sorted(tmp_path.glob("*.json")),
        load_fn=lambda c: None if c == "600002" else df,
        index_df=_df(9),
    )
    assert keys == [
        ("600001|full", 400),
        ("600001|full", 450),
    ]  # 无数据的票跳过；窗外日期不取


def test_review_rows_and_diff(tmp_path):
    bars = {"000001|full": _df(1)}
    since, until = (
        str(bars["000001|full"]["date"].iloc[300])[:10],
        str(bars["000001|full"]["date"].iloc[340])[:10],
    )
    a = _args("review", since=since, until=until, pool="all")
    rep = cp.run_study(a, bars=bars, index_df=_df(9))
    assert rep["n_bars"] == 41 and rep["factor_version"] == 1
    rows = rep["samples"]["patterns.j_low"]
    assert {r["class"] for r in rows} <= {"hit", "near_miss"}
    assert sum(r["class"] == "hit" for r in rows) == sum(
        bool(v["patterns.j_low"]) for v in rep["values"]
    )
    d = cp.write_outputs(dict(rep), tmp_path)
    assert (
        (d / "labels.csv")
        .read_text(encoding="utf-8-sig")
        .splitlines()[0]
        .endswith("形态理由（写形态，不写后来涨跌）")
    )
    old = __import__("json").loads((d / "summary.json").read_text(encoding="utf-8"))
    new = __import__("copy").deepcopy(old)
    new["factor_version"] = 2
    flip = next(v for v in new["values"] if v["patterns.j_low"])
    flip["patterns.j_low"] = False
    diff = cp.diff_reports(old, new)
    assert diff["fields"]["patterns.j_low"]["removed"] == [
        (flip["key"][0].split("|")[0], flip["date"])
    ]
    md = cp._diff_md(diff, "b1_thresholds")
    assert "v1 → v2" in md and "消失 1" in md


# ── P0H 持仓大形态（v0.485） ──────────────────────────────────────────


def test_holding_modules_patch_live_function_defaults():
    from custos.pipeline.market_timing import technical_monitor as tm

    df = _df(5, n=500)
    with cp.patched(
        "custos.pipeline.market_timing.technical_monitor",
        {"N_LOOKBACK": 30, "BOX_SHORT": 10},
    ):
        an = tm.analyze(df, "600000")
        assert (an["n_structure"].get("pivot_window") or {}).get("lookback", 30) == 30
        assert an["box_20d"]["period"] == 10
    assert tm.analyze(df, "600000")["box_20d"]["period"] == 20


@pytest.mark.parametrize("module", ["n_structure", "trend_box"])
def test_holding_modules_run(module):
    rep = cp.run_study(
        _args("sensitivity", module=module, n_bars=12),
        bars={"000001|2014": _df(1)},
        index_df=_df(9),
        spans=[("2015-01-01", "2016-12-31")],
    )
    assert rep["n_errors"] == 0 and set(rep["fields"]) == set(
        cp.MODULES[module]["fields"]
    )


def test_review_pool_holdings_rows(tmp_path):
    import json

    df = _df(3, start="2025-01-02")
    d1 = str(df["date"].iloc[400])[:10]
    (tmp_path / f"{d1}_holding_technical_summary.json").write_text(
        json.dumps([{"code": "600001.SH"}, {"name": "无代码"}]), encoding="utf-8"
    )
    args = argparse.Namespace(
        since=d1, until=d1, count=100000, review_source="holdings"
    )
    _b, _i, keys = cp.review_pool(
        args,
        pool_files=[*tmp_path.glob("*.json")],
        load_fn=lambda c: df,
        index_df=_df(9),
    )
    assert keys == [("600001|full", 400)]
