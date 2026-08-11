# -*- coding: utf-8 -*-
r"""Shared pipeline infrastructure for strategy_team runners.

Extracted verbatim from existing code (daily_pipeline.py run(), run_0905.py /
run_1700.py md-to-digest block) to eliminate duplication across the five
runners. Behavior must match the sources exactly:

- run_stage: subprocess wrapper with [RUN] header, PYTHONIOENCODING=utf-8,
  stdout/stderr echo, RuntimeError on required failure, truncated dict result.
  Stages are bounded by a timeout (default 600s); a timeout is treated as a
  failure (ok=False, timeout=True in the result, RuntimeError when required).
- check_trading_day: unified trading-calendar check replacing the three
  divergent parsing styles in the four runners.
- md_to_digest: markdown-to-plaintext digest conversion.
- now_iso / log_stage / write_run_log: run-log observability shared by the
  one-shot runners (run_0850, run_0905); each run leaves
  artifacts/logs/{date}_{tag}_run_log.json with per-stage ok/returncode/timeout/
  timings/stdout/stderr tails and an overall status.
- warn: unified [WARN] output to stderr.
"""
from __future__ import annotations

import io

import contextlib
from typing import NamedTuple

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from custos.core.paths import BASE, TOOLS, cn_now


def _as_text(data) -> str:
    """Normalize subprocess output that may be str, bytes, or None to str."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def run_stage(cmd: list[str], name: str, required: bool = True, timeout: int = 600) -> dict:
    print(f"\n[RUN] {name}")
    print(" ".join(cmd))
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    timed_out = False
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=timeout)
        stdout, stderr, returncode = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = _as_text(e.stdout)
        stderr = _as_text(e.stderr)
        returncode = None
        print(f"[TIMEOUT] {name} exceeded {timeout}s, process killed")
    if stdout:
        print(stdout.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
    if stderr:
        print("[stderr]", stderr.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
    ok = not timed_out and returncode == 0
    if required and not ok:
        if timed_out:
            raise RuntimeError(f"stage timed out: {name}, timeout={timeout}s")
        raise RuntimeError(f"stage failed: {name}, code={returncode}")
    return {"stage": name, "ok": ok, "returncode": returncode, "timeout": timed_out,
            "stdout": stdout[-4000:], "stderr": stderr[-4000:]}


class CalendarGate(NamedTuple):
    """交易日门控结果。`exit_code` 非 None 时调用方应立即 return 它。"""
    cal: dict
    exit_code: int | None


def calendar_gate(target: str, *, log_dir, session: str, run_started: str,
                  t0: float, stages_log: list[dict],
                  fail_msg: str, closed_msg: str) -> CalendarGate:
    """跑交易日检查，落 stage 日志，决定是否继续。

    2026-08-06 从 5 个 runner 抽出 —— 那 5 份是**结构完全相同**的 23 行块
    （同样的 `calendar_failed` → exit 1、`closed` → exit 0、同样的 stdout 捕获与计时），
    只有两句打印消息不同。抽出前，改门控语义要手工同步 5 遍，
    而 2026-07-30 那次事故正是门控行为在多处叠加改动导致的。

    三种结局：

        日历检查抛错   → 记 stage(ok=False) + run_log("calendar_failed") + 打印 → exit 1
        非交易日       → 记 stage(ok=True)  + run_log("closed")           + 打印 → exit 0
        交易日         → 记 stage(ok=True)，exit_code=None（调用方继续）

    ⚠️ `check_trading_day` 的 stdout 必须捕获：runner 的 stdout 是**给机器消费的协议**，
    日历检查的回显会污染它。捕获到的内容进 stage 日志，不丢。

    `fail_msg` / `closed_msg` 支持 `{target}` 与 `{err}` 占位。
    """
    c_started = now_iso()
    c_t0 = time.time()
    cal_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(cal_buf):
            cal = check_trading_day(target)
    except RuntimeError as e:
        stages_log.append(log_stage("calendar",
                                    {"ok": False, "returncode": None, "timeout": False,
                                     "stdout": cal_buf.getvalue(), "stderr": str(e)},
                                    c_started, now_iso(), time.time() - c_t0,
                                    note=str(e)[:500]))
        write_run_log(log_dir, session, target, "calendar_failed", run_started, t0, stages_log)
        print(fail_msg.format(target=target, err=str(e)[:200]))
        return CalendarGate({}, 1)
    stages_log.append(log_stage("calendar",
                                {"ok": True, "returncode": 0, "timeout": False,
                                 "stdout": cal_buf.getvalue()},
                                c_started, now_iso(), time.time() - c_t0,
                                note=f"is_trading_day={cal.get('is_trading_day')}"))
    if not cal.get("is_trading_day", False):
        write_run_log(log_dir, session, target, "closed", run_started, t0, stages_log)
        print(closed_msg.format(target=target))
        return CalendarGate(cal, 0)
    return CalendarGate(cal, None)


GATE_EXIT_CODES = frozenset({3, 4, 5})   # 3 非交易日 / 4 质量 blocked / 5 持仓 blocked


def propagate_gate_code(r: dict, default: int = 1) -> int:
    """子进程若以**门控码**退出就原样上抛，其余压成 `default`。

    为什么需要：`daily_pipeline` 已经把门控码（3/4/5）穿透到自己的进程退出码
    （见其 `raise SystemExit(gate_stage["returncode"] or 1)`），**但 runner 曾把它压平成 1**
    ⇒ cron 只看到「失败」，分不清「质量 blocked」和「任意 stage 挂了」。

    当前无害（run_0905/run_1700 都没传 `--strict-quality-gate`，内层不会 exit 4），
    但 README 明确说硬闸会在 stale 校准跑通后启用 —— **那一刻正是需要区分的时刻**，
    而那时没人会想起来 runner 这一层把码抹了。所以现在就修。

    只放行 3/4/5：其他非零码（子进程崩、超时、依赖缺失）语义是「跑挂了」，
    统一成 1 更清楚，避免把 Python 的 exit 2（argparse 用法错）之类误读成门控结论。
    """
    rc = r.get("returncode")
    return rc if rc in GATE_EXIT_CODES else default


def run_stage_quiet(cmd: list[str], name: str) -> dict:
    """静默跑一个 stage，把 stdout+stderr 合并进 `r["out"]`。

    **为什么要静默**：runner 的 stdout 是**给机器消费的协议**（cron / 上游解析它），
    stage 的回显（`[RUN]` 头、子进程输出）会污染协议，所以重定向掉，
    只保留 runner 自己打的摘要行。

    2026-08-06 从 5 个 runner 里抽出 —— 那 5 份 `_stage` 是**字节级相同**的
    （`run_1800` 只少了 docstring）。抽出前任何一处改动都得手工同步 5 遍。
    `required=False` 是这些 runner 的一致选择：单 stage 失败不中断整链，
    由 runner 汇总成 `status="degraded"`。
    """
    with contextlib.redirect_stdout(io.StringIO()):
        r = run_stage(cmd, name, required=False)
    r["out"] = (r["stdout"] + r["stderr"]).strip()
    return r


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from text. Tolerates stderr noise or
    other non-JSON content mixed into the output, and both compact
    single-line JSON and pretty-printed multi-line JSON.

    Tries json.JSONDecoder().raw_decode at every '{' position in order and
    returns the first result that is a dict. Returns {} if none is found.
    """
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text[start:])
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            return obj
        start = text.find("{", start + 1)
    return {}


def check_trading_day(date_str: str) -> dict:
    """Check whether date_str (YYYY-MM-DD) is a trading day.

    Runs trading_calendar.py as a subprocess (required: a non-zero exit
    raises RuntimeError) and extracts the first JSON object printed on
    stdout. Returns {} when no JSON object is found; non-trading-day /
    failure semantics are decided by the caller (e.g. cal.get("is_trading_day")).
    """
    r = run_stage(
        ["uv", "run", "python", str(TOOLS / "datasource" / "trading_calendar.py"), "--check-date", date_str],
        f"trading_calendar {date_str}",
        required=True,
    )
    return _extract_json(r["stdout"])


def _split_digest_sections(digest: str) -> list[str]:
    """Split a digest into sections; a new section starts at each converted
    header (a text line followed by a ─ underline line)."""
    lines = digest.split("\n")
    sections: list[str] = []
    current: list[str] = []
    for i, line in enumerate(lines):
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        starts_header = bool(line.strip()) and bool(nxt) and set(nxt) == {"─"}
        if starts_header and current:
            sections.append("\n".join(current).strip("\n"))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current).strip("\n"))
    return [s for s in sections if s]


def _truncate_digest(digest: str, limit: int, truncate_note: str) -> str:
    """Truncate an over-limit digest, keeping key sections whole when possible.

    Sections are selected by priority — first section (report title), sections
    headed "1." (今日核心结论) / "6." (当日行动建议), then the rest in original
    order — and emitted in original document order within a limit - 50 char
    budget, with truncate_note appended (same contract as the plain cut). When
    no section fits the budget, fall back to the plain prefix cut.
    """
    budget = limit - 50
    sections = _split_digest_sections(digest)

    def is_key(section: str) -> bool:
        head = section.split("\n", 1)[0].strip()
        return head.startswith("1.") or head.startswith("6.")

    priority = ([0] + [i for i in range(1, len(sections)) if is_key(sections[i])]
                + [i for i in range(1, len(sections)) if not is_key(sections[i])])
    picked: list[int] = []
    total = 0
    for i in priority:
        cost = len(sections[i]) + (2 if picked else 0)  # "\n\n" separator
        if total + cost > budget:
            continue
        picked.append(i)
        total += cost
    if not picked:
        return digest[:budget] + "\n" + truncate_note
    body = "\n\n".join(sections[i] for i in sorted(picked))
    return body + "\n" + truncate_note


def md_to_digest(md_text: str, limit: int = 3500, truncate_note: str = "...(完整报告见文件)") -> str:
    """Convert a markdown report to a plaintext digest.

    Headers become text followed by a ─ underline, table rows become
    pipe-joined cell text (separator rows skipped), bullet lines are kept,
    other non-empty lines are kept only after the first header (in_section).
    Leading empty lines are skipped. When the digest exceeds limit chars it is
    truncated to a limit - 50 char budget and truncate_note is appended on a
    new line (run_0905 uses "...(完整报告见文件)", run_1700 uses
    "...(完整复盘见文件)"); truncation prefers keeping the title section and
    the "1."/"6." sections whole (see _truncate_digest).
    """
    lines = md_text.split("\n")
    digest_lines = []
    in_section = False
    for line in lines:
        # Skip empty lines at start
        if not line.strip() and not digest_lines:
            continue
        # Include headers, bullet points, and key content; convert tables to text
        if line.startswith("#"):
            # Convert markdown header to text
            text = line.lstrip("#").strip()
            digest_lines.append(f"\n{text}")
            digest_lines.append("─" * min(len(text) * 2, 40))
            in_section = True
        elif line.startswith("|"):
            # Convert table rows to text
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if cells and not all(set(c) <= set("-: ") for c in cells):
                digest_lines.append(" | ".join(cells))
        elif line.startswith("- ") or line.startswith("• "):
            digest_lines.append(line)
        elif line.strip() and in_section:
            digest_lines.append(line)

    digest = "\n".join(digest_lines).strip()
    if len(digest) > limit:
        digest = _truncate_digest(digest, limit, truncate_note)
    return digest


def now_iso() -> str:
    return cn_now().isoformat(timespec="seconds")


def log_stage(name: str, r: dict, started_at: str, finished_at: str, duration_sec: float,
              note: str = "") -> dict:
    """Build one run-log stage entry from a run_stage-style result dict."""
    entry = {
        "name": name,
        "ok": bool(r.get("ok", False)),
        "returncode": r.get("returncode"),
        "timeout": bool(r.get("timeout", False)),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": round(duration_sec, 2),
        "stdout_tail": (r.get("stdout") or "")[-1000:],
        "stderr_tail": (r.get("stderr") or "")[-1000:],
    }
    if note:
        entry["note"] = note
    return entry


def write_run_log(log_dir: Path, tag: str, target: str, status: str, started_at: str,
                  t0: float, stages: list[dict]) -> Path:
    """Write artifacts/logs/{date}_{tag}_run_log.json; tag is the runner suffix
    ("0850", "0905", "1700", "1800"), which also determines the script field (run_{tag})."""
    log = {
        "date": target,
        "script": f"run_{tag}",
        "status": status,
        "started_at": started_at,
        "finished_at": now_iso(),
        "duration_sec": round(time.time() - t0, 2),
        "stages": stages,
    }
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{target}_{tag}_run_log.json"
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)
