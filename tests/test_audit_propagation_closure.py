# -*- coding: utf-8 -*-
"""传导链闭合回归测试。

整仓审计的核心结论是:系统的检测能力充足,但**降级信息不传导**——检测到异常后
落盘一个标记,然后用默认值继续跑,下游把默认值当真值。修上游而不接下游,等于
只把问题从"没检测"变成"检测了但没人听"。

本文件专门守两条曾经断开的链:
  C5  compass_amv 说 unverified → sync_compass_amv 必须据此降级
  C6  concept_tags 说 stale     → enrich_candidates 必须据此告警
"""

from __future__ import annotations

import json

import pytest


class TestCompassQualityReachesLedger:
    """C5: 上游识别未经真值校验时,台账不得记 confirmed,也不得自动填 amv_0day。"""

    def _records(self):
        return [{"date": "2026-08-03", "change_pct": 4.5}]

    def test_unverified_is_written_as_unverified(self, tmp_path):
        from custos.datasource import sync_compass_amv as sca

        led = tmp_path / "0amv_observations.jsonl"
        added, _ = sca.merge_ledger(self._records(), led, quality="unverified")
        assert added == 1
        rec = json.loads(led.read_text(encoding="utf-8").strip())
        assert rec["quality"] == "unverified", "fallback 选链不得冒充真值"

    def test_verified_still_confirmed(self, tmp_path):
        from custos.datasource import sync_compass_amv as sca

        led = tmp_path / "0amv_observations.jsonl"
        sca.merge_ledger(self._records(), led, quality="confirmed")
        rec = json.loads(led.read_text(encoding="utf-8").strip())
        assert rec["quality"] == "confirmed"

    def test_default_remains_confirmed_for_back_compat(self, tmp_path):
        from custos.datasource import sync_compass_amv as sca

        led = tmp_path / "0amv_observations.jsonl"
        sca.merge_ledger(self._records(), led)
        assert (
            json.loads(led.read_text(encoding="utf-8").strip())["quality"]
            == "confirmed"
        )

    def test_main_downgrades_and_skips_autofill_when_unverified(
        self, tmp_path, monkeypatch, capsys
    ):
        """端到端:parse_amv_daily 报 unverified → 台账降级 + 不写 amv_0day。"""
        from custos.datasource import sync_compass_amv as sca

        led = tmp_path / "led.jsonl"
        market = tmp_path / "market"
        market.mkdir()
        target = "2026-08-03"
        (market / f"{target}_market_timing_input.json").write_text(
            "{}", encoding="utf-8"
        )

        monkeypatch.setattr(sca, "LEDGER", led)
        monkeypatch.setattr(sca, "MARKET_DIR", market)
        monkeypatch.setattr(
            sca, "trading_day_status", lambda d: {"is_trading_day": True}
        )
        monkeypatch.setattr(
            sca.compass_amv,
            "parse_amv_daily",
            lambda since=None, root=None, truth_path=None: {
                "records": [{"date": target, "change_pct": 4.5}],
                "latest_date": target,
                "quality": "unverified",
                "identification": "fallback_longest",
            },
        )
        rc = sca.main(["--date", target])
        out = capsys.readouterr()
        assert rc == 0  # best-effort,不炸管线
        assert (
            json.loads(led.read_text(encoding="utf-8").strip())["quality"]
            == "unverified"
        )
        mkt = json.loads(
            (market / f"{target}_market_timing_input.json").read_text(encoding="utf-8")
        )
        assert "amv_0day" not in mkt, "未校验的读数不得自动填充,应回落人工确认"
        assert "未经真值校验" in out.out

    def test_main_autofills_when_verified(self, tmp_path, monkeypatch):
        from custos.datasource import sync_compass_amv as sca

        led = tmp_path / "led.jsonl"
        market = tmp_path / "market"
        market.mkdir()
        target = "2026-08-03"
        (market / f"{target}_market_timing_input.json").write_text(
            "{}", encoding="utf-8"
        )
        monkeypatch.setattr(sca, "LEDGER", led)
        monkeypatch.setattr(sca, "MARKET_DIR", market)
        monkeypatch.setattr(
            sca, "trading_day_status", lambda d: {"is_trading_day": True}
        )
        monkeypatch.setattr(
            sca.compass_amv,
            "parse_amv_daily",
            lambda since=None, root=None, truth_path=None: {
                "records": [{"date": target, "change_pct": 4.5}],
                "latest_date": target,
                "quality": "verified",
                "identification": "truth_match",
            },
        )
        sca.main(["--date", target])
        mkt = json.loads(
            (market / f"{target}_market_timing_input.json").read_text(encoding="utf-8")
        )
        assert mkt["amv_0day"] == 4.5


class TestConceptTagStalenessReachesConsumer:
    """C6: 标签陈旧必须能被消费方看到,不能只落在文件里。"""

    def test_load_tags_meta_reports_staleness(self, tmp_path, monkeypatch):
        from custos.datasource.local_tdx import concept_tags

        out = tmp_path / "tags.json"
        out.write_text(
            json.dumps(
                {
                    "date": "2026-07-27",
                    "stale": True,
                    "requested_date": "2026-08-03",
                    "tags": {"600000": ["银行"]},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(concept_tags, "OUT_PATH", out)
        tags, meta = concept_tags.load_tags_meta()
        assert tags == {"600000": ["银行"]}
        assert meta["stale"] is True
        assert meta["date"] == "2026-07-27" and meta["requested_date"] == "2026-08-03"

    def test_fresh_tags_not_marked_stale(self, tmp_path, monkeypatch):
        from custos.datasource.local_tdx import concept_tags

        out = tmp_path / "tags.json"
        out.write_text(
            json.dumps({"date": "2026-08-03", "tags": {"600000": ["银行"]}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(concept_tags, "OUT_PATH", out)
        _, meta = concept_tags.load_tags_meta()
        assert meta["available"] is True and meta["stale"] is False

    def test_missing_file_is_distinguishable_from_stale(self, tmp_path, monkeypatch):
        from custos.datasource.local_tdx import concept_tags

        monkeypatch.setattr(concept_tags, "OUT_PATH", tmp_path / "nope.json")
        tags, meta = concept_tags.load_tags_meta()
        assert tags == {} and meta["available"] is False
        assert meta["reason"] == "tags_file_missing"

    def test_load_tags_still_returns_plain_dict(self, tmp_path, monkeypatch):
        """向后兼容:老调用方拿到的仍是 {code: [tags]}。"""
        from custos.datasource.local_tdx import concept_tags

        out = tmp_path / "tags.json"
        out.write_text(
            json.dumps({"date": "2026-08-03", "tags": {"600000": ["银行"]}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(concept_tags, "OUT_PATH", out)
        assert concept_tags.load_tags() == {"600000": ["银行"]}

    def test_enrich_warns_on_stale_tags(self, monkeypatch, capsys):
        """消费侧:陈旧标签必须产生告警,但标签本身仍要被使用(退化慢,可用性优先)。"""
        from custos.pipeline.screening import enrich_candidates as ec

        monkeypatch.setattr(ec.concept_tags, "load_tags", lambda: {"600000": ["银行"]})
        monkeypatch.setattr(
            ec.concept_tags,
            "load_tags_meta",
            lambda: (
                {"600000": ["银行"]},
                {
                    "available": True,
                    "stale": True,
                    "date": "2026-07-27",
                    "requested_date": "2026-08-03",
                },
            ),
        )
        monkeypatch.setattr(
            ec,
            "_load_json",
            lambda p, d: {
                "themes": [
                    {"theme_id": "T1", "theme_name": "银行", "semantic_tags": ["银行"]}
                ]
            },
        )
        stock_theme, ok = ec.build_stock_theme_map()
        err = capsys.readouterr().err
        assert "概念标签陈旧" in err
        assert "2026-07-27" in err, "告警须带上标签的真实日期,便于归因"
        assert stock_theme.get("600000", {}).get("theme_id") == "T1", "陈旧不等于弃用"
        assert ok is True

    def test_enrich_silent_when_tags_fresh(self, monkeypatch, capsys):
        from custos.pipeline.screening import enrich_candidates as ec

        monkeypatch.setattr(ec.concept_tags, "load_tags", lambda: {"600000": ["银行"]})
        monkeypatch.setattr(
            ec.concept_tags,
            "load_tags_meta",
            lambda: (
                {"600000": ["银行"]},
                {"available": True, "stale": False, "date": "2026-08-03"},
            ),
        )
        monkeypatch.setattr(
            ec,
            "_load_json",
            lambda p, d: {
                "themes": [
                    {"theme_id": "T1", "theme_name": "银行", "semantic_tags": ["银行"]}
                ]
            },
        )
        ec.build_stock_theme_map()
        assert "概念标签陈旧" not in capsys.readouterr().err


class TestReadmeContractMatchesCode:
    """D4 的文档面:README 不得再指向没有生产者的字段。"""

    def test_readme_points_at_real_confirmation_file(self):
        from custos.core.paths import BASE

        readme = (BASE / "README.md").read_text(encoding="utf-8")
        assert "position_confirmations.json` — 交易日无交易确认标记" in readme
        assert "`_import_meta.json` — 交易日无交易确认标记" not in readme, (
            "该键从未有生产者,曾导致 weekly_review 每周误报"
        )
