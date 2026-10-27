# -*- coding: utf-8 -*-
"""holdout_tracking（TODO #120 holdout 跟踪月报节，v0.455）测试——全合成注入，
不 Spawn 研究子进程（conftest 已设 CUSTOS_SKIP_HOLDOUT_TRACKING=1，专测自行
delenv 或注入假 runner）。
"""

import json

import pytest

from custos.core import contracts
from custos.pipeline.close_review import holdout_tracking as ht
from custos.pipeline.close_review import monthly_review as mr

LEDGER_HEADER = "成交日期,成交时间,代码,名称,交易类别,成交数量,成交价格,成交金额,费用\n"


def _write_ledger(base, rows):
    p = base / "data" / "trades" / "master_trade_ledger.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(LEDGER_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def _row(day, code, side, qty, price, fee, name="测试股"):
    amount = round(qty * price, 2)
    return f"{day},09:31:00,{code},{name},{side},{qty},{price},{amount},{fee}"


def _write_shadow(base, rows):
    p = base / "data" / "trades" / "plan_shadow_observations.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return p


def _shadow_row(day, code, stage="1700", agree=None, plan_source=None):
    return {
        "schema": "plan_shadow_observation/v1",
        "date": day,
        "code": code,
        "stage": stage,
        "plan_source": plan_source,
        "agree": agree,
        "recorded_at": f"{day}T17:00:00",
    }


def _none_runner(base, workdir):
    return None, "注入的不可用原因"


# ── 分布统计（手算）──────────────────────────────────────


def test_dist_stats_hand_computed():
    s = ht._dist_stats([0.25, -0.12, 0.05])
    assert s["n"] == 3
    assert s["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert s["avg_win"] == pytest.approx(0.15, abs=1e-4)
    assert s["avg_loss"] == pytest.approx(-0.12, abs=1e-4)
    assert s["payoff_ratio"] == pytest.approx(1.25, abs=1e-4)
    assert s["big_win_rate"] == pytest.approx(1 / 3, abs=1e-4)  # +25% ≥ +20%
    assert s["big_loss_rate"] == pytest.approx(1 / 3, abs=1e-4)  # −12% ≤ −10%
    # 打平单计非赢；空列全 None（「没算出来」不冒充 0）
    assert ht._dist_stats([0.0])["win_rate"] == 0.0
    empty = ht._dist_stats([])
    assert empty["n"] == 0 and empty["win_rate"] is None
    # 非有限值入口剔除（产物走 write_json allow_nan=False，不给它留炸点）
    assert ht._dist_stats([float("nan"), 0.25])["n"] == 1


def test_thresholds_single_source():
    """大单/大亏口径 = R48/R49 写死定义（≥+20% / ≤−10%），模块里只有这一份。"""
    assert ht.BIG_WIN_RET == 0.20 and ht.BIG_LOSS_RET == -0.10
    assert ht._month_index("2026-09") == 1
    assert ht._month_index("2026-10") == 2
    assert ht._month_index("2026-08") == 0  # 早于 holdout 起点


def test_month_window_matches_monthly_review():
    """月窗口换算与 monthly_review.month_range 逐位一致（两个实现钉在一起，
    任一改动都必须显式同步——不 import 是为了不成环，见模块注释）。"""
    for m in ("2026-09", "2026-10", "2025-12", "2024-02", "2026-01"):
        a = ht._month_window(m)
        b = mr.month_range(m)
        assert (a["label"], a["start"], a["end"]) == (b["label"], b["start"], b["end"])


# ── 源 1：实盘平仓（跨月分组 + holdout 累计 + 起点前剔除）────────────────


def test_actual_trades_month_and_cumulative(tmp_path):
    """三笔 holdout 内平仓（+25% 大单 / −12% 大亏 / +5%）+ 一笔起点前平仓（+50%，
    不得计入）；本月 = 2026-10 只含后两笔。"""
    _write_ledger(
        tmp_path,
        [
            _row("2026-08-20", "600009", "买入", 100, 10.0, 0),
            _row("2026-09-02", "600009", "卖出", 100, 15.0, 0),  # 起点前平仓，剔除
            _row("2026-09-03", "600001", "买入", 100, 10.0, 0),
            _row("2026-09-10", "600001", "卖出", 100, 12.5, 0),  # +25% 大单（9 月）
            _row("2026-09-15", "600002", "买入", 100, 10.0, 0),
            _row("2026-10-08", "600002", "卖出", 100, 8.8, 0),  # −12% 大亏（10 月）
            _row("2026-10-09", "600003", "买入", 100, 10.0, 0),
            _row("2026-10-20", "600003", "卖出", 100, 10.5, 0),  # +5%（10 月）
        ],
    )
    rep = ht.build_report(
        tmp_path, "2026-10", pick_runner=_none_runner, shadow_runner=_none_runner
    )
    at = rep["actual_trades"]
    assert at["available"]
    m, c = at["month"], at["since_holdout"]
    assert m["n"] == 2 and m["win_rate"] == 0.5
    assert m["big_loss_rate"] == 0.5 and m["big_win_rate"] == 0.0
    assert c["n"] == 3
    assert c["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert c["big_win_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert c["big_loss_rate"] == pytest.approx(1 / 3, abs=1e-4)
    # 样本薄 ⇒ 显式标注
    assert any("样本不足" in n for n in rep["sample_notes"])


def test_missing_master_ledger_unavailable(tmp_path):
    rep = ht.build_report(
        tmp_path, "2026-10", pick_runner=_none_runner, shadow_runner=_none_runner
    )
    assert not rep["actual_trades"]["available"]
    assert any("成交台账缺失" in u for u in rep["unavailable"])
    # 全不可用 ⇒ 如实说「只有格式没有读数」，不得冒充样本达标
    assert rep["sample_notes"] == ["全部数据源不可用——本节只有格式没有读数"]
    md = ht.render_markdown(rep)
    assert "unavailable" in md


# ── 源 2：选股练习（注入报告 dict）────────────────────────


def test_pick_block_from_report_month_slice():
    rep = {
        "closed": [
            {"date": "2026-09-20", "code": "600001", "status": "closed", "ret": 0.25},
            {"date": "2026-10-10", "code": "600002", "status": "closed", "ret": -0.12},
            {"date": "2026-10-11", "code": "600003", "status": "closed", "ret": 0.03},
        ],
        "open": [{"code": "600004"}],
        "pending": [{"code": "600005"}],
    }
    b = ht._pick_block_from_report(rep, "2026-10")
    assert b["month"]["n"] == 2 and b["month"]["big_loss_rate"] == 0.5
    assert b["since_holdout"]["n"] == 3
    assert b["since_holdout"]["big_win_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert b["n_open"] == 1 and b["n_pending"] == 1


def test_pick_runner_failure_marks_unavailable(tmp_path):
    _write_ledger(tmp_path, [])
    rep = ht.build_report(
        tmp_path, "2026-10", pick_runner=_none_runner, shadow_runner=_none_runner
    )
    assert not rep["pick_practice"]["available"]
    assert any("选股练习" in u for u in rep["unavailable"])


# ── 源 3：plan 影子台账（行计数 + 注入打分）─────────────────


def test_plan_shadow_counts_and_review(tmp_path):
    _write_ledger(tmp_path, [])
    _write_shadow(
        tmp_path,
        [
            _shadow_row("2026-09-10", "600001", "1700", True, "real"),
            _shadow_row(
                "2026-09-11", "600001", "1700", False, "real"
            ),  # 不一致（9 月）
            _shadow_row(
                "2026-10-09", "600002", "1700", False, "real"
            ),  # 不一致（10 月）
            _shadow_row(
                "2026-10-09", "600002", "1445", False, "real"
            ),  # 盘中行只报行数
            _shadow_row(
                "2026-10-10", "600003", "1700", False, "default"
            ),  # default 视同无计划
            _shadow_row("2026-10-12", "600003", "1700", None, None),  # 无计划
        ],
    )

    def fake_review(base, workdir):
        return {
            "n_events": 2,
            "n_distinct_codes": 2,
            "n_continuation_rows": 1,
            "summary": {
                "5": {
                    "n": 1,
                    "mean": 0.012,
                    "median": 0.012,
                    "win_rate": 1.0,
                    "n_pending": 1,
                }
            },
        }, None

    rep = ht.build_report(
        tmp_path, "2026-10", pick_runner=_none_runner, shadow_runner=fake_review
    )
    sh = rep["plan_shadow"]
    mc, cc = sh["month_rows"], sh["since_holdout_rows"]
    # 本月：1700 行 3（真不一致 1 + default 1 + 无计划 1），1445 行 1
    assert (mc["rows_1700"], mc["rows_1445"]) == (3, 1)
    assert mc["agree_false"] == 2 and mc["agree_none"] == 1
    assert mc["disagree_nondefault_rows"] == 1  # default 不算
    assert mc["disagree_distinct_codes"] == 1
    # 累计自 holdout 起点：9 月两行也进
    assert cc["rows_1700"] == 5 and cc["disagree_nondefault_rows"] == 2
    assert cc["disagree_distinct_codes"] == 2
    rv = sh["review"]
    assert rv["available"] and rv["n_events"] == 2
    assert rv["delta5"]["mean"] == 0.012 and rv["delta5"]["n_pending"] == 1
    md = ht.render_markdown(rep)
    assert "独立事件" in md and "分歧段段头" in md


def test_plan_shadow_review_empty_guard(tmp_path):
    """0 独立事件 ⇒ plan_shadow_review 空结果护栏非零退出 ⇒ review 块 unavailable，
    但行计数照报（有台账行、0 事件本身是读数）。"""
    _write_ledger(tmp_path, [])
    _write_shadow(tmp_path, [_shadow_row("2026-10-09", "600001", "1700", True, "real")])
    rep = ht.build_report(
        tmp_path, "2026-10", pick_runner=_none_runner, shadow_runner=_none_runner
    )
    sh = rep["plan_shadow"]
    assert sh["month_rows"]["agree_true"] == 1
    assert not sh["review"]["available"]
    assert "注入的不可用原因" in sh["review"]["reason"]


# ── 源 4：trade_cf 同次产物 ──────────────────────────────


def _write_trade_cf(base, stem="2026-10_monthly_review"):
    p = base / "artifacts" / "logs" / "trade_counterfactual" / f"{stem}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "summary": {
                    "rule": {
                        "n_episodes": 3,
                        "n_closed": 2,
                        "total_delta_amount": -1234.5,
                        "categories": {"止损拖延": {"n": 1}},
                    }
                },
                "episodes": [
                    {
                        "closed": True,
                        "instrument": "stock",
                        "first_lot_ret": 0.22,
                        "rule": {"ret": 0.25},
                    },
                    {
                        "closed": True,
                        "instrument": "stock",
                        "first_lot_ret": -0.11,
                        "rule": {"ret": -0.10},
                    },
                    {  # 未清仓 / 场内基金不进分布
                        "closed": False,
                        "instrument": "etf_bond",
                        "first_lot_ret": None,
                        "rule": {"ret": None},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def test_trade_cf_block_reads_same_run_artifact(tmp_path):
    _write_ledger(tmp_path, [])
    _write_trade_cf(tmp_path)
    rep = ht.build_report(
        tmp_path, "2026-10", pick_runner=_none_runner, shadow_runner=_none_runner
    )
    cf = rep["trade_cf"]
    assert cf["available"] and cf["n_closed"] == 2
    a, r = cf["actual_first_lot"], cf["rule_first_lot"]
    assert a["n"] == 2 and a["big_win_rate"] == 0.5 and a["big_loss_rate"] == 0.5
    assert r["n"] == 2 and r["big_win_rate"] == 0.5
    assert cf["categories"] == {"止损拖延": 1}
    assert cf["total_delta_amount"] == -1234.5


def test_trade_cf_missing_unavailable(tmp_path):
    _write_ledger(tmp_path, [])
    rep = ht.build_report(
        tmp_path, "2026-10", pick_runner=_none_runner, shadow_runner=_none_runner
    )
    assert not rep["trade_cf"]["available"]
    assert any("trade_cf" in u for u in rep["unavailable"])


# ── 基线与契约 ───────────────────────────────────────────


def test_baseline_frozen_snapshot_and_contract(tmp_path):
    _write_ledger(tmp_path, [])
    rep = ht.build_report(
        tmp_path, "2026-10", pick_runner=_none_runner, shadow_runner=_none_runner
    )
    bl = rep["baseline"]["r44_ref"]
    assert bl["n"] == 569861 and bl["win_rate"] == 0.43622
    assert "缺精确基线" in bl["big_win_note"]
    # 契约已登记且产物过 require（不红即过）
    assert "holdout_tracking_monthly" in contracts.SPECS
    contracts.require("holdout_tracking_monthly", rep)


# ── section：env 跳过 / 伴生 JSON 落盘 / fail-soft ────────


def test_section_env_skip(tmp_path):
    """conftest 默认设 CUSTOS_SKIP_HOLDOUT_TRACKING=1 ⇒ 整节跳过返回空串。"""
    assert ht.section(tmp_path, "2026-10") == ""


def test_section_writes_companion_json(tmp_path, monkeypatch):
    monkeypatch.delenv("CUSTOS_SKIP_HOLDOUT_TRACKING", raising=False)
    monkeypatch.setattr(ht, "_run_pick_practice", _none_runner)
    monkeypatch.setattr(ht, "_run_plan_shadow_review", _none_runner)
    _write_ledger(
        tmp_path,
        [
            _row("2026-10-09", "600003", "买入", 100, 10.0, 0),
            _row("2026-10-20", "600003", "卖出", 100, 10.5, 0),
        ],
    )
    md = ht.section(tmp_path, "2026-10")
    assert ht.HEADER in md
    out = (
        tmp_path / "artifacts" / "reports" / "monthly" / "2026-10_holdout_tracking.json"
    )
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["schema"] == "holdout_tracking_monthly/v1"
    assert rep["month"] == "2026-10" and rep["holdout_month_index"] == 2
    assert "纯描述" in rep["permission_rule"]
    assert "明细 JSON" in md


def test_section_fail_soft_never_raises(tmp_path, monkeypatch):
    """旁路定位：任何异常（含 require 的 SystemExit）⇒ 一行 unavailable，不抛出。"""
    monkeypatch.delenv("CUSTOS_SKIP_HOLDOUT_TRACKING", raising=False)

    def boom(base, workdir):
        raise RuntimeError(" synthetic boom")

    monkeypatch.setattr(ht, "_run_pick_practice", boom)
    md = ht.section(tmp_path, "2026-10")
    assert "unavailable" in md and ht.HEADER in md


def test_monthly_main_appends_section(tmp_path, monkeypatch):
    """接线：monthly_review 主体渲染后拼上 holdout 节（trade_cf 之后）。"""
    json_dir = tmp_path / "data" / "review" / "monthly"
    monkeypatch.setattr(mr, "MONTHLY_JSON_DIR", json_dir)
    monkeypatch.setattr(ht, "section", lambda base, month: "\nHOLDOUT_MARKER\n")
    rc = mr.main(["--month", "2026-07", "--base", str(tmp_path)])
    assert rc == 0
    md = (
        tmp_path / "artifacts" / "reports" / "monthly" / "2026-07_monthly_review.md"
    ).read_text(encoding="utf-8")
    assert "HOLDOUT_MARKER" in md


def test_actual_trades_b1_split_from_trade_cf_flags(tmp_path):
    """v0.458：B1 标记取自 trade_cf 产物 episodes.b1_entry（键 = code + 首笔买入日）；
    只有 B1 组进基线对照表，非 B1 / 无法判定另列。"""
    import json

    _write_ledger(
        tmp_path,
        [
            _row("2026-09-03", "600001", "买入", 100, 10.0, 0),
            _row("2026-09-10", "600001", "卖出", 100, 12.5, 0),  # B1，+25%
            _row("2026-09-15", "600002", "买入", 100, 10.0, 0),
            _row("2026-10-08", "600002", "卖出", 100, 8.8, 0),  # 非 B1，−12%
            _row("2026-10-09", "600003", "买入", 100, 10.0, 0),
            _row("2026-10-20", "600003", "卖出", 100, 10.5, 0),  # 无标记
        ],
    )
    d = tmp_path / "artifacts" / "logs" / "trade_counterfactual"
    d.mkdir(parents=True)
    (d / "2026-09_monthly_review.json").write_text(
        json.dumps(
            {
                "episodes": [
                    {
                        "code": "600001",
                        "entry_date": "2026-09-03",
                        "b1_entry": True,
                        "instrument": "stock",
                    },
                    {
                        "code": "600002",
                        "entry_date": "2026-09-15",
                        "b1_entry": False,
                        "instrument": "stock",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    rep = ht.build_report(
        tmp_path, "2026-10", pick_runner=_none_runner, shadow_runner=_none_runner
    )
    sp = rep["actual_trades"]["b1_split"]["since_holdout"]
    assert sp["b1"]["n"] == 1 and sp["b1"]["big_win_rate"] == 1.0
    assert sp["non_b1"]["n"] == 1 and sp["non_b1"]["big_loss_rate"] == 1.0
    assert sp["n_unknown"] == 1
    md = ht.render_markdown(rep) if hasattr(ht, "render_markdown") else ""
    if md:
        assert "本月 B1" in md and "非 B1 入场笔数" in md
