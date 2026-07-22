# -*- coding: utf-8 -*-
"""个股概念/主题标签源（TQ download_file down_type=4 → miscinfo.json）。

背景：sector_code_map v1 的 880 板块成员反查映射存在明显错配（一只股属于
多个 880 板块，首个命中主题即中标）。miscinfo.json 直接给出每只股票的
官方概念/主题标签（id=10001），是更准确的板块归属数据源
（TQ_INTERFACE_PROBE_2026-07-20 ★3，评级高）。

用法：
    uv run python 07_tools/local_tdx/concept_tags.py --date YYYY-MM-DD

安全约束（探测报告 §四教训）：
- 只调用 down_type=4（实测安全）；禁止触碰 1/5/6（可打挂 TQ 服务）。
- 单次调用、30s 客户端超时、不重试；任何失败结构化返回，绝不 raise。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SELF = Path(__file__).resolve()
TOOLS_DIR = SELF.parents[1]
for p in (str(TOOLS_DIR), str(SELF.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from paths import BASE  # noqa: E402

SECTORS_DIR = BASE / "01_data" / "sectors"
OUT_PATH = SECTORS_DIR / "stock_concept_tags.json"
CONCEPT_ID = "10001"  # 概念和主题
TDX_DATA_DIR = Path(os.environ.get("TDX_ROOT", r"E:\new_tdx64")) / "PYPlugins" / "data"


def parse_miscinfo(path: Path) -> dict[str, list[str]]:
    """解析 miscinfo.json → {code6: [概念标签...]}（仅 id=10001）。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    tags: dict[str, list[str]] = {}
    for item in raw if isinstance(raw, list) else []:
        if str(item.get("id")) != CONCEPT_ID:
            continue
        code = str(item.get("code") or "").strip()
        if not (code.isdigit() and len(code) == 6):
            continue
        concepts = [t.strip() for t in str(item.get("xq") or "").split(",") if t.strip()]
        if concepts:
            tags[code] = concepts
    return tags


def refresh(date: str, call_fn=None) -> dict[str, Any]:
    """触发 TQ 下载并解析落盘。call_fn 可注入以便测试；绝不 raise。"""
    result: dict[str, Any] = {"date": date, "status": "ok", "output": str(OUT_PATH)}
    if call_fn is None:
        import tq_http
        call_fn = tq_http.call
    r = call_fn("download_file", {"down_type": 4}, timeout=30)
    if not r.get("ok"):
        result.update({"status": "unavailable",
                       "degraded_reason": f"tq_download_failed:{(r.get('error') or {}).get('code', 'unknown')}"})
        return result
    src = TDX_DATA_DIR / "miscinfo.json"
    if not src.exists():
        result.update({"status": "unavailable", "degraded_reason": f"miscinfo_missing:{src}"})
        return result
    try:
        tags = parse_miscinfo(src)
    except (OSError, ValueError) as exc:
        result.update({"status": "unavailable", "degraded_reason": f"parse_failed:{exc}"})
        return result
    payload = {
        "date": date,
        "refreshed_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(src),
        "stock_count": len(tags),
        "tags": tags,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result["stock_count"] = len(tags)
    return result


def load_tags() -> dict[str, list[str]]:
    """读取已落盘的概念标签（{code6: [tags]}）；缺失返回 {}。"""
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    tags = data.get("tags")
    return tags if isinstance(tags, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    r = refresh(args.date)
    print(json.dumps({k: v for k, v in r.items() if k != "tags"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
