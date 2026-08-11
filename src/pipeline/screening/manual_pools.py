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

import os
import re
from pathlib import Path
from typing import Any, Optional

TDX_BLOCK_DIR = Path(os.environ.get("TDX_ROOT", r"E:\new_tdx64")) / "T0002" / "blocknew"

_MARKET_PREFIX = {"0": "SZ", "1": "SH", "2": "BJ"}

# 沪深 A 股代码前缀。与 formula_screen._A_SHARE_RE 同一份规则：自选池是用户在通达信
# 客户端手工维护的板块，里面混 ETF(51/15/16xxxx)、可转债(11/12/13xxxx)、B股(900xxx/2xxxxx)
# 是常态。此前 hits 直接透出、enrich 又只排 BJ 前缀 → 这些非股票标的能一路走进 A-D 分层
# 和 StockPool 契约，被当成"可买个股"给出买入计划（审计 B10）。
_A_SHARE_RE = re.compile(r"^(60[0-5]|688|00[0-3]|30[0-3])\d{3}$")


def _is_bj(code: str, market: str = "") -> bool:
    """BJ 单独识别：它有独立的 exclude_bj 开关（enrich 段可配置放开），
    不能被 not_a_share 一把吞掉，否则用户关掉 exclude_bj 也再也拿不到北交所票。"""
    return market == "BJ" or str(code).startswith(("4", "8", "920"))


def resolve_block_file(block_name: str, block_dir: Optional[Path] = None) -> Optional[Path]:
    """板块中文名 → blk 文件路径；找不到返回 None（绝不 raise）。"""
    d = Path(block_dir) if block_dir else TDX_BLOCK_DIR
    cfg = d / "blocknew.cfg"
    try:
        text = cfg.read_bytes().decode("gbk", errors="replace")
    except OSError:
        return None
    # 非空段序列：板块名与 blk 短名交替出现
    segs = [s for s in re.split(r"\x00+", text) if s.strip()]
    for i in range(len(segs) - 1):
        name, blk = segs[i].strip(), segs[i + 1].strip()
        if name == block_name and re.fullmatch(r"[A-Za-z0-9_]+", blk):
            path = d / f"{blk}.blk"
            if path.exists():
                return path
    # 兜底：同名 .blk 直接存在（如用户自建板块未入 cfg）
    direct = d / f"{block_name}.blk"
    return direct if direct.exists() else None


def read_blk(path: Path) -> list[dict[str, str]]:
    """解析 .blk → [{"code": "600150", "market": "SH"}]，跳过空行/脏行。"""
    out: list[dict[str, str]] = []
    try:
        lines = Path(path).read_text(encoding="gbk", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        s = line.strip()
        if len(s) == 7 and s.isdigit() and s[0] in _MARKET_PREFIX:
            out.append({"code": s[1:], "market": _MARKET_PREFIX[s[0]]})
    return out


def load_pool(block_name: str, date: str,
              block_dir: Optional[Path] = None,
              name_map: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """读取一个自选池，输出与公式命中同构的结构。绝不 raise。

    非 A 股标的（ETF/可转债/B股/指数）不进 hits，而是落到 ``excluded``：既不让它们
    冒充可买个股，也不静默丢弃（否则"池里 20 只只出来 3 只"无从解释）。BJ 保留在
    hits 中，由 enrich 的 exclude_bj 开关统一裁决。
    """
    result: dict[str, Any] = {"block_name": block_name, "hits": [], "excluded": [], "error": None}
    path = resolve_block_file(block_name, block_dir)
    if path is None:
        result["error"] = f"block_not_found:{block_name}"
        return result
    names = name_map or {}
    for item in read_blk(path):
        code = item["code"]
        if not (_A_SHARE_RE.match(code) or _is_bj(code, item["market"])):
            result["excluded"].append({"code": code, "market": item["market"],
                                       "reason": "not_a_share"})
            continue
        result["hits"].append({
            "code": code,
            "name": names.get(code, ""),
            "signal_date": date,
            "market": item["market"],
        })
    result["block_file"] = str(path)
    return result
