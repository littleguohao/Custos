# -*- coding: utf-8 -*-
"""Kimi Work 内置数据源的最小 JSON-RPC 入口（由 Kimi 托管 Python 执行）。

项目虚拟环境不安装 agent-gw；生产采集器用 ``KIMI_WORK_PYTHON`` 指向的托管
Python 执行本文件。只打印原始 JSON，不保存插件返回的附件——调用方直接消费
``files[].content``，避免把临时文件写到仓库外。
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from typing import Any


def _params(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("--params-json 必须是 JSON 对象")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-source", required=True)
    parser.add_argument("--api-name", required=True)
    parser.add_argument("--params-json", default="{}")
    args = parser.parse_args(argv)
    try:
        agent_gw = importlib.import_module("agent_gw")
        with agent_gw.AgentGwClient(timeout=90.0) as client:
            response = client.tools.call_data_source_tool(
                {
                    "data_source_name": args.data_source,
                    "api_name": args.api_name,
                    "params": _params(args.params_json),
                }
            )
        print(json.dumps(response.raw, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 — 子进程边界统一结构化报错
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
