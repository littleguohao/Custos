"""15:00 尾盘报告独立验收：确定性检查，异常才写报告并推飞书。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CST = timezone(timedelta(hours=8))


def evaluate(day: str, repo: Path = REPO) -> dict[str, Any]:
    log_path = repo / "artifacts" / "logs" / f"{day}_1445_run_log.json"
    report_path = (
        repo / "artifacts" / "reports" / "daily" / day / f"{day}_1445_review.md"
    )
    issues = []
    status = "missing"
    try:
        payload = json.loads(log_path.read_text(encoding="utf-8"))
        status = str(payload.get("status") or "missing")
        if status not in {"completed", "closed"}:
            issues.append(f"run log status={status}，预期 completed|closed")
        for index, stage in enumerate(payload.get("stages") or []):
            if stage.get("ok") is False and not stage.get("skipped"):
                issues.append(
                    f"stage[{index}] {stage.get('name') or stage.get('stage')} 失败"
                )
    except (OSError, ValueError) as exc:
        issues.append(f"run log 不可读: {exc}")
    if status == "completed" and (
        not report_path.is_file() or report_path.stat().st_size == 0
    ):
        issues.append(f"completed 但报告缺失或为空: {report_path}")
    return {
        "date": day,
        "run_status": status,
        "issues": issues,
        "log_path": str(log_path),
        "report_path": str(report_path),
    }


def _publisher(command: list[str], *, cwd: Path, timeout: int):
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def execute(
    day: str,
    *,
    repo: Path = REPO,
    dry_run: bool = False,
    runner: Callable[..., Any] = _publisher,
) -> dict[str, Any]:
    result = evaluate(day, repo)
    issues = result["issues"]
    artifact = {
        **result,
        "status": "ok" if not issues else "alert_pending",
        "alert_sent": False,
        "dry_run": dry_run,
        "summary": "14:45链路验收正常" if not issues else f"发现 {len(issues)} 项异常",
    }
    if not issues or dry_run:
        return artifact

    alert_path = (
        repo / "artifacts" / "reports" / "daily" / day / f"{day}_1445_delivery_alert.md"
    )
    lines = [f"# 14:45尾盘报告投递异常｜{day}", "", "确定性验收发现："]
    lines.extend(f"- {issue}" for issue in issues)
    lines.extend(
        ["", "建议：核对 14:45 run log、报告生成与飞书投递记录；不要以旧报告替代。"]
    )
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    alert_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        "src/custos/pipeline/feishu_report_publisher.py",
        "--report",
        str(alert_path),
        "--title",
        "14:45尾盘报告投递异常",
        "--date",
        day,
    ]
    published = runner(command, cwd=repo, timeout=120)
    if published.returncode:
        raise RuntimeError(
            f"尾盘异常告警投递失败: {(published.stderr or published.stdout)[-1000:]}"
        )
    artifact.update(status="alert_sent", alert_sent=True, alert_path=str(alert_path))
    return artifact


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    raw = ctx.get("input")
    if isinstance(raw, str) and raw.strip():
        options = json.loads(raw)
    else:
        options = raw if isinstance(raw, dict) else {}
    day = str(options.get("date") or datetime.now(CST).date().isoformat())
    return {"artifact": execute(day, dry_run=options.get("dry_run") is True)}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(CST).date().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = execute(args.date, dry_run=args.dry_run)
    print(json.dumps({"artifact": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
