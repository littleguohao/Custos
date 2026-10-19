# -*- coding: utf-8 -*-
"""周报 / 月报的「操作反事实」小节（v0.424，owner 2026-10-19：默认每周每月报告里也生成）。

推演引擎在研究侧（``custos.research.trade_counterfactual``，复用回测引擎出场语义），而**生产永不
import 研究**（架构分层测试强制）——所以这里只以**子进程**调用其 CLI、读回它写的 markdown，
不 import、不共享任何对象；报告侧只是附录，不参与任何判定。

fail-soft：子进程失败 / 超时 / 台账缺失只在报告里留一行原因，**绝不打断周报 / 月报主体**。
环境变量 ``CUSTOS_SKIP_TRADE_CF=1`` 跳过（测试默认设，见 conftest）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 900
HEADER = "## 🔁 操作反事实：同一笔买入，严格按规则操作 vs 实际"


def section(base: Path, start: str, end: str, stem: str) -> str:
    """返回可直接拼到报告末尾的 markdown 小节（含失败说明）。"""
    if os.environ.get("CUSTOS_SKIP_TRADE_CF") == "1":
        return ""
    ledger = base / "data" / "trades" / "master_trade_ledger.csv"
    if not ledger.exists():
        return f"\n{HEADER}\n\n> unavailable：成交台账不存在（{ledger.name}）\n"
    out = base / "artifacts" / "logs" / "trade_counterfactual" / f"{stem}.json"
    cmd = [
        sys.executable,
        "-m",
        "custos.research",
        "trade_counterfactual",
        "--ledger",
        str(ledger),
        "--plans",
        str(base / "data" / "trades" / "position_plans.json"),
        "--period",
        start,
        end,
        "--out",
        str(out),
    ]
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"\n{HEADER}\n\n> unavailable：推演子进程失败（{type(exc).__name__}）\n"
    md = out.with_suffix(".md")
    if cp.returncode != 0 or not md.exists():
        tail = (cp.stderr or "").strip().splitlines()[-1:] or ["无输出"]
        return f"\n{HEADER}\n\n> unavailable：{tail[0][:200]}\n"
    body = md.read_text(encoding="utf-8").splitlines()
    # 工具报告自带一级 / 二级标题 ⇒ 降两级嵌进报告；首行标题由本节 HEADER 代替
    lines = [("##" + ln) if ln.startswith("#") else ln for ln in body[1:]]
    return (
        "\n"
        + HEADER
        + "\n"
        + "\n".join(lines)
        + f"\n\n> 明细 JSON：`{out.relative_to(base).as_posix()}`\n"
    )
