# -*- coding: utf-8 -*-
"""17:00 前一次性飞书补收：交易日检查通过后接收待处理回复并立即退出。

由 deterministic_pipeline 以带超时的子进程调用；非交易日不接收。回复「有交易」时沿用
入站桥的投资账本同步与台账唯一入口，其他回复只做确定性登记，不调用模型。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]


def _load_sibling(module_name: str) -> Any:
    path = Path(__file__).with_name(module_name + ".py")
    spec = importlib.util.spec_from_file_location(module_name + "_once", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_once(
    day: str,
    *,
    trading_day_fn: Callable[[str], bool] | None = None,
    ingest_fn: Callable[[dict], dict] | None = None,
) -> dict[str, Any]:
    """执行一次补收；依赖可注入，测试不触网、不碰真实台账。"""
    date.fromisoformat(day)
    if trading_day_fn is None or ingest_fn is None:
        reminder = _load_sibling("feishu_postclose_reminder")
        bridge = _load_sibling("feishu_inbound_bridge")
        trading_day_fn = trading_day_fn or reminder._is_trading_day
        ingest_fn = ingest_fn or bridge.run
    if not trading_day_fn(day):
        return {
            "date": day,
            "trading_day": False,
            "ingested": False,
            "new_messages": 0,
            "actions": [],
            "reason": "non_trading_day",
        }
    payload = ingest_fn({})
    artifact = payload.get("artifact") if isinstance(payload, dict) else None
    if not isinstance(artifact, dict):
        raise RuntimeError("飞书入站桥返回缺少 artifact 对象")
    return {**artifact, "date": day, "trading_day": True, "ingested": True}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    args = parser.parse_args(argv)
    print(json.dumps({"artifact": run_once(args.date)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
