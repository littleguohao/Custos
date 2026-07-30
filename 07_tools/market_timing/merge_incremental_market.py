# -*- coding: utf-8 -*-
"""Merge incremental market data into market_timing_input.json and auto-confirm 0AMV quality.

Extracted from the post-close runner (former steps 4-5) so the post-close
"market_timing_input finalization" logic becomes a reusable pipeline stage.
Prints the same [OK]/[WARN] lines the in-process code used to print.
Missing input files are a silent no-op (exit 0), matching the original
behavior.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import BASE  # noqa: E402


def section_quality(as_of: str, target: str) -> str:
    """新鲜度判定:数据日 != 目标日 ⇒ **stale**,不能按 auto(满分)记。

    以前无条件写 quality="auto",于是 TdxW 没刷新时上一交易日的宽度/成交额会
    冒充当日数据、quality_score 照样 pass,评分器用 T-1 涨跌比给分而报告毫无提示。
    """
    if not as_of:
        return "raw_only"
    return "auto" if str(as_of)[:10] == str(target)[:10] else "stale"


def merge_incremental(inc: dict, mkt: dict, target: str) -> tuple[dict, list[str]]:
    """把增量采集结果并入 market_timing_input(纯函数,便于测试)。

    返回 (合并后的 mkt, stale 项列表)。沿用 setdefault 的"只增不毁"语义。
    """
    stale: list[str] = []

    def _q(as_of: str, field: str) -> str:
        q = section_quality(as_of, target)
        if q != "auto":
            stale.append(f"{field}({as_of or 'no_as_of'})")
        return q

    breadth = inc.get("breadth", {})
    if "880005" in breadth:
        b = breadth["880005"]
        mkt.setdefault("market_breadth", {
            "quality": _q(b.get("date", ""), "market_breadth"),
            "as_of": b.get("date", ""),
            "up_count": b.get("up_count"),
            "down_count": b.get("down_count"),
            "source": "mootdx_reader_880005",
        })
    if "880006" in breadth:
        b6 = breadth["880006"]
        mkt.setdefault("sentiment", {
            "quality": _q(b6.get("date", ""), "sentiment"),
            "as_of": b6.get("date", ""),
            "limit_up": b6.get("close"),
            "source": "mootdx_reader_880006",
        })
    # Turnover from 880001 amount (全市场成交额; close 是平均股价指数点位,不是成交额)
    if "880001" in breadth:
        b1 = breadth["880001"]
        amt = b1.get("amount")
        prev_amt = b1.get("previous_amount")
        chg_pct = round((amt / prev_amt - 1) * 100, 3) if amt and prev_amt else None
        if amt:
            q1 = _q(b1.get("date", ""), "turnover")
            mkt.setdefault("turnover", {
                "total_turnover": amt,
                "turnover_change_pct": chg_pct,
                "quality": q1,
                "as_of": b1.get("date", ""),
                "source": "vipdoc_880001_amount",
            })
            mkt.setdefault("market_turnover", {
                "quality": q1,
                "as_of": b1.get("date", ""),
                "value": amt,
                "source": "vipdoc_880001_amount",
            })
    # Overseas from incremental (只增不毁: 不覆盖已有非空值,也不写入 None)
    if "a50_futures" in inc:
        v = inc["a50_futures"].get("change_pct")
        if v is not None:
            mkt.setdefault("overseas_market", {}).setdefault("a50_change_pct", v)
    if "cnh_usd" in inc:
        v = inc["cnh_usd"].get("change_pct")
        if v is not None:
            mkt.setdefault("overseas_market", {}).setdefault("cnh_change_pct", v)
    # Northbound
    if "northbound" in inc:
        mkt["northbound"] = inc["northbound"]
    return mkt, stale


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Merge incremental market data into market_timing_input.json")
    ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)
    target = args.date

    incremental_path = BASE / "01_data" / "market" / f"{target}_incremental_market.json"
    market_path = BASE / "01_data" / "market" / f"{target}_market_timing_input.json"

    # 1. Merge incremental data into market_timing_input.json
    if incremental_path.exists() and market_path.exists():
        try:
            inc = json.loads(incremental_path.read_text(encoding="utf-8"))
            mkt = json.loads(market_path.read_text(encoding="utf-8"))
            mkt, stale = merge_incremental(inc, mkt, target)
            market_path.write_text(json.dumps(mkt, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[OK] incremental data merged into market_timing_input.json")
            if stale:
                print(f"[WARN] 以下指标数据日非目标日 {target},已标记 stale(不得当作当日数据): {', '.join(stale)}")
        except Exception as e:
            print(f"[WARN] merge incremental failed: {e}")

    # 2. Auto-fix 0AMV quality if amv_0day is set but quality missing.
    #    amv_0day 缺失时回退到人工观测台账(用户 15:15 告知的值由 LLM 写入 0amv_observations.jsonl)
    if market_path.exists():
        mkt = json.loads(market_path.read_text(encoding="utf-8"))
        amv = mkt.get("amv_0", {})
        amv_day = mkt.get("amv_0day")
        amv_source = "amv_0day"
        if amv_day is None:
            ledger_path = BASE / "01_data" / "market" / "0amv_observations.jsonl"
            if ledger_path.exists():
                for line in ledger_path.read_text(encoding="utf-8").splitlines():
                    try:
                        obs = json.loads(line)
                    except ValueError:
                        continue
                    if (obs.get("date") == target and obs.get("quality") == "confirmed"
                            and obs.get("amv_change_pct") is not None):
                        amv_day = obs["amv_change_pct"]  # 同日多条时取最后出现的(最新)
                        amv_source = "0amv_observations"
        if amv_day is not None and amv.get("quality") != "confirmed":
            amv["amv_change_pct"] = amv_day
            amv["quality"] = "confirmed"
            if not amv.get("effective_state"):
                amv["effective_state"] = amv.get("amv_zone") or ("空头" if amv_day < -2.3 else "做多" if amv_day > 4 else "中性")
            mkt["amv_0"] = amv
            market_path.write_text(json.dumps(mkt, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] 0AMV quality auto-set to confirmed (value={amv_day}%, regime={amv['effective_state']}, source={amv_source})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
