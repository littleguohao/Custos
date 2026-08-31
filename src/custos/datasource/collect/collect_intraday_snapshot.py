# -*- coding: utf-8 -*-
"""14:45 盘中市场快照采集（TQ-Local HTTP get_market_snapshot）。

抓取 999999.SH / 880005.SH / 880006.SH / 880001.SH 四个指数的实时快照，
写入 ``data/market/{date}_intraday_snapshot.json`` 并打印一行 JSON 摘要；
随后把盘中涨跌幅回填进 ``{date}_market_timing_input.json`` 的
``a_share_indices[*].intraday``（见 ``merge_into_market_timing_input``）——
market_timing_scorer 的盘中腿与 final_close_review 的盘中优先分支都吃该字段。

best-effort 语义：TdxW 未运行或任一指数失败都不会 raise、exit 恒为 0；
失败体现在 error / quality 字段（quality=unavailable），绝不让 run_1445 挂。

CLI::

    uv run python src/custos/datasource/collect/collect_intraday_snapshot.py --date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import MARKET_DIR, write_json_atomic  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.core.indicators import pct_change  # noqa: E402
from custos.datasource.local_tdx import tq_http  # noqa: E402

SOURCE = "tq_http_snapshot"

# 快照指数 → market_timing_input.a_share_indices 键名。
# 快照四只里只有 999999.SH 与 a_share_indices 重叠（880005/880006/880001
# 是宽度/涨跌停/成交额统计，不属于指数段）；其余三只指数无盘中来源，
# intraday 保留 collector 写的 available=False 占位，如实缺测。
SNAPSHOT_TO_MARKET_INDEX = {"上证指数": "999999.SH"}

# 指数 → (角色, 字段映射)。字段含义见 tq-tq-local SKILL 文档与实测：
# 999999.SH 上证指数；880005.SH Now=上涨家数；
# 880006.SH Now=涨停数/Max=曾涨停/Min=跌停；880001.SH Amount=成交额(万元)
INDEX_SPECS: dict[str, dict[str, Any]] = {
    "999999.SH": {
        "role": "sh_index",
        "fields": {
            "Now": "now",
            "LastClose": "last_close",
            "UpHome": "up_home",
            "DownHome": "down_home",
            "Amount": "amount",
        },
    },
    "880005.SH": {"role": "advance_count", "fields": {"Now": "up_count"}},
    "880006.SH": {
        "role": "limit_stats",
        "fields": {"Now": "limit_up", "Max": "ever_limit_up", "Min": "limit_down"},
    },
    "880001.SH": {"role": "turnover", "fields": {"Amount": "amount_wan"}},
}


def _num(value: Any) -> Any:
    """快照数值为字符串；可解析则转 float，否则保留原值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def collect() -> dict:
    """采集全部指数快照，任何失败结构化返回，绝不 raise。"""
    as_of = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    indices: dict[str, Any] = {}
    ok_count = 0
    for code, spec in INDEX_SPECS.items():
        resp = tq_http.snapshot(code)
        if not resp["ok"]:
            indices[code] = {"role": spec["role"], "ok": False, "error": resp["error"]}
            continue
        raw = resp["value"] if isinstance(resp["value"], dict) else {}
        entry = {"role": spec["role"], "ok": True}
        for src_key, dst_key in spec["fields"].items():
            entry[dst_key] = _num(raw.get(src_key))
        indices[code] = entry
        ok_count += 1

    total = len(INDEX_SPECS)
    if ok_count == total:
        quality = "ok"
    elif ok_count > 0:
        quality = "partial"
    else:
        quality = "unavailable"
    result: dict[str, Any] = {
        "as_of": as_of,
        "source": SOURCE,
        "quality": quality,
        "indices_ok": ok_count,
        "indices_total": total,
        "indices": indices,
        "error": None,
    }
    if quality == "unavailable":
        first_err = next((v["error"] for v in indices.values() if not v["ok"]), None)
        result["error"] = first_err or {"code": "snapshot_failed"}
    return result


def apply_intraday_to_indices(indices: dict, snapshot: dict) -> int:
    """把快照里的盘中涨跌幅就地填进 a_share_indices 的 intraday 段，返回填了几只。

    快照失败/数值不可解析的指数保持原状（collector 的 available=False 占位），
    如实缺测，不编造 0。"""
    merged = 0
    for name, code in SNAPSHOT_TO_MARKET_INDEX.items():
        entry = (snapshot.get("indices") or {}).get(code) or {}
        now, last_close = entry.get("now"), entry.get("last_close")
        # 快照数值是字符串转 float 后的结果；转不动的（如 "-"）不算可用盘中值
        if not entry.get("ok") or not isinstance(now, (int, float)):
            continue
        chg = pct_change(now, last_close, digits=2)  # 判定路径统一 round-2（TODO #56）
        item = indices.setdefault(name, {"available": False, "source": SOURCE})
        item["intraday"] = {
            "available": True,
            "now": now,
            "last_close": last_close if isinstance(last_close, (int, float)) else None,
            "intraday_change_pct": chg,
            "as_of": snapshot.get("as_of"),
            "source": SOURCE,
        }
        merged += 1
    return merged


def merge_into_market_timing_input(date: str, snapshot: dict) -> bool:
    """把盘中快照回填进 {date}_market_timing_input.json 的 intraday 段。

    消费端：market_timing_scorer._score_one_index 的盘中腿（此前恒 None 恒 0 分）、
    final_close_review.index_rows 的「Prefer intraday change」分支（此前恒走
    daily_change_pct 兜底）。返回是否写盘；文件缺失或快照全不可用是正常路径
    （08:50 collector 没跑成 / TdxW 未运行），如实跳过不编造。"""
    market_path = MARKET_DIR / f"{date}_market_timing_input.json"
    if not market_path.exists():
        print(f"[SKIP] {market_path.name} 不存在，盘中快照无处回填")
        return False
    mkt = json.loads(market_path.read_text(encoding="utf-8"))
    indices = mkt.get("a_share_indices") or {}
    merged = apply_intraday_to_indices(indices, snapshot)
    if not merged:
        print("[SKIP] 快照无可用指数，market_timing_input 不回填")
        return False
    mkt["a_share_indices"] = indices
    # ⚠️ 落盘前校验：本步只动 a_share_indices（only 的责任范围语义见 contracts._narrow）；
    # require 失败抛 SystemExit，由 main 统一按 best-effort 降级为 WARN。
    require("market_timing_input", mkt, only=("a_share_indices",))
    write_json_atomic(market_path, mkt)  # 与其他 7 个写方读-改-写同一文件 ⇒ 原子写
    print(f"[OK] 盘中快照已回填 market_timing_input（{merged} 只指数）")
    return True


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="14:45 盘中市场快照采集（TQ-Local HTTP，best-effort）"
    )
    parser.add_argument(
        "--date", required=True, help="采集日期 YYYY-MM-DD，用于输出文件命名"
    )
    args = parser.parse_args(argv)

    result = collect()
    result["date"] = args.date

    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MARKET_DIR / f"{args.date}_intraday_snapshot.json"
    require("intraday_snapshot", result)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "date": args.date,
        "quality": result["quality"],
        "indices_ok": result["indices_ok"],
        "output": str(out_path),
    }
    if result["error"]:
        summary["error"] = result["error"]

    # 快照落盘后回填 market_timing_input（接通 14:45 盘中腿）。
    # best-effort 同快照本体：回填失败（含 require 抛 SystemExit）只留 WARN，
    # 绝不让已落盘的快照跟着陪葬。
    try:
        summary["merged"] = merge_into_market_timing_input(args.date, result)
    except SystemExit as e:
        print(
            f"[WARN] 盘中快照回填 market_timing_input 契约不过：{e}",
            file=sys.stderr,
        )
        summary["merged"] = False
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 盘中快照回填 market_timing_input 失败：{e!r}", file=sys.stderr)
        summary["merged"] = False

    print(json.dumps(summary, ensure_ascii=False))
    return 0  # best-effort：失败也 exit 0，错误体现在 quality/error 字段


if __name__ == "__main__":
    raise SystemExit(main())
