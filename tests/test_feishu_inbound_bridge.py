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


def test_has_trade_classification_and_no_trade_precedence():
    assert bridge._classify("有交易") == ("has_trade", None)
    assert bridge._classify("有交易 0AMV-0.87%") == ("trade_amv", -0.87)
    assert bridge._classify("没有交易") == ("no_trade", None)
    assert bridge._classify("无交易 0AMV -0.87%") == ("both", -0.87)


def test_has_trade_refreshes_tzzb_on_message_day(env, monkeypatch):
    ts = _ts_days_ago(0, 16)
    refreshed: list[str] = []
    monkeypatch.setattr(
        bridge,
        "_refresh_tzzb_trades",
        lambda day: refreshed.append(day) or "投资账本已核对",
    )

    out = _run_with(monkeypatch, [_text_msg(ts, "有交易 0AMV-0.87%", "m7")])

    assert refreshed == [_day_of_ts(ts)]
    assert out["registered"] == [(_day_of_ts(ts), -0.87)]
    assert "投资账本已核对" in out["artifact"]["actions"]
    assert not bridge.CONFIRM_PATH.exists()


def test_tzzb_failure_leaves_trade_fact_unwritten_and_notifies(env, monkeypatch):
    ts = _ts_days_ago(0, 16)

    def fail(_day):
        raise RuntimeError("PC 同步未覆盖收盘")

    monkeypatch.setattr(bridge, "_refresh_tzzb_trades", fail)
    out = _run_with(monkeypatch, [_text_msg(ts, "有交易", "m8")])

    assert out["artifact"]["actions"] == []
    assert "投资账本刷新失败" in out["artifact"]["notes"]
    assert "未写台账" in out["artifact"]["notes"]
    assert not bridge.CONFIRM_PATH.exists()
    assert any("投资账本刷新失败" in line for line in out["sent"][0].splitlines())


def _write_ledger_source(repo: Path, day: str, note: str) -> None:
    path = repo / "data" / "trades" / "master_trade_ledger.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"成交日期,备注\n{day},{note}\n", encoding="utf-8-sig")


def test_tzzb_refresh_rejects_existing_csv_rows(env):
    day = datetime.now(bridge.CST).date().isoformat()
    _write_ledger_source(env, day, "券商表格")

    with pytest.raises(RuntimeError, match="禁止叠加导入"):
        bridge._assert_trade_source_compatible(day, "tzzb")
    bridge._assert_trade_source_compatible(day, "csv")


def test_csv_upload_rejects_existing_tzzb_rows(env):
    day = datetime.now(bridge.CST).date().isoformat()
    _write_ledger_source(env, day, "投资账本MCP（接口无成交时间字段）")

    bridge._assert_trade_source_compatible(day, "tzzb")
    with pytest.raises(RuntimeError, match="禁止再叠加券商表格"):
        bridge._assert_trade_source_compatible(day, "csv")


def test_new_trades_reject_existing_no_trade_confirmation(env):
    day = datetime.now(bridge.CST).date().isoformat()
    bridge.CONFIRM_PATH.write_text(
        json.dumps({day: {"no_trades": True}}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="与新成交冲突"):
        bridge._assert_trade_source_compatible(day, "tzzb")


def test_stdout_json_accepts_pretty_printed_audit():
    result = bridge.subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {"appended_rows": 25, "transaction_ids": ["a-001"]}, indent=2
        ),
    )

    assert bridge._stdout_json(result, "投资账本入库")["appended_rows"] == 25
