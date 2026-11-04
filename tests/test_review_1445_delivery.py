"""15:00 尾盘验收：正常静默、休市放行、异常才告警。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "automation" / "review_1445_delivery.py"
SPEC = importlib.util.spec_from_file_location("review_1445_delivery", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)
DAY = "2026-11-04"


def _write(repo: Path, status: str = "completed", report: bool = True, stages=None):
    log = repo / "artifacts" / "logs" / f"{DAY}_1445_run_log.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"status": status, "stages": stages or []}), encoding="utf-8"
    )
    if report:
        path = repo / "artifacts" / "reports" / "daily" / DAY / f"{DAY}_1445_review.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 尾盘报告\n", encoding="utf-8")


def _forbidden(*args, **kwargs):
    raise AssertionError("正常或 dry-run 不得投递")


def test_completed_with_report_is_silent(tmp_path: Path):
    _write(tmp_path)

    result = review.execute(DAY, repo=tmp_path, runner=_forbidden)

    assert result["status"] == "ok"
    assert result["issues"] == []
    assert result["alert_sent"] is False


def test_closed_needs_no_report(tmp_path: Path):
    _write(tmp_path, status="closed", report=False)

    result = review.execute(DAY, repo=tmp_path, runner=_forbidden)

    assert result["status"] == "ok"


def test_missing_report_alerts_with_current_path(tmp_path: Path):
    _write(tmp_path, report=False)
    calls = []

    def publisher(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='{"sent": true}', stderr="")

    result = review.execute(DAY, repo=tmp_path, runner=publisher)

    assert result["status"] == "alert_sent"
    assert result["alert_sent"] is True
    alert = (
        tmp_path
        / "artifacts"
        / "reports"
        / "daily"
        / DAY
        / f"{DAY}_1445_delivery_alert.md"
    )
    assert alert.is_file()
    assert str(alert) in calls[0]


def test_failed_stage_is_alerted(tmp_path: Path):
    _write(tmp_path, stages=[{"name": "close_review", "ok": False}])

    result = review.evaluate(DAY, tmp_path)

    assert "close_review 失败" in result["issues"][0]


def test_abnormal_dry_run_does_not_write_or_publish(tmp_path: Path):
    _write(tmp_path, report=False)

    result = review.execute(DAY, repo=tmp_path, dry_run=True, runner=_forbidden)

    assert result["status"] == "alert_pending"
    assert not (
        tmp_path
        / "artifacts"
        / "reports"
        / "daily"
        / DAY
        / f"{DAY}_1445_delivery_alert.md"
    ).exists()


@pytest.mark.parametrize("status", ["failed", "missing"])
def test_bad_run_status_is_rejected(status: str, tmp_path: Path):
    _write(tmp_path, status=status)

    assert review.evaluate(DAY, tmp_path)["issues"]
