# -*- coding: utf-8 -*-
"""monthly_review（MASTER_WORKFLOW §七 月度复盘）测试。"""

import json
from datetime import date

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


# ── 月份窗口 ─────────────────────────────────────────────


def test_month_range_explicit():
    r = mr.month_range("2026-01")
    assert (r["start"], r["end"]) == ("2026-01-01", "2026-01-31")
    r = mr.month_range("2025-12")
    assert (r["start"], r["end"], r["label"]) == ("2025-12-01", "2025-12-31", "2025-12")


def test_month_range_default_cross_year(monkeypatch):
    """默认上个月；1 月复盘上年 12 月（跨年不回负月）。"""
    monkeypatch.setattr(mr, "cn_today", lambda: date(2026, 1, 15))
    assert mr.month_range(None)["label"] == "2025-12"
    monkeypatch.setattr(mr, "cn_today", lambda: date(2026, 8, 11))
    assert mr.month_range(None)["label"] == "2026-07"


# ── 缺数据：fail-closed ──────────────────────────────────


def test_missing_ledger_reports_unavailable(tmp_path):
    """台账缺失 ⇒ 盈亏/胜率类 unavailable，且如实登记——不得显示成「本月零盈亏」。

    「没算出来」与「算出来是 0」必须可区分（仓库反复强调的失真类型）。
    """
    r = mr.build_monthly_review(tmp_path, "2026-07")
    assert any("成交台账缺失" in u for u in r["unavailable"])
    assert r["realized"]["win_rate_pct"] is None
    md = mr.render_markdown(r)
    assert "数据缺口" in md and "成交台账缺失" in md


# ── 指标手算 ─────────────────────────────────────────────


def test_metrics_hand_computed(tmp_path):
    """一盈一亏两笔平仓 + 一笔下月单（不并入）。手算：
    A 股：买 100@10 费 5 → 卖 100@11 费 5：毛 +100，净 +90
    B 股：买 100@10 费 0 → 卖 100@9 费 0：毛 −100，净 −100
    """
    _write_ledger(
        tmp_path,
        [
            _row("2026-07-01", "600001", "买入", 100, 10.0, 5),
            _row("2026-07-03", "600002", "买入", 100, 10.0, 0),
            _row("2026-07-10", "600001", "卖出", 100, 11.0, 5),
            _row("2026-07-11", "600002", "卖出", 100, 9.0, 0),
            _row("2026-08-03", "600001", "买入", 100, 12.0, 0),  # 下月，不并入
        ],
    )
    r = mr.build_monthly_review(tmp_path, "2026-07")
    real = r["realized"]
    assert real["n_trades"] == 4 and real["n_closings"] == 2
    assert real["gross_total"] == 0.0  # +100 −100
    assert real["closed_fee_total"] == 10.0
    assert real["net_total"] == -10.0
    assert real["win_rate_pct"] == 50.0
    assert real["pl_ratio"] == 1.0
    assert real["expectancy_per_trade"] == -5.0
    assert real["avg_hold_days"] == 8.5  # (9 + 8) / 2
    # 个股归因按净盈亏升序：亏的在前
    assert [s["code"] for s in real["stock_attribution"]] == ["600002", "600001"]


def test_loss_streak_integrated(tmp_path):
    """同票两连亏进 loss_streaks.flagged（复用 loss_streak 口径，net_pnl 判亏）。"""
    _write_ledger(
        tmp_path,
        [
            _row("2026-07-01", "600001", "买入", 100, 10.0, 0),
            _row("2026-07-05", "600001", "卖出", 100, 9.0, 0),
            _row("2026-07-08", "600001", "买入", 100, 9.0, 0),
            _row("2026-07-12", "600001", "卖出", 100, 8.0, 0),
        ],
    )
    r = mr.build_monthly_review(tmp_path, "2026-07")
    assert "600001" in r["loss_streaks"]["flagged"]
    assert r["loss_streaks"]["streaks"]["600001"]["count"] == 2


# ── 期末集中度（仅上个月可用当前快照）─────────────────────


def test_concentration_only_for_last_month(tmp_path, monkeypatch):
    """current_positions 是当前快照：复盘上个月才可用，复盘更早月份必须 unavailable。"""
    monkeypatch.setattr(mr, "cn_today", lambda: date(2026, 8, 11))
    pos = tmp_path / "data" / "trades" / "current_positions.json"
    pos.parent.mkdir(parents=True, exist_ok=True)
    pos.write_text(
        json.dumps(
            [
                {"代码": "600001", "持有数量": 300, "单位成本": 10.0},  # 3000
                {"代码": "600002", "持有数量": 100, "单位成本": 10.0},  # 1000
            ]
        ),
        encoding="utf-8",
    )
    _write_ledger(tmp_path, [])
    r = mr.build_monthly_review(tmp_path, "2026-07")
    c = r["concentration"]
    assert c["n_positions"] == 2
    assert c["top1_pct"] == 75.0 and c["top3_pct"] == 100.0

    # 复盘 6 月（非上个月）：即使快照在，也不得冒充期末持仓
    r2 = mr.build_monthly_review(tmp_path, "2026-06")
    assert r2["concentration"] is None
    assert any("组合集中度" in u for u in r2["unavailable"])


# ── 产物落点与命名 ───────────────────────────────────────


def test_output_files_and_nine_sections(tmp_path):
    rc = mr.main(["--month", "2026-07", "--base", str(tmp_path)])
    assert rc == 0
    out = tmp_path / "artifacts" / "reports" / "monthly"
    assert (out / "2026-07_monthly_review.json").exists()
    md = (out / "2026-07_monthly_review.md").read_text(encoding="utf-8")
    # §七 固定结构九节，一节不缺
    for i, title in enumerate(
        [
            "月度市场环境",
            "月度收益、回撤、波动",
            "已实现/未实现盈亏",
            "规则表现",
            "计划内外交易",
            "选股池、主线识别",
            "规则版本表现",
            "重复错误和重大风险事件",
            "下月策略参数",
        ]
    ):
        assert f"## {i + 1}." in md and title in md, f"缺第 {i + 1} 节（{title}）"
    review = json.loads(
        (out / "2026-07_monthly_review.json").read_text(encoding="utf-8")
    )
    assert review["month"] == "2026-07"
