# -*- coding: utf-8 -*-
"""海外行情 TDX 扩展市场 fallback 测试。

定位是**降级不是替代**：ext 市场覆盖不全（无 A50 期货、无 USDCNH 汇率、无指数本身，
指数只能用 ETF 代理），所以 Yahoo 仍是主路径。这套测试主要钉住两件事：
① 不支持的品种要老实返回 None，不能编数据；
② 用了代理必须留痕（proxy/proxy_note/fallback_source），不能让读报告的人
   把 ETF 涨跌当成指数涨跌。
"""
from __future__ import annotations

import pandas as pd
import pytest

from market_timing import tdx_ext_quotes as t


class TestCoverage:
    def test_us_singles_are_not_proxies(self):
        """美股个股口径一致，不该标 proxy。"""
        for s in ("NVDA", "AMD", "TSM"):
            assert t.EXT_MAP[s][2] is False

    def test_indices_are_marked_as_proxies(self):
        """指数只能用 ETF 代理，必须标出来。"""
        for s in ("^DJI", "^IXIC", "^GSPC", "^SOX"):
            market, code, is_proxy, note = t.EXT_MAP[s]
            assert is_proxy is True
            assert note, f"{s} 缺少代理说明"

    def test_unsupported_symbols_absent(self):
        """A50 / 汇率 / 日经 / KOSPI 在 ext 里没有，不能假装支持。"""
        for s in ("^N225", "^KS11", "005930.KS", "000660.KS"):
            assert s not in t.EXT_MAP

    def test_unsupported_returns_none(self):
        assert t.fetch_ext_change("^N225") is None
        assert t.fetch_ext_change("NOT_A_SYMBOL") is None


class TestFetch:
    def _stub(self, monkeypatch, closes):
        class Q:
            def bars(self, symbol=None, market=None, frequency=None, offset=None):
                if closes is None:
                    return None
                return pd.DataFrame({"close": closes})
        monkeypatch.setattr(t, "_get_ext_client", lambda timeout=12: Q())

    def test_change_pct_computed(self, monkeypatch):
        self._stub(monkeypatch, [100.0, 100.0, 102.0])
        r = t.fetch_ext_change("NVDA")
        assert r["change_pct"] == pytest.approx(2.0)
        assert r["proxy"] is False

    def test_proxy_note_carried(self, monkeypatch):
        self._stub(monkeypatch, [100.0, 101.0])
        r = t.fetch_ext_change("^GSPC")
        assert r["proxy"] is True and "SPY" in r["proxy_note"]

    @pytest.mark.parametrize("closes", [None, [], [100.0], [0.0, 0.0]])
    def test_insufficient_data_returns_none(self, monkeypatch, closes):
        self._stub(monkeypatch, closes)
        assert t.fetch_ext_change("NVDA") is None

    def test_exception_returns_none_not_raise(self, monkeypatch):
        def boom(timeout=12):
            raise OSError("ext down")
        monkeypatch.setattr(t, "_get_ext_client", boom)
        assert t.fetch_ext_change("NVDA") is None


class TestCollectorIntegration:
    def test_fallback_marks_degraded_and_source(self, monkeypatch, tmp_path):
        """Yahoo 挂了走 ext 时，必须留下 degraded 与 fallback_source 供下游归因。"""
        from market_timing import overseas_market_collector as omc

        monkeypatch.setattr(omc, "SYMBOLS",
                            {"nvda": {"symbol": "NVDA", "name": "英伟达",
                                      "group": "ai_leader"}})
        monkeypatch.setattr(omc, "FIELD_MAP", {"nvda": "nvda_change_pct"})

        def yahoo_down(symbol, region=""):
            raise OSError("yahoo 404")
        monkeypatch.setattr(omc, "fetch_chart", yahoo_down)
        monkeypatch.setattr(t, "fetch_ext_change",
                            lambda s, **kw: {"change_pct": 1.5, "last_close": 208.0,
                                             "prev_close": 204.9,
                                             "source": "TDX ext (market=74, NVDA)",
                                             "proxy": False, "proxy_note": ""})
        out = tmp_path / "o.json"
        import sys as _s
        monkeypatch.setattr(_s, "argv",
                            ["x", "--date", "2026-08-04", "--input", str(out)])
        try:
            omc.main()
        except SystemExit:
            pass
        import json
        d = json.loads(out.read_text(encoding="utf-8"))
        ov = d["overseas_market"]
        assert ov["nvda_change_pct"] == 1.5
        assert ov["details"]["nvda"]["degraded"] is True
        assert "yahoo_error" in ov["details"]["nvda"]
        assert "fallback_source" in ov and "TDX ext" in ov["source"]

    def test_no_fallback_key_when_yahoo_works(self, monkeypatch, tmp_path):
        from market_timing import overseas_market_collector as omc
        monkeypatch.setattr(omc, "SYMBOLS",
                            {"nvda": {"symbol": "NVDA", "name": "英伟达",
                                      "group": "ai_leader"}})
        monkeypatch.setattr(omc, "FIELD_MAP", {"nvda": "nvda_change_pct"})
        monkeypatch.setattr(omc, "fetch_chart",
                            lambda symbol, region="": {"change_pct": 0.8,
                                                       "source": "Yahoo Finance chart API"})
        out = tmp_path / "o.json"
        import sys as _s
        monkeypatch.setattr(_s, "argv",
                            ["x", "--date", "2026-08-04", "--input", str(out)])
        try:
            omc.main()
        except SystemExit:
            pass
        import json
        ov = json.loads(out.read_text(encoding="utf-8"))["overseas_market"]
        assert "fallback_source" not in ov
        assert ov["source"] == "Yahoo Finance chart API"
