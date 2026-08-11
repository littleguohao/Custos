# -*- coding: utf-8 -*-
"""个股概念/主题标签源（TQ download_file down_type=4 → miscinfo.json）。

背景：sector_code_map v1 的 880 板块成员反查映射存在明显错配（一只股属于
多个 880 板块，首个命中主题即中标）。miscinfo.json 直接给出每只股票的
官方概念/主题标签（id=10001），是更准确的板块归属数据源
（TDX_LOCAL_INTERFACES.md「探过但没接」down_type=3，评级高）。

用法：
    uv run python src/datasource/local_tdx/concept_tags.py --date YYYY-MM-DD

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
for p in (str(TOOLS_DIR), str(TOOLS_DIR.parent / "core"), str(SELF.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from paths import BASE, cn_now, SECTORS_DIR  # noqa: E402

OUT_PATH = SECTORS_DIR / "stock_concept_tags.json"
CONCEPT_ID = "10001"  # 概念和主题
TDX_DATA_DIR = Path(os.environ.get("TDX_ROOT", r"E:\new_tdx64")) / "PYPlugins" / "data"


def parse_miscinfo(path: Path) -> dict[str, list[str]]:
    """解析 miscinfo.json → {code6: [概念标签...]}（仅 id=10001）。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    tags: dict[str, list[str]] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):  # 脏行（str/null/数字）跳过，不炸解析
            continue
        if str(item.get("id")) != CONCEPT_ID:
            continue
        code = str(item.get("code") or "").strip()
        if not (code.isdigit() and len(code) == 6):
            continue
        concepts = [t.strip() for t in str(item.get("xq") or "").split(",") if t.strip()]
        if concepts:
            tags[code] = concepts
    return tags


def _mtime(path: Path) -> float | None:
    """文件落盘时刻（秒）；不存在返回 None。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _mtime_day(ts: float) -> str:
    """mtime → 交易所时区的日期串（不能用宿主时区：UTC 主机上会差一天）。"""
    return datetime.fromtimestamp(ts, tz=cn_now().tzinfo).date().isoformat()


def refresh(date: str, call_fn=None) -> dict[str, Any]:
    """触发 TQ 下载并解析落盘。call_fn 可注入以便测试；绝不 raise。

    新鲜度判定（只看 TQ 返回 ok 是不够的）：
    ``download_file`` 返回 ok 只代表**请求被接受**，TQ 是异步落盘的；若 TdxW 卡住、
    下载失败或磁盘上是上周的残留，miscinfo.json 根本没被重写。原实现照样解析、
    照样盖上今天的 ``date`` 落盘 —— 18:00 主线指纹于是拿一周前的概念标签当当日
    板块族密度算，脏数据完全看不出来。
    故比对**调用前后的 mtime**：文件被重写、或 mtime 本就是目标日（当日已下过、
    TQ 不重复写盘）才算新鲜；否则 ``status="stale"``，且落盘的 ``date`` 用
    **文件自己的日期**而非请求日期，绝不给过期标签盖今日戳。
    """
    result: dict[str, Any] = {"date": date, "status": "ok", "output": str(OUT_PATH)}
    if call_fn is None:
        import tq_http
        call_fn = tq_http.call
    src = TDX_DATA_DIR / "miscinfo.json"
    mtime_before = _mtime(src)                    # 调用前的落盘时刻
    r = call_fn("download_file", {"down_type": 4}, timeout=30)
    if not r.get("ok"):
        result.update({"status": "unavailable",
                       "degraded_reason": f"tq_download_failed:{(r.get('error') or {}).get('code', 'unknown')}"})
        return result
    mtime_after = _mtime(src)
    if mtime_after is None:
        result.update({"status": "unavailable", "degraded_reason": f"miscinfo_missing:{src}"})
        return result
    try:
        tags = parse_miscinfo(src)
    except (OSError, ValueError) as exc:
        result.update({"status": "unavailable", "degraded_reason": f"parse_failed:{exc}"})
        return result
    src_day = _mtime_day(mtime_after)
    rewritten = mtime_before is None or mtime_after > mtime_before
    stale = not (rewritten or src_day >= str(date)[:10])
    payload = {
        # 陈旧时用文件自己的日期：下游看到的 date 必须是数据真正的日期
        "date": src_day if stale else date,
        "refreshed_at": cn_now().isoformat(timespec="seconds"),
        "source": str(src),
        "source_mtime": datetime.fromtimestamp(mtime_after,
                                               tz=cn_now().tzinfo).isoformat(timespec="seconds"),
        "stock_count": len(tags),
        "tags": tags,
    }
    if stale:
        payload["stale"] = True
        payload["requested_date"] = date
        result.update({
            "status": "stale",
            "degraded_reason": f"miscinfo_stale:mtime_day={src_day} 未随本次调用更新"
                               f"(请求 {date})，标签可能是上一交易日/上周的",
            "source_date": src_day,
            "requested_date": date,
        })
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result["stock_count"] = len(tags)
    return result


def load_tags() -> dict[str, list[str]]:
    """读取已落盘的概念标签（{code6: [tags]}）；缺失返回 {}。

    只给标签、不给新鲜度。需要判断标签是否陈旧的调用方请用 :func:`load_tags_meta`
    —— refresh() 会在 TQ 未真正重写 miscinfo 时落 ``stale: true``,若消费方不读它,
    过期概念标签照样进主线指纹(审计 C6 的传导链终点)。
    """
    return load_tags_meta()[0]


def load_tags_meta() -> tuple[dict[str, list[str]], dict[str, Any]]:
    """Return (tags, meta) where meta carries date / stale / requested_date.

    meta 缺失时给 ``{"available": False}``,便于调用方区分"没有标签文件"与
    "有标签但是旧的"。
    """
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {"available": False, "reason": "tags_file_missing"}
    tags = data.get("tags")
    if not isinstance(tags, dict):
        return {}, {"available": False, "reason": "tags_malformed"}
    meta = {
        "available": True,
        "date": data.get("date"),
        "stale": bool(data.get("stale")),
        "requested_date": data.get("requested_date"),
        "source_mtime": data.get("source_mtime"),
    }
    return tags, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    r = refresh(args.date)
    print(json.dumps({k: v for k, v in r.items() if k != "tags"}, ensure_ascii=False))
    # 降级必须让调用方（run_1800 的 stage log）看得见：只打印 JSON 的话，
    # best-effort 的 stage 会一律记 ok，脏标签就此静默进入主线指纹。
    return 0 if r.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
