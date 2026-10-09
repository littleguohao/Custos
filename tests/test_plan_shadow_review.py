# -*- coding: utf-8 -*-
"""plan_shadow_review（#60 判据 C 事后打分，v0.311）钉测。

锁的契约：事件筛选（stage=1700 ∧ agree=False ∧ 来源非 default）、动作映射
（P0=T+1 首可卖日开盘清仓 / P1（P2 同档）=开盘卖半仓 / P3=不动）、跌停
顺延与全窗不可卖兜底（引擎 tradable_flags/_next_tradable 单源）、pending
未到期与 error 分列、空结果护栏非零退出不写产物、汇总均值/符号/胜率与
plan 更防守事件的 live MAE 副读数。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from custos.research import plan_shadow_review as psr


def _bars(rows):
    """rows=(date, open, close)；high/low 派生（非一字板），量恒 1000。"""
    return pd.DataFrame(
        {
            "date": [r[0] for r in rows],
            "open": [float(r[1]) for r in rows],
            "high": [max(r[1], r[2]) + 0.1 for r in rows],
            "low": [min(r[1], r[2]) - 0.1 for r in rows],
            "close": [float(r[2]) for r in rows],
            "volume": [1000.0] * len(rows),
        }
    )


def _event(
    day="2026-09-01",
    code="600000",
    plan_p="P0",
    live_p="P3",
    close=10.0,
    agree=False,
    stage="1700",
    source="candidate:2026-08-31",
):
    return {
        "schema": "plan_shadow_observation/v1",
        "date": day,
        "code": code,
        "stage": stage,
        "plan_source": source,
        "plan_stop_price": 9.3,
        "plan_tp": None,
        "shadow_signal": "plan_stop_breach",
        "shadow_priority": plan_p,
        "shadow_action": "计划止损位清仓评估",
        "live_final_priority": live_p,
        "live_action": "条件持有",
        "report_priority": live_p,
        "close": close,
        "entry_price": 10.2,
        "agree": agree,
        "recorded_at": "2026-09-01T17:05:00",
    }


def _write_ledger(path, rows):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return path


#: 下跌剧本：T 收 10.0 → T+1 开 9.0（收 9.05，−9.5% 未触跌停可卖）→ T+5 收 8.0 → T+10 收 7.5
FALL_ROWS = [
    ("2026-09-01", 10.1, 10.0),  # T（决策日）
    ("2026-09-02", 9.0, 9.05),  # T+1（可卖日，开盘 9.0；收盘 −9.5% 未跌停）
    ("2026-09-03", 8.8, 8.75),
    ("2026-09-04", 8.6, 8.5),
    ("2026-09-07", 8.4, 8.3),
    ("2026-09-08", 8.2, 8.0),  # T+5
    ("2026-09-09", 8.0, 7.95),
    ("2026-09-10", 7.9, 7.85),
    ("2026-09-11", 7.8, 7.7),
    ("2026-09-14", 7.65, 7.6),
    ("2026-09-15", 7.55, 7.5),  # T+10
]


class TestEventFilter:
    def test_filter_stage_agree_source(self, tmp_path):
        rows = [
            _event(code="600000"),  # 命中
            _event(code="600001", agree=True),  # 一致不是事件
            _event(code="600002", source="default"),  # default 视同无计划
            _event(code="600003", stage="1445"),  # 盘中口径不进打分
        ]
        p = _write_ledger(tmp_path / "led.jsonl", rows)
        n_rows, events, n_cont = psr.load_ledger_events(p)
        assert n_rows == 4
        assert [e["code"] for e in events] == ["600000"]
        assert n_cont == 0

    def test_missing_ledger_is_zero(self, tmp_path):
        assert psr.load_ledger_events(tmp_path / "none.jsonl") == (0, [], 0)

    def test_disagreement_segments_counted_once(self, tmp_path):
        """同 code 同 plan_source 连续不一致天只取段头（v0.314）——真按
        plan 执行第一天就已离场；段断（agree=True 介入/来源切换）另起新段。"""
        rows = [
            _event("2026-09-01", "600000"),  # 段 A 头
            _event("2026-09-02", "600000"),  # 段 A 续
            _event("2026-09-03", "600000"),  # 段 A 续
            _event("2026-09-04", "600000", agree=True),  # 段断
            _event("2026-09-07", "600000"),  # 段 B 头
            _event(
                "2026-09-08", "600000", source="candidate:2026-09-07"
            ),  # 来源换=新段头
            _event("2026-09-02", "600001"),  # 另一持仓独立事件
        ]
        p = _write_ledger(tmp_path / "led.jsonl", rows)
        _n, events, n_cont = psr.load_ledger_events(p)
        assert [(e["code"], e["date"]) for e in events] == [
            ("600000", "2026-09-01"),
            ("600001", "2026-09-02"),
            ("600000", "2026-09-07"),
            ("600000", "2026-09-08"),
        ]
        assert n_cont == 2


class TestPathReturns:
    def test_p0_vs_p3_plan_better(self):
        ev = psr.evaluate_event(_event(plan_p="P0", live_p="P3"), _bars(FALL_ROWS))
        d5 = ev["deltas"]["5"]
        assert d5["plan_ret"] == pytest.approx(-0.10)  # 9.0/10−1
        assert d5["live_ret"] == pytest.approx(-0.20)  # 8.0/10−1
        assert d5["delta"] == pytest.approx(+0.10)
        assert d5["plan_sell_executed"] is True
        d10 = ev["deltas"]["10"]
        assert d10["delta"] == pytest.approx(-0.10 - (-0.25))
        assert ev["live_mae"]["5"] == pytest.approx(-0.20)

    def test_p0_vs_p1_half_position(self):
        ev = psr.evaluate_event(_event(plan_p="P0", live_p="P1"), _bars(FALL_ROWS))
        d5 = ev["deltas"]["5"]
        # live P1：半仓 9.0 卖 + 半仓骑到 8.0 ⇒ (4.5+4.0)/10−1 = −0.15
        assert d5["live_ret"] == pytest.approx(-0.15)
        assert d5["delta"] == pytest.approx(+0.05)

    def test_pending_when_bars_short(self):
        ev = psr.evaluate_event(
            _event(day="2026-09-10"),
            _bars(FALL_ROWS),  # 决策日后只剩 2 根
        )
        assert ev["deltas"]["5"] is None and ev["deltas"]["10"] is None
        assert "error" not in ev  # 未到期≠数据错误

    def test_limit_down_postpones_sale(self):
        rows = [
            ("2026-09-01", 10.1, 10.0),  # T
            ("2026-09-02", 9.0, 9.0),  # T+1 跌停（−10%）不可卖
            ("2026-09-03", 8.6, 8.5),  # T+2 可卖（−5.6%）⇒ 顺延至此开盘
            ("2026-09-04", 8.4, 8.3),
            ("2026-09-07", 8.2, 8.1),
            ("2026-09-08", 8.0, 8.0),  # T+5
        ]
        ev = psr.evaluate_event(_event(plan_p="P0", live_p="P3"), _bars(rows))
        d5 = ev["deltas"]["5"]
        assert d5["plan_ret"] == pytest.approx(8.6 / 10.0 - 1.0)  # 顺延后 T+2 开盘
        assert d5["plan_sell_executed"] is True

    def test_all_locked_window_rides_to_end(self):
        # 全窗连跌停：10.0 → 9.0 → 8.1 → 7.29 → 6.561 → 5.9049（每天 −10%）
        closes = [10.0]
        for _ in range(5):
            closes.append(round(closes[-1] * 0.9, 4))
        rows = [(f"2026-09-{d + 1:02d}", closes[d], closes[d]) for d in range(6)]
        ev = psr.evaluate_event(_event(plan_p="P0", live_p="P3"), _bars(rows))
        d5 = ev["deltas"]["5"]
        assert d5["plan_sell_executed"] is False
        assert d5["plan_ret"] == pytest.approx(d5["live_ret"])  # 骑到末日=持有不动
        assert d5["delta"] == pytest.approx(0.0)

    def test_sellable_on_day_n_is_executed(self):
        """可卖搜索区间=[1, N] 含第 N 天（钉死防误读 [1, n−1]）：T+1~T+4
        连跌停、T+5 开板 ⇒ P0 在第 N 天开盘成交（不是按未卖兜底）。"""
        rows = [
            ("2026-09-01", 10.1, 10.0),  # T
            ("2026-09-02", 9.0, 9.0),  # T+1 跌停
            ("2026-09-03", 8.1, 8.1),  # T+2 跌停
            ("2026-09-04", 7.29, 7.29),  # T+3 跌停
            ("2026-09-07", 6.56, 6.56),  # T+4 跌停
            ("2026-09-08", 6.6, 6.7),  # T+5 开板（+2.1%）⇒ 第 N 天可卖
        ]
        ev = psr.evaluate_event(_event(plan_p="P0", live_p="P3"), _bars(rows))
        d5 = ev["deltas"]["5"]
        assert d5["plan_sell_executed"] is True
        assert d5["plan_ret"] == pytest.approx(6.6 / 10.0 - 1.0)

    def test_decision_day_halted_marks_error(self):
        """决策日停牌/缺数据（bar 序列首日≠决策日）⇒ error（close0 与 bar
        序列对不上），不硬算。"""
        rows = [r for r in FALL_ROWS if r[0] != "2026-09-01"]
        ev = psr.evaluate_event(_event(day="2026-09-01"), _bars(rows))
        assert "决策日无 bar" in ev["error"]
        assert ev["deltas"] == {}


class TestCli:
    def _run(self, tmp_path, rows, dfs):
        led = _write_ledger(tmp_path / "led.jsonl", rows)
        return psr.main(
            [
                "--ledger",
                str(led),
                "--tag",
                "t1",
                "--out-dir",
                str(tmp_path),
            ],
            bars_loader=lambda c: dfs.get(c),
        )

    def test_empty_guard_no_artifact(self, tmp_path):
        rc = self._run(tmp_path, [_event(agree=True)], {"600000": _bars(FALL_ROWS)})
        assert rc == 2
        assert not (tmp_path / "t1").exists(), "空结果护栏：0 事件不写产物"

    def test_end_to_end_summary(self, tmp_path):
        rows = [
            _event(code="600000", plan_p="P0", live_p="P3"),  # Δ5=+0.10
            _event(code="600001", plan_p="P0", live_p="P1"),  # Δ5=+0.05
            _event(code="600002", day="2026-09-10"),  # 未到期
            _event(code="600003", agree=True),  # 非事件
        ]
        dfs = {c: _bars(FALL_ROWS) for c in ("600000", "600001", "600002")}
        rc = self._run(tmp_path, rows, dfs)
        assert rc == 0
        rep = json.loads(
            (tmp_path / "t1" / "_plan_shadow_review__t1.json").read_text(
                encoding="utf-8"
            )
        )
        assert rep["n_events"] == 3 and rep["n_ledger_rows"] == 4
        assert rep["n_continuation_rows"] == 0  # 三事件各独立（不同持仓）
        assert rep["n_distinct_codes"] == 3
        s5 = rep["summary"]["5"]
        assert s5["n"] == 2 and s5["n_pending"] == 1
        assert s5["mean"] == pytest.approx((0.10 + 0.05) / 2)
        assert s5["n_pos"] == 2 and s5["win_rate"] == pytest.approx(1.0)
        assert "merge_check" in s5  # E 判据 live 半的「不更差」读法指引
        # 副读数：两个事件 plan=P0 且 live≠P0 ⇒ 都进 MAE 集
        mae5 = rep["mae_avoided"]["5"]
        assert mae5["n"] == 2
        assert mae5["mean"] < 0 and mae5["worst"] == pytest.approx(-0.20)
        assert rep["summary"]["10"]["n"] == 2
        assert rep["schema"] == "plan_shadow_review/v1"
