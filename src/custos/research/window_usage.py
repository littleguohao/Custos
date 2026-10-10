# -*- coding: utf-8 -*-
"""判定窗 / pre2019 使用台账（owner 方法论 review #1①，v0.321）。

双窗纪律只能防住**单次跑数内**偷看判定窗，管不了**跨研究**的情况：判定窗
2024-08-01~2026-09-04 已被 13 个研究单元反复读取——每轮看完读数再决定
下一步问什么、改什么判据（「跑数前修订」每次都合规，但修订依据都是前
一轮读数），分岔路径下多次使用的判定窗**不再是样本外**。本台账把「谁
在什么时候读了哪扇窗」机械化：**每读一次记一行**（append-only + 每日
快照，0AMV 事故纪律），研究报告写明「这是该窗**第 k 次被读**」——
k 越大，该窗的读数越要按「已被反复使用」打折判读。台账自 v0.321 起算
（历史使用不追溯，在案事实见各单元回填区）。

- 台账路径：`governance/research/window_usage.jsonl`（治理数据，跨机
  靠 git 同步——判读的「第 k 次」以全量台账为准）；测试经 conftest
  改道 tmp，不写真实台账；
- 键 = ``window`` 字段（``"judgment"`` / ``"pre2019"``——窗口区间由
  `exit_c5_terminal` 常量单源锚定，不带进键）；
- 行字段：schema/unit/window/tag/purpose/recorded_at。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from custos.core.paths import RESEARCH_DIR, cn_now  # noqa: E402

#: 台账路径（治理数据；测试由 conftest autouse 改道 tmp）
LEDGER = RESEARCH_DIR / "window_usage.jsonl"

SCHEMA = "window_usage/v1"

#: 窗名 → 区间锚定（展示/判读用；键只用窗名）
WINDOW_SPAN = {
    "judgment": "2024-08-01~2026-09-04",
    "pre2019": "2010-01-01~2016-12-31",
}


def _backup_ledger_daily(path: Path) -> None:
    """每日首写前快照 ``<name>.bak_YYYYMMDD``（已存在跳过；失败只 WARN）。"""
    if not path.exists():
        return
    bak = path.with_name(f"{path.name}.bak_{cn_now().strftime('%Y%m%d')}")
    if bak.exists():
        return
    try:
        bak.write_bytes(path.read_bytes())
    except Exception as exc:  # noqa: BLE001 — 快照失败不阻断主链写
        print(
            f"[WARN] 判定窗台账当日快照失败: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def count_uses(window: str, *, path: Optional[Path] = None) -> int:
    """该窗已登记的使用次数（损坏行跳过不炸链；路径缺失/非文件 ⇒ 0）。"""
    path = Path(path) if path else LEDGER
    if not path.is_file():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            if row.get("window") == window:
                n += 1
    return n


def record_use(
    unit: str, window: str, tag: str, purpose: str, *, path: Optional[Path] = None
) -> int:
    """登记一次窗口读取，返回「这是该窗第 k 次被读」（含本次）。

    append-only + 每日首写快照；**台账写失败不炸研究**（旁路治理数据——
    WARN 后返回当时计数，同影子台账的隔离语义）。
    """
    path = Path(path) if path else LEDGER
    row: dict[str, Any] = {
        "schema": SCHEMA,
        "unit": str(unit),
        "window": str(window),
        "tag": str(tag),
        "purpose": str(purpose),
        "recorded_at": cn_now().isoformat(timespec="seconds"),
    }
    try:
        _backup_ledger_daily(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — 旁路治理数据不炸研究主链
        print(
            f"[WARN] 判定窗台账写入失败（不影响研究）: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    return count_uses(window, path=path)


def usage_note(unit: str, window: str, k: int) -> str:
    """报告用注记行：「这是该窗第 k 次被读」+ 判读打折指引。"""
    span = WINDOW_SPAN.get(window, window)
    return (
        f"{unit} 本次是 {window} 窗（{span}）第 {k} 次被读（台账 v0.321 起算）"
        "——多轮读取的判定窗按「已被反复使用」打折判读（分岔路径，"
        "owner 方法论 review #1）"
    )


# ---------------------------------------------------------------------------
# CLI（诊断：查窗口被读次数）
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """查某窗已被登记读取的次数（诊断/对账用，只读不写）。"""
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(
        description="判定窗/pre2019 使用台账查询（「第 k 次被读」判读打折依据）"
    )
    ap.add_argument(
        "--window",
        default="judgment",
        choices=sorted(WINDOW_SPAN),
        help="窗名（默认 judgment）",
    )
    args = ap.parse_args(argv)
    n = count_uses(args.window)
    print(
        f"{args.window}（{WINDOW_SPAN[args.window]}）已被登记读取 {n} 次"
        f"（台账 {LEDGER}）——k 越大越要按「已被反复使用」打折判读"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
