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


class TestLabelUiAndIngestV0502:
    """v0.502：网页标注界面（不重算升级既有产物）+ ingest 汇总。"""

    def _mk(self, tmp_path, mode="sample", rows=None):
        import csv
        import json

        d = tmp_path / "b1_thresholds__t"
        d.mkdir()
        (d / "summary.json").write_text(
            json.dumps({"module": "b1_thresholds", "tag": "t", "mode": mode}),
            encoding="utf-8",
        )
        rows = rows or [
            [1, "patterns.j_low", "000001", "2022-01-05", "hit", "True", ""],
            [
                2,
                "patterns.j_low",
                "000002",
                "2022-01-06",
                "boundary",
                "False",
                "J_LOW_THRESHOLD×1.2",
            ],
            [
                3,
                "patterns.j_low",
                "000003",
                "2022-01-07",
                "boundary",
                "True",
                "J_LOW_THRESHOLD×0.8",
            ],
        ]
        tail = cp.CSV_TAIL[mode]
        with (d / "labels.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cp.CSV_HEAD + tail)
            for r in rows:
                w.writerow([*r, "", ""])
        (d / "gallery.html").write_text(
            "<html><body>"
            + "".join(f"<div><svg>{r[0]}</svg></div>" for r in rows)
            + "</body></html>",
            encoding="utf-8",
        )
        return d

    def test_upgrade_idempotent_and_count_guard(self, tmp_path):
        d = self._mk(tmp_path)
        assert cp.upgrade_gallery(d) == 3
        g1 = (d / "gallery.html").read_text(encoding="utf-8")
        assert cp.upgrade_gallery(d) == 3
        g2 = (d / "gallery.html").read_text(encoding="utf-8")
        assert g1 == g2 and g2.count("const M = ") == 1 and g2.count("data-id='3'") == 1
        (d / "gallery.html").write_text(
            "<html><body><div><svg/></div></body></html>", encoding="utf-8"
        )
        import pytest

        with pytest.raises(RuntimeError, match="不配套"):
            cp.upgrade_gallery(d)

    def _fill(self, d, labs):
        import csv

        with (d / "labels.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        for r, (c, why) in zip(rows[1:], labs):
            r[7], r[8] = c, why
        with (d / "labels.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerows(rows)

    def test_ingest_sample_votes(self, tmp_path):
        d = self._mk(tmp_path)
        self._fill(d, [("对", ""), ("错", "J 13.5 也是深超卖"), ("对", "")])
        o = cp.ingest(d)
        assert o["n_labeled"] == 3 and len(o["wrong"]) == 1
        w = o["wrong"][0]
        assert w["should_hit"] and w["flip"] == [
            "J_LOW_THRESHOLD×1.2"
        ]  # 漏中，×1.2 能修
        assert o["param_votes"]["patterns.j_low｜J_LOW_THRESHOLD×1.2"] == {
            "修正": 1,
            "误伤": 0,
        }
        assert o["param_votes"]["patterns.j_low｜J_LOW_THRESHOLD×0.8"] == {
            "修正": 0,
            "误伤": 1,
        }
        md = (d / "ingest.md").read_text(encoding="utf-8")
        assert "漏中" in md and "J 13.5 也是深超卖" in md

    def test_ingest_review_compares_with_current_hit(self, tmp_path):
        rows = [
            [1, "n.status", "000001", "2026-10-05", "hit", "True", ""],
            [
                2,
                "n.status",
                "000002",
                "2026-10-06",
                "near_miss",
                "False",
                "N_LOOKBACK×1.2",
            ],
        ]
        d = self._mk(tmp_path, mode="review", rows=rows)
        self._fill(d, [("N", "只是小级别回撤，不是 N 字"), ("Y", "")])
        o = cp.ingest(d)
        kinds = sorted((w["id"], w["now_hit"]) for w in o["wrong"])
        assert kinds == [("1", True), ("2", False)]  # 1 误中，2 漏中

    def test_cli_ui_and_ingest(self, tmp_path):
        d = self._mk(tmp_path)
        assert (
            cp.main(["--module", "b1_thresholds", "--mode", "ui", "--dir", str(d)]) == 0
        )
        assert (
            cp.main(["--module", "b1_thresholds", "--mode", "ingest", "--dir", str(d)])
            == 1
        )  # 未填
        self._fill(d, [("对", ""), ("对", ""), ("不确定", "")])
        assert (
            cp.main(["--module", "b1_thresholds", "--mode", "ingest", "--dir", str(d)])
            == 0
        )


class TestTerminalLabelV0503:
    """v0.503：终端逐条标注（--mode label）——每条立刻写回 labels.csv、可退出续标、边界样本优先。"""

    def _mk(self, tmp_path):
        import json

        d = TestLabelUiAndIngestV0502()._mk(tmp_path)
        bars = 30
        cd = {
            "title": "t",
            "dates": [f"2022-01-{i + 1:02d}" for i in range(bars)],
            "open": [10.0 + i * 0.1 for i in range(bars)],
            "high": [10.5 + i * 0.1 for i in range(bars)],
            "low": [9.5 + i * 0.1 for i in range(bars)],
            "close": [10.2 + i * 0.1 for i in range(bars)],
            "volume": [1000.0 + i for i in range(bars)],
            "j": [50.0 - i * 2 for i in range(bars)],
            "dif": [0.0] * bars,
            "dea": [0.0] * bars,
            "hist": [(-1) ** i * 0.1 for i in range(bars)],
        }
        (d / "chart_data.json").write_text(
            json.dumps({str(i): cd for i in (1, 2, 3)}), encoding="utf-8"
        )
        return d

    def _read(self, d):
        import csv

        with (d / "labels.csv").open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.reader(fh))[1:]

    def test_label_resume_and_order(self, tmp_path):
        d = self._mk(tmp_path)
        seen = []
        ans = iter(["2", "J 13.5 也是深超卖", "q"])
        n = cp.label_cli(
            d, color=False, ask=lambda p: next(ans), say=lambda s: seen.append(s)
        )
        rows = self._read(d)
        assert n == 1 and rows[1][7:9] == [
            "错",
            "J 13.5 也是深超卖",
        ]  # 边界样本 #2 排第一
        assert any("#2" in s for s in seen[:3])
        ans = iter(["b", "1", "", "1", "", "q"])  # 续标：只剩未标；b 在首条不越界
        cp.label_cli(d, color=False, ask=lambda p: next(ans), say=lambda s: None)
        rows = self._read(d)
        assert [r[7] for r in rows] == ["对", "错", "对"]

    def test_invalid_key_and_skip(self, tmp_path):
        d = self._mk(tmp_path)
        ans = iter(["x", "s", "3", "", "q"])
        out = []
        cp.label_cli(d, color=False, ask=lambda p: next(ans), say=out.append)
        assert any("无效按键" in s for s in out)
        assert [r[7] for r in self._read(d)] == ["", "", "不确定"]

    def test_missing_chart_data_rejected(self, tmp_path):
        d = TestLabelUiAndIngestV0502()._mk(tmp_path)
        with pytest.raises(RuntimeError, match="chart_data"):
            cp.label_cli(d, ask=lambda p: "q", say=lambda s: None)

    def test_term_chart_flat_series(self):
        cd = {
            k: [1.0] * 5
            for k in (
                "open",
                "high",
                "low",
                "close",
                "volume",
                "j",
                "dif",
                "dea",
                "hist",
            )
        }
        cd.update(title="t", dates=["2022-01-01"] * 5)
        txt = cp.term_chart(cd, color=False)
        assert txt.startswith("t") and "MACD" in txt


def test_term_chart_half_blocks_and_marker():
    """半格版：上涨实体用 █▀▄、影线 │╵╷；末根底部 ▲ 竖标；13 线 ┄；宽度 = 9 + 根数。"""
    n = 30
    cd = {
        "title": "t",
        "dates": [f"2022-01-{i + 1:02d}" for i in range(n)],
        "open": [10.0 + i * 0.1 for i in range(n)],
        "high": [10.6 + i * 0.1 for i in range(n)],
        "low": [9.4 + i * 0.1 for i in range(n)],
        "close": [10.3 + i * 0.1 for i in range(n)],
        "volume": [1000.0 + 10 * i for i in range(n)],
        "j": [60.0 - 3 * i for i in range(n)],
        "dif": [0.05 * (i - 15) for i in range(n)],
        "dea": [0.04 * (i - 15) for i in range(n)],
        "hist": [0.02 * (i - 15) for i in range(n)],
    }
    lines = cp.term_chart(cd, color=False).split("\n")
    body = "\n".join(lines[1:13])
    assert set(body) & set("█▀▄") and set(body) & set("│╵╷")
    assert "┄" in "\n".join(lines) and lines[-2].rstrip().endswith("▲ 当根")
    assert lines[-2].index("▲") == 9 + n - 1
    assert all(len(ln) <= 9 + n + 6 for ln in lines[1:-2])


class TestKLineChartGalleryV0506:
    """v0.506：gallery 改 KLineChart（vendored，sha256 钉死）；资源缺失退回静态 SVG。"""

    def test_vendor_digest_and_license(self):
        import hashlib

        assert hashlib.sha256(cp.KC_JS.read_bytes()).hexdigest() == cp.KC_SHA256
        for f in ("LICENSE", "NOTICE", "LICENSE-lightweight-charts", "README.md"):
            assert (cp.KC_DIR / f).exists()

    def test_section_inlines_lib_and_our_values(self):
        sec = cp.kc_section(
            {"1": {"dates": ["2022-01-03"], "j": [float("nan")], "code": "000001"}}
        )
        assert (
            "klinecharts" in sec and "const KC = " in sec and "registerIndicator" in sec
        )
        assert "'当根'" in sec and "l13: 13" in sec  # 13 线 + 当根标注
        assert (
            "</script" not in sec.split("const KC = ")[1].split("\n")[0]
        )  # 数据段不提前闭合

    def test_missing_asset_falls_back(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cp, "KC_JS", tmp_path / "nope.js")
        assert cp.kc_assets() is None and cp.kc_section({}) == ""
