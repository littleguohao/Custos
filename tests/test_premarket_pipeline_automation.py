"""08:50 无聊天采集与情报验收钉测；测试不得触网或调用插件。"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = _load(
    "wind_announcement_collector",
    ROOT / "src" / "custos" / "datasource" / "news" / "wind_announcement_collector.py",
)
pipeline = _load(
    "premarket_pipeline", ROOT / "scripts" / "automation" / "premarket_pipeline.py"
)
DAY = "2026-11-04"


def _raw(rows: list[dict[str, str]]) -> dict:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=["content", "date", "doc_type", "title", "url"]
    )
    writer.writeheader()
    writer.writerows(rows)
    return {"files": [{"name": "cache/full.csv", "content": buffer.getvalue()}]}


def test_announcement_records_use_headers_and_mark_truncation():
    raw = _raw(
        [
            {
                "content": "x" * 2100,
                "date": DAY,
                "doc_type": "announcement",
                "title": "标题",
                "url": "https://example",
            }
        ]
    )

    records = collector.announcement_records(raw)

    assert records[0]["title"] == "标题"
    assert len(records[0]["content_brief"]) == 2000
    assert records[0]["content_truncated"] is True
    assert records[0]["content_chars"] == 2100
    assert records[0]["full_content_cache"] == "cache/full.csv"


def test_announcement_failure_stops_without_retry(monkeypatch, tmp_path: Path):
    positions = tmp_path / "positions.json"
    positions.write_text(
        json.dumps(
            [
                {"代码": "000001", "名称": "甲"},
                {"代码": "000002", "名称": "乙"},
                {"代码": "000003", "名称": "丙"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise collector.KimiWorkDataError("429 rate limited")

    monkeypatch.setattr(collector, "call_api", fail)
    monkeypatch.setattr(
        collector,
        "announcement_window",
        lambda day: (
            collector.datetime(2026, 11, 3, 15, tzinfo=collector.CST),
            collector.datetime(2026, 11, 4, 9, tzinfo=collector.CST),
        ),
    )

    result = collector.collect(
        DAY, positions_path=positions, output_path=tmp_path / "out.json"
    )

    assert len(calls) == 1
    assert result["status"] == "degraded"
    assert result["remaining_skipped"] == 2
    document = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in document["holdings"]] == [
        "failed",
        "skipped",
        "skipped",
    ]


def test_collect_dry_run_has_no_side_effects(tmp_path: Path):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run 不得执行")

    artifact = pipeline.collect(DAY, repo=tmp_path, dry_run=True, runner=forbidden)

    assert artifact["status"] == "dry_run"
    assert len(artifact["planned_commands"]) == 2
    assert artifact["steps"] == []


def test_existing_valid_intelligence_skips_collection(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        pipeline, "validate_intelligence", lambda day, repo: {"valid": True}
    )

    artifact = pipeline.collect(DAY, repo=tmp_path)

    assert artifact["status"] == "already_completed"
    assert artifact["steps"] == []


def test_collect_runs_pipeline_then_announcements(tmp_path: Path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    artifact = pipeline.collect(DAY, repo=tmp_path, runner=runner)

    assert artifact["status"] == "completed"
    assert len(calls) == 2
    assert "run_0850.py" in calls[0][4]
    assert "wind_announcement_collector.py" in calls[1][4]


def _valid_document() -> dict:
    return {
        "date": DAY,
        "window": {
            "start": "2026-11-03T15:00:00+08:00",
            "end": "2026-11-04T09:00:00+08:00",
        },
        "market_events": [],
        "holding_events": [],
        "data_quality": {"status": "confirmed", "notes": []},
        "market_outlook": {"summary": "中性", "stimulus": [], "risks": "无"},
    }


def test_intelligence_contract_accepts_valid_document(tmp_path: Path):
    path = pipeline.intelligence_path(DAY, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_valid_document(), ensure_ascii=False), encoding="utf-8")

    result = pipeline.validate_intelligence(DAY, tmp_path)

    assert result["valid"] is True
    assert result["errors"] == []


@pytest.mark.parametrize("key", ["market_outlook", "window", "data_quality"])
def test_intelligence_contract_rejects_missing_scheduled_fields(
    key: str, tmp_path: Path
):
    document = _valid_document()
    del document[key]
    path = pipeline.intelligence_path(DAY, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    assert pipeline.validate_intelligence(DAY, tmp_path)["valid"] is False
