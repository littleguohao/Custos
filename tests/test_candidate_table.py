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


def _move_cand(
    code, name, *, j=5.0, tech=55.0, bucket="B", bottom=False, ign=False, zd=True
):
    """门内提醒测试用候选：震荡池成员（默认）+ 池内票（默认 J<13）+ 异动字段。"""
    c = _cand(code, name, "半导体", bucket, "优", 4, True, tech=tech)
    c.update(
        {
            "daily_j": j,
            "change_pct": 3.0,
            "formula_hits": ["POOL_ZHENDANG"] if zd else ["KDJ_J_LOW"],
            "bottom_volume": {"hit": bottom},
            "ignition": {"hit": ign},
        }
    )
    return c


def test_in_gate_reminder_section():
    """v0.160（owner）：门内提醒——震荡池内 J≤13 即入节，**不再强制异动强**；
    异动判据列降级为展示信息。"""
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [
            _move_cand("600100", "甲", bottom=True),
            _move_cand("600101", "乙", ign=True),
            _move_cand("600102", "丙"),  # 无异动 —— v0.160 起也进提醒
        ],
    }
    md = ct.render_table(pool, "2026-08-20")
    sec = md.split("## 📌 门内提醒")[1].split("\n## ")[0]
    assert "600100" in sec and "底部巨量" in sec
    assert "600101" in sec and "放量点火" in sec
    assert "600102" in sec, "无异动的震荡池 J≤13 票 v0.160 起也要进提醒"
    assert "仅提醒" in sec


def test_in_gate_reminder_only_zhendang_pool():
    """v0.91（owner）：只列震荡池（POOL_ZHENDANG）——旧观察区的事实口径
    （KDJ_J_LOW 公式命中自带 J<13 几乎不会被挡，门外票实际全是震荡池成员），
    改门内后显式补回；纯公式命中票即便 J≤13 且异动强也不进提醒。"""
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [
            _move_cand("600100", "甲", bottom=True, zd=False),  # 纯 KDJ_J_LOW
            _move_cand("600101", "乙", bottom=True),  # 震荡池
        ],
    }
    md = ct.render_table(pool, "2026-08-20")
    sec = md.split("## 📌 门内提醒")[1].split("\n## ")[0]
    assert "600100" not in sec and "600101" in sec
    assert "震荡池" in sec


def test_in_gate_reminder_excludes_j_above_13():
    """v0.89：只列 J≤13——J>13 的票即便异动强也不进提醒（门外票只进 excluded）。"""
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [
            _move_cand("600100", "甲", j=45.2, bottom=True),
            _move_cand("600101", "乙", j=12.9, bottom=True),
        ],
    }
    md = ct.render_table(pool, "2026-08-20")
    sec = md.split("## 📌 门内提醒")[1].split("\n## ")[0]
    assert "600100" not in sec and "600101" in sec


def test_in_gate_reminder_empty_not_silent():
    """无提醒对象也出一节（「（今日无）」）——节消失分不清「没查」与「查了没有」。"""
    pool = {"status": "ok", "amv_state": "做多", "candidates": []}
    md = ct.render_table(pool, "2026-08-20")
    assert "门内提醒" in md and "（今日无）" in md


def test_in_gate_reminder_sorted_by_j_asc_and_capped():
    """v0.90（owner）：按日 J 从小到大——J 越小越接近超卖极值；
    命中超过 _IN_GATE_REMINDER_TOP_N 时截断并注明总数。"""
    cands = [
        _move_cand(f"60{i:04d}", f"票{i}", j=float(25 - i) / 2, bottom=True)
        for i in range(25)
    ]
    pool = {"status": "ok", "amv_state": "做多", "candidates": cands}
    md = ct.render_table(pool, "2026-08-20")
    sec = md.split("## 📌 门内提醒")[1].split("\n## ")[0]
    assert sec.index("600024") < sec.index("600023")  # J 最小者在前
    assert "600000" not in sec  # 截断：J 最大者掉出前 20
    assert "共 25 只命中" in sec


def test_signal_labels_sg_merged_into_sb():
    """2026-08-14（owner 反馈 SG/SB 两名单每次完全相同）：SG 不单列——
    SB = SG ∧ 当日 J<13，本池已过 J<13 硬门槛 ⇒ 池内两名单恒重合，单列是噪声。
    （两者算法不同，见 b2_surge_factor；v0.169 起「恒重合」图注行已随解释文字一并撤下，
    口径只留在代码注释里。）"""
    cand = _cand("600000", "甲", "半导体", "A", "优", 4, True)
    cand["signals"] = {
        "bottom_surge": {"state": "hit"},
        "surge_then_b1": {"state": "hit"},
    }
    pool = {"status": "ok", "amv_state": "做多", "candidates": [cand]}
    md = ct.render_table(pool, "2026-07-30")
    sec = md.split("## 🏷️ 信号标注一览")[1].split("\n## ")[0]
    assert "异动后的B1" in sec
    assert "- **底部异动" not in sec, "SG 行必须并入 SB，不再单列"
    assert "恒重合" not in sec, "v0.169（owner）：解释文字行已从一览撤下"


def test_top5_caption_states_uncalibrated_heuristic():
    """v0.52（#37 阶段 C）：Top5 必须标注排序口径——未校准启发式、非 alpha 排序。"""
    pool = {
        "date": "2026-07-22",
        "status": "ok",
        "bucket_counts": {"C": 1},
        "candidates": [
            {
                "code": "600000",
                "name": "甲",
                "bucket": "C",
                "score_detail": {"total": 50, "technical_score": 50},
                "formula_hits": [],
                "risk_flags": [],
            }
        ],
    }
    md = ct.render_table(pool, "2026-07-22")
    top = md.split("## 得分 Top 5")[1].split("## ")[0]
    assert "未校准启发式" in top and "非 alpha 排序" in top
    assert "总分=技术分" in top


def test_evidence_columns_in_bucket_table():
    """v0.51：ADX25/S反转 进主表（证据列）。"""
    c = _cand("600000", "甲", "半导体", "A", "优", 4, True)
    c["adx25"] = True
    c["s_reversal"] = {"available": True, "s_reversal": 66.5}
    pool = {"status": "ok", "amv_state": "做多", "candidates": [c]}
    md = ct.render_table(pool, "2026-07-30")
    assert "ADX25" in md and "S反转" in md
    sec = md.split("## A 池")[1]  # 牛股候选表也含同名行，会撞——只看 A 池主表
    row = next(ln for ln in sec.split("\n") if ln.startswith("| 600000"))
    assert "✅" in row and "66.5" in row


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
    """可审计块（原待办 #29，已实现）：选股表头部必须带 report_id / 策略版本 / 数据截止。

    出问题时靠它定位「当时用的哪版规则、哪天的数据」；
    输入文件缺失时登记「缺失」标记而不是不产出（缺失标记在 JSON audit.inputs，
    v0.181 起输入清单行不进 MD）。
    """
    pool_dir = tmp_path / "stock_pool"
    pool_dir.mkdir()
    monkeypatch.setattr(ct, "STOCK_POOL_DIR", pool_dir)
    monkeypatch.setattr(ct, "QUALITY_DIR", tmp_path / "quality")
    md = ct.render_table({"status": "ok", "candidates": []}, "2026-08-07")
    header = md.split("## ")[0]
    assert "report_id `2026-08-07_candidate_table_" in header
    assert "策略版本" in header and "数据截止" in header
    assert "输入清单" not in header
