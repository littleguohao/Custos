# -*- coding: utf-8 -*-
"""18:00 one-shot daily screening pipeline (standalone, separate from reports).

Runs the screening chain — formula_screen → enrich_candidates →
score_candidates → candidate_table — after the 17:00 post-close review has
produced same-day sector_state / risk_decision / refreshed EOD klines.

All stages are best-effort: with TdxW off the chain degrades cleanly
(status=unavailable) and still writes its run log. stdout is a
machine-consumed protocol; observability goes to
06_logs/{date}_1800_run_log.json.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from datetime import date

from paths import BASE, cn_today
from pipeline_kit import check_trading_day, log_stage, md_to_digest, now_iso, run_stage, write_run_log

TOOLS = BASE / "07_tools"
SCREEN_DIR = TOOLS / "screening"
TABLE_DIR = BASE / "03_daily_plans" / "_supporting"
LOG_DIR = BASE / "06_logs"

_now_iso = now_iso
_log_stage = log_stage


def _write_run_log(target: str, status: str, started_at: str, t0: float, stages: list[dict]):
    return write_run_log(LOG_DIR, "1800", target, status, started_at, t0, stages)


def _last_line(text: str) -> str:
    """取 stage 输出的最后一行非空文本(脚本摘要行)。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _stage(cmd: list[str], name: str) -> dict:
    with contextlib.redirect_stdout(io.StringIO()):
        r = run_stage(cmd, name, required=False)
    r["out"] = (r["stdout"] + r["stderr"]).strip()
    return r


def stage_json_status(stdout: str) -> str:
    """从 stage stdout 提取摘要 JSON 的 status 字段。

    stdout 混有 tqdm 进度条等非 JSON 行——只取最后一行尝试 json.loads，
    解析失败返回 ""（不计入 degraded 判定）。
    """
    lines = (stdout or "").strip().splitlines()
    if not lines:
        return ""
    try:
        obj = json.loads(lines[-1])
    except ValueError:
        return ""
    if not isinstance(obj, dict):
        return ""
    return str(obj.get("status", "") or "")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=cn_today().strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    target = args.date

    os.chdir(BASE)

    run_started = _now_iso()
    t0 = time.time()
    stages_log: list[dict] = []

    def _run_stage(cmd: list[str], name: str, note: str = "") -> dict:
        s_started = _now_iso()
        s_t0 = time.time()
        r = _stage(cmd, name)
        stages_log.append(_log_stage(name, r, s_started, _now_iso(), time.time() - s_t0, note=note))
        return r

    # 1. Trading calendar
    c_started = _now_iso()
    c_t0 = time.time()
    cal_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(cal_buf):
            cal = check_trading_day(target)
    except RuntimeError as e:
        stages_log.append(_log_stage("calendar", {"ok": False, "returncode": None, "timeout": False,
                                                  "stdout": cal_buf.getvalue(), "stderr": str(e)},
                                     c_started, _now_iso(), time.time() - c_t0,
                                     note=str(e)[:500]))
        _write_run_log(target, "calendar_failed", run_started, t0, stages_log)
        print(f"【每日选股失败｜{target}】日历检查失败：{str(e)[:200]}")
        return 1
    stages_log.append(_log_stage("calendar", {"ok": True, "returncode": 0, "timeout": False,
                                              "stdout": cal_buf.getvalue()},
                                 c_started, _now_iso(), time.time() - c_t0,
                                 note=f"is_trading_day={cal.get('is_trading_day')}"))
    if not cal.get("is_trading_day", False):
        _write_run_log(target, "closed", run_started, t0, stages_log)
        print(f"今日休市，每日选股不运行（{target}）")
        return 0

    # 2. Refresh concept tags (miscinfo) so sector mapping uses the accurate source
    r = _run_stage(["uv", "run", "python", str(TOOLS / "local_tdx" / "concept_tags.py"),
                    "--date", target], "refresh_concept_tags", note="best-effort，失败不中断")
    if not r["ok"]:
        print(f"[WARN] refresh_concept_tags failed: {r['out'][:200]}")
    else:
        print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'concept tags refreshed'}")

    # 2b. Refresh 板块指数缓存(供 enrich 的 sector_phase hint 用当日相位;best-effort,需 TdxW)。
    #     **增量合并**:只拉各板块缓存末日期前 30 天起的新数据并 merge 进已有 CSV。
    #     此前每天全量重拉 20180101 起的 400+ 板块 → 600s stage 超时;
    #     且 --period day 是错的周期串(TQ 要 1d,见 TQ_INTERFACE_PROBE),现由脚本自动探测。
    r = _run_stage(["uv", "run", "python", str(TOOLS / "local_tdx" / "fetch_sector_index_history.py"),
                    "--out", str(BASE / "01_data" / "market" / "sector_index"),
                    "--start", "20180101", "--incremental"],
                   "refresh_sector_index", note="best-effort，失败不中断(仅影响板块相位 hint)")
    tail = _last_line(r["out"])
    if not r["ok"]:
        # 摘要行自带 "x/y 成功率"：低成功率(3/430)时脚本退 2，这里必须把该行透出，
        # 否则 [WARN] 只打前 200 字（"[INFO] 板块数: 430"），看不出这批基本全失败。
        print(f"[WARN] refresh_sector_index failed: {tail or r['out'][:200]}")
    elif tail.startswith("[WARN]"):
        print(tail)
    else:
        print(f"[OK] {tail or 'sector index refreshed'}")

    # 3. Screening chain (each stage propagates degradation downstream)
    degraded = []
    for script, name in [
        ("formula_screen.py", "screening_formula_screen"),
        ("enrich_candidates.py", "screening_enrich_candidates"),
        ("score_candidates.py", "screening_score_candidates"),
        ("candidate_table.py", "screening_candidate_table"),
    ]:
        r = _run_stage(["uv", "run", "python", str(SCREEN_DIR / script), "--date", target],
                       name, note="best-effort，失败不中断")
        stage_status = stage_json_status(r.get("stdout") or "")
        if not r["ok"]:
            degraded.append(name)
            print(f"[WARN] {name} failed: {r['out'][:200]}")
        else:
            if stage_status in ("unavailable", "partial"):
                degraded.append(name)
            print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else name}")

    # 4. Digest of the candidate table (may be absent when the chain degraded early)
    table_path = TABLE_DIR / target / f"{target}_candidate_table.md"
    if table_path.exists():
        stages_log.append(_log_stage("candidate_digest", {"ok": True, "returncode": 0, "timeout": False},
                                     _now_iso(), _now_iso(), 0.0, note=f"table={table_path.name}"))
        digest = md_to_digest(table_path.read_text(encoding="utf-8"), truncate_note="...(完整备选表见文件)")
    else:
        stages_log.append(_log_stage("candidate_digest", {"ok": False, "returncode": None, "timeout": False},
                                     _now_iso(), _now_iso(), 0.0,
                                     note=f"备选表未生成：{table_path}"))
        digest = "备选表未生成（选股链降级，详见 run log）"

    status = "completed" if not degraded else "degraded"
    _write_run_log(target, status, run_started, t0, stages_log)

    print(f"【每日选股｜{target}】")
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
