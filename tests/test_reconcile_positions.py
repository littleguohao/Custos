"""台账↔持仓对账。

2026-08-06 review `07_tools/trades/` 时发现的缺口：
`incremental_ledger._commit` 刻意选择「ledger 先落、positions 后落」这个失败顺序，
理由是崩在两次 `os.replace` 之间留下的「已记录成交但持仓未更新」**可检测、可修复**；
反过来会让下次导入把同一批成交再算一遍（持仓静默翻倍，真实发生过）。

**但没有任何常规检查在「检测」它**：唯一的对账逻辑在
`backtest_0amv_bear_regime.py`（自称「不触碰任何管线」的研究脚本）里，
而 `runtime_guards` 读台账只判新鲜度、不校验「持仓 == 台账回放」。
⇒ 「detectable」在设计上成立、在运行上不成立。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "07_tools"))
sys.path.insert(0, str(ROOT / "07_tools" / "trades"))

import reconcile_positions as rp  # noqa: E402

COLS = ["成交日期", "成交时间", "代码", "名称", "交易类别", "成交数量",
        "成交价格", "成交金额", "发生金额", "费用", "备注"]


def _ledger(tmp, rows):
    p = tmp / "master_trade_ledger.csv"
    pd.DataFrame(rows, columns=COLS).to_csv(p, index=False, encoding="utf-8-sig")
    return p


def _row(day, t, code, cat, qty, price, fee=0.0):
    return [day, t, code, "测试", cat, qty, price, qty * price, qty * price, fee, ""]


def _pos(tmp, rows):
    p = tmp / "current_positions.json"
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return p


class TestReplay:
    def test_buy_then_partial_sell(self, tmp_path):
        led = _ledger(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "买入", 1000, 10.0, 5.0),
                                 _row("2026-08-04", "14:00:00", "600000", "卖出", 400, 11.0)])
        r = rp.replay_ledger(led)
        assert r["ok"] and r["trade_rows"] == 2
        assert r["positions"][0]["持有数量"] == 600

    def test_non_trade_categories_ignored(self, tmp_path):
        """银行转证券之类的流水不该影响持仓。"""
        led = _ledger(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "买入", 100, 10.0),
                                 _row("2026-08-03", "10:00:00", "600000", "银行转证券", 0, 0)])
        assert rp.replay_ledger(led)["positions"][0]["持有数量"] == 100

    def test_out_of_order_rows_are_sorted(self, tmp_path):
        """CSV 里行序可能是乱的；**先卖后买的顺序会把合法卖出误判成超卖**。"""
        led = _ledger(tmp_path, [_row("2026-08-05", "10:00:00", "600000", "卖出", 500, 11.0),
                                 _row("2026-08-03", "09:31:00", "600000", "买入", 1000, 10.0)])
        r = rp.replay_ledger(led)
        assert r["ok"], f"排序失效导致误判超卖：{r['error']}"
        assert r["positions"][0]["持有数量"] == 500

    def test_oversell_is_a_finding_not_a_crash(self, tmp_path):
        """超卖是**诊断结论**，不是运行故障 —— 调用方要拿到结论而非 traceback。

        它恰恰说明台账不完整（期初持仓未计入）或台账本身有缺陷。
        """
        led = _ledger(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "卖出", 100, 10.0)])
        r = rp.replay_ledger(led)
        assert r["ok"] is False and "oversell" in r["error"]

    def test_baseline_fixes_incomplete_ledger(self, tmp_path):
        """台账非从零开始时，靠 `--baseline` 传期初持仓。"""
        led = _ledger(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "卖出", 100, 10.0)])
        base = [{"代码": "600000", "名称": "测试", "持有数量": 300.0, "单位成本": 9.0}]
        r = rp.replay_ledger(led, baseline=base)
        assert r["ok"] and r["positions"][0]["持有数量"] == 200

    def test_missing_ledger_reported(self, tmp_path):
        r = rp.replay_ledger(tmp_path / "nope.csv")
        assert r["ok"] is False and r["error"] == "ledger_missing"


class TestDiff:
    def test_identical_is_clean(self):
        rows = [{"代码": "600000", "持有数量": 600.0, "单位成本": 10.0}]
        assert rp.diff_positions(rows, [dict(r) for r in rows]) == []

    def test_qty_mismatch_flagged(self):
        d = rp.diff_positions([{"代码": "600000", "持有数量": 600.0, "单位成本": 10.0}],
                              [{"代码": "600000", "持有数量": 1200.0, "单位成本": 10.0}])
        assert d[0]["kind"] == "qty_mismatch" and d[0]["qty_diff"] == -600.0

    def test_only_in_one_side(self):
        d = rp.diff_positions([{"代码": "600000", "持有数量": 100.0, "单位成本": 1.0}], [])
        assert d[0]["kind"] == "only_in_replay"

    def test_cost_float_residue_tolerated(self):
        """回放与增量累加的运算顺序不同，末位尾差不该报警。"""
        d = rp.diff_positions([{"代码": "600000", "持有数量": 100.0, "单位成本": 10.000000000001}],
                              [{"代码": "600000", "持有数量": 100.0, "单位成本": 10.0}])
        assert d == []

    def test_real_cost_diff_flagged(self):
        d = rp.diff_positions([{"代码": "600000", "持有数量": 100.0, "单位成本": 10.5}],
                              [{"代码": "600000", "持有数量": 100.0, "单位成本": 10.0}])
        assert d[0]["kind"] == "cost_mismatch"

    def test_qty_has_no_tolerance(self):
        """数量是整数股，float 对 2^53 内整数精确 ⇒ **有差就是真有差**，不设容差。"""
        d = rp.diff_positions([{"代码": "600000", "持有数量": 100.0, "单位成本": 1.0}],
                              [{"代码": "600000", "持有数量": 100.0000001, "单位成本": 1.0}])
        assert d and d[0]["qty_diff"] != 0


class TestReconcileStatus:
    def _run(self, tmp_path, led_rows, pos_rows):
        return rp.reconcile(_ledger(tmp_path, led_rows), _pos(tmp_path, pos_rows))

    def test_ok(self, tmp_path):
        r = self._run(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "买入", 100, 10.0)],
                      [{"代码": "600000", "名称": "测试", "持有数量": 100.0, "单位成本": 10.0}])
        assert r["status"] == "ok" and r["qty_mismatch_count"] == 0

    def test_detects_the_crash_window_failure(self, tmp_path):
        """**这就是要检测的那个失败模式**：台账记了成交，持仓没更新。"""
        r = self._run(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "买入", 100, 10.0)], [])
        assert r["status"] == "mismatch" and r["qty_mismatch_count"] == 1
        assert r["diffs"][0]["kind"] == "only_in_replay"

    def test_detects_doubled_positions(self, tmp_path):
        """反向失败模式：持仓被算了两遍（历史上真实发生过的缺陷）。"""
        r = self._run(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "买入", 100, 10.0)],
                      [{"代码": "600000", "持有数量": 200.0, "单位成本": 10.0}])
        assert r["qty_mismatch_count"] == 1 and r["diffs"][0]["qty_diff"] == -100.0

    def test_cost_only_diff_is_a_softer_status(self, tmp_path):
        """成本差单独一档 —— 多为浮点或期初基线问题，不该与「持仓脱节」同级。"""
        r = self._run(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "买入", 100, 10.0)],
                      [{"代码": "600000", "持有数量": 100.0, "单位成本": 12.0}])
        assert r["status"] == "cost_only_diff" and r["qty_mismatch_count"] == 0

    def test_replay_failed_status(self, tmp_path):
        r = self._run(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "卖出", 100, 10.0)], [])
        assert r["status"] == "replay_failed"


class TestReusesSingleSourceOfTruth:
    def test_replay_uses_incremental_ledger_compute_positions(self):
        """回放必须复用 `incremental_ledger.compute_positions`。

        否则「对账」只是在比两个都可能错的实现 —— 持仓推导逻辑必须只有一份。

        ⚠️ 判据用 **AST** 而非源码字符串（2026-08-07 改）：
        原判据是 `"from incremental_ledger import" in src` —— 那行出现在注释里、
        或者 import 了却没调用，都照样通过。现在直接查 `replay_ledger` 函数体内
        **确有一次 `compute_positions(...)` 调用**。
        原本还有一条 `assert "交易类别" not in ... or True` —— `or True` 让它
        恒真，等于没有。已去掉 `or True`，实测本来就成立。
        """
        import ast

        src = (ROOT / "07_tools" / "trades" / "reconcile_positions.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        imported = {a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) and n.module == "incremental_ledger"
                    for a in n.names}
        assert "compute_positions" in imported, \
            f"必须从 incremental_ledger 导入 compute_positions，实际导入 {imported}"

        fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name == "replay_ledger"), None)
        assert fn is not None, "replay_ledger 改名了？"

        called = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert "compute_positions" in called, \
            f"replay_ledger 必须真的调用 compute_positions，实际调用 {sorted(called)}"

        # 不许自己再写一遍买卖分支：函数体内不得出现**方向**字面量。
        # ⚠️ `"交易类别"` 不在禁用清单里 —— 它是读台账 CSV 的**列名**，
        #    合法。原判据把它一起禁了，于是恒假，当初被加 `or True` 掩掉
        #    （掩的是判据的错，不是代码的错）。类别取值本身来自常量
        #    `TRADE_CATEGORIES`，不是散落的字面量。
        literals = {n.value for n in ast.walk(fn)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for bad in ("买入", "卖出"):
            assert bad not in literals, \
                f"replay_ledger 出现方向字面量 {bad!r} —— 应复用 compute_positions 而非自己实现"

class TestDefaultIsAdvisory:
    def test_strict_off_returns_zero_even_on_mismatch(self, tmp_path, monkeypatch, capsys):
        """默认只报告不阻断 —— 新校验先观察若干交易日再开硬闸。

        依据 2026-07-30 事故：门控与口径同时收紧导致 17:00 整条链失败。
        """
        led = _ledger(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "买入", 100, 10.0)])
        monkeypatch.setattr(rp, "LEDGER", led)
        monkeypatch.setattr(rp, "POS", _pos(tmp_path, []))
        monkeypatch.setattr(rp, "QUALITY_DIR", tmp_path)
        assert rp.main(["--date", "2026-08-03"]) == 0
        assert (tmp_path / "2026-08-03_ledger_reconcile.json").exists(), "结果必须落盘"

    def test_strict_on_returns_one(self, tmp_path, monkeypatch):
        led = _ledger(tmp_path, [_row("2026-08-03", "09:31:00", "600000", "买入", 100, 10.0)])
        monkeypatch.setattr(rp, "LEDGER", led)
        monkeypatch.setattr(rp, "POS", _pos(tmp_path, []))
        monkeypatch.setattr(rp, "QUALITY_DIR", tmp_path)
        assert rp.main(["--date", "2026-08-03", "--strict"]) == 1


class TestWiredIntoDailyChain:
    """对账必须真的每天跑 —— 否则「可检测」还是停在设计上。

    接在 17:00 链（`run_1700`）里，**非阻断**：新校验先观察若干交易日再考虑开硬闸
    （2026-07-30 的教训是别同时收紧多个闸）。
    """

    SRC = (ROOT / "07_tools" / "run_1700.py").read_text(encoding="utf-8")

    def test_stage_present(self):
        assert "reconcile_positions.py" in self.SRC, "17:00 链未接入对账"
        assert '"ledger_reconcile"' in self.SRC

    def test_not_blocking(self):
        """失败只打 WARN，不 return 1 —— 对账挂了不该让整条盘后链失败。"""
        i = self.SRC.index('"ledger_reconcile"')
        seg = self.SRC[i:i + 400]
        assert "不阻断" in seg
        assert "return 1" not in seg, "对账失败不该中断盘后链"

    def test_note_computed_after_stage_runs(self):
        """⚠️ note 必须在 stage 跑完**之后**算。

        `_run_stage(cmd, name, note=X)` 的 `X` 是实参，会在子进程启动**前**求值 ——
        那时今天的对账 JSON 还没写，读到的是上一次的结果或读不到，
        run log 里就会留下一条**看起来正常的陈旧结论**（比没有更糟）。
        """
        i = self.SRC.index('"ledger_reconcile"')
        seg = self.SRC[i:i + 300]
        assert "note=_reconcile_note" not in seg, \
            "note 作为实参会在 stage 之前求值 —— 应在 stage 之后写入 stages_log[-1]"
        assert 'stages_log[-1]["note"] = _reconcile_note' in seg

    def test_note_helper_degrades_loudly(self):
        """读不到对账结果时要明确说「读不到」，不能返回空串装作正常。"""
        assert "reconcile_json_unreadable" in self.SRC
