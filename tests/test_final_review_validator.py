# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import pytest

from custos.pipeline.close_review import final_review_validator as frv
from custos.pipeline.close_review.final_review_validator import (
    REQUIRED_SECTIONS,
    validate,
)


def valid_payload():
    return {
        "date": "2026-07-15",
        "report_quality": "degraded",
        "news_digest": {
            "permission_rule": "news cannot directly increase trading permissions"
        },
        "execution_review": {"rows": []},
        "theme_lifecycles": [],
        "market_quality_checks": [],
        "revalued_positions": [],
        "next_day_plan": {"holding_plans": []},
        "rule_review": {},
        "unavailable": ["turnover"],
    }


class FinalReviewValidatorTests(unittest.TestCase):
    def test_valid_degraded_report(self):
        self.assertEqual(
            validate("2026-07-15", "\n".join(REQUIRED_SECTIONS), valid_payload()), []
        )

    def test_complete_cannot_hide_missing_inputs(self):
        payload = valid_payload()
        payload["report_quality"] = "complete"
        self.assertIn(
            "complete report cannot contain unavailable inputs",
            validate("2026-07-15", "\n".join(REQUIRED_SECTIONS), payload),
        )

    def test_missing_section_fails(self):
        errors = validate("2026-07-15", "", valid_payload())
        self.assertTrue(any(x.startswith("markdown section missing") for x in errors))


class TestArtifactNameFallback:
    """旧名回退钉测（2026-08-31 review 低优先项）：v0.141 起报告文件名带 1700
    时点标记，其他读方（weekly_review._load_daily_review_json）都做了三路径
    回退，validator 之前只认新名 —— 校验历史日期不该直接报缺。

    v0.179 起 .json 机器接口改道 data/review/（名不带时点标记）为第一候选，
    reports/ 三路径回退口径不变；.md 不落 data/review/，候选仍是 reports/ 三路径。"""

    DAY = "2026-07-15"

    @pytest.fixture(autouse=True)
    def rev(self, tmp_path, monkeypatch):
        """把产物根目录换到 tmp（daily_report_dir(day, REV) = REV/day）。"""
        monkeypatch.setattr(frv, "REV", tmp_path)
        # .json 新落点一并改道 tmp 下空目录——不 patch 会命中真实 data/review/
        monkeypatch.setattr(frv, "REVIEW_JSON_DIR", tmp_path / "data" / "review")
        return tmp_path

    def _mk(self, rev, rel):
        p = rev / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        return p

    def test_new_name_preferred(self, rev):
        new = self._mk(rev, f"{self.DAY}/{self.DAY}_1700_final_review.md")
        self._mk(rev, f"{self.DAY}/{self.DAY}_final_review.md")
        assert frv._resolve_artifact(self.DAY, "md") == new

    def test_old_name_fallback(self, rev):
        """历史日期只有旧名（无 1700 标记）⇒ 回退命中，不报缺。"""
        old = self._mk(rev, f"{self.DAY}/{self.DAY}_final_review.md")
        assert frv._resolve_artifact(self.DAY, "md") == old

    def test_flat_legacy_layout_fallback(self, rev):
        """更早的平铺布局（2026-08-12 目录重构前）⇒ 第三路径回退。"""
        flat = self._mk(rev, f"{self.DAY}_final_review.json")
        assert frv._resolve_artifact(self.DAY, "json") == flat

    def test_json_data_review_dir_preferred(self, rev):
        """v0.179 起 .json 落 data/review/：与 reports/ 旧位置并存时新路径优先。"""
        new = self._mk(rev, f"data/review/{self.DAY}_final_review.json")
        self._mk(rev, f"{self.DAY}/{self.DAY}_1700_final_review.json")
        assert frv._resolve_artifact(self.DAY, "json") == new

    def test_json_reports_location_still_resolves(self, rev):
        """新路径没有时 .json 回退 reports/ 旧位置（历史产物不搬）。"""
        old = self._mk(rev, f"{self.DAY}/{self.DAY}_1700_final_review.json")
        assert frv._resolve_artifact(self.DAY, "json") == old

    def test_all_missing_returns_none(self, rev):
        assert frv._resolve_artifact(self.DAY, "md") is None


if __name__ == "__main__":
    unittest.main()
