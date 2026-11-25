"""calib_probe（TODO #121 定义校准探针）：参数改动覆盖绑定与默认参数、取值口径、抽样纪律、产物。"""

import argparse
import random
import numpy as np
import pandas as pd
import pytest

from custos.core import b1_thresholds as bth
from custos.core.factors import weekly_j
from custos.research import calib_probe as cp


def test_weekly_j_excluded_from_definition_calibration():
    """owner 2026-11-14：周 J<13 是纯数值比较，不再做人工定义校准。"""
    assert "weekly_j_low" not in cp.MODULES["b1_thresholds"]["fields"]


def test_numeric_fields_and_revoked_list_excluded_from_calibration():
    """BBI / 相对强度是纯计算；revoked 空名单不是普通 miss。"""
    assert "entry_patterns" not in cp.MODULES
    assert cp.MODULES["distribution"]["fields"] == ["distribution.risk_level"]
    prompt = cp.ask_html("distribution.risk_level", "hit", "watch")
    assert "none=无命中" in prompt and "high=命中①/②任一或同时 ≥2 腿" in prompt
    assert "名单为空表示没有失效的信号" in cp.FIELD_DEF["distribution_confirm.revoked"]
    assert "10 项" in cp.FIELD_DEF["@capital_intent.level"]


def test_simple_numeric_fields_and_their_sensitivity_params_removed():
    """owner 2026-11-15：只保留复合形态；纯数值字段及其敏感性参数退出。"""
    expected = {
        "trend_box": ["trend.state"],
        "b1_thresholds": ["patterns.reversal_k_candidate"],
        "b1_structure": [
            "non_one_wave.status",
            "repair_signals.signals",
            "five_day_entry.hit",
        ],
        "volume_detectors": [
            "volume_sustain.status",
            "bottom_volume.hit",
        ],
        "macd_technics": [
            "macd_technics.bottom_divergence.hit",
            "macd_technics.top_divergence.hit",
            "macd_technics.three_peaks.hit",
        ],
    }
    for module, fields in expected.items():
        assert cp.MODULES[module]["fields"] == fields
    assert not set(cp.param_names("trend_box")) & {
        "BOX_UPPER_Q",
        "BOX_LOWER_Q",
        "BOX_SHORT",
        "BOX_LONG",
    }
    assert not set(cp.param_names("b1_structure")) & {
        "STOP_LOOKBACK",
        "LIQUIDITY_WIN",
    }
    assert not set(cp.param_names("volume_detectors")) & {
        "LEADER_VOL_BASE_DAYS",
        "LEADER_VOL_RATIO",
    }
    assert "macd_technics.overextended.hit" not in cp.MODULES["macd_technics"]["fields"]


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


def test_trend_shape_wave_type_forwards_code(monkeypatch):
    """v0.597：校准图集的涨停口径也按股票代码前缀，不能默认全按主板 10%。"""
    from custos.core.factors import b1_structure as bsm
    from custos.core.factors import distribution as dm
    from custos.core.factors import wave_type as wt
    from custos.pipeline.market_timing import technical_monitor as tm
    from custos.research import swing_state as sws

    seen = {}
    monkeypatch.setattr(bsm, "check_one_wave_v3", lambda df: {})
    monkeypatch.setattr(bsm, "check_one_wave_v4", lambda df: {})
    monkeypatch.setattr(tm, "analyze", lambda df, code: {})
    monkeypatch.setattr(dm, "detect_distribution", lambda df, code: {})
    monkeypatch.setattr(sws, "swing_state", lambda df: {"state": None})

    def fake_wave(df, code=""):
        seen["code"] = code
        return {"wave_type": "unknown"}

    monkeypatch.setattr(wt, "detect_wave_type", fake_wave)
    cp.trend_shape_readings((pd.DataFrame(), pd.DataFrame(), None, "300750"))
    assert seen["code"] == "300750"


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
        if cp.MODULES[m].get("params") == []:  # 显式无参（v0.548 trend_shape）
            continue
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
    rows = rep["samples"]["patterns.reversal_k_candidate"]
    assert {r["class"] for r in rows} <= {"hit", "near_miss"}
    assert sum(r["class"] == "hit" for r in rows) == sum(
        bool(v["patterns.reversal_k_candidate"]) for v in rep["values"]
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
    flip = next(v for v in new["values"] if not v["patterns.reversal_k_candidate"])
    flip["patterns.reversal_k_candidate"] = True
    diff = cp.diff_reports(old, new)
    assert diff["fields"]["patterns.reversal_k_candidate"]["added"] == [
        (flip["key"][0].split("|")[0], flip["date"])
    ]
    md = cp._diff_md(diff, "b1_thresholds")
    assert "v1 → v2" in md and "新增 1" in md


# ── P0H 持仓大形态（v0.485） ──────────────────────────────────────────


def test_holding_modules_patch_live_function_defaults():
    from custos.pipeline.market_timing import technical_monitor as tm

    df = _df(5, n=500)
    with cp.patched(
        "custos.core.factors.b1_structure", {"N_LOOKBACK": 30}
    ):  # v0.589：N 结构唯一实现在因子层
        an = tm.analyze(df, "600000")
        assert an["n_structure"].get("lookback", 30) == 30
    with cp.patched(
        "custos.pipeline.market_timing.technical_monitor", {"BOX_SHORT": 10}
    ):
        an = tm.analyze(df, "600000")
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

        raw = cp.KC_JS.read_bytes().replace(b"\r\n", b"\n")  # Windows CRLF 检出同口径
        assert hashlib.sha256(raw).hexdigest() == cp.KC_SHA256
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


def test_kc_digest_tolerates_crlf_checkout(monkeypatch, tmp_path):
    """Windows autocrlf 检出（LF→CRLF）仍能通过摘要校验（2026-11-10 Windows CI 红）。"""
    crlf = tmp_path / "klinecharts.min.js"
    crlf.write_bytes(
        cp.KC_JS.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    )
    monkeypatch.setattr(cp, "KC_JS", crlf)
    assert cp.kc_assets() is not None


def test_vendor_dir_marked_binary_in_gitattributes():
    import pathlib

    ga = (pathlib.Path(cp.__file__).resolve().parents[3] / ".gitattributes").read_text(
        encoding="utf-8"
    )
    assert "src/custos/research/vendor/** -text" in ga


def test_every_module_field_has_chinese_question_v0516():
    """owner 2026-11-13：每张图顶部写「检测器在问什么」——所有模块字段都要有中文定义。"""
    missing = [
        f for m in cp.MODULES.values() for f in m["fields"] if f not in cp.FIELD_DEF
    ]
    assert not missing, missing
    h = cp.ask_html("five_day_entry.hit", "miss", "false")
    assert "检测器在问" in h and "不成立" in h and "漏判" in h
    assert "算得对吗" in cp.ask_html("stop_loss_ref.price", "hit", "5.59")
    st = cp.ask_html("non_one_wave.status", "hit", "revoked")
    assert "判定对不对" in st and "成立</b>" not in st


def test_label_ui_prefill_and_hidden_v0518():
    ui = cp.label_ui(
        [[1, "f", "600001", "2024-01-02", "hit", "x", ""]],
        "sample",
        "k:v2",
        prefill={"1": "买点：错，放量"},
        hidden=["2"],
    )
    assert (
        '"prefill": {"1": "买点：错，放量"}' in ui
        and '"hidden": ["2"]' in ui
        and "HID.has" in ui
    )


def test_values_shown_in_chinese_v0537():
    """owner 2026-11-17：检测值显示中文（原值括注），labels 原值不变。"""
    assert cp.value_zh("revoked") == "撤销（revoked）" and cp.value_zh("中") == "中"
    assert "疑似一波流 / 出货（revoked）" in cp.ask_html(
        "non_one_wave.status", "hit", "revoked"
    )  # 字段专属，不用双重否定
    assert "一波流（one_wave）" in cp.ask_html("one_wave_v3.status", "hit", "one_wave")
    assert "成立（成立" not in cp.ask_html("five_day_entry.hit", "hit", "True")


class TestTrendShapeV0548:
    """v0.548 前置走势形态合并图集：多检测器读数 + 形态菜单 + 顶部出货勾选 + 对照读回。"""

    def _rep(self, tmp_path):
        bars = {"000001|2014": _df(1), "000002|2014": _df(2)}
        rep = cp.run_study(
            _args("sample", module="trend_shape", workers=1, n_show=3),
            bars=bars,
            index_df=_df(9),
            spans=[("2014-01-01", "2016-12-31")],
        )
        return rep, cp.write_outputs(rep, tmp_path)

    def test_readings_menu_and_extra(self, tmp_path):
        rep, d = self._rep(tmp_path)
        rows = [r for rs in rep["samples"].values() for r in rs]
        assert rows and set(rows[0]["readings"]) == set(cp.SHAPE_READINGS)
        codes = [r["key"][0].split("|")[0] for r in rows]
        assert len(codes) == len(set(codes))  # 每票最多一张
        g = (d / "gallery.html").read_text(encoding="utf-8")
        assert (
            "下跌转横盘" in g
            and "横盘转上涨" in g
            and cp.SHAPE_EXTRA in g
            and "检测器读数" in g
        )
        head = (d / "labels.csv").read_text(encoding="utf-8-sig").splitlines()[0]
        assert head.endswith("形态,顶部放量出货(Y/N),一波流(Y/N),备注")
        assert "button.xb" in g or "'xb'" in g  # v0.551 有/无 按钮

    def test_ingest_maps_shape_to_detectors(self, tmp_path):
        import csv as _csv

        rep, d = self._rep(tmp_path)
        rows = list(_csv.reader((d / "labels.csv").open(encoding="utf-8-sig")))
        rd = [r for rs in rep["samples"].values() for r in rs]
        for k, r in enumerate(rows[1:]):
            tr = rd[k]["readings"]["trend"]
            r[7] = {
                "上涨": "横盘转上涨",
                "下跌": "震荡下跌",
                "横盘震荡": "上涨转横盘",
            }.get(tr, "不确定")
            r[8] = "Y" if k == 0 else ("N" if k == 1 else "")  # 其余未选 ⇒ 不计
            r[9] = "N"
        with (d / "labels.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            _csv.writer(fh).writerows(rows)
        o = cp.ingest(d)
        assert o["mode"] == "shape" and o["n_labeled"] == len(rows) - 1
        assert o["agreement"]["trend"]["不一致"] == 0  # 按读数标（转折取后段）⇒ 全一致
        assert "trend" in o["crosstab"] and (d / "ingest.md").exists()

    def test_uncertain_not_counted(self):
        chk = cp._shape_checks("不确定", True, {"one_wave_v3": "unclear"})
        assert chk["trend"][0] is None  # 形态不确定 ⇒ 形态类不计
        assert chk["distribution"][0] == "有"  # v0.553 附加项与形态独立，照常计

    def test_unanswered_top_not_counted(self):
        chk = cp._shape_checks("横盘震荡", None, {"distribution": "watch"})
        assert chk["distribution"][0] is None and chk["trend"][0] == "横盘震荡"


def test_shape_menu_three_states_and_legacy_v0552():
    """owner 2026-11-19：三态 + 6 种转折；旧标签换算；趋势期望取转折后段。"""
    assert len(cp.SHAPE_MENU) == 10 and cp.SHAPE_MENU[:3] == cp.SHAPE_STATES
    assert (
        cp.shape_now("上涨转横盘") == "横盘震荡"
        and cp.shape_now("下跌中") == "震荡下跌"
    )
    assert cp.SHAPE_LEGACY["拉升后横盘震荡"] in cp.SHAPE_MENU


def test_shape_blind_hides_readings_v0553():
    h = cp.shape_ask_html({"trend": "上涨"}, blind=True)
    assert "复验盲标" in h and "趋势三态" not in h and "一波流" in h
    assert "趋势三态" in cp.shape_ask_html({"trend": "上涨"})


def test_retest_blind_and_self_agreement_v0556(tmp_path):
    """B 口径盲标复测：抽样打乱、隐去代码 / 日期与读数；读回与原标注比对。"""
    import csv as _csv

    t = TestTrendShapeV0548()
    _rep, d = t._rep(tmp_path / "src")
    rows = list(_csv.reader((d / "labels.csv").open(encoding="utf-8-sig")))
    for k, r in enumerate(rows[1:]):
        r[7], r[8], r[9] = ("横盘震荡" if k % 2 else "上涨转下跌"), "N", "N"
    with (d / "labels.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        _csv.writer(fh).writerows(rows)
    out = cp.build_retest([(d, d / "labels.csv")], 4, 1, tmp_path / "rt")
    g = (out / "gallery.html").read_text(encoding="utf-8")
    assert "复验盲标" in g and "检测器读数（只作参考）" not in g
    summ = __import__("json").loads((out / "summary.json").read_text(encoding="utf-8"))
    assert all(
        p["code"] not in g and p["date"] not in g for p in summ["retest_of"]
    )  # 整份页面（含 M.rows / KC）不露真实代码与日期
    chart_data = __import__("json").loads(
        (out / "chart_data.json").read_text(encoding="utf-8")
    )
    assert all(
        set(item["dates"])
        <= {
            (cp.date(2000, 1, 3) + cp.timedelta(days=i)).isoformat()
            for i in range(len(item["dates"]))
        }
        for item in chart_data.values()
    )
    rr = list(_csv.reader((out / "labels.csv").open(encoding="utf-8-sig")))
    assert all(r[2] == f"复测{n}" and r[3] == "" for n, r in enumerate(rr[1:], 1))
    for n, r in enumerate(rr[1:]):
        o = summ["retest_of"][n]["orig"]
        r[7], r[8], r[9] = (o["形态"] if n else "震荡下跌"), "N", "N"
    with (out / "labels.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        _csv.writer(fh).writerows(rr)
    res = cp.ingest(out)
    assert res["mode"] == "retest"
    assert res["self_agreement"]["形态整条"] == {"一致": len(rr) - 2, "不一致": 1}


def test_trend_shape_chart_only_kline_and_volume(tmp_path):
    """owner 2026-11-23：形态图集只画 K 线和量（不画 J / MACD / RSI）。"""
    t = TestTrendShapeV0548()
    _rep, d = t._rep(tmp_path)
    cd = __import__("json").loads((d / "chart_data.json").read_text(encoding="utf-8"))
    assert all(v.get("panes") == ["vol"] for v in cd.values())
    g = (d / "gallery.html").read_text(encoding="utf-8")
    assert "want('macd')" in g or "kc" not in g


def test_macd_gallery_hides_j_pane():
    """owner 2026-11-23：MACD 图集只画 K 线 / 量 / MACD。"""
    assert cp.MODULES["macd_technics"]["chart_panes"] == ["vol", "macd"]
