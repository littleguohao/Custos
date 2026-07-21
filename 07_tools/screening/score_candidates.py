# -*- coding: utf-8 -*-
"""Screening 链第 3 段：板块过滤 + 共振打分分层（score_candidates）。

规则来源（历史设计，已批准照此实现）：
00_governance/FORMULA_SCREEN_SECTOR_FILTER_WORKFLOW.md（git d674335）。

技术面 x 板块热度共振矩阵（base bucket）：

| 技术面\\板块 | 强 | 中 | 弱 | 未知 |
|---|---|---|---|---|
| 强 | A | B | C | C |
| 中 | B | C | D | D |
| 弱 | C | D | D | D |

板块过滤（pass_level 封顶）：
- 主升/修复 → allow_A（可 A/B）
- 震荡/分歧 → allow_B（最多 B）
- 退潮 → observe_only（原则 D 或 C 观察，封顶 C）
- 未知/缺 sector_state → reject_A（不进 A）

附加调整：
- 0AMV 空头 → 全池最高 B 且 next_step=observe_price。
- 无可定义止损位（近10日最低价缺失）→ 不得入 A（封顶 B，打 risk_flags）。

CLI::

    uv run python 07_tools/screening/score_candidates.py --date YYYY-MM-DD

输出 ``01_data/stock_pool/{date}_stock_pool.json``（StockPool 契约，
见 00_governance/DATA_FLOW_CONTRACT.md）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from paths import DATA, MARKET_DIR, SECTORS_DIR, STOCK_POOL_DIR  # noqa: E402

SCREENING_DIR = DATA / "screening"

BUCKET_ORDER = ["A", "B", "C", "D"]

# 共振矩阵：(technical_level, sector_heat_level) → base bucket
RESONANCE_MATRIX = {
    ("强", "强"): "A", ("强", "中"): "B", ("强", "弱"): "C", ("强", "未知"): "C",
    ("中", "强"): "B", ("中", "中"): "C", ("中", "弱"): "D", ("中", "未知"): "D",
    ("弱", "强"): "C", ("弱", "中"): "D", ("弱", "弱"): "D", ("弱", "未知"): "D",
}

# 板块状态 → (heat_level, pass_level, 封顶)
SECTOR_STATE_MAP = {
    "主升": ("强", "allow_A", "A"),
    "修复": ("强", "allow_A", "A"),
    "震荡": ("中", "allow_B", "B"),
    "分歧": ("中", "allow_B", "B"),
    "退潮": ("弱", "observe_only", "C"),
}

NEXT_STEP = {
    "A": "generate_buy_plan",
    "B": "observe_price",
    "C": "long_term_track",
    "D": "avoid",
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def cap_bucket(bucket: str, cap: str) -> str:
    """把 bucket 封顶到 cap（A 最优、D 最差；只降档、不升档）。"""
    return bucket if BUCKET_ORDER.index(bucket) >= BUCKET_ORDER.index(cap) else cap


def technical_score(cand: dict) -> tuple[int, str]:
    """技术分（0-100）与技术面层级（强>=60 / 中30-59 / 弱<30）。确定性加分。"""
    patterns = cand.get("patterns") or {}
    score = 0
    if patterns.get("bbi_above"):
        score += 25
    daily_j = cand.get("daily_j")
    if patterns.get("j_low"):
        score += 20
    elif daily_j is not None and 13 <= daily_j < 50:
        score += 10
    if patterns.get("volume_contraction"):
        score += 15
    if patterns.get("reversal_k_candidate"):
        score += 25
    if patterns.get("relative_strength_strong"):
        score += 15
    score = min(score, 100)
    level = "强" if score >= 60 else ("中" if score >= 30 else "弱")
    return score, level


def sector_heat(sector_entry: Optional[dict]) -> tuple[str, str, str, str]:
    """板块状态 → (heat_level, pass_level, 封顶bucket, reason)。"""
    if not sector_entry:
        return "未知", "reject_A", "B", "板块未映射或无 sector_state，不进 A"
    state = str(sector_entry.get("state") or sector_entry.get("sector_state") or "")
    heat, pass_level, cap = SECTOR_STATE_MAP.get(
        state, ("未知", "reject_A", "B")
    )
    reason = {
        "allow_A": f"板块{state}，可进 A/B",
        "allow_B": f"板块{state}，最多 B",
        "observe_only": f"板块{state}，原则 D 或 C 观察",
    }.get(pass_level, f"板块状态{state or '未知'}，不进 A")
    return heat, pass_level, cap, reason


def resonance_level(tech_level: str, heat_level: str) -> str:
    if (tech_level, heat_level) == ("强", "强"):
        return "强共振"
    if "强" in (tech_level, heat_level):
        return "弱共振"
    if (tech_level, heat_level) == ("弱", "弱"):
        return "反向"
    return "无共振"


def market_permission(amv_state: str) -> str:
    return {"做多": "允许", "空头": "观察"}.get(amv_state, "仅低吸")


def score_candidate(
    cand: dict,
    sector_entry: Optional[dict],
    amv_state: str,
) -> dict:
    """对单只充实候选打分分层，输出 StockPool 契约条目（含打分明细）。"""
    tech_score, tech_level = technical_score(cand)
    heat, pass_level, sector_cap, reason = sector_heat(sector_entry)
    sector_score = float(sector_entry.get("score", 0) or 0) if sector_entry else 0.0

    base_bucket = RESONANCE_MATRIX[(tech_level, heat)]
    res_level = resonance_level(tech_level, heat)
    permission = market_permission(amv_state)

    risk_flags: list[str] = []
    if cand.get("is_holding"):
        risk_flags.append("is_holding")

    # 封顶规则
    bucket = cap_bucket(base_bucket, sector_cap)
    if not (cand.get("stop_loss_ref") or {}).get("price"):
        if BUCKET_ORDER.index(bucket) < BUCKET_ORDER.index("B"):
            risk_flags.append("no_stop_loss_ref")
        bucket = cap_bucket(bucket, "B")
    if amv_state == "空头":
        bucket = cap_bucket(bucket, "B")

    # 总分：技术 60% + 板块 40% + 共振调整，0-100
    resonance_adj = {"强共振": 5, "弱共振": 0, "无共振": 0, "反向": -5}[res_level]
    total = round(0.6 * tech_score + 0.4 * sector_score + resonance_adj, 1)
    total = max(0.0, min(100.0, total))

    entry_reason: list[str] = []
    for fid in cand.get("formula_hits") or []:
        entry_reason.append(f"公式命中:{fid}")
    label = {
        "bbi_above": "收盘站上BBI",
        "j_low": "日J低位(<13)",
        "volume_contraction": "极致缩量",
        "reversal_k_candidate": "反转K候选",
        "relative_strength_strong": "20日相对强度>=+3pp",
    }
    for tag, hit in (cand.get("patterns") or {}).items():
        if hit:
            entry_reason.append(label.get(tag, tag))

    next_step = NEXT_STEP[bucket]
    if amv_state == "空头":
        next_step = "observe_price"

    return {
        "code": cand.get("code", ""),
        "name": cand.get("name", ""),
        "sector": cand.get("sector", "未知"),
        "theme_id": cand.get("theme_id", ""),
        "formula_hits": cand.get("formula_hits") or [],
        "sector_heat_filter": {
            "sector_state": (sector_entry or {}).get("state")
                            or (sector_entry or {}).get("sector_state") or "未知",
            "sector_score": sector_score,
            "heat_level": heat,
            "pass_level": pass_level,
            "reason": reason,
        },
        "resonance": {
            "technical_level": tech_level,
            "sector_heat_level": heat,
            "market_permission": permission,
            "resonance_level": res_level,
        },
        "stock_role": "未定",
        "relative_strength": "未定",
        "score": total,
        "score_detail": {
            "technical_score": tech_score,
            "sector_score": sector_score,
            "base_bucket": base_bucket,
            "resonance_adj": resonance_adj,
            "total": total,
        },
        "bucket": bucket,
        "entry_reason": entry_reason,
        "risk_flags": risk_flags,
        "next_step": next_step,
        "patterns": cand.get("patterns") or {},
        "daily_j": cand.get("daily_j"),
        "stop_loss_ref": cand.get("stop_loss_ref"),
        "is_holding": bool(cand.get("is_holding")),
    }


def score_all(
    date: str,
    enriched: Optional[dict] = None,
    sector_states: Optional[list] = None,
    amv_state: Optional[str] = None,
) -> dict:
    """整池打分。输入缺失时干净降级，绝不 raise。"""
    if enriched is None:
        enriched = _load_json(SCREENING_DIR / f"{date}_candidates_enriched.json", {})
    if sector_states is None:
        sector_states = _load_json(SECTORS_DIR / f"{date}_sector_state.json", [])
    if amv_state is None:
        market = _load_json(MARKET_DIR / f"{date}_market_timing_input.json", {})
        amv_state = str((market.get("amv_0") or {}).get("effective_state") or "")

    result: dict[str, Any] = {
        "date": date,
        "status": "ok",
        "degraded_reason": "",
        "source": "screening_chain_v1",
        "amv_state": amv_state or "未知",
        "market_permission": market_permission(amv_state),
        "bucket_counts": {"A": 0, "B": 0, "C": 0, "D": 0},
        "candidates": [],
    }

    if not enriched or enriched.get("status") == "unavailable":
        result["status"] = "unavailable"
        result["degraded_reason"] = (
            f"enriched_unavailable:{(enriched or {}).get('degraded_reason', 'missing')}"
        )
        return result
    if not sector_states:
        result["status"] = "partial"
        result["degraded_reason"] = "sector_state_missing"

    # theme_id / sector 名 → sector_state 条目
    by_theme: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for s in sector_states if isinstance(sector_states, list) else []:
        if s.get("theme_id"):
            by_theme[str(s["theme_id"])] = s
        if s.get("sector"):
            by_name[str(s["sector"])] = s

    for cand in enriched.get("candidates", []):
        entry = by_theme.get(cand.get("theme_id", "")) or by_name.get(cand.get("sector", ""))
        scored = score_candidate(cand, entry, amv_state)
        result["candidates"].append(scored)
        result["bucket_counts"][scored["bucket"]] += 1

    result["candidates"].sort(key=lambda x: (-x["score"], x["code"]))
    return result


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="screening 链第 3 段：板块过滤+共振打分分层（确定性）")
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args(argv)

    result = score_all(args.date)

    STOCK_POOL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = STOCK_POOL_DIR / f"{args.date}_stock_pool.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "date": args.date,
        "status": result["status"],
        "degraded_reason": result["degraded_reason"],
        "bucket_counts": result["bucket_counts"],
        "output": str(out_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
