# -*- coding: utf-8 -*-
"""Tests for B1/CZ scoring rules in screening.score_candidates."""
from screening import score_candidates as sc


def _cand(**extra):
    cand = {
        "code": "600000",
        "name": "示例",
        "sector": "半导体/芯片/存储/封测",
        "theme_id": "semiconductor_chip_memory_packaging",
        "formula_hits": ["UPN_3"],
        "patterns": {"bbi_above": True, "j_low": True, "volume_contraction": True,
                     "reversal_k_candidate": True, "relative_strength_strong": True},
        "daily_j": 10.0,
        "stop_loss_ref": {"price": 10.0, "basis": "近10日最低价"},
        "is_holding": False,
        # 资金意图默认强（量能持续主线只加资金意图分、不加技术分）→ base bucket = 强×强 = A
        "volume_sustain": {"status": "mainline_confirmed"},
    }
    cand.update(extra)
    return cand


SECTOR_STRONG = {"state": "主升", "score": 80, "sector": "半导体/芯片/存储/封测"}
PREF = {"favored": ["半导体", "芯片"], "avoid": ["稀土", "白酒"]}


def test_sprint_wave_caps_at_b_and_no_buy_plan():
    scored = sc.score_candidate(_cand(wave={"wave_type": "sprint", "available": True}),
                                SECTOR_STRONG, "做多")
    assert scored["bucket"] == "B"
    assert scored["next_step"] != "generate_buy_plan"
    assert "sprint_wave_first_b1_forbidden" in scored["risk_flags"]


def test_volume_sustain_retreat_caps_at_c():
    scored = sc.score_candidate(_cand(volume_sustain={"status": "retreat", "available": True}),
                                SECTOR_STRONG, "做多")
    assert scored["bucket"] == "C"
    assert "main_force_retreat" in scored["risk_flags"]


def test_cz_avoid_sector_forces_d():
    scored = sc.score_candidate(_cand(), SECTOR_STRONG, "做多", cz_sector="avoid")
    assert scored["bucket"] == "D"
    assert scored["next_step"] == "avoid"
    assert "cz_avoid_sector" in scored["risk_flags"]


def test_non_one_wave_revoked_caps_at_c():
    scored = sc.score_candidate(_cand(non_one_wave={"status": "revoked", "available": True}),
                                SECTOR_STRONG, "做多")
    assert scored["bucket"] == "C"
    assert "non_one_wave_revoked" in scored["risk_flags"]


def test_no_new_rules_keeps_a():
    scored = sc.score_candidate(_cand(wave={"wave_type": "buildup", "available": True},
                                      volume_sustain={"status": "mainline_confirmed"},
                                      non_one_wave={"status": "confirmed"}),
                                SECTOR_STRONG, "做多", cz_sector="favored")
    assert scored["bucket"] == "A"
    assert scored["next_step"] == "generate_buy_plan"


def test_bonus_factor_contrib_recorded():
    cand = _cand(
        five_day_entry={"hit": True, "available": True},
        leader_volume={"hit": True, "available": True},
        bottom_volume={"hit": True, "available": True},
        repair_signals={"signals": ["j_turn_up", "rs_turn_strong"]},
        non_one_wave={"status": "confirmed"},
    )
    scored = sc.score_candidate(cand, SECTOR_STRONG, "做多")
    contrib = scored["score_detail"]["factor_contrib"]
    # 反转K复合分取代子项：j_low / volume_contraction 不再单独计分
    assert "j_low" not in contrib and "volume_contraction" not in contrib
    assert contrib["reversal_k_candidate"] == 25
    assert contrib["reversal_k_replaces"] == "j_low+volume_contraction"
    assert contrib["five_day_entry"] == 8
    assert contrib["leader_volume"] == 6
    assert contrib["bottom_volume"] == 6
    assert contrib["repair_signals"] == 6  # 每项+3，上限+6
    assert contrib["non_one_wave_confirmed"] == 5
    # 基础 bbi25 + 反转K25 + 强RS15 + 8+6+6+6+5 = 96
    assert scored["score_detail"]["technical_score"] == 96


def test_cz_sector_of_matching():
    assert sc.cz_sector_of("半导体/芯片/存储/封测", PREF) == "favored"
    assert sc.cz_sector_of("稀土", PREF) == "avoid"
    assert sc.cz_sector_of("证券/券商/金融风险偏好", PREF) == "neutral"
    assert sc.cz_sector_of("未知", PREF) == "neutral"
    assert sc.cz_sector_of("半导体", None) == "neutral"
    # avoid 优先（保守）：同时含白/黑关键词时判 avoid
    assert sc.cz_sector_of("稀土半导体", PREF) == "avoid"


def test_score_all_preference_missing_degrades():
    enriched = {"status": "ok", "candidates": [_cand()]}
    states = [{**SECTOR_STRONG, "theme_id": "semiconductor_chip_memory_packaging"}]
    result = sc.score_all("2026-07-21", enriched=enriched, sector_states=states,
                          amv_state="做多", cz_preference={})
    assert result["cz_sector_status"] == "missing"
    assert result["status"] == "partial"
    assert "cz_sector_preference_missing" in result["degraded_reason"]
    assert result["candidates"][0]["cz_sector"] == "neutral"


def test_score_all_avoid_theme_goes_d():
    cand = _cand(sector="稀土", theme_id="rare_earth")
    enriched = {"status": "ok", "candidates": [cand]}
    states = [{"state": "主升", "score": 80, "sector": "稀土", "theme_id": "rare_earth"}]
    result = sc.score_all("2026-07-21", enriched=enriched, sector_states=states,
                          amv_state="做多", cz_preference=PREF)
    assert result["candidates"][0]["cz_sector"] == "avoid"
    assert result["candidates"][0]["bucket"] == "D"
    assert result["bucket_counts"]["D"] == 1


# ---------- P1: 待回测封顶规则可配置化（默认全开＝历史行为） ----------

def test_cap_rules_default_still_fire():
    # 不传 cap_rules → 默认全开，sprint 仍封顶 B（回归保护）
    scored = sc.score_candidate(_cand(wave={"wave_type": "sprint", "available": True}),
                                SECTOR_STRONG, "做多")
    assert scored["bucket"] == "B"
    assert "sprint_wave_first_b1_forbidden" in scored["risk_flags"]


def test_cap_rule_disabled_sprint_keeps_a():
    scored = sc.score_candidate(
        _cand(wave={"wave_type": "sprint", "available": True}),
        SECTOR_STRONG, "做多", cap_rules={"sprint_wave": False})
    assert scored["bucket"] == "A"                       # 不再降档
    assert scored["next_step"] == "generate_buy_plan"    # 双保险也随开关关闭
    assert "sprint_wave_detected_cap_disabled" in scored["risk_flags"]
    assert "sprint_wave_first_b1_forbidden" not in scored["risk_flags"]


def test_cap_rules_disabled_retreat_revoked_avoid_keep_a():
    scored = sc.score_candidate(
        _cand(volume_sustain={"status": "retreat", "available": True},
              non_one_wave={"status": "revoked", "available": True},
              zhixing={"available": True, "qsx_gt_dks": True, "close_above_qsx": True}),
        SECTOR_STRONG, "做多", cz_sector="avoid",
        cap_rules={"volume_retreat": False, "non_one_wave_revoked": False,
                   "cz_avoid_sector": False})
    assert scored["bucket"] == "A"  # 三条降档全关 + 资金意图强 → 保持基础 强×强＝A
    assert "main_force_retreat_cap_disabled" in scored["risk_flags"]
    assert "non_one_wave_revoked_cap_disabled" in scored["risk_flags"]
    assert "cz_avoid_sector_cap_disabled" in scored["risk_flags"]


def test_score_detail_records_effective_cap_rules():
    scored = sc.score_candidate(_cand(), SECTOR_STRONG, "做多",
                                cap_rules={"sprint_wave": False})
    caps = scored["score_detail"]["cap_rules"]
    assert caps["sprint_wave"] is False
    assert caps["volume_retreat"] is True  # 未指定 → 保持默认开


# ---------- P2: sector_score 量纲归一化 + clamp ----------

def test_sector_score_normalized_and_clamped():
    over = sc.score_candidate(_cand(), {"state": "主升", "score": 200}, "做多")
    assert over["score_detail"]["sector_score"] == 100.0      # 越界→clamp 100
    assert over["score_detail"]["sector_score_raw"] == 200
    assert over["sector_heat_filter"]["sector_score"] == 100.0

    neg = sc.score_candidate(_cand(), {"state": "主升", "score": -5}, "做多")
    assert neg["score_detail"]["sector_score"] == 0.0          # 负值→clamp 0

    none = sc.score_candidate(_cand(), {"state": "主升"}, "做多")
    assert none["score_detail"]["sector_score"] == 0.0         # 缺 score→0
    assert none["score_detail"]["sector_score_raw"] is None


def test_sector_score_custom_scale_normalizes():
    scored = sc.score_candidate(_cand(), {"state": "主升", "score": 8}, "做多",
                                sector_score_max=10)
    assert scored["score_detail"]["sector_score"] == 80.0     # 8/10*100


def test_score_all_records_cap_rules_and_sector_max():
    enriched = {"status": "ok", "candidates": [_cand()]}
    states = [{**SECTOR_STRONG, "theme_id": "semiconductor_chip_memory_packaging"}]
    result = sc.score_all("2026-07-21", enriched=enriched, sector_states=states,
                          amv_state="做多", cap_rules={"sprint_wave": False})
    assert result["cap_rules"]["sprint_wave"] is False
    assert result["cap_rules"]["volume_retreat"] is True
    assert result["sector_score_max"] == 100.0
