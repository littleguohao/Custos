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

    def test_no_timestamp_does_not_fabricate_as_of(self, monkeypatch, tmp_path):
        """⚠️ 一个时间戳都没拿到时**不得编 `as_of`** —— 2026-08-10 按 TODO #52 路子① 改。

        改之前：`as_of = datetime.now()`，门控的判据「有值 且 as_of 非空」
        据此给出**最强结论 `confirmed`**。而这不是罕见分支 —— Yahoo 不可达时走的
        TDX ext 降级 `tdx_ext_quotes.fetch_ext_change` **根本不返回 last_timestamp**
        ⇒ Yahoo 全挂时必然走到这里，恰恰是最需要判新鲜度的时候。

        与契约层已拍板的 `amv_0.as_of` 同一原则，原话：
        **「编一个 as_of 等于给门控一个假的新鲜度」**。
        形状也对齐 amv_0：**键存在、值为 None**（不是省略键）。
        """
        def chart(symbol, region=""):
            return {"change_pct": 1.0}          # 两个 symbol 都没时间戳

        ov = self._run(monkeypatch, tmp_path, chart)
        assert ov["as_of"] is None, f"不得编 as_of，实际 {ov['as_of']!r}"
        assert ov["as_of_basis"] == "no_timestamp_from_any_symbol"
        # 采集时刻另存 —— 有排障价值，但它不是数据新鲜度，不许冒充
        assert ov.get("collected_at"), "采集时刻应保留在独立键里"
        assert ov["collected_at"] != ov["as_of"]

    def test_gate_degrades_to_candidate_not_confirmed(self, monkeypatch, tmp_path):
        """⚠️ 门控随之自然降到 `candidate` —— **不是 `missing`**（值还在，只是新鲜度不可证）。

        这个区分是路子① 成立的前提：若降到 `missing` 就等于把「有数但不知何时」
        和「压根没数」混为一谈，那才是过度收紧。
        """
        import runtime_guards as rg

        ov = self._run(monkeypatch, tmp_path, lambda symbol, region="": {"change_pct": 1.0})
        gate = rg.market_quality_gate(
            {"date": "2026-08-04", "overseas_market": ov}, "2026-08-04")
        chk = next(c for c in gate["checks"] if c["field"] == "overseas")
        assert chk["quality"] == "candidate", \
            f"应降到 candidate（值在、新鲜度不可证），实际 {chk['quality']}"

    def test_fallback_path_really_lacks_timestamp(self):
        """⚠️ 钉住上面几条的**前提事实**：TDX ext 降级不返回 `last_timestamp`。

        若哪天它开始返回时间戳，上面「必然走到 else 分支」的论证就不再成立，
        本条会失败，提醒回来重新推敲（而不是让那几条测试悄悄变成测别的东西）。
        """
        import inspect

        from market_timing import tdx_ext_quotes as tq

        src = inspect.getsource(tq.fetch_ext_change)
        assert "last_timestamp" not in src, \
            "fetch_ext_change 开始返回 last_timestamp 了 —— 请重新评估 #52 的前提"

    def test_downgrade_does_not_change_overall_status(self):
        """⚠️ overseas 降级**不得改变整体 `status`** —— 这是路子① 安全的前提。

        `runtime_guards` 把 overseas 排除在 `core` 之外
        （`core = [x for x in checks if x["field"] != "overseas"]`，权重也只有 10/100），
        所以它从 `confirmed` 掉到 `candidate` 不会波及门控结论。

        2026-08-10 实测：完整数据下修复前后都是 `status=pass`。
        本条钉住这层隔离 —— 若哪天 core 的排除规则改了，这个 as_of 修复会
        **静默变成一个阻断源**，而 2026-07-30 正是「门控与 stale 判定同时收紧
        导致 17:00 整条链失败」。
        """
        import runtime_guards as rg

        D = "2026-08-10"
        full = {
            "date": D,
            "amv_0": {"amv_change_pct": 1.0, "quality": "confirmed",
                      "as_of": D, "effective_state": "中性"},
            "market_breadth": {"up_count": 2600, "down_count": 2100,
                               "as_of": D, "quality": "auto"},
            "turnover": {"total_turnover": 9e11, "turnover_change_pct": 3.0,
                         "as_of": D, "quality": "auto"},
            "sentiment": {"limit_up_count": 45, "as_of": D, "quality": "auto"},
        }
        results = {}
        for label, ov in [
            ("fabricated", {"sox_change_pct": 2.019, "as_of": f"{D}T14:44:40+08:00"}),
            ("none", {"sox_change_pct": 2.019, "as_of": None,
                      "as_of_basis": "no_timestamp_from_any_symbol"}),
        ]:
            r = rg.market_quality_gate({**full, "overseas_market": ov}, D)
            chk = next(c for c in r["checks"] if c["field"] == "overseas")
            results[label] = (chk["quality"], r.get("status"))

        assert results["fabricated"][0] == "confirmed"
        assert results["none"][0] == "candidate"
        assert results["fabricated"][1] == results["none"][1] == "pass", \
            f"overseas 降级改变了整体 status：{results}"


class TestImpactSummary:
    """⚠️ 外围影响摘要 —— 它直接写进盘前日报的「海外」段，是 owner 看到的判断。

    分三链归因：美股 AI/半导体（sox/nvda/amd/tsm 平均）、港股科技、日韩。
    """

    # ⚠️ `omc` 在本文件里是**函数内局部导入**（其他测试类各自 import），
    #    类级别用不到 —— 第一版直接写 `omc.impact_summary` 得 NameError。
    @staticmethod
    def _mod():
        from market_timing import overseas_market_collector as omc
        return omc

    @staticmethod
    def _d(**kw):
        return {k: {"change_pct": v} for k, v in kw.items()}

    def test_strong_tech_says_favorable(self):
        out = self._mod().impact_summary(self._d(sox=2.0, nvda=3.0, amd=2.5, tsm=1.8))
        assert "偏强" in out and "利于" in out

    def test_weak_tech_says_lower_chase_permission(self):
        """⚠️ 偏弱时说的是「追高**权限**应下降」而不是「应减仓」——
        海外是情境证据，不是持仓指令（决策优先级：个股服从板块、板块服从大盘）。"""
        out = self._mod().impact_summary(self._d(sox=-2.0, nvda=-3.0))
        assert "偏弱" in out and "权限应下降" in out
        assert "减仓" not in out

    def test_mild_move_is_explicitly_neutral(self):
        out = self._mod().impact_summary(self._d(sox=0.2, nvda=-0.3))
        assert "中性" in out

    def test_all_missing_does_not_fabricate_a_conclusion(self):
        """⚠️⚠️ 全缺时**不得**编出「中性」这类结论 ——
        「没数据」与「数据显示中性」是两件事，后者会让读者以为海外已核对过。
        """
        out = self._mod().impact_summary({})
        assert "偏强" not in out and "偏弱" not in out
        assert "AI/半导体链整体中性" not in out, \
            "无数据时不该给出 AI 链的中性结论"

    def test_partial_data_uses_only_what_exists(self):
        """只有 sox 时也要能给结论 —— 平均是对**非 None** 的那些取的。"""
        out = self._mod().impact_summary(self._d(sox=2.5))
        assert "偏强" in out


class TestA50Sanity:
    """⚠️ A50 期货的 |change_pct| > 3% 通常是 `previous_close` 错位（换月/元数据滞后）。"""

    def test_large_move_is_flagged_not_silently_dropped(self):
        r = {"a50_futures": {"change_pct": 5.2}}
        from collect import collect_incremental_market as cim
        cim._a50_sanity(r)
        a50 = r["a50_futures"]
        assert a50["suspect"] is True
        assert "人工核对" in a50["note"]

    def test_value_is_not_modified(self):
        """⚠️ **只标记不改值** —— 改值会让下游算出的数字与源不一致且无从追溯。"""
        from collect import collect_incremental_market as cim
        r = {"a50_futures": {"change_pct": -4.4}}
        cim._a50_sanity(r)
        assert r["a50_futures"]["change_pct"] == -4.4

    def test_normal_move_is_not_flagged(self):
        from collect import collect_incremental_market as cim
        r = {"a50_futures": {"change_pct": 1.2}}
        cim._a50_sanity(r)
        assert "suspect" not in r["a50_futures"]

    def test_non_numeric_does_not_crash(self):
        from collect import collect_incremental_market as cim
        for bad in (None, "N/A", ""):
            r = {"a50_futures": {"change_pct": bad}}
            cim._a50_sanity(r)
            assert "suspect" not in r["a50_futures"]

    def test_missing_section_does_not_crash(self):
        from collect import collect_incremental_market as cim
        r = {}
        cim._a50_sanity(r)
        assert r == {}
