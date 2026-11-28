# -*- coding: utf-8 -*-
"""东方财富 datacenter 分页完整性守卫（全项目唯一实现）。

市值与 PIT 财务共用同一响应协议：``pages`` 优先、``count`` 兜底；残缺响应必须抛
``FetchIncomplete``，不得把限流空页当成正常翻完。两个采集模块保留原函数签名作兼容包装。
"""

from __future__ import annotations

import json
from typing import Any


class FetchIncomplete(RuntimeError):
    """分页未拉完或响应残缺；残缺样本不得按成功落盘。"""


def as_int(value: Any) -> int | None:
    """转 int；不可解析、NaN 或 ±inf 一律返回 None。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def is_empty_ok(payload: dict) -> bool:
    """响应自称成功且带 ``result`` 段时，才允许调用方把空结果视为正常。"""
    if payload.get("success") is False:
        return False
    if str(payload.get("code", 0)) not in ("0", "None"):
        return False
    return "result" in payload


def brief(payload: dict, n: int = 160) -> str:
    """异常响应的短摘要，保持原采集模块的序列化与截断口径。"""
    try:
        return json.dumps(payload, ensure_ascii=False)[:n]
    except (TypeError, ValueError):
        return str(payload)[:n]


def page_done(label: str, page: int, pages: Any, count: Any, rows: list[dict]) -> bool:
    """按接口自报的 ``pages/count`` 判断是否翻完；两者都缺则报残缺。"""
    if pages is not None:
        return page >= pages
    if count is not None and len(rows) >= count:
        return True
    raise FetchIncomplete(f"{label} 第 {page} 页未给 pages/count，无法判断是否翻完")
