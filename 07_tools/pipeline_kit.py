# -*- coding: utf-8 -*-
r"""Shared pipeline infrastructure for strategy_team runners.

Extracted verbatim from existing code (daily_pipeline.py run(), run_0905.py /
run_2030.py md-to-digest block) to eliminate duplication across the four
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
  06_logs/{date}_{tag}_run_log.json with per-stage ok/returncode/timeout/
  timings/stdout/stderr tails and an overall status.
- warn: unified [WARN] output to stderr.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from paths import BASE


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
        ["uv", "run", "python", str(BASE / "07_tools" / "trading_calendar.py"), "--check-date", date_str],
        f"trading_calendar {date_str}",
        required=True,
    )
    return _extract_json(r["stdout"])


def md_to_digest(md_text: str, limit: int = 3500, truncate_note: str = "...(完整报告见文件)") -> str:
    """Convert a markdown report to a plaintext digest.

    Headers become text followed by a ─ underline, table rows become
    pipe-joined cell text (separator rows skipped), bullet lines are kept,
    other non-empty lines are kept only after the first header (in_section).
    Leading empty lines are skipped. When the digest exceeds limit chars it
    is truncated to limit - 50 chars and truncate_note is appended on a new
    line (run_0905 uses "...(完整报告见文件)", run_2030 uses "...(完整复盘见文件)").
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
        digest = digest[:limit - 50] + "\n" + truncate_note
    return digest


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
    """Write 06_logs/{date}_{tag}_run_log.json; tag is the runner suffix
    ("0850", "0905"), which also determines the script field (run_{tag})."""
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
