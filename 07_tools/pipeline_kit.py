# -*- coding: utf-8 -*-
r"""Shared pipeline infrastructure for strategy_team runners.

Extracted verbatim from existing code (daily_pipeline.py run(), run_0905.py /
run_2030.py md-to-digest block) to eliminate duplication across the four
runners. Behavior must match the sources exactly:

- run_stage: subprocess wrapper with [RUN] header, PYTHONIOENCODING=utf-8,
  stdout/stderr echo, RuntimeError on required failure, truncated dict result.
- check_trading_day: unified trading-calendar check replacing the three
  divergent parsing styles in the four runners.
- md_to_digest: markdown-to-plaintext digest conversion.
- warn: unified [WARN] output to stderr.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from paths import BASE


def run_stage(cmd: list[str], name: str, required: bool = True) -> dict:
    print(f"\n[RUN] {name}")
    print(" ".join(cmd))
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if p.stdout:
        print(p.stdout.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
    if p.stderr:
        print("[stderr]", p.stderr.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
    ok = p.returncode == 0
    if required and not ok:
        raise RuntimeError(f"stage failed: {name}, code={p.returncode}")
    return {"stage": name, "ok": ok, "returncode": p.returncode, "stdout": p.stdout[-4000:], "stderr": p.stderr[-4000:]}


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


def warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)
