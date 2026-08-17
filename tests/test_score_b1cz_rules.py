# -*- coding: utf-8 -*-
"""Tests for B1/CZ scoring rules in screening.score_candidates."""

from custos.pipeline.screening import score_candidates as sc


def _cand(**extra):
    cand = {
        "code": "600000",
        "name": "示例",
        "sector": "半导体/芯片/存储/封测",
        "theme_id": "semiconductor_chip_memory_packaging",
        "formula_hits": ["UPN_3"],
        "patterns": {
            "bbi_above": True,
            "j_low": True,
            "volume_contraction": True,
            "reversal_k_candidate": True,
            "relative_strength_strong": True,
        },
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
    scored = sc.score_candidate(
        _cand(wave={"wave_type": "sprint", "available": True}), SECTOR_STRONG, "做多"
    )
    assert scored["bucket"] == "B"
    assert scored["next_step"] != "buy_review"
    assert "sprint_wave_first_b1_forbidden" in scored["risk_flags"]


def test_volume_sustain_retreat_no_longer_caps():
    """v0.60（2026-08-14，owner）：量能撤退封顶去掉——与「缩量回调是 B1 健康形态」
    语义冲突（同一缩量事实一边加分一边封顶）。检出仍记录（cap_disabled 证据 flag），
    分层按矩阵自然落。"""
    scored = sc.score_candidate(
        _cand(volume_sustain={"status": "retreat", "available": True}),
        SECTOR_STRONG,
        "做多",
    )
    assert "main_force_retreat" not in scored["risk_flags"]
    assert "main_force_retreat_cap_disabled" in scored["risk_flags"]
    # v0.61 后 base patterns 技术分 72（强）× 资金 retreat 后 3 分（中）-> 矩阵自然落 B
    # （v0.58 时 base 59 是中×中=C；矩阵自然落，不是封顶压的）
    assert scored["bucket"] == "B"


def test_cz_avoid_sector_forces_d():
    scored = sc.score_candidate(_cand(), SECTOR_STRONG, "做多", cz_sector="avoid")
    assert scored["bucket"] == "D"
    assert scored["next_step"] == "avoid"
    assert "cz_avoid_sector" in scored["risk_flags"]


def test_non_one_wave_revoked_caps_at_c():
    scored = sc.score_candidate(
        _cand(non_one_wave={"status": "revoked", "available": True}),
        SECTOR_STRONG,
        "做多",
    )
    assert scored["bucket"] == "C"
    assert "non_one_wave_revoked" in scored["risk_flags"]


def test_no_new_rules_keeps_a():
    scored = sc.score_candidate(
        _cand(
            wave={"wave_type": "buildup", "available": True},
            volume_sustain={"status": "mainline_confirmed"},
            non_one_wave={"status": "confirmed"},
            # v0.58：base patterns 只值 54（中），补两个资金轴中性的加分凑技术强
            weekly_j_low=True,
            adx=61.0,
        ),
        SECTOR_STRONG,
        "做多",
        cz_sector="favored",
    )
    assert scored["bucket"] == "A"
    assert scored["next_step"] == "buy_review"


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
    # v0.58：反转K 不再取代子项——j_low / volume_contraction 照常独算
    assert contrib["j_low"] == 24 and contrib["volume_contraction"] == 15
    assert contrib["reversal_k_candidate"] == 4
    assert "reversal_k_replaces" not in contrib
    assert contrib["five_day_entry"] == 8
    assert contrib["leader_volume"] == 6
    assert contrib["bottom_volume"] == 10  # v0.58：6→10
    assert contrib["repair_signals"] == 8  # 每项+4，上限+8（v0.61：3/6 -> 4/8）
    assert contrib["non_one_wave_confirmed"] == 5
    # bbi5 + 反转K4 + j_low24 + 缩量15 + 强RS15 + 8+6+10+8+5 = 100 恰好（v0.63）
    assert scored["score_detail"]["technical_score"] == 100


def test_v058_new_score_items():
    """v0.58（2026-08-14，owner 定权重）：周日共振/ADX/知行三态/阴阳量/出货减分。"""
    base = sc.technical_score({"code": "600000", "patterns": {}})[0]
    assert base == 0

    # 周线 J<13（周日共振）+5
    s, _, c = sc.technical_score({"code": "600000", "weekly_j_low": True})
    assert s == 5 and c["weekly_j_low"] == 5

    # ADX>60 +5；ADX≤60 不加
    s, _, c = sc.technical_score({"code": "600000", "adx": 61.2})
    assert s == 5 and c["adx_gt_60"] == 5
    s, _, _ = sc.technical_score({"code": "600000", "adx": 60.0})
    assert s == 0

    # 知行三态：多头+9（v0.61）；骑线再 +5；回踩区（QSX>C≥DKS）+5 且与骑线互斥
    zx = {"available": True, "qsx_gt_dks": True, "close_above_qsx": True}
    s, _, c = sc.technical_score({"code": "600000", "zhixing": zx})
    assert s == 14 and c["zhixing_bull"] == 9 and c["zhixing_close_above_qsx"] == 5
    zx_band = {
        "available": True,
        "qsx_gt_dks": True,
        "close_above_qsx": False,
        "close_above_dks": True,
    }
    s, _, c = sc.technical_score({"code": "600000", "zhixing": zx_band})
    assert s == 14 and c["zhixing_in_qsx_dks_band"] == 5

    # 2026-08-16 review 修复：空头排列（QSX<DKS）下价站 QSX 不算骑线，不加 +5
    zx_bear = {"available": True, "qsx_gt_dks": False, "close_above_qsx": True}
    s, _, c = sc.technical_score({"code": "600000", "zhixing": zx_bear})
    assert s == 0 and "zhixing_close_above_qsx" not in c

    # 阴阳量：阳量>阴量 +7；阴量>阳量 −5；平局不加不减（2026-08-16 review 修复，
    # 此前平局被 bull_gt_bear=False 当空方 −5）
    s, _, c = sc.technical_score(
        {
            "code": "600000",
            "volume_yy": {"available": True, "bull_vol": 120.0, "bear_vol": 100.0},
        }
    )
    assert s == 7 and c["volume_yy_bull"] == 7
    s, _, c = sc.technical_score(
        {
            "code": "600000",
            "patterns": {"j_low": True},
            "volume_yy": {"available": True, "bull_vol": 90.0, "bear_vol": 100.0},
        }
    )
    assert s == 19 and c["volume_yy_bear"] == -5  # 24 − 5（v0.63：j_low 24）
    s, _, c = sc.technical_score(
        {
            "code": "600000",
            "patterns": {"j_low": True},
            "volume_yy": {"available": True, "bull_vol": 100.0, "bear_vol": 100.0},
        }
    )
    assert s == 24 and "volume_yy_bear" not in c  # 平局中性

    # 出货形态分数层减分（封顶规则不在这里）：watch −10 / high −20；
    # available 守卫（2026-08-16 review 修复）：未评估的残留 risk_level 不减分
    s, _, c = sc.technical_score(
        {
            "code": "600000",
            "patterns": {"j_low": True},
            "distribution": {"available": True, "risk_level": "watch"},
        }
    )
    assert s == 14 and c["distribution_watch"] == -10  # 24 − 10
    s, _, c = sc.technical_score(
        {
            "code": "600000",
            "patterns": {"j_low": True},
            "distribution": {"available": True, "risk_level": "high"},
        }
    )
    assert s == 4 and c["distribution_high"] == -20  # 24 − 20
    s, _, c = sc.technical_score(
        {
            "code": "600000",
            "patterns": {"j_low": True},
            "distribution": {"risk_level": "high"},  # 无 available ⇒ 未评估
        }
    )
    assert s == 24 and "distribution_high" not in c

    # 负分下限截断（2026-08-16 review 修复）：纯负分组合展示分不跌破 0
    s, _, c = sc.technical_score(
        {
            "code": "600000",
            "distribution": {"available": True, "risk_level": "high"},
            "volume_yy": {"available": True, "bull_vol": 1.0, "bear_vol": 2.0},
        }
    )
    assert s == 0 and c["distribution_high"] == -20 and c["volume_yy_bear"] == -5


def test_v060_macd_score_items():
    """v0.60（2026-08-14，owner）：MACD 水上/日线红柱增长/周月红柱同增各 +5。"""
    mt = {
        "available": True,
        "above_water": True,
        "bar_grow": True,
        "wm_bar_grow": True,
    }
    s, _, c = sc.technical_score({"code": "600000", "macd_technics": mt})
    assert s == 17  # v0.61：水上 7 + 红柱 5 + 周月 5
    assert c["macd_above_water"] == 7
    assert c["macd_bar_grow"] == 5
    assert c["macd_wm_bar_grow"] == 5
    # 部分命中只加部分分
    s, _, c = sc.technical_score(
        {"code": "600000", "macd_technics": {"available": True, "above_water": True}}
    )
    assert s == 7 and "macd_bar_grow" not in c
    # available=False 一律不加
    s, _, _ = sc.technical_score(
        {"code": "600000", "macd_technics": mt | {"available": False}}
    )
    assert s == 0


def test_v064_healthy_pullback_pack_bonus():
    """v0.64（owner 定向）：J低位∧缩量回调∧知行多头 三腿齐 -> 组合奖 +9；缺一腿不发。

    离线模拟依据：8 只正例 7/8 三腿齐（全回 ≥70），08-14 池仅 ~1/3 命中
    --组合只奖励完整 B1 健康回调结构，避免 v0.61 式单因子普涨。
    """
    base = {
        "code": "600000",
        "patterns": {"j_low": True},
        "pullback_shrink": {"hit": True},
        "zhixing": {"available": True, "qsx_gt_dks": True},
    }
    s, _, c = sc.technical_score(base)
    # j_low 24 + 缩量回调 5 + 知行多头 9 + 组合包 9 = 47
    assert s == 47 and c["b1_healthy_pullback_pack"] == 9
    # 缺缩量回调腿：j 24 + 知行 9 = 33，无包
    s2, _, c2 = sc.technical_score({**base, "pullback_shrink": {"hit": False}})
    assert s2 == 33 and "b1_healthy_pullback_pack" not in c2
    # 缺知行多头腿：j 24 + 缩量 5 = 29，无包
    s3, _, c3 = sc.technical_score(
        {**base, "zhixing": {"available": True, "qsx_gt_dks": False}}
    )
    assert s3 == 29 and "b1_healthy_pullback_pack" not in c3
    # 缺 J 低位腿（主池不会出现，防御性钉住）：知行 9 + 缩量 5 = 14，无包
    s4, _, c4 = sc.technical_score({**base, "patterns": {}})
    assert s4 == 14 and "b1_healthy_pullback_pack" not in c4


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
    result = sc.score_all(
        "2026-07-21",
        enriched=enriched,
        sector_states=states,
        amv_state="做多",
        cz_preference={},
    )
    assert result["cz_sector_status"] == "missing"
    assert result["status"] == "partial"
    assert "cz_sector_preference_missing" in result["degraded_reason"]
    assert result["candidates"][0]["cz_sector"] == "neutral"


def test_score_all_avoid_theme_goes_d():
    cand = _cand(sector="稀土", theme_id="rare_earth")
    enriched = {"status": "ok", "candidates": [cand]}
    states = [
        {"state": "主升", "score": 80, "sector": "稀土", "theme_id": "rare_earth"}
    ]
    result = sc.score_all(
        "2026-07-21",
        enriched=enriched,
        sector_states=states,
        amv_state="做多",
        cz_preference=PREF,
    )
    assert result["candidates"][0]["cz_sector"] == "avoid"
    assert result["candidates"][0]["bucket"] == "D"
    assert result["bucket_counts"]["D"] == 1


# ---------- P1: 待回测封顶规则可配置化（默认全开＝历史行为） ----------


def test_cap_rules_default_still_fire():
    # 不传 cap_rules → 默认全开，sprint 仍封顶 B（回归保护）
    scored = sc.score_candidate(
        _cand(wave={"wave_type": "sprint", "available": True}), SECTOR_STRONG, "做多"
    )
    assert scored["bucket"] == "B"
    assert "sprint_wave_first_b1_forbidden" in scored["risk_flags"]


def test_cap_rule_disabled_sprint_keeps_a():
    scored = sc.score_candidate(
        # v0.58：同 test_no_new_rules_keeps_a，补资金轴中性加分凑技术强
        _cand(
            wave={"wave_type": "sprint", "available": True},
            weekly_j_low=True,
            adx=61.0,
        ),
        SECTOR_STRONG,
        "做多",
        cap_rules={"sprint_wave": False},
    )
    assert scored["bucket"] == "A"  # 不再降档
    assert scored["next_step"] == "buy_review"  # 双保险也随开关关闭
    assert "sprint_wave_detected_cap_disabled" in scored["risk_flags"]
    assert "sprint_wave_first_b1_forbidden" not in scored["risk_flags"]


def test_cap_rules_disabled_retreat_revoked_avoid_keep_a():
    scored = sc.score_candidate(
        _cand(
            volume_sustain={"status": "retreat", "available": True},
            non_one_wave={"status": "revoked", "available": True},
            zhixing={"available": True, "qsx_gt_dks": True, "close_above_qsx": True},
        ),
        SECTOR_STRONG,
        "做多",
        cz_sector="avoid",
        cap_rules={
            "volume_retreat": False,
            "non_one_wave_revoked": False,
            "cz_avoid_sector": False,
        },
    )
    assert scored["bucket"] == "A"  # 三条降档全关 + 资金意图强 → 保持基础 强×强＝A
    assert "main_force_retreat_cap_disabled" in scored["risk_flags"]
    assert "non_one_wave_revoked_cap_disabled" in scored["risk_flags"]
    assert "cz_avoid_sector_cap_disabled" in scored["risk_flags"]


def test_score_detail_records_effective_cap_rules():
    scored = sc.score_candidate(
        _cand(), SECTOR_STRONG, "做多", cap_rules={"sprint_wave": False}
    )
    caps = scored["score_detail"]["cap_rules"]
    assert caps["sprint_wave"] is False
    assert caps["volume_retreat"] is False  # v0.60：默认关（owner）


# ---------- P2: sector_score 量纲归一化 + clamp ----------


def test_sector_score_normalized_and_clamped():
    over = sc.score_candidate(_cand(), {"state": "主升", "score": 200}, "做多")
    assert over["score_detail"]["sector_score"] == 100.0  # 越界→clamp 100
    assert over["score_detail"]["sector_score_raw"] == 200
    assert over["sector_heat_filter"]["sector_score"] == 100.0

    neg = sc.score_candidate(_cand(), {"state": "主升", "score": -5}, "做多")
    assert neg["score_detail"]["sector_score"] == 0.0  # 负值→clamp 0

    none = sc.score_candidate(_cand(), {"state": "主升"}, "做多")
    assert none["score_detail"]["sector_score"] == 0.0  # 缺 score→0
    assert none["score_detail"]["sector_score_raw"] is None


def test_sector_score_custom_scale_normalizes():
    scored = sc.score_candidate(
        _cand(), {"state": "主升", "score": 8}, "做多", sector_score_max=10
    )
    assert scored["score_detail"]["sector_score"] == 80.0  # 8/10*100


def test_score_all_records_cap_rules_and_sector_max():
    enriched = {"status": "ok", "candidates": [_cand()]}
    states = [{**SECTOR_STRONG, "theme_id": "semiconductor_chip_memory_packaging"}]
    result = sc.score_all(
        "2026-07-21",
        enriched=enriched,
        sector_states=states,
        amv_state="做多",
        cap_rules={"sprint_wave": False},
    )
    assert result["cap_rules"]["sprint_wave"] is False
    assert result["cap_rules"]["volume_retreat"] is False  # v0.60：默认关（owner）
    assert result["sector_score_max"] == 100.0
