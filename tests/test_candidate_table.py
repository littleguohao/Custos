# -*- coding: utf-8 -*-
"""candidate_table 渲染测试(重点:基本面牛股候选观察区 + 共振受限重点研究区)。"""
from screening import candidate_table as ct


def _cand(code, name, sector, bucket, tier, aligned, bull, tech=55.0, cap="中", stop=10.0,
          risk_flags=None):
    return {"code": code, "name": name, "sector": sector, "bucket": bucket,
            "fundamental_quality": {"tier": tier},
            "resonance_4leg": {"sector": aligned >= 2, "technical": aligned >= 2,
                               "aligned": aligned, "label": f"{aligned}面",
                               "bull_candidate": bull},
            "score_detail": {"technical_score": tech, "total": tech + 20},
            "capital_intent": {"level": cap}, "stop_loss_ref": {"price": stop},
            "risk_flags": risk_flags or []}


def test_bull_watchlist_section():
    pool = {"status": "ok", "candidates": [
        _cand("600000", "甲", "半导体", "A", "优", 4, True),      # 四面共振+A→🐂可买
        _cand("000002", "乙", "AI", "C", "优", 3, False),        # C层→进 🔍 受限区(不进🐂主表)
        _cand("300001", "丙", "X", "D", "差", 1, False),          # 差/不共振→两个区都不入
    ]}
    md = ct.render_table(pool, "2026-07-28")
    assert "🐂 基本面牛股候选" in md
    sec = md.split("## 🐂 基本面牛股候选")[1].split("\n## ")[0]
    assert "600000" in sec and "000002" not in sec and "300001" not in sec
    assert "🐂可买" in sec
    capped = md.split("## 🔍 共振成立但分层受限")[1].split("\n## ")[0]
    assert "000002" in capped and "300001" not in capped   # C层共振标的在重点观察区,不埋没


def test_bull_watchlist_empty():
    pool = {"status": "ok", "candidates": [_cand("300001", "丙", "X", "D", "差", 1, False)]}
    md = ct.render_table(pool, "2026-07-28")
    assert "（今日无基本面牛股候选）" in md


def test_capped_resonance_listed_separately_not_buyable():
    # 四面共振但被风控压到 C/D → 单列 🔍 重点研究观察区,带受限因素,绝不标"可买"
    pool = {"status": "ok", "candidates": [
        _cand("600000", "甲", "半导体", "A", "优", 4, True),
        _cand("600003", "丁", "高位股", "D", "优", 4, True, risk_flags=["distribution_high"]),
        _cand("600005", "戊", "分歧股", "C", "优", 4, True, risk_flags=["macd_top_divergence"]),
    ]}
    md = ct.render_table(pool, "2026-07-28")
    main_sec = md.split("## 🐂 基本面牛股候选")[1].split("\n## ")[0]
    assert "600003" not in main_sec and "600005" not in main_sec   # 受限不进🐂主表
    capped = md.split("## 🔍 共振成立但分层受限")[1].split("\n## ")[0]
    assert "重点研究观察" in md and "非可买" in md
    row_d = [ln for ln in capped.splitlines() if "600003" in ln][0]
    row_c = [ln for ln in capped.splitlines() if "600005" in ln][0]
    assert "distribution_high" in row_d and "🐂可买" not in row_d
    assert "macd_top_divergence" in row_c and "🐂可买" not in row_c


def test_bull_mark_b_bucket_is_observe_not_buyable():
    # B 层四腿命中:留主表但标"观察价位"(next_step=observe_price),不标可买
    pool = {"status": "ok", "candidates": [_cand("600007", "己", "X", "B", "优", 4, True)]}
    sec = ct.render_table(pool, "2026-07-28").split("## 🐂 基本面牛股候选")[1].split("\n## ")[0]
    assert "🐂观察价位(B)" in sec and "🐂可买" not in sec


def test_mainline_fingerprint_section(monkeypatch, tmp_path):
    """candidate_table 渲染当日主线指纹(best-effort);构造 members 并指向临时 market 目录。"""
    from screening import candidate_table as ct
    import json as _json
    market = tmp_path / "market"
    market.mkdir()
    members = {"880201.SH": ["600000", "600001", "600002", "600003", "600004", "600005", "600006", "600007"],
               "880300.SH": ["000%03d" % i for i in range(120)]}
    (market / "sector_members.json").write_text(_json.dumps(members), encoding="utf-8")
    # 把 STOCK_POOL_DIR.parent 指到 tmp_path,使 helper 找到 market/sector_members.json
    monkeypatch.setattr(ct, "STOCK_POOL_DIR", tmp_path / "stock_pool")
    cands = [{"code": "600000"}, {"code": "600001"}, {"code": "600002"}, {"code": "000001"}]
    section = ct._mainline_fingerprint_section(cands)
    assert any("当日主线指纹" in ln for ln in section)
    assert any("非进场过滤" in ln for ln in section)
