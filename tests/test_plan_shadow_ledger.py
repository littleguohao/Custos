# -*- coding: utf-8 -*-
"""plan_shadow_ledger（#60 影子判定台账，v0.310）钉测。

锁的契约：幂等 (date, code, stage)（同时点重跑跳过；1445/1700 两行都保留
不合并）、append-only + 每日快照 .bak_YYYYMMDD（0AMV 事故纪律）、落盘前
contracts.require（生产者硬失败）、default 来源 agree=None（不计入一致
率）、字段从 b1_state + extra 单源组装。
"""

from __future__ import annotations

import json

import pytest

from custos.pipeline.holdings import plan_shadow_ledger as psl


def _b1_state(shadow=None, final_priority="P1", final_action="减仓评估"):
    return {
        "final_priority": final_priority,
        "final_action": final_action,
        "shadow": shadow
        or {
            "reason": "ok",
            "plan_source": "candidate:2026-08-17",
            "plan_based_priority": "P0",
            "plan_based_action": "计划止损位清仓评估",
            "signals": [
                {
                    "signal": "plan_stop_breach",
                    "priority": "P0",
                    "action": "计划止损位清仓评估",
                    "reason": "x",
                }
            ],
        },
    }


def _plan(source="candidate:2026-08-17"):
    return {
        "entry_date": "2026-08-18",
        "entry_price": 10.0,
        "stop": {"rule_id": "stock_pool_stop_ref", "price": 9.3, "basis": "b"},
        "take_profit": {"scale_out_two_bull": {"params": {"require_above_bbi": True}}},
        "source": source,
    }


def _read(path):
    return [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def _extra(**kw):
    base = {"close": 9.0, "plan": _plan(), "report_priority": "P1"}
    base.update(kw)
    return base


class TestAppendIdempotent:
    def test_same_stage_twice_one_row(self, tmp_path):
        p = tmp_path / "led.jsonl"
        st = _b1_state()
        assert (
            psl.append_shadow("1445", "2026-10-09", "600000", st, _extra(), path=p)
            is True
        )
        assert (
            psl.append_shadow(
                "1445", "2026-10-09", "600000", st, _extra(close=9.1), path=p
            )
            is False
        )
        rows = _read(p)
        assert len(rows) == 1
        assert rows[0]["close"] == 9.0  # 首写留存（同时点重跑不覆盖）

    def test_two_stages_two_rows(self, tmp_path):
        p = tmp_path / "led.jsonl"
        st = _b1_state()
        assert psl.append_shadow("1445", "2026-10-09", "600000", st, _extra(), path=p)
        assert psl.append_shadow(
            "1700", "2026-10-09", "600000", st, _extra(close=9.2), path=p
        )
        rows = _read(p)
        assert [r["stage"] for r in rows] == ["1445", "1700"]  # 两时点都保留


class TestAppendOnlyAndBackup:
    def test_daily_snapshot_before_append(self, tmp_path):
        p = tmp_path / "led.jsonl"
        st = _b1_state()
        psl.append_shadow("1445", "2026-10-08", "600000", st, _extra(), path=p)
        psl.append_shadow("1445", "2026-10-09", "600000", st, _extra(), path=p)
        baks = list(tmp_path.glob("led.jsonl.bak_*"))
        assert len(baks) == 1, "第二次写入（文件已存在）前留当日快照"
        assert len(_read(baks[0])) == 1  # 快照=首写前内容（只含第一行）
        assert len(_read(p)) == 2  # 台账只增不减


class TestSchemaRequire:
    def test_bad_stage_rejected(self, tmp_path):
        with pytest.raises(SystemExit):
            psl.append_shadow(
                "9999",
                "2026-10-09",
                "600000",
                _b1_state(),
                _extra(),
                path=tmp_path / "led.jsonl",
            )


class TestRowSemantics:
    def test_default_agree_none(self, tmp_path):
        st = _b1_state(
            shadow={
                "reason": "plan_default",
                "plan_source": "default",
                "plan_based_priority": None,
                "plan_based_action": None,
                "signals": [],
            }
        )
        p = tmp_path / "led.jsonl"
        psl.append_shadow(
            "1700", "2026-10-09", "600000", st, _extra(plan=_plan("default")), path=p
        )
        row = _read(p)[0]
        assert row["agree"] is None, "default 视同无计划——不计入一致/不一致"
        assert row["plan_source"] == "default"
        assert row["plan_stop_price"] == 9.3  # 计划价格留痕（审计用）

    def test_agree_true_and_fields(self, tmp_path):
        st = _b1_state(final_priority="P0")  # shadow P0 == live P0
        p = tmp_path / "led.jsonl"
        psl.append_shadow(
            "1700", "2026-10-09", "600000", st, _extra(report_priority="P0"), path=p
        )
        row = _read(p)[0]
        assert row["agree"] is True
        assert row["shadow_signal"] == "plan_stop_breach"
        assert row["entry_price"] == 10.0
        assert row["live_final_priority"] == "P0"
        assert row["report_priority"] == "P0"
        assert row["schema"] == psl.SCHEMA
        assert row["recorded_at"]

    def test_agree_false(self, tmp_path):
        st = _b1_state(final_priority="P1")  # shadow P0 vs live P1
        p = tmp_path / "led.jsonl"
        psl.append_shadow("1700", "2026-10-09", "600000", st, _extra(), path=p)
        assert _read(p)[0]["agree"] is False
