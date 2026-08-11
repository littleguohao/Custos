# -*- coding: utf-8 -*-
"""`screening/financials` —— 18:00 选股链的财务维度（**best-effort**）。

⚠️ 它在**逐票循环**里被调用（几十到上千只），所以两条硬要求：
① **绝不 raise** —— 财务只是候选充实的一个维度，抛异常会让整条选股链失败；
② **带缓存** —— 不缓存会把一次读盘放大成几百次。

⚠️ 通达信财务表**有重复列名** ⇒ `row.get(col)` 返回 Series 而不是标量。
不处理会让报告里出现 `0    20260630\nName: rp, dtype: object` 这种东西。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("src", "src/pipeline/screening", "src/datasource/local_tdx"):
    sys.path.insert(0, str(ROOT / _p))

from screening import financials as fin  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_fin_cache():
    """`_fin_cache` 是模块级全局缓存 —— 测试写进去的条目（含 `latest: None`）
    不还原会泄漏给同进程里后跑的其他测试文件（2026-08-11 评审指出）。"""
    saved = dict(fin._fin_cache)
    try:
        yield
    finally:
        fin._fin_cache.clear()
        fin._fin_cache.update(saved)


class TestLoadFinancialsBestEffort:
    """⚠️ `load_financials` 是 **best-effort、绝不 raise、带缓存**（docstring）。

    它在 18:00 选股链的逐票循环里被调用 —— 抛异常会让整条链失败，
    而财务只是候选充实的一个维度。
    """

    def _clear(self):
        fin._fin_cache.clear()

    def test_import_failure_returns_none_not_raise(self, monkeypatch):
        self._clear()
        import builtins
        real = builtins.__import__

        def blow(name, *a, **k):
            if name == "local_tdx_data":
                raise ImportError("no tdx here")
            return real(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", blow)
        assert fin.load_financials("20260630") is None

    def test_getter_exception_returns_none(self, monkeypatch):
        self._clear()
        import types
        mod = types.ModuleType("local_tdx_data")
        mod.get_financial_data = lambda p: (_ for _ in ()).throw(RuntimeError("boom"))
        monkeypatch.setitem(sys.modules, "local_tdx_data", mod)
        assert fin.load_financials("20260630") is None

    def test_result_is_cached_by_period(self, monkeypatch):
        """⚠️ 缓存按报告期分键 —— 逐票循环里每票都调一次，不缓存会重复读盘。"""
        self._clear()
        calls = {"n": 0}
        import types
        import pandas as pd
        mod = types.ModuleType("local_tdx_data")

        def get(period):
            calls["n"] += 1
            return pd.DataFrame({"x": [1]})
        mod.get_financial_data = get
        monkeypatch.setitem(sys.modules, "local_tdx_data", mod)
        fin.load_financials("20260630")
        fin.load_financials("20260630")
        assert calls["n"] == 1, f"同一报告期应只读一次，实际 {calls['n']}"
        fin.load_financials("20260331")
        assert calls["n"] == 2, "不同报告期必须各读一次"

    def test_none_result_is_also_cached(self):
        """⚠️ 失败结果也要缓存 —— 否则 TDX 不可用时每票都去重试一遍，
        把一次失败放大成几百次超时。"""
        self._clear()
        fin._fin_cache["latest"] = None
        assert fin.load_financials("") is None


class TestCellText:
    """`_cell_text` 取字符串单元格（如报告期）—— **绝不 raise**。"""

    def test_missing_logical_column_is_empty(self):
        assert fin._cell_text({"a": 1}, {}, "report_period") == ""

    def test_none_row_is_empty(self):
        assert fin._cell_text(None, {"report_period": "a"}, "report_period") == ""

    def test_duplicate_columns_take_first_non_empty(self):
        """⚠️ 通达信财务表**有重复列名** ⇒ `row.get()` 返回 Series。
        取首个非空值，而不是让它变成 `"0    xxx\\nName: ..."` 那种字符串。"""
        import pandas as pd
        row = pd.Series([None, "20260630"], index=["rp", "rp"])
        out = fin._cell_text({"rp": row["rp"]} if not hasattr(row["rp"], "iloc") else
                             {"rp": row}, {"report_period": "rp"}, "report_period")
        assert "Name:" not in out, f"Series 泄漏进文案：{out!r}"

    def test_getter_exception_is_swallowed(self):
        class Bad:
            def get(self, k):
                raise RuntimeError("bad row")
        assert fin._cell_text(Bad(), {"report_period": "rp"}, "report_period") == ""
