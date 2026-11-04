"""08:50 持仓公告确定性采集：Wind 固定工具、CSV 解析、失败即停。

输出是供独立 LLM 研判使用的 cache 材料，不直接进入交易权限。完整公告由插件
保存在 artifacts/cache/kimiwork/；组合材料只带正文开头，避免每次把长公告全文
重复注入模型。任何一只查询失败均不紧密重试，其余持仓标记 skipped。
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from custos.core.paths import BASE, DATA, KIMI_WORK_CACHE, cn_now
from custos.core.runtime_guards import previous_confirmed_trading_day
from custos.datasource.kimiwork_data import KimiWorkDataError, call_api, csv_table

CST = timezone(timedelta(hours=8))
POSITIONS = DATA / "trades" / "current_positions.json"
CONTENT_LIMIT = 2000


def announcement_window(day: str) -> tuple[datetime, datetime]:
    previous = previous_confirmed_trading_day(day)
    if not previous:
        raise RuntimeError(f"无法确定 {day} 的上一交易日")
    start_day = date_from_iso(previous)
    end_day = date_from_iso(day)
    return (
        datetime(start_day.year, start_day.month, start_day.day, 15, 0, tzinfo=CST),
        datetime(end_day.year, end_day.month, end_day.day, 9, 0, tzinfo=CST),
    )


def date_from_iso(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def _query_text(name: str, code: str, start: datetime, end: datetime) -> str:
    """Wind 自然语言参数不含空白；时间范围显式写出，不依赖“近期”。"""
    fmt = "%Y年%m月%d日%H:%M"
    return (
        f"查询{name}（{code}）{start.strftime(fmt)}至{end.strftime(fmt)}的上市公司公告"
    )


def announcement_records(
    raw: dict[str, Any],
    *,
    content_limit: int = CONTENT_LIMIT,
    cache_path: Path | None = None,
):
    """按表头解析；完整 CSV 由本脚本落盘，不依赖插件附件实际持久化。"""
    header, rows = csv_table(raw)
    required = {"content", "date", "title", "url"}
    if not required.issubset(header):
        raise KimiWorkDataError(f"公告响应缺少表头: {sorted(required - set(header))}")
    files = raw.get("files") or []
    # 与 csv_table 同一选择口径：第一个**非空**附件（files[0] 可能为空壳）。
    full = next(
        (
            f
            for f in files
            if isinstance(f, dict) and str(f.get("content") or "").strip()
        ),
        None,
    )
    if cache_path is None:
        cache_file = str(full.get("name") or "") if full else ""
    else:
        if full is None:
            raise KimiWorkDataError("公告成功响应缺少完整 CSV 内容")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temporary.write_bytes(str(full["content"]).encode("utf-8"))
        temporary.replace(cache_path)
        cache_file = str(cache_path)
    records = []
    for row in rows:
        item = dict(zip(header, row))
        content = item["content"]
        records.append(
            {
                "published_at": item["date"],
                "title": item["title"],
                "content_brief": content[:content_limit],
                "content_truncated": len(content) > content_limit,
                "content_chars": len(content),
                "url": item["url"],
                "doc_type": item.get("doc_type", ""),
                "full_content_cache": cache_file,
            }
        )
    return records


def collect(
    day: str,
    *,
    positions_path: Path = POSITIONS,
    output_path: Path | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    date_from_iso(day)
    start, end = announcement_window(day)
    positions = json.loads(positions_path.read_text(encoding="utf-8"))
    if not isinstance(positions, list):
        raise TypeError("current_positions.json 顶层必须是数组")
    holdings = [
        {
            "code": str(item.get("代码") or "").strip(),
            "name": str(item.get("名称") or "").strip(),
        }
        for item in positions
        if isinstance(item, dict) and item.get("代码") and item.get("名称")
    ]
    output = (
        output_path
        or BASE / "artifacts" / "cache" / "premarket" / f"{day}_wind_announcements.json"
    )
    document: dict[str, Any] = {
        "date": day,
        "fetched_at": cn_now().isoformat(timespec="seconds"),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "source": "wind.wind_get_company_announcements",
        "status": "completed",
        "holdings": [],
        "announcements": [],
        "errors": [],
    }
    stopped = False
    for index, holding in enumerate(holdings):
        code, name = holding["code"], holding["name"]
        if stopped:
            document["holdings"].append(
                {**holding, "status": "skipped", "announcement_count": 0}
            )
            continue
        cache_file = KIMI_WORK_CACHE / "wind_announcements" / f"{day}_{code}.csv"
        try:
            raw = call_api(
                "wind",
                "wind_get_company_announcements",
                {
                    "query": _query_text(name, code, start, end),
                    "top_k": top_k,
                    "file_path": str(cache_file),
                },
            )
            records = announcement_records(raw, cache_path=cache_file)
        except KimiWorkDataError as exc:
            stopped = True
            message = f"{type(exc).__name__}: {exc}"
            document["errors"].append({**holding, "error": message})
            document["holdings"].append(
                {
                    **holding,
                    "status": "failed",
                    "announcement_count": 0,
                    "error": message,
                }
            )
            document["status"] = "degraded"
            document["remaining_skipped"] = len(holdings) - index - 1
            continue
        document["holdings"].append(
            {**holding, "status": "completed", "announcement_count": len(records)}
        )
        document["announcements"].extend({**holding, **record} for record in records)

    document["remaining_skipped"] = sum(
        item["status"] == "skipped" for item in document["holdings"]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    return {
        "date": day,
        "status": document["status"],
        "holdings": len(holdings),
        "announcements": len(document["announcements"]),
        "errors": len(document["errors"]),
        "remaining_skipped": document["remaining_skipped"],
        "output": str(output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)
    result = collect(args.date, top_k=args.top_k)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
