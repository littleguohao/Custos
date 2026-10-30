# -*- coding: utf-8 -*-
"""Kimi Work 内置金融数据源适配器（纯脚本、无项目侧新增依赖）。

通过托管 Python 子进程调用 ``kimiwork_agent_gw.py``，直接解析原始 JSON 与
CSV 内容。插件 ``file_path`` 仅指定到 ``artifacts/cache/kimiwork/``，附件是否
落盘由插件决定，解析不依赖附件。这里不引入 LLM 推理，也不改变交易决策；
所有失败均显式抛出，由采集器记录为缺数，禁止用旧缓存冒充本次成功。
自然语言妙想接口只用于影子对账，不得作为 live 主源。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, cast

from custos.core.paths import KIMI_WORK_CACHE, KIMI_WORK_PYTHON, TOOLS, cn_now, cn_today


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


def dongcai_a50_daily() -> list[dict[str, str]]:
    """仅供诊断的东财自然语言 A50 查询，禁止进入 live 主链。

    即使诊断也按表头取列；列顺序变化不会把日期误读成价格。
    """
    raw = call_api(
        "dongcai",
        "dongcai_mx_finance_data",
        {
            "query": "富时中国A50期货最近5个交易日收盘价，按日期倒序",
            "indicators": "收盘价",
            "file_path": str(_cache_path("dongcai", "a50")),
        },
    )
    header, raw_rows = csv_table(raw)
    if "headName" not in header:
        raise KimiWorkDataError("A50 诊断响应缺少日期表头 headName")
    value_fields = [name for name in header if name != "headName"]
    if len(value_fields) != 1:
        raise KimiWorkDataError(f"A50 诊断响应收盘字段不唯一: {value_fields}")
    value_field = value_fields[0]
    return [
        {"date": row["headName"], "close": row[value_field]}
        for row in (dict(zip(header, item)) for item in raw_rows)
    ]


def expected_daily_date(target: str) -> str:
    """返回该次采集允许的最新日线日，盘后要求当日、盘前要求上一交易日。"""
    from custos.core.runtime_guards import (  # noqa: PLC0415
        previous_confirmed_trading_day,
        trading_day_status,
    )

    target = str(target)[:10]
    today = cn_today().isoformat()
    if target < today and trading_day_status(target)["is_trading_day"] is True:
        return target
    if (
        target == today
        and trading_day_status(target)["is_trading_day"] is True
        and cn_now().hour >= 15
    ):
        return target
    expected = previous_confirmed_trading_day(target)
    if not expected:
        raise KimiWorkDataError(f"无法确定 {target} 的最近交易日")
    return expected


def validate_daily_as_of(day: str, target: str) -> str:
    """日期必须恰好是最近允许的交易日，拒绝旧数据或未来数据。"""
    expected = expected_daily_date(target)
    actual = str(day)[:10]
    if actual != expected:
        raise KimiWorkDataError(
            f"数据日期陈旧或不匹配: as_of={actual}, expected={expected}, target={target}"
        )
    return expected


def wind_a50_daily() -> list[dict[str, str]]:
    """Wind 固定代码 L5471360：富时中国 A50 指数（非期货，代理用途）。"""
    raw = call_api(
        "wind",
        "wind_query_economic_indicator_data",
        {
            "question": "L5471360",
            "observation": "5",
            "file_path": str(_cache_path("wind", "a50_index")),
        },
    )
    return _csv_records(raw)


def parse_cn_amount(value: Any) -> float | None:
    """把 ``-1.23亿`` / ``456.7万`` / ``1.08万亿`` 转为元，空值返回 None。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or text in {"-", "--", "None", "nan", "NaN"}:
        return None
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(万亿|亿|万|千|百)?", text)
    if not match:
        raise ValueError(f"无法解析中文金额: {value!r}")
    number, unit = match.groups()
    multiplier = {None: 1.0, "万亿": 1e12, "亿": 1e8, "万": 1e4, "千": 1e3, "百": 1e2}[
        unit
    ]
    return float(number) * multiplier


def _number_or_none(value: Any) -> float | None:
    if value is None or str(value).strip() in {"", "-", "--", "None", "nan", "NaN"}:
        return None
    return float(str(value).replace("%", "").strip())


def _field(row: dict[str, str], prefix: str) -> str:
    """按表头前缀取字段，屏蔽内置接口附加的日期／单位后缀。"""
    for key, value in row.items():
        if key.startswith(prefix):
            return value
    return ""


def _header_dates(header: list[str]) -> set[str]:
    dates = set()
    for name in header:
        match = re.search(r"\((\d{4})\.(\d{2})\.(\d{2})\)", name)
        if match:
            dates.add("-".join(match.groups()))
    return dates


def dongcai_fund_flow_stocks(
    limit: int = 200, *, target_date: str | None = None
) -> list[dict[str, Any]]:
    """仅供影子对账的东财妙想资金流榜，绝不作为 live 主源。

    接口接受自然语言查询，无法由契约证明后端不含 LLM，因此只能作诊断。
    这里仍严格校验每个分段的日期、重复、缺口、排序和数量，防止拼接静默污染。
    """
    limit = max(1, int(limit))
    target = target_date or cn_today().isoformat()
    expected = expected_daily_date(target)
    result = []
    seen = set()
    for start in range(1, limit + 1, 10):
        end = min(start + 9, limit)
        query = (
            f"今日主力净额排名第{start}至{end}的A股，展示代码、名称、最新价、涨跌幅、"
            "主力净额、主力净占比、超大单净额、超大单净占比、大单净额、大单净占比、"
            "中单净额、中单净占比、小单净额、小单净占比"
        )
        raw = call_api(
            "dongcai",
            "dongcai_mx_stocks_screener",
            {
                "query": query,
                "select_type": "A股",
                "file_path": str(
                    _cache_path("dongcai", f"fund_flow_stocks_{start}_{end}")
                ),
            },
            timeout=180,
        )
        header, raw_rows = csv_table(raw)
        dates = _header_dates(header)
        if dates != {expected}:
            raise KimiWorkDataError(
                f"资金流分段日期不匹配: {sorted(dates) or '缺失'}，expected={expected}"
            )
        rows = [dict(zip(header, row)) for row in raw_rows]
        if len(rows) != end - start + 1:
            raise KimiWorkDataError(
                f"资金流分段 {start}~{end} 返回 {len(rows)} 行，存在缺口"
            )
        for row in rows:
            code = _field(row, "代码").strip()
            if not code or code in seen:
                raise KimiWorkDataError(f"资金流分段重复或缺代码: {code!r}")
            seen.add(code)
            result.append(
                {
                    "code": code,
                    "name": _field(row, "名称"),
                    "price": _number_or_none(_field(row, "最新价")),
                    "change_pct": _number_or_none(_field(row, "涨跌幅")),
                    "main_net_inflow": parse_cn_amount(_field(row, "主力净额")),
                    "main_net_pct": _number_or_none(_field(row, "主力净占比")),
                    "super_large_net": parse_cn_amount(_field(row, "超大单净额")),
                    "super_large_pct": _number_or_none(_field(row, "超大单净占比")),
                    "large_net": parse_cn_amount(_field(row, "大单净额")),
                    "large_pct": _number_or_none(_field(row, "大单净占比")),
                    "medium_net": parse_cn_amount(_field(row, "中单净额")),
                    "medium_pct": _number_or_none(_field(row, "中单净占比")),
                    "small_net": parse_cn_amount(_field(row, "小单净额")),
                    "small_pct": _number_or_none(_field(row, "小单净占比")),
                }
            )
    if len(result) != limit:
        raise KimiWorkDataError(f"资金流榜单数量不足: {len(result)}/{limit}")
    if any(not isinstance(x["main_net_inflow"], (int, float)) for x in result):
        raise KimiWorkDataError("资金流榜单存在缺失主力净额")
    result.sort(key=lambda x: cast(float, x["main_net_inflow"]), reverse=True)
    amounts = [cast(float, x["main_net_inflow"]) for x in result]
    if any(a < b for a, b in zip(amounts, amounts[1:])):
        raise KimiWorkDataError("资金流拼接结果未按主力净额单调递减")
    return result


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
