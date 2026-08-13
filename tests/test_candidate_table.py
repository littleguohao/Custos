# -*- coding: utf-8 -*-
"""candidate_table 渲染测试(重点:基本面牛股候选观察区 + 共振受限重点研究区)。"""

from custos.pipeline.screening import candidate_table as ct


def _cand(
    code,
    name,
    sector,
    bucket,
    tier,
    aligned,
    bull,
    tech=55.0,
    cap="中",
    stop=10.0,
    risk_flags=None,
):
    return {
        "code": code,
        "name": name,
        "sector": sector,
        "bucket": bucket,
        "fundamental_quality": {"tier": tier},
        "resonance_4leg": {
            "sector": aligned >= 2,
            "technical": aligned >= 2,
            "aligned": aligned,
            "label": f"{aligned}面",
            "bull_candidate": bull,
        },
        "score_detail": {"technical_score": tech, "total": tech + 20},
        "capital_intent": {"level": cap},
        "stop_loss_ref": {"price": stop},
        "risk_flags": risk_flags or [],
    }


def test_bull_watchlist_section():
    pool = {
        "status": "ok",
        "candidates": [
            _cand("600000", "甲", "半导体", "A", "优", 4, True),  # 四面共振+A→🐂可买
            _cand(
                "000002", "乙", "AI", "C", "优", 3, False
            ),  # C层→进 🔍 受限区(不进🐂主表)
            _cand("300001", "丙", "X", "D", "差", 1, False),  # 差/不共振→两个区都不入
        ],
    }
    md = ct.render_table(pool, "2026-07-28")
    assert "🐂 基本面牛股候选" in md
    sec = md.split("## 🐂 基本面牛股候选")[1].split("\n## ")[0]
    assert "600000" in sec and "000002" not in sec and "300001" not in sec
    assert "🐂可买" in sec
    capped = md.split("## 🔍 共振成立但分层受限")[1].split("\n## ")[0]
    assert (
        "000002" in capped and "300001" not in capped
    )  # C层共振标的在重点观察区,不埋没


def test_bull_watchlist_empty():
    pool = {
        "status": "ok",
        "candidates": [_cand("300001", "丙", "X", "D", "差", 1, False)],
    }
    md = ct.render_table(pool, "2026-07-28")
    assert "（今日无基本面牛股候选）" in md


def test_capped_resonance_listed_separately_not_buyable():
    # 四面共振但被风控压到 C/D → 单列 🔍 重点研究观察区,带受限因素,绝不标"可买"
    pool = {
        "status": "ok",
        "candidates": [
            _cand("600000", "甲", "半导体", "A", "优", 4, True),
            _cand(
                "600003",
                "丁",
                "高位股",
                "D",
                "优",
                4,
                True,
                risk_flags=["distribution_high"],
            ),
            _cand(
                "600005",
                "戊",
                "分歧股",
                "C",
                "优",
                4,
                True,
                risk_flags=["macd_top_divergence"],
            ),
        ],
    }
    md = ct.render_table(pool, "2026-07-28")
    main_sec = md.split("## 🐂 基本面牛股候选")[1].split("\n## ")[0]
    assert "600003" not in main_sec and "600005" not in main_sec  # 受限不进🐂主表
    capped = md.split("## 🔍 共振成立但分层受限")[1].split("\n## ")[0]
    assert "重点研究观察" in md and "非可买" in md
    row_d = [ln for ln in capped.splitlines() if "600003" in ln][0]
    row_c = [ln for ln in capped.splitlines() if "600005" in ln][0]
    assert "distribution_high" in row_d and "🐂可买" not in row_d
    assert "macd_top_divergence" in row_c and "🐂可买" not in row_c


def test_bull_mark_b_bucket_is_observe_not_buyable():
    # B 层四腿命中:留主表但标"观察价位"(next_step=observe_price),不标可买
    pool = {
        "status": "ok",
        "candidates": [_cand("600007", "己", "X", "B", "优", 4, True)],
    }
    sec = (
        ct.render_table(pool, "2026-07-28")
        .split("## 🐂 基本面牛股候选")[1]
        .split("\n## ")[0]
    )
    assert "🐂观察价位(B)" in sec and "🐂可买" not in sec


def test_mainline_fingerprint_section(monkeypatch, tmp_path):
    """candidate_table 渲染当日主线指纹(best-effort);构造 members 并指向临时 market 目录。"""
    from custos.pipeline.screening import candidate_table as ct
    import json as _json

    market = tmp_path / "market"
    market.mkdir()
    members = {
        "880201.SH": [
            "600000",
            "600001",
            "600002",
            "600003",
            "600004",
            "600005",
            "600006",
            "600007",
        ],
        "880300.SH": ["000%03d" % i for i in range(120)],
    }
    (market / "sector_members.json").write_text(_json.dumps(members), encoding="utf-8")
    # 把 STOCK_POOL_DIR.parent 指到 tmp_path,使 helper 找到 market/sector_members.json
    monkeypatch.setattr(ct, "STOCK_POOL_DIR", tmp_path / "stock_pool")
    cands = [
        {"code": "600000"},
        {"code": "600001"},
        {"code": "600002"},
        {"code": "000001"},
    ]
    section = ct._mainline_fingerprint_section(cands)
    assert any("当日主线指纹" in ln for ln in section)
    assert any("非进场过滤" in ln for ln in section)


def test_bear_market_outpost_section():
    # 空头期:基本面优+技术强但板块腿未到位 → 进 📡 前哨区(非可买);已在🐂/🔍区的不重复列
    outpost = _cand("600100", "哨", "半导体", "D", "优", 3, False)
    outpost["resonance_4leg"]["sector"] = False  # 技术强但板块腿未到位
    pool = {
        "status": "ok",
        "amv_state": "空头",
        "candidates": [
            outpost,  # →📡
            _cand("600000", "甲", "半导体", "A", "优", 4, True),  # 已在🐂区→不进📡
            _cand(
                "600003",
                "丁",
                "高位股",
                "D",
                "优",
                4,
                True,
                risk_flags=["distribution_high"],
            ),  # 已在🔍区→不进📡
            _cand("300001", "丙", "X", "D", "差", 1, False),  # 基本面差→不进📡
        ],
    }
    md = ct.render_table(pool, "2026-07-30")
    assert "📡 空头前哨" in md and "非可买" in md
    sec = md.split("## 📡 空头前哨")[1].split("\n## ")[0]
    assert "600100" in sec and "未到位" in sec
    assert "600000" not in sec and "600003" not in sec and "300001" not in sec


def test_no_outpost_section_in_bull_regime():
    # 非空头不出前哨区
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [_cand("600100", "哨", "半导体", "D", "优", 1, False)],
    }
    assert "📡 空头前哨" not in ct.render_table(pool, "2026-07-30")


def test_daily_signal_summary_section():
    # 置顶 ⭐ 一览:三档各归其位,一眼看清今日真信号
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [
            _cand("600000", "甲", "半导体", "A", "优", 4, True),  # → 可买
            _cand("600007", "己", "X", "B", "优", 4, True),  # → 观察价位
            _cand("600008", "庚", "Y", "A", "优", 3, False),  # 三面非四面 → 待0AMV做多
            _cand("300001", "丙", "Z", "D", "差", 1, False),  # 不上榜
        ],
    }
    md = ct.render_table(pool, "2026-07-30")
    sec = md.split("## ⭐ 今日信号一览")[1].split("\n## ")[0]
    # v0.50（#37 阶段 A）：可买 = A + 市场/基本面/技术三面共振（板块相位移出，
    # 降为「4面共振」情境标注列）。
    buy_line = next(
        l for l in sec.splitlines() if "可买（A + 市场/基本面/技术三面共振）" in l
    )
    obs_line = next(l for l in sec.splitlines() if "观察价位" in l)
    wait_line = next(l for l in sec.splitlines() if "待0AMV做多" in l)
    assert "600000" in buy_line and "600007" not in buy_line
    assert "600007" in obs_line and "600000" not in obs_line
    assert "600008" in wait_line and "300001" not in sec


def test_daily_signal_summary_bear_discipline():
    # 空头:明示纪律"空头不买",可买档恒无
    pool = {
        "status": "ok",
        "amv_state": "空头",
        "candidates": [_cand("600000", "甲", "半导体", "A", "优", 4, True)],
    }
    md = ct.render_table(pool, "2026-07-30")
    sec = md.split("## ⭐ 今日信号一览")[1].split("\n## ")[0]
    assert "空头不买" in sec


def test_platform_pullback_column():
    c1 = _cand("600000", "甲", "半导体", "C", "优", 3, False)
    c1["platform_pullback"] = {
        "platform_high": 10.25,
        "breakout_date": "2026-07-20",
        "pullback_low": 10.1,
    }
    c2 = _cand("000002", "乙", "AI", "D", "差", 1, False)
    md = ct.render_table({"status": "ok", "candidates": [c1, c2]}, "2026-08-02")
    assert "平台回踩" in md
    pool_sec = md.split("## C 池")[1]  # 主池表(观察区无此列)
    row1 = next(l for l in pool_sec.splitlines() if "600000" in l)
    row2 = next(l for l in md.split("## D 池")[1].splitlines() if "000002" in l)
    assert "✓@10.25" in row1  # 命中:显示平台高(自然止损位)
    assert "✓" not in row2  # 未命中:横杠


def test_industry_preferred_over_theme_sector_in_table():
    """「板块」列优先显示 TDX 官方细分行业(industry),缺行业时回退主题族(sector)。

    行业是每股唯一官方归属(建设银行→全国性银行),主题族是 9 选 1 的聚合层——
    展示层以官方行业为准(2026-08-04 owner 决策)。
    """
    c = _cand("601939", "建设银行", "船舶/军工/高端装备", "A", "优", 4, True)
    c["industry"] = "全国性银行"
    md = ct.render_table({"status": "ok", "candidates": [c]}, "2026-08-04")
    bull = md.split("## 🐂 基本面牛股候选")[1].split("\n## ")[0]
    assert "全国性银行" in bull and "船舶/军工" not in bull
    # 缺 industry 字段时回退主题族(向后兼容旧 enriched 文件)
    md2 = ct.render_table(
        {
            "status": "ok",
            "candidates": [
                _cand("000977", "浪潮信息", "AI算力/服务器/液冷", "A", "优", 4, True)
            ],
        },
        "2026-08-04",
    )
    bull2 = md2.split("## 🐂 基本面牛股候选")[1].split("\n## ")[0]
    assert "AI算力/服务器/液冷" in bull2


def test_render_table_carries_audit_block(tmp_path, monkeypatch):
    """可审计块（待办 #29）：选股表头部必须带 report_id / 策略版本 / 输入清单。

    出问题时靠它定位「当时用的哪版规则、哪天的数据」；
    输入文件缺失时登记「缺失」标记而不是不产出。
    """
    pool_dir = tmp_path / "stock_pool"
    pool_dir.mkdir()
    monkeypatch.setattr(ct, "STOCK_POOL_DIR", pool_dir)
    monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path / "quality")
    md = ct.render_table({"status": "ok", "candidates": []}, "2026-08-07")
    header = md.split("## ")[0]
    assert "report_id `2026-08-07_candidate_table_" in header
    assert "策略版本" in header and "数据截止" in header and "输入清单" in header
    assert "缺失" in header  # 两个输入都不存在 → 如实标缺失
