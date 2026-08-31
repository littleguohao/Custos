# -*- coding: utf-8 -*-
"""market_timing daily input collector v4.

Phase 1 collector (08:50, pre-open):
- auto: local TongDaXin vipdoc daily files for key index trends
- auto: local vipdoc 880-series for market breadth / sentiment / turnover
  (previous trading day's EOD — no intraday data exists at 08:50)
- manual placeholders: macro policy, 0AMV, overseas

The old TDX TQ snapshot path was removed: tqcenter is deprecated and the
TqSession stub raised unconditionally, leaving breadth/sentiment/turnover
permanently missing. Intraday index snapshots are not collected here (none
exist at 08:50); the 14:45 chain's collect_intraday_snapshot backfills
``a_share_indices[*].intraday`` — and this collector applies the same-day
snapshot itself when rerun after 14:45.

Usage:
python market_timing_collector.py --date 2026-07-09 --amv 1.2
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


from custos.datasource.local_tdx import local_tdx_data as ltd  # type: ignore
from custos.core.paths import cn_now, write_json_atomic, MARKET_DIR  # noqa: E402
from custos.core.indicators import pct_change as pct  # noqa: E402
from custos.core.runtime_guards import previous_confirmed_trading_day  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.datasource.breadth_basis import breadth_counts_real  # noqa: E402
from custos.datasource.collect.collect_intraday_snapshot import (  # noqa: E402
    apply_intraday_to_indices,
)

OUT_DIR = MARKET_DIR

INDICES = {
    "上证指数": {"prefix": "sh", "code": "999999"},
    "创业板指": {"prefix": "sz", "code": "399006"},
    "科创50": {"prefix": "sh", "code": "000688"},
    "北证50": {"prefix": "bj", "code": "899050"},
}

# vipdoc 880-series market-wide statistics (same codes as refresh_market_indices.py)
BREADTH_CODE = "880005.SH"  # close=上涨家数
SENTIMENT_CODE = "880006.SH"  # close=涨停数, high=盘中曾涨停数, low=跌停数
TURNOVER_CODE = "880001.SH"  # amount=全市场成交额(元)
# 跌家数口径见 breadth_basis：原先这里有个 TOTAL_STOCKS_APPROX = 5530 硬编码近似总数，
# 用 `总数 - 涨家数` 推算跌家数，把平盘/停牌计入下跌，使 up_down_ratio 系统性偏低。


def to_float(x: Any):
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s in ("", "--", "nan", "None"):
            return None
        v = float(s)
        if not math.isfinite(v):
            # NaN 与 ±inf 都不是有效读数（inf 会被下游阈值当成真实极大值）
            return None
        return v
    except Exception:
        return None


def read_day(prefix: str, code: str) -> list[dict]:
    """Read daily K lines through the unified local_tdx_data layer.

    prefix is kept for backward compatibility with old callers.
    """
    tdx_code = {"sh": f"{code}.SH", "sz": f"{code}.SZ", "bj": f"{code}.BJ"}.get(
        prefix, code
    )
    df = ltd.get_ohlcv_table(tdx_code, count=260, prefer="vipdoc")
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        dt = r.get("date")
        rows.append(
            {
                "date": dt.strftime("%Y%m%d") if hasattr(dt, "strftime") else str(dt),
                "open": to_float(r.get("open")),
                "high": to_float(r.get("high")),
                "low": to_float(r.get("low")),
                "close": to_float(r.get("close")),
                "amount": to_float(r.get("amount")),
                "volume": to_float(r.get("volume")),
            }
        )
    return rows


def trend(rows: list[dict]) -> dict:
    if not rows:
        return {"available": False, "source": "vipdoc_day"}
    rows = sorted(rows, key=lambda r: r["date"])
    latest = rows[-1]
    closes = [r["close"] for r in rows]
    latest_close = closes[-1]

    def close_n(n):
        return closes[-1 - n] if len(closes) > n else None

    ma25 = sum(closes[-25:]) / 25 if len(closes) >= 25 else None
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
    ma144 = sum(closes[-144:]) / 144 if len(closes) >= 144 else None
    ma240 = sum(closes[-240:]) / 240 if len(closes) >= 240 else None
    return {
        "available": True,
        "source": "vipdoc_day",
        "latest_date": latest["date"],
        "latest_close": round(latest_close, 4),
        "change_5d_pct": pct(latest_close, close_n(5)),
        "change_20d_pct": pct(latest_close, close_n(20)),
        "change_60d_pct": pct(latest_close, close_n(60)),
        "ma25": round(ma25, 4) if ma25 else None,
        "ma60": round(ma60, 4) if ma60 else None,
        "ma144": round(ma144, 4) if ma144 else None,
        "ma240": round(ma240, 4) if ma240 else None,
        "above_ma25": bool(latest_close > ma25) if ma25 else None,
        "above_ma60": bool(latest_close > ma60) if ma60 else None,
        "above_ma144": bool(latest_close > ma144) if ma144 else None,
        "above_ma240": bool(latest_close > ma240) if ma240 else None,
    }


def amv_zone(v):
    if v is None:
        return ""
    if v > 4:
        return "做多"
    if v < -2.3:
        return "空头"
    return "中性"


def _vipdoc_rows(code: str, count: int = 5) -> list[dict]:
    """读 vipdoc 880 系列末 N 根——统一走 local_tdx_data 数据层（2026-08-24 解耦）。

    此前本模块自己缓存一个 `mootdx Reader.factory(tdxdir=TDX_ROOT)` 直读，
    绕过 datasource 层（且 global 缓存 reader 是连接卫生盲区）。列语义核对：
    read_vipdoc_daily 对 880xxx.SH 与旧直调是**同一个 mootdx Reader**
    （_is_bj_code 尊重显式后缀，880 系列不会误入 BJ 直读），high/low/close/amount
    同名同单位；差别只是 date 从 DatetimeIndex 变成 Timestamp 列，下面取值已对齐。
    """
    df = ltd.read_vipdoc_daily(code)
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.tail(count).iterrows():
        dt = r.get("date")
        rows.append(
            {
                "date": dt.strftime("%Y-%m-%d")
                if hasattr(dt, "strftime")
                else str(dt)[:10],
                "high": to_float(r.get("high")),
                "low": to_float(r.get("low")),
                "close": to_float(r.get("close")),
                "amount": to_float(r.get("amount")),
            }
        )
    return sorted(rows, key=lambda r: r["date"])


def _freshness(as_of: str, expected: str | None, label: str, quality: dict) -> str:
    """quality=auto when data date matches the expected previous trading day."""
    if expected is not None and as_of == expected:
        return "auto"
    quality["notes"].append(
        f"{label} 最新数据日期 {as_of or '无'} 与预期前一交易日 {expected or '无法确认'} 不一致（周末/假日/vipdoc 未更新或已含更新数据），标记 degraded。"
    )
    return "degraded"


def derive_market_fields(target_date: str) -> tuple[dict, dict, dict, dict]:
    """Fill breadth/sentiment/turnover from local vipdoc 880-series EOD data.

    Honest labeling: at 08:50 the latest available bar is the previous trading
    day's close, so each section carries as_of (actual data date) and is marked
    "auto" when fresh vs the trading calendar, "degraded" when vipdoc lags.
    """
    expected = previous_confirmed_trading_day(target_date)
    quality: dict[str, Any] = {
        "notes": [
            "market_breadth/sentiment/turnover 来自本地 vipdoc 880 系列前一交易日 EOD 数据；08:50 盘前无当日盘中数据。",
            "指数盘中涨跌幅由 14:45 链 collect_intraday_snapshot 回填 a_share_indices[*].intraday。",
        ],
        "sources": ["vipdoc_880_series"],
        "expected_data_date": expected,
    }

    breadth: dict[str, Any] = {
        "up_count": None,
        "down_count": None,
        "flat_count": None,
        "suspended_count": None,
        "up_down_ratio": None,
        "up_down_ratio_status": "unavailable",
        "source": None,
        "quality": "missing",
        "as_of": None,
        # 自算桶数据日（机器可读；as_of 是 880005 官方涨家数的数据日，两者可能不同日）
        "vipdoc_as_of": None,
    }
    try:
        rows = _vipdoc_rows(BREADTH_CODE)
        if rows:
            last = rows[-1]
            up = last["close"]
            as_of = last["date"]
            if up is not None:
                # 880005 只给涨家数;跌/平/停由 vipdoc 本地自算真值口径（v0.137，
                # 见 breadth_basis.compute_breadth_from_vipdoc），自算失败才回落
                # 总数推算/unavailable，**不编造**。
                counts = breadth_counts_real(int(up), date=as_of)
                breadth.update(
                    {
                        "up_count": int(up),
                        "down_count": counts["down_count"],
                        "flat_count": counts["flat_count"],
                        "suspended_count": counts["suspended_count"],
                        "up_down_ratio": counts["up_down_ratio"],
                        "up_down_ratio_status": counts["up_down_ratio_status"],
                        "total_stocks": counts["total_stocks"],
                        "total_stocks_source": counts["total_stocks_source"],
                        "vipdoc_as_of": counts["vipdoc_as_of"],
                        "source": "vipdoc_880005",
                        "as_of": as_of,
                        "quality": _freshness(
                            as_of, expected, "880005 涨跌家数", quality
                        ),
                    }
                )
                quality["notes"].append(f"880005 涨跌家数: {counts['note']}")
    except Exception as e:
        quality["notes"].append(f"880005 涨跌家数读取失败: {e!r}")

    sentiment: dict[str, Any] = {
        "limit_up_count": None,
        "limit_down_count": None,
        "once_limit_up_count": None,
        "once_limit_down_count": None,
        "blowup_rate": None,
        "market_height": None,
        "above_2_board_count": None,
        "source": None,
        "quality": "missing",
        "as_of": None,
    }
    try:
        rows = _vipdoc_rows(SENTIMENT_CODE)
        if rows:
            last = rows[-1]
            limit_up = last["close"]
            once_up = last["high"]
            limit_down = last["low"]
            as_of = last["date"]
            if limit_up is not None:
                sentiment.update(
                    {
                        "limit_up_count": int(limit_up),
                        "once_limit_up_count": int(once_up)
                        if once_up is not None
                        else None,
                        "limit_down_count": int(limit_down)
                        if limit_down is not None
                        else None,
                        "blowup_rate": round((once_up - limit_up) / once_up, 4)
                        if once_up
                        else None,
                        "source": "vipdoc_880006",
                        "as_of": as_of,
                        "quality": _freshness(
                            as_of, expected, "880006 涨跌停", quality
                        ),
                    }
                )
                quality["notes"].append(
                    "连板高度/2板以上家数无法从 880006 获取，market_height/above_2_board_count 留空待人工或盘后填充。"
                )
    except Exception as e:
        quality["notes"].append(f"880006 涨跌停读取失败: {e!r}")

    turnover: dict[str, Any] = {
        "total_turnover": None,
        "turnover_change_pct": None,
        "volume_summary": "",
        "source": None,
        "quality": "missing",
        "as_of": None,
    }
    try:
        rows = _vipdoc_rows(TURNOVER_CODE)
        if rows:
            last = rows[-1]
            amt = last["amount"]
            as_of = last["date"]
            prev_amt = rows[-2]["amount"] if len(rows) >= 2 else None
            if amt is not None:
                turnover.update(
                    {
                        "total_turnover": amt,
                        "turnover_change_pct": pct(amt, prev_amt),
                        "source": "vipdoc_880001",
                        "as_of": as_of,
                        "quality": _freshness(
                            as_of, expected, "880001 成交额", quality
                        ),
                        "volume_summary": f"全市场成交额(元)来自 880001.SH vipdoc，数据日期 {as_of}（前一交易日 EOD）。",
                    }
                )
    except Exception as e:
        quality["notes"].append(f"880001 成交额读取失败: {e!r}")

    return breadth, sentiment, turnover, quality


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=cn_now().strftime("%Y-%m-%d"))
    ap.add_argument("--amv", type=float, default=None, help="0AMV 当日涨跌幅，百分比")
    ap.add_argument(
        "--out",
        default="",
        help="可选输出路径；为空则写入正式 market_timing_input.json",
    )
    args = ap.parse_args()

    breadth, sentiment, turnover, quality = derive_market_fields(args.date)

    data = {
        "date": args.date,
        "collector_version": "market_timing_collector_v4_vipdoc_880",
        "macro_policy": {
            "monetary_policy": "",
            "fiscal_policy": "",
            "credit_environment": "",
            "regulation_environment": "",
            "policy_summary": "",
        },
        "amv_0": {
            "amv_change_pct": args.amv,
            "amv_zone": amv_zone(args.amv),
            # as_of 故意留 None：08:50 手工 --amv 的读数属哪个数据日无法自证（0AMV 是盘后
            # 指标，盘前能看到的最新值其实是 T-1），编一个 as_of 等于给门控一个假的新鲜度。
            # 这里也不置 quality=confirmed，门控按 candidate 处理。唯一会把 amv_0 标
            # confirmed 的是 merge_incremental_market（数据日可证），as_of 由它写。
            "as_of": None,
            "note": "0AMV > 4% = 做多；0AMV < -2.3% = 空头",
        },
        "overseas_market": {
            "nasdaq_change_pct": None,
            "sp500_change_pct": None,
            "sox_change_pct": None,
            "nikkei_change_pct": None,
            "kospi_change_pct": None,
            "hstech_change_pct": None,
            # ⚠️ 与 `amv_0.as_of` 同形：键恒存在、初值 None。
            #    骨架阶段没有任何数据日可证，**不得填采集时刻**（TODO #52）。
            "as_of": None,
            "overseas_summary": "",
        },
        "a_share_indices": {},
        "market_breadth": breadth,
        "sentiment": sentiment,
        "turnover": turnover,
        # theme 节已删：主线口径随 TODO #26 撤下，theme_clarity 恒空串、
        # 全仓零读者（main_themes 无读者），scorer 的 theme 打分腿同步删除。
        "data_quality": quality,
    }

    for name, meta in INDICES.items():
        item = trend(read_day(meta["prefix"], meta["code"]))
        item["intraday"] = {
            "available": False,
            # 08:50 盘前采集时盘中未发生，available=False 是正常状态而非缺数；
            # 14:45 链 collect_intraday_snapshot 落盘后由它回填真实盘中涨跌幅。
            "note": "盘前采集无盘中数据；14:45 链 collect_intraday_snapshot 回填。",
        }
        data["a_share_indices"][name] = item

    # collector 在当日 14:45 后重跑（--refresh-market / 文件缺失重建）时，
    # 同日快照已存在 ⇒ 直接回填真实盘中值，不把已接通的数据退回占位。
    snapshot_path = MARKET_DIR / f"{args.date}_intraday_snapshot.json"
    if snapshot_path.exists():
        try:
            snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
            n = apply_intraday_to_indices(data["a_share_indices"], snap)
            if n:
                print(f"[OK] 同日盘中快照回填 a_share_indices.intraday（{n} 只指数）")
        except (OSError, ValueError) as e:
            print(f"[WARN] 盘中快照读取失败，intraday 保持占位：{e!r}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = (
        Path(args.out)
        if args.out
        else OUT_DIR / f"{args.date}_market_timing_input.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    # ⚠️ 落盘前校验：这是全项目扇出最大的产物（19 个消费者，12 个读 amv_0）。
    # 它是渐进填充文档，契约只管结构 —— 见 contracts.py。
    require("market_timing_input", data)
    # 与其他写方（merge/amv_state/refresh/…）同一份读-改-写共享文件 ⇒ 原子写
    write_json_atomic(out, data)
    print(out)


if __name__ == "__main__":
    main()
