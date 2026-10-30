"""内置个股资金流与主备切换测试（合成响应，不联网）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from custos.datasource import kimiwork_data as kd
from custos.datasource.collect import collect_fund_flow as cff

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "dev" / "fund_flow_shadow_diff.py"
)
_spec = importlib.util.spec_from_file_location("fund_flow_shadow_diff", _SCRIPT)
shadow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shadow)


def _raw(header: str, rows: list[str]) -> dict:
    return {
        "is_success": True,
        "files": [{"name": "x.csv", "content": header + "\n" + "\n".join(rows) + "\n"}],
    }


HEADER = (
    "代码,名称,最新价(元)(2026.10.30),涨跌幅(%)(2026.10.30),"
    "主力净额(元)(2026.10.30),主力净占比(%)(2026.10.30),"
    "超大单净额(元),超大单净占比(%),大单净额(元),大单净占比(%),"
    "中单净额(元),中单净占比(%),小单净额(元),小单净占比(%)"
)


def _row(code: str, amount: str = "1.00亿") -> str:
    return f"{code},测试,10,1.0,{amount},2.0,0.50亿,1.0,0.50亿,1.0,0,0,0,0"


def test_parse_chinese_amount_units():
    assert kd.parse_cn_amount("1.08万亿") == pytest.approx(1.08e12)
    assert kd.parse_cn_amount("-396.7亿") == pytest.approx(-396.7e8)
    assert kd.parse_cn_amount("456.7万") == pytest.approx(456.7e4)
    assert kd.parse_cn_amount("123") == 123.0
    assert kd.parse_cn_amount("-") is None
    with pytest.raises(ValueError):
        kd.parse_cn_amount("not-a-number")


def test_fund_flow_stocks_paginates_and_normalizes(monkeypatch):
    calls = []

    def fake_api(source, api, params, **kwargs):
        calls.append(params["query"])
        start = 1 if "第1至10" in params["query"] else 11
        rows = [_row(str(start + i).zfill(6)) for i in range(10)]
        return _raw(HEADER, rows)

    monkeypatch.setattr(kd, "call_api", fake_api)
    rows = kd.dongcai_fund_flow_stocks(limit=20, target_date="2026-10-30")
    assert len(rows) == 20 and len({x["code"] for x in rows}) == 20
    assert rows[0]["main_net_inflow"] == 100000000.0
    assert rows[0]["super_large_pct"] == 1.0
    assert len(calls) == 2 and "第11至20" in calls[1]


def test_fund_flow_rejects_missing_date(monkeypatch):
    monkeypatch.setattr(
        kd, "call_api", lambda *args, **kwargs: _raw("代码,名称", ["600000,测试"])
    )
    with pytest.raises(kd.KimiWorkDataError, match="日期"):
        kd.dongcai_fund_flow_stocks(limit=1, target_date="2026-10-30")


def test_fund_flow_rejects_duplicate_code(monkeypatch):
    monkeypatch.setattr(
        kd,
        "call_api",
        lambda *args, **kwargs: _raw(HEADER, [_row("600000"), _row("600000")]),
    )
    with pytest.raises(kd.KimiWorkDataError, match="重复"):
        kd.dongcai_fund_flow_stocks(limit=2, target_date="2026-10-30")


def test_fund_flow_rejects_chunk_gap(monkeypatch):
    monkeypatch.setattr(
        kd, "call_api", lambda *args, **kwargs: _raw(HEADER, [_row("600000")])
    )
    with pytest.raises(kd.KimiWorkDataError, match="缺口"):
        kd.dongcai_fund_flow_stocks(limit=2, target_date="2026-10-30")


def _sector_payload(url):
    return {"data": {"diff": [{"f12": "BK0001", "f14": "半导体", "f62": 2.0}]}}


def test_default_uses_push2_and_never_calls_natural_language(monkeypatch, tmp_path):
    monkeypatch.setattr(cff, "MARKET_DIR", tmp_path)
    monkeypatch.setattr(
        cff, "legacy_stock_rows", lambda: [{"code": "600000", "main_net_inflow": 1.0}]
    )
    monkeypatch.setattr(
        cff,
        "kimiwork_stock_rows",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("live 不应调用妙想")),
    )
    monkeypatch.setattr(cff, "fetch_json", _sector_payload)
    monkeypatch.setattr(cff.time, "sleep", lambda _: None)
    assert cff.main(["--date", "2026-10-30"]) == 0
    out = json.loads(
        (tmp_path / "2026-10-30_fund_flow_rank.json").read_text(encoding="utf-8")
    )
    assert out["stock_source"] == "eastmoney_direct_api"
    assert out["stock_fallback"] is False
    assert out["source"] == "eastmoney_direct_api"
    assert out["sector_rank_status"]["industry"]["status"] == "ok"


def test_kimiwork_source_is_explicit_diagnostic_only(monkeypatch, tmp_path):
    monkeypatch.setattr(cff, "MARKET_DIR", tmp_path)
    seen = {}

    def fake_rows(**kwargs):
        seen.update(kwargs)
        return [{"code": "600000", "main_net_inflow": 2.0}]

    monkeypatch.setattr(cff, "kimiwork_stock_rows", fake_rows)
    monkeypatch.setattr(cff, "fetch_json", _sector_payload)
    monkeypatch.setattr(cff.time, "sleep", lambda _: None)
    assert cff.main(["--date", "2026-10-30", "--source", "kimiwork"]) == 0
    out = json.loads(
        (tmp_path / "2026-10-30_fund_flow_rank.json").read_text(encoding="utf-8")
    )
    assert seen["target_date"] == "2026-10-30"
    assert out["stock_source"] == "kimiwork_dongcai_screener"
    assert out["stock_fallback"] is False


def test_shadow_compare_tracks_order_and_rounding():
    old = [
        {"code": "a", "main_net_inflow": 100000001.0},
        {"code": "b", "main_net_inflow": 2.0},
    ]
    new = [
        {"code": "a", "main_net_inflow": 100000000.0},
        {"code": "b", "main_net_inflow": 2.0},
    ]
    report = shadow.compare(old, new)
    assert report["common_codes"] == 2
    assert report["same_order_prefix"] == 2
    assert report["fields"]["main_net_inflow"]["n_compared"] == 2
