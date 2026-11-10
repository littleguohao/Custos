# -*- coding: utf-8 -*-
"""无聊天定时入口：命令计划、日历门、报告验收与投递失败钉测。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "automation" / "deterministic_pipeline.py"
SPEC = importlib.util.spec_from_file_location("deterministic_pipeline", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)

DAY = "2026-11-04"


def _result(code: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize("name", ["0905", "1445", "1700", "1800", "calendar"])
def test_dry_run_plans_without_executing(name: str, tmp_path: Path):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run 不得执行命令")

    artifact = pipeline.execute(
        name, DAY, repo=tmp_path, dry_run=True, runner=forbidden
    )

    assert artifact["status"] == "dry_run"
    assert artifact["sent"] is False
    assert artifact["steps"] == []
    assert artifact["planned_commands"]


@pytest.mark.parametrize(
    ("name", "report_name"),
    [
        ("0905", "2026-11-04_0905_daily_report.md"),
        ("1445", "2026-11-04_1445_review.md"),
        ("1700", "2026-11-04_1700_final_review.md"),
    ],
)
def test_reports_use_current_date_directory(
    name: str, report_name: str, tmp_path: Path
):
    plan = pipeline.build_plan(name, DAY, tmp_path)

    expected = tmp_path / "artifacts" / "reports" / "daily" / DAY / report_name
    assert plan["report"] == expected
    assert str(expected) in plan["commands"][1]["command"]


def test_closed_pipeline_skips_publish(tmp_path: Path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return _result(3, "今日确认休市")

    artifact = pipeline.execute("1445", DAY, repo=tmp_path, runner=runner)

    assert artifact["status"] == "skipped"
    assert artifact["reason"] == "trading_calendar_closed"
    assert artifact["sent"] is False
    assert len(calls) == 1


def test_success_requires_report_then_publishes(tmp_path: Path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            report = pipeline.build_plan("0905", DAY, tmp_path)["report"]
            report.parent.mkdir(parents=True)
            report.write_text("# 盘前日报\n", encoding="utf-8")
            return _result(0, "report ready")
        return _result(0, '{"sent": true}')

    artifact = pipeline.execute("0905", DAY, repo=tmp_path, runner=runner)

    assert artifact["status"] == "completed"
    assert artifact["sent"] is True
    assert len(calls) == 2
    assert [step["name"] for step in artifact["steps"]] == ["pipeline", "publish"]


def test_missing_report_is_not_published(tmp_path: Path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return _result(0)

    with pytest.raises(pipeline.PipelineError, match="报告缺失或为空"):
        pipeline.execute("1700", DAY, repo=tmp_path, runner=runner)
    assert len(calls) == 1


def test_publish_failure_is_not_success(tmp_path: Path):
    def runner(command, **kwargs):
        if "--report" not in command:
            report = pipeline.build_plan("1445", DAY, tmp_path)["report"]
            report.parent.mkdir(parents=True)
            report.write_text("# 尾盘报告\n", encoding="utf-8")
            return _result(0)
        return _result(1, stderr="feishu rejected")

    with pytest.raises(pipeline.PipelineError, match="飞书投递失败"):
        pipeline.execute("1445", DAY, repo=tmp_path, runner=runner)


def test_calendar_does_not_publish(tmp_path: Path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return _result(0, '{"status": "updated"}')

    artifact = pipeline.execute("calendar", DAY, repo=tmp_path, runner=runner)

    assert artifact["status"] == "completed"
    assert artifact["sent"] is False
    assert len(calls) == 1
    assert "--require-refresh" in calls[0]


def test_blueprint_run_returns_artifact_wrapper(tmp_path: Path):
    output = pipeline.run(
        {"input": {"pipeline": "calendar", "date": DAY, "dry_run": True}}
    )

    assert output["artifact"]["pipeline"] == "calendar"
    assert output["artifact"]["status"] == "dry_run"


def test_scheduled_run_without_input_uses_task_identity(monkeypatch):
    selected = []

    def fake_execute(name, day, **kwargs):
        selected.append(name)
        return {"pipeline": name, "date": day}

    monkeypatch.setattr(pipeline, "execute", fake_execute)
    output = pipeline.run({"taskId": "automation_a0d643ef-60f3-4fa0-a042-b9f20a01d9ea"})

    assert selected == ["calendar"]
    assert output["artifact"]["pipeline"] == "calendar"


def _write_screening_outputs(
    repo: Path,
    *,
    run_status: str = "completed",
    run_date: str = DAY,
    pool_status: str = "ok",
    degraded_reason: str = "",
    stages: list[dict] | None = None,
    report: bool = True,
) -> None:
    log_path = repo / "artifacts" / "logs" / f"{DAY}_1800_run_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "date": run_date,
                "status": run_status,
                "stages": stages or [],
            }
        ),
        encoding="utf-8",
    )
    if report:
        report_path = pipeline.build_plan("1800", DAY, repo)["report"]
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("# 公式选股备选池\n", encoding="utf-8")
    if run_status != "closed":
        pool_path = repo / "data" / "stock_pool" / f"{DAY}_stock_pool.json"
        pool_path.parent.mkdir(parents=True, exist_ok=True)
        pool_path.write_text(
            json.dumps(
                {
                    "date": DAY,
                    "status": pool_status,
                    "degraded_reason": degraded_reason,
                    "bucket_counts": {"A": 1, "B": 2, "C": 3, "D": 4},
                }
            ),
            encoding="utf-8",
        )


def test_1800_success_summarizes_counts_without_publish(tmp_path: Path):
    _write_screening_outputs(tmp_path)
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return _result(0)

    artifact = pipeline.execute("1800", DAY, repo=tmp_path, runner=runner)

    assert artifact["status"] == "completed"
    assert artifact["bucket_counts"] == {"A": 1, "B": 2, "C": 3, "D": 4}
    assert "A=1，B=2，C=3，D=4" in artifact["summary"]
    assert artifact["sent"] is False
    assert len(calls) == 1
    assert [step["name"] for step in artifact["steps"]] == ["pipeline"]
    assert len(artifact["planned_commands"]) == 1


def test_1800_closed_skips_without_reusing_old_report(tmp_path: Path):
    _write_screening_outputs(tmp_path, run_status="closed", report=False)

    artifact = pipeline.execute(
        "1800", DAY, repo=tmp_path, runner=lambda *args, **kwargs: _result(0)
    )

    assert artifact["status"] == "skipped"
    assert artifact["reason"] == "trading_calendar_closed"
    assert artifact["sent"] is False


def test_1800_degraded_summary_keeps_screening_and_refresh_warnings(tmp_path: Path):
    _write_screening_outputs(
        tmp_path,
        run_status="degraded",
        pool_status="degraded",
        degraded_reason="个股资金流不可用",
        stages=[
            {"name": "refresh_xdxr", "ok": False},
            {"name": "screening_enrich_candidates", "ok": False},
        ],
    )

    artifact = pipeline.execute(
        "1800", DAY, repo=tmp_path, runner=lambda *args, **kwargs: _result(0)
    )

    assert artifact["status"] == "degraded"
    assert "screening_enrich_candidates" in artifact["degraded_reason"]
    assert "个股资金流不可用" in artifact["summary"]
    assert artifact["warning_stages"] == [
        "refresh_xdxr",
        "screening_enrich_candidates",
    ]


def test_1800_rejects_stale_run_log(tmp_path: Path):
    _write_screening_outputs(tmp_path, run_date="2026-11-03")

    with pytest.raises(pipeline.PipelineError, match="运行日志日期不符"):
        pipeline.execute(
            "1800", DAY, repo=tmp_path, runner=lambda *args, **kwargs: _result(0)
        )


def test_1800_scheduled_run_without_input_uses_task_identity(monkeypatch):
    selected = []

    def fake_execute(name, day, **kwargs):
        selected.append(name)
        return {"pipeline": name, "date": day}

    monkeypatch.setattr(pipeline, "execute", fake_execute)
    output = pipeline.run({"taskId": "automation_d5f253a6-5b04-4195-8149-6c81d214d317"})

    assert selected == ["1800"]
    assert output["artifact"]["pipeline"] == "1800"
