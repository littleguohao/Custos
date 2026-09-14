# -*- coding: utf-8 -*-
"""通达信安装目录**本地文件**解析器（纯格式解析，无 pipeline 语义）——TODO #63。

两个消费方此前各自在 pipeline 层直读 TDX 安装目录文件：
`pipeline/holdings/holding_sector_mapper.py`（tdxhy.cfg / incon.dat）与
`pipeline/screening/manual_pools.py`（blocknew.cfg / *.blk）。2026-08-24 数据层
解耦只收敛了 mootdx 直调，本地文件解析属灰色地带 —— 按 TODO #63 下沉到
datasource：本模块只做**字节/文本格式解析**，不含任何选股/持仓语义；
消费方留薄门面（import + 适配，行为逐位不变）。

文件格式（只读，绝不写入）：

- ``TDX_ROOT/T0002/hq_cache/tdxhy.cfg`` —— ``1|688114|T0403|||X270302``（ASCII；
  市场 0=SZ, 1=SH, 2=BJ；T-code 通达信行业码、X-code 申万行业码）。
- ``TDX_ROOT/incon.dat`` —— GBK 名称表，``#TDXNHY``（T-code→名）/
  ``#TDXRSHY``（X-code→申万名）等 ``#SECTION`` 段。
- ``TDX_ROOT/T0002/blocknew/blocknew.cfg`` —— 定长记录序列：板块名（GBK，
  \\0 填充）+ blk 短名（\\0 填充）交替出现。
- ``TDX_ROOT/T0002/blocknew/*.blk`` —— 每行 7 位代码 = 市场位 + 6 位代码
  （0=SZ, 1=SH, 2=BJ），允许空行。

⚠️ 行为冻结：以下函数从两处 pipeline 逐字下沉，输出/异常/脏数据口径逐位一致
（等价性钉测：tests/test_tdx_block_files.py —— 小样例文件对拍 + 异常路径）。

已知刻意的同文件变体（不动）：`fetch_sector_index_history.load_tdxhy_tcodes`
只取第 3 字段（T-code）、strip 且文件缺失返回 {} —— 与 ``load_tdxhy`` 的
双字段/原样/抛错口径不同，是记录在案的同源变体，不合并。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from custos.core.paths import TDX_ROOT

HQ_CACHE = TDX_ROOT / "T0002" / "hq_cache"
TDXHY_CFG = HQ_CACHE / "tdxhy.cfg"
INCON_DAT = TDX_ROOT / "incon.dat"
TDX_BLOCK_DIR = TDX_ROOT / "T0002" / "blocknew"

_MARKET_PREFIX = {"0": "SZ", "1": "SH", "2": "BJ"}


def load_tdxhy(path: Path = TDXHY_CFG) -> dict:
    """Parse tdxhy.cfg -> {code: {"tdx": T-code, "sw": X-code}}。"""
    mapping = {}
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 3 and parts[1].isdigit():
            mapping[parts[1]] = {
                "tdx": parts[2] or "",
                "sw": parts[5] if len(parts) > 5 else "",
            }
    return mapping


def load_incon_sections(path: Path = INCON_DAT) -> dict:
    """Parse incon.dat -> {section: {code: name}}（GBK，``#SECTION`` 块）。"""
    text = path.read_text(encoding="gbk", errors="replace")
    sections: dict[str, dict[str, str]] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "######":
            continue
        if line.startswith("#"):
            current = line[1:]
            sections.setdefault(current, {})
            continue
        if current and "|" in line:
            code, _, name = line.partition("|")
            if name:
                sections[current][code] = name
    return sections


def lookup_name(tree: dict, code: str) -> str:
    """Resolve an industry code against a name tree, trimming to parent."""
    code = (code or "").strip()
    while code:
        if code in tree:
            return tree[code]
        code = code[:-2]
    return ""


def resolve_block_file(
    block_name: str, block_dir: Optional[Path] = None
) -> Optional[Path]:
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
