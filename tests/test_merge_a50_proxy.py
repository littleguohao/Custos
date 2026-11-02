"""A50 指数代理身份随合并带入 overseas_market（键名沿用历史契约，v0.474）。"""

from custos.pipeline.market_timing import merge_incremental_market as mim


def test_merge_carries_a50_proxy_flag():
    inc = {
        "a50_futures": {
            "change_pct": 0.8,
            "proxy": True,
            "proxy_note": "富时中国A50指数（非A50期货）",
        }
    }
    mkt, _ = mim.merge_incremental(inc, {}, "2026-10-30")
    ov = mkt["overseas_market"]
    assert ov["a50_change_pct"] == 0.8
    assert ov["a50_is_proxy"] is True and "非A50期货" in ov["a50_proxy_note"]


def test_merge_non_proxy_and_missing_value_unchanged():
    mkt, _ = mim.merge_incremental(
        {"a50_futures": {"change_pct": 1.1}}, {}, "2026-10-30"
    )
    assert mkt["overseas_market"] == {"a50_change_pct": 1.1}
    mkt, _ = mim.merge_incremental(
        {"a50_futures": {"status": "unavailable"}}, {}, "2026-10-30"
    )
    assert "overseas_market" not in mkt
