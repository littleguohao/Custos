# -*- coding: utf-8 -*-
"""Deterministic handoff files and fail-closed validation for specialist Agents.

This script does not call Agents. `main` owns delegation and writes raw responses to:
01_data/agent_handoffs/<date>/responses/<agent>.json

Commands:
  prepare  --date YYYY-MM-DD --session-type premarket
  validate --date YYYY-MM-DD [--require theme-sector portfolio-execution]
"""
from __future__ import annotations
import argparse, json, math
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "01_data"
GOV = BASE / "00_governance"
AGENTS = ("market-intelligence", "theme-sector", "portfolio-execution")
DEFAULT_REQUIRED = {
    "premarket": list(AGENTS),
    "intraday_1445": ["portfolio-execution"],
    "postclose": list(AGENTS),
    "weekly": list(AGENTS),
    "monthly": list(AGENTS),
    "ad_hoc": list(AGENTS),
}
OUTPUT_NAMES = {
    "market-intelligence": "MARKET_INTELLIGENCE_OUTPUT.schema.json",
    "theme-sector": "THEME_SECTOR_OUTPUT.schema.json",
    "portfolio-execution": "PORTFOLIO_EXECUTION_OUTPUT.schema.json",
}
WORKSPACES = {
    "market-intelligence": Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace-market-intelligence"),
    "theme-sector": Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace-theme-sector"),
    "portfolio-execution": Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace-portfolio-execution"),
}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default
    except Exception:
        return default


def sanitize(value):
    """Produce strict JSON: pandas NaN/Inf values become null."""
    if isinstance(value, float) and not math.isfinite(value): return None
    if isinstance(value, dict): return {str(k): sanitize(v) for k, v in value.items()}
    if isinstance(value, list): return [sanitize(v) for v in value]
    return value


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def day_dir(date: str) -> Path:
    return DATA / "agent_handoffs" / date


def compact_positions(positions: list[dict], snapshot_date: str | None) -> list[dict]:
    """Expose stable position facts without implying snapshot prices are current."""
    return [{
        "security_code": str(x.get("代码") or "").split(".")[0],
        "security_name": x.get("名称") or "",
        "quantity": x.get("持有数量"),
        "unit_cost": x.get("单位成本"),
        "position_pct": x.get("仓位占比"),
        "snapshot_date": snapshot_date,
        "price_fields_omitted": True,
    } for x in positions if x.get("代码")]


def compact_technical(items: list[dict]) -> list[dict]:
    fields = ("code", "name", "technical_available", "latest_date", "trend_state",
              "box20_position", "box60_position", "daily_kdj_state",
              "daily_macd_hist_direction", "weekly_kdj_state",
              "weekly_macd_hist_direction", "monthly_kdj_state",
              "monthly_macd_hist_direction")
    return [{key: x.get(key) for key in fields} for x in items]


def compact_sectors(items: list[dict]) -> list[dict]:
    fields = ("theme_id", "theme_name", "primary_code", "representative_stocks",
              "holding_related", "confidence", "available", "latest_date",
              "trend_state", "box20_position", "stage", "stage_reason", "score")
    return [{key: x.get(key) for key in fields} for x in items]


def prepare(date: str, session_type: str, as_of: str, selected_agents: list[str] | None = None) -> dict:
    root = day_dir(date); req = root / "requests"
    selected_agents = selected_agents or list(AGENTS)
    common = {"contract_version": "1.0", "request_id": f"{date}-{session_type}", "session_type": session_type,
              "target_date": date, "as_of": as_of}
    gate = load(DATA / "quality" / f"{date}_runtime_gate.json", {})
    raw_positions = load(DATA / "trades" / "current_positions.json", [])
    positions = compact_positions(raw_positions, gate.get("position_freshness", {}).get("snapshot_date"))
    market_input = load(DATA / "market" / f"{date}_market_timing_input.json", {})
    if market_input:
        market = {
            "date": date,
            "amv_0": market_input.get("amv_0", {}),
            "a_share_indices": market_input.get("a_share_indices", {}),
            "market_breadth": market_input.get("market_breadth", {}),
            "sentiment": market_input.get("sentiment", {}),
            "turnover": market_input.get("turnover", {}),
            "theme": market_input.get("theme", {}),
            "runtime_quality": gate.get("market_quality", {}),
        }
    else:
        market = {"date": date, "status": "missing", "quality": "missing"}
    theme = {**common, "market_state": market,
             "market_intelligence": load(root / "validated" / "market-intelligence.json", None),
             "sector_universe": compact_sectors(load(DATA / "sectors" / f"{date}_sector_technical_summary.json", [])),
             "holdings": positions, "watchlist": load(DATA / "stock_pool" / f"{date}_stock_pool.json", []),
             "previous_sector_state": None,
             "required_sections": ["SectorState", "ThemeLifecycle", "HoldingSectorMap"]}
    portfolio = {**common, "positions": positions,
                 "position_freshness": gate.get("position_freshness", {}),
                 "technical_freshness": gate.get("technical_freshness", {}),
                 "quotes": compact_technical(load(DATA / "holdings" / f"{date}_holding_technical_summary.json", [])),
                 "market_state": market,
                 "sector_state": load(root / "validated" / "theme-sector.json", {"status": "missing"}),
                 "risk_decision": load(DATA / "risk" / f"{date}_risk_decision.json", {"status": "missing"}),
                 "candidate_pool": load(DATA / "stock_pool" / f"{date}_stock_pool_normalized.json", []),
                 "prior_plans": load(DATA / "buy_strategy" / f"{date}_buy_plan_normalized.json", []),
                 "portfolio_limits": gate.get("position_gate", {})}
    filtered_rss = DATA / "news" / "rss" / "filtered" / f"{date}_{session_type}_rss_candidates.json"
    rss_evidence = load(filtered_rss, [])
    rss_cfg = load(GOV / "RSS_FILTER_CONFIG.json", {})
    summary_limit = int(rss_cfg.get("agent_summary_max_chars", 400))
    rss_evidence = [{
        "item_id": x.get("item_id"), "published_at": x.get("published_at"),
        "source_id": x.get("source_id"), "source_name": x.get("source_name"),
        "source_tier": x.get("source_tier"), "title": x.get("title"),
        "summary": str(x.get("summary") or "")[:summary_limit],
        "source_url": x.get("source_url"), "quality": x.get("quality"),
        "confirmed": x.get("confirmed"), "relevance_score": x.get("relevance_score"),
        "matched_holdings_or_pool": x.get("matched_holdings_or_pool"),
        "matched_themes": x.get("matched_themes"),
        "matched_market_keywords": x.get("matched_market_keywords"),
        "policy_stage": x.get("policy_stage"),
    } for x in rss_evidence]
    rss_log = {
        "collection": load(BASE / "06_logs" / "rss" / f"{date}_collection_log.json", {}),
        "filter": load(BASE / "06_logs" / "rss" / f"{date}_{session_type}_filter_log.json", {}),
        "input_kind": "filtered_candidates",
        "filtered_path": str(filtered_rss),
    }
    market_req = {**common, "holdings": positions, "previous_market_state": market,
                  "information_cutoff": as_of, "rss_evidence": rss_evidence,
                  "rss_collection_quality": rss_log}
    required_fields = {
        "market-intelligence": ["market_state", "news_evidence", "notice_evidence", "overseas_evidence", "risk_flags", "validation_points"],
        "theme-sector": ["sector_states", "theme_lifecycles", "holding_sector_map", "risk_flags", "validation_points"],
        "portfolio-execution": ["holding_reviews", "entry_rules", "exit_plans", "position_advice", "risk_flags"],
    }
    bodies = {"market-intelligence": market_req, "theme-sector": theme,
              "portfolio-execution": portfolio}
    for agent in selected_agents:
        body = bodies[agent]
        body["response_contract"] = {
            "strict_json": True,
            "schema_version": "1.0",
            "schema_path": str(WORKSPACES[agent] / "contracts" / OUTPUT_NAMES[agent]),
            "required_common_fields": ["contract_version", "request_id", "agent_id", "status", "target_date", "as_of", "data_quality", "handoff_to_main"],
            "required_agent_fields": required_fields[agent],
            "field_style": "lower_snake_case",
            "handoff_to_main_type": "object",
        }
        dump(req / f"{agent}.json", body)
    manifest = {"date": date, "session_type": session_type, "as_of": as_of,
                "request_id": common["request_id"], "selected_agents": selected_agents,
                "requests": [str(req / f"{x}.json") for x in selected_agents]}
    dump(root / "request_manifest.json", manifest)
    return manifest


def _resolve_schema(schema: dict, root: dict) -> dict:
    if "$ref" not in schema:
        return schema
    node = root
    for part in schema["$ref"].removeprefix("#/").split("/"):
        node = node[part]
    return node


def _schema_errors(value: object, schema: dict, root: dict, path: str = "$") -> list[str]:
    if "$ref" in schema:
        return _schema_errors(value, _resolve_schema(schema, root), root, path)
    errors = []
    for part in schema.get("allOf", []):
        errors.extend(_schema_errors(value, part, root, path))
    if "const" in schema and value != schema["const"]:
        errors.append(f"schema:{path}:const")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"schema:{path}:enum")
    expected = schema.get("type")
    expected_types = [expected] if isinstance(expected, str) else (expected or [])
    checks = {
        "object": lambda x: isinstance(x, dict),
        "array": lambda x: isinstance(x, list),
        "string": lambda x: isinstance(x, str),
        "boolean": lambda x: isinstance(x, bool),
        "null": lambda x: x is None,
        "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
        "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
    }
    if expected_types and not any(checks[t](value) for t in expected_types if t in checks):
        return errors + [f"schema:{path}:type"]
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"schema:{path}.{key}:required")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                errors.extend(_schema_errors(value[key], child, root, f"{path}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for idx, item in enumerate(value):
            errors.extend(_schema_errors(item, schema["items"], root, f"{path}[{idx}]"))
    return errors


def simple_validate(agent: str, raw: object, date: str, request_id: str) -> list[str]:
    errors = []
    if not isinstance(raw, dict): return ["response_not_object"]
    required_common = ["contract_version", "request_id", "agent_id", "status", "target_date", "as_of",
                       "data_quality", "handoff_to_main"]
    required_agent = {
        "market-intelligence": ["market_state", "news_evidence", "notice_evidence", "overseas_evidence", "risk_flags", "validation_points"],
        "theme-sector": ["sector_states", "theme_lifecycles", "holding_sector_map", "risk_flags", "validation_points"],
        "portfolio-execution": ["holding_reviews", "entry_rules", "exit_plans", "position_advice", "risk_flags"],
    }[agent]
    for key in required_common + required_agent:
        if key not in raw: errors.append(f"missing:{key}")
    if raw.get("agent_id") != agent: errors.append("agent_id_mismatch")
    if raw.get("target_date") != date: errors.append("target_date_mismatch")
    if raw.get("request_id") != request_id: errors.append("request_id_mismatch")
    if raw.get("status") not in {"complete", "partial", "blocked", "failed"}: errors.append("invalid_status")
    if raw.get("status") != "complete" and not raw.get("missing_inputs") and raw.get("status") != "failed":
        errors.append("degraded_without_missing_inputs")
    if agent == "theme-sector":
        forbidden_keys = {"buy_action", "sell_action", "position_pct", "quantity", "final_action"}
        if forbidden_keys.intersection(raw): errors.append("theme_sector_overreach")
    if agent == "portfolio-execution":
        for item in raw.get("position_advice", []) if isinstance(raw.get("position_advice"), list) else []:
            if item.get("quantity") is not None: errors.append("non_null_trade_quantity")
            if item.get("requires_main_approval") is not True: errors.append("main_approval_not_required")
        for item in raw.get("entry_rules", []) if isinstance(raw.get("entry_rules"), list) else []:
            pool, permission = item.get("pool"), item.get("permission")
            # English aliases make deterministic tests and cross-model output robust.
            observe_or_block = {"仅观察", "禁止", "observe_only", "blocked"}
            blocked_only = {"禁止", "blocked"}
            if pool == "B" and permission not in observe_or_block: errors.append("b_pool_permission_overreach")
            if pool in {"C", "D"} and permission not in blocked_only: errors.append("cd_pool_permission_overreach")
    schema = load(WORKSPACES[agent] / "contracts" / OUTPUT_NAMES[agent], {})
    if schema:
        errors.extend(_schema_errors(raw, schema, schema))
    return list(dict.fromkeys(errors))


def validate(date: str, required: list[str] | None, optional: bool = False) -> dict:
    root = day_dir(date); responses = root / "responses"; valid_dir = root / "validated"
    manifest = load(root / "request_manifest.json", {})
    request_id = manifest.get("request_id", "")
    session_type = manifest.get("session_type", "ad_hoc")
    required = [] if optional else (DEFAULT_REQUIRED.get(session_type, list(AGENTS)) if required is None else required)
    invalid_required = [agent for agent in required if agent not in AGENTS]
    if invalid_required:
        raise ValueError(f"unknown required agents: {invalid_required}")
    results = {}; all_accepted = True; all_complete = True
    for agent in AGENTS:
        raw_path = responses / f"{agent}.json"; raw = load(raw_path, None)
        errors = ["response_missing"] if raw is None else simple_validate(agent, raw, date, request_id)
        accepted = not errors and raw.get("status") in {"complete", "partial"}
        usable_for_permission_increase = accepted and raw.get("status") == "complete"
        valid_path = valid_dir / f"{agent}.json"
        if accepted:
            dump(valid_path, raw)
        elif valid_path.exists():
            valid_path.unlink()
        results[agent] = {"accepted": accepted, "usable_for_permission_increase": usable_for_permission_increase,
                          "status": raw.get("status") if isinstance(raw, dict) else "missing",
                          "errors": errors, "response": str(raw_path)}
        if agent in required:
            if not accepted: all_accepted = False
            if not usable_for_permission_increase: all_complete = False
    if optional:
        accepted_optional = [x for x in results.values() if x["accepted"]]
        gate_status = "available" if accepted_optional else "unavailable"
    else:
        gate_status = "pass" if all_accepted and all_complete else ("degraded" if all_accepted else "blocked")
    gate = {"date": date, "request_id": request_id, "required_agents": required,
            "status": gate_status, "optional_enrichment": optional,
            "permission_increase_allowed": False if optional else all_accepted and all_complete,
            "agents": results,
            "rule": "specialist evidence is asynchronous enrichment; missing/invalid/partial evidence never blocks reports or increases permissions"}
    dump(root / "handoff_gate.json", gate)
    return gate


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--date", required=True); p.add_argument("--session-type", required=True,
        choices=["premarket", "intraday_1445", "postclose", "weekly", "monthly", "ad_hoc"]); p.add_argument("--as-of")
    p.add_argument("--agent", action="append", choices=AGENTS, dest="agents")
    v = sub.add_parser("validate"); v.add_argument("--date", required=True); v.add_argument("--require", nargs="*", default=None); v.add_argument("--optional", action="store_true")
    a = ap.parse_args()
    if a.cmd == "prepare": result = prepare(a.date, a.session_type, a.as_of or datetime.now().astimezone().isoformat(timespec="seconds"), a.agents)
    else: result = validate(a.date, a.require, a.optional)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if a.cmd == "validate" and not a.optional and result["status"] != "pass": raise SystemExit(2)

if __name__ == "__main__": main()
