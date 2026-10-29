# -*- coding: utf-8 -*-
"""清理逐笔重放备忘落盘（v0.465）：删非当前出场源码版本的目录 + 当前版本 jsonl 去重 / 剔坏行。

随时可整目录删除（只是下次重算）；本脚本是保留当前版本的温和清理。

    uv run python scripts/dev/prune_replay_cache.py [--dry-run] [--root DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from custos.research import factor_exit_study as fes

    ap = argparse.ArgumentParser(description="清理重放备忘落盘缓存")
    ap.add_argument(
        "--root",
        default="",
        help="缺省 = CUSTOS_REPLAY_CACHE 或 artifacts/cache/replay_memo",
    )
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    out = fes.prune_replay_cache(Path(a.root) if a.root else None, dry_run=a.dry_run)
    print(
        f"[prune] 根 {out['root']}；当前版本 {fes.replay_code_version()}；"
        f"删旧版本 {len(out['removed_versions'])} 个 {out['removed_versions']}；"
        f"{out['bytes_before'] / 1e6:.0f}MB → {out['bytes_after'] / 1e6:.0f}MB"
        + ("（dry-run，未改动）" if a.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
