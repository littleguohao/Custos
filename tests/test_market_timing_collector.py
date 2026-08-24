# -*- coding: utf-8 -*-
"""`market_timing_collector` —— 产出 `market_timing_input.json`（**19 个消费者**）。

它在 08:50 跑，而那时**最新可得的日线是前一交易日收盘** ——
所以每段都要带 `as_of`（真实数据日）并按交易日历标 `auto`/`degraded`。
源码 docstring 把这叫「honest labeling」：**宁可标 degraded，也不冒充当日**。

⚠️ 这个「标记」不是装饰：`market_timing_scorer.is_stale` 靠 `quality` 判是否按
满分计入（v0.40 后 `degraded` 也算不新鲜），而 `runtime_guards` 靠 `as_of` 判门控。
标错 = 用 T-1 的宽度/成交额给当日满分，且报告看不出来。
"""

from __future__ import annotations

import math
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.pipeline.market_timing import market_timing_collector as mtc  # noqa: E402


class TestToFloat:
    """⚠️ 行情源的「无数据」有多种形态，全部要变 None ——
    **不能变 0.0**：0.0 会被下游阈值判定当成真实读数
    （同 `code_utils.fnum` 的理由）。"""

    @pytest.mark.parametrize(
        "raw", [None, "", "  ", "--", "nan", "None", "abc", [], {}]
    )
    def test_non_numeric_is_none(self, raw):
        assert mtc.to_float(raw) is None

    @pytest.mark.parametrize(
        "raw,want",
        [
            ("3.5", 3.5),
            (3.5, 3.5),
            ("  12 ", 12.0),
            (0, 0.0),
            ("0", 0.0),
            ("-2.3", -2.3),
        ],
    )
    def test_numeric_passes(self, raw, want):
        assert mtc.to_float(raw) == want

    def test_zero_is_kept_not_none(self):
        """⚠️ **0.0 是合法读数**（涨跌幅可以是 0）—— 不得被当成缺失。"""
        assert mtc.to_float(0) == 0.0
        assert mtc.to_float("0.0") == 0.0

    def test_nan_becomes_none(self):
        assert mtc.to_float(float("nan")) is None

    def test_inf_becomes_none(self):
        """⚠️ ±inf 通过 float() 但不是有效读数 —— 必须变 None（与 NaN 同处理）。

        此前 inf 被静默保留（`assert x in (None, inf)` 那条同义反复断言
        如实记录了这个漏洞）：inf 会被下游阈值判定当成真实极大值，
        与该模块「无数据不变 0.0」的 fail-closed 语义相悖，2026-08-11 修。
        """
        assert mtc.to_float(float("inf")) is None
        assert mtc.to_float(float("-inf")) is None
        assert mtc.to_float("inf") is None


class TestAmvZone:
    """0AMV 分区阈值：>4 做多 / <−2.3 空头 / 其余中性。

    ⚠️ 这两个数与 `amv_state` 的 regime 切换线是同一套 —— R4 的「0AMV 是主过滤器、
    熊市减亏 ~15pp」就建立在它上面，分错区等于方向反了。
    """

    @pytest.mark.parametrize(
        "v,want",
        [
            (5.0, "做多"),
            (4.01, "做多"),
            (4.0, "中性"),
            (0.0, "中性"),
            (-2.3, "中性"),
            (-2.31, "空头"),
            (-5.0, "空头"),
        ],
    )
    def test_thresholds_are_exclusive(self, v, want):
        assert mtc.amv_zone(v) == want

    def test_none_is_empty_string_not_neutral(self):
        """⚠️ 缺 0AMV 时返回**空串**而不是「中性」——
        「不知道」不能冒充「中性」，否则加仓白名单 `{做多, 中性}` 会放行未知方向。
        （v0.28 修过这个形状：`regime != "空头"` 在空串时为真。）"""
        assert mtc.amv_zone(None) == ""


class TestTrend:
    def _rows(self, n, close=3000.0):
        return [
            {
                "date": f"2026{m:02d}{d:02d}",
                "close": close + i * 0.5,
                "amount": 1e11,
                "volume": 1e9,
            }
            for i, (m, d) in enumerate([(1 + i // 28, 1 + i % 28) for i in range(n)])
        ]

    def test_empty_is_unavailable(self):
        assert mtc.trend([]) == {"available": False, "source": "vipdoc_day"}

    def test_sorts_before_taking_latest(self):
        """行序不可靠 —— `latest` 必须按日期取，不是取最后一行。"""
        rows = self._rows(5)
        out = mtc.trend(list(reversed(rows)))
        assert out["latest_date"] == rows[-1]["date"]

    def test_short_history_leaves_long_mas_none(self):
        """⚠️ 数据不够长时 `ma240` 是 **None 而不是 0 或 False** ——
        下游 `above_ma240` 若得 False 会被 `score_indices` 当成「跌破」扣分，
        把「算不出」显示成「在均线下方」。"""
        out = mtc.trend(self._rows(30))
        assert out["ma25"] is not None
        assert out["ma60"] is None and out["ma144"] is None and out["ma240"] is None

    def test_full_history_fills_all(self):
        out = mtc.trend(self._rows(250))
        for k in ("ma25", "ma60", "ma144", "ma240"):
            assert out[k] is not None, k


class TestFreshnessLabeling:
    """⚠️ 「honest labeling」的核心：数据日与预期不符时标 `degraded` **并写明原因**。"""

    def test_matching_date_is_auto(self):
        q = {"notes": []}
        assert mtc._freshness("20260810", "20260810", "880005 涨跌家数", q) == "auto"
        assert q["notes"] == [], "一致时不该产生噪声 note"

    def test_mismatch_is_degraded_with_reason(self):
        q = {"notes": []}
        assert (
            mtc._freshness("20260807", "20260810", "880005 涨跌家数", q) == "degraded"
        )
        assert len(q["notes"]) == 1
        note = q["notes"][0]
        assert "20260807" in note and "20260810" in note, "note 必须给出两个日期"
        assert "degraded" in note

    def test_unknown_expected_is_degraded_not_auto(self):
        """⚠️ 预期日算不出（`expected=None`）时判 **degraded**，不是 auto ——
        「无法确认」不能冒充「已确认新鲜」。"""
        q = {"notes": []}
        assert mtc._freshness("20260810", None, "x", q) == "degraded"
        assert "无法确认" in q["notes"][0]

    def test_missing_as_of_is_degraded_and_says_none(self):
        q = {"notes": []}
        assert mtc._freshness("", "20260810", "x", q) == "degraded"
        assert "无" in q["notes"][0]

    def test_degraded_is_recognized_as_not_fresh_downstream(self):
        """⚠️ 端到端语义：本模块产出的 `degraded` 必须被 scorer 当成「不新鲜」。

        2026-08-10 之前**不是** —— `is_stale` 只认 `"stale"`，`degraded` 被当新鲜
        照满分计入（v0.40 修）。本条把两个模块的词表对上，防它再分叉。
        """
        from custos.core import contracts as C

        assert "degraded" in C.SECTION_NOT_FRESH, (
            "collector 会产出 degraded，它必须在「不新鲜」域里"
        )


class TestSectionSkeletonCarriesAsOf:
    """契约钉（2026-08-12 #45②）：collector 的三段骨架必须带 `as_of` 键
    （初值 None——「编一个 as_of 等于给门控假新鲜度」）。

    本机无 vipdoc ⇒ 走「读取失败/无数据」分支，恰是校验**骨架初值**的路径。
    """

    def test_three_sections_have_as_of_key(self):
        breadth, sentiment, turnover, _q = mtc.derive_market_fields("2026-08-12")
        for sec in (breadth, sentiment, turnover):
            assert "as_of" in sec, "三段骨架缺 as_of 键 ⇒ 契约必填会硬失败"


def _write_day(path, recs):
    """写合成 TDX .day 文件（32 字节/记录：date,o,h,l,c(int 0.01元),amount(float),vol,reserved）。"""
    import struct

    with open(path, "wb") as f:
        for r in recs:
            f.write(struct.pack("<IIIIIfII", *r))


def _isolate_tdx_root(monkeypatch, tmp_path):
    """把 local_tdx_data 的 TDX_ROOT 指到合成 vipdoc，并清掉 reader/校验缓存。"""
    monkeypatch.setattr(mtc.ltd, "TDX_ROOT", tmp_path)
    monkeypatch.setattr(mtc.ltd, "_reader", None)
    monkeypatch.setattr(mtc.ltd, "_tdx_root_verified", set())


class TestVipdocRows:
    """`_vipdoc_rows` 改走 `ltd.read_vipdoc_daily`（2026-08-24 数据层解耦）后的
    行为钉测：合成 880 系列 .day，真读数据层（不是打桩字符串）。

    与旧「自备 mootdx Reader + DatetimeIndex」路径核对过：date 从 index 变列、
    格式同为 %Y-%m-%d，high/low/close/amount 同名同单位，结果按日期升序。
    """

    RECS = [
        (20260817, 1000, 1050, 990, 1020, 111111.0, 100, 0),
        (20260818, 1020, 1060, 1000, 1040, 222222.0, 100, 0),
        (20260819, 1040, 1080, 1030, 1070, 333333.0, 100, 0),
        (20260820, 1070, 1090, 1050, 1080, 444444.0, 100, 0),
        (20260821, 1080, 1100, 1060, 1090, 555555.0, 100, 0),
        (20260824, 1090, 1110, 1070, 1100, 666666.0, 100, 0),
    ]

    def _mk880(self, tmp_path):
        d = tmp_path / "vipdoc" / "sh" / "lday"
        d.mkdir(parents=True)
        _write_day(d / "sh880005.day", self.RECS)

    def test_tail_count_sorted_with_iso_dates(self, monkeypatch, tmp_path):
        self._mk880(tmp_path)
        _isolate_tdx_root(monkeypatch, tmp_path)
        rows = mtc._vipdoc_rows("880005.SH", count=3)
        assert [r["date"] for r in rows] == ["2026-08-20", "2026-08-21", "2026-08-24"]
        last = rows[-1]
        # 880 系列按 SH_INDEX 系数：价格 /100，amount 原样（元）——与旧路径同一 Reader
        assert last["close"] == 11.0
        assert last["high"] == pytest.approx(11.1)  # mootdx 系数乘法的浮点尾差
        assert last["low"] == pytest.approx(10.7)
        assert last["amount"] == 666666.0

    def test_default_count_is_five(self, monkeypatch, tmp_path):
        self._mk880(tmp_path)
        _isolate_tdx_root(monkeypatch, tmp_path)
        assert len(mtc._vipdoc_rows("880005.SH")) == 5

    def test_missing_code_returns_empty(self, monkeypatch, tmp_path):
        (tmp_path / "vipdoc").mkdir(parents=True)
        _isolate_tdx_root(monkeypatch, tmp_path)
        assert mtc._vipdoc_rows("880099.SH") == []
