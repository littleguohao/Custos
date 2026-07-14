# -*- coding: utf-8 -*-
"""TDX skill evidence adapters for strategy_team.

The adapters are deliberately pure: they accept dictionaries/lists and return
contract-compliant dictionaries. They never call tools, never place orders and
never override risk_control or chief_decision.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Iterable

ALLOWED_EVIDENCE_STATUS = {"ok", "partial", "stale", "failed"}
ALLOWED_BUCKETS = {"A", "B", "C", "D"}


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _dedupe(values: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    out = []
    for value in values:
        key = repr(value)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def normalize_code(code: Any) -> str:
    raw = str(code or "").strip().upper()
    m = re.search(r"(\d{6})", raw)
    if not m:
        return raw
    digits = m.group(1)
    if raw.endswith((".SH", ".SZ", ".BJ")):
        return raw
    if digits.startswith(("6", "9")):
        suffix = ".SH" if digits.startswith("6") else ".BJ"
    elif digits.startswith(("0", "3")):
        suffix = ".SZ"
    elif digits.startswith(("4", "8")):
        suffix = ".BJ"
    else:
        suffix = ""
    return digits + suffix


@dataclass
class SkillEvidence:
    skill_id: str
    entity_type: str
    entity_id: str
    as_of: str
    trade_date: str
    horizon: str = "short"
    source_tools: list[str] = field(default_factory=list)
    status: str = "ok"
    report_date: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    signals: list[Any] = field(default_factory=list)
    risk_flags: list[Any] = field(default_factory=list)
    raw_ref: str | None = None

    def validate(self) -> None:
        if not self.skill_id or not self.entity_type or not self.entity_id:
            raise ValueError("skill_id/entity_type/entity_id are required")
        if self.status not in ALLOWED_EVIDENCE_STATUS:
            raise ValueError(f"invalid evidence status: {self.status}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.trade_date):
            raise ValueError(f"invalid trade_date: {self.trade_date}")
        datetime.fromisoformat(self.as_of.replace("Z", "+00:00"))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillEvidence":
        allowed = {x.name for x in cls.__dataclass_fields__.values()}
        obj = cls(**{k: v for k, v in data.items() if k in allowed})
        obj.validate()
        return obj


class ThemeStageAdapter:
    """Normalize raw theme stages into SectorState contract values."""

    RULES = (
        (("主升", "加速", "高潮"), "主升"),
        (("修复", "回流", "弱转强", "启动", "发酵"), "修复"),
        (("分歧", "扩散", "高位震荡"), "分歧"),
        (("退潮", "衰退", "下跌", "冰点"), "退潮"),
        (("震荡", "盘整", "整理"), "震荡"),
    )

    @classmethod
    def normalize_stage(cls, raw_stage: Any, trend: Any = "") -> str:
        text = f"{raw_stage or ''}/{trend or ''}"
        for keys, state in cls.RULES:
            if any(key in text for key in keys):
                return state
        return "震荡"

    @classmethod
    def adapt(cls, row: dict[str, Any], date: str) -> dict[str, Any]:
        raw_stage = row.get("raw_stage", row.get("stage", row.get("state", "")))
        trend = row.get("trend", row.get("trend_state", "横盘震荡"))
        state = cls.normalize_stage(raw_stage, trend)
        score = row.get("score")
        action = str(row.get("action_bias") or "")
        if state == "退潮" or "回避" in action or "禁止" in action:
            permission = "回避"
        elif state in {"主升", "修复"} and (score is None or float(score) >= 60):
            permission = "支持"
        else:
            permission = "观察"
        flags = list(_items(row.get("risk_flags")))
        if state == "退潮":
            flags.append("板块退潮")
        if "过热" in str(row.get("daily_kdj_state")) or "过热" in str(row.get("monthly_kdj_state")):
            flags.append("板块过热")
        return {
            "date": date,
            "sector": row.get("theme_name") or row.get("sector") or "未知板块",
            "theme_id": row.get("theme_id"),
            "raw_stage": raw_stage or "未知",
            "state": state,
            "trend": trend if trend in {"上涨", "横盘震荡", "下跌"} else "横盘震荡",
            "relative_strength": row.get("relative_strength", "待确认"),
            "support": row.get("box20_lower", row.get("support")),
            "resistance": row.get("box20_upper", row.get("resistance")),
            "trade_permission": permission,
            "score": score,
            "risk_flags": _dedupe(flags),
            "evidence_refs": _dedupe(_items(row.get("evidence_refs"))),
        }


class CandidateAdapter:
    """Normalize and deduplicate candidates without discovering new stocks."""

    @classmethod
    def adapt_many(
        cls,
        rows: list[dict[str, Any]],
        sector_states: list[dict[str, Any]] | None = None,
        market_permission: str | None = None,
    ) -> list[dict[str, Any]]:
        sector_by_id = {x.get("theme_id"): x for x in sector_states or [] if x.get("theme_id")}
        sector_by_name = {x.get("sector"): x for x in sector_states or [] if x.get("sector")}
        merged: dict[str, dict[str, Any]] = {}
        for source in rows:
            row = copy.deepcopy(source)
            code = normalize_code(row.get("code"))
            if not code:
                continue
            bucket = str(row.get("bucket") or "C").upper()
            if bucket not in ALLOWED_BUCKETS:
                bucket = "C"
            sector_state = sector_by_id.get(row.get("theme_id")) or sector_by_name.get(row.get("sector")) or {}
            risks = list(_items(row.get("risk_flags")))
            if sector_state.get("trade_permission") == "回避":
                risks.append("板块回避")
                bucket = "D"
            if market_permission in {"禁止", "原则不允许"} and bucket == "A":
                bucket = "B"
                risks.append("市场许可不足，A池降级为B池")
            if bucket == "D":
                next_step = "avoid"
            elif bucket == "C":
                next_step = "long_term_track"
            elif bucket == "B":
                next_step = "observe_price"
            else:
                next_step = "generate_buy_plan"
            normalized = {
                **row,
                "code": code,
                "bucket": bucket,
                "risk_flags": _dedupe(risks),
                "next_step": next_step,
                "evidence_refs": _dedupe(_items(row.get("evidence_refs"))),
            }
            if code not in merged or float(normalized.get("score") or 0) > float(merged[code].get("score") or 0):
                merged[code] = normalized
            else:
                current = merged[code]
                current["source"] = _dedupe(_items(current.get("source")) + _items(normalized.get("source")))
                current["risk_flags"] = _dedupe(_items(current.get("risk_flags")) + _items(normalized.get("risk_flags")))
                current["evidence_refs"] = _dedupe(_items(current.get("evidence_refs")) + _items(normalized.get("evidence_refs")))
        return sorted(merged.values(), key=lambda x: (-float(x.get("score") or 0), x["code"]))


class BuyPlanAdapter:
    """Convert raw trade-plan output to BuyPlan with conservative downgrades."""

    @classmethod
    def adapt(
        cls,
        candidate: dict[str, Any],
        raw_plan: dict[str, Any] | None,
        market_permission: str | None = None,
        sector_permission: str | None = None,
    ) -> dict[str, Any]:
        raw = copy.deepcopy(raw_plan or {})
        bucket = str(candidate.get("bucket") or "D").upper()
        risks = _dedupe(_items(candidate.get("risk_flags")) + _items(raw.get("risk_flags")))
        invalid = _items(raw.get("invalid_conditions"))
        stop = raw.get("stop_loss") if isinstance(raw.get("stop_loss"), dict) else {}
        first = raw.get("first_position_pct") if isinstance(raw.get("first_position_pct"), dict) else {}
        entry = _items(raw.get("entry_conditions"))
        missing = []
        if not invalid: missing.append("缺失失效条件")
        if stop.get("price") is None and stop.get("max_loss_pct") is None: missing.append("缺失止损")
        if first.get("upper") in (None, 0, 0.0): missing.append("缺失首仓上限")
        if not entry: missing.append("缺失入场条件")
        risks = _dedupe(risks + missing)

        if bucket in {"C", "D"}:
            conclusion = "禁止"
        elif market_permission in {"禁止", "原则不允许"} or sector_permission == "回避":
            conclusion = "禁止"
        elif bucket == "B":
            conclusion = "仅观察"
        elif missing:
            conclusion = "仅观察"
        else:
            requested = raw.get("conclusion", "小仓试探")
            conclusion = requested if requested in {"允许", "小仓试探"} else "小仓试探"

        return {
            "code": normalize_code(candidate.get("code")),
            "name": candidate.get("name", raw.get("name", "")),
            "stock_pool_bucket": bucket,
            "conclusion": conclusion,
            "buy_mode": raw.get("buy_mode", "无"),
            "buy_price_range": raw.get("buy_price_range", {"lower": None, "upper": None, "basis": ""}),
            "first_position_pct": first or {"lower": 0.0, "upper": 0.0},
            "entry_conditions": entry,
            "add_conditions": _items(raw.get("add_conditions")),
            "invalid_conditions": invalid,
            "stop_loss": stop or {"price": None, "basis": "", "max_loss_pct": None},
            "time_stop": raw.get("time_stop"),
            "risk_level": raw.get("risk_level", "高" if risks else "中"),
            "risk_flags": risks,
            "evidence_refs": _dedupe(_items(candidate.get("evidence_refs")) + _items(raw.get("evidence_refs"))),
        }


class RiskFlagAdapter:
    """Append-only normalization of evidence and workflow risks."""

    KEYWORDS = {
        "业绩预警": ("业绩预警", "禁止加仓", "高"),
        "预亏": ("业绩预警", "禁止加仓", "高"),
        "退潮": ("板块退潮", "禁止加仓", "高"),
        "破位": ("破位", "减仓", "高"),
        "解禁": ("限售解禁", "观察", "中"),
        "减持": ("股东减持", "观察", "中"),
        "stale": ("数据过期", "观察", "中"),
        "failed": ("数据缺失", "观察", "中"),
        "缺失止损": ("无止损计划", "禁止加仓", "高"),
    }

    @classmethod
    def adapt(
        cls,
        date: str,
        evidences: list[dict[str, Any]] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        buy_plans: list[dict[str, Any]] | None = None,
        existing: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        risks = list(existing or [])

        def append(code: str, name: str, text: str, ref: str | None = None):
            for keyword, (risk_type, action, priority) in cls.KEYWORDS.items():
                if keyword in text:
                    risks.append({
                        "code": normalize_code(code), "name": name,
                        "risk_type": risk_type, "action": action, "priority": priority,
                        "reason": text, "evidence_ref": ref,
                    })

        for e in evidences or []:
            code = e.get("entity_id", "") if e.get("entity_type") == "stock" else ""
            name = e.get("facts", {}).get("name", "") if isinstance(e.get("facts"), dict) else ""
            append(code, name, str(e.get("status", "")), e.get("raw_ref"))
            for flag in _items(e.get("risk_flags")):
                append(code, name, str(flag), e.get("raw_ref"))
        for c in candidates or []:
            for flag in _items(c.get("risk_flags")):
                append(c.get("code", ""), c.get("name", ""), str(flag))
        for p in buy_plans or []:
            for flag in _items(p.get("risk_flags")):
                append(p.get("code", ""), p.get("name", ""), str(flag))

        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for risk in risks:
            key = (normalize_code(risk.get("code")), str(risk.get("risk_type")), str(risk.get("reason")))
            unique[key] = {**risk, "code": key[0]}
        ordered = sorted(unique.values(), key=lambda x: ({"高": 0, "中": 1, "低": 2}.get(x.get("priority"), 9), x.get("code", "")))
        level = "强风控" if any(x.get("priority") == "高" for x in ordered) else ("提高" if ordered else "普通")
        forbidden = _dedupe([x.get("action") for x in ordered if x.get("action") in {"禁止加仓", "止损", "清仓"}])
        return {"date": date, "risk_level": level, "forbidden_actions": forbidden, "stock_risks": ordered}


def evidence_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
