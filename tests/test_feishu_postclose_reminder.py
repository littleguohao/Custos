# -*- coding: utf-8 -*-
"""盘后补数提醒：只读台账，缺项才发送；测试不得触网。"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "automation" / "feishu_postclose_reminder.py"
SPEC = importlib.util.spec_from_file_location("feishu_postclose_reminder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
reminder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reminder)

DAY = "2026-11-02"


def _write_amv(repo: Path, rows: list[dict]) -> None:
    path = repo / "data" / "market" / "0amv_observations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_confirmations(repo: Path, records: dict) -> None:
    path = repo / "data" / "trades" / "position_confirmations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


def _write_ledger(repo: Path, rows: list[dict[str, str]]) -> None:
    path = repo / "data" / "trades" / "master_trade_ledger.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["成交日期", "成交时间", "代码", "名称", "交易类别"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_amv_requires_current_confirmed_unsuperseded_finite_value(tmp_path: Path):
    _write_amv(
        tmp_path,
        [
            {
                "date": DAY,
                "as_of": DAY,
                "quality": "confirmed",
                "amv_change_pct": 0,
            }
        ],
    )
    assert reminder._has_confirmed_amv(DAY, tmp_path) is True

    for invalid in (
        {"quality": "candidate"},
        {"superseded": True},
        {"as_of": "2026-10-30"},
        {"amv_change_pct": True},
    ):
        row = {
            "date": DAY,
            "as_of": DAY,
            "quality": "confirmed",
            "amv_change_pct": 3.94,
        }
        row.update(invalid)
        _write_amv(tmp_path, [row])
        assert reminder._has_confirmed_amv(DAY, tmp_path) is False

    _write_amv(
        tmp_path,
        [{"date": DAY, "quality": "confirmed", "amv_change_pct": math_nan()}],
    )
    assert reminder._has_confirmed_amv(DAY, tmp_path) is False


def math_nan() -> float:
    return float("nan")


def test_amv_missing_or_malformed_file_is_not_confirmed(tmp_path: Path):
    assert reminder._has_confirmed_amv(DAY, tmp_path) is False
    _write_amv(
        tmp_path,
        [{"date": "2026-10-30", "quality": "confirmed", "amv_change_pct": -2.17}],
    )
    assert reminder._has_confirmed_amv(DAY, tmp_path) is False


def test_trade_record_accepts_no_trade_confirmation(tmp_path: Path):
    _write_confirmations(tmp_path, {DAY: {"no_trades": True}})
    assert reminder._has_trade_record(DAY, tmp_path) is True

    _write_confirmations(tmp_path, {DAY: {"no_trades": False}})
    assert reminder._has_trade_record(DAY, tmp_path) is False


def test_trade_record_accepts_current_day_ledger_row(tmp_path: Path):
    _write_ledger(
        tmp_path,
        [
            {
                "成交日期": DAY,
                "成交时间": "13:26:18",
                "代码": "600312",
                "名称": "平高电气",
                "交易类别": "卖出",
            }
        ],
    )
    assert reminder._has_trade_record(DAY, tmp_path) is True
    assert reminder._has_trade_record("2026-11-03", tmp_path) is False


@pytest.mark.parametrize(
    ("status", "expected_missing", "expected_text"),
    [
        (
            {"amv": False, "trades": False},
            ["amv", "trades"],
            "请补充或确认当日交易记录与 0AMV",
        ),
        ({"amv": False, "trades": True}, ["amv"], "当前仅缺 0AMV"),
        ({"amv": True, "trades": False}, ["trades"], "当前仅缺交易记录"),
    ],
)
def test_missing_records_send_targeted_reminder(
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, bool],
    expected_missing: list[str],
    expected_text: str,
):
    sent: list[str] = []
    monkeypatch.setattr(reminder, "_is_trading_day", lambda _day: True)
    monkeypatch.setattr(reminder, "_record_status", lambda _day: status)
    monkeypatch.setattr(reminder, "_send_text", sent.append)

    artifact = reminder._run_for_day(DAY)

    assert artifact["sent"] is True
    assert artifact["missing"] == expected_missing
    assert len(sent) == 1
    assert expected_text in sent[0]


def test_complete_records_stay_silent(monkeypatch: pytest.MonkeyPatch):
    sent: list[str] = []
    monkeypatch.setattr(reminder, "_is_trading_day", lambda _day: True)
    monkeypatch.setattr(
        reminder, "_record_status", lambda _day: {"amv": True, "trades": True}
    )
    monkeypatch.setattr(reminder, "_send_text", sent.append)

    artifact = reminder._run_for_day(DAY)

    assert artifact == {
        "date": DAY,
        "trading_day": True,
        "sent": False,
        "amv_confirmed": True,
        "trade_recorded": True,
        "missing": [],
        "reason": "当日 0AMV 与交易记录均已确认，未发送",
    }
    assert sent == []


def test_non_trading_day_stays_silent_without_reading_ledgers(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(reminder, "_is_trading_day", lambda _day: False)
    monkeypatch.setattr(
        reminder,
        "_record_status",
        lambda _day: pytest.fail("非交易日不应读取台账"),
    )
    artifact = reminder._run_for_day(DAY)
    assert artifact["sent"] is False
    assert artifact["trading_day"] is False


def test_complete_message_cannot_be_generated():
    with pytest.raises(ValueError, match="记录齐全"):
        reminder._message(DAY, [])
