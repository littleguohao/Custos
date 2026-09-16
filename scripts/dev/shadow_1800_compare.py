#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""1800 候选表标注口径的影子对照工具（TODO #76① 的收集机制）。

用法（**生产机**每日 1800 跑完后执行一次）::

    bash scripts/dev/shadow_1800_compare.sh --date 2026-09-16

干什么：

1. 取当日**现行版**产出（主仓 `artifacts/reports/daily/{date}/{date}_1800_candidate_table.md`
   ——每日 1800 已跑完，本脚本不重跑现行版）；
2. `git worktree add` 一个钉死在 **#67 迁移前 commit（b35e97b，立项设计稿
   ccad92f8^）**的旁路目录（幂等：已存在且钉对 commit 就复用）；
3. 旁路的 `data/` **软链共享**主仓 data/（vipdoc 只读、当日输入 JSON 共享——
   两版代码同机同时跑同一批输入）；⚠️ **旁路只跑 1800 计算段**
   （formula_screen → enrich_candidates → score_candidates → candidate_table
   四个脚本），**不跑 08:50 采集段、不跑 refresh 段**（它们会写共享缓存）；
4. 从两版候选表里抽出**信号标注相关部分**（🏷️ 信号标注一览段 + A/B/C/D 池
   明细表的「标注」列逐票单元格），逐位对比，差异报告落
   `artifacts/logs/factor67_shadow/{date}.log`；**发现不一致 → 非零退出**
   （fail-closed 提醒）。

共享数据的安全网：旁路的 score_candidates 会把 `data/stock_pool/{date}_stock_pool.json`
在共享 data/ 里重写成旧口径——脚本跑前**快照**该文件、跑后**恢复**
（当日真产出不受影子污染；每日 1800 次日照常重生成）。

幂等：每日可跑；worktree 复用（钉 commit 不符 → 报错退出，不静默换基线）。
本机无通达信数据无法真跑——本文件的抽取/对比逻辑由
tests/test_shadow_1800_compare.py 用合成候选表钉住。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# #67 迁移前钉点（立项设计稿 ccad92f8 的父提交 = 迁移前最后一个 commit）
PIN_COMMIT = "b35e97bb1239ea26832a5a6be3f62d63042c7e27"
# 1800 计算段四脚本（只跑这些；refresh/08:50 采集段一律不跑——见 docstring）
CHAIN = (
    "formula_screen.py",
    "enrich_candidates.py",
    "score_candidates.py",
    "candidate_table.py",
)
LABEL_OVERVIEW_HEAD = "## 🏷️ 信号标注一览"


def extract_label_sections(md: str) -> dict:
    """从候选表 markdown 抽「信号标注」相关部分（纯函数，测试钉住）。

    返回 ``{"overview": 🏷️ 段全文（&nbsp; 归一）, "pool": {code: 标注单元格}}``——
    池明细表的列序：... | 标注 | 分层 | 建议止损位 | next_step |（标注=倒数第 4 列）。
    """
    overview = ""
    if LABEL_OVERVIEW_HEAD in md:
        seg = md.split(LABEL_OVERVIEW_HEAD, 1)[1]
        seg = re.split(r"\n## ", seg, maxsplit=1)[0]
        # 段标题同行可能带括注（真表为「## 🏷️ 信号标注一览（研究因子·只标注…）」）
        # —— 比较的是内容不是标题，首行（标题残余）剥掉
        seg = seg.split("\n", 1)[1] if "\n" in seg else ""
        overview = seg.replace("&nbsp;", " ").strip()
    pool: dict[str, str] = {}
    for ln in md.splitlines():
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        # 池数据行：首列是 6 位数字代码、列数 ≥ 8（表头/分隔行自然滤掉）
        if len(cells) < 8 or not re.fullmatch(r"\d{6}", cells[0]):
            continue
        pool[cells[0]] = cells[-4].replace("&nbsp;", " ")
    return {"overview": overview, "pool": pool}


def compare_tables(md_new: str, md_old: str) -> list[str]:
    """两版候选表的标注差异行（空 list = 逐位一致）。"""
    a, b = extract_label_sections(md_new), extract_label_sections(md_old)
    diffs: list[str] = []
    if a["overview"] != b["overview"]:
        diffs.append("## 🏷️ 信号标注一览 段不一致（见上下文 diff）")
    codes = sorted(set(a["pool"]) | set(b["pool"]))
    for code in codes:
        ca, cb = a["pool"].get(code), b["pool"].get(code)
        if ca != cb:
            diffs.append(f"标注列不一致 {code}: 现行={ca!r} vs 迁移前={cb!r}")
    if a["pool"].keys() != b["pool"].keys():
        missing_a = sorted(set(b["pool"]) - set(a["pool"]))
        missing_b = sorted(set(a["pool"]) - set(b["pool"]))
        if missing_a:
            diffs.append(f"现行缺票（迁移前有）: {','.join(missing_a[:10])}")
        if missing_b:
            diffs.append(f"迁移前缺票（现行有）: {','.join(missing_b[:10])}")
    return diffs


def _run(cmd: list[str], cwd: Path, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--date",
        default=_dt.date.today().isoformat(),
        help="交易日 YYYY-MM-DD（默认今天）；须是当日 1800 已跑完的日期",
    )
    ap.add_argument(
        "--repo",
        default=str(Path(__file__).resolve().parents[2]),
        help="主仓根（默认取脚本所在仓）",
    )
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    date = args.date
    log_dir = repo / "artifacts" / "logs" / "factor67_shadow"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{date}.log"
    lines: list[str] = [
        f"# 1800 候选表标注口径影子对照｜{date}",
        f"# 现行版 = 工作区 HEAD；迁移前钉点 = {PIN_COMMIT[:8]}（#67 立项设计稿父提交）",
        "",
    ]

    def bail(msg: str, rc: int = 2) -> int:
        lines.append(f"❌ {msg}")
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines[-3:]))
        return rc

    # ① 现行版产出（当日 1800 已跑完）
    main_table = (
        repo
        / "artifacts"
        / "reports"
        / "daily"
        / date
        / f"{date}_1800_candidate_table.md"
    )
    if not main_table.is_file():
        return bail(f"现行候选表不存在：{main_table}（先跑当日 1800）")
    pool_json = repo / "data" / "stock_pool" / f"{date}_stock_pool.json"
    snapshot = log_dir / f"_stock_pool_snapshot__{date}.json"
    if pool_json.is_file():
        shutil.copy2(pool_json, snapshot)

    # ② 旁路 worktree（幂等复用；钉 commit 不符 → 报错）
    wt = log_dir / "worktree_pre67"
    if wt.is_dir():
        head = _run(["git", "rev-parse", "HEAD"], cwd=wt)
        if head.stdout.strip() != PIN_COMMIT:
            return bail(
                f"旁路目录 {wt} 钉在 {head.stdout.strip()[:8]}，与钉点不符——请手工处理后重跑"
            )
    else:
        r = _run(["git", "worktree", "add", "--detach", str(wt), PIN_COMMIT], cwd=repo)
        if r.returncode != 0:
            return bail(f"git worktree add 失败: {r.stderr.strip()[:200]}")

    # ③ data/ 软链共享（输入共享；artifacts 不链——旁路产出落在旁路本地）
    wt_data = wt / "data"
    if not wt_data.is_symlink():
        if wt_data.exists():
            shutil.rmtree(wt_data)
        os.symlink(repo / "data", wt_data)

    # ④ 旁路跑 1800 计算段（只跑四脚本；refresh/08:50 采集段不跑）
    for script in CHAIN:
        r = _run(
            [
                "uv",
                "run",
                "python",
                f"src/custos/pipeline/screening/{script}",
                "--date",
                date,
            ],
            cwd=wt,
        )
        if r.returncode != 0:
            # 恢复共享 stock_pool 后再退（旁路已污染共享 data/）
            if snapshot.is_file():
                shutil.copy2(snapshot, pool_json)
            return bail(
                f"旁路 {script} 失败（{r.returncode}）: {r.stderr.strip()[-300:]}"
            )

    # ⑤ 恢复共享 stock_pool（旁路重写过它）
    if snapshot.is_file():
        shutil.copy2(snapshot, pool_json)

    # ⑥ 对比标注段
    old_table = (
        wt
        / "artifacts"
        / "reports"
        / "daily"
        / date
        / f"{date}_1800_candidate_table.md"
    )
    if not old_table.is_file():
        return bail(f"旁路候选表未产出：{old_table}（检查旁路计算段日志）")
    diffs = compare_tables(
        main_table.read_text(encoding="utf-8"), old_table.read_text(encoding="utf-8")
    )
    if not diffs:
        lines.append("✅ 逐位一致：🏷️ 信号标注一览段 + A/B/C/D 池「标注」列全部相同")
        lines.append(
            f"对照池票数：{len(extract_label_sections(main_table.read_text(encoding='utf-8'))['pool'])}"
        )
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0
    lines.append(
        f"⚠️ 发现 {len(diffs)} 处不一致（fail-closed——请人工研判是否预期内口径差）："
    )
    lines += [f"  - {d}" for d in diffs[:200]]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:20]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
