# -*- coding: utf-8 -*-
"""v2 窗口布局钉测（TODO #90，v0.356）：段常量、段检查、段末隔离带。"""

from __future__ import annotations

import pytest

from custos.research import load_window as lw


class TestSegments:
    def test_constants_sane(self):
        assert lw.SEG_MINING_A == ("2000-01-01", "2016-12-31")
        assert lw.SEG_JUDGMENT == ("2017-01-01", "2021-12-31")
        assert lw.SEG_MINING_B == ("2022-01-01", "2026-09-04")
        # holdout 起点 = 挖掘 B 末日次日（冻结不变）
        assert lw.SEG_MINING_B[1] < lw.FORWARD_HOLDOUT_START
        assert lw.SEGMENT_NAMES == ("mining_a", "judgment", "mining_b")
        assert lw.SEGMENT_BUFFER_DAYS == 90


class TestSegmentViolation:
    @pytest.mark.parametrize(
        "s,e",
        [
            ("2000-01-01", "2016-12-31"),  # 挖掘 A 整段
            ("2010-01-01", "2016-12-31"),  # 原 pre2019 段（已并入挖掘 A）
            ("2017-01-01", "2021-12-31"),  # 判定整段
            ("2022-01-01", "2026-09-04"),  # 挖掘 B 整段
            ("2022-01-01", "2024-07-31"),  # R40 旧挖掘窗 ⊆ B（兼容通道）
            ("2024-08-01", "2026-09-04"),  # R40 旧判定窗 ⊆ B（兼容通道）
            ("2018-03-01", "2019-11-30"),  # 判定段内子窗
        ],
    )
    def test_inside_segment_ok(self, s, e):
        assert lw.segment_violation(s, e) is None

    @pytest.mark.parametrize(
        "s,e",
        [
            ("2015-01-01", "2024-07-31"),  # 跨 A/判定/B 三段
            ("2016-12-01", "2017-01-15"),  # 跨 A↔判定 边界一天
            ("2021-12-01", "2022-01-15"),  # 跨 判定↔B 边界
            ("1999-01-01", "2005-01-01"),  # 段外（挖掘 A 起点前）
            ("2026-09-05", "2026-10-01"),  # holdout 段
            ("2026-08-01", "2026-09-05"),  # 触及 holdout（holdout 文案优先）
            ("2024-01-01", "2023-01-01"),  # 起终颠倒
        ],
    )
    def test_violations_rejected(self, s, e):
        assert lw.segment_violation(s, e) is not None

    def test_holdout_wording_preserved(self):
        msg = lw.segment_violation("2026-08-01", "2026-09-05")
        assert "holdout" in msg


class TestEntryBuffer:
    def test_entry_deadline(self):
        assert lw.entry_deadline("2021-12-31") == "2021-10-02"
        assert lw.entry_deadline("2016-12-31") == "2016-10-02"

    def test_clip_entries_tail_only(self):
        recs = [
            {"date": "2016-01-15"},  # 段首侧：不滤（预热只读价格定案豁免）
            {"date": "2016-10-02"},  # = 截止日：留
            {"date": "2016-10-03"},  # 截止日后：滤
            {"date": "2016-12-31"},  # 段末：滤
        ]
        kept = lw.clip_entries(recs, "2016-12-31")
        assert [r["date"] for r in kept] == ["2016-01-15", "2016-10-02"]

    def test_clip_entries_trade_key_and_callable(self):
        trades = [{"entry_date": "2021-10-01"}, {"entry_date": "2021-11-01"}]
        kept = lw.clip_entries(trades, "2021-12-31", key="entry_date")
        assert len(kept) == 1
        kept2 = lw.clip_entries(trades, "2021-12-31", key=lambda t: t["entry_date"])
        assert kept2 == kept

    def test_clipped_count_is_len_diff(self):
        recs = [{"date": d} for d in ("2016-09-30", "2016-11-30", "2016-12-30")]
        kept = lw.clip_entries(recs, "2016-12-31")
        assert len(recs) - len(kept) == 2  # 报告记隔离带剔除数的口径
