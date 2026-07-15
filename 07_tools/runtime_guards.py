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
CALENDAR_CACHE = DATA / "market" / "CN_TRADING_CALENDAR_CACHE.json"


def load_json(path: Path, default: Any):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def official_year_status(d: date, cfg: dict[str, Any]) -> dict[str, Any] | None:
    year = (cfg.get("official_years") or {}).get(str(d.year))
    if not year:
        return None
    source = year.get("source_url") or str(CALENDAR_CONFIG)
    if d.weekday() >= 5:
        return {"is_trading_day": False, "reason": "周末休市", "quality": "confirmed", "source": source}
    day = d.isoformat()
    for item in year.get("closed_ranges") or []:
        if item.get("start") <= day <= item.get("end"):
            return {"is_trading_day": False, "reason": f"交易所官方{item.get('name', '节假日')}休市安排", "quality": "confirmed", "source": source}
    return {"is_trading_day": True, "reason": "交易所官方年度安排：周一至周五且不在休市区间", "quality": "confirmed", "source": source}


def trading_day_status(day: str) -> dict[str, Any]:
    d = date.fromisoformat(day)
    cfg = load_json(CALENDAR_CONFIG, {})
    cache = load_json(CALENDAR_CACHE, {})
    overrides = cfg.get("overrides", {})
    if day in overrides:
        item = overrides[day]
        return {"date": day, "is_trading_day": bool(item["is_trading_day"]), "reason": item.get("reason", "配置覆盖"), "quality": "confirmed", "source": str(CALENDAR_CONFIG)}
    official = official_year_status(d, cfg)
    if official is not None:
        return {"date": day, **official}
    if day in set(cache.get("trading_days", [])):
        return {"date": day, "is_trading_day": True, "reason": "本地通达信交易日历缓存", "quality": "confirmed", "source": str(CALENDAR_CACHE)}
    if day in set(cache.get("non_trading_days", [])):
        return {"date": day, "is_trading_day": False, "reason": "本地通达信日历覆盖范围内非交易日", "quality": "confirmed", "source": str(CALENDAR_CACHE)}
    if d.weekday() >= 5:
        return {"date": day, "is_trading_day": False, "reason": "周末", "quality": "confirmed", "source": "weekday_rule"}
    return {"date": day, "is_trading_day": None, "reason": "工作日但不在通达信缓存覆盖范围；禁止自动假定开市", "quality": "unknown", "source": str(CALENDAR_CONFIG)}


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

    eligible = [d for d in confirmations if d <= day]
    if eligible:
        confirmed_day = max(eligible)
        confirmation = confirmations[confirmed_day]
        no_trades = confirmation.get("no_trades") is True or "无交易" in str(confirmation.get("note", ""))
        if no_trades:
            result.update({
                "status": "confirmed",
                "confirmed": True,
                "inherited": True,
                "inherited_from": confirmed_day,
                "assumption": "B1盘中默认不交易；若用户告知或成交台账出现目标日成交，则立即覆盖此基线",
                "reason": f"默认 {day} 盘中无交易，沿用 {confirmed_day} 已确认无交易后的收盘持仓作为14:45尾盘建议基线",
                "confirmation": confirmation,
            })
    return result


def _quality(value: Any, section: dict[str, Any], default: str = "candidate") -> str:
    if value is None or value == "":
        return "missing"
    q = str(section.get("quality") or default)
    return q if q in {"confirmed", "auto", "candidate", "partial", "raw_only", "stale", "missing"} else default


def _latest_market_section(day: str, section_name: str, value_key: str) -> tuple[dict[str, Any], str | None]:
    for path in sorted((DATA / "market").glob("*_market_timing_input.json"), reverse=True):
        source_day = path.name[:10]
        if source_day >= day:
            continue
        section = load_json(path, {}).get(section_name, {})
        if isinstance(section, dict) and section.get(value_key) not in {None, ""}:
            return section, source_day
    return {}, None


def market_quality_gate(market: dict[str, Any], day: str) -> dict[str, Any]:
    checks = []
    specs = [
        ("0AMV", "amv_0", "amv_change_pct"),
        ("market_breadth", "market_breadth", "up_count"),
        ("sentiment", "sentiment", "limit_up_count"),
        ("turnover", "turnover", "turnover_change_pct"),
    ]
    inherited: dict[str, Any] = {}
    for field, section_name, value_key in specs:
        section = market.get(section_name, {})
        source_day = day
        if not isinstance(section, dict) or section.get(value_key) in {None, ""}:
            prior, prior_day = _latest_market_section(day, section_name, value_key)
            if prior_day:
                section = prior
                source_day = prior_day
                inherited[section_name] = {"as_of": prior_day, "data": prior}
        default_quality = "confirmed" if field == "0AMV" else "candidate"
        quality = _quality(section.get(value_key), section, default_quality)
        if source_day != day and quality in {"confirmed", "auto"}:
            quality = "stale"
        checks.append({
            "field": field,
            "quality": quality,
            "as_of": section.get("as_of") or source_day,
            "inherited": source_day != day,
        })
    overseas = market.get("overseas_market", {})
    overseas_values = [overseas.get(k) for k in ("nasdaq_change_pct", "sp500_change_pct", "sox_change_pct", "nikkei_change_pct", "kospi_change_pct", "hstech_change_pct")]
    checks.append({"field": "overseas", "quality": "confirmed" if any(v is not None for v in overseas_values) and overseas.get("as_of") else ("candidate" if any(v is not None for v in overseas_values) else "missing"), "as_of": overseas.get("as_of")})
    rank = {"confirmed": 1.0, "auto": 1.0, "candidate": 0.5, "partial": 0.4, "raw_only": 0.0, "stale": 0.0, "missing": 0.0}
    score = sum(rank[x["quality"]] for x in checks) / len(checks)
    return {
        "date": day, "status": "pass" if score >= 0.8 else ("degraded" if score >= 0.4 else "blocked"),
        "quality_score": round(score, 3), "checks": checks, "inherited_sections": inherited,
        "rule": "盘中缺少盘后指标时沿用最近有效交易日并标明日期；继承值仅供状态判断，不单独授予加仓权限",
    }


def write_runtime_gate(day: str) -> dict[str, Any]:
    market_path = DATA / "market" / f"{day}_market_timing_input.json"
    market = load_json(market_path, {})
    positions = load_json(DATA / "trades" / "current_positions.json", [])
    freshness = position_freshness_with_confirmation(day)
    market_quality = market_quality_gate(market, day)
    quote_path = DATA / "market" / f"{day}_holding_quotes.json"
    quote_snapshot = load_json(quote_path, {})
    quotes = quote_snapshot.get("quotes", []) if isinstance(quote_snapshot, dict) else []
    position_codes = {str(x.get("代码", "")).split(".")[0] for x in positions}
    quote_codes = {str(x.get("code", "")).split(".")[0] for x in quotes if x.get("date") == day and x.get("price") is not None}
    quotes_current = bool(position_codes) and position_codes.issubset(quote_codes)
    technical = load_json(DATA / "holdings" / f"{day}_holding_technical_summary.json", [])
    technical_dates = sorted({str(x.get("latest_date")) for x in technical if x.get("latest_date")})
    technical_current = bool(technical_dates) and technical_dates == [day]
    technical_freshness = {
        "status": "confirmed" if technical_current else ("stale" if technical_dates else "missing"),
        "latest_dates": technical_dates,
        "expected_date": day,
        "reason": "持仓技术行情已更新至目标日" if technical_current else "持仓技术指标未更新至目标日，不得据此提高仓位；精确减仓数量另由当日行情快照授权",
    }
    reduction_ready = freshness.get("status") == "confirmed" and quotes_current
    market_regime = str(market.get("amv_0", {}).get("effective_state") or "")
    increase_ready = reduction_ready and technical_current and market_quality.get("status") == "pass" and market_regime != "空头"
    position_gate = {
        "status": "pass" if increase_ready else ("degraded" if reduction_ready else "blocked"),
        "allow_precise_quantity": reduction_ready,
        "allow_position_reduction": reduction_ready,
        "allow_position_increase": increase_ready,
        "position_count": len(positions),
        "quote_snapshot": str(quote_path),
        "quote_date": quote_snapshot.get("as_of_date") if isinstance(quote_snapshot, dict) else None,
        "quotes_current": quotes_current,
        "market_regime": market_regime or "未知",
        "rule": "B1默认盘中不交易；最近确认无交易后的收盘持仓可作为14:45尾盘建议基线。持仓基线+当日全持仓行情可授予精确减仓数量权限；加仓另需当日技术、完整市场质量通过且0AMV非空头",
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
