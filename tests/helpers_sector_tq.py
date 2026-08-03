# -*- coding: utf-8 -*-
"""测试替身:部分板块成功的 TQ(用于板块指数成功率门槛回归)。"""
from __future__ import annotations


class PartialTQ:
    """只对 ``ok_codes`` 里的代码返回数据,其余抛错(模拟 3/430 成功的真实故障)。

    ``ok_codes=None`` 表示全部成功。周期探测在第一个板块上做,故第一个板块必须成功,
    否则脚本会在 resolve_period 阶段快速退出(那是另一条已有测试覆盖的路径)。
    """

    def __init__(self, ok_codes=None, rows=None):
        self.ok = ok_codes
        self.rows = rows or [("2026-07-29", 100.0), ("2026-07-30", 101.0)]
        self.refresh_calls: list[tuple] = []
        self.sleeps: list[float] = []

    def _allowed(self, code: str) -> bool:
        return self.ok is None or code in self.ok

    def refresh_kline(self, codes, period=""):
        self.refresh_calls.append((tuple(codes), period))
        if not all(self._allowed(c) for c in codes):
            raise RuntimeError(f"refresh failed: {codes}")

    def get_market_data(self, field_list=None, stock_list=None, period="", start_time="", count=0):
        import pandas as pd

        code = (stock_list or ["880001.SH"])[0]
        if not self._allowed(code):
            raise RuntimeError(f"no data: {code}")
        return {code: pd.DataFrame({"Close": [c for _, c in self.rows]},
                                   index=pd.to_datetime([d for d, _ in self.rows]))}
