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
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("07_tools", "07_tools/market_timing"):
    sys.path.insert(0, str(ROOT / _p))

from market_timing import market_timing_collector as mtc  # noqa: E402


class TestToFloat:
    """⚠️ 行情源的「无数据」有多种形态，全部要变 None ——
    **不能变 0.0**：0.0 会被下游阈值判定当成真实读数
    （同 `code_utils.fnum` 的理由）。"""

    @pytest.mark.parametrize("raw", [None, "", "  ", "--", "nan", "None", "abc", [], {}])
    def test_non_numeric_is_none(self, raw):
        assert mtc.to_float(raw) is None

    @pytest.mark.parametrize("raw,want", [("3.5", 3.5), (3.5, 3.5), ("  12 ", 12.0),
                                          (0, 0.0), ("0", 0.0), ("-2.3", -2.3)])
    def test_numeric_passes(self, raw, want):
        assert mtc.to_float(raw) == want

    def test_zero_is_kept_not_none(self):
        """⚠️ **0.0 是合法读数**（涨跌幅可以是 0）—— 不得被当成缺失。"""
        assert mtc.to_float(0) == 0.0
        assert mtc.to_float("0.0") == 0.0

    def test_nan_becomes_none(self):
        assert mtc.to_float(float("nan")) is None

    def test_inf_is_not_silently_kept(self):
        """inf 通过 float() 但不是有效读数 —— 记录当前行为以便发现变化。"""
        assert mtc.to_float(float("inf")) in (None, float("inf"))


class TestAmvZone:
    """0AMV 分区阈值：>4 做多 / <−2.3 空头 / 其余中性。

    ⚠️ 这两个数与 `amv_state` 的 regime 切换线是同一套 —— R4 的「0AMV 是主过滤器、
    熊市减亏 ~15pp」就建立在它上面，分错区等于方向反了。
    """

    @pytest.mark.parametrize("v,want", [(5.0, "做多"), (4.01, "做多"),
                                        (4.0, "中性"), (0.0, "中性"), (-2.3, "中性"),
                                        (-2.31, "空头"), (-5.0, "空头")])
    def test_thresholds_are_exclusive(self, v, want):
        assert mtc.amv_zone(v) == want

    def test_none_is_empty_string_not_neutral(self):
        """⚠️ 缺 0AMV 时返回**空串**而不是「中性」——
        「不知道」不能冒充「中性」，否则加仓白名单 `{做多, 中性}` 会放行未知方向。
        （v0.28 修过这个形状：`regime != "空头"` 在空串时为真。）"""
        assert mtc.amv_zone(None) == ""


class TestTrend:
    def _rows(self, n, close=3000.0):
        return [{"date": f"2026{m:02d}{d:02d}", "close": close + i * 0.5,
                 "amount": 1e11, "volume": 1e9}
                for i, (m, d) in enumerate([(1 + i // 28, 1 + i % 28) for i in range(n)])]

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
        assert mtc._freshness("20260807", "20260810", "880005 涨跌家数", q) == "degraded"
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
        import contracts as C
        assert "degraded" in C.SECTION_NOT_FRESH, \
            "collector 会产出 degraded，它必须在「不新鲜」域里"
