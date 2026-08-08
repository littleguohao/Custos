#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给无指纹的 m2_sweep 旧结果补上样本量指纹（按实际笔数推断）。

背景：第一版 `m2_stop_sweep.py` 的结果文件名是 `{组}__{方案}.json`，**不含样本量**。
owner 先跑 300 样本、再跑 1000 样本时，300 的旧文件被 `[SKIP]` 复用，汇总表把
~400 笔与 ~1300 笔混在一起比，A 组一半方案的判定作废。

现在文件名要求 `{组}__{方案}__s{样本量}.json`。这个脚本按每个文件里的实际笔数
反推它属于哪一批，重命名后就能被新版脚本正确分批汇总——省掉已完成方案的重跑时间。

**默认只打印推断结果，不动文件**；核对无误后加 `--apply`。

用法：
    uv run python 07_tools/research/m2_migrate_fingerprint.py            # 预览
    uv run python 07_tools/research/m2_migrate_fingerprint.py --apply    # 执行
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parents[2]
OUTDIR = BASE / "06_logs" / "m2_sweep"

# 笔数 → 样本量的推断区间。信号数大致与样本股票数成正比（实测 1000 样本 ≈ 1300 笔）。
# 择时方案（--amv-long-only）会把笔数砍到约 18%，所以要按「是否含 amv」分别定档。
BANDS_PLAIN = [(900, 99999, 1000), (250, 900, 300), (80, 250, 100)]
BANDS_AMV = [(150, 99999, 1000), (45, 150, 300), (10, 45, 100)]


def _n_of(p: pathlib.Path) -> int | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                     # noqa: BLE001
        print(f"[WARN] 读不了 {p.name}: {e}")
        return None
    for k in ("trade_summary", "trade_sim", "summary"):
        blk = d.get(k)
        if isinstance(blk, dict) and blk.get("n"):
            return int(blk["n"])
    if d.get("n"):
        return int(d["n"])
    # 纯组合级结果：用成交+被限反推
    pf = d.get("portfolio") or {}
    tk, sk = pf.get("n_taken"), pf.get("n_skipped")
    if tk is not None and sk is not None:
        return int(tk) + int(sk)
    return None


def _infer(name: str, n: int) -> int | None:
    bands = BANDS_AMV if "amv" in name else BANDS_PLAIN
    for lo, hi, sample in bands:
        if lo <= n < hi:
            return sample
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="为 m2_sweep 旧结果补样本量指纹")
    ap.add_argument("--apply", action="store_true", help="真正重命名（默认只预览）")
    ap.add_argument("--only-sample", type=int, default=0,
                    help="只迁移推断为该样本量的文件（如 1000）")
    a = ap.parse_args()

    if not OUTDIR.exists():
        print(f"目录不存在: {OUTDIR}")
        return 1
    legacy = [p for p in sorted(OUTDIR.glob("*__*.json"))
              if not re.search(r"__s\d+(_cw)?\.json$", p.name)]
    if not legacy:
        print("没有需要迁移的旧文件（都已带指纹）")
        return 0

    print(f"发现 {len(legacy)} 个无指纹文件：\n")
    print(f"{'文件':<40}{'笔数':>7}{'推断样本':>10}  新文件名")
    print("-" * 96)
    plan: list[tuple[pathlib.Path, pathlib.Path]] = []
    unknown = []
    for p in legacy:
        n = _n_of(p)
        if n is None:
            unknown.append(p)
            print(f"{p.name:<40}{'?':>7}{'—':>10}  （读不出笔数，跳过）")
            continue
        stem = p.name[:-5]
        cw = stem.startswith("cw_")
        if cw:
            stem = stem[3:]
        name = stem.split("__", 1)[1] if "__" in stem else stem
        s = _infer(name, n)
        if s is None:
            unknown.append(p)
            print(f"{p.name:<40}{n:>7}{'—':>10}  （笔数不在已知档位，跳过）")
            continue
        if a.only_sample and s != a.only_sample:
            continue
        new = OUTDIR / f"{stem}__s{s}{'_cw' if cw else ''}.json"
        plan.append((p, new))
        print(f"{p.name:<40}{n:>7}{s:>10}  {new.name}")

    if unknown:
        print(f"\n⚠️ {len(unknown)} 个文件无法推断，需手工处理或删除后重跑")
    if not plan:
        print("\n没有可迁移的文件")
        return 0

    by_sample: dict[int, int] = {}
    for _, new in plan:
        m = re.search(r"__s(\d+)", new.name)
        if m:
            by_sample[int(m.group(1))] = by_sample.get(int(m.group(1))) or 0
    counts: dict[int, int] = {}
    for _, new in plan:
        m = re.search(r"__s(\d+)", new.name)
        if m:
            counts[int(m.group(1))] = counts.get(int(m.group(1)), 0) + 1
    print(f"\n按样本量分组：{dict(sorted(counts.items()))}")

    if not a.apply:
        print("\n（预览模式，未改动文件。核对无误后加 --apply）")
        return 0

    done = 0
    for old, new in plan:
        if new.exists():
            print(f"[SKIP] {new.name} 已存在")
            continue
        old.rename(new)
        done += 1
    print(f"\n已重命名 {done} 个文件。现在跑："
          f"\n  uv run python 07_tools/research/m2_stop_sweep.py --sample 1000"
          f"\n缺失的方案会自动补跑，已完成的会 [SKIP]。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
