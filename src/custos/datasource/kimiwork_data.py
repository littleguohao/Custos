# -*- coding: utf-8 -*-
"""Kimi Work 内置金融数据源适配器（纯脚本、无项目侧新增依赖）。

通过托管 Python 子进程调用 ``kimiwork_agent_gw.py``，直接解析原始 JSON 与
CSV 内容。这里不引入 LLM 推理，也不改变任何交易决策；所有失败均显式抛出，
由采集器记录为缺数，禁止用旧缓存冒充本次成功。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from custos.core.paths import KIMI_WORK_CACHE, KIMI_WORK_PYTHON, TOOLS


class KimiWorkDataError(RuntimeError):
    """内置数据源调用失败或返回结构不完整。"""


def call_api(
    data_source: str, api_name: str, params: dict[str, Any], *, timeout: float = 100
) -> dict[str, Any]:
    """调用内置数据源并返回原始响应；不解析、不保存插件附件。"""
    command = [
        str(KIMI_WORK_PYTHON),
        str(TOOLS / "datasource" / "kimiwork_agent_gw.py"),
        "--data-source",
        data_source,
        "--api-name",
        api_name,
        "--params-json",
        json.dumps(params, ensure_ascii=False),
    ]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KimiWorkDataError(f"{data_source}.{api_name} 调用失败: {exc}") from exc
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).strip()[-600:]
        raise KimiWorkDataError(f"{data_source}.{api_name}: {detail}")
    try:
        raw = json.loads(proc.stdout)
    except ValueError as exc:
        raise KimiWorkDataError(
            f"{data_source}.{api_name} 返回非 JSON: {proc.stdout[-300:]!r}"
        ) from exc
    if not isinstance(raw, dict) or not raw.get("is_success"):
        error = raw.get("error") if isinstance(raw, dict) else raw
        raise KimiWorkDataError(f"{data_source}.{api_name}: {error!r}")
    return raw


def csv_table(raw: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    """从原始响应的 CSV 附件内容取表头和数据行，不落临时文件。"""
    for info in raw.get("files") or []:
        if not isinstance(info, dict):
            continue
        content = str(info.get("content") or "")
        if not content.strip():
            continue
        rows = list(csv.reader(io.StringIO(content)))
        if rows:
            return rows[0], rows[1:]
    raise KimiWorkDataError("数据源成功响应中没有 CSV 数据")


def _csv_records(raw: dict[str, Any]) -> list[dict[str, str]]:
    header, rows = csv_table(raw)
    return [dict(zip(header, row)) for row in rows]


def _cache_path(group: str, key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
    return KIMI_WORK_CACHE / group / f"{safe}.csv"


def yahoo_daily(symbol: str) -> list[dict[str, str]]:
    """内置 Yahoo 最近 5 日日 K（股票、指数、ETF、外汇）。"""
    raw = call_api(
        "yahoo_finance",
        "get_historical_stock_prices",
        {
            "ticker": symbol,
            "period": "5d",
            "interval": "1d",
            "file_path": str(_cache_path("yahoo", symbol)),
        },
    )
    return _csv_records(raw)


def dongcai_a50_daily() -> list[list[str]]:
    """东财富时中国 A50 期货最近 5 日收盘序列（日期倒序）。"""
    raw = call_api(
        "dongcai",
        "dongcai_mx_finance_data",
        {
            "query": "富时中国A50期货最近5个交易日收盘价，按日期倒序",
            "indicators": "收盘价",
            "file_path": str(_cache_path("dongcai", "a50")),
        },
    )
    _, rows = csv_table(raw)
    return rows


def wind_cnh_daily() -> list[dict[str, str]]:
    """Wind 即期汇率：美元兑离岸人民币（固定 EDB 指标 M0290205）。"""
    raw = call_api(
        "wind",
        "wind_query_economic_indicator_data",
        {
            "question": "M0290205",
            "observation": "5",
            "file_path": str(_cache_path("wind", "usdcnh")),
        },
    )
    return _csv_records(raw)


def cls_telegraphs(*, limit: int = 100) -> list[dict[str, Any]]:
    """财联社最新快讯；过滤隐藏与删除记录，保留正常状态条目。"""
    raw = call_api(
        "cls",
        "cls_telegraphs",
        {"classification": 0, "pageIndex": 1, "pageSize": int(limit)},
    )
    result = raw.get("result") or {}
    texts = result.get("user") or result.get("assistant") or []
    for part in texts:
        if not isinstance(part, dict) or not part.get("text"):
            continue
        payload = json.loads(str(part["text"]))
        entries = payload.get("result")
        if payload.get("code") == 0 and isinstance(entries, list):
            return [
                entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("status") == 1
            ]
    raise KimiWorkDataError("财联社响应缺少正常快讯列表")
