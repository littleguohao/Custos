# -*- coding: utf-8 -*-
"""Shared P0 runtime guards: trading calendar, freshness and data quality."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "01_data"
CALENDAR_CONFIG = BASE / "00_governance" / "CN_TRADING_CALENDAR.json"


def load_json(path: Path, default: Any):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def trading_day_status(day: str) -> dict[str, Any]:
    d = date.fromisoformat(day)
    cfg = load_json(CALENDAR_CONFIG, {})
    overrides = cfg.get("overrides", {})
    if day in overrides:
        item = overrides[day]
        return {"date": day, "is_trading_day": bool(item["is_trading_day"]), "reason": item.get("reason", "配置覆盖"), "quality": "confirmed", "source": str(CALENDAR_CONFIG)}
    if d.weekday() >= 5:
        return {"date": day, "is_trading_day": False, "reason": "周末", "quality": "confirmed", "source": "weekday_rule"}
    return {"date": day, "is_trading_day": None, "reason": "工作日但未命中已确认交易日/休市日；禁止自动假定开市", "quality": "unknown", "source": str(CALENDAR_CONFIG)}


def previous_confirmed_trading_day(day: str) -> str | None:
    """Return the latest confirmed trading day before day, or fail closed."""
    cursor = date.fromisoformat(day) - timedelta(days=1)
    for _ in range(14):
        status = trading_day_status(cursor.isoformat())
        if status["is_trading_day"] is True:
            return cursor.isoformat()
        if status["is_trading_day"] is None:
            return None
        cursor -= timedelta(days=1)
    return None


def position_freshness(day: str) -> dict[str, Any]:
    meta = load_json(DATA / "trades" / "_import_meta.json", {})
    imported_at = meta.get("imported_at")
    source_mtime = meta.get("source_mtime")
    status = "stale"
    reason = "缺少导入元数据"
    expected_close_date = previous_confirmed_trading_day(day)
    snapshot_date = meta.get("snapshot_date")
    if imported_at:
        try:
            imported = datetime.fromisoformat(imported_at)
            effective_snapshot_date = snapshot_date or imported.date().isoformat()
            if effective_snapshot_date == expected_close_date:
                status = "confirmed"
                reason = f"使用最近已确认交易日 {expected_close_date} 的收盘持仓快照"
            elif effective_snapshot_date == day and imported.time() >= datetime.strptime("15:00", "%H:%M").time():
                status = "confirmed"
                reason = "当日收盘后已导入持仓快照"
            elif effective_snapshot_date == day:
                status = "uncertain"
                reason = "当日已导入，但无法确认导入后是否发生盘中交易"
            else:
                reason = f"最近快照日期为 {effective_snapshot_date}，预期为 {expected_close_date or '无法确认'}"
        except ValueError:
            reason = "导入时间格式无效"
    return {
        "date": day, "status": status, "confirmed": status == "confirmed",
        "imported_at": imported_at, "source_mtime": source_mtime,
        "snapshot_date": snapshot_date or (imported_at[:10] if imported_at else None),
        "expected_close_date": expected_close_date,
        "reason": reason, "source": str(DATA / "trades" / "_import_meta.json"),
    }


def confirm_position_snapshot(day: str, note: str = "user_confirmed") -> dict[str, Any]:
    path = DATA / "trades" / "position_confirmations.json"
    records = load_json(path, {})
    records[day] = {"confirmed_at": datetime.now().isoformat(timespec="seconds"), "note": note}
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records[day]


def position_freshness_with_confirmation(day: str) -> dict[str, Any]:
    result = position_freshness(day)
    confirmations = load_json(DATA / "trades" / "position_confirmations.json", {})
    if day in confirmations:
        result.update({"status": "confirmed", "confirmed": True, "reason": "用户已确认当日持仓快照", "confirmation": confirmations[day]})
    return result


def _quality(value: Any, section: dict[str, Any], default: str = "candidate") -> str:
    if value is None or value == "":
        return "missing"
    q = str(section.get("quality") or default)
    return q if q in {"confirmed", "auto", "candidate", "partial", "raw_only", "stale", "missing"} else default


def market_quality_gate(market: dict[str, Any], day: str) -> dict[str, Any]:
    checks = []
    amv = market.get("amv_0", {})
    checks.append({"field": "0AMV", "quality": _quality(amv.get("amv_change_pct"), amv, "confirmed" if "user" in str(amv.get("note", "")).lower() else "candidate"), "as_of": amv.get("as_of") or day})
    breadth = market.get("market_breadth", {})
    checks.append({"field": "market_breadth", "quality": _quality(breadth.get("up_count"), breadth), "as_of": breadth.get("as_of") or day})
    sentiment = market.get("sentiment", {})
    checks.append({"field": "sentiment", "quality": _quality(sentiment.get("limit_up_count"), sentiment), "as_of": sentiment.get("as_of") or day})
    turnover = market.get("turnover", {})
    checks.append({"field": "turnover", "quality": _quality(turnover.get("turnover_change_pct"), turnover), "as_of": turnover.get("as_of") or day})
    overseas = market.get("overseas_market", {})
    overseas_values = [overseas.get(k) for k in ("nasdaq_change_pct", "sp500_change_pct", "sox_change_pct", "nikkei_change_pct", "kospi_change_pct", "hstech_change_pct")]
    checks.append({"field": "overseas", "quality": "confirmed" if any(v is not None for v in overseas_values) and overseas.get("as_of") else ("candidate" if any(v is not None for v in overseas_values) else "missing"), "as_of": overseas.get("as_of")})
    rank = {"confirmed": 1.0, "auto": 1.0, "candidate": 0.5, "partial": 0.4, "raw_only": 0.0, "stale": 0.0, "missing": 0.0}
    score = sum(rank[x["quality"]] for x in checks) / len(checks)
    return {
        "date": day, "status": "pass" if score >= 0.8 else ("degraded" if score >= 0.4 else "blocked"),
        "quality_score": round(score, 3), "checks": checks,
        "rule": "confirmed/auto=满权，candidate/partial=降权，raw_only/stale/missing=不得上调权限",
    }


def write_runtime_gate(day: str) -> dict[str, Any]:
    market_path = DATA / "market" / f"{day}_market_timing_input.json"
    market = load_json(market_path, {})
    positions = load_json(DATA / "trades" / "current_positions.json", [])
    freshness = position_freshness_with_confirmation(day)
    market_quality = market_quality_gate(market, day)
    technical = load_json(DATA / "holdings" / f"{day}_holding_technical_summary.json", [])
    technical_dates = sorted({str(x.get("latest_date")) for x in technical if x.get("latest_date")})
    technical_current = bool(technical_dates) and technical_dates == [day]
    technical_freshness = {
        "status": "confirmed" if technical_current else ("stale" if technical_dates else "missing"),
        "latest_dates": technical_dates,
        "expected_date": day,
        "reason": "持仓技术行情已更新至目标日" if technical_current else "持仓技术行情未更新至目标日，不得据此输出精确调仓数量或提高仓位",
    }
    execution_ready = freshness.get("status") == "confirmed" and technical_current and market_quality.get("status") == "pass"
    position_gate = {
        "status": "pass" if execution_ready else ("degraded" if freshness.get("status") in {"confirmed", "uncertain"} else "blocked"),
        "allow_precise_quantity": execution_ready,
        "allow_position_increase": execution_ready,
        "position_count": len(positions),
        "rule": "持仓快照、目标日行情和市场质量必须同时通过，才允许精确交易数量或提高仓位",
    }
    result = {
        "date": day,
        "calendar": trading_day_status(day),
        "position_freshness": freshness,
        "technical_freshness": technical_freshness,
        "position_gate": position_gate,
        "market_quality": market_quality,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    out = DATA / "quality" / f"{day}_runtime_gate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
