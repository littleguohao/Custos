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
import json
import os
import sys
import time

from paths import BASE, SCREENING, TOOLS, cn_today
from pipeline_kit import log_stage, md_to_digest, now_iso, write_run_log, run_stage_quiet as _stage, calendar_gate

SCREEN_DIR = SCREENING
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
    _cg = calendar_gate(
        target, log_dir=LOG_DIR, session="1800", run_started=run_started,
        t0=t0, stages_log=stages_log,
        fail_msg="【每日选股失败｜{target}】日历检查失败：{err}",
        closed_msg="今日休市，每日选股不运行（{target}）")
    if _cg.exit_code is not None:
        return _cg.exit_code
    cal = _cg.cal

    # 2. Runtime gate —— **只落盘，不阻断**。
    #    18:00 是纯粹的选股流程，门控不得影响选股结果（不改 bucket/next_step/分层），
    #    只由 candidate_table 在备选表里单独给出「数据可信度提示」区块。这样选股结果
    #    保持与回测同口径、可复现，"策略本身选出了什么"始终可回溯。
    #    不传任何 --require-* 开关：非交易日已在上面的 calendar 检查里返回，
    #    质量/持仓 blocked 不该让选股链失败（失败等于连诊断产物都没有）。
    r = _run_stage(["uv", "run", "python", str(TOOLS / "runtime_gate.py"),
                    "--date", target, "--data-session", "postclose"],
                   "runtime_gate", note="只落盘供候选表引用，不阻断选股")
    if not r["ok"]:
        print(f"[WARN] runtime_gate failed: {r['out'][:200]}")

    # 3. Refresh stock name cache —— ST 硬排除的唯一依据，必须滚动更新。
    #    此前只有 mootdx 一个在线源且它 2026-07 起持续失败，缓存靠手动跑脚本、无 cron、
    #    读取时也不校验时效 ⇒ 一份永不更新的名称表长期在用，新被 ST 的票名字还是正常的，
    #    照样通过硬排除，而 st_filter 仍报 ok（审计 B5 的延伸）。
    #    best-effort：失败不中断，选股链内部还有"候选名称按需刷新 + 缓存时效判定"两道防线。
    r = _run_stage(["uv", "run", "python", str(TOOLS / "local_tdx" / "stock_names.py"),
                    "--source", "auto"], "refresh_stock_names",
                   note="best-effort，失败不中断；ST 硬排除依赖它")
    if not r["ok"]:
        print(f"[WARN] refresh_stock_names failed: {r['out'][:200]}")
    else:
        print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'stock names refreshed'}")

    # 3b. Refresh ex-dividend (权息) cache —— 前复权的依据（owner 2026-08-04 拍板全链前复权）。
    #     未复权数据会把除权跳空当成真实暴跌：假止损、假 J<13 信号、假跌停
    #     （实测同一段真实上涨走势 −42.50% vs +25.00%，差 67.5pp）。
    #     除权事件是历史事实不会变，所以只按 --max-age 增量刷新；分红送转有 >2 周
    #     预案公告期，7 天上限足以在除权日前拿到新事件。
    #     best-effort：权息拿不到时 get_ohlcv_table 按未复权返回并在 attrs 留痕，
    #     不该让整条选股链停摆。
    r = _run_stage(["uv", "run", "python", str(TOOLS / "local_tdx" / "adjust_factors.py"),
                    "--warmup", "--max-age", "7"], "refresh_xdxr",
                   note="前复权依据，best-effort；缺失时按未复权并在 attrs 留痕")
    if not r["ok"]:
        print(f"[WARN] refresh_xdxr failed: {r['out'][:200]}")
    else:
        print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'xdxr refreshed'}")

    # 4. Refresh concept tags (miscinfo) so sector mapping uses the accurate source
    r = _run_stage(["uv", "run", "python", str(TOOLS / "local_tdx" / "concept_tags.py"),
                    "--date", target], "refresh_concept_tags", note="best-effort，失败不中断")
    if not r["ok"]:
        print(f"[WARN] refresh_concept_tags failed: {r['out'][:200]}")
    else:
        print(f"[OK] {r['out'].splitlines()[-1] if r['out'] else 'concept tags refreshed'}")

    # 4b. Refresh 板块指数缓存(供 enrich 的 sector_phase hint 用当日相位;best-effort,需 TdxW)。
    #     **增量合并**:只拉各板块缓存末日期前 30 天起的新数据并 merge 进已有 CSV。
    #     此前每天全量重拉 20180101 起的 400+ 板块 → 600s stage 超时;
    #     且 --period day 是错的周期串(TQ 要 1d,见 TDX_LOCAL_INTERFACES.md),现由脚本自动探测。
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

    # 5. Screening chain (each stage propagates degradation downstream)
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

    # 6. Digest of the candidate table (may be absent when the chain degraded early)
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
