"""Excel 全量导入（灾备路径）的测试。

⚠️ **为什么必须测**：覆盖率清点时它是 **0%**。它是 `MASTER_WORKFLOW §十二` 第 1 条里
「Excel 全量导入降级为历史迁移/灾备」的那条路径 —— **只在首次导入或台账损坏后才跑**，
也就是**出事的时候才跑**。那时候更不能有 bug，而恰恰那时候没人有余力调试。

同批清点还查出 `sync_trades.py`（更老一代、config 驱动）是**死代码**：
零调用、依赖的 `trades_config.json` 不存在（`load_config()` 必然抛错）、
且有三个真 bug（输出路径指向仓库外、BJ 代码判成沪市、死 fallback），已删除。
⇒ **覆盖率低的地方要先问「这段代码该不该存在」**，给该删的代码写测试是浪费。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "custos" / "core" / "trades"))

from custos.core.trades import standardize_trades as st  # noqa: E402

TRADE_COLS = ["成交日期", "成交时间", "代码", "名称", "交易类别",
              "成交数量", "成交价格", "成交金额", "发生金额", "费用", "备注"]


def _xlsx(tmp_path, trades=None, closed=None, pos=None):
    """造一份三 sheet 的 xlsx。"""
    trades = trades if trades is not None else [
        ["2026-08-03", "09:31:00", "600000", "浦发银行", "买入", 1000, 10.0, 10000, -10005, 5.0, ""],
        ["2026-08-04", "14:00:00", "600000", "浦发银行", "卖出", 400, 11.0, 4400, 4395, 5.0, ""],
        ["2026-08-04", "15:00:00", "600000", "浦发银行", "银行转证券", 0, 0, 0, 0, 0, "非买卖"],
    ]
    closed = closed if closed is not None else [["000001", "平安银行", "2026-07-01", "2026-07-20", 3.5]]
    pos = pos if pos is not None else [
        ["600000", "浦发银行", 0.12, 0.034, 5, 10.02, 10.36],
        ["汇总", "", 0.12, 0.034, 5, 0, 0],          # 汇总行必须被剔除
    ]
    p = tmp_path / "trades.xlsx"
    with pd.ExcelWriter(p) as w:
        pd.DataFrame(trades, columns=TRADE_COLS).to_excel(w, sheet_name="交易记录", index=False)
        pd.DataFrame(closed, columns=["代码", "名称", "建仓日期", "清仓日期", "收益率"]
                     ).to_excel(w, sheet_name="已清仓", index=False)
        pd.DataFrame(pos, columns=["代码", "名称", "仓位占比", "持有盈亏率", "持仓天数",
                                   "单位成本", "最新价"]).to_excel(w, sheet_name="持仓数据", index=False)
    return p


@pytest.fixture()
def out_dir(tmp_path, monkeypatch):
    d = tmp_path / "out"
    monkeypatch.setattr(st, "OUT_DIR", d)
    return d


class TestFullImport:
    def test_writes_all_five_artifacts(self, tmp_path, out_dir, monkeypatch, capsys):
        src = _xlsx(tmp_path)
        monkeypatch.setattr(sys, "argv", ["standardize_trades", "--src", str(src)])
        st.main()
        for f in ("trades_all.csv", "trades_stock.json", "closed_positions.json",
                  "current_positions.json", "_import_meta.json"):
            assert (out_dir / f).exists(), f"缺产物 {f}"

    def test_stock_json_excludes_non_trade_categories(self, tmp_path, out_dir, monkeypatch):
        """`银行转证券` 之类的流水不该进买卖明细 —— 否则会被当成成交去算持仓。"""
        src = _xlsx(tmp_path)
        monkeypatch.setattr(sys, "argv", ["x", "--src", str(src)])
        st.main()
        rows = json.loads((out_dir / "trades_stock.json").read_text(encoding="utf-8"))
        assert len(rows) == 2
        assert {r["交易类别"] for r in rows} == {"买入", "卖出"}

    def test_summary_row_removed_from_positions(self, tmp_path, out_dir, monkeypatch):
        """xlsx 的「汇总」行必须剔除 —— 它会变成一只代码为「汇总」的假持仓。"""
        src = _xlsx(tmp_path)
        monkeypatch.setattr(sys, "argv", ["x", "--src", str(src)])
        st.main()
        rows = json.loads((out_dir / "current_positions.json").read_text(encoding="utf-8"))
        assert [r["代码"] for r in rows] == ["600000"]

    def test_codes_normalized_via_code_utils(self, tmp_path, out_dir, monkeypatch):
        """代码规范化必须走 `code_utils.clean_code`（唯一实现）。

        ⚠️ 已删的 `sync_trades.py` 自己内联了一套后缀规则，实测把 `920808`（北交所）
        判成 `.SH`（因为以 `9` 开头）、把 `880006`（板块指数）判成 `.BJ` ——
        与昨天在 `mootdx.utils.get_stock_market` 里查出的是**同一个 bug 形状**。
        """
        src = _xlsx(tmp_path, trades=[
            ["2026-08-03", "09:31:00", "920808.0", "北交所票", "买入", 100, 10.0, 1000, -1005, 5.0, ""],
            ["2026-08-03", "09:32:00", "1", "补零票", "买入", 100, 10.0, 1000, -1005, 5.0, ""],
        ])
        monkeypatch.setattr(sys, "argv", ["x", "--src", str(src)])
        st.main()
        rows = json.loads((out_dir / "trades_stock.json").read_text(encoding="utf-8"))
        codes = {r["代码"] for r in rows}
        assert codes == {"920808", "000001"}, f"代码规范化不对：{codes}"

    def test_meta_records_source_and_counts(self, tmp_path, out_dir, monkeypatch):
        """导入元数据是持仓新鲜度判定的依据（`runtime_guards` 读 `_import_meta.json`）。"""
        src = _xlsx(tmp_path)
        monkeypatch.setattr(sys, "argv", ["x", "--src", str(src)])
        st.main()
        m = json.loads((out_dir / "_import_meta.json").read_text(encoding="utf-8"))
        assert m["source_path"] == str(src)
        assert m["rows"]["trades_all"] == 3 and m["rows"]["trades_stock"] == 2
        assert m["rows"]["current_positions"] == 1
        assert m["sheets"] == ["持仓数据", "已清仓", "交易记录"]
        assert m["imported_at"] and m["source_mtime"]

    def test_trades_sorted_by_time(self, tmp_path, out_dir, monkeypatch):
        """乱序输入必须按 (日期, 时间) 排好 —— 下游回放持仓依赖顺序。"""
        src = _xlsx(tmp_path, trades=[
            ["2026-08-05", "10:00:00", "600000", "x", "卖出", 100, 11.0, 1100, 1095, 5.0, ""],
            ["2026-08-03", "09:31:00", "600000", "x", "买入", 500, 10.0, 5000, -5005, 5.0, ""],
        ])
        monkeypatch.setattr(sys, "argv", ["x", "--src", str(src)])
        st.main()
        df = pd.read_csv(out_dir / "trades_all.csv")
        assert list(df["交易类别"]) == ["买入", "卖出"]


class TestGuards:
    def test_missing_src_raises(self, tmp_path, out_dir, monkeypatch):
        """源文件不存在必须**明确报错**，不能静默产出空台账覆盖掉好的。"""
        monkeypatch.setattr(sys, "argv", ["x", "--src", str(tmp_path / "nope.xlsx")])
        with pytest.raises(FileNotFoundError):
            st.main()
        assert not out_dir.exists(), "报错前不该已经建了输出目录"

    def test_src_is_required(self):
        """`--src` 必填：灾备脚本不该猜输入文件 —— 猜错会用错误的 xlsx 覆盖持仓快照。"""
        import argparse
        with pytest.raises(SystemExit):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(sys, "argv", ["x"])
                st.main()

    def test_no_dead_fallback_left(self):
        """`find_latest_xlsx` 是死代码（argparse required=True ⇒ 永不可达），已删。"""
        assert not hasattr(st, "find_latest_xlsx")
        assert not hasattr(st, "DEFAULT_SRC_DIR")

    def test_docstring_warns_about_mixing_with_incremental(self):
        """必须写明**不能与 incremental_ledger 混用** —— 一个覆盖式、一个增量式，
        混用后台账与持仓快照会不一致（那正是 reconcile_positions 要检测的失配）。"""
        s = (ROOT / "src" / "custos" / "core" / "trades" / "standardize_trades.py").read_text(encoding="utf-8")
        assert "不能混用" in s and "reconcile_positions" in s
