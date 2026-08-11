# -*- coding: utf-8 -*-
"""⚠️ TODO #33 的实际处置：**不合并，改为交叉核对**。

原待办写「`backtest_0amv_bear_regime.check_positions` 与新的
`reconcile_positions` 功能重叠，可让研究脚本改调用后者」。
2026-08-11 核实后**这个前提不成立**：

    reconcile_positions       忠实回放台账（委托 incremental_ledger.compute_positions），
                              无情景分支；`diff_positions` 只返回**有差异**的代码，
                              比数量 **和** 单位成本，并分类 kind
    backtest_0amv_bear_regime **反事实情景模拟器**：no_bear_buys 跳过空头期买入、
                              rebound_reduce 反弹减仓；lots 带 bear/other 标签做归因；
                              处理转债转入/拆股、跟踪现金与浮盈
    check_positions           产出**全部代码**的对照表供研究报告用，只比数量

⇒ 反事实模拟无法用忠实回放实现（那是它存在的理由）；`check_positions` 与
`diff_positions` 的**输出契约也不同**（全表 vs 仅差异），直接替换会改研究报告内容。

**但有件更有价值的事**：`actual` 情景应当与生产回放**得出同一份持仓**。
若不一致，那三个情景的对比就建立在与生产不同的基线上，
而 R4/R10 的结论正引用这份回测。本文件钉住这一点。
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("src", "src/custos/core/trades", "src/research"):
    sys.path.insert(0, str(ROOT / _p))


def _write_ledger(path: pathlib.Path, rows):
    """写主台账 CSV。列与生产口径一致（含 `发生金额`，`load_ledger` 必填）：

    买入 = -(成交金额+费用)，卖出 = 成交金额-费用。本文件只核对**数量**，
    现金流只要求列存在、口径大致正确。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["成交日期", "成交时间", "代码", "名称", "交易类别",
                    "成交数量", "成交价格", "成交金额", "费用", "发生金额"])
        for r in rows:
            date, time, code, name, cat, qty, price, amount, fee = r
            cash = (amount - fee) if cat == "卖出" else -(amount + fee)
            w.writerow([*r, cash])


class TestActualScenarioAgreesWithProductionReplay:
    """`actual` 情景的期末持仓 == 生产回放的持仓（数量口径）。

    核对的是这两层（同一份台账 CSV 各走一遍）：

    - 生产侧：`incremental_ledger.compute_positions`（reconcile_positions 的同一条路径）
    - 研究侧：`backtest_0amv_bear_regime.load_ledger` + `run_scenario("actual")`
    """

    ROWS = [
        ("2026-07-01", "09:31", "600000", "浦发银行", "买入", 1000, 10.00, 10000.0, 5.0),
        ("2026-07-03", "10:05", "600000", "浦发银行", "买入", 500, 10.50, 5250.0, 3.0),
        ("2026-07-10", "14:20", "600000", "浦发银行", "卖出", 800, 11.00, 8800.0, 6.0),
        ("2026-07-15", "09:40", "000001", "平安银行", "买入", 2000, 12.00, 24000.0, 8.0),
    ]
    EXPECTED = {"600000": 700.0, "000001": 2000.0}   # 1000+500-800；2000

    def test_qty_matches_incremental_ledger(self, tmp_path):
        """⚠️ 两条推导路径必须给出同一份数量 —— 真跑两侧再互相比对。

        差异的后果：反事实情景（no_bear_buys / rebound_reduce）都以 `actual`
        为基线做减法，基线错了三个数字全错，而 R4/R10 引用了这份回测。
        """
        from custos.core.trades.incremental_ledger import compute_positions
        from custos.research import backtest_0amv_bear_regime as br

        led = tmp_path / "master_trade_ledger.csv"
        _write_ledger(led, self.ROWS)

        import pandas as pd
        df = pd.read_csv(led, dtype={"代码": str})
        # ⚠️ 签名是 `compute_positions(new, current_rows)` —— 它是「把成交应用到
        #    一份持仓快照上」，不是「从零回放」。从零回放 = 传空快照。
        #    第一版按单参调用，TypeError；这类签名只有真调一次才知道。
        prod = compute_positions(df, [])
        prod_qty = {str(p["代码"]).zfill(6): float(p["持有数量"]) for p in prod}

        # 研究侧：同一份台账走 load_ledger → run_scenario("actual")。
        # regime/prices 不影响 actual 的数量（regime 只决定批次标签，
        # 无行情时市值按成本折算），给最小输入即可。
        trades = br.load_ledger(led)
        days = sorted({t["date"] for t in trades})
        res = br.run_scenario(trades, regime_map={}, amv_days=days, prices={},
                              scenario="actual", sell_fee_rate=0.0,
                              start=days[0], end=days[-1])
        research_qty = {code: p["qty"] for code, p in res["positions_end"].items()}

        assert prod_qty == self.EXPECTED, f"生产回放偏离手算基准：{prod_qty}"
        assert research_qty == self.EXPECTED, f"研究 actual 情景偏离手算基准：{research_qty}"
        assert research_qty == prod_qty, \
            f"两条路径不一致：研究 {research_qty} vs 生产 {prod_qty}"

    def test_check_positions_reports_full_table_not_just_diffs(self, tmp_path,
                                                              monkeypatch):
        """⚠️ 钉住 `check_positions` 的**输出契约**：全部代码都要在表里。

        这正是它不能被 `diff_positions` 替换的原因 —— 后者只返回有差异的。
        研究报告需要「重放 vs 台账」的完整对照，包括一致的那些
        （「一致」本身是结论，不是「无内容」）。
        """
        from custos.research import backtest_0amv_bear_regime as br

        pos = tmp_path / "current_positions.json"
        pos.write_text(json.dumps([{"代码": "600000", "持有数量": 700},
                                   {"代码": "000001", "持有数量": 2000}]),
                       encoding="utf-8")
        rows = br.check_positions({"600000": {"qty": 700.0},
                                   "000001": {"qty": 2000.0}},
                                  positions_path=pos)
        assert len(rows) == 2, "一致的代码也必须出现在对照表里"
        assert all(abs(r["diff"]) < 1e-9 for r in rows)
        assert {r["code"] for r in rows} == {"600000", "000001"}

    def test_check_positions_includes_codes_only_on_one_side(self, tmp_path):
        """只在一侧出现的代码必须出现（union，不是 intersection）——
        「台账有而持仓没有」正是要暴露的情况。"""
        from custos.research import backtest_0amv_bear_regime as br

        pos = tmp_path / "current_positions.json"
        pos.write_text(json.dumps([{"代码": "000001", "持有数量": 2000}]),
                       encoding="utf-8")
        rows = br.check_positions({"600000": {"qty": 700.0}}, positions_path=pos)
        by = {r["code"]: r for r in rows}
        assert by["600000"]["diff"] == 700.0, "只在重放里的票，diff 应为正"
        assert by["000001"]["diff"] == -2000.0, "只在持仓里的票，diff 应为负"


class TestWhyNotMerged:
    """把「为什么不合并」写成可执行的断言，免得下次又有人提这件事。"""

    def test_reconcile_returns_only_diffs(self):
        """`diff_positions` 只返回差异 —— 与 `check_positions` 的全表契约不同。"""
        from custos.core.trades.reconcile_positions import diff_positions

        same = [{"代码": "600000", "持有数量": 700, "单位成本": 10.0}]
        assert diff_positions(same, same) == [], "一致时应返回空列表"

    def test_reconcile_also_compares_unit_cost(self):
        """它还比**单位成本** —— `check_positions` 不比，合并会引入新的失败面。"""
        from custos.core.trades.reconcile_positions import diff_positions

        a = [{"代码": "600000", "持有数量": 700, "单位成本": 10.0}]
        b = [{"代码": "600000", "持有数量": 700, "单位成本": 10.5}]
        out = diff_positions(a, b)
        assert len(out) == 1 and out[0]["kind"] == "cost_mismatch"

    def test_research_scenarios_are_counterfactual(self):
        """⚠️ 研究脚本的三个情景里有两个是**反事实**的 ——
        忠实回放函数按定义做不到，所以不能替换。"""
        from custos.research import backtest_0amv_bear_regime as br

        assert set(br.SCENARIOS) >= {"actual", "no_bear_buys", "rebound_reduce"}, \
            f"情景集合变了：{br.SCENARIOS}"
        import inspect
        src = inspect.getsource(br.run_scenario)
        assert "no_bear_buys" in src and "skipped_buys" in src, \
            "run_scenario 不再做反事实模拟？那可以重新评估 #33"
