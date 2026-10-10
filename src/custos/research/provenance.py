# -*- coding: utf-8 -*-
"""研究产物溯源块（owner 方法论 review #8，v0.325）。

产物此前只记 objective_version，没有溯源——复现一份报告靠的是人记。
本模块统一产出 ``provenance`` 块，各研究终端的报告组装处调
``provenance.build(args, unit=..., criteria_version=...)`` 一次：

- ``git_sha`` / ``git_dirty``：产物出自哪份代码（dirty=True 警示「报告
  不是出自干净提交」——判读权重打折）；
- ``objective_version``：strategy_grid 单源（续跑守卫 v0.320 可直接建在
  本块上）；
- ``criteria_version``：判据版本串（各单元预注册定稿版本，由终端传入）；
- ``pre_reg_blob_hash``：预注册文档的 git blob hash（#11 前置——日后
  「判据节变了但产物引用旧 hash ⇒ 必须写修订注记」的机械核对原料）；
- ``universe_sha256``：宇宙文件（--codes-file）内容 hash——「同宇宙」
  不再是口头对齐；
- ``data_last_date``：加载数据的最后日期（数据新鲜度自证）；
- ``cmdline``：完整命令行（R13 可复现的落点）。

铁律：**溯源失败绝不炸研究**——git 不可用/文件缺失 ⇒ 对应字段 None +
stderr WARN（旁路元数据，同影子台账隔离语义）。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from custos.core.paths import BASE  # noqa: E402
from custos.research import strategy_grid as sg  # noqa: E402


def _git(args: list[str]) -> Optional[str]:
    """跑一条 git 子命令；失败（无 git/非仓库）⇒ None + WARN（不炸）。"""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=BASE,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] provenance git 不可用: {type(exc).__name__}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(
            f"[WARN] provenance git {' '.join(args)} 失败: {out.stderr.strip()[:120]}",
            file=sys.stderr,
        )
        return None
    return out.stdout.strip()


def git_sha() -> Optional[str]:
    """当前 HEAD 短 sha（溯源块用；git 不可用 ⇒ None）。"""
    return _git(["rev-parse", "--short=10", "HEAD"])


def git_dirty() -> Optional[bool]:
    """工作区是否有未提交改动（含未跟踪；git 不可用 ⇒ None）。"""
    out = _git(["status", "--porcelain"])
    return None if out is None else bool(out)


def git_blob_hash(path: Path) -> Optional[str]:
    """文件当前内容的 git blob hash（`git hash-object`——与历史无关，
    内容寻址；文件缺失 ⇒ None）。"""
    path = Path(path)
    if not path.is_file():
        return None
    return _git(["hash-object", str(path)])


def file_sha256(path: Path, *, head: int = 16) -> Optional[str]:
    """文件内容 sha256 前 N 位（宇宙文件指纹；缺失/目录 ⇒ None）。"""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:head]
    except OSError as exc:
        print(f"[WARN] provenance sha256 失败 {path}: {exc}", file=sys.stderr)
        return None


def last_date_of(per_code: dict[str, dict]) -> Optional[str]:
    """per_code 全宇宙的最后 bar 日期（df 尾行；占位/缺 df 跳过不炸）。"""
    last: Optional[str] = None
    for pack in per_code.values():
        df = pack.get("df") if isinstance(pack, dict) else None
        if df is None or not hasattr(df, "iloc") or not len(df):
            continue
        try:
            d = str(df["date"].iloc[-1])[:10]
        except Exception:  # noqa: BLE001 — 单列缺失跳该票
            continue
        if last is None or d > last:
            last = d
    return last


def build(
    args: Any,
    *,
    unit: str,
    criteria_version: Optional[str] = None,
    pre_reg_doc: Optional[Path] = None,
    data_last_date: Optional[str] = None,
) -> dict[str, Any]:
    """组装 provenance 块（字段缺失一律 None，不炸研究主链）。

    ``args.cmdline``：main 入口在 parse 后塞的完整命令行（run_study 直调
    的测试无此属性 ⇒ None）。``args.codes_file``：宇宙文件（缺省/无文件
    ⇒ universe_sha256 None）。
    """
    return {
        "git_sha": git_sha(),
        "git_dirty": git_dirty(),
        "objective_version": sg.OBJECTIVE_VERSION,
        "unit": str(unit),
        "criteria_version": criteria_version,
        "pre_reg_blob_hash": (
            git_blob_hash(pre_reg_doc) if pre_reg_doc is not None else None
        ),
        "universe_sha256": (
            file_sha256(getattr(args, "codes_file", "") or "")
            if getattr(args, "codes_file", "")
            else None
        ),
        "data_last_date": data_last_date,
        "cmdline": getattr(args, "cmdline", None),
    }


# ---------------------------------------------------------------------------
# CLI（诊断：当前仓库溯源信息）
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """打印当前仓库的溯源信息（git sha/dirty——诊断「报告出自哪份代码」用）。"""
    print(
        f"provenance: git_sha={git_sha()} git_dirty={git_dirty()} "
        f"objective_version={sg.OBJECTIVE_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
