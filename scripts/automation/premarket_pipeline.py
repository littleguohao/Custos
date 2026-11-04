"""08:50 无聊天任务脚本入口：采集、公告材料准备、情报契约验收。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _run(command: list[str], *, cwd: Path, timeout: int):
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


def _command(script: str, day: str) -> list[str]:
    return ["uv", "run", "--no-sync", "python", script, "--date", day]


def intelligence_path(day: str, repo: Path = REPO) -> Path:
    return repo / "data" / "news" / "premarket" / f"{day}_premarket_intelligence.json"


def validate_intelligence(day: str, repo: Path = REPO) -> dict[str, Any]:
    from custos.datasource.news.premarket_intel_schema import (
        validate_premarket_intelligence,
    )

    path = intelligence_path(day, repo)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"valid": False, "errors": [f"情报不可读: {exc}"], "path": str(path)}
    result = validate_premarket_intelligence(data)
    errors = list(result["errors"])
    if data.get("date") != day:
        errors.append(f"date 应为 {day}")
    window = data.get("window") or {}
    if not window.get("start") or not window.get("end"):
        errors.append("window.start/end 必填")
    outlook = data.get("market_outlook")
    if not isinstance(outlook, dict) or not isinstance(outlook.get("stimulus"), list):
        errors.append("market_outlook.stimulus 必填且为 list")
    quality = data.get("data_quality") or {}
    if quality.get("status") not in {"confirmed", "degraded"}:
        errors.append("data_quality.status 必须为 confirmed|degraded")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": result["warnings"],
        "path": str(path),
        "market_events": len(data.get("market_events") or []),
        "holding_events": len(data.get("holding_events") or []),
        "data_quality": quality.get("status"),
    }


def collect(
    day: str,
    *,
    repo: Path = REPO,
    dry_run: bool = False,
    force: bool = False,
    runner: Callable[..., Any] = _run,
) -> dict[str, Any]:
    commands = [
        _command("src/custos/pipeline/run_0850.py", day) + ["--run-on-closed"],
        _command("src/custos/datasource/news/wind_announcement_collector.py", day),
    ]
    artifact: dict[str, Any] = {
        "summary": "",
        "date": day,
        "status": "dry_run" if dry_run else "running",
        "dry_run": dry_run,
        "planned_commands": commands,
        "steps": [],
    }
    if dry_run:
        artifact["summary"] = "仅校验08:50命令计划，未采集、未调用插件"
        return artifact
    if not force:
        existing = validate_intelligence(day, repo)
        if existing["valid"]:
            artifact.update(
                status="already_completed",
                summary="当日盘前情报已存在且契约有效，跳过重复采集与插件调用",
                intelligence=existing,
            )
            return artifact

    for name, command in zip(("pipeline", "announcements"), commands, strict=True):
        result = runner(command, cwd=repo, timeout=1200 if name == "pipeline" else 600)
        step = {
            "name": name,
            "returncode": result.returncode,
            "stdout_tail": (result.stdout or "")[-4000:],
            "stderr_tail": (result.stderr or "")[-4000:],
        }
        artifact["steps"].append(step)
        if result.returncode:
            raise RuntimeError(
                f"08:50 {name} 失败: {step['stderr_tail'] or step['stdout_tail']}"
            )
    artifact.update(status="completed", summary="08:50预采集与持仓公告材料已完成")
    return artifact


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("collect", "validate"):
        sub = subparsers.add_parser(action)
        sub.add_argument("--date", required=True)
        if action == "collect":
            sub.add_argument("--dry-run", action="store_true")
            sub.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "validate":
        result = validate_intelligence(args.date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["valid"] else 1
    result = collect(args.date, dry_run=args.dry_run, force=args.force)
    print(json.dumps({"artifact": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
