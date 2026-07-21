# -*- coding: utf-8 -*-
"""Screening 链第 2 段：命中股充实 + 模式识别（enrich_candidates）。

对公式初筛命中股（去重后通常几十只）用本地日线（vipdoc，mootdx Reader）
计算确定性指标并打模式标签；每个标签对应的实际数值一并落盘，可复盘。

指标与标签（全部为确定性规则）：
- BBI=(MA3+MA6+MA12+MA24)/4，bbi_above：收盘价 >= BBI。
- 日 J（KDJ 9,3,3），j_low：J < 13。
- 量比=当日量/前5日均量；20日量分位=当日量在近20日量中的百分位。
  volume_contraction：量比 <= 50% 且 20日量分位 <= 10%。
- 20日相对强度=个股20日涨幅 - 上证指数(999999)20日涨幅（百分点）。
  relative_strength_strong：相对强度 >= +3pp。
- reversal_k_candidate：j_low + volume_contraction + 涨跌幅∈[-2%,+2%]
  + 振幅<=7%，四项同时满足。

硬排除：名称含 ST、停牌（无当日K线）、上市不足 min_list_days 天、
risk_decision 高优先级股、北交所（exclude_bj）。已持仓股打 is_holding
标记但不剔除。

CLI::

    uv run python 07_tools/screening/enrich_candidates.py --date YYYY-MM-DD

输出 ``01_data/screening/{date}_candidates_enriched.json``。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
for p in (TOOLS_DIR, TOOLS_DIR / "local_tdx", TOOLS_DIR / "market_timing"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from paths import DATA, RISK_DIR, SECTORS_DIR, TRADES_DIR  # noqa: E402
import local_tdx_data  # noqa: E402
from technical_monitor import bbi_state, kdj  # noqa: E402

SCREENING_DIR = DATA / "screening"
SECTOR_CODE_MAP = SECTORS_DIR / "sector_code_map.json"
INDEX_CODE = "999999"  # 上证指数 vipdoc 代码（reader.daily 里 000001 是平安银行）

J_LOW_THRESHOLD = 13.0
VOL_RATIO_MAX = 0.5          # 量比 <= 50%
VOL_PCTILE_MAX = 10.0        # 20日量分位 <= 10%
RS_STRONG_PP = 3.0           # 20日相对强度 >= +3pp
REVERSAL_CHANGE_PCT = 2.0
REVERSAL_AMPLITUDE_PCT = 7.0
STOP_LOOKBACK = 10           # 建议止损位：近10日最低价


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_hits(date: str) -> dict:
    return _load_json(SCREENING_DIR / f"{date}_formula_hits.json", {})


def load_risk_high_codes(date: str) -> set[str]:
    data = _load_json(RISK_DIR / f"{date}_risk_decision.json", {})
    out = set()
    for x in (data.get("stock_risks") or []):
        if str(x.get("priority", "")) == "高" and x.get("code"):
            out.add(str(x["code"]).split(".")[0].zfill(6))
    return out


def load_holding_codes() -> set[str]:
    data = _load_json(TRADES_DIR / "current_positions.json", [])
    out = set()
    for x in data if isinstance(data, list) else []:
        code = str(x.get("代码", "") or "").split(".")[0]
        if code.isdigit():
            out.add(code.zfill(6))
    return out


def latest_tq_sector_map() -> dict:
    """加载最新的 01_data/sectors/*_tq_sector_map.json（880板块→成分股）。"""
    files = sorted(glob.glob(str(SECTORS_DIR / "*_tq_sector_map.json")))
    if not files:
        return {}
    return _load_json(Path(files[-1]), {})


def build_stock_theme_map() -> tuple[dict[str, dict], bool]:
    """股 → 主题方向（theme_id/sector 名）。

    用最新 tq_sector_map 的成分股关系反查 880 板块代码，再对照
    sector_code_map.json 的 primary/candidate_sector_codes 归并到主题。
    返回 ({code6: {"theme_id","sector","matched_code"}}, map_available)。
    """
    sector_map = latest_tq_sector_map()
    code_map = _load_json(SECTOR_CODE_MAP, {})
    themes = code_map.get("themes") or []
    if not sector_map.get("sectors") or not themes:
        return {}, False

    # 880 板块代码 → 主题（primary 优先于 candidate，按注册顺序取先命中者）
    code_to_theme: dict[str, dict] = {}
    for t in themes:
        theme = {"theme_id": t.get("theme_id", ""), "sector": t.get("theme_name", "")}
        for c in t.get("candidate_sector_codes") or []:
            code_to_theme.setdefault(str(c).upper(), theme)
    for t in themes:
        theme = {"theme_id": t.get("theme_id", ""), "sector": t.get("theme_name", "")}
        for c in t.get("primary_sector_codes") or []:
            code_to_theme[str(c).upper()] = theme

    stock_theme: dict[str, dict] = {}
    for s in sector_map["sectors"]:
        theme = code_to_theme.get(str(s.get("code", "")).upper())
        if not theme:
            continue
        for raw in s.get("stocks") or []:
            code6 = str(raw).split(".")[0].zfill(6)
            stock_theme.setdefault(code6, {**theme, "matched_code": s.get("code", "")})
    return stock_theme, True


def _pct_change(df, n: int) -> Optional[float]:
    if len(df) < n + 1:
        return None
    prev = float(df["close"].iloc[-n - 1])
    now = float(df["close"].iloc[-1])
    if prev == 0:
        return None
    return (now / prev - 1) * 100


def compute_metrics(df, index_df) -> dict[str, Any]:
    """对单股日线 DataFrame 计算全部指标与模式标签（确定性）。"""
    close = df["close"]
    bbi = bbi_state(df)
    j = kdj(df)
    last = df.iloc[-1]
    prev_close = float(close.iloc[-2]) if len(df) >= 2 else None

    vol = df["volume"].astype(float)
    vol_today = float(vol.iloc[-1])
    vol_ma5_prev = float(vol.iloc[-6:-1].mean()) if len(df) >= 6 else None
    vol_ratio = (vol_today / vol_ma5_prev) if vol_ma5_prev else None
    vol20 = vol.tail(20)
    vol_pctile = float((vol20 < vol_today).mean() * 100) if len(vol20) >= 20 else None

    change_pct = ((float(last["close"]) / prev_close - 1) * 100) if prev_close else None
    amplitude_pct = (
        (float(last["high"]) / prev_close - float(last["low"]) / prev_close) * 100
        if prev_close else None
    )

    stock_ret20 = _pct_change(df, 20)
    index_ret20 = _pct_change(index_df, 20) if index_df is not None and not index_df.empty else None
    rs_20d = (stock_ret20 - index_ret20) if (stock_ret20 is not None and index_ret20 is not None) else None

    stop_ref = None
    if len(df) >= STOP_LOOKBACK:
        stop_ref = round(float(df["low"].tail(STOP_LOOKBACK).min()), 4)

    daily_j = j.get("j") if j.get("available") else None
    j_low = daily_j is not None and daily_j < J_LOW_THRESHOLD
    vol_contraction = (
        vol_ratio is not None and vol_ratio <= VOL_RATIO_MAX
        and vol_pctile is not None and vol_pctile <= VOL_PCTILE_MAX
    )
    reversal_k = bool(
        j_low and vol_contraction
        and change_pct is not None and abs(change_pct) <= REVERSAL_CHANGE_PCT
        and amplitude_pct is not None and amplitude_pct <= REVERSAL_AMPLITUDE_PCT
    )
    rs_strong = rs_20d is not None and rs_20d >= RS_STRONG_PP

    return {
        "close": round(float(last["close"]), 4),
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "amplitude_pct": round(amplitude_pct, 2) if amplitude_pct is not None else None,
        "bbi": bbi.get("value") if bbi.get("available") else None,
        "bbi_distance_pct": bbi.get("distance_pct") if bbi.get("available") else None,
        "daily_j": daily_j,
        "vol_ratio_vs_ma5": round(vol_ratio, 4) if vol_ratio is not None else None,
        "vol_pctile_20d": round(vol_pctile, 1) if vol_pctile is not None else None,
        "stock_ret_20d_pct": round(stock_ret20, 2) if stock_ret20 is not None else None,
        "index_ret_20d_pct": round(index_ret20, 2) if index_ret20 is not None else None,
        "relative_strength_20d_pp": round(rs_20d, 2) if rs_20d is not None else None,
        "stop_loss_ref": {"price": stop_ref, "basis": f"近{STOP_LOOKBACK}日最低价"} if stop_ref else None,
        "patterns": {
            "bbi_above": bool(bbi.get("available") and bbi.get("close_above")),
            "j_low": bool(j_low),
            "volume_contraction": bool(vol_contraction),
            "reversal_k_candidate": reversal_k,
            "relative_strength_strong": bool(rs_strong),
        },
    }


def enrich(
    date: str,
    hits_data: Optional[dict] = None,
    ohlcv_loader=None,
    index_loader=None,
    universe_cfg: Optional[dict] = None,
) -> dict:
    """充实命中股。loader 可注入以便测试；所有失败结构化落盘，绝不 raise。"""
    hits_data = hits_data if hits_data is not None else load_hits(date)
    cfg = universe_cfg or {}
    min_list_days = int(cfg.get("min_list_days", 60))

    result: dict[str, Any] = {
        "date": date,
        "status": "ok",
        "degraded_reason": "",
        "candidates": [],
        "excluded": [],
    }

    if not hits_data or hits_data.get("status") == "unavailable":
        result["status"] = "unavailable"
        result["degraded_reason"] = (
            f"formula_hits_unavailable:{(hits_data or {}).get('degraded_reason', 'missing')}"
        )
        return result

    # 去重合并：code → {name, formula_ids}
    merged: dict[str, dict] = {}
    for f in hits_data.get("formulas", []):
        for h in f.get("hits", []):
            code6 = str(h.get("code", "")).split(".")[0].zfill(6)
            if not (code6.isdigit() and len(code6) == 6):
                continue
            entry = merged.setdefault(code6, {"code": code6, "name": h.get("name", ""), "formula_hits": []})
            if not entry["name"] and h.get("name"):
                entry["name"] = h["name"]
            if f.get("id") and f["id"] not in entry["formula_hits"]:
                entry["formula_hits"].append(f["id"])

    risk_high = load_risk_high_codes(date)
    holding = load_holding_codes()
    stock_theme, theme_map_available = build_stock_theme_map()
    if not theme_map_available:
        result["status"] = "partial"
        result["degraded_reason"] = "sector_map_unavailable"

    load_ohlcv = ohlcv_loader or (lambda c: local_tdx_data.get_ohlcv_table(c, count=260))
    load_index = index_loader or (lambda: local_tdx_data.get_ohlcv_table(INDEX_CODE, count=260))
    try:
        index_df = load_index()
    except Exception:  # noqa: BLE001
        index_df = None

    for code6 in sorted(merged):
        item = merged[code6]
        name = item["name"]

        def exclude(reason: str) -> None:
            result["excluded"].append({"code": code6, "name": name, "reason": reason})

        if cfg.get("exclude_bj", True) and code6.startswith(("4", "8", "920")):
            exclude("exclude_bj")
            continue
        if cfg.get("exclude_st", True) and "ST" in name.upper():
            exclude("st_stock")
            continue
        if code6 in risk_high:
            exclude("risk_high_priority")
            continue

        try:
            df = load_ohlcv(code6)
        except Exception:  # noqa: BLE001
            df = None
        if df is None or df.empty:
            exclude("no_local_kline")
            continue
        df = df.sort_values("date").reset_index(drop=True)
        last_date = str(df["date"].iloc[-1])[:10]
        if last_date != date:
            exclude(f"no_today_bar:last={last_date}")  # 停牌或本地数据未更新
            continue
        if len(df) < min_list_days:
            exclude(f"list_days<{min_list_days}")
            continue

        cand = {
            "code": code6,
            "name": name,
            "formula_hits": item["formula_hits"],
            "is_holding": code6 in holding,
            "list_days": len(df),
            **compute_metrics(df, index_df),
        }
        theme = stock_theme.get(code6)
        if theme:
            cand["theme_id"] = theme["theme_id"]
            cand["sector"] = theme["sector"]
        else:
            cand["theme_id"] = ""
            cand["sector"] = "未知"
        result["candidates"].append(cand)

    return result


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="screening 链第 2 段：命中股充实+模式识别（确定性）")
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    registry = _load_json(
        Path(__file__).resolve().parents[2] / "00_governance" / "SCREEN_FORMULA_REGISTRY.json", {}
    )
    result = enrich(args.date, universe_cfg=registry.get("universe") or {})

    SCREENING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SCREENING_DIR / f"{args.date}_candidates_enriched.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    summary = {
        "date": args.date,
        "status": result["status"],
        "degraded_reason": result["degraded_reason"],
        "candidates": len(result["candidates"]),
        "excluded": len(result["excluded"]),
        "output": str(out_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
