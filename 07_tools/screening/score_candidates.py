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

from paths import DATA, GOVERNANCE, MARKET_DIR, SECTORS_DIR, STOCK_POOL_DIR  # noqa: E402

SCREENING_DIR = DATA / "screening"
CZ_SECTOR_PREF_PATH = GOVERNANCE / "CZ_SECTOR_PREFERENCE.json"
REGISTRY_PATH = GOVERNANCE / "SCREEN_FORMULA_REGISTRY.json"

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

# 待回测启发式驱动的封顶规则开关。默认全开＝保持历史行为；关闭某项后不再据此
# 降档，改在 risk_flags 记录 "<rule>_detected_cap_disabled"（仍随候选落盘，便于
# 回测校准前后对比）。可经 SCREEN_FORMULA_REGISTRY.json 的 "scoring".cap_rules
# 覆盖，见 00_governance/SCREENING_WORKFLOW.md「可配置项」。
DEFAULT_CAP_RULES = {
    "sprint_wave": True,           # 冲刺波后首个 B1 禁买 → 封顶 B（检测阈值待回测）
    "volume_retreat": True,        # 量能持续性=主力撤退 → 封顶 C（CZ §14.6，部分阈值待回测）
    "non_one_wave_revoked": True,  # 非一波流撤销 → 封顶 C（待回测）
    "cz_avoid_sector": True,       # CZ 回避方向板块 → D（治理名单驱动）
    "distribution_cap": True,      # 主力出货五方式命中 → high 封 D / watch 封 C（B1 §七.3，待回测）
    "macd_divergence": True,       # MACD 顶背离/三打白骨精 → 封顶 C（macd十大技术，待回测）
}

# sector_state.score 的量纲：generate_risk_and_sectors 用 float(score)>=60 门控
# 主升/修复，即 0-100。此常量供 normalize_sector_score 归一化/兜底，若未来 generator
# 改量纲，只需改 registry "scoring".sector_score_max 一处即可。
SECTOR_SCORE_MAX = 100.0


def resolve_cap_rules(cap_rules: Optional[dict]) -> dict:
    """把外部（registry/调用方）传入的 cap 开关并入默认表；未知键忽略。"""
    rules = dict(DEFAULT_CAP_RULES)
    if isinstance(cap_rules, dict):
        for key, val in cap_rules.items():
            if key in rules:
                rules[key] = bool(val)
    return rules


def normalize_sector_score(raw: Any, score_max: float = SECTOR_SCORE_MAX) -> float:
    """把 sector_state.score 归一化到 0-100 并 clamp，量纲异常/缺失时鲁棒兜底。

    - raw 为 None/非数值 → 0.0（板块无评分，等价最弱）。
    - score_max<=0 或非法 → 回退 SECTOR_SCORE_MAX（避免除零/放大）。
    - 结果一律 clamp 到 [0, 100]，确保 0.4*sector_score 的量纲不被脏数据放大。
    """
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    try:
        smax = float(score_max)
    except (TypeError, ValueError):
        smax = SECTOR_SCORE_MAX
    if smax <= 0:
        smax = SECTOR_SCORE_MAX
    return max(0.0, min(100.0, val / smax * 100.0))


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def cap_bucket(bucket: str, cap: str) -> str:
    """把 bucket 封顶到 cap（A 最优、D 最差；只降档、不升档）。"""
    return bucket if BUCKET_ORDER.index(bucket) >= BUCKET_ORDER.index(cap) else cap


def technical_score(cand: dict) -> tuple[int, str, dict]:
    """技术分（0-100）与技术面层级（强>=60 / 中30-59 / 弱<30）。确定性加分。

    B1/CZ 对齐加分（阈值见 enrich_candidates 顶部"待回测参数"）：
    five_day_entry +8、leader_volume +6、bottom_volume +6、
    repair_signals 每项 +3（上限 +6）、non_one_wave=confirmed +5。
    返回 (score, level, factor_contrib)（factor_contrib 落盘可复盘）。
    """
    patterns = cand.get("patterns") or {}
    contrib: dict[str, int] = {}
    score = 0
    if patterns.get("bbi_above"):
        score += 25
        contrib["bbi_above"] = 25
    if patterns.get("reversal_k_candidate"):
        # 反转K为复合信号：命中时其子项（j_low、volume_contraction）不再单独加分，
        # 由 composite 分取代（未命中时子项照常计分）。
        score += 25
        contrib["reversal_k_candidate"] = 25
        contrib["reversal_k_replaces"] = "j_low+volume_contraction"
    else:
        daily_j = cand.get("daily_j")
        if patterns.get("j_low"):
            score += 20
            contrib["j_low"] = 20
        elif daily_j is not None and 13 <= daily_j < 50:
            score += 10
            contrib["j_mid"] = 10
        if patterns.get("volume_contraction"):
            score += 15
            contrib["volume_contraction"] = 15
    if patterns.get("relative_strength_strong"):
        score += 15
        contrib["relative_strength_strong"] = 15
    if (cand.get("five_day_entry") or {}).get("hit"):
        score += 8
        contrib["five_day_entry"] = 8
    leader = cand.get("leader_volume") or {}
    if leader.get("available") and leader.get("hit"):
        score += 6
        contrib["leader_volume"] = 6
    bottom = cand.get("bottom_volume") or {}
    if bottom.get("available") and bottom.get("hit"):
        score += 6
        contrib["bottom_volume"] = 6
    repair_hits = (cand.get("repair_signals") or {}).get("signals") or []
    if repair_hits:
        pts = min(len(repair_hits) * 3, 6)
        score += pts
        contrib["repair_signals"] = pts
    if (cand.get("non_one_wave") or {}).get("status") == "confirmed":
        score += 5
        contrib["non_one_wave_confirmed"] = 5
    # 完美 B1 图形贴合度（0-8 梯度：J深度/回踩贴线/缩量程度/MACD零轴/DKS上行）
    fit = (cand.get("perfect_b1_fit") or {}).get("score")
    if fit:
        score += fit
        contrib["perfect_b1_fit"] = fit
    # MACD 十大技术（正向）：第一区间强势扩张 +3；第一区间再启动（3/5浪买点）+5；
    # 底背离 +5（B1 修复确认）。负向顶背离/三打白骨精走封顶，不在此减分。
    mt = cand.get("macd_technics") or {}
    if mt.get("available"):
        if mt.get("zone") == 1:
            score += 3
            contrib["macd_zone1"] = 3
        if mt.get("zone1_restart"):
            score += 5
            contrib["macd_zone1_restart"] = 5
        if (mt.get("bottom_divergence") or {}).get("hit"):
            score += 5
            contrib["macd_bottom_divergence"] = 5
    # 知行量价（good_b1）：多头趋势线 + 点火 + 缩量企稳 + 复合确认。
    # 注意：b1_ignition 是复合信号（含 ignition/pullback_shrink 条件），此处
    # 子项与复合项有意叠加计分，待回测校准（与 reversal_k 的"复合取代子项"
    # 口径不同，属已知不一致，回测后统一）。
    zx = cand.get("zhixing") or {}
    if zx.get("available") and zx.get("qsx_gt_dks"):
        score += 6
        contrib["zhixing_bull"] = 6
    if (cand.get("ignition") or {}).get("hit"):
        score += 4
        contrib["ignition"] = 4
    if (cand.get("pullback_shrink") or {}).get("hit"):
        score += 3
        contrib["pullback_shrink"] = 3
    if (cand.get("b1_ignition") or {}).get("hit"):
        score += 8
        contrib["b1_ignition"] = 8
    score = min(score, 100)
    level = "强" if score >= 60 else ("中" if score >= 30 else "弱")
    return score, level, contrib


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
    cz_sector: str = "neutral",
    cap_rules: Optional[dict] = None,
    sector_score_max: float = SECTOR_SCORE_MAX,
) -> dict:
    """对单只充实候选打分分层，输出 StockPool 契约条目（含打分明细）。

    cap_rules 传 None 时用 DEFAULT_CAP_RULES（全开＝历史行为）；显式传部分键可
    单独关闭某条待回测封顶规则（关闭后仅在 risk_flags 记录检出、不降档）。
    sector_score_max 指定 sector_state.score 的量纲上界，用于归一化到 0-100。
    """
    rules = resolve_cap_rules(cap_rules)
    tech_score, tech_level, factor_contrib = technical_score(cand)
    heat, pass_level, sector_cap, reason = sector_heat(sector_entry)
    sector_score_raw = (sector_entry or {}).get("score") if sector_entry else None
    sector_score = normalize_sector_score(sector_score_raw, sector_score_max)

    base_bucket = RESONANCE_MATRIX[(tech_level, heat)]
    res_level = resonance_level(tech_level, heat)
    permission = market_permission(amv_state)

    risk_flags: list[str] = []
    if cand.get("is_holding"):
        risk_flags.append("is_holding")

    # 封顶规则（cap 只降不升；条件 flag 无条件记录，cap 是否实际触发另算）
    bucket = cap_bucket(base_bucket, sector_cap)
    if not (cand.get("stop_loss_ref") or {}).get("price"):
        risk_flags.append("no_stop_loss_ref")
        bucket = cap_bucket(bucket, "B")
    if amv_state == "空头":
        bucket = cap_bucket(bucket, "B")
    wave_type = (cand.get("wave") or {}).get("wave_type")
    if wave_type == "sprint":
        # B1 §四.0：冲刺波后首个 B1 禁止买入 → 最高 B
        if rules["sprint_wave"]:
            risk_flags.append("sprint_wave_first_b1_forbidden")
            bucket = cap_bucket(bucket, "B")
        else:
            risk_flags.append("sprint_wave_detected_cap_disabled")
    if (cand.get("volume_sustain") or {}).get("status") == "retreat":
        # CZ §14.6：连续3日量<峰值55%，主力撤退 → 最高 C
        if rules["volume_retreat"]:
            risk_flags.append("main_force_retreat")
            bucket = cap_bucket(bucket, "C")
        else:
            risk_flags.append("main_force_retreat_cap_disabled")
    if (cand.get("non_one_wave") or {}).get("status") == "revoked":
        # B1 §四：非一波流撤销（顶部放量大阴/回调放量破位）→ 最高 C
        if rules["non_one_wave_revoked"]:
            risk_flags.append("non_one_wave_revoked")
            bucket = cap_bucket(bucket, "C")
        else:
            risk_flags.append("non_one_wave_revoked_cap_disabled")
    if cz_sector == "avoid":
        # CZ §七：回避方向板块 → D
        if rules["cz_avoid_sector"]:
            risk_flags.append("cz_avoid_sector")
            bucket = "D"
        else:
            risk_flags.append("cz_avoid_sector_cap_disabled")
    dist = cand.get("distribution") or {}
    if dist.get("available") and dist.get("hits"):
        # B1 §七.3：主力出货五方式命中 → 顶部派发规避
        if rules["distribution_cap"]:
            if dist.get("risk_level") == "high":
                risk_flags.append("distribution_high")
                bucket = "D"
            else:
                risk_flags.append("distribution_watch")
                bucket = cap_bucket(bucket, "C")
        else:
            risk_flags.append("distribution_detected_cap_disabled")
    mt_cap = cand.get("macd_technics") or {}
    top_div_hit = (mt_cap.get("top_divergence") or {}).get("hit")
    three_peaks_hit = (mt_cap.get("three_peaks") or {}).get("hit")
    if mt_cap.get("available") and (top_div_hit or three_peaks_hit):
        # MACD 十大技术：顶背离 / 三打白骨精（K线三高+MACD三低）→ 封顶 C
        if rules["macd_divergence"]:
            if top_div_hit:
                risk_flags.append("macd_top_divergence")
            if three_peaks_hit:
                risk_flags.append("macd_three_peaks")
            bucket = cap_bucket(bucket, "C")
        else:
            risk_flags.append("macd_divergence_detected_cap_disabled")
    if mt_cap.get("available") and (mt_cap.get("overextended") or {}).get("hit"):
        risk_flags.append("macd_overextended")  # 开口/空间拐离：仅记录，不降档

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
    if (cand.get("five_day_entry") or {}).get("hit"):
        entry_reason.append("五日战法入场")
    if (cand.get("leader_volume") or {}).get("hit"):
        entry_reason.append("龙头量能")
    if (cand.get("bottom_volume") or {}).get("hit"):
        entry_reason.append("底部巨量")
    for sig in (cand.get("repair_signals") or {}).get("signals") or []:
        entry_reason.append(f"修复信号:{sig}")
    if (cand.get("non_one_wave") or {}).get("status") == "confirmed":
        entry_reason.append("非一波流确认")
    if wave_type and wave_type != "unknown":
        entry_reason.append(f"波浪:{ {'buildup': '建仓波', 'rally': '拉升波', 'sprint': '冲刺波'}[wave_type] }")
    if (cand.get("b1_ignition") or {}).get("hit"):
        entry_reason.append("知行B1点火确认")
    elif (cand.get("zhixing") or {}).get("available") and (cand.get("zhixing") or {}).get("qsx_gt_dks"):
        entry_reason.append("知行多头(QSX>DKS)")
    for _dk in dist.get("hits") or []:
        entry_reason.append(f"出货信号:{_dk}")
    _mt = cand.get("macd_technics") or {}
    if _mt.get("available"):
        if _mt.get("zone1_restart"):
            entry_reason.append("MACD第一区间再启动")
        if (_mt.get("bottom_divergence") or {}).get("hit"):
            entry_reason.append("MACD底背离")

    next_step = NEXT_STEP[bucket]
    if amv_state == "空头":
        next_step = "observe_price"
    if wave_type == "sprint" and rules["sprint_wave"] and next_step == "generate_buy_plan":
        # 双保险：冲刺波后首个 B1 禁买，不得生成买入计划
        next_step = "observe_price"

    return {
        "code": cand.get("code", ""),
        "name": cand.get("name", ""),
        "sector": cand.get("sector", "未知"),
        "sector_source": cand.get("sector_source", ""),
        "theme_id": cand.get("theme_id", ""),
        "formula_hits": cand.get("formula_hits") or [],
        "sector_heat_filter": {
            "sector_state": (sector_entry or {}).get("state")
                            or (sector_entry or {}).get("sector_state") or "未知",
            "sector_score": sector_score,
            "sector_score_raw": sector_score_raw,
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
            "sector_score_raw": sector_score_raw,
            "base_bucket": base_bucket,
            "resonance_adj": resonance_adj,
            "cap_rules": rules,
            "factor_contrib": factor_contrib,
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
        # B1/CZ 策略对齐落盘字段
        "cz_sector": cz_sector,
        "wave": cand.get("wave") or {},
        "weekly_j": cand.get("weekly_j"),
        "weekly_j_low": bool(cand.get("weekly_j_low")),
        "non_one_wave": cand.get("non_one_wave") or {},
        "repair_signals": cand.get("repair_signals") or {},
        "five_day_entry": cand.get("five_day_entry") or {},
        "volume_sustain": cand.get("volume_sustain") or {},
        "leader_volume": cand.get("leader_volume") or {},
        "three_lows": cand.get("three_lows") or {},
        "bottom_volume": cand.get("bottom_volume") or {},
        # 知行量价 + 出货识别（good_b1 / 出货五方式）
        "zhixing": cand.get("zhixing") or {},
        "ignition": cand.get("ignition") or {},
        "pullback_shrink": cand.get("pullback_shrink") or {},
        "ride_above_fast": bool(cand.get("ride_above_fast")),
        "b1_ignition": cand.get("b1_ignition") or {},
        "distribution": cand.get("distribution") or {},
    }


def load_cz_sector_preference(path: Optional[Path] = None) -> Optional[dict]:
    """加载 CZ 板块白/黑名单；文件缺失/损坏返回 None（调用方降级为 neutral）。"""
    p = Path(path) if path else CZ_SECTOR_PREF_PATH
    data = _load_json(p, None)
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("favored"), list) or not isinstance(data.get("avoid"), list):
        return None
    return data


def cz_sector_of(sector_name: str, preference: Optional[dict]) -> str:
    """主题名子串匹配白/黑名单（avoid 优先，保守）；无匹配或名单缺失 → neutral。"""
    if not preference or not sector_name or sector_name == "未知":
        return "neutral"
    for kw in preference.get("avoid") or []:
        if kw and kw in sector_name:
            return "avoid"
    for kw in preference.get("favored") or []:
        if kw and kw in sector_name:
            return "favored"
    return "neutral"


def _load_scoring_config(path: Optional[Path] = None) -> dict:
    """读 SCREEN_FORMULA_REGISTRY.json 的 "scoring" 段（cap_rules / sector_score_max）；
    缺失/损坏返回 {}（调用方回退默认，行为不变）。"""
    p = Path(path) if path else REGISTRY_PATH
    data = _load_json(p, {})
    scoring = data.get("scoring") if isinstance(data, dict) else None
    return scoring if isinstance(scoring, dict) else {}


def score_all(
    date: str,
    enriched: Optional[dict] = None,
    sector_states: Optional[list] = None,
    amv_state: Optional[str] = None,
    cz_preference: Optional[dict] = None,
    cap_rules: Optional[dict] = None,
    sector_score_max: Optional[float] = None,
) -> dict:
    """整池打分。输入缺失时干净降级，绝不 raise。

    cz_preference 传 None 时从 00_governance/CZ_SECTOR_PREFERENCE.json 加载；
    显式传 {} 表示"已加载但不可用"（测试降级路径用）。
    cap_rules / sector_score_max 传 None 时从 registry "scoring" 段加载，缺失回退
    默认（全开 + 0-100），行为与历史一致。
    """
    if enriched is None:
        enriched = _load_json(SCREENING_DIR / f"{date}_candidates_enriched.json", {})
    if sector_states is None:
        sector_states = _load_json(SECTORS_DIR / f"{date}_sector_state.json", [])
    if amv_state is None:
        market = _load_json(MARKET_DIR / f"{date}_market_timing_input.json", {})
        amv_state = str((market.get("amv_0") or {}).get("effective_state") or "")
    if cz_preference is None:
        cz_preference = load_cz_sector_preference() or {}
    cz_status = "ok" if cz_preference else "missing"

    if cap_rules is None or sector_score_max is None:
        scoring_cfg = _load_scoring_config()
        if cap_rules is None:
            cap_rules = scoring_cfg.get("cap_rules")
        if sector_score_max is None:
            sector_score_max = scoring_cfg.get("sector_score_max", SECTOR_SCORE_MAX)
    effective_caps = resolve_cap_rules(cap_rules)

    result: dict[str, Any] = {
        "date": date,
        "status": "ok",
        "degraded_reason": "",
        "source": "screening_chain_v1",
        "amv_state": amv_state or "未知",
        "market_permission": market_permission(amv_state),
        "cz_sector_status": cz_status,
        "cap_rules": effective_caps,
        "sector_score_max": float(sector_score_max),
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
    if cz_status == "missing":
        # 名单缺失：cz_sector 一律 neutral（不起作用），在 status/degraded_reason 注明
        if result["status"] == "ok":
            result["status"] = "partial"
        note = "cz_sector_preference_missing"
        result["degraded_reason"] = (
            f"{result['degraded_reason']};{note}" if result["degraded_reason"] else note
        )
    if not amv_state:
        # market_timing 缺失：按保守处理（不放宽任何 cap，视同仅低吸），
        # 但必须显式标注，不得静默。
        if result["status"] == "ok":
            result["status"] = "partial"
        note = "market_timing_missing"
        result["degraded_reason"] = (
            f"{result['degraded_reason']};{note}" if result["degraded_reason"] else note
        )

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
        cz_sector = cz_sector_of(cand.get("sector", ""), cz_preference)
        scored = score_candidate(cand, entry, amv_state, cz_sector=cz_sector,
                                 cap_rules=cap_rules, sector_score_max=sector_score_max)
        result["candidates"].append(scored)
        result["bucket_counts"][scored["bucket"]] += 1

    result["candidates"].sort(key=lambda x: (BUCKET_ORDER.index(x["bucket"]), -x["score"], x["code"]))
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
