# -*- coding: utf-8 -*-
"""candidate_table 渲染测试(重点:基本面牛股候选观察区)。"""
from screening import candidate_table as ct


def _cand(code, name, sector, bucket, tier, aligned, bull, tech=55.0, cap="中", stop=10.0):
    return {"code": code, "name": name, "sector": sector, "bucket": bucket,
            "fundamental_quality": {"tier": tier},
            "resonance_4leg": {"sector": aligned >= 2, "technical": aligned >= 2,
                               "aligned": aligned, "label": f"{aligned}面",
                               "bull_candidate": bull},
            "score_detail": {"technical_score": tech, "total": tech + 20},
            "capital_intent": {"level": cap}, "stop_loss_ref": {"price": stop}}


def test_bull_watchlist_section():
    pool = {"status": "ok", "candidates": [
        _cand("600000", "甲", "半导体", "A", "优", 4, True),      # 四面共振→🐂可买
        _cand("000002", "乙", "AI", "C", "优", 3, False),        # 基本面优+板块+技术,空头→待做多
        _cand("300001", "丙", "X", "D", "差", 1, False),          # 差/不共振→不入观察区
    ]}
    md = ct.render_table(pool, "2026-07-28")
    assert "🐂 基本面牛股候选" in md
    sec = md.split("## 🐂 基本面牛股候选")[1].split("\n## ")[0]
    assert "600000" in sec and "000002" in sec and "300001" not in sec
    assert "🐂可买" in sec and "待0AMV做多" in sec


def test_bull_watchlist_empty():
    pool = {"status": "ok", "candidates": [_cand("300001", "丙", "X", "D", "差", 1, False)]}
    md = ct.render_table(pool, "2026-07-28")
    assert "（今日无基本面牛股候选）" in md
