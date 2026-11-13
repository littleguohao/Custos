# -*- coding: utf-8 -*-
"""投资账本 MCP 成交适配器钉测；所有网络与 OAuth 均注入，不触真实台账。"""

from __future__ import annotations

import csv
import json
import urllib.error
from pathlib import Path

import pytest

from custos.datasource import tzzb_trades as tt

DAY = "2026-11-13"


def _entry(side: str = "买入", **overrides) -> dict:
    row = {
        "entryDate": DAY,
        "opName": side,
        "stockCode": "600000",
        "stockName": "浦发银行",
        "entryCount": "100",
        "entryPrice": "10.0",
        "entryMoney": "1000.0",
        "feeTotal": "0.0",
    }
    row.update(overrides)
    return row


def test_normalize_trades_standard_fields_and_nontrade_skip():
    rows = tt.normalize_trades(
        {
            "list": [
                _entry(),
                _entry("卖出", entryMoney="1000.0"),
                _entry("红利入账"),
            ]
        },
        DAY,
    )

    assert len(rows) == 2
    assert rows[0]["发生金额"] == -1000.0
    assert rows[1]["发生金额"] == 1000.0
    assert all(row["成交时间"] == "" for row in rows)
    assert all(row["费用"] == 0.0 for row in rows)
    assert all(row["代码"] == "600000" for row in rows)


def test_normalize_rejects_wrong_date():
    with pytest.raises(tt.TzzbError, match="其他日期"):
        tt.normalize_trades({"list": [_entry(entryDate="2026-11-12")]}, DAY)


def test_normalize_rejects_amount_mismatch():
    with pytest.raises(tt.TzzbError, match="成交金额不符"):
        tt.normalize_trades({"list": [_entry(entryMoney="999.0")]}, DAY)


def test_normalize_requires_known_fee():
    with pytest.raises(tt.TzzbError, match="feeTotal"):
        tt.normalize_trades({"list": [_entry(feeTotal="")]}, DAY)


@pytest.mark.parametrize("last_sync", ["2026-11-13 15:00:00", "2026-11-13 21:30:00"])
def test_sync_time_covers_close(last_sync: str):
    tt.validate_sync_time(last_sync, DAY)


@pytest.mark.parametrize(
    "last_sync", ["2026-11-13 14:59:59", "2026-11-12 18:00:00", ""]
)
def test_sync_time_rejects_incomplete_close(last_sync: str):
    with pytest.raises(tt.TzzbError, match="未覆盖|lastSyncTime 无效"):
        tt.validate_sync_time(last_sync, DAY)


def test_sse_payload_uses_last_data_event():
    body = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
    assert tt._json_payload(body, "text/event-stream")["result"] == {"ok": True}


def test_single_stock_account_is_selected_and_saved(tmp_path: Path, monkeypatch):
    path = tmp_path / "oauth.json"
    path.write_text(json.dumps({"client_id": "client"}), encoding="utf-8")
    monkeypatch.delenv("TZZB_FUND_KEY", raising=False)
    monkeypatch.setattr(
        tt,
        "mcp_call",
        lambda *args, **kwargs: {
            "fund_key": [{"id": "fund-1", "lastSyncTime": "2026-11-13 15:01:00"}]
        },
    )

    assert tt.resolve_account(path=path) == ("fund-1", "2026-11-13 15:01:00")
    assert json.loads(path.read_text(encoding="utf-8"))["fund_key"] == "fund-1"


def test_account_selection_preserves_tokens_refreshed_during_call(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "oauth.json"
    path.write_text(
        json.dumps({"client_id": "client", "access_token": "old"}), encoding="utf-8"
    )
    monkeypatch.delenv("TZZB_FUND_KEY", raising=False)

    def refreshed_call(*args, **kwargs):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(access_token="new", refresh_token="new-refresh")
        path.write_text(json.dumps(data), encoding="utf-8")
        return {"fund_key": [{"id": "fund-1", "lastSyncTime": "2026-11-13 15:01:00"}]}

    monkeypatch.setattr(tt, "mcp_call", refreshed_call)
    tt.resolve_account(path=path)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["fund_key"] == "fund-1"
    assert saved["access_token"] == "new"
    assert saved["refresh_token"] == "new-refresh"


def test_multiple_accounts_require_explicit_id(tmp_path: Path, monkeypatch):
    path = tmp_path / "oauth.json"
    path.write_text(json.dumps({"client_id": "client"}), encoding="utf-8")
    monkeypatch.delenv("TZZB_FUND_KEY", raising=False)
    monkeypatch.setattr(
        tt,
        "mcp_call",
        lambda *args, **kwargs: {"fund_key": [{"id": "f1"}, {"id": "f2"}]},
    )
    with pytest.raises(tt.TzzbError, match="TZZB_FUND_KEY"):
        tt.resolve_account(path=path)


def test_fetch_trades_reads_all_pages():
    pages = []

    def fake_call(tool, arguments, **kwargs):
        assert tool == "queryMoneyHistory"
        pages.append(arguments["page"])
        page = int(arguments["page"])
        return {
            "maxPage": "2",
            "list": [_entry(entryCount=str(page * 100), entryMoney=str(page * 1000.0))],
        }

    rows, sync = tt.fetch_trades(
        DAY,
        account_fn=lambda **kwargs: ("fund-1", "2026-11-13 15:01:00"),
        call_fn=fake_call,
    )

    assert pages == ["1", "2"]
    assert [row["成交数量"] for row in rows] == [100.0, 200.0]
    assert sync == "2026-11-13 15:01:00"


def test_mcp_call_refreshes_once_on_401(tmp_path: Path, monkeypatch):
    attempts = []
    refreshed = []

    def fake_once(tool, arguments, path):
        attempts.append(tool)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(tt.MCP_URL, 401, "unauthorized", {}, None)
        return {"ok": True}

    monkeypatch.setattr(tt, "_mcp_call_once", fake_once)
    monkeypatch.setattr(
        tt,
        "_refresh_oauth",
        lambda path, force=False: refreshed.append(force) or {},
    )

    assert tt.mcp_call("queryMoneyHistory", {}, path=tmp_path) == {"ok": True}
    assert attempts == ["queryMoneyHistory", "queryMoneyHistory"]
    assert refreshed == [True]


def test_write_trade_csv_roundtrip_and_empty_guard(tmp_path: Path):
    path = tmp_path / "trades.csv"
    rows = tt.normalize_trades({"list": [_entry()]}, DAY)
    tt.write_trade_csv(rows, path)

    with path.open(encoding="utf-8-sig", newline="") as fh:
        saved = list(csv.DictReader(fh))
    assert len(saved) == 1
    assert saved[0]["成交时间"] == ""
    assert saved[0]["发生金额"] == "-1000.0"

    from custos.core.trades import incremental_ledger as ledger

    normalized = ledger.norm(ledger.read_input(path))
    assert normalized.iloc[0]["成交时间"] == ""

    with pytest.raises(tt.TzzbError, match="拒绝写空"):
        tt.write_trade_csv([], path)


def test_repeated_identical_fills_use_multiset_incremental_semantics():
    import pandas as pd

    from custos.core.trades import incremental_ledger as ledger

    rows = tt.normalize_trades({"list": [_entry(), _entry()]}, DAY)
    incoming = ledger.norm(pd.DataFrame(rows))
    incoming["_fingerprint"] = incoming.apply(ledger.fingerprint, axis=1)

    first = ledger.select_new_rows(incoming, [])
    assert len(first) == 2
    existing = first["_fingerprint"].tolist()
    assert ledger.select_new_rows(incoming, existing).empty

    expanded = pd.concat([incoming, incoming.iloc[:1]], ignore_index=True)
    extra = ledger.select_new_rows(expanded, existing)
    assert len(extra) == 1
