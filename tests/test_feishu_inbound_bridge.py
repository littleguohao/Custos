# -*- coding: utf-8 -*-
"""飞书入站桥：消息日归属 / 幂等 / 极值守卫；测试不得触网、不得碰真实台账。"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "automation" / "feishu_inbound_bridge.py"
SPEC = importlib.util.spec_from_file_location("feishu_inbound_bridge", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def _ts_days_ago(days: int, hour: int) -> int:
    """N 天前 hour 点（CST）的消息时间戳（毫秒）。"""
    dt = datetime.now(bridge.CST) - timedelta(days=days)
    dt = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    return int(dt.timestamp() * 1000)


def _day_of_ts(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, bridge.CST).date().isoformat()


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """桥的全部外部依赖改道 tmp：状态/收件箱/仓库台账/凭据/网络。"""
    monkeypatch.setattr(bridge, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bridge, "STATE_PATH", tmp_path / "bridge_state.json")
    monkeypatch.setattr(bridge, "INBOX_PATH", tmp_path / "inbox.jsonl")
    monkeypatch.setattr(bridge, "REPO", tmp_path)
    monkeypatch.setattr(
        bridge, "CONFIRM_PATH", tmp_path / "position_confirmations.json"
    )
    monkeypatch.setattr(bridge, "_credentials", lambda: {"to_open_id": "ou_test"})
    monkeypatch.setattr(bridge, "_token", lambda _cred: "token")
    return tmp_path


def _run_with(monkeypatch: pytest.MonkeyPatch, messages: list[dict]) -> dict:
    sent: list[str] = []
    registered: list[tuple[str, float]] = []
    monkeypatch.setattr(bridge, "_fetch_new_user_messages", lambda _t, _s: messages)
    monkeypatch.setattr(bridge, "_send_text", lambda _c, _t, text: sent.append(text))
    monkeypatch.setattr(
        bridge, "_register_amv", lambda day, value: registered.append((day, value))
    )
    artifact = bridge.run(ctx={})["artifact"]
    return {"artifact": artifact, "sent": sent, "registered": registered}


def _amv_ledger(repo: Path, rows: list[dict]) -> None:
    path = repo / "data" / "market" / "0amv_observations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def _text_msg(ts_ms: int, text: str, mid: str) -> dict:
    return {"message_id": mid, "ts": ts_ms, "msg_type": "text", "text": text}


def test_amv_attributed_to_message_day_not_run_day(env, monkeypatch):
    ts = _ts_days_ago(1, 20)  # 昨晚 20:00 的回复，今日 15:15 才被接收
    out = _run_with(monkeypatch, [_text_msg(ts, "0AMV -1.18%", "m1")])

    assert out["registered"] == [(_day_of_ts(ts), -1.18)]
    today = datetime.now(bridge.CST).date().isoformat()
    assert out["registered"][0][0] != today
    inbox = bridge.INBOX_PATH.read_text(encoding="utf-8").strip()
    rec = json.loads(inbox)
    assert rec["date"] == _day_of_ts(ts)
    assert rec["run_date"] == today


def test_amv_skipped_when_message_day_already_confirmed(env, monkeypatch):
    ts = _ts_days_ago(1, 20)
    day = _day_of_ts(ts)
    _amv_ledger(
        env,
        [{"date": day, "as_of": day, "quality": "confirmed", "amv_change_pct": -1.0}],
    )
    out = _run_with(monkeypatch, [_text_msg(ts, "-1.18", "m2")])

    assert out["registered"] == []
    assert any("已有 confirmed" in a for a in out["artifact"]["actions"])


def test_amv_candidate_does_not_block_registration(env, monkeypatch):
    ts = _ts_days_ago(1, 20)
    day = _day_of_ts(ts)
    _amv_ledger(
        env,
        [{"date": day, "as_of": day, "quality": "candidate", "amv_change_pct": -1.0}],
    )
    out = _run_with(monkeypatch, [_text_msg(ts, "-1.18", "m3")])

    assert out["registered"] == [(day, -1.18)]


def test_extreme_amv_rejected_to_manual(env, monkeypatch):
    ts = _ts_days_ago(0, 16)
    out = _run_with(monkeypatch, [_text_msg(ts, "0AMV -18.2%", "m4")])

    assert out["registered"] == []
    assert "极值" in out["artifact"]["notes"]
    assert "--confirm-extreme" in out["artifact"]["notes"]
    assert any("极值" in line for line in out["sent"][0].splitlines())


def test_no_trade_attributed_to_message_day(env, monkeypatch):
    ts = _ts_days_ago(1, 21)
    out = _run_with(monkeypatch, [_text_msg(ts, "今天无交易", "m5")])

    assert out["registered"] == []
    data = json.loads(bridge.CONFIRM_PATH.read_text(encoding="utf-8"))
    assert list(data) == [_day_of_ts(ts)]
    assert data[_day_of_ts(ts)]["no_trades"] is True


def test_file_message_uses_message_day(env, monkeypatch):
    ts = _ts_days_ago(1, 19)
    handled: list[str] = []
    monkeypatch.setattr(
        bridge, "_handle_file", lambda _t, day, _m: handled.append(day) or "ok"
    )
    _run_with(
        monkeypatch,
        [{"message_id": "m6", "ts": ts, "msg_type": "file", "text": ""}],
    )
    assert handled == [_day_of_ts(ts)]
