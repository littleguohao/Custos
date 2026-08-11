# -*- coding: utf-8 -*-
"""P1 审计修复的回归测试。

主题:同一个不变量在仓库里必须只有一套实现。审计发现正确实现常常就在隔壁文件,
只是没被复用——regime 归一化写在 runtime_guards 却有五个模块各自用精确等值比较,
涨跌停幅度推断承诺了"20% 前缀不得降 5%"却因为调用方不传 code 而失效。
"""
from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd
import pytest

from custos.core import code_utils
from custos.core import runtime_guards as rg
from custos.core.paths import CN_TZ, cn_now, cn_today

# 三套并行词表的全部取值(amv_state / amv_zone / README 措辞),归一后必须落到同一档
BEAR_WORDS = ["空头", "空头触发", "0AMV空头", "空头区间"]
LONG_WORDS = ["做多", "做多触发", "多头", "0AMV做多"]
NEUTRAL_WORDS = ["中性", "阈值内"]


def _strip_comments_and_strings(src: str) -> str:
    """Drop comments and string literals so static assertions only see code.

    Without this a test that greps for a forbidden pattern also matches the
    explanatory comment that documents why the pattern is forbidden.
    """
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return src
    return " ".join(out)


class TestRegimeNormalizationIsShared:
    """B1: 五个模块必须复用 normalize_regime,不能各自 == "空头"。"""

    @pytest.mark.parametrize("word", BEAR_WORDS)
    def test_all_bear_words_normalize(self, word):
        assert rg.normalize_regime(word) == "空头"

    @pytest.mark.parametrize("word", LONG_WORDS)
    def test_all_long_words_normalize(self, word):
        assert rg.normalize_regime(word) == "做多"

    @pytest.mark.parametrize("word", NEUTRAL_WORDS)
    def test_all_neutral_words_normalize(self, word):
        assert rg.normalize_regime(word) == "中性"

    @pytest.mark.parametrize("blank", ["", None, "  ", "???"])
    def test_unknown_is_not_tradeable(self, blank):
        assert rg.normalize_regime(blank) == "未知"

    @pytest.mark.parametrize("word", BEAR_WORDS)
    def test_score_candidates_caps_on_every_bear_word(self, word):
        """空头封顶 B 必须对 amv_zone 的"空头触发"同样生效。"""
        from custos.pipeline.screening import score_candidates as sc
        assert sc.market_permission(word) == "观察"

    @pytest.mark.parametrize("word", LONG_WORDS)
    def test_score_candidates_allows_on_every_long_word(self, word):
        from custos.pipeline.screening import score_candidates as sc
        assert sc.market_permission(word) == "允许"

    def test_unknown_regime_is_not_permitted(self):
        from custos.pipeline.screening import score_candidates as sc
        assert sc.market_permission("") == "仅低吸"

    @pytest.mark.parametrize("word", BEAR_WORDS)
    def test_position_increase_denied_for_every_bear_word(self, word):
        mq = {"status": "pass", "amv_ok": True, "limitations": []}
        d = rg.position_increase_decision({"amv_0": {"effective_state": word}},
                                          reduction_ready=True, technical_current=True,
                                          quotes_current=True, market_quality=mq)
        assert d["allow"] is False and d["regime"] == "空头"

    def test_amv_zone_fallback_is_honoured(self):
        """merge_incremental_market 用 amv_zone 兜底填 effective_state 的场景。"""
        mq = {"status": "pass", "amv_ok": True, "limitations": []}
        d = rg.position_increase_decision({"amv_0": {"amv_zone": "空头触发"}},
                                          reduction_ready=True, technical_current=True,
                                          quotes_current=True, market_quality=mq)
        assert d["allow"] is False


class TestSectorIndexMarketMapping:
    """B11: 881xxx 细分行业指数属沪市,不能落到北交所。"""

    @pytest.mark.parametrize("code", ["881101", "881280", "881999", "880300", "880001"])
    def test_tdx_sector_indices_are_shanghai(self, code):
        assert code_utils.market_of(code) == "SH"

    @pytest.mark.parametrize("code", ["920819", "830799", "430047"])
    def test_real_bj_codes_still_bj(self, code):
        assert code_utils.market_of(code) == "BJ"

    @pytest.mark.parametrize("code,mkt", [("600000", "SH"), ("688111", "SH"),
                                          ("000001", "SZ"), ("300750", "SZ")])
    def test_ordinary_codes_unaffected(self, code, mkt):
        assert code_utils.market_of(code) == mkt


class TestPriceLimitInferenceGetsCode:
    """B2: analyze 必须把 code 传下去,否则 20% 品种被按 10% 甚至 5% 判定。"""

    def _quiet_df(self, n=40, base=10.0):
        """波动很小的 20+ 日窗口:会触发 _infer_price_limit 的 ST 降级分支。"""
        rows = []
        for i in range(n):
            close = base + (i % 3) * 0.02          # 日波动 < 1%
            rows.append({"date": pd.Timestamp("2026-06-01") + pd.Timedelta(days=i),
                         "open": close, "high": close * 1.002, "low": close * 0.998,
                         "close": close, "volume": 1e6, "amount": 1e7})
        return pd.DataFrame(rows)

    def test_chinext_not_demoted_to_five_percent(self):
        from custos.pipeline.market_timing import technical_monitor as tm
        assert tm._infer_price_limit("300750", self._quiet_df()) == 20
        assert tm._infer_price_limit("301029", self._quiet_df()) == 20
        assert tm._infer_price_limit("688111", self._quiet_df()) == 20
        # ⚠️ 2026-08-07：这里原本断言 920xxx == 20，**把一个 bug 锁死了** ——
        # 北交所竞价交易的涨跌幅是 **30%**，见 `code_utils.price_limit_pct`。
        # 本测试的**意图**（20%/30% 品种不得被安静窗口降级为 5%）不变，
        # 只把 BJ 的期望值改成正确的 30。
        assert tm._infer_price_limit("920819", self._quiet_df()) == 30

    def test_missing_code_is_what_broke_it(self):
        """留证:不传 code 时安静窗口会被判成 5%,这正是 analyze 旧行为。"""
        from custos.pipeline.market_timing import technical_monitor as tm
        assert tm._infer_price_limit("", self._quiet_df()) == 5

    def test_analyze_forwards_code_to_price_volume(self):
        from custos.pipeline.market_timing import technical_monitor as tm
        df = self._quiet_df(n=60)
        got = tm.analyze(df, "300750")
        assert got["available"] is True
        assert got["price_volume"].get("price_limit") == 20, "analyze 必须把 code 透传下去"

    def test_analyze_without_code_still_runs(self):
        """向后兼容:老调用方不传 code 不能崩。"""
        from custos.pipeline.market_timing import technical_monitor as tm
        assert tm.analyze(self._quiet_df(n=60))["available"] is True


class TestMarketClockIsExchangeTime:
    """A6: 日期与时间戳必须来自交易所时钟,不能跟随宿主时区。"""

    def test_cn_now_is_aware_and_plus_eight(self):
        assert cn_now().tzinfo is not None
        assert cn_now().utcoffset().total_seconds() == 8 * 3600

    def test_cn_today_matches_cn_now(self):
        assert cn_today() == cn_now().date()

    def test_naive_timestamp_interpreted_as_exchange_time(self):
        """历史数据是 naive 的,必须按上海时间解释,否则 15:00 判定偏移宿主时区。"""
        d = rg._as_cn_datetime("2026-08-03T15:30:00")
        assert d.time() >= rg.CLOSE_TIME
        assert d.utcoffset().total_seconds() == 8 * 3600

    def test_utc_timestamp_converted_before_comparison(self):
        """同一时刻用 UTC 记录时,转换后仍应通过收盘判定。"""
        assert rg._as_cn_datetime("2026-08-03T07:30:00+00:00").time() >= rg.CLOSE_TIME

    def test_pre_close_import_still_rejected(self):
        assert rg._as_cn_datetime("2026-08-03T14:30:00+08:00").time() < rg.CLOSE_TIME

    def test_no_naive_now_calls_left_in_tools(self):
        """静态防线:src 下不得再出现裸 datetime.now()/date.today()。"""
        import pathlib
        offenders = []
        root = pathlib.Path(rg.__file__).resolve().parent
        for f in root.rglob("*.py"):
            if f.name == "paths.py":
                continue
            src = _strip_comments_and_strings(f.read_text(encoding="utf-8"))
            for pat in ("datetime.now()", "date.today()"):
                if pat in src:
                    offenders.append(f"{f.name}:{pat}")
        assert offenders == [], f"应改用 paths.cn_now()/cn_today(): {offenders}"


class TestQuoteDateVerification:
    """C1: 快照源写入的日期是"假定"的,必须留痕,不能消解陈旧检测。"""

    def test_snapshot_sources_are_marked_unverified(self):
        import inspect
        from custos.datasource.collect.collect_holding_quotes import _tq_snapshot_quote, _eastmoney_bj_quote
        for fn in (_tq_snapshot_quote, _eastmoney_bj_quote):
            src = inspect.getsource(fn)
            assert '"date_verified": False' in src, f"{fn.__name__} 必须标记日期未自证"

    def test_bars_source_is_marked_verified(self, monkeypatch):
        """行为断言:reader 源的日期来自 K 线索引,应标记为已自证。"""
        from custos.datasource.collect import collect_holding_quotes as chq

        idx = pd.DatetimeIndex([pd.Timestamp("2026-08-02"), pd.Timestamp("2026-08-03")])
        df = pd.DataFrame({"open": [10.0, 10.1], "high": [10.2, 10.3],
                           "low": [9.9, 10.0], "close": [10.1, 10.2],
                           "volume": [1e6, 1.1e6], "amount": [1e7, 1.1e7]}, index=idx)

        class _R:
            def daily(self, symbol):
                return df

        monkeypatch.setattr(chq, "_get_reader", lambda: _R())
        q = chq._reader_quote("600000", "浦发", 1)
        assert q["date"] == "2026-08-03"
        assert q["date_verified"] is True

    def test_gate_reports_unverified_quotes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rg, "DATA", tmp_path)
        (tmp_path / "trades").mkdir(parents=True, exist_ok=True)
        (tmp_path / "market").mkdir(parents=True, exist_ok=True)
        (tmp_path / "quality").mkdir(parents=True, exist_ok=True)
        (tmp_path / "holdings").mkdir(parents=True, exist_ok=True)
        day = "2026-08-03"
        (tmp_path / "trades" / "current_positions.json").write_text(
            '[{"代码": "600000"}]', encoding="utf-8")
        (tmp_path / "market" / f"{day}_holding_quotes.json").write_text(
            '{"quotes": [{"code": "600000", "price": 10.0, "date": "%s",'
            ' "date_verified": false}]}' % day, encoding="utf-8")
        r = rg.write_runtime_gate(day)
        pg = r["position_gate"]
        assert pg["quotes_date_unverified"] == ["600000"]
        assert any("未经数据自证" in x for x in pg["limitations"])


class TestOhlcvFreshness:
    """C2: 本地读到陈旧数据时必须标记,不能静默返回旧序列。"""

    def _df(self, last_day: str, n: int = 30):
        end = pd.Timestamp(last_day)
        return pd.DataFrame({
            "date": pd.date_range(end=end, periods=n, freq="D"),
            "open": [10.0] * n, "high": [10.1] * n, "low": [9.9] * n,
            "close": [10.0] * n, "volume": [1e6] * n,
        })

    def test_stale_local_data_is_flagged(self, monkeypatch):
        from custos.datasource.local_tdx import local_tdx_data as ltd
        monkeypatch.setattr(ltd, "read_vipdoc_daily", lambda c: self._df("2026-07-30"))
        monkeypatch.setattr(ltd, "get_online_bars", lambda c, offset=0: pd.DataFrame())
        df = ltd.get_ohlcv_table("600000", count=30, expect_last_date="2026-08-03", adjust="none")
        assert df.attrs["stale"] is True
        assert df.attrs["last_date"] == "2026-07-30"
        assert df.attrs["expected"] == "2026-08-03"

    def test_fresh_local_data_not_flagged(self, monkeypatch):
        from custos.datasource.local_tdx import local_tdx_data as ltd
        monkeypatch.setattr(ltd, "read_vipdoc_daily", lambda c: self._df("2026-08-03"))
        df = ltd.get_ohlcv_table("600000", count=30, expect_last_date="2026-08-03", adjust="none")
        assert df.attrs["stale"] is False

    def test_online_refresh_wins_over_stale_local(self, monkeypatch):
        """本地陈旧时应尝试在线源,拿到更新的就用它。"""
        from custos.datasource.local_tdx import local_tdx_data as ltd
        monkeypatch.setattr(ltd, "read_vipdoc_daily", lambda c: self._df("2026-07-30"))
        monkeypatch.setattr(ltd, "get_online_bars",
                            lambda c, offset=0: self._df("2026-08-03"))
        df = ltd.get_ohlcv_table("600000", count=30, expect_last_date="2026-08-03", adjust="none")
        assert df.attrs["stale"] is False and df.attrs["last_date"] == "2026-08-03"

    def test_without_expect_date_behaviour_unchanged(self):
        """不传 expect_last_date 时不做校验,老调用方行为不变。"""
        from custos.datasource.local_tdx import local_tdx_data as ltd
        import unittest.mock as m
        with m.patch.object(ltd, "read_vipdoc_daily", lambda c: self._df("2026-07-30")):
            df = ltd.get_ohlcv_table("600000", count=30, adjust="none")
        assert "stale" not in df.attrs


class TestChiefDecisionFailsClosed:
    """A3: 门控缺失时决策层必须拒绝出计划,不能当作"无限制"。"""

    def test_gate_is_mandatory_input(self):
        import inspect
        from custos.pipeline.market_timing import chief_decision_report as cdr
        src = inspect.getsource(cdr.main)
        assert "mandatory runtime_gate missing" in src

    def test_allow_increase_uses_truthiness_not_is_false(self):
        """None(字段缺失)必须与 False 同等对待。"""
        import inspect
        from custos.pipeline.market_timing import chief_decision_report as cdr
        raw = inspect.getsource(cdr.main)
        code_only = _strip_comments_and_strings(raw)
        # `is False` 只在注释里出现(说明历史缺陷),真实代码里必须没有
        assert "is False" not in code_only, "不得用 `is False`:None 会绕过判定"
        # 真实代码里必须是真值判断
        assert "not position_gate.get('allow_position_increase')" in raw
