# -*- coding: utf-8 -*-
"""传导链闭合回归测试。

整仓审计的核心结论是:系统的检测能力充足,但**降级信息不传导**——检测到异常后
落盘一个标记,然后用默认值继续跑,下游把默认值当真值。修上游而不接下游,等于
只把问题从"没检测"变成"检测了但没人听"。

本文件守一条曾经断开的链:
  C5  compass_amv 说 unverified → sync_compass_amv 必须据此降级

（C6 concept_tags 的 stale 元数据传导链随数据源整体删除——v0.157 owner 拍板，
miscinfo 概念标签无在链消费方，模块已整删。）
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


class TestReadmeContractMatchesCode:
    """D4 的文档面:README 不得再指向没有生产者的字段。"""

    def test_readme_points_at_real_confirmation_file(self):
        from custos.core.paths import BASE

        readme = (BASE / "README.md").read_text(encoding="utf-8")
        assert "position_confirmations.json` — 交易日无交易确认标记" in readme
        assert "`_import_meta.json` — 交易日无交易确认标记" not in readme, (
            "该键从未有生产者,曾导致 weekly_review 每周误报"
        )
