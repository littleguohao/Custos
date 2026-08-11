# -*- coding: utf-8 -*-
"""前复权（qfq）回归测试。owner 2026-08-04 拍板：全链统一前复权。

为什么这套测试值得写细：未复权数据的错误**不会报错、不会崩溃**，只会让回测数字
悄悄偏掉（实测同一段真实上涨走势，未复权 −42.50% vs 前复权 +25.00%，差 67.5pp）。
这正是本次审计反复遇到的最危险失效模式，只能靠断言钉住。
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import pytest

from code_utils import is_index
from local_tdx import adjust_factors as af


def _ev(date, *, fenhong=0.0, songzhuangu=0.0, peigu=0.0, peigujia=0.0, suogu=0.0):
    return {"date": date, "fenhong": fenhong, "songzhuangu": songzhuangu,
            "peigu": peigu, "peigujia": peigujia, "suogu": suogu}


def _mk(closes, dates=None):
    n = len(closes)
    d = dates or pd.bdate_range("2025-01-01", periods=n).astype(str).tolist()
    c = np.asarray(closes, float)
    return pd.DataFrame({"date": d, "open": c, "high": c * 1.01,
                         "low": c * 0.99, "close": c,
                         "volume": np.full(n, 1e6)})


class TestEventRatio:
    """除权参考价公式：(前收 − 现金红利 + 配股价×配股比例) / (1 + 送股比例 + 配股比例)"""

    @pytest.mark.parametrize("ev,prev,expect", [
        (_ev("2025-01-05", fenhong=4.2), 10.0, (10 - 0.42) / 10),
        (_ev("2025-01-05", songzhuangu=10), 20.0, 1 / 2),
        (_ev("2025-01-05", songzhuangu=5), 15.0, 1 / 1.5),
        (_ev("2025-01-05", peigu=3, peigujia=5.0), 10.0, (10 + 5 * 0.3) / 1.3 / 10),
        (_ev("2025-01-05", fenhong=2, songzhuangu=5), 20.0, (20 - 0.2) / 1.5 / 20),
    ])
    def test_textbook_cases(self, ev, prev, expect):
        assert af.event_ratio(prev, ev) == pytest.approx(expect, rel=1e-12)

    def test_rights_issue_adds_not_subtracts(self):
        """配股是**加**项：股东掏钱换股，理论价是加权平均。

        漏掉这一项会把配股当纯送股，因子偏小 ⇒ 历史价格被压得过低 ⇒ 虚增涨幅。
        """
        with_pg = af.event_ratio(10.0, _ev("d", peigu=3, peigujia=5.0))
        as_bonus = af.event_ratio(10.0, _ev("d", songzhuangu=3))
        assert with_pg > as_bonus

    def test_reverse_split_raises_price(self):
        """缩股：10 股缩为 1 股 ⇒ 价格放大 10 倍。"""
        r = af.event_ratio(1.0, _ev("d", suogu=1.0))
        assert r == pytest.approx(10.0)

    @pytest.mark.parametrize("prev", [0.0, -1.0, None])
    def test_rejects_bad_prev_close(self, prev):
        assert af.event_ratio(prev, _ev("d", fenhong=1)) is None

    def test_rejects_absurd_ratio(self):
        """分红大于股价属数据错误，必须拒绝而非产出负价。"""
        assert af.event_ratio(1.0, _ev("d", fenhong=100)) is None


class TestFactorSeries:
    def test_latest_factor_is_always_one(self):
        """**最关键的不变量**：最新一天因子恒为 1 ⇒ 前复权价 = 盘面价。

        这条成立，「统一用前复权」才不会让买入价/止损价偏离盘面，
        也才不需要把展示价与指标价分成两套维护。
        """
        for events in ([], [_ev("2025-01-03", songzhuangu=10)],
                       [_ev("2025-01-03", fenhong=5), _ev("2025-01-07", songzhuangu=5)]):
            f = af.compute_qfq_factors(
                pd.bdate_range("2025-01-01", periods=10).astype(str).tolist(),
                np.full(10, 20.0), events)
            assert f[-1] == 1.0

    def test_factor_applies_only_before_event(self):
        dates = pd.bdate_range("2025-01-01", periods=6).astype(str).tolist()
        f = af.compute_qfq_factors(dates, np.full(6, 20.0),
                                   [_ev(dates[3], songzhuangu=10)])
        assert list(f[:3]) == [0.5, 0.5, 0.5]
        assert list(f[3:]) == [1.0, 1.0, 1.0]

    def test_multiple_events_compound(self):
        dates = pd.bdate_range("2025-01-01", periods=8).astype(str).tolist()
        f = af.compute_qfq_factors(dates, np.full(8, 20.0),
                                   [_ev(dates[2], songzhuangu=10),
                                    _ev(dates[5], songzhuangu=10)])
        assert f[0] == pytest.approx(0.25)      # 两次腰斩累乘
        assert f[3] == pytest.approx(0.5)
        assert f[-1] == 1.0

    def test_events_outside_sample_ignored(self):
        dates = pd.bdate_range("2025-06-01", periods=5).astype(str).tolist()
        f = af.compute_qfq_factors(dates, np.full(5, 20.0),
                                   [_ev("2020-01-01", songzhuangu=10),
                                    _ev("2030-01-01", songzhuangu=10)])
        assert list(f) == [1.0] * 5

    def test_empty_inputs(self):
        assert len(af.compute_qfq_factors([], [], [_ev("d", fenhong=1)])) == 0


class TestApplyQfq:
    def test_removes_fake_gap(self):
        """核心目的：除权日的假跳空必须消失。"""
        d = pd.bdate_range("2025-01-01", periods=6).astype(str).tolist()
        raw = _mk([20, 20, 20, 10, 10, 10], d)          # 未复权：10送10 后腰斩
        adj = af.apply_qfq(raw, [_ev(d[3], songzhuangu=10)])
        gap_raw = raw["close"].iloc[3] / raw["close"].iloc[2] - 1
        gap_adj = adj["close"].iloc[3] / adj["close"].iloc[2] - 1
        assert gap_raw == pytest.approx(-0.5)
        assert gap_adj == pytest.approx(0.0, abs=1e-12)

    def test_last_price_unchanged(self):
        d = pd.bdate_range("2025-01-01", periods=6).astype(str).tolist()
        raw = _mk([20, 20, 20, 10, 10, 11], d)
        adj = af.apply_qfq(raw, [_ev(d[3], songzhuangu=10)])
        assert adj["close"].iloc[-1] == pytest.approx(raw["close"].iloc[-1])

    def test_keeps_raw_close(self):
        """展示与下单要用未复权价，必须保留。"""
        d = pd.bdate_range("2025-01-01", periods=4).astype(str).tolist()
        raw = _mk([20, 20, 10, 10], d)
        adj = af.apply_qfq(raw, [_ev(d[2], songzhuangu=10)])
        assert list(adj["raw_close"]) == [20, 20, 10, 10]

    def test_volume_adjusted_inversely(self):
        """量必须同步:送转后股数增加,不调量会让量比/20日量底在除权日断层。"""
        d = pd.bdate_range("2025-01-01", periods=4).astype(str).tolist()
        raw = _mk([20, 20, 10, 10], d)
        adj = af.apply_qfq(raw, [_ev(d[2], songzhuangu=10)])
        assert adj["volume"].iloc[0] == pytest.approx(2e6)      # 1e6 / 0.5
        assert adj["volume"].iloc[-1] == pytest.approx(1e6)

    def test_attrs_marked(self):
        adj = af.apply_qfq(_mk([10] * 5), [])
        assert adj.attrs.get("adjust") == "qfq"

    def test_dropped_event_marks_qfq_partial(self):
        """样本内事件 ratio 求不出(前收盘<=0)被跳过 ⇒ 必须标 qfq_partial,不许盖章 qfq
        ——「部分除权事件没参与复权」是静默降级,attrs 必须可观测。"""
        d = pd.bdate_range("2025-01-01", periods=4).astype(str).tolist()
        raw = _mk([0, 0, 10, 10], d)                      # 事件日前收盘为 0 → ratio None
        adj = af.apply_qfq(raw, [_ev(d[2], songzhuangu=10)])
        assert adj.attrs.get("adjust") == "qfq_partial"
        assert adj.attrs.get("adjust_events_dropped") == 1

    def test_missing_columns_marks_none_not_qfq(self):
        """缺 date/close 列时按未复权返回,且必须留痕(此前会被 qfq_table 盖章成 qfq)。"""
        adj = af.apply_qfq(pd.DataFrame({"x": [1, 2]}), [])
        assert adj.attrs.get("adjust") == "none"
        assert "adjust_error" in adj.attrs

    def test_ohlc_all_scaled(self):
        d = pd.bdate_range("2025-01-01", periods=4).astype(str).tolist()
        raw = _mk([20, 20, 10, 10], d)
        adj = af.apply_qfq(raw, [_ev(d[2], songzhuangu=10)])
        for col in ("open", "high", "low", "close"):
            assert adj[col].iloc[0] == pytest.approx(raw[col].iloc[0] * 0.5)


class TestNormalizeXdxr:
    def test_keeps_only_price_affecting(self):
        df = pd.DataFrame([
            {"year": 2025, "month": 7, "day": 16, "category": 1, "fenhong": 4.2,
             "songzhuangu": 0, "peigu": 0, "peigujia": 0, "suogu": None},
            {"year": 2025, "month": 10, "day": 16, "category": 5, "fenhong": None,
             "songzhuangu": None, "peigu": None, "peigujia": None, "suogu": None},
        ])
        ev = af.normalize_xdxr(df)
        assert len(ev) == 1 and ev[0]["date"] == "2025-07-16"

    def test_drops_all_zero_rows(self):
        df = pd.DataFrame([{"year": 2025, "month": 1, "day": 1, "category": 1,
                            "fenhong": 0, "songzhuangu": 0, "peigu": 0,
                            "peigujia": 0, "suogu": 0}])
        assert af.normalize_xdxr(df) == []

    def test_nan_becomes_zero(self):
        df = pd.DataFrame([{"year": 2025, "month": 1, "day": 1, "category": 1,
                            "fenhong": 3.0, "songzhuangu": float("nan"),
                            "peigu": None, "peigujia": None, "suogu": None}])
        ev = af.normalize_xdxr(df)
        assert ev[0]["songzhuangu"] == 0.0 and ev[0]["fenhong"] == 3.0

    def test_handles_none_and_empty(self):
        assert af.normalize_xdxr(None) == []
        assert af.normalize_xdxr(pd.DataFrame()) == []

    def test_sorted_by_date(self):
        df = pd.DataFrame([
            {"year": 2025, "month": 7, "day": 1, "category": 1, "fenhong": 1,
             "songzhuangu": 0, "peigu": 0, "peigujia": 0, "suogu": 0},
            {"year": 2023, "month": 5, "day": 1, "category": 1, "fenhong": 2,
             "songzhuangu": 0, "peigu": 0, "peigujia": 0, "suogu": 0}])
        ev = af.normalize_xdxr(df)
        assert [e["date"] for e in ev] == ["2023-05-01", "2025-07-01"]

    def test_truncation_keeps_newest(self):
        """超 MAX_EVENTS_SANE 截断必须保留**最新**事件（新事件才影响近期复权，
        此前保留最旧 500 条方向反了）。"""
        days = pd.date_range("2000-01-01", periods=600, freq="D")
        df = pd.DataFrame([
            {"year": d.year, "month": d.month, "day": d.day, "category": 1,
             "fenhong": 1, "songzhuangu": 0, "peigu": 0, "peigujia": 0, "suogu": 0}
            for d in days])
        ev = af.normalize_xdxr(df)
        assert len(ev) == af.MAX_EVENTS_SANE
        assert ev[-1]["date"] == str(days[-1])[:10]
        assert ev[0]["date"] == str(days[-af.MAX_EVENTS_SANE])[:10]


class TestCache:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        ev = [_ev("2025-07-16", fenhong=4.2)]
        af.save_xdxr_cache("600000", ev, fetched_at="2026-08-04T18:00:00+08:00")
        assert af.load_xdxr_cache("600000") == ev

    def test_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        assert af.load_xdxr_cache("999999") is None

    def test_corrupt_returns_none_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        (tmp_path / "600000.json").write_text("{not json", encoding="utf-8")
        assert af.load_xdxr_cache("600000") is None

    def test_stale_detection(self, tmp_path, monkeypatch):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        af.save_xdxr_cache("600000", [], fetched_at="2020-01-01T00:00:00+08:00")
        af.save_xdxr_cache("000002", [], fetched_at=af.cn_now().isoformat())
        stale = af.stale_codes(["600000", "000002", "600519"], max_age_days=7)
        assert "600000" in stale, "旧缓存要刷新"
        assert "600519" in stale, "无缓存要取数"
        assert "000002" not in stale, "新缓存不该重取"

    def test_write_is_atomic(self, tmp_path, monkeypatch):
        """两阶段写：崩在中途不能留下半个 json（审计 D1 同类问题）。"""
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        af.save_xdxr_cache("600000", [_ev("2025-01-01", fenhong=1)])
        assert not list(tmp_path.glob("*.tmp"))
        json.loads((tmp_path / "600000.json").read_text(encoding="utf-8"))


class TestIndexSkip:
    """指数不除权，对它取权息是白费网络请求（880/881 细分行业有 467 个）。"""

    @pytest.mark.parametrize("code", ["999999", "880001", "880005.SH", "881101",
                                      "399001", "399006.SZ", "899050",
                                      "000300.SH", "000688.SH"])
    def test_indices_detected(self, code):
        assert is_index(code) is True

    @pytest.mark.parametrize("code", ["600000", "000002", "300750", "920819",
                                      "688111", "603001", "001979"])
    def test_stocks_not_flagged(self, code):
        assert is_index(code) is False

    def test_bare_000001_is_not_index(self):
        """000001 无后缀时是平安银行（深市个股），不能当上证指数。"""
        assert is_index("000001") is False
        assert is_index("000001.SH") is True

    def test_empty_and_garbage(self):
        for v in ("", None, "abc", "12"):
            assert is_index(v) is False


class TestEntryPointDefault:
    def test_default_is_qfq(self):
        """默认必须是 qfq——「统一前复权」就是要消除「哪个调用方记得传参」的不确定性。"""
        import inspect

        from local_tdx.local_tdx_data import get_ohlcv_table
        assert inspect.signature(get_ohlcv_table).parameters["adjust"].default == "qfq"

    def test_qfq_failure_degrades_with_trace(self, monkeypatch):
        """权息取不到时不得中断选股链，但**必须留痕**——否则又是「降级了没人知道」。"""
        def boom(*a, **k):
            raise af.AdjustError("网络不可用")
        monkeypatch.setattr(af, "get_xdxr", boom)
        raw = _mk([10] * 5)
        out = af.qfq_table("600000", raw, strict=False)
        assert out.attrs.get("adjust") == "none"
        assert "网络不可用" in str(out.attrs.get("adjust_error"))

    def test_strict_mode_raises(self, monkeypatch):
        def boom(*a, **k):
            raise af.AdjustError("网络不可用")
        monkeypatch.setattr(af, "get_xdxr", boom)
        with pytest.raises(af.AdjustError):
            af.qfq_table("600000", _mk([10] * 5), strict=True)


class TestSharesEvents:
    """股本数据来自同一份 xdxr（category=5「股本变化」），替代东财市值接口。

    owner 原则「尽量用本地 TDX 接口，HTTP 不稳定」（2026-08-04）。
    """

    def _raw(self):
        return pd.DataFrame([
            {"year": 2020, "month": 5, "day": 20, "category": 5,
             "houzongguben": 1130200.0, "panhouliutong": 970000.0,
             "fenhong": None, "songzhuangu": None, "peigu": None,
             "peigujia": None, "suogu": None},
            {"year": 2023, "month": 6, "day": 30, "category": 5,
             "houzongguben": 1163100.0, "panhouliutong": 971000.0,
             "fenhong": None, "songzhuangu": None, "peigu": None,
             "peigujia": None, "suogu": None},
            {"year": 2025, "month": 7, "day": 16, "category": 1,
             "fenhong": 4.2, "songzhuangu": 0, "peigu": 0, "peigujia": 0,
             "suogu": None, "houzongguben": None, "panhouliutong": None},
        ])

    def test_extracts_shares_in_shares_not_wan(self):
        """xdxr 单位是**万股**，必须换算成股——差 10000 倍的错误不会报错只会算错市值。"""
        sh = af.normalize_shares(self._raw())
        assert len(sh) == 2
        assert sh[0]["total_shares"] == pytest.approx(1130200.0 * 10000)
        assert sh[0]["float_shares"] == pytest.approx(970000.0 * 10000)

    def test_price_events_excluded_from_shares(self):
        sh = af.normalize_shares(self._raw())
        assert all(e["date"] != "2025-07-16" for e in sh)

    def test_shares_events_do_not_pollute_price_events(self):
        """两类事件必须分开：股本变化不影响单股权益，混进复权会算错因子。"""
        ev = af.normalize_xdxr(self._raw())
        assert len(ev) == 1 and ev[0]["date"] == "2025-07-16"

    def test_sorted_and_empty_safe(self):
        assert af.normalize_shares(None) == []
        assert af.normalize_shares(pd.DataFrame()) == []
        sh = af.normalize_shares(self._raw())
        assert [e["date"] for e in sh] == sorted(e["date"] for e in sh)

    def test_point_in_time_lookup(self, tmp_path, monkeypatch):
        """**PIT**：算历史市值只能用当时的股本，用今天的是未来函数。"""
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        af.save_xdxr_cache("000002", [], fetched_at=af.cn_now().isoformat(),
                           shares=af.normalize_shares(self._raw()))
        assert af.total_shares_at("000002", "2019-01-01") is None, "事件之前应为 None"
        assert af.total_shares_at("000002", "2021-01-01") == pytest.approx(1130200e4)
        assert af.total_shares_at("000002", "2026-08-04") == pytest.approx(1163100e4)

    def test_float_shares_field(self, tmp_path, monkeypatch):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        af.save_xdxr_cache("000002", [], fetched_at=af.cn_now().isoformat(),
                           shares=af.normalize_shares(self._raw()))
        v = af.total_shares_at("000002", "2026-08-04", field="float_shares")
        assert v == pytest.approx(971000e4)

    def test_cache_holds_both_kinds(self, tmp_path, monkeypatch):
        """一份缓存装两类——两者来自同一次 xdxr 调用，分开存会多跑一次网络。"""
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        af.save_xdxr_cache("600000", [_ev("2025-07-16", fenhong=4.2)],
                           fetched_at="x", shares=[{"date": "2025-01-01",
                                                    "total_shares": 1e10,
                                                    "float_shares": 1e10}])
        d = json.loads((tmp_path / "600000.json").read_text(encoding="utf-8"))
        assert len(d["events"]) == 1 and len(d["shares"]) == 1

    def test_saving_events_preserves_existing_shares(self, tmp_path, monkeypatch):
        """只更新权息时不得把已有股本数据覆盖没了。"""
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        af.save_xdxr_cache("600000", [], fetched_at="x",
                           shares=[{"date": "2025-01-01", "total_shares": 1e10,
                                    "float_shares": 1e10}])
        af.save_xdxr_cache("600000", [_ev("2026-07-16", fenhong=4.2)], fetched_at="y")
        d = json.loads((tmp_path / "600000.json").read_text(encoding="utf-8"))
        assert len(d["shares"]) == 1, "股本数据被覆盖丢失"
        assert len(d["events"]) == 1


class TestMarketCapFromTdx:
    def test_contract_matches_eastmoney_path(self, monkeypatch):
        """本地路径必须产出与东财路径同样的事件契约，否则下游 load_events 读不了。"""
        from local_tdx import fetch_market_cap as fmc

        monkeypatch.setattr(
            "local_tdx.adjust_factors.get_shares_events",
            lambda code, refresh=False: [
                {"date": "2020-05-20", "total_shares": 1.13e10, "float_shares": 9.7e9},
                {"date": "2023-06-30", "total_shares": 1.16e10, "float_shares": 9.71e9}])
        evs = fmc.build_from_tdx(["000002"], progress_every=0)
        assert len(evs) == 2
        for e in evs:
            assert set(e) >= {"code", "name", "observed_on", "prev_sample",
                              "total_shares", "prev_shares", "free_shares",
                              "close", "market_cap", "kind"}
        assert evs[0]["kind"] == "first_seen" and evs[1]["kind"] == "change"
        assert evs[1]["prev_shares"] == pytest.approx(1.13e10)
        assert evs[0]["source"] == "tdx_xdxr"

    def test_unchanged_shares_not_written(self, monkeypatch):
        from local_tdx import fetch_market_cap as fmc
        monkeypatch.setattr(
            "local_tdx.adjust_factors.get_shares_events",
            lambda code, refresh=False: [
                {"date": "2020-05-20", "total_shares": 1.13e10, "float_shares": None},
                {"date": "2021-05-20", "total_shares": 1.13e10, "float_shares": None}])
        evs = fmc.build_from_tdx(["000002"], progress_every=0)
        assert len(evs) == 1, "股本没变不该写事件"

    def test_failure_is_skipped_not_fatal(self, monkeypatch):
        from local_tdx import fetch_market_cap as fmc

        def boom(code, refresh=False):
            raise af.AdjustError("down")
        monkeypatch.setattr("local_tdx.adjust_factors.get_shares_events", boom)
        assert fmc.build_from_tdx(["000002"], progress_every=0) == []


class TestClientReconnect:
    """「mootdx 名称源 2026-07 起持续失败」的真实原因：客户端永不重连。

    连接一断，stock_count() 返回 None，mootdx 内部 `if counts > 0` 抛 `'>' NoneType`。
    当时把它归因成「接口失效」并改走东财 HTTP，真正的 bug 留了一个月。
    """

    def test_retry_rebuilds_connection(self, monkeypatch):
        from local_tdx import local_tdx_data as ltd
        built = []

        class Good:
            def stocks(self, market=0):
                return "ok"

            def close(self):
                pass

        class Dead:
            def stocks(self, market=0):
                raise TypeError("'>' not supported between 'NoneType' and 'int'")

            def close(self):
                pass

        seq = [Dead(), Good()]

        def fake_factory(**kw):
            built.append(1)
            return seq[min(len(built) - 1, len(seq) - 1)]

        monkeypatch.setattr(ltd, "_client", None)
        monkeypatch.setattr(ltd, "_client_created_at", None)
        import mootdx.quotes as mq
        monkeypatch.setattr(mq.Quotes, "factory", staticmethod(fake_factory))
        got = ltd._with_client_retry(lambda c: c.stocks(market=1), tries=2)
        assert got == "ok"
        assert len(built) == 2, "第二次必须**重建**连接，用同一个死连接重试没意义"

    def test_raises_after_exhausting_tries(self, monkeypatch):
        from local_tdx import local_tdx_data as ltd

        class Dead:
            def stocks(self, market=0):
                raise OSError("connection reset")

            def close(self):
                pass

        monkeypatch.setattr(ltd, "_client", None)
        monkeypatch.setattr(ltd, "_client_created_at", None)
        import mootdx.quotes as mq
        monkeypatch.setattr(mq.Quotes, "factory", staticmethod(lambda **kw: Dead()))
        with pytest.raises(ltd.LocalTdxError):
            ltd._with_client_retry(lambda c: c.stocks(market=1), tries=2)

    def test_connection_expires_by_age(self, monkeypatch):
        """长跑进程（18:00 跑几百只票）中途连接会被服务器踢，靠时效主动重建。"""
        from local_tdx import local_tdx_data as ltd
        built = []

        class C:
            def close(self):
                pass

        monkeypatch.setattr(ltd, "_client", C())
        monkeypatch.setattr(ltd, "_client_created_at", 0.0)     # 很久以前
        import mootdx.quotes as mq
        monkeypatch.setattr(mq.Quotes, "factory",
                            staticmethod(lambda **kw: built.append(1) or C()))
        ltd._get_client()
        assert built, "超过 CLIENT_MAX_AGE_SEC 应重建"


class TestCliFullMarketPath:
    """CLI 的「全市场」路径必须真的能跑。

    2026-08-04 实盘踩过：`refresh_xdxr` stage 报
    `module 'local_tdx_data' has no attribute 'list_local_codes'` —— 正确名字是
    `list_local_vipdoc_codes`，我在三个文件里都写错了。
    根因是本机无 vipdoc 数据，测试时全都传 `--codes` 绕过，这条路径**从未被执行**。
    所以这里只断言"函数名存在"，不需要真数据也能挡住同类错误。
    """

    def test_referenced_lister_exists(self):
        from local_tdx import local_tdx_data as ltd
        assert hasattr(ltd, "list_local_vipdoc_codes")

    @pytest.mark.parametrize("mod", [
        "07_tools/local_tdx/adjust_factors.py",
        "07_tools/local_tdx/fetch_market_cap.py",
        "07_tools/research/adjust_diagnostic.py",
    ])
    def test_no_stale_function_name(self, mod):
        import pathlib
        src = pathlib.Path(mod).read_text(encoding="utf-8")
        assert "list_local_codes()" not in src, f"{mod} 用了不存在的 list_local_codes"

    @pytest.mark.parametrize("mod", [
        "07_tools/local_tdx/adjust_factors.py",
        "07_tools/local_tdx/fetch_market_cap.py",
        "07_tools/research/adjust_diagnostic.py",
    ])
    def test_lister_attribute_resolvable(self, mod):
        """更强的检查：脚本里**调用**的 local_tdx_data.X() 都必须真实存在。

        只匹配调用形式（后跟左括号）——否则注释里的 `local_tdx_data.py` 会被
        当成属性 `py` 而误报。
        """
        import pathlib
        import re

        from local_tdx import local_tdx_data as ltd
        src = pathlib.Path(mod).read_text(encoding="utf-8")
        called = set(re.findall(r"local_tdx_data\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", src))
        assert called, f"{mod} 没有调用 local_tdx_data 的任何函数（测试假设失效）"
        for attr in called:
            assert hasattr(ltd, attr), f"{mod} 调用了不存在的 local_tdx_data.{attr}()"


class TestBjMarketRouting:
    """BJ 权息取数必须**自己判 market**，不能用 mootdx 的推断。

    `mootdx.utils.get_stock_market` 的规则是「'5'/'6'/'9' 开头为 sh，其余为 sz」，
    于是北交所新代码段 `920xxx` 被判成**沪市**（老 BJ 段 43/83/87 判对了）。
    而 `q.xdxr(symbol=...)` 内部用的就是它 ⇒ 查 `SH:920808` 的权息，服务器返回空。

    实测对照（2026-08-06，同一台服务器连测 5 次稳定）：

        get_xdxr_info(1, "920808") →  0 条
        get_xdxr_info(2, "920808") → **24 条**（8 条影响价格 + 16 条股本变化）

    后果：`get_xdxr(BJ)` 返回 `[]` 而不报错 ⇒ `qfq_table` 走成功路径 ⇒
    `apply_qfq` 盖章 `adjust="qfq"` 而价格一字未改 ⇒ **未复权数据被标成已前复权**。
    920808 实测首根因子 **0.0403** —— 未复权价是复权价的约 25 倍，
    任何除权日在样本里都长得像 -96% 暴跌。BJ 约占 universe 4.8%。
    """

    @pytest.mark.parametrize("code,want", [
        ("600000", 1), ("601398", 1), ("688001", 1),      # 沪
        ("000001", 0), ("002415", 0), ("300750", 0),      # 深
        ("920808", 2), ("920002", 2),                     # 北（新代码段，mootdx 判错的）
        ("830799", 2), ("870204", 2), ("430047", 2),      # 北（老代码段）
        ("880005", 1),                                    # ⚠️ 沪市统计指数，不是北交所
    ])
    def test_market_mapping(self, code, want):
        assert af._tdx_market(code) == want

    def test_unknown_code_raises_instead_of_guessing(self):
        """判不出交易所时必须报错——用错 market 查出来的空结果会被当成「没有除权」。"""
        with pytest.raises(af.AdjustError):
            af._tdx_market("XYZ")

    def test_does_not_use_mootdx_inference(self):
        """回归：源码里不得再出现 `q.xdxr(symbol=` 调用（它会走内部推断）。"""
        import pathlib
        src = pathlib.Path(af.__file__).read_text(encoding="utf-8")
        calls = [ln for ln in src.splitlines()
                 if "q.xdxr(symbol=" in ln and not ln.strip().startswith(("#", "`"))
                 and "而 `q.xdxr" not in ln]
        assert not calls, f"仍在用 mootdx 的 market 推断: {calls}"


class TestXdxrCacheMarketStamp:
    """缓存必须记 `market`，且**缺 market 的空事件缓存一律作废**。

    2026-08-06 之前 BJ 查到 0 条并**把空结果缓存了下来**，而缓存策略是
    「除权是历史事实，不会变」⇒ 永不过期 ⇒ 那些票会永远按未复权处理。
    """

    def test_save_stamps_market(self, tmp_path, monkeypatch):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        af.save_xdxr_cache("920808", [], fetched_at="2026-08-06T12:00:00+08:00")
        d = json.loads((tmp_path / "920808.json").read_text(encoding="utf-8"))
        assert d["market"] == 2

    def test_empty_cache_without_market_is_discarded(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        (tmp_path / "920808.json").write_text(json.dumps(
            {"code": "920808", "events": [], "n": 0}), encoding="utf-8")
        assert af.load_xdxr_cache("920808") is None
        assert "缺 market 标记" in capsys.readouterr().err

    def test_empty_cache_with_market_is_trusted(self, tmp_path, monkeypatch):
        """带 market 的空事件是真查过的结论（该票确实没除权），不该反复重取。"""
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        (tmp_path / "600000.json").write_text(json.dumps(
            {"code": "600000", "events": [], "n": 0, "market": 1}), encoding="utf-8")
        assert af.load_xdxr_cache("600000") == []

    def test_nonempty_old_cache_still_usable(self, tmp_path, monkeypatch):
        """非空的老缓存是真查到的事件，不能因为缺 market 就丢掉。"""
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        ev = [{"date": "2020-07-09", "fenhong": 5.1, "songzhuangu": 0.0,
               "peigu": 0.0, "peigujia": 0.0, "suogu": 0.0}]
        (tmp_path / "600000.json").write_text(json.dumps(
            {"code": "600000", "events": ev, "n": 1}), encoding="utf-8")
        assert af.load_xdxr_cache("600000") == ev


class TestNormalizeAcceptsRawList:
    """`normalize_*` 必须兼容 list —— 直调 `get_xdxr_info` 返回 list[OrderedDict]，
    而 `q.xdxr()` 返回 DataFrame。原实现只做 `df.to_dict("records")`，
    喂 list 会异常并 `return []` ⇒ 又是一次静默降级。"""

    def _raw(self):
        return [
            {"year": 2018, "month": 7, "day": 2, "category": 1, "name": "除权除息",
             "fenhong": 2.4, "songzhuangu": 30.75, "peigu": 0.0, "peigujia": 0.0,
             "suogu": 0.0, "houzongguben": None, "panhouliutong": None},
            {"year": 2018, "month": 6, "day": 12, "category": 5, "name": "股本变化",
             "fenhong": None, "songzhuangu": None, "peigu": None, "peigujia": None,
             "suogu": None, "houzongguben": 736.196, "panhouliutong": 304.349},
        ]

    def test_xdxr_from_list(self):
        ev = af.normalize_xdxr(self._raw())
        assert len(ev) == 1 and ev[0]["date"] == "2018-07-02"
        assert ev[0]["songzhuangu"] == pytest.approx(30.75)

    def test_shares_from_list(self):
        sh = af.normalize_shares(self._raw())
        assert len(sh) == 1 and sh[0]["date"] == "2018-06-12"
        assert sh[0]["total_shares"] == pytest.approx(736.196 * 10000)

    def test_empty_and_none_safe(self):
        for x in (None, [], [{}]):
            assert af.normalize_xdxr(x) == []
            assert af.normalize_shares(x) == []


class TestCacheAgeAndStaleness:
    """权息缓存年龄 —— `stale_codes` 靠它决定 18:00 选股链要重取哪些票。"""

    def _write(self, tmp_path, monkeypatch, code, payload):
        import json
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        p = tmp_path / f"{code}.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return p

    def test_absent_cache_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        assert af.cache_age_days("600000") is None

    def test_missing_fetched_at_is_none(self, tmp_path, monkeypatch):
        """⚠️ 缓存存在但没有 `fetched_at` ⇒ 返回 None（**视为需要刷新**）。

        返回 0 会让它看起来「刚取的」而永不刷新 —— 那才是危险的方向。
        """
        self._write(tmp_path, monkeypatch, "600000", {"events": []})
        assert af.cache_age_days("600000") is None

    def test_corrupt_json_is_none_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        (tmp_path / "600000.json").write_text("{ not json", encoding="utf-8")
        assert af.cache_age_days("600000") is None

    def test_fresh_cache_age_near_zero(self, tmp_path, monkeypatch):
        from paths import cn_now
        self._write(tmp_path, monkeypatch, "600000",
                    {"fetched_at": cn_now().isoformat(timespec="seconds")})
        age = af.cache_age_days("600000")
        assert age is not None and age < 0.01

    def test_naive_timestamp_is_treated_as_local(self, tmp_path, monkeypatch):
        """⚠️ 无时区的 `fetched_at` 按本地时区处理 —— 否则会算出 ±8 小时的偏差，
        在 `max_age_days=7` 这种粒度上不致命，但会让「刚取的」显示成负年龄。"""
        from datetime import timedelta

        from paths import cn_now
        naive = (cn_now() - timedelta(days=2)).replace(tzinfo=None).isoformat(timespec="seconds")
        self._write(tmp_path, monkeypatch, "600000", {"fetched_at": naive})
        age = af.cache_age_days("600000")
        assert age is not None and 1.9 < age < 2.1, age

    def test_stale_codes_picks_absent_and_old(self, tmp_path, monkeypatch):
        from datetime import timedelta

        from paths import cn_now
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        self._write(tmp_path, monkeypatch, "600000",
                    {"fetched_at": cn_now().isoformat(timespec="seconds")})
        self._write(tmp_path, monkeypatch, "000001",
                    {"fetched_at": (cn_now() - timedelta(days=30)).isoformat(timespec="seconds")})
        out = af.stale_codes(["600000", "000001", "600519"], max_age_days=7.0)
        assert "600000" not in out, "新缓存不该被重取"
        assert "000001" in out and "600519" in out, f"旧缓存与无缓存都要重取：{out}"


class TestGetXdxrCacheFirst:
    """⚠️ 权息**默认优先缓存** —— 除权是历史事实，不会变。

    这不是性能优化而已：`Quotes.factory()` 每次都要选 bestip + 建 TCP 连接，
    18:00 选股链几百只候选逐只新建连接会把 <1s 拖成几分钟（源码注释）。
    """

    def test_cache_hit_does_not_touch_network(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        (tmp_path / "600000.json").write_text(json.dumps(
            {"fetched_at": "2026-08-01T00:00:00+08:00",
             "events": [{"date": "20260610", "category": 1, "songzhuangu": 0.0,
                         "fenhong": 1.0, "peigu": 0.0, "peigujia": 0.0}]}),
            encoding="utf-8")

        def boom(*a, **k):
            raise AssertionError("命中缓存时不该建连接")
        monkeypatch.setattr(af, "_tdx_market", boom)
        ev = af.get_xdxr("600000")
        assert ev and ev[0]["date"] == "20260610"

    def test_refresh_true_bypasses_cache(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        (tmp_path / "600000.json").write_text(json.dumps(
            {"fetched_at": "2026-08-01T00:00:00+08:00", "events": []}),
            encoding="utf-8")
        called = {"n": 0}

        def mark(c):
            called["n"] += 1
            raise RuntimeError("stop here")
        monkeypatch.setattr(af, "_tdx_market", mark)
        with pytest.raises(af.AdjustError):
            af.get_xdxr("600000", refresh=True)
        assert called["n"] == 1, "refresh=True 必须绕过缓存去取数"

    def test_fetch_failure_raises_adjust_error_not_bare(self, tmp_path, monkeypatch):
        """⚠️ 取数失败必须包成 `AdjustError` 并带代码 —— 裸异常在批量路径里
        会丢掉「是哪只票」这个信息。"""
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(af, "_tdx_market",
                            lambda c: (_ for _ in ()).throw(RuntimeError("net down")))
        with pytest.raises(af.AdjustError) as e:
            af.get_xdxr("600519")
        assert "600519" in str(e.value)


class TestSharesEventsCacheShared:
    """股本事件与权息**共用一份缓存文件** —— 避免两次取数（源码注释）。"""

    def test_shares_read_from_same_cache(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        (tmp_path / "600000.json").write_text(json.dumps(
            {"fetched_at": "2026-08-01T00:00:00+08:00", "events": [],
             "shares": [{"date": "20260610", "total": 1.0e10}]}),
            encoding="utf-8")

        def boom(*a, **k):
            raise AssertionError("共用缓存时不该重新取数")
        monkeypatch.setattr(af, "_tdx_market", boom)
        sh = af.get_shares_events("600000")
        assert sh and sh[0]["total"] == 1.0e10

    def test_missing_shares_key_falls_through_to_fetch(self, tmp_path, monkeypatch):
        """⚠️ 缓存里没有 `shares` 键时**必须去取**，不能返回空列表 ——
        空列表会让 `total_shares_at` 算出 None 而市值静默变缺失
        （`_shares` 曾因漏 `import json` 让 mcap 静默失效，同一类）。"""
        import json
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        (tmp_path / "600000.json").write_text(json.dumps(
            {"fetched_at": "2026-08-01T00:00:00+08:00", "events": []}),
            encoding="utf-8")
        monkeypatch.setattr(af, "_tdx_market",
                            lambda c: (_ for _ in ()).throw(RuntimeError("net")))
        with pytest.raises(af.AdjustError):
            af.get_shares_events("600000")


class TestFetchBatchReusesConnection:
    """批量取数**复用同一个连接** —— 这是它存在的唯一理由。"""

    def test_single_factory_call_for_many_codes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)
        factories = {"n": 0}

        class FakeClient:
            def get_xdxr_info(self, market, code):
                return []

        class FakeQuotes:
            client = FakeClient()

        class Factory:
            @staticmethod
            def factory(**kw):
                factories["n"] += 1
                return FakeQuotes()

        import types
        mod = types.ModuleType("mootdx.quotes")
        mod.Quotes = Factory
        monkeypatch.setitem(sys.modules, "mootdx.quotes", mod)
        out = af.fetch_xdxr_batch(["600000", "000001", "600519"])
        assert factories["n"] == 1, f"应只建一次连接，实际 {factories['n']}"
        assert set(out) == {"600000", "000001", "600519"}

    def test_empty_codes_returns_empty_without_connecting(self, monkeypatch):
        def boom(**k):
            raise AssertionError("空清单不该建连接")
        import types
        mod = types.ModuleType("mootdx.quotes")
        mod.Quotes = type("Q", (), {"factory": staticmethod(boom)})
        monkeypatch.setitem(sys.modules, "mootdx.quotes", mod)
        assert af.fetch_xdxr_batch([]) == {}

    def test_on_error_skip_continues(self, tmp_path, monkeypatch):
        """⚠️ `on_error="skip"` 时单只失败跳过并计数 —— 一只票取不到
        不该让几百只的批量全废。"""
        monkeypatch.setattr(af, "CACHE_DIR", tmp_path)

        class FakeClient:
            def get_xdxr_info(self, market, code):
                if code == "000001":
                    raise RuntimeError("that one fails")
                return []

        import types
        mod = types.ModuleType("mootdx.quotes")
        mod.Quotes = type("Q", (), {"factory": staticmethod(
            lambda **k: type("Q2", (), {"client": FakeClient()})())})
        monkeypatch.setitem(sys.modules, "mootdx.quotes", mod)
        out = af.fetch_xdxr_batch(["600000", "000001", "600519"], on_error="skip")
        assert "000001" not in out
        assert set(out) == {"600000", "600519"}
