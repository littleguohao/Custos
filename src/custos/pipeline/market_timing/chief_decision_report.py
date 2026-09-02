# -*- coding: utf-8 -*-
"""Build ChiefDecision JSON. RiskDecision is mandatory.

v0.165 起人读的 chief 日报 md 停产；市场状态/评分四值改从
scorer 产出的 score JSON（`data/market/{date}_market_timing_score.json`）
读取，不再 regex 解析 md。
"""

from __future__ import annotations
import argparse, json, sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from custos.core.paths import DATA, MARKET_DIR  # noqa: E402
from custos.core.paths import read_json as load  # noqa: E402
from custos.core.code_utils import bare_code as bare  # noqa: E402
from custos.core.contracts import require  # noqa: E402


def dedupe(xs):
    return list(dict.fromkeys(x for x in xs if x))


def _load_inputs(date):
    """读取全部输入文件（强制校验留在 main —— 源码守卫钉在 main 上）。

    模块级常量 DATA/MARKET_DIR 必须**运行时**读取（monkeypatch 通道），
    不得在函数默认值里捕获。
    """
    return {
        "score": load(MARKET_DIR / f"{date}_market_timing_score.json", {}),
        "risk": load(DATA / "risk" / f"{date}_risk_decision.json", {}),
        "holdings": load(DATA / "holdings" / f"{date}_holding_review.json", []),
        "sectors": load(DATA / "sectors" / f"{date}_sector_state.json", []),
        "gate": load(DATA / "quality" / f"{date}_runtime_gate.json", {}),
        "b1_rows": load(DATA / "holdings" / f"{date}_b1_holding_state.json", []),
    }


def _index_b1_rows(b1_rows: list) -> dict:
    """B1 基线按裸码索引。"""
    return {bare(x.get("code")): x for x in b1_rows}


def _index_stock_risks(risk: dict) -> dict:
    """stock_risks 按裸码聚合成有序列表（保持原出现顺序）。"""
    risk_by_code: dict = {}
    for x in risk.get("stock_risks", []):
        risk_by_code.setdefault(bare(x.get("code")), []).append(x)
    return risk_by_code


def _baseline_action_reasons(h: dict, b1: dict) -> tuple:
    """B1 基线：final_* 缺失时回落到持仓复核自身的 action/priority/reason。"""
    action = b1.get("final_action") or h.get("action", "观察")
    priority = b1.get("final_priority") or h.get("priority", "P3")
    reasons = (
        [b1.get("final_reason")]
        if b1.get("final_reason")
        else list(h.get("reason") or [])
    )
    return action, priority, reasons


def _apply_high_risk_override(
    action: str, priority: str, reasons: list, high: list
) -> tuple:
    """高优先风险覆盖：按严重度取最重动作，理由追加风险依据。"""
    # ⚠️ 高优先风险**至少**升到 P1，但**不得把已经更紧急的 P0 降下来**。
    # 2026-08-07 修：原写法是无条件 `priority='P1'`，于是
    #   甲(b1=P0 + 高风险止损) → P1，在处置表里排到
    #   乙(b1=P0、无风险)      → P0 的**后面**
    # 即「多一条高优先风控依据反而降了优先级」。而 holding_actions 是按
    # priority 排序的「先动手处理哪个」清单 —— 增加风险绝不该降低紧急度。
    priority = "P0" if priority == "P0" else "P1"
    actions = [x.get("action") for x in high]
    if "清仓" in actions:
        action = "清仓"
    elif "止损" in actions:
        action = "止损"
    elif "减仓" in actions:
        action = "减仓"
    else:
        action = "禁止加仓"
    reasons += [str(x.get("reason") or x.get("risk_type")) for x in high]
    return action, priority, reasons


def _resolve_one_holding(
    h: dict, b1_by_code: dict, risk_by_code: dict, technical_status: str
) -> dict:
    """单只持仓裁决：B1 基线 + 高优先风险覆盖 + 技术陈旧挂起。"""
    code = bare(h.get("code"))
    rlist = risk_by_code.get(code, [])
    high = [x for x in rlist if x.get("priority") == "高"]
    b1 = b1_by_code.get(code, {})
    action, priority, reasons = _baseline_action_reasons(h, b1)
    if high:
        action, priority, reasons = _apply_high_risk_override(
            action, priority, reasons, high
        )
    elif technical_status != "confirmed":
        action = "等待行情更新"
        reasons = ["目标日持仓技术行情未确认，不沿用旧技术动作"]
    return {
        "priority": priority,
        "code": code,
        "name": h.get("name", ""),
        "action": action,
        "reasons": dedupe(reasons),
        "risk_refs": rlist,
        "b1_holding_state": b1,
        "b1_reference_action": b1.get("final_action"),
        "b1_reference_priority": b1.get("final_priority"),
        "execution_status": "current"
        if technical_status == "confirmed"
        else "waiting_for_current_technical",
    }


def _resolve_holding_actions(holdings, risk, b1_rows, gate):
    """持仓处置裁决段：B1 基线 + 高优先风险覆盖 + 技术陈旧挂起，按优先级排序。"""
    b1_by_code = _index_b1_rows(b1_rows)
    risk_by_code = _index_stock_risks(risk)
    technical_status = gate.get("technical_freshness", {}).get("status", "missing")
    holding_actions = [
        _resolve_one_holding(h, b1_by_code, risk_by_code, technical_status)
        for h in holdings
    ]
    holding_actions.sort(key=lambda x: (x["priority"], x["code"]))
    return holding_actions


def _build_decision(
    date,
    state,
    score,
    position,
    permission,
    effective_risk,
    gate,
    position_gate,
    allowed,
    forbidden,
    holding_actions,
    buy_actions,
    main_sectors,
):
    """组装 ChiefDecision JSON（键集合被消费方钉住，不得增删改名）。"""
    return {
        "date": date,
        "market_state": state,
        "market_score": score,
        "total_position_range": position,
        "new_position_permission": permission,
        "risk_level": effective_risk,
        "position_freshness": gate.get("position_freshness", {}),
        "position_gate": position_gate,
        "market_quality": gate.get("market_quality", {}),
        "allowed_actions": allowed,
        "forbidden_actions": forbidden,
        "holding_actions": holding_actions,
        "buy_actions": buy_actions,
        "watchlist": main_sectors,
        "tomorrow_validation": [
            "市场数据质量是否改善",
            "主线是否形成并保持支持状态",
            "风险持仓是否修复关键结构",
        ],
        "risk_notice": "RiskDecision为强制输入；B1持仓状态只可在硬风险优先级下裁决；任何上游证据均不得提高交易权限或覆盖风险否决。",
        "sources": {
            "risk_decision": str(DATA / "risk" / f"{date}_risk_decision.json"),
            "b1_holding_state": str(
                DATA / "holdings" / f"{date}_b1_holding_state.json"
            ),
            "runtime_gate": str(DATA / "quality" / f"{date}_runtime_gate.json"),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    risk_path = DATA / "risk" / f"{a.date}_risk_decision.json"
    gate_path = DATA / "quality" / f"{a.date}_runtime_gate.json"
    if not risk_path.exists():
        raise SystemExit(f"mandatory RiskDecision missing: {risk_path}")
    inputs = _load_inputs(a.date)
    score_json = inputs["score"]
    risk = inputs["risk"]
    sectors = inputs["sectors"]
    gate = inputs["gate"]
    # 门控与 RiskDecision 同为强制输入。此前缺失时兜底成 {} ⇒ status=None、
    # allow_position_increase=None,两个 `== 'blocked'` / `is False` 判定全部落空,
    # 于是**没有门控的情况下照样输出"允许开新仓"的计划**(审计 A3)。
    if not isinstance(gate, dict) or not gate:
        raise SystemExit(f"mandatory runtime_gate missing/corrupt: {gate_path}")
    state = score_json.get("market_state", "未知")
    score = score_json.get("market_score", "待确认")
    position = score_json.get("total_position_range", "待确认")
    permission = score_json.get("new_position_permission", "原则不允许")
    holding_actions = _resolve_holding_actions(
        inputs["holdings"], risk, inputs["b1_rows"], gate
    )
    buy_actions = []
    # Candidate discovery disabled in pure-script mode; buy_actions always empty
    market_quality_status = gate.get("market_quality", {}).get("status")
    position_gate = gate.get("position_gate", {})
    effective_risk = risk.get("risk_level", "提高")
    # 未知状态(None/拼错)按 blocked 处理:门控读不出结论时不得给出开仓权限
    if risk.get("risk_level") == "强风控" or market_quality_status not in {
        "pass",
        "degraded",
    }:
        permission = "禁止"
        effective_risk = "强风控"
    elif market_quality_status == "degraded" and effective_risk == "普通":
        effective_risk = "提高"
    # 真值判断:None(字段缺失/门控未算出)必须与 False 同等对待——未获授权 ≠ 已获授权
    if not position_gate.get("allow_position_increase"):
        permission = "禁止" if permission == "禁止" else "仅观察，不得加仓"
    allowed = ["处理P1/P2风险持仓", "观察支持交易的主线和A/B池条件"]
    forbidden = dedupe(
        risk.get("forbidden_actions", [])
        + ["无计划追高", "因J值低直接补仓", "绕过risk_control开仓"]
    )
    if market_quality_status not in {"pass", "degraded"}:
        forbidden.append(f"市场数据质量={market_quality_status!r} 时新开仓")
    if not position_gate.get("allow_position_increase"):
        forbidden.append(
            "持仓快照、目标日技术行情或市场质量未全部通过时加仓或输出精确交易数量"
        )
    deterministic_sectors = [
        x.get("sector") for x in sectors if x.get("trade_permission") == "支持"
    ]
    main_sectors = dedupe(deterministic_sectors)[:3]
    decision = _build_decision(
        a.date,
        state,
        score,
        position,
        permission,
        effective_risk,
        gate,
        position_gate,
        allowed,
        forbidden,
        holding_actions,
        buy_actions,
        main_sectors,
    )
    # ⚠️ 落盘前强制校验：这是**最终交易计划**，开仓权限与持仓处理优先级在此定稿。
    require("chief_decision", decision)
    out_json = DATA / "decisions" / f"{a.date}_chief_decision.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(out_json)


if __name__ == "__main__":
    main()
