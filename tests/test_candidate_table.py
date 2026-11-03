# -*- coding: utf-8 -*-
"""candidate_table 渲染测试(重点:基本面牛股候选观察区 + 共振受限重点研究区)。"""

import copy

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
    buy_line = next(l for l in sec.splitlines() if "- **可买（现行规则" in l)
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


# ---------------------------------------------------------------------------
# v0.229（TODO #64，owner 拍板接线方式=门内提醒）：RD（RSI 深水区）证据在
# ⭐ 今日信号一览的可买/观察价位名单内亮 ⚡RD 灯——仅提示，不改判定。
# ---------------------------------------------------------------------------


def _rd_cand(code, name, *, bucket="A", aligned=4, bull=True, tech=55.0, rd="hit"):
    """RD 门内提醒测试用候选：默认四面共振 A 层（进行动区可买档）。"""
    c = _cand(code, name, "半导体", bucket, "优", aligned, bull, tech=tech)
    c["signals"] = {"rsi_deep_oversold": {"state": rd}}
    return c


def _overview_lines(md):
    sec = md.split("## ⭐ 今日信号一览")[1].split("\n## ")[0]
    return sec.splitlines()


def test_rd_action_zone_light_on_buy_and_observe():
    """hit 亮灯：可买（A）与观察价位（B）两档名单内 RD=hit 的候选名后带 ⚡RD。"""
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [
            _rd_cand("600000", "甲", rd="hit"),  # A 可买 + RD → 亮灯
            _rd_cand("600007", "己", bucket="B", rd="hit"),  # B 观察价位 + RD → 亮灯
        ],
    }
    lines = _overview_lines(ct.render_table(pool, "2026-09-14"))
    buy_line = next(l for l in lines if "- **可买（现行规则" in l)
    obs_line = next(l for l in lines if "观察价位" in l)
    assert "600000 甲 ⚡RD" in buy_line
    assert "600007 己 ⚡RD" in obs_line


def test_rd_miss_and_unavailable_no_light():
    """三态 fail-closed：miss 与 unavailable（数据不足）都不亮灯、不出图例行。"""
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [
            _rd_cand("600000", "甲", rd="miss"),
            _rd_cand("600007", "己", rd="unavailable"),
        ],
    }
    lines = _overview_lines(ct.render_table(pool, "2026-09-14"))
    assert not any("⚡RD" in l for l in lines)
    joined = "\n".join(lines)
    assert "600000" in joined and "600007" in joined  # 名单成员本身不变


def test_rd_no_light_outside_action_zone():
    """不在行动区的候选即使 RD=hit 也不亮灯（待0AMV做多档/C 池受限区）。"""
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [
            _rd_cand("600008", "庚", aligned=3, bull=False, rd="hit"),  # → 待0AMV做多
            _rd_cand("600003", "丁", bucket="C", rd="hit"),  # C 池 → 🔍 受限区
        ],
    }
    lines = _overview_lines(ct.render_table(pool, "2026-09-14"))
    assert not any("⚡RD" in l for l in lines)
    wait_line = next(l for l in lines if "待0AMV做多" in l)
    assert "600008" in wait_line  # 等待区名单照常，只是不亮灯
    assert not any("600003" in l for l in lines)


def test_rd_legend_cites_r21_and_stop_advisory():
    """图例行：R21 引用 + 止损 advisory（纯文本提示，零行为变化）。"""
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [_rd_cand("600000", "甲", rd="hit")],
    }
    lines = _overview_lines(ct.render_table(pool, "2026-09-14"))
    legend = next(l for l in lines if l.startswith("> ⚡RD"))
    assert "R21" in legend and "+26~+42pp" in legend
    assert "仅提示，不改判定" in legend
    assert "12% 宽止损" in legend and "−7%/−10%" in legend
    assert "另行拍板" in legend


def test_rd_reminder_bitwise_no_change_to_lists_and_buckets():
    """对拍基线：RD 亮灯 vs 同池 miss，可买名单/分层池/得分逐位不变。

    差异只许是 ⭐ 一览内的 ⚡RD 灯与图例行；「## 得分 Top 5」起（含四个分层池
    明细表）两段渲染必须逐字节相同。
    """
    base = {
        "status": "ok",
        "amv_state": "做多",
        "bucket_counts": {"A": 1, "B": 1},
        "candidates": [
            _rd_cand("600000", "甲", rd="hit", tech=61.0),
            _rd_cand("600007", "己", bucket="B", rd="hit", tech=55.0),
        ],
    }
    miss = copy.deepcopy(base)
    for c in miss["candidates"]:
        c["signals"]["rsi_deep_oversold"]["state"] = "miss"
    md_hit = ct.render_table(base, "2026-09-14")
    md_miss = ct.render_table(miss, "2026-09-14")
    # ① ⭐ 一览：剥掉灯与图例行后逐行相同（可买清单成员逐位不变）
    stripped = [
        ln.replace(" ⚡RD", "")
        for ln in _overview_lines(md_hit)
        if not ln.startswith("> ⚡RD")
    ]
    assert stripped == _overview_lines(md_miss)
    # ② 候选总表起：逐字节相同（判定 / 分数零变化；RD 不进总表证据列）
    tail_hit = md_hit.split("## 📋 候选总表")[1].split("\n## ")[0]
    tail_miss = md_miss.split("## 📋 候选总表")[1].split("\n## ")[0]
    assert tail_hit == tail_miss


# ---------------------------------------------------------------------------
# v0.478（owner 2026-11-03）：去掉 A/B/C/D 分池、Top5 与 🐂/🔍/📡 子区，全部候选一张总表
# ---------------------------------------------------------------------------


def _rows(md):
    sec = md.split("## 📋 候选总表")[1].split("\n## ")[0]
    out = {}
    for ln in sec.splitlines():
        cells = [c.strip() for c in ln.split("|")[1:-1]]
        if len(cells) == 12 and cells[0][:1].isdigit():
            out[cells[0]] = cells
    return out


def test_no_bucket_sections_or_letters():
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "bucket_counts": {"A": 1, "D": 1},
        "candidates": [
            _cand("600000", "甲", "半导体", "A", "优", 4, True),
            _cand("300001", "丙", "X", "D", "差", 1, False),
        ],
    }
    md = ct.render_table(pool, "2026-11-03")
    for gone in (
        "## A 池",
        "## B 池",
        "## C 池",
        "## D 池",
        "## 得分 Top 5",
        "🐂 基本面牛股候选",
        "## 🔍",
        "| 分层 |",
    ):
        assert gone not in md, gone
    assert "## 📋 候选总表（2 只，按技术分降序）" in md and "## 📖 列含义" in md


def test_status_column_keeps_invariants():
    """原各区不变量收进「状态」列：A+三面=🐂可买；B+三面=观察价位；未到市场腿=待0AMV做多；
    共振但 C/D=🔍受限·非可买（带风险原因）；不共振=-。"""
    wait = _cand("600008", "庚", "Y", "A", "优", 3, False)
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [
            _cand("600000", "甲", "半导体", "A", "优", 4, True, tech=70),
            _cand("600007", "己", "X", "B", "优", 4, True, tech=65),
            wait,
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
            _cand("300001", "丙", "Z", "D", "差", 1, False, tech=10),
        ],
    }
    r = _rows(ct.render_table(pool, "2026-11-03"))
    assert r["600000"][4] == "🐂可买"
    assert r["600007"][4] == "🐂观察价位"
    assert r["600008"][4] == "待0AMV做多"
    assert r["600003"][4] == "🔍受限·非可买" and "distribution_high" in r["600003"][8]
    assert r["300001"][4] == "-"
    assert list(r)[:2] == ["600000", "600007"]  # 技术分降序


def test_bear_outpost_status():
    outpost = _cand("600100", "哨", "半导体", "D", "优", 3, False)
    outpost["resonance_4leg"]["sector"] = False
    bull = {"status": "ok", "amv_state": "做多", "candidates": [copy.deepcopy(outpost)]}
    bear = {"status": "ok", "amv_state": "空头", "candidates": [outpost]}
    assert (
        _rows(ct.render_table(bear, "2026-11-03"))["600100"][4] == "📡空头前哨·非可买"
    )
    assert _rows(ct.render_table(bull, "2026-11-03"))["600100"][4] == "-"


def test_evidence_and_trigger_cells():
    c = _cand("600000", "甲", "半导体", "C", "优", 3, False)
    c.update(
        {
            "adx25": True,
            "s_reversal": {"s_reversal": 66.5},
            "platform_pullback": {"platform_high": 10.25},
            "formula_hits": ["KDJ_J_LOW"],
            "patterns": {"j_low": True, "bbi_above": False},
            "five_day_entry": {"hit": True},
        }
    )
    c2 = _cand("000002", "乙", "AI", "D", "差", 1, False)
    r = _rows(ct.render_table({"status": "ok", "candidates": [c, c2]}, "2026-11-03"))
    assert "ADX25" in r["600000"][6] and "S反转66.5" in r["600000"][6]
    assert "平台回踩✓@10.25" in r["600000"][6] and r["000002"][6] == "-"
    assert r["600000"][5] == "KDJ_J_LOW · 低J · 五日"


def test_industry_preferred_over_theme_sector():
    c = _cand("601939", "建设银行", "船舶/军工/高端装备", "A", "优", 4, True)
    c["industry"] = "全国性银行"
    c2 = _cand("000977", "浪潮信息", "AI算力/服务器/液冷", "A", "优", 4, True)
    r = _rows(ct.render_table({"status": "ok", "candidates": [c, c2]}, "2026-11-03"))
    assert r["601939"][9] == "全国性银行" and r["000977"][9] == "AI算力/服务器/液冷"


def test_legend_covers_every_header_column():
    """列含义必须覆盖总表里除代码/名称/止损位外的全部列（防口径漂移）。"""
    keys = {k for k, _ in ct.COLUMN_LEGEND}
    md = ct.render_table(
        {
            "status": "ok",
            "candidates": [_cand("600000", "甲", "X", "A", "优", 4, True)],
        },
        "2026-11-03",
    )
    header = next(
        ln for ln in md.splitlines() if ln.startswith("| 代码 | 名称 | 技术分")
    )
    cols = [c.strip() for c in header.split("|")[1:-1]]
    assert set(cols) - {"代码", "名称", "建议止损位"} <= keys
