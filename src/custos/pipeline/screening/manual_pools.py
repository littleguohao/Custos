# -*- coding: utf-8 -*-
"""通达信自定义板块（自选池）读取：blocknew.cfg 名称解析 + .blk 成分解析。

用途：把用户在通达信客户端手工维护的备选池（如"震荡"）作为公式命中之外的
第二候选来源接入 screening 链。本地文件读取，不依赖 TQ/TdxW 在线。

文件格式（T0002/blocknew/，只读，绝不写入）：
- blocknew.cfg：定长记录序列，板块名（GBK，\0 填充）+ blk 短名（\0 填充），
  如 "震荡" → ZD.blk。解析按非空段成对提取，再校验 blk 文件真实存在。
- *.blk：每行 7 位代码 = 市场位 + 6 位代码（0=SZ, 1=SH, 2=BJ），允许空行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

# TODO #63（v0.225）：纯格式解析下沉 datasource/local_tdx/tdx_block_files.py ——
# 此处只剩薄门面。注意 TDX_BLOCK_DIR 的门面语义：tests/调用方 patch 的是**本
# 模块**的常量 ⇒ resolve_block_file 必须走包装把门面常量传下去（直 re-export
# 会让 patch 打在数据源模块的常量上而失效——别名只引用函数体）。
from custos.core.code_utils import is_ashare_stock, market_of  # noqa: E402
from custos.datasource.local_tdx import tdx_block_files as _tbf  # noqa: E402
from custos.datasource.local_tdx.tdx_block_files import read_blk  # noqa: E402

TDX_BLOCK_DIR = _tbf.TDX_BLOCK_DIR


def resolve_block_file(
    block_name: str, block_dir: Optional[Path] = None
) -> Optional[Path]:
    """门面包装：解析本体在 ``datasource.local_tdx.tdx_block_files``；``block_dir``
    缺省时用**本门面**的 TDX_BLOCK_DIR（保留本模块常量的可 patch 语义）。"""
    return _tbf.resolve_block_file(block_name, block_dir or TDX_BLOCK_DIR)


def _is_bj(code: str, market: str = "") -> bool:
    """兼容包装：显式市场优先，否则委托 ``code_utils.market_of``。"""
    return str(market).strip().upper() == "BJ" if market else market_of(code) == "BJ"


def load_pool(
    block_name: str,
    date: str,
    block_dir: Optional[Path] = None,
    name_map: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """读取一个自选池，输出与公式命中同构的结构。绝不 raise。

    非 A 股标的（ETF/可转债/B股/指数）不进 hits，而是落到 ``excluded``：既不让它们
    冒充可买个股，也不静默丢弃（否则"池里 20 只只出来 3 只"无从解释）。BJ 保留在
    hits 中，由 enrich 的 exclude_bj 开关统一裁决。
    """
    result: dict[str, Any] = {
        "block_name": block_name,
        "hits": [],
        "excluded": [],
        "error": None,
    }
    path = resolve_block_file(block_name, block_dir)
    if path is None:
        result["error"] = f"block_not_found:{block_name}"
        return result
    names = name_map or {}
    for item in read_blk(path):
        code = item["code"]
        market = item["market"]
        qualified = f"{code}.{market}" if market else code
        if not is_ashare_stock(qualified):
            result["excluded"].append(
                {"code": code, "market": market, "reason": "not_a_share"}
            )
            continue
        result["hits"].append(
            {
                "code": code,
                "name": names.get(code, ""),
                "signal_date": date,
                "market": market,
            }
        )
    result["block_file"] = str(path)
    return result
