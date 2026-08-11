# -*- coding: utf-8 -*-
"""09:05 one-shot premarket report pipeline.

stdout is a machine-consumed protocol (see the summary lines below) and is
kept byte-compatible; observability goes to artifacts/logs/{date}_0905_run_log.json
instead — every run (completed / closed / calendar_failed / failed) leaves
one behind.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
for _bp in (str(_SRC / "core"),):          # core/: paths/pipeline_kit 等 L0 模块
    if _bp not in sys.path:
        sys.path.insert(0, _bp)

from paths import BASE, cn_today, TOOLS, LOGS, PLANS
from pipeline_kit import log_stage, md_to_digest, now_iso, warn, write_run_log, run_stage_quiet as _stage, calendar_gate, propagate_gate_code

LOG_DIR = LOGS

# Module-level aliases kept for tests and readability; implementation lives in pipeline_kit.
_now_iso = now_iso
_log_stage = log_stage


def _write_run_log(target: str, status: str, started_at: str, t0: float, stages: list[dict]):
    return write_run_log(LOG_DIR, "0905", target, status, started_at, t0, stages)




DISCOVERY_STAGES = ("overseas", "rss_collect", "rss_filter")


def _check_0850_status(target: str) -> tuple[bool, str]:
    """Decide whether the 09:05 pipeline may reuse 08:50 discovery artifacts.

    复用条件收紧为**逐 stage 判定**:只有 08:50 的三个 discovery stage
    (overseas / rss_collect / rss_filter) 全部 ok 才允许复用。
    仅看 status == "completed" 是不够的——08:50 采集全失败时曾照样写 completed,
    09:05 于是跳过重采、用空数据渲染出外观正常的报告(评分器按"中性半分"填)。
    Returns (reuse_discovery, note); note 记入 run log 并 warn 到 stderr(stdout 协议不变)。
    """
    path = LOG_DIR / f"{target}_0850_run_log.json"
    if not path.exists():
        return False, "0850_log_missing, fallback to full collection"
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "0850_log_unreadable, fallback to full collection"
    status = log.get("status")
    stage_ok = {s.get("name"): s.get("ok") for s in (log.get("stages") or []) if isinstance(s, dict)}
    bad = [n for n in DISCOVERY_STAGES if stage_ok.get(n) is not True]
    if bad:
        return False, f"0850_status={status}, discovery_failed={','.join(bad)}, fallback to full collection"
    if status not in {"completed", "degraded"}:
        return False, f"0850_status={status}, fallback to full collection"
    if status == "degraded":
        return True, "0850_status=degraded but discovery stages ok, reuse discovery"
    return True, ""


def _daily_pipeline_cmd(target: str, reuse_discovery: bool) -> list[str]:
    cmd = ["uv", "run", "python", str(TOOLS / "pipeline" / "daily_pipeline.py"), "--date", target,
           "--session-type", "premarket"]
    if reuse_discovery:
        cmd.append("--reuse-discovery")
    return cmd


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=cn_today().strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    target = args.date

    # Subprocesses rely on project discovery (uv run) from the repo root.
    os.chdir(BASE)

    run_started = _now_iso()
    t0 = time.time()
    stages_log: list[dict] = []

    # 1. Trading calendar
    _cg = calendar_gate(
        target, log_dir=LOG_DIR, session="0905", run_started=run_started,
        t0=t0, stages_log=stages_log,
        fail_msg="【盘前日报失败｜{target}】日历检查失败：{err}",
        closed_msg="今日休市，盘前日报不生成（{target}）")
    if _cg.exit_code is not None:
        return _cg.exit_code
    cal = _cg.cal

    # 2. Daily pipeline (premarket; reuse 08:50 discovery only when it completed)
    s_started = _now_iso()
    s_t0 = time.time()
    reuse_discovery, fallback_note = _check_0850_status(target)
    if fallback_note:
        warn(fallback_note)
    r = _stage(_daily_pipeline_cmd(target, reuse_discovery), "daily_pipeline premarket")
    stages_log.append(_log_stage("daily_pipeline premarket", r, s_started, _now_iso(), time.time() - s_t0,
                                 note=fallback_note))
    if not r["ok"]:
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【盘前日报失败｜{target}】daily_pipeline失败：{r['out'][:500]}")
        return propagate_gate_code(r)   # 门控码 3/4/5 原样上抛供 cron 判定

    # 3. Read generated report and convert to text digest
    d_started = _now_iso()
    d_t0 = time.time()
    report_path = PLANS / f"{target}_daily_report.md"
    if not report_path.exists():
        stages_log.append(_log_stage("report_digest", {"ok": False, "returncode": None, "timeout": False},
                                     d_started, _now_iso(), time.time() - d_t0,
                                     note=f"报告文件未生成：{report_path}"))
        _write_run_log(target, "failed", run_started, t0, stages_log)
        print(f"【盘前日报失败｜{target}】报告文件未生成：{report_path}")
        return 1

    digest = md_to_digest(report_path.read_text(encoding="utf-8"))
    stages_log.append(_log_stage("report_digest", {"ok": True, "returncode": 0, "timeout": False},
                                 d_started, _now_iso(), time.time() - d_t0,
                                 note=f"report={report_path}；digest_chars={len(digest)}"))
    _write_run_log(target, "completed", run_started, t0, stages_log)

    print(f"【盘前日报｜{target}】")
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
