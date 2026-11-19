# -*- coding: utf-8 -*-
"""#131 一次性迁移：v0.547 之前补过仓的持仓，止损改为摊薄成本 × 0.93（owner 2026-11-18 定 B）。

默认只预览（列出每只持仓的旧止损 → 新止损），不写盘；``--apply`` 才写，写前把原文件复制为
``position_plans.json.bak_restop_YYYYMMDD``。只在生产机跑（计划文件在那里）。幂等：已迁移的不再变。

    uv run python scripts/dev/restop_averaged_plans.py            # 预览
    uv run python scripts/dev/restop_averaged_plans.py --apply    # 写盘
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Optional

from custos.core.trades import position_plans as pp


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plans", default=str(pp.PLANS_FILE))
    ap.add_argument("--apply", action="store_true", help="写盘（默认只预览）")
    args = ap.parse_args(argv)
    path = Path(args.plans)
    if not path.exists():
        print(f"[FAIL] 计划文件不存在：{path}", file=sys.stderr)
        return 2
    plans = pp.load_plans(path)
    changes = pp.restop_averaged(plans)
    if not changes:
        print("[OK] 无需迁移（没有补过仓且止损仍按首次成本的持仓）")
        return 0
    print("| 代码 | 摊薄成本 | 旧止损 | 新止损 |\n|---|---:|---:|---:|")
    for c in changes:
        old = "-" if c["old_stop"] is None else f"{c['old_stop']:.3f}"
        print(f"| {c['code']} | {c['entry_price']:.3f} | {old} | {c['new_stop']:.3f} |")
    if not args.apply:
        print(f"[PREVIEW] {len(changes)} 只待迁移；确认后加 --apply 写盘")
        return 0
    bak = path.with_name(
        path.name + ".bak_restop_" + pp.cn_now().date().strftime("%Y%m%d")
    )
    shutil.copy2(path, bak)
    pp.save_plans(plans, path)
    print(f"[OK] 已迁移 {len(changes)} 只；原文件备份 {bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
