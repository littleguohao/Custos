# -*- coding: utf-8 -*-
"""merge_incremental_market 新鲜度测试——T-1 数据不得冒充当日(必须标 stale)。"""
from __future__ import annotations

from market_timing import merge_incremental_market as mim

TARGET = "2026-07-20"


def _inc(day: str):
    return {"breadth": {
        "880005": {"date": day, "up_count": 3000, "down_count": 1500},
        "880006": {"date": day, "close": 42},
        "880001": {"date": day, "amount": 1.2e12, "previous_amount": 1.0e12},
    }}


def test_section_quality_same_day_is_auto():
    assert mim.section_quality(TARGET, TARGET) == "auto"
    assert mim.section_quality("2026-07-20T15:00:00", TARGET) == "auto"


def test_section_quality_other_day_is_stale():
    assert mim.section_quality("2026-07-17", TARGET) == "stale"


def test_section_quality_missing_as_of_is_raw_only():
    assert mim.section_quality("", TARGET) == "raw_only"


def test_current_day_data_marked_auto():
    mkt, stale = mim.merge_incremental(_inc(TARGET), {}, TARGET)
    assert stale == []
    assert mkt["market_breadth"]["quality"] == "auto"
    assert mkt["sentiment"]["quality"] == "auto"
    assert mkt["turnover"]["quality"] == "auto"
    assert mkt["market_turnover"]["quality"] == "auto"
    assert mkt["turnover"]["turnover_change_pct"] == 20.0


def test_prior_day_data_marked_stale_and_reported():
    """核心回归:TdxW 未刷新时 collect 取到上一根 K 线,以前会写 quality=auto 满分通过。"""
    mkt, stale = mim.merge_incremental(_inc("2026-07-17"), {}, TARGET)
    assert mkt["market_breadth"]["quality"] == "stale"
    assert mkt["sentiment"]["quality"] == "stale"
    assert mkt["turnover"]["quality"] == "stale"
    assert mkt["market_turnover"]["quality"] == "stale"
    assert {s.split("(")[0] for s in stale} == {"market_breadth", "sentiment", "turnover"}
    assert all("2026-07-17" in s for s in stale)


def test_existing_sections_not_overwritten():
    """只增不毁:已有字段保持原样(setdefault 语义)。"""
    mkt = {"market_breadth": {"quality": "confirmed", "up_count": 1}}
    out, _ = mim.merge_incremental(_inc(TARGET), mkt, TARGET)
    assert out["market_breadth"] == {"quality": "confirmed", "up_count": 1}


def test_overseas_and_northbound_merge():
    inc = {"a50_futures": {"change_pct": 0.8}, "cnh_usd": {"change_pct": None},
           "northbound": {"net": 12.3}}
    out, _ = mim.merge_incremental(inc, {}, TARGET)
    assert out["overseas_market"]["a50_change_pct"] == 0.8
    assert "cnh_change_pct" not in out["overseas_market"]      # None 不写入
    assert out["northbound"] == {"net": 12.3}
