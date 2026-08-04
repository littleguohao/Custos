# -*- coding: utf-8 -*-
"""前复权（qfq）回归测试。owner 2026-08-04 拍板：全链统一前复权。

为什么这套测试值得写细：未复权数据的错误**不会报错、不会崩溃**，只会让回测数字
悄悄偏掉（实测同一段真实上涨走势，未复权 −42.50% vs 前复权 +25.00%，差 67.5pp）。
这正是本次审计反复遇到的最危险失效模式，只能靠断言钉住。
"""
from __future__ import annotations

import json

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
