# -*- coding: utf-8 -*-
"""持仓计划影子判定台账（#60 并轨判据的证据链，v0.310 owner 拍板）。

影子结果此前只渲染在 14:45/17:00 两份报告里——**没有台账，无法审计**
「每次不一致时事后看哪边的动作更好」（并轨的真正判据）。本模块是台账的
**唯一写入口径**：

- **append-only**：每日首写前自动快照 ``.bak_YYYYMMDD``（0AMV 覆写事故
  纪律，镜像 ``amv_state._backup_ledger_daily``）；严禁覆盖式写入；
- **幂等 (date, code, stage)**：同一时点的重复运行跳过；``stage="1445"``
  （盘中价）与 ``"1700"``（收盘价）两行**都保留不合并**——两个时点价格
  不同、影子结论可能不同。事后打分（research/plan_shadow_review）以
  ``stage="1700"`` 收盘口径为准，1445 只用于核对盘中/收盘判定一致性；
- 每行经 ``contracts.require("plan_shadow_observation")`` 校验后追加
  （生产者硬失败惯例）；
- 现行判定记两个口径：``live_final_priority``（b1 final_priority——
  **一致性统计只用这个**）+ ``report_priority``（报告自身列：14:45=
  classify() 输出，只留痕不参与统计）。

写入点 = 两份报告的重估流程（``review_core`` / ``final_close_review``）。
⚠️ 不是主批：``b1_holding_state.main`` 调 evaluate **不传 plan**，落盘的
shadow 恒为 plan_missing——写主批永远采不到事件（v0.310 核实）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from custos.core.contracts import require  # noqa: E402
from custos.core.paths import PLAN_SHADOW_LEDGER, cn_now  # noqa: E402

#: 台账行 schema 版本（contracts.SPECS 同名键）
SCHEMA = "plan_shadow_observation/v1"

#: 合法时点值：1445=盘中快照（现价），1700=盘后（收盘口径，事后打分以此为准）
STAGES = ("1445", "1700")


def _backup_ledger_daily(path: Path) -> None:
    """每日首写前把台账快照到 ``<name>.bak_YYYYMMDD``（已存在则跳过）。

    0AMV 事故纪律（2026-09-21 覆盖式写入灭失 8215 行）：任何写路径先进
    这里留当日快照——幂等，成本每天一次 copy；快照失败只 WARN 不阻断
    主链写（主防线上层纪律：append-only，禁覆盖写）。
    """
    if not path.exists():
        return
    bak = path.with_name(f"{path.name}.bak_{cn_now().strftime('%Y%m%d')}")
    if bak.exists():
        return
    try:
        bak.write_bytes(path.read_bytes())
    except Exception as exc:  # noqa: BLE001 — 快照失败不阻断主链写
        print(
            f"[WARN] plan 影子台账当日快照失败: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def _existing_keys(path: Path) -> set[tuple[str, str, str]]:
    """已落盘的 (date, code, stage) 键集（幂等判定；损坏行跳过不炸链）。"""
    keys: set[tuple[str, str, str]] = set()
    if not path.exists():
        return keys
    with path.open("r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            keys.add(
                (str(row.get("date")), str(row.get("code")), str(row.get("stage")))
            )
    return keys


def build_row(
    stage: str,
    day: str,
    code: str,
    b1_state: Optional[dict],
    extra: Optional[dict] = None,
) -> dict[str, Any]:
    """组台账行：影子字段取 b1_state["shadow"]，plan 价格/入场价取
    extra["plan"]（重估流程手上现成），close/report_priority 取 extra。

    ``agree``：plan_based_priority 为 None（plan_missing/plan_default——
    default 视同无计划，v0.310）⇒ None（不计入一致/不一致）；否则 =
    plan_based_priority == live_final_priority。
    """
    extra = extra or {}
    plan = extra.get("plan") if isinstance(extra.get("plan"), dict) else None
    b1_state = b1_state or {}
    shadow = b1_state.get("shadow") or {}
    signals = shadow.get("signals") or []
    live_p = b1_state.get("final_priority")
    plan_p = shadow.get("plan_based_priority")
    return {
        "schema": SCHEMA,
        "date": str(day),
        "code": str(code),
        "stage": str(stage),
        "plan_source": shadow.get("plan_source"),
        "plan_stop_price": (plan.get("stop") or {}).get("price") if plan else None,
        "plan_tp": plan.get("take_profit") if plan else None,
        "shadow_signal": signals[0].get("signal") if signals else None,
        "shadow_priority": plan_p,
        "shadow_action": shadow.get("plan_based_action"),
        "live_final_priority": live_p,
        "live_action": b1_state.get("final_action"),
        "report_priority": extra.get("report_priority"),
        "close": extra.get("close"),
        "entry_price": plan.get("entry_price") if plan else None,
        "agree": None if plan_p is None else (plan_p == live_p),
        "recorded_at": cn_now().isoformat(timespec="seconds"),
    }


def append_shadow(
    stage: str,
    day: str,
    code: str,
    b1_state: Optional[dict],
    extra: Optional[dict] = None,
    *,
    path: Optional[Path] = None,
) -> bool:
    """追加一行台账；同 (date, code, stage) 已存在 ⇒ 跳过（幂等，14:45
    与 17:00 各自可能重跑）。返回 True=本次写入 / False=已存在跳过。

    落盘前 contracts.require 校验（生产者硬失败）；append-only + 每日快照。
    """
    path = Path(path) if path else PLAN_SHADOW_LEDGER
    key = (str(day), str(code), str(stage))
    if key in _existing_keys(path):
        return False
    row = build_row(stage, day, code, b1_state, extra)
    require("plan_shadow_observation", row)
    _backup_ledger_daily(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True
