# -*- coding: utf-8 -*-
"""财报时效上限回归测试（审计 E11，2026-08-03 裁定）。

问题：陈旧财报被无限期视为有效。长期停牌/失去持续披露能力的壳公司挂着几年前的报表，
代理条件(净利>0 / 现金流>0 / ROE>0)照样成立 → 被判"品质优"进入 ⭐ 四面共振。
系统不报错，只是安静地把空壳标成优质标的。

两条路径同口径（按 report_date，阈值 financials.REPORT_MAX_AGE_DAYS 单一定义）：
  - live: financials.financial_factor  → available=False, reason="report_stale"
  - 回测: scan_signals_ytd._tier_you    → False（不算优）

阈值 270 的依据：A 股法定披露截止（年报次年4-30 / 一季报4-30 / 半年报8-31 / 三季报10-31）
决定正常公司"最新已披露报告期"距当日上限为 211 天（4月29日，最新可得仍是上年三季报）。
270 = 211 + 59 天余量。本文件把这个推导钉住。
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from screening import financials as fin
from screening import scan_signals_ytd as scan

# A 股法定披露截止日
DISCLOSURE_DEADLINES = [
    ("上年三季报", date(2025, 9, 30), date(2025, 10, 31)),
    ("上年年报", date(2025, 12, 31), date(2026, 4, 30)),
    ("一季报", date(2026, 3, 31), date(2026, 4, 30)),
    ("半年报", date(2026, 6, 30), date(2026, 8, 31)),
    ("三季报", date(2026, 9, 30), date(2026, 10, 31)),
]


def newest_visible_age(probe: date) -> int:
    """probe 日能看到的最新报告期距 probe 的天数。"""
    avail = [(n, rd) for n, rd, dl in DISCLOSURE_DEADLINES if dl <= probe]
    _, rd = max(avail, key=lambda x: x[1])
    return (probe - rd).days


class TestThresholdDerivation:
    """270 不是拍脑袋：它由法定披露规则推导而来。"""

    def test_normal_company_age_ceiling_is_211(self):
        """正常公司报告期陈旧度上限出现在 4/29（年报与一季报都未到截止）。"""
        worst = max(newest_visible_age(p) for p in (
            date(2026, 1, 15), date(2026, 3, 31), date(2026, 4, 29),
            date(2026, 4, 30), date(2026, 6, 15), date(2026, 8, 30),
            date(2026, 8, 31), date(2026, 10, 30)))
        assert worst == 211
        assert newest_visible_age(date(2026, 4, 29)) == 211

    def test_threshold_leaves_margin_over_ceiling(self):
        """阈值必须高于 211，否则会误伤正常公司。"""
        assert fin.REPORT_MAX_AGE_DAYS > 211
        assert fin.REPORT_MAX_AGE_DAYS == 270, "改阈值需同步更新本测试与注释里的推导"

    def test_grey_window_is_bounded(self):
        """已停止披露却仍被判优的窗口 = 阈值 - 211。"""
        assert fin.REPORT_MAX_AGE_DAYS - 211 == 59

    def test_single_definition_shared_by_both_paths(self):
        """两条路径必须共用一份阈值，口径漂移会让同一只票判定相反。

        不能用 `is` 比较——270 超出 CPython 小整数缓存范围(-5~256)，同值也是不同对象。
        改为值相等 + 断言 scan 侧是 import 进来的、没有二次赋值。
        """
        assert scan.REPORT_MAX_AGE_DAYS == fin.REPORT_MAX_AGE_DAYS
        import inspect
        src = inspect.getsource(scan)
        assert "from financials import REPORT_MAX_AGE_DAYS" in src
        assert "REPORT_MAX_AGE_DAYS = " not in src, "阈值不得在 scan 侧二次定义"


class TestFinancialFactorStaleness:
    """live 路径：financial_factor 按 report_date 判时效。"""

    COLMAP = {"code": "证券代码", "report_date": "报告期", "net_profit": "净利润",
              "op_cashflow": "经营现金流", "roe": "ROE"}

    def _df(self, report_date):
        return pd.DataFrame([{
            "证券代码": "600000", "报告期": report_date, "净利润": 1.0e8,
            "经营现金流": 5.0e7, "ROE": 12.5,
        }])

    def test_fresh_report_is_available(self):
        r = fin.financial_factor("600000", self._df("2026-06-30"), self.COLMAP,
                                 as_of="2026-08-03")
        assert r["available"] is True
        assert r["report_stale"] is False and r["stale_check"] == "ok"

    def test_stale_report_is_rejected(self):
        """壳公司场景：2023Q3 后停止披露，2026 年仍会取到那期。"""
        r = fin.financial_factor("600000", self._df("2023-09-30"), self.COLMAP,
                                 as_of="2026-08-03")
        assert r["available"] is False
        assert r["reason"] == "report_stale"
        assert r["report_age_days"] == 1038

    def test_boundary_at_threshold(self):
        """恰好等于阈值算新鲜，超过一天才算陈旧。"""
        base = date(2026, 8, 3)
        at = base.toordinal() - fin.REPORT_MAX_AGE_DAYS
        over = at - 1
        r_at = fin.financial_factor("600000", self._df(date.fromordinal(at).isoformat()),
                                    self.COLMAP, as_of=base.isoformat())
        r_over = fin.financial_factor("600000", self._df(date.fromordinal(over).isoformat()),
                                      self.COLMAP, as_of=base.isoformat())
        assert r_at["available"] is True, "恰好 270 天不该被判陈旧"
        assert r_over["available"] is False and r_over["reason"] == "report_stale"

    def test_missing_report_date_does_not_assume_fresh(self):
        """没有报告期列时不假定新鲜，交调用方裁决。"""
        cm = dict(self.COLMAP)
        cm.pop("report_date")
        r = fin.financial_factor("600000", self._df("2023-09-30"), cm, as_of="2026-08-03")
        assert r["available"] is True                  # 不硬否决
        assert r["report_stale"] is None
        assert r["stale_check"] == "no_report_date"    # 但如实标注无法判定

    def test_check_can_be_disabled(self):
        r = fin.financial_factor("600000", self._df("2023-09-30"), self.COLMAP,
                                 as_of="2026-08-03", max_age_days=0)
        assert r["available"] is True and r["stale_check"] == "disabled"


class TestTierYouStaleness:
    """回测路径：_tier_you 按 report_date 判时效。"""

    def _idx(self, notice, report):
        """构造 PIT 索引：数字全部达标，只让时效变化。"""
        return {"600000": [(notice, report, 1.0e8, 0.5, 12.5)]}

    def test_fresh_report_is_tier_you(self):
        idx = self._idx("2026-07-15", "2026-06-30")
        assert scan._tier_you(idx, "600000", "2026-08-03") is True

    def test_stale_report_is_not_tier_you(self):
        """一家 2023-10 出了最后一期财报、之后再没披露的公司。"""
        idx = self._idx("2023-10-25", "2023-09-30")
        assert scan._tier_you(idx, "600000", "2026-08-03") is False

    def test_same_data_was_tier_you_before_fix(self):
        """留证：关掉时效检查即回到修复前行为，说明这条真的会命中。"""
        idx = self._idx("2023-10-25", "2023-09-30")
        assert scan._tier_you(idx, "600000", "2026-08-03", max_age_days=0) is True

    def test_boundary_at_threshold(self):
        day = date(2026, 8, 3)
        at = date.fromordinal(day.toordinal() - fin.REPORT_MAX_AGE_DAYS).isoformat()
        over = date.fromordinal(day.toordinal() - fin.REPORT_MAX_AGE_DAYS - 1).isoformat()
        assert scan._tier_you(self._idx("2026-01-01", at), "600000", day.isoformat()) is True
        assert scan._tier_you(self._idx("2026-01-01", over), "600000", day.isoformat()) is False

    def test_missing_report_date_is_conservative(self):
        """报告期缺失(旧台账)时不给"优"——正向判定遇未知取保守。"""
        assert scan._tier_you(self._idx("2026-07-15", ""), "600000", "2026-08-03") is False

    def test_bad_numbers_still_rejected(self):
        """时效通过也不代表达标，数字条件照样要满足。"""
        idx = {"600000": [("2026-07-15", "2026-06-30", -1.0e8, 0.5, 12.5)]}
        assert scan._tier_you(idx, "600000", "2026-08-03") is False


class TestPitSemanticsPreserved:
    """加时效不能破坏原有的 as-of 语义（notice_date 必须留在元组首位）。"""

    def _idx(self):
        return {"600000": [
            ("2026-04-29", "2026-03-31", 1.0e8, 0.5, 12.5),   # 一季报
            ("2026-08-28", "2026-06-30", 2.0e8, 0.8, 15.0),   # 半年报
        ]}

    def test_picks_latest_visible_period(self):
        """8月3日只能看到一季报，看不到8月28日才公告的半年报。"""
        assert scan._tier_you(self._idx(), "600000", "2026-08-03") is True

    def test_announcement_day_not_yet_visible(self):
        """公告当日不可见（偏严一档，无 look-ahead）。"""
        idx = {"600000": [("2026-08-03", "2026-06-30", 1.0e8, 0.5, 12.5)]}
        assert scan._tier_you(idx, "600000", "2026-08-03") is False

    def test_day_after_announcement_is_visible(self):
        idx = {"600000": [("2026-08-03", "2026-06-30", 1.0e8, 0.5, 12.5)]}
        assert scan._tier_you(idx, "600000", "2026-08-04") is True

    def test_no_data_before_first_announcement(self):
        assert scan._tier_you(self._idx(), "600000", "2026-01-05") is False

    def test_unknown_code(self):
        assert scan._tier_you(self._idx(), "999999", "2026-08-03") is False


class TestPitIndexStructure:
    """_pit_index 的元组结构：notice_date 必须在首位，否则 bisect 语义静默失效。"""

    def test_index_carries_report_date(self, tmp_path):
        p = tmp_path / "pit.jsonl"
        p.write_text(json.dumps({
            "code": "600000", "notice_date": "2026-07-15", "report_date": "2026-06-30",
            "net_profit": 1.0e8, "ocf_ps": 0.5, "roe_waa": 12.5,
        }) + "\n", encoding="utf-8")
        idx = scan._pit_index(p)
        assert idx["600000"][0][0] == "2026-07-15", "notice_date 必须在首位"
        assert idx["600000"][0][1] == "2026-06-30"
        assert len(idx["600000"][0]) == 5

    def test_legacy_record_without_report_date(self, tmp_path):
        p = tmp_path / "pit.jsonl"
        p.write_text(json.dumps({
            "code": "600000", "notice_date": "2026-07-15",
            "net_profit": 1.0e8, "ocf_ps": 0.5, "roe_waa": 12.5,
        }) + "\n", encoding="utf-8")
        idx = scan._pit_index(p)
        assert idx["600000"][0][1] == "", "旧台账缺 report_date 时置空串"

    def test_sorted_by_notice_date(self, tmp_path):
        p = tmp_path / "pit.jsonl"
        rows = [
            {"code": "600000", "notice_date": "2026-08-28", "report_date": "2026-06-30",
             "net_profit": 2.0e8, "ocf_ps": 0.8, "roe_waa": 15.0},
            {"code": "600000", "notice_date": "2026-04-29", "report_date": "2026-03-31",
             "net_profit": 1.0e8, "ocf_ps": 0.5, "roe_waa": 12.5},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        idx = scan._pit_index(p)
        assert [e[0] for e in idx["600000"]] == ["2026-04-29", "2026-08-28"]

    def test_skips_records_without_notice_date(self, tmp_path):
        p = tmp_path / "pit.jsonl"
        p.write_text(json.dumps({
            "code": "600000", "report_date": "2026-06-30", "net_profit": 1.0e8,
        }) + "\n", encoding="utf-8")
        assert scan._pit_index(p) == {}
