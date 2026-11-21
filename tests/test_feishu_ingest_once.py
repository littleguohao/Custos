# -*- coding: utf-8 -*-
"""17:00 前一次性飞书补收入口：交易日门与单次执行钉测。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "automation" / "feishu_ingest_once.py"
SPEC = importlib.util.spec_from_file_location("feishu_ingest_once", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
once = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(once)

DAY = "2026-11-19"


def test_non_trading_day_skips_ingest():
    calls: list[dict] = []
    result = once.run_once(
        DAY,
        trading_day_fn=lambda _day: False,
        ingest_fn=lambda ctx: calls.append(ctx),
    )
    assert result["trading_day"] is False
    assert result["ingested"] is False
    assert result["reason"] == "non_trading_day"
    assert calls == []


def test_trading_day_ingests_exactly_once():
    calls: list[dict] = []

    def ingest(ctx):
        calls.append(ctx)
        return {"artifact": {"new_messages": 1, "actions": ["已登记"]}}

    result = once.run_once(DAY, trading_day_fn=lambda _day: True, ingest_fn=ingest)
    assert calls == [{}]
    assert result["date"] == DAY
    assert result["trading_day"] is True
    assert result["ingested"] is True
    assert result["new_messages"] == 1


def test_bad_bridge_payload_is_explicit_error():
    with pytest.raises(RuntimeError, match="artifact"):
        once.run_once(
            DAY,
            trading_day_fn=lambda _day: True,
            ingest_fn=lambda _ctx: {},
        )
