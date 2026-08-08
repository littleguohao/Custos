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


class TestReconnect:
    """连接管理(DATA_SOURCE_PRINCIPLE):判死重建 + 超龄重建,不允许"建一次用一辈子"。"""

    def test_dead_connection_retried_with_fresh_client(self, monkeypatch):
        """第一次调用连接异常 ⇒ 丢弃缓存重建后再试一次,而不是整个进程余生静默失效。"""
        calls = {"n": 0}

        class Q:
            def bars(self, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("dead")
                return pd.DataFrame({"close": [100.0, 101.0]})

        monkeypatch.setattr(t, "_get_ext_client", lambda timeout=12: Q())
        r = t.fetch_ext_change("NVDA")
        assert r is not None and r["change_pct"] == pytest.approx(1.0)
        assert calls["n"] == 2

    def test_stale_client_rebuilt(self, monkeypatch):
        import time as _t

        built = {"n": 0}

        class FakeQuotes:
            @staticmethod
            def factory(market=None, timeout=None):
                built["n"] += 1
                return object()

        import mootdx.quotes as mq
        monkeypatch.setattr(mq, "Quotes", FakeQuotes)
        monkeypatch.setattr(t, "_client", None)
        monkeypatch.setattr(t, "_client_created_at", 0.0)
        t._get_ext_client()
        t._get_ext_client()
        assert built["n"] == 1, "未超龄必须复用缓存"
        monkeypatch.setattr(t, "_client_created_at", _t.monotonic() - 9999)
        t._get_ext_client()
        assert built["n"] == 2, "超龄必须重建"


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


class TestOverseasAsOfDerivation:
    """⚠️ `overseas_market.as_of` 的**推导**此前零测试覆盖。

    检索到的 `as_of` 出现在 6 个地方，全部是**手写给消费者**
    （`runtime_guards`）的输入，从没有人验证生产者是怎么算出来的 ——
    而 `as_of` 正是运行门控判新鲜度的依据（「当日文件里装 T-1 数据同样记 stale」）。

    2026-08-07 review tests/ 时补上。补的过程里查出下面那条 fail-open。
    """

    @staticmethod
    def _run(monkeypatch, tmp_path, chart):
        import json
        import sys as _s

        from market_timing import overseas_market_collector as omc
        monkeypatch.setattr(omc, "SYMBOLS",
                            {"nvda": {"symbol": "NVDA", "name": "英伟达", "group": "ai_leader"},
                             "sox": {"symbol": "^SOX", "name": "费半", "group": "ai_leader"}})
        monkeypatch.setattr(omc, "FIELD_MAP",
                            {"nvda": "nvda_change_pct", "sox": "sox_change_pct"})
        monkeypatch.setattr(omc, "fetch_chart", chart)
        out = tmp_path / "o.json"
        monkeypatch.setattr(_s, "argv", ["x", "--date", "2026-08-04", "--input", str(out)])
        try:
            omc.main()
        except SystemExit:
            pass
        return json.loads(out.read_text(encoding="utf-8"))["overseas_market"]

    def test_as_of_is_max_timestamp_across_symbols(self, monkeypatch, tmp_path):
        """有时间戳时取**跨 symbol 的最大值** —— 不是第一个、也不是最后一个。

        取 max 的道理：几个市场收盘时间不同，`as_of` 该表示「这批数字里最新的
        那个截止到什么时候」。取错会让门控对着更早的时间判新鲜度。
        """
        stamps = {"NVDA": 1_754_300_000, "^SOX": 1_754_400_000}   # ^SOX 更晚

        def chart(symbol, region=""):
            return {"change_pct": 1.0, "last_timestamp": stamps[symbol],
                    "source": "Yahoo Finance chart API"}

        ov = self._run(monkeypatch, tmp_path, chart)
        assert ov["as_of_basis"] == "max(last_timestamp) across symbols"
        from datetime import datetime
        from zoneinfo import ZoneInfo
        want = datetime.fromtimestamp(max(stamps.values()),
                                      ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        assert ov["as_of"] == want, "as_of 应为最大时间戳，取到的却是别的"

    def test_partial_timestamps_still_use_the_available_max(self, monkeypatch, tmp_path):
        """只有部分 symbol 带时间戳时，用**拿到的那些**里的最大值，不回落到采集时刻。"""
        def chart(symbol, region=""):
            if symbol == "NVDA":
                return {"change_pct": 1.0, "last_timestamp": 1_754_300_000}
            return {"change_pct": 0.5}          # ^SOX 没给时间戳

        ov = self._run(monkeypatch, tmp_path, chart)
        assert ov["as_of_basis"] == "max(last_timestamp) across symbols"

    def test_no_timestamp_fabricates_collection_time_and_gate_calls_it_confirmed(
            self, monkeypatch, tmp_path):
        """⚠️⚠️ **一个时间戳都没拿到时，`as_of` 被写成采集时刻（now）**，
        而运行门控据此判 `confirmed`。

        这是一处 **fail-open**，且与本项目已拍板的相反决定不一致：
        契约层刻意允许 `amv_0.as_of` 为 None，理由原话是
        **「编一个 as_of 等于给门控假的新鲜度」**。这里的代码正是在编。

        生产者其实留了痕 —— `as_of_basis: "collection_time_fallback"` ——
        但 `runtime_guards.market_quality_gate` **从不读这个字段**，
        它的判据只有「有值 且 as_of 非空」⇒ 伪造的 as_of 照样得满分。

        影响面有界：overseas 权重 10/100，且被排除在 `core` 覆盖率判定之外。
        本条测试**钉住现状**（不擅自改门控 —— 2026-07-30 有过「门控与口径同时
        收紧导致 17:00 链失败」的事故），并把问题记入 TODO 待 owner 定：
        是「生产者不再伪造」还是「门控改读 as_of_basis」。
        """
        def chart(symbol, region=""):
            return {"change_pct": 1.0}          # 两个 symbol 都没时间戳

        ov = self._run(monkeypatch, tmp_path, chart)
        assert ov["as_of_basis"] == "collection_time_fallback"
        assert ov["as_of"], "当前实现会填采集时刻"

        # 门控对着这份伪造的 as_of 判 confirmed —— 记录现状
        import runtime_guards as rg
        gate = rg.market_quality_gate(
            {"date": "2026-08-04", "overseas_market": ov}, "2026-08-04")
        chk = next(c for c in gate["checks"] if c["field"] == "overseas")
        assert chk["quality"] == "confirmed", \
            "若这里变成 candidate/missing，说明门控或生产者已修 —— 请同步更新本测试与 TODO"
