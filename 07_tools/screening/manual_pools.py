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
    """读取一个自选池，输出与公式命中同构的结构。绝不 raise。"""
    result: dict[str, Any] = {"block_name": block_name, "hits": [], "error": None}
    path = resolve_block_file(block_name, block_dir)
    if path is None:
        result["error"] = f"block_not_found:{block_name}"
        return result
    names = name_map or {}
    for item in read_blk(path):
        result["hits"].append({
            "code": item["code"],
            "name": names.get(item["code"], ""),
            "signal_date": date,
            "market": item["market"],
        })
    result["block_file"] = str(path)
    return result
