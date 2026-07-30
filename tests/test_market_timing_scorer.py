# -*- coding: utf-8 -*-
"""market_timing_scorer 首批测试——它给出 100 分制择时评分，此前 0% 覆盖。

重点:①各模块评分边界;②**缺数不得当成好数**(中性半分且理由写明);
③**stale(数据日非当日)不得按当日给分**——T-1 涨跌比曾能拿满分。
"""
from __future__ import annotations

from market_timing import market_timing_scorer as sc


class TestFnum:
    def test_parses_and_tolerates_garbage(self):
        assert sc.fnum("1.5") == 1.5
        assert sc.fnum(None) is None
        assert sc.fnum("n/a") is None


class TestScoreAmv:
    def test_bear_regime_scores_zero(self):
        s, note = sc.score_amv({"amv_0": {"effective_state": "空头", "amv_change_pct": -3.0}})
        assert s == 0 and "空头" in note

    def test_long_regime_scores_full(self):
        s, _ = sc.score_amv({"amv_0": {"effective_state": "做多", "amv_change_pct": 5.0}})
        assert s == 15

    def test_missing_value_is_neutral_half(self):
        s, note = sc.score_amv({})
        assert s == 7.5 and "中性半分" in note

    def test_unlocked_positive_and_negative(self):
        assert sc.score_amv({"amv_0": {"amv_change_pct": 1.0}})[0] == 9
        assert sc.score_amv({"amv_0": {"amv_change_pct": -1.0}})[0] == 5


class TestScoreOverseas:
    def test_missing_is_neutral_half(self):
        s, note = sc.score_overseas({})
        assert s == 5 and "中性半分" in note

    def test_monotonic_in_average(self):
        def _s(v):
            return sc.score_overseas({"overseas_market": {"nasdaq_change_pct": v}})[0]
        assert _s(2.0) > _s(0.5) > _s(0.0) > _s(-0.5) > _s(-2.0)


class TestScoreBreadth:
    def _d(self, up, down, **kw):
        return {"market_breadth": {"up_count": up, "down_count": down, **kw}}

    def test_missing_is_neutral(self):
        assert sc.score_breadth({})[0] == 7.5
        assert sc.score_breadth(self._d(100, 0))[0] == 7.5     # 除零保护

    def test_ratio_bands_monotonic(self):
        assert sc.score_breadth(self._d(3000, 1000))[0] == 15
        assert sc.score_breadth(self._d(1300, 1000))[0] == 11
        assert sc.score_breadth(self._d(900, 1000))[0] == 8
        assert sc.score_breadth(self._d(600, 1000))[0] == 5
        assert sc.score_breadth(self._d(300, 1000))[0] == 2

    def test_candidate_quality_is_averaged_with_neutral(self):
        s, note = sc.score_breadth(self._d(3000, 1000, quality="candidate"))
        assert s == 11.25 and "候选口径" in note

    def test_stale_data_scores_neutral_not_full(self):
        """核心回归:数据日非当日时不得按满分计,否则 T-1 宽度会撑起当日评分。"""
        s, note = sc.score_breadth(self._d(3000, 1000, quality="stale", as_of="2026-07-17"))
        assert s == 7.5
        assert "stale" in note and "2026-07-17" in note


class TestScoreTurnoverAndSentiment:
    def test_turnover_bands(self):
        def _s(chg, **kw):
            return sc.score_turnover({"turnover": {"turnover_change_pct": chg, **kw}})[0]
        assert _s(20) == 8 and _s(10) == 6 and _s(0) == 4 and _s(-10) == 3 and _s(-20) == 1

    def test_turnover_missing_and_stale(self):
        assert sc.score_turnover({})[0] == 4
        s, note = sc.score_turnover({"turnover": {"turnover_change_pct": 30, "quality": "stale"}})
        assert s == 4 and "stale" in note

    def test_sentiment_clamped_and_stale(self):
        hot = sc.score_sentiment({"sentiment": {"limit_up_count": 120, "limit_down_count": 0,
                                                "blowup_rate": 0.05, "market_height": 6}})[0]
        cold = sc.score_sentiment({"sentiment": {"limit_up_count": 10, "limit_down_count": 60,
                                                 "blowup_rate": 0.6, "market_height": 1}})[0]
        assert 0 <= cold < hot <= 15
        s, note = sc.score_sentiment({"sentiment": {"limit_up_count": 120, "limit_down_count": 0,
                                                    "quality": "stale"}})
        assert s == 7.5 and "stale" in note


class TestStatusFromScore:
    def test_monotonic_labels(self):
        labels = [sc.status_from_score(x)[0] for x in (95, 75, 55, 35, 10)]
        assert len(set(labels)) >= 3            # 不同分档给出不同结论
        assert all(isinstance(x, str) and x for x in labels)

    def test_extremes_do_not_raise(self):
        for v in (0, 0.0, 100, 100.0, 49.9999):
            assert isinstance(sc.status_from_score(v), tuple)


class TestIsStale:
    def test_only_stale_quality_flagged(self):
        assert sc.is_stale({"quality": "stale"}) is True
        for q in ("auto", "confirmed", "candidate", None):
            assert sc.is_stale({"quality": q}) is False
        assert sc.is_stale({}) is False
        assert sc.is_stale(None) is False


def test_stale_as_of_auto_quality_scores_neutral():
    """生产形态回归(H1):collector 取上一根 K 线 → quality=auto + as_of=T-1。
    仅看 quality 会漏,必须按 as_of!=date 判陈旧,否则 T-1 宽度照样拿满分。"""
    d = {"date": "2026-07-30",
         "market_breadth": {"up_count": 3000, "down_count": 1000, "quality": "auto",
                            "as_of": "2026-07-29"}}
    s, note = sc.score_breadth(d)
    assert s == 7.5 and "stale" in note and "2026-07-29" in note
    d2 = {"date": "2026-07-30",
          "market_breadth": {"up_count": 3000, "down_count": 1000, "quality": "auto",
                             "as_of": "2026-07-30"}}
    assert sc.score_breadth(d2)[0] == 15        # as_of 当日 → 正常给分
