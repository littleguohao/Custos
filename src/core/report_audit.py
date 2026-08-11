# -*- coding: utf-8 -*-
"""报告可审计块（待办 #29）—— `report_id` / 策略版本 / 数据截止 / 输入清单。

原 MASTER_WORKFLOW §十二 第 8 条要求所有正式报告带这四个字段，2026-08-06
核查时全仓零实现；研究侧 R13 查出的「历史批次不可复现」是同一类问题：
出问题时无法定位当时用的**哪版规则、哪天的数据**。

本模块是四个报告生成器共用的唯一实现（盘前 `daily_report`、14:45
`review_core`、盘后 `final_close_review`、选股 `score_candidates` +
`candidate_table`），各生成器只负责喂**自己实际读过的输入文件清单**。

设计口径（从简，不发明复杂方案）：

- `report_id`：`{date}_{session}_{sha1前8位}`，哈希输入为
  日期 + session + 策略版本 + 各输入文件的路径与内容哈希 ——
  **同一天同一份输入重跑得到同一个 id**，输入变了 id 跟着变。
- 策略版本：`CHANGELOG.md` 表格里**最后一个**
  `vX.Y`（该文件是版本变更的唯一登记处，解析它就是机器可读来源）；
  读不到返回 `未知`，不阻断报告。
- 数据截止：输入文件 mtime 里**最晚**的一个（本地时区 ISO 秒）。
- 输入清单：项目相对路径 + 内容 sha1 前 8 位；文件缺失留 `缺失` 标记
  （缺失本身是事实，报告不该因此不产出）。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from paths import BASE

VERSION_LOG = BASE / "CHANGELOG.md"
_VERSION_RE = re.compile(r"\|\s*(v\d+(?:\.\d+)?)\s*\|")


def strategy_version(log_path: Path | None = None) -> str:
    """最新策略版本号：版本日志表格里最后一个 `vX.Y`；读不到返回 `未知`。"""
    try:
        text = (log_path or VERSION_LOG).read_text(encoding="utf-8")
    except OSError:
        return "未知"
    versions = _VERSION_RE.findall(text)
    return versions[-1] if versions else "未知"


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(BASE.resolve()).as_posix()
    except ValueError:
        return str(path)


def _input_entry(path: Path) -> dict:
    try:
        raw = path.read_bytes()
        mtime = path.stat().st_mtime
    except OSError:
        return {"path": _rel(path), "sha1": None, "mtime": None}
    return {
        "path": _rel(path),
        "sha1": hashlib.sha1(raw).hexdigest()[:8],
        "mtime": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
    }


def build(date: str, session: str, inputs: Iterable[Path]) -> dict:
    """组装可审计块。`inputs` 是生成器**实际读过**的输入文件（可含缺失文件）。"""
    entries = [_input_entry(Path(p)) for p in inputs]
    version = strategy_version()
    fingerprint = "|".join(f"{e['path']}:{e['sha1'] or '-'}" for e in entries)
    short = hashlib.sha1(f"{date}|{session}|{version}|{fingerprint}".encode()).hexdigest()[:8]
    mtimes = [e["mtime"] for e in entries if e["mtime"]]
    return {
        "report_id": f"{date}_{session}_{short}",
        "strategy_version": version,
        "data_as_of": max(mtimes) if mtimes else None,
        "inputs": entries,
    }


def render_md(audit: dict) -> list[str]:
    """MD 报告头部的两行引用块（紧跟「生成时间」一行）。"""
    items = "；".join(f"`{e['path']}`（{e['sha1'] or '缺失'}）" for e in audit["inputs"])
    return [
        f"> 可审计：report_id `{audit['report_id']}`｜策略版本 {audit['strategy_version']}"
        f"｜数据截止 {audit['data_as_of'] or '未知'}",
        f"> 输入清单（{len(audit['inputs'])} 项，括号为内容 sha1 前 8 位）：{items or '无'}",
    ]
