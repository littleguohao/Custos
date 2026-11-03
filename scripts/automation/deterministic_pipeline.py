# -*- coding: utf-8 -*-
"""Kimi Work 无聊天定时入口：直接运行确定性管线并按需投递飞书。

BluePrint Python 以 ``run(ctx)`` 调用；也支持命令行 ``--dry-run`` 只检查命令
计划。除 08:50 的消息语义研判外，定时编排本身不调用模型、不写聊天历史。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
CST = timezone(timedelta(hours=8))
OUTPUT_TAIL = 4000

PIPELINES: dict[str, dict[str, Any]] = {
    "0905": {
        "runner": "src/custos/pipeline/run_0905.py",
        "report": "{day}/{day}_0905_daily_report.md",
        "title": "盘前日报",
        "run_on_closed": True,
        "runner_timeout": 1500,
        "task_timeout_ms": 1800000,
    },
    "1445": {
        "runner": "src/custos/pipeline/run_1445.py",
        "report": "{day}/{day}_1445_review.md",
        "title": "14:45尾盘报告",
        "closed_exit": 3,
        "runner_timeout": 720,
        "task_timeout_ms": 900000,
    },
    "1700": {
        "runner": "src/custos/pipeline/run_1700.py",
        "report": "{day}/{day}_1700_final_review.md",
        "title": "盘后复盘",
        "closed_exit": 3,
        "runner_timeout": 1500,
        "task_timeout_ms": 1800000,
    },
    "calendar": {
        "runner": "src/custos/datasource/trading_calendar.py",
        "require_refresh": True,
        "runner_timeout": 480,
        "task_timeout_ms": 600000,
    },
}

# 定时触发实测可能不提供 defaultInput；用持久任务标识选择管线，避免空调度失败。
TASK_PIPELINES = {
    "automation_f4000a0c-673e-4fe9-9fa9-471ed632a45e": "0905",
    "automation_4b658250-ee41-405b-a78e-b032d0f8e78f": "1445",
    "automation_44f0790b-93f4-48f4-a7ec-1e366bb8fdde": "1700",
    "automation_a0d643ef-60f3-4fa0-a042-b9f20a01d9ea": "calendar",
}


class PipelineError(RuntimeError):
    """脚本、报告验收或飞书投递失败。"""


def _tail(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-OUTPUT_TAIL:]


def _run_subprocess(
    command: list[str], *, cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
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


def _python_command(script: str, *args: str) -> list[str]:
    return ["uv", "run", "--no-sync", "python", script, *args]


def build_plan(pipeline: str, day: str, repo: Path = REPO) -> dict[str, Any]:
    """生成唯一命令计划；报告路径与当前 daily/YYYY-MM-DD 布局一致。"""
    if pipeline not in PIPELINES:
        raise ValueError(f"未知管线: {pipeline}")
    date.fromisoformat(day)
    cfg = PIPELINES[pipeline]
    args = ["--require-refresh"] if cfg.get("require_refresh") else ["--date", day]
    if cfg.get("run_on_closed"):
        args.append("--run-on-closed")
    commands = [
        {
            "name": "pipeline",
            "command": _python_command(str(cfg["runner"]), *args),
            "timeout": int(cfg["runner_timeout"]),
        }
    ]
    report = None
    if cfg.get("report"):
        report = (
            repo / "artifacts" / "reports" / "daily" / cfg["report"].format(day=day)
        )
        commands.append(
            {
                "name": "publish",
                "command": _python_command(
                    "src/custos/pipeline/feishu_report_publisher.py",
                    "--report",
                    str(report),
                    "--title",
                    str(cfg["title"]),
                    "--date",
                    day,
                ),
                "timeout": 120,
            }
        )
    return {"commands": commands, "report": report}


def execute(
    pipeline: str,
    day: str,
    *,
    repo: Path = REPO,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_subprocess,
) -> dict[str, Any]:
    """执行一条管线；非交易日跳过与脚本/投递失败均显式进入结果。"""
    plan = build_plan(pipeline, day, repo)
    planned = [step["command"] for step in plan["commands"]]
    artifact: dict[str, Any] = {
        "summary": "",
        "pipeline": pipeline,
        "date": day,
        "status": "dry_run" if dry_run else "running",
        "sent": False,
        "dry_run": dry_run,
        "planned_commands": planned,
        "steps": [],
    }
    if dry_run:
        artifact["summary"] = "命令计划校验完成，未执行、未刷新、未投递"
        return artifact

    pipeline_step = plan["commands"][0]
    result = runner(
        pipeline_step["command"], cwd=repo, timeout=pipeline_step["timeout"]
    )
    step_result = {
        "name": "pipeline",
        "returncode": result.returncode,
        "stdout_tail": _tail(result.stdout),
        "stderr_tail": _tail(result.stderr),
    }
    artifact["steps"].append(step_result)
    closed_exit = PIPELINES[pipeline].get("closed_exit")
    if closed_exit is not None and result.returncode == closed_exit:
        artifact.update(
            status="skipped",
            summary="非交易日，管线按日历门跳过，未投递",
            reason="trading_calendar_closed",
        )
        return artifact
    if result.returncode != 0:
        raise PipelineError(
            f"{pipeline} 管线失败(exit={result.returncode}): "
            f"{_tail(result.stderr) or _tail(result.stdout)}"
        )

    if pipeline == "calendar":
        artifact.update(status="completed", summary="A股交易日历刷新完成")
        return artifact

    report = plan["report"]
    if report is None or not report.is_file() or report.stat().st_size == 0:
        raise PipelineError(f"{pipeline} 报告缺失或为空，拒绝投递: {report}")

    publish_step = plan["commands"][1]
    published = runner(
        publish_step["command"], cwd=repo, timeout=publish_step["timeout"]
    )
    artifact["steps"].append(
        {
            "name": "publish",
            "returncode": published.returncode,
            "stdout_tail": _tail(published.stdout),
            "stderr_tail": _tail(published.stderr),
        }
    )
    if published.returncode != 0:
        raise PipelineError(
            f"{pipeline} 飞书投递失败(exit={published.returncode}): "
            f"{_tail(published.stderr) or _tail(published.stdout)}"
        )
    artifact.update(
        status="completed",
        sent=True,
        summary=f"{PIPELINES[pipeline]['title']}已生成并投递",
        report=str(report),
    )
    return artifact


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    """Kimi Work Python 唯一入口，返回 AutomationOutput 包装对象。

    任务使用 text 输入以保存固定默认管线；手工验证时可传 JSON 字符串，
    例如 ``{"pipeline":"0905","dry_run":true}``。
    """
    raw_options = ctx.get("input")
    if not raw_options:
        raw_options = {"pipeline": TASK_PIPELINES.get(str(ctx.get("taskId") or ""), "")}
    if isinstance(raw_options, str):
        try:
            options = json.loads(raw_options)
        except json.JSONDecodeError:
            options = {"pipeline": raw_options}
    else:
        options = raw_options
    if not isinstance(options, dict):
        raise ValueError("任务输入必须是管线名或 JSON 对象")
    pipeline = str(options.get("pipeline") or "")
    day = str(options.get("date") or datetime.now(CST).date().isoformat())
    artifact = execute(
        pipeline,
        day,
        dry_run=options.get("dry_run") is True,
    )
    return {"artifact": artifact}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline", choices=sorted(PIPELINES), required=True)
    parser.add_argument("--date", default=datetime.now(CST).date().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    output = {"artifact": execute(args.pipeline, args.date, dry_run=args.dry_run)}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
