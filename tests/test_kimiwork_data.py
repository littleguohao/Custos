"""Kimi Work 内置数据源适配与生产链接入回归测试。"""

from __future__ import annotations

import json
import sys

import pytest

from custos.datasource import kimiwork_data as kd
from custos.datasource import overseas_market_collector as omc
from custos.datasource.collect import collect_incremental_market as cim
from custos.datasource.news import rss_collector as rc


def _csv_raw(content: str) -> dict:
    return {"is_success": True, "files": [{"name": "x.csv", "content": content}]}


def test_csv_adapters_parse_builtin_payloads(monkeypatch):
    monkeypatch.setattr(
        kd,
        "call_api",
        lambda *args, **kwargs: _csv_raw(
            "Date,Close,currency\n2026-10-28T04:00:00Z,100,USD\n"
            "2026-10-29T04:00:00Z,101,USD\n"
        ),
    )
    rows = kd.yahoo_daily("^GSPC")
    assert rows[-1]["Close"] == "101"

    monkeypatch.setattr(
        kd,
        "call_api",
        lambda *args, **kwargs: _csv_raw(
            "headName,325898\n2026-10-29(日),13032.68点\n"
        ),
    )
    assert kd.dongcai_a50_daily() == [{"date": "2026-10-29(日)", "close": "13032.68点"}]

    seen = {}

    def wind(data_source, api_name, params, **kwargs):
        seen["params"] = params
        return _csv_raw("code,date,value\nM0290205,20261029,6.65699\n")

    monkeypatch.setattr(kd, "call_api", wind)
    assert kd.wind_cnh_daily()[0]["value"] == "6.65699"
    assert seen["params"]["file_path"].endswith("usdcnh.csv")


def test_cls_adapter_filters_hidden_and_deleted(monkeypatch):
    payload = {
        "code": 0,
        "result": [
            {"id": 1, "status": 1, "content": "正常"},
            {"id": 2, "status": 2, "content": "隐藏"},
            {"id": 3, "status": 3, "content": "删除"},
        ],
    }
    raw = {
        "is_success": True,
        "result": {"user": [{"type": "text", "text": json.dumps(payload)}]},
    }
    monkeypatch.setattr(kd, "call_api", lambda *args, **kwargs: raw)
    assert [x["id"] for x in kd.cls_telegraphs(limit=3)] == [1]


def test_overseas_chart_from_kimiwork_daily(monkeypatch):
    monkeypatch.setattr(
        kd,
        "yahoo_daily",
        lambda symbol: [
            {"Date": "2026-10-28T04:00:00Z", "Close": "100", "currency": "USD"},
            {"Date": "2026-10-29T04:00:00Z", "Close": "101.5", "currency": "USD"},
        ],
    )
    out = omc._fetch_kimiwork_chart("NVDA")
    assert out["price"] == 101.5
    assert out["previous_close"] == 100
    assert out["change_pct"] == 1.5
    assert out["source"] == "Kimi Work / Yahoo Finance"
    assert out["last_timestamp"]


def test_incremental_a50_and_cnh_from_builtin(monkeypatch):
    monkeypatch.setattr(
        kd,
        "wind_a50_daily",
        lambda: [
            {"date": "20261028", "value": "13000"},
            {"date": "20261029", "value": "13032.68"},
        ],
    )
    a50 = cim.fetch_a50_kimiwork("2026-10-29")
    assert a50["change_pct"] == pytest.approx(0.25)
    assert a50["as_of"] == "2026-10-29"
    assert a50["proxy"] is True
    assert "非A50期货" in a50["proxy_note"]
    assert a50["source"] == "Kimi Work / Wind EDB L5471360"

    monkeypatch.setattr(
        kd,
        "wind_cnh_daily",
        lambda: [
            {"date": "20261028", "value": "6.66158"},
            {"date": "20261029", "value": "6.65699"},
        ],
    )
    cnh = cim.fetch_cnh_kimiwork("2026-10-29")
    assert cnh["change_pct"] == pytest.approx(-0.07)
    assert cnh["source"] == "Kimi Work / Wind EDB M0290205"


def test_incremental_rejects_stale_date(monkeypatch):
    monkeypatch.setattr(
        kd,
        "wind_cnh_daily",
        lambda: [
            {"date": "20261028", "value": "6.66158"},
            {"date": "20261029", "value": "6.65699"},
        ],
    )
    with pytest.raises(Exception, match="陈旧|不匹配"):
        cim.fetch_cnh_kimiwork("2026-10-30")


def test_incremental_rejects_price_outlier(monkeypatch):
    monkeypatch.setattr(
        kd,
        "wind_a50_daily",
        lambda: [
            {"date": "20261028", "value": "13000"},
            {"date": "20261029", "value": "999999999"},
        ],
    )
    with pytest.raises(ValueError, match="合理区间"):
        cim.fetch_a50_kimiwork("2026-10-29")


def test_legacy_ext_is_not_called_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CUSTOS_LEGACY_OVERSEAS", raising=False)
    monkeypatch.setattr(
        omc,
        "SYMBOLS",
        {"nvda": {"symbol": "NVDA", "name": "英伟达", "group": "ai_leader"}},
    )
    monkeypatch.setattr(omc, "FIELD_MAP", {"nvda": "nvda_change_pct"})
    monkeypatch.setattr(omc, "MARKET_DIR", tmp_path)
    monkeypatch.setattr(
        omc, "fetch_chart", lambda *args: (_ for _ in ()).throw(OSError("down"))
    )
    from custos.datasource import tdx_ext_quotes as ext

    called = []
    monkeypatch.setattr(
        ext, "fetch_ext_change", lambda *args, **kwargs: called.append(args)
    )
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-10-30", "--input", str(out)])
    omc.main()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overseas_market"]["nvda_change_pct"] is None
    assert called == []


def test_cls_normalization_keeps_evidence_contract():
    src = {
        "id": "cls_telegraphs",
        "name": "财联社-电报快讯",
        "tier": "B",
        "category": "cn_financial_media",
        "url": "https://www.cls.cn/telegraph",
    }
    entries = [
        {"id": 1, "status": 1, "ctime": 1793342565, "title": "正常", "content": "正文"},
        {"id": 2, "status": 3, "ctime": 1793342565, "content": "已删除"},
    ]
    items = rc.parse_cls_telegraphs(entries, src, "2026-10-30T14:30:00+08:00")
    assert len(items) == 1
    item = items[0]
    assert item["quality"] == "candidate" and item["confirmed"] is False
    assert item["title"] == "正常" and item["summary"] == "正文"
    assert item["published_at"].startswith("2026-10-30T")
