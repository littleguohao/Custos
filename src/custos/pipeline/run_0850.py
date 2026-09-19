# -*- coding: utf-8 -*-
"""08:50 one-shot premarket data collection (except wenda_notice_query which needs LLM tool).

stdout is a machine-consumed protocol (see the summary lines below) and is
kept byte-compatible; observability goes to artifacts/logs/{date}_0850_run_log.json
instead — every run (completed / closed / calendar_failed) leaves one behind.
"""

from __future__ import annotations

import argparse
import os
import sys
import time


from custos.core.paths import BASE, cn_today, TOOLS, LOGS
from custos.core.pipeline_kit import (
    _extract_json,
    log_stage,
    now_iso,
    write_run_log,
    run_stage_quiet as _stage,
    calendar_gate,
)

LOG_DIR = LOGS

# Module-level aliases kept for tests and readability; implementation lives in pipeline_kit.
_now_iso = now_iso
_log_stage = log_stage


def _write_run_log(
    target: str, status: str, started_at: str, t0: float, stages: list[dict]
):
    return write_run_log(LOG_DIR, "0850", target, status, started_at, t0, stages)


def _rss_summary_fragments(results: dict) -> list[str]:
    """Cheap quality dims for the summary line, parsed from stage stdout JSON
    (rss_collect prints {items, sources_ok, sources_failed, ...}; rss_filter
    prints the filter report with selected_count). Anything unparseable is
    silently skipped — the summary prefix contract is never at risk."""
    frags = []
    coll = _extract_json((results.get("rss_collect") or {}).get("stdout", ""))
    items, sok, sfail = (
        coll.get("items"),
        coll.get("sources_ok"),
        coll.get("sources_failed"),
    )
    if isinstance(items, int) and isinstance(sok, int) and isinstance(sfail, int):
        frags.append(f"rss_items={items}({sok}/{sok + sfail})")
    report = _extract_json((results.get("rss_filter") or {}).get("stdout", ""))
    cand = report.get("selected_count")
    if isinstance(cand, int):
        frags.append(f"rss_candidates={cand}")
    return frags


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=cn_today().strftime("%Y-%m-%d"))
    ap.add_argument(
        "--run-on-closed",
        action="store_true",
        help="非交易日也继续跑发现采集（overseas/RSS），摘要带休市标记；"
        "缺省保持休市即退出的旧语义",
    )
    args = ap.parse_args(argv)
    target = args.date

    # Subprocesses rely on project discovery (uv run) from the repo root.
    os.chdir(BASE)

    run_started = _now_iso()
    t0 = time.time()
    stages_log: list[dict] = []

    # 1. Trading calendar
    _cg = calendar_gate(
        target,
        log_dir=LOG_DIR,
        session="0850",
        run_started=run_started,
        t0=t0,
        stages_log=stages_log,
        fail_msg="【08:50预采集失败｜{target}】日历检查失败：{err}",
        closed_msg="今日休市，08:50预采集跳过（{target}）",
        continue_on_closed=args.run_on_closed,
    )
    if _cg.exit_code is not None:
        return _cg.exit_code
    closed = _cg.cal.get("is_trading_day") is not True

    steps = [f"calendar={'closed' if closed else 'ok'}"]

    # 2-6. Data collectors (best-effort: rc recorded into steps, never fatal)
    STAGES = [
        (
            [
                "uv",
                "run",
                "python",
                str(
                    TOOLS / "pipeline" / "market_timing" / "market_timing_collector.py"
                ),
                "--date",
                target,
            ],
            "market_timing",
        ),
        (
            [
                "uv",
                "run",
                "python",
                str(TOOLS / "datasource" / "overseas_market_collector.py"),
                "--date",
                target,
            ],
            "overseas",
        ),
        (
            [
                "uv",
                "run",
                "python",
                str(TOOLS / "datasource" / "news" / "rss_collector.py"),
                "--date",
                target,
            ],
            "rss_collect",
        ),
        (
            [
                "uv",
                "run",
                "python",
                str(TOOLS / "datasource" / "collect" / "collect_incremental_market.py"),
                "--date",
                target,
            ],
            "incremental",
        ),
        (
            [
                "uv",
                "run",
                "python",
                str(TOOLS / "datasource" / "news" / "rss_filter.py"),
                "--date",
                target,
                "--session-type",
                "premarket",
            ],
            "rss_filter",
        ),
    ]
    # 非交易日只跑发现采集（overseas/RSS）：market_timing/incremental 写的是按日键控的
    # A 股行情底座，休日照旧跑会把 T-1 数据落进当日 market_timing_input，下一交易日
    # 门控的继承检索（runtime_guards._latest_market_section）会命中它并把 source_day
    # 误判成陈旧来源——与 2026-07-30 盘后链误阻断同一形态。
    if closed:
        for cmd, name in STAGES:
            if name in {"market_timing", "incremental"}:
                stages_log.append(
                    {
                        "stage": name,
                        "ok": True,
                        "skipped": True,
                        "note": "非交易日跳过：A股行情底座不落休市日文件",
                    }
                )
                steps.append(f"{name}=skipped")
    active = [
        (cmd, name)
        for cmd, name in STAGES
        if not closed or name not in {"market_timing", "incremental"}
    ]
    results: dict[str, dict] = {}
    for cmd, name in active:
        s_started = _now_iso()
        s_t0 = time.time()
        r = _stage(cmd, name)
        results[name] = r
        stages_log.append(
            _log_stage(name, r, s_started, _now_iso(), time.time() - s_t0)
        )
        steps.append(f"{name}={'ok' if r['ok'] else 'fail'}")

    # 09:05 会**复用**本次采集(--reuse-discovery)。若这里任一 stage 失败却仍写 "completed",
    # 09:05 就会跳过重采、用空/旧数据渲染出一份外观正常的报告(评分器给"中性半分")。
    # 故:只要有 stage 失败就写 degraded,并把失败项列进 run log,由 09:05 决定是否重采。
    failed = [n for n, r in results.items() if not r["ok"]]
    status = "completed" if not failed else "degraded"
    stages_log.append(
        {
            "stage": "collection_summary",
            "ok": not failed,
            "failed_stages": failed,
            "status": status,
            "note": "任一采集失败即 degraded;09:05 据此拒绝复用并重采关键项",
        }
    )
    _write_run_log(target, status, run_started, t0, stages_log)
    tag = "" if not failed else f"（降级：{','.join(failed)} 失败，09:05 将重采）"
    print(
        f"【08:50预采集{'完成' if not failed else '降级完成'}｜{target}"
        f"{'｜休市' if closed else ''}】{tag}"
        f"{'；'.join(steps + _rss_summary_fragments(results))}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
