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
    status=None,
):
    # v0.529：状态列改读 score_candidates 的 b1_status；夹具按旧语义映射，保持各用例原意
    if status is None:
        if tier == "优" and aligned >= 2:
            if bucket in ("A", "B"):
                status = (
                    ("可买" if bucket == "A" else "观察价位") if bull else "待0AMV做多"
                )
            else:
                status = "受限"
        else:
            status = "-"
    return {
        "b1_status": status,
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
    code,
    name,
    *,
    j=5.0,
    tech=55.0,
    bucket="B",
    bottom=False,
    ign=False,
    pool_id="POOL_LAODENG",
):
    """门内提醒测试用候选：默认属于老登池，且日 J<13。"""
    c = _cand(code, name, "半导体", bucket, "优", 4, True, tech=tech)
    c.update(
        {
            "daily_j": j,
            "change_pct": 3.0,
            "formula_hits": [pool_id] if pool_id else ["KDJ_J_LOW"],
            "bottom_volume": {"hit": bottom},
            "ignition": {"hit": ign},
        }
    )
    return c


def test_in_gate_reminder_section():
    """v0.160（owner）：自选池内 J≤13 即入节，不再强制异动强。"""
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
    assert "600102" in sec, "无异动的自选池 J≤13 票 v0.160 起也要进提醒"
    assert "仅提醒" in sec


def test_in_gate_reminder_only_current_manual_pools():
    """v0.581（owner）：只列老登 / 科技 / 医药；纯公式与旧震荡池均不进提醒。"""
    pool = {
        "status": "ok",
        "amv_state": "做多",
        "candidates": [
            _move_cand("600100", "老", pool_id="POOL_LAODENG"),
            _move_cand("600101", "科", pool_id="POOL_KEJI"),
            _move_cand("600102", "医", pool_id="POOL_YIYAO"),
            _move_cand("600103", "外", pool_id=None),
            _move_cand("600104", "旧", pool_id="POOL_ZHENGDANG"),
        ],
    }
    md = ct.render_table(pool, "2026-11-24")
    sec = md.split("## 📌 门内提醒")[1].split("\n## ")[0]
    assert all(code in sec for code in ("600100", "600101", "600102"))
    assert "600103" not in sec and "600104" not in sec
    assert "老登 / 科技 / 医药" in sec
    assert "震荡池" not in sec


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


def test_signal_labels_overview_removed_but_table_marks_kept():
    """v0.580（owner）：RD / SB 已在候选总表展示，不再另列重复的一览区块。"""
    cand = _cand("600000", "甲", "半导体", "A", "优", 4, True)
    cand["signals"] = {
        "rsi_deep_oversold": {"state": "hit"},
        "surge_then_b1": {"state": "hit"},
        "summary": {"label": "2/2", "abbrs": ["RD", "SB"], "neg_abbrs": []},
    }
    pool = {"status": "ok", "amv_state": "做多", "candidates": [cand]}
    md = ct.render_table(pool, "2026-11-24")
    assert "## 🏷️ 信号标注一览" not in md
    row = next(ln for ln in md.splitlines() if ln.startswith("| 600000 |"))
    assert "2/2 RD·SB" in row


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
    buy_line = next(l for l in sec.splitlines() if "- **可买（" in l)
    obs_line = next(l for l in sec.splitlines() if "观察价位" in l)
    wait_line = next(l for l in sec.splitlines() if "待0AMV做多" in l)
    assert "600000" in buy_line and "600007" not in buy_line
    assert "600007" in obs_line and "600000" not in obs_line
    assert (
        "1 只" in wait_line and "600008" not in wait_line and "300001" not in sec
    )  # v0.543：只给计数


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
    buy_line = next(l for l in lines if "- **可买（" in l)
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
    assert "1 只" in wait_line  # v0.543：等待区只给计数，不列名单
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
        if len(cells) == 10 and cells[0][:1].isdigit():  # v0.529 再去「资金意图」列
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
    assert (
        "## 📋 候选总表（2 只，按状态排序，同状态内按技术分降序）" in md
        and "## 📖 列含义" in md
    )


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
    assert r["600000"][5] == "🐂可买"
    assert r["600007"][5] == "🐂观察价位"
    assert r["600008"][5] == "待0AMV做多"
    assert r["600003"][5] == "🔍受限·非可买" and "出货（高，否决）" in r["600003"][9]
    assert r["300001"][5] == "-"
    assert list(r)[:2] == ["600000", "600007"]  # 技术分降序


def test_bear_waits_for_amv_v0529():
    """v0.529：空头前哨撤销——空头期满足 B1 条件的候选统一显示「待0AMV做多」（b1_status 由打分侧给出）。"""
    c = _cand("600100", "哨", "半导体", "B", "优", 3, False, status="待0AMV做多")
    bear = {"status": "ok", "amv_state": "空头", "candidates": [c]}
    assert _rows(ct.render_table(bear, "2026-11-03"))["600100"][5] == "待0AMV做多"
    assert "空头前哨" not in ct.render_table(bear, "2026-11-03")


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
    assert "ADX25" in r["600000"][7] and "S反转66.5" in r["600000"][7]
    assert "平台回踩✓@10.25" in r["600000"][7] and r["000002"][7] == "-"
    assert r["600000"][6] == "KDJ_J_LOW · 低J"  # v0.576 五日不再显示


def test_industry_preferred_over_theme_sector():
    c = _cand("601939", "建设银行", "船舶/军工/高端装备", "A", "优", 4, True)
    c["industry"] = "全国性银行"
    c2 = _cand("000977", "浪潮信息", "AI算力/服务器/液冷", "A", "优", 4, True)
    r = _rows(ct.render_table({"status": "ok", "candidates": [c, c2]}, "2026-11-03"))
    assert r["601939"][2] == "全国性银行" and r["000977"][2] == "AI算力/服务器/液冷"


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
    header = next(ln for ln in md.splitlines() if ln.startswith("| 代码 | 名称 | 板块"))
    cols = [c.strip() for c in header.split("|")[1:-1]]
    assert "建议止损位" not in cols  # v0.528：近 10 日最低价退出 live
    assert set(cols) - {"代码", "名称"} <= keys


def test_hot_sub_industry_marks_board_column_v0529():
    c = _cand("601939", "建设银行", "x", "A", "优", 4, True)
    c["industry"] = "全国性银行"
    hot = {
        "window": 10,
        "top_n": 10,
        "hot": [{"industry": "全国性银行", "hits": 4, "days": [], "last_rank": 2}],
    }
    md = ct.render_table({"status": "ok", "candidates": [c]}, "2026-11-03", hot=hot)
    assert "## 🔥 近 10 个交易日热门细分行业" in md and "| 全国性银行 | 4 | 2 |" in md
    assert _rows(md)["601939"][2] == "🔥4 全国性银行"


def test_risk_flags_shown_in_chinese():
    from custos.pipeline.screening import score_candidates as sc  # noqa: F401
    import inspect, re

    flags = set(re.findall(r'risk_flags\.append\("([^"]+)"\)', inspect.getsource(sc)))
    assert flags and flags <= set(ct.RISK_ZH), flags - set(ct.RISK_ZH)


def test_unified_table_sorted_by_status_then_score():
    def c(code, st, tech):
        return {
            "code": code,
            "name": code,
            "b1_status": st,
            "score_detail": {"technical_score": tech},
        }

    pool = {
        "status": "ok",
        "candidates": [
            c("1", "-", 90),
            c("2", "受限", 80),
            c("3", "可买", 30),
            c("4", "观察价位", 70),
            c("5", "可买", 60),
            c("6", "待0AMV做多", 99),
        ],
    }
    order = [
        ln.split("|")[1].strip()
        for ln in ct.render_table(pool, "2026-11-18").splitlines()
        if ln.startswith("| ") and ln.split("|")[1].strip().isdigit()
    ]
    assert order[:6] == ["5", "3", "4", "6", "2", "1"]
