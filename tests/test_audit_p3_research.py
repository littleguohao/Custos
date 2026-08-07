# -*- coding: utf-8 -*-
"""P3 研究链回归测试（E8/E9/E11/O11/O12/O13）。

这一组守的是**"没跑成"不得伪装成"跑出来了"**：

  E8  特征/门槛依赖缺失静默返回 False/{} → 一个可能有用的因子被误判为"无判别力"
  E9  数据全空仍 exit 0，且空 firings 通过续跑校验被永久复用
  E11 财报时效：陈旧财报不得无限期视为有效（+ _tier_you 注释与实现相反）
  O11 --shard 参数非法时崩栈而非 ap.error
  O12 --out 父目录不存在时直接 FileNotFoundError
  O13 run_bear_to_long_study 缺 Optional 导入（注解在 get_type_hints 下炸）
"""
from __future__ import annotations

import json
import sys
import typing

import pandas as pd
import pytest

import backtest_factors as bt
import launch_point_study as lp
import s_data
from screening import financials as fin_mod
from research import run_bear_to_long_study as rb


def _bars(n=80, start="2024-01-02", base=10.0, step=0.1, vol=1e6):
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]
    closes = [base + step * i for i in range(n)]
    return pd.DataFrame({"date": dates, "open": closes,
                         "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
                         "close": closes, "volume": [vol] * n, "amount": [vol * 10] * n})


def _synth_two(step=0.1, base=(10.0, 20.0), n=60, vol=1e6):
    """两只票的合成日线。step<0 → 长期下跌(KDJ 深度超卖,--entry-filter j_low 会命中);
    step>0 → 单调上行(reversal_k / j_low 都不会命中,用来造'0 信号'场景)。"""
    out = {}
    for code, b in zip(("600000", "600001"), base):
        out[code] = _bars(n=n, base=b, step=step, vol=vol)
    return out, list(out["600000"]["date"])


# --------------------------------------------------------------------------- E8
class TestGateDependencyFailureNotSilent:
    """E8: 依赖缺失/异常必须与'真的不命中'分开计数并告警。"""

    def test_platform_pullback_dependency_missing_is_counted(self, monkeypatch):
        bt.reset_gate_stats()
        monkeypatch.setitem(sys.modules, "platform_pullback", None)   # import 触发 ImportError
        df = _bars(n=90)
        assert bt.platform_pullback_gate(df) is False
        st = bt.gate_stats().get("platform_pullback") or {}
        assert st.get("dep_missing", 0) >= 1, "依赖缺失必须计数,不能与'不命中'混为一谈"
        assert st.get("miss", 0) == 0, "依赖缺失不得记成'真的不命中'"

    def test_platform_pullback_detector_exception_is_counted(self, monkeypatch):
        bt.reset_gate_stats()

        def boom(*a, **k):
            raise RuntimeError("detector broken")

        import platform_pullback as pp
        monkeypatch.setattr(pp, "detect_platform_pullback", boom)
        assert bt.platform_pullback_gate(_bars(n=90)) is False
        st = bt.gate_stats().get("platform_pullback") or {}
        assert st.get("error", 0) >= 1
        assert st.get("miss", 0) == 0

    def test_platform_pullback_genuine_miss_recorded_as_miss(self):
        bt.reset_gate_stats()
        assert bt.platform_pullback_gate(_bars(n=90)) is False        # 单调上行,无平台
        st = bt.gate_stats().get("platform_pullback") or {}
        assert st.get("miss", 0) >= 1 and not st.get("dep_missing") and not st.get("error")

    def test_short_history_not_counted_as_miss(self):
        bt.reset_gate_stats()
        assert bt.platform_pullback_gate(_bars(n=30)) is False
        st = bt.gate_stats().get("platform_pullback") or {}
        assert st.get("short_history", 0) >= 1 and st.get("miss", 0) == 0

    def test_gate_stats_report_flags_broken_dependency(self, monkeypatch):
        bt.reset_gate_stats()
        monkeypatch.setitem(sys.modules, "platform_pullback", None)
        for _ in range(3):
            bt.platform_pullback_gate(_bars(n=90))
        rep = bt.gate_stats_report()
        assert rep["broken"], "依赖失败必须能被 main 检出并告警"
        assert "platform_pullback" in rep["text"]


class TestSectorFeatureBuildFailureNotSilent:
    """E8: build_sector_features 无板块数据时必须自报,不能全程返回 {} 装作'板块无判别力'。"""

    @pytest.fixture(autouse=True)
    def _isolate_sector_name_table(self, monkeypatch):
        """板块名称表(tdxzs.cfg)是宿主环境依赖：本机 880201=黑龙江(tdx_type=3 地区板块)
        会被 invert_members 剔除导致 sectors_requested 少 1,无 cfg 的机器则不过滤。
        注入空名称表把口径固定为"不过滤"(与无 cfg 环境一致),测试结果不随宿主漂移。"""
        import tq_sector
        monkeypatch.setattr(tq_sector, "load_sector_names", lambda *a, **kw: {})

    def test_no_csv_reports_zero_sectors_loaded(self, tmp_path):
        members = {"880201.SH": ["600000"], "880900.SH": ["000002"]}
        fn = lp.build_sector_features(tmp_path, members)
        st = fn.stats
        assert st["sectors_requested"] == 2 and st["sectors_loaded"] == 0
        assert st["csv_missing"] == 2
        assert fn("600000", "2024-01-02") == {}

    def test_unparsable_csv_counted_as_error_not_missing(self, tmp_path):
        (tmp_path / "880201.SH.csv").write_text("garbage\nnot,a,frame", encoding="utf-8")
        fn = lp.build_sector_features(tmp_path, {"880201.SH": ["600000"]})
        assert fn.stats["csv_error"] == 1 and fn.stats["sectors_loaded"] == 0

    def test_query_counters_separate_unclassified_from_emitted(self, tmp_path):
        n = 130
        dates = [str(d)[:10] for d in pd.date_range("2022-01-03", periods=n, freq="B")]
        closes = [10 + 0.15 * i for i in range(n)]
        (tmp_path / "880201.SH.csv").write_text(
            "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, closes)), encoding="utf-8")
        fn = lp.build_sector_features(tmp_path, {"880201.SH": ["600000"]})
        assert fn("600000", dates[-1])["f_sector_favorable"] == 1
        assert fn("999999", dates[-1]) == {}
        assert fn.stats["emitted"] == 1 and fn.stats["unclassified"] == 1

    def test_main_reports_sector_feature_yield_when_data_present(self, tmp_path, capsys):
        """有板块数据时正常跑通,并把"多少信号拿到了板块特征"打出来(区分缺省 vs 值为0)。"""
        bars, dates = _synth_two(step=-0.3)
        sec_closes = [50 - 0.2 * i for i in range(len(dates))]
        (tmp_path / "880201.SH.csv").write_text(
            "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, sec_closes)),
            encoding="utf-8")
        members = tmp_path / "members.json"
        members.write_text(json.dumps({"880201.SH": list(bars)}), encoding="utf-8")
        out = tmp_path / "f.json"
        rc = lp.main(["--codes", ",".join(bars), "--start", dates[40], "--end", dates[-1],
                      "--emit-firings", str(out), "--entry-filter", "j_low",
                      "--rank-score", "none", "--buffer-days", "0",
                      "--sector-features", "--sector-members", str(members),
                      "--sector-index-dir", str(tmp_path)],
                     loader=lambda codes, _n: {c: bars[c] for c in codes if c in bars})
        err = capsys.readouterr().err
        assert rc == 0, err
        assert "1/1 板块有数据" in err and "板块特征产出" in err
        head = json.loads(out.read_text(encoding="utf-8"))
        assert head["sector_features"] is True and head["n_signal_days"] > 0
        assert any("f_sector_favorable" in (d[2] if len(d) > 2 else {})
                   for r in head["records"] for d in r["days"])

    def test_main_refuses_sector_features_without_any_index_data(self, tmp_path, capsys):
        members = tmp_path / "members.json"
        members.write_text(json.dumps({"880201.SH": ["600000"]}), encoding="utf-8")
        bars, dates = _synth_two()
        with pytest.raises(SystemExit) as e:
            lp.main(["--codes", ",".join(bars), "--start", dates[0], "--end", dates[-1],
                     "--emit-firings", str(tmp_path / "f.json"),
                     "--sector-features", "--sector-members", str(members),
                     "--sector-index-dir", str(tmp_path / "empty_dir"),
                     "--buffer-days", "0"],
                    loader=lambda codes, _n: {c: bars[c] for c in codes if c in bars})
        assert e.value.code != 0
        assert "板块指数" in capsys.readouterr().err


# --------------------------------------------------------------------------- E9
class TestEmptyResultMustFail:
    """E9: 全空数据/空结果必须非零退出,且不得落下可复用产物。"""

    def test_emit_firings_with_no_bars_exits_nonzero_and_writes_nothing(self, tmp_path):
        out = tmp_path / "f.json"
        rc = lp.main(["--codes", "600000", "--start", "2024-01-02", "--end", "2024-06-28",
                      "--emit-firings", str(out), "--buffer-days", "0",
                      "--sector-members", str(tmp_path / "none.json")],
                     loader=lambda codes, _n: {})
        assert rc != 0, "一根 K 线都没加载到,不能报成功"
        assert not out.exists(), "空结果不得落盘(否则被续跑校验当已完成永久复用)"
        assert not (tmp_path / "f.json.tmp").exists()

    def test_emit_firings_with_zero_signals_exits_nonzero(self, tmp_path):
        bars, dates = _synth_two()                      # 单调上行 → reversal_k 一次都不命中
        out = tmp_path / "f.json"
        rc = lp.main(["--codes", ",".join(bars), "--start", dates[0], "--end", dates[-1],
                      "--emit-firings", str(out), "--buffer-days", "0",
                      "--entry-filter", "reversal_k",
                      "--sector-members", str(tmp_path / "none.json")],
                     loader=lambda codes, _n: {c: bars[c] for c in codes if c in bars})
        assert rc != 0 and not out.exists()

    def test_allow_empty_opt_in_still_writes_and_marks(self, tmp_path):
        out = tmp_path / "f.json"
        rc = lp.main(["--codes", "600000", "--start", "2024-01-02", "--end", "2024-06-28",
                      "--emit-firings", str(out), "--buffer-days", "0", "--allow-empty",
                      "--sector-members", str(tmp_path / "none.json")],
                     loader=lambda codes, _n: {})
        assert rc == 0 and out.exists()
        assert json.loads(out.read_text(encoding="utf-8"))["empty_ok"] is True

    def test_analyze_path_with_empty_regime_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp.bt, "load_amv_regime", lambda since="2015-01-01", root=None: {})
        bars, dates = _synth_two()
        rc = lp.main(["--codes", ",".join(bars), "--start", dates[0], "--end", dates[-1],
                      "--buffer-days", "0", "--sector-members", str(tmp_path / "none.json")],
                     loader=lambda codes, _n: {c: bars[c] for c in codes if c in bars})
        assert rc != 0, "0AMV regime 全空 → 起涨点按 regime 分类全是'未知',结论无意义"

    def test_backtest_factors_no_bars_exits_nonzero_without_writing(self, tmp_path):
        out = tmp_path / "bt.json"
        rc = bt.main(["--codes", "600000", "--horizons", "5", "--out", str(out)],
                     loader=lambda codes, count: {})
        assert rc != 0 and not out.exists()

    def test_backtest_factors_zero_records_exits_nonzero(self, tmp_path):
        out = tmp_path / "bt.json"
        rc = bt.main(["--codes", "600000", "--horizons", "5", "--out", str(out),
                      "--entry-filter", "platform_pullback"],
                     loader=lambda codes, count: {"600000": _bars(n=90)})
        assert rc != 0 and not out.exists()

    def test_backtest_factors_trade_sim_zero_trades_exits_nonzero(self, tmp_path):
        out = tmp_path / "sim.json"
        rc = bt.main(["--codes", "600000", "--trade-sim", "--scorer", "baseline",
                      "--entry-filter", "platform_pullback", "--out", str(out)],
                     loader=lambda codes, count: {"600000": _bars(n=90)})
        assert rc != 0 and not out.exists()

    def test_backtest_factors_allow_empty_opt_in(self, tmp_path):
        out = tmp_path / "bt.json"
        rc = bt.main(["--codes", "600000", "--horizons", "5", "--out", str(out),
                      "--entry-filter", "platform_pullback", "--allow-empty"],
                     loader=lambda codes, count: {"600000": _bars(n=90)})
        assert rc == 0 and out.exists()

    def test_empty_firings_not_reusable(self, tmp_path):
        """空 records 的 firings 不得被断点续跑当'已完成'复用。"""
        f = tmp_path / "firings_x.json"
        head = {"entry_filter": "reversal_k", "rank_score": "none",
                "feature_scores": rb.DEFAULT_FEATURES, "delisted_ret": -1.0,
                "universe": "sdata", "records": []}
        f.write_text(json.dumps(head), encoding="utf-8")
        assert rb.firings_reusable(f, _rb_args()) is False

    def test_nonempty_firings_still_reusable(self, tmp_path):
        f = tmp_path / "firings_x.json"
        head = {"entry_filter": "reversal_k", "rank_score": "none",
                "feature_scores": rb.DEFAULT_FEATURES, "delisted_ret": -1.0,
                "universe": "sdata",
                "records": [{"code": "600000", "ret": 0.1, "days": [["2022-06-01", 0.0]]}]}
        f.write_text(json.dumps(head), encoding="utf-8")
        assert rb.firings_reusable(f, _rb_args()) is True

    def test_s_data_warns_when_root_has_no_bundle(self, tmp_path, capsys):
        (tmp_path / "not_a_bundle").mkdir()
        assert s_data.list_bundles(tmp_path) == []
        assert "WARN" in capsys.readouterr().err, "目录在但一个 bundle 都没有,必须告警"

    def test_s_data_warns_when_nothing_loaded(self, tmp_path, capsys):
        assert s_data.load_bars_qlib(["600000", "000001"], 0, root=tmp_path) == {}
        err = capsys.readouterr().err
        assert "WARN" in err and "0/2" in err


def _rb_args():
    """构造与 run_bear_to_long_study 默认一致的 args namespace(供 firings_reusable 单测)。"""
    class NS:
        entry_filter = "reversal_k"
        feature_scores = rb.DEFAULT_FEATURES
        delisted_ret = -1.0
        sector_features = False
        style_features = False
        trade_sim = False
        pit_features = False
        pit_visible_same_day = False
        pit_ledger = ""
        stop_pct = 8.0
        bbi_consec = 2
    return NS()


# -------------------------------------------------------------------------- E11
class TestFinancialReportStaleness:
    """E11: 陈旧财报不得无限期视为有效。"""

    def _df(self, report_date):
        return pd.DataFrame({"证券代码": ["600000"], "报告期": [report_date],
                             "归属于母公司股东的净利润": [1.0e8],
                             "经营活动产生的现金流量净额": [2.0e8],
                             "净资产收益率(加权)": [8.0]})

    def _colmap(self, df):
        return fin_mod.auto_colmap(df.columns)

    def test_fresh_report_available(self):
        df = self._df("2026-03-31")
        r = fin_mod.financial_factor("600000", df, self._colmap(df), as_of="2026-05-30")
        assert r["available"] is True and r["report_stale"] is False
        assert r["report_age_days"] == 60

    def test_stale_report_marked_unavailable(self):
        df = self._df("2019-12-31")
        r = fin_mod.financial_factor("600000", df, self._colmap(df), as_of="2026-05-30")
        assert r["available"] is False and r["reason"] == "report_stale"
        assert r["report_age_days"] > fin_mod.REPORT_MAX_AGE_DAYS

    def test_staleness_cap_can_be_disabled(self):
        df = self._df("2019-12-31")
        r = fin_mod.financial_factor("600000", df, self._colmap(df),
                                     as_of="2026-05-30", max_age_days=0)
        assert r["available"] is True and r["report_stale"] is None

    def test_missing_report_date_is_reported_not_assumed_fresh(self):
        df = pd.DataFrame({"证券代码": ["600000"], "归属于母公司股东的净利润": [1.0e8],
                           "经营活动产生的现金流量净额": [2.0e8]})
        r = fin_mod.financial_factor("600000", df, fin_mod.auto_colmap(df.columns),
                                    as_of="2026-05-30")
        assert r["available"] is True
        assert r["report_stale"] is None and r["stale_check"] == "no_report_date"

    def test_report_age_days_helper(self):
        assert fin_mod.report_age_days("2026-01-01", "2026-01-31") == 30
        assert fin_mod.report_age_days("", "2026-01-31") is None
        assert fin_mod.report_age_days("bogus", "2026-01-31") is None


class TestTierYouVisibilitySemantics:
    """E11: `_tier_you` 的可见性口径钉住——注释曾写'公告当日即可见',实现却是'次日起可见'。

    实现(严口径/无 look-ahead)与模块 docstring、launch_point_study 的
    `--pit-visible-same-day` 默认关闭一致,故只改注释、不动行为;本测试防它日后被"顺着
    错注释改对"。
    """

    def _idx(self):
        # 元组结构 = (notice_date, report_date, net_profit, ocf_ps, roe_waa)。
        # report_date 取信号日附近的新鲜值，把财报时效检查(REPORT_MAX_AGE_DAYS)
        # 隔离出去——本类只测「公告日→可见日」的 as-of 语义，不测时效。
        # 时效上限本身在 tests/test_report_staleness.py 覆盖。
        return {"600000": [("2026-01-10", "2025-12-31", 1.0, 0.5, 8.0),
                           ("2026-04-20", "2026-03-31", 2.0, 0.6, 9.0)]}

    def test_announcement_day_itself_not_yet_visible(self):
        from research import scan_signals_ytd as scan
        assert scan._tier_you(self._idx(), "600000", "2026-01-10") is False

    def test_next_day_visible(self):
        from research import scan_signals_ytd as scan
        assert scan._tier_you(self._idx(), "600000", "2026-01-11") is True

    def test_unknown_code_false(self):
        from research import scan_signals_ytd as scan
        assert scan._tier_you(self._idx(), "999999", "2026-01-11") is False


# ------------------------------------------------------------------- O11 / O12
class TestCliGuards:
    def test_bad_shard_is_argparse_error_not_traceback(self, tmp_path, capsys):
        bars, dates = _synth_two()
        for bad in ("abc", "1/2/3", "0/3", "3/2", "1/0", "x/3"):
            with pytest.raises(SystemExit) as e:
                lp.main(["--codes", ",".join(bars), "--start", dates[0], "--end", dates[-1],
                         "--emit-firings", str(tmp_path / "f.json"), "--shard", bad,
                         "--buffer-days", "0",
                         "--sector-members", str(tmp_path / "none.json")],
                        loader=lambda codes, _n: {c: bars[c] for c in codes if c in bars})
            assert e.value.code == 2, bad
            assert "--shard" in capsys.readouterr().err, bad

    def test_valid_shard_still_works(self, tmp_path):
        bars, dates = _synth_two(step=-0.3)             # 长期下跌 → j_low 命中
        out = tmp_path / "deep" / "f.json"              # 顺带覆盖 --emit-firings 建父目录
        rc = lp.main(["--codes", ",".join(bars), "--start", dates[40], "--end", dates[-1],
                      "--emit-firings", str(out), "--shard", "1/2", "--buffer-days", "0",
                      "--entry-filter", "j_low", "--rank-score", "none",
                      "--sector-members", str(tmp_path / "none.json")],
                     loader=lambda codes, _n: {c: bars[c] for c in codes if c in bars})
        assert rc == 0
        head = json.loads(out.read_text(encoding="utf-8"))
        assert head["shard"] == "1/2" and head["n_signal_days"] > 0
        assert "empty_ok" not in head

    def test_out_creates_parent_dirs_pass2(self, tmp_path):
        firings = tmp_path / "f.json"
        firings.write_text(json.dumps({
            "start": "2024-01-02", "end": "2024-06-28",
            "records": [{"code": "600000", "ret": 0.5,
                         "days": [["2024-03-01", 1.0, {"fwd20": 0.3}]]},
                        {"code": "600001", "ret": -0.2,
                         "days": [["2024-03-01", 0.2, {"fwd20": -0.1}]]}]}), encoding="utf-8")
        deep = tmp_path / "a" / "b" / "out.json"
        rc = lp.main(["--from-firings", str(firings), "--discriminate", "--out", str(deep)])
        assert rc == 0 and deep.is_file()

    def test_out_creates_parent_dirs_window_pairs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp.bt, "load_amv_regime",
                            lambda since=None, root=None: {"2022-01-03": "空头"})
        deep = tmp_path / "x" / "y" / "pairs.json"
        rc = lp.main(["--list-window-pairs", "--out", str(deep)])
        assert rc == 0 and deep.is_file()

    def test_out_creates_parent_dirs_long_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp.bt, "load_amv_regime",
                            lambda since=None, root=None: {"2022-01-03": "做多"})
        deep = tmp_path / "p" / "q" / "long.json"
        rc = lp.main(["--list-long-windows", "--out", str(deep), "--min-window-days", "1"])
        assert rc == 0 and deep.is_file()


# -------------------------------------------------------------------------- O13
def test_run_bear_to_long_annotations_resolve():
    """O13: 缺 Optional 导入 → get_type_hints 解析注解直接 NameError。"""
    typing.get_type_hints(rb.survivorship_report)
