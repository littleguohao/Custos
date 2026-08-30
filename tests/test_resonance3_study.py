# -*- coding: utf-8 -*-
"""resonance3_study 钉测：PIT as-of 正确性 / 共振 gate 逐腿 / 两臂对照判定 / 无未来函数。"""

from __future__ import annotations

import pytest

from custos.research import resonance3_study as r3


def _pit(code="000001", rd="2024-03-31", nd="2024-04-25", np_=1e8, ocf=0.5, roe=8.0):
    return {
        "code": code,
        "report_date": rd,
        "notice_date": nd,
        "net_profit": np_,
        "ocf_ps": ocf,
        "roe_waa": roe,
    }


class TestPitTier:
    def test_asof_visibility(self):
        """notice_date < 信号日才可见（公告次日口径）；此前 ⇒ 未知。"""
        m = r3.build_pit_map([_pit(nd="2024-04-25")])
        assert r3.pit_tier_at(m, "000001", "2024-04-25") == "未知"  # 公告当日不可见
        assert r3.pit_tier_at(m, "000001", "2024-04-24") == "未知"
        assert r3.pit_tier_at(m, "000001", "2024-04-26") == "优"  # 次日可见

    def test_latest_visible_wins(self):
        """多期取 report_date 最大且已可见者；新期公告前仍用旧期。"""
        m = r3.build_pit_map(
            [
                _pit(rd="2023-12-31", nd="2024-04-24", np_=-1.0),  # 差
                _pit(rd="2024-03-31", nd="2024-04-25", np_=1e8),  # 优
            ]
        )
        assert (
            r3.pit_tier_at(m, "000001", "2024-04-24") == "未知"
        )  # 旧期公告当日也不可见
        assert r3.pit_tier_at(m, "000001", "2024-04-25") == "差"  # 新期尚未可见
        assert r3.pit_tier_at(m, "000001", "2024-05-06") == "优"  # 新期可见

    def test_no_record_unknown(self):
        assert r3.pit_tier_at(r3.build_pit_map([]), "600000", "2024-01-01") == "未知"

    def test_quality_mapping(self):
        """PIT 记录 → 品质档：优=真业绩+ROE；中=净利正（现金流缺/负）；差=净利非正。"""
        assert r3.pit_record_to_financials(None)["available"] is False
        fin = r3.pit_record_to_financials(_pit(np_=1.0, ocf=0.5, roe=8.0))
        from custos.core.factors.fundamentals import fundamental_quality

        assert fundamental_quality(fin)["tier"] == "优"
        # 现金流缺失 ⇒ real_earnings_cashflow 不成立（不冒充）⇒ 中
        fin2 = r3.pit_record_to_financials(_pit(np_=1.0, ocf=None, roe=8.0))
        assert fin2["dixi_proxy"]["op_cashflow_positive"] is None
        assert fin2["dixi_proxy"]["real_earnings_cashflow"] is False
        assert fundamental_quality(fin2)["tier"] == "中"
        # 净利非正 ⇒ 差
        fin3 = r3.pit_record_to_financials(_pit(np_=-1.0, ocf=0.5, roe=8.0))
        assert fundamental_quality(fin3)["tier"] == "差"


class TestResonanceGate:
    """gate 逐腿判定（as-of 技术分用 monkeypatch 隔离，重计算路径单独钉无未来函数）。"""

    def _mk(self, monkeypatch, tier="优", score=70):
        import pandas as pd

        monkeypatch.setattr(
            r3.srs, "asof_technical_score", lambda *a, **kw: (score, "强", {})
        )
        pit_map = r3.build_pit_map([_pit(nd="2024-04-25")])
        df = pd.DataFrame(
            {
                "date": pd.bdate_range("2024-03-01", periods=60).strftime("%Y-%m-%d"),
                "open": [10.0] * 60,
                "high": [10.0] * 60,
                "low": [10.0] * 60,
                "close": [10.0] * 60,
                "volume": [1000.0] * 60,
            }
        )
        long_dates = frozenset({df["date"].iloc[-1]})  # 末日=做多日（随构造对齐）
        return pit_map, long_dates, df

    def test_all_legs_required(self, monkeypatch):
        pit_map, long_dates, df = self._mk(monkeypatch)
        gate = r3.make_resonance_gate("000001", long_dates, pit_map, df)
        d = df["date"].iloc[-1]
        assert d in long_dates  # 末日=做多日
        monkeypatch.setattr(r3.bf, "j_low_gate", lambda *a, **kw: True)
        assert gate(df) is True
        # ① regime 不满足
        gate2 = r3.make_resonance_gate("000001", frozenset({"2025-01-01"}), pit_map, df)
        assert gate2(df) is False
        # ② j_low 不满足
        monkeypatch.setattr(r3.bf, "j_low_gate", lambda *a, **kw: False)
        assert gate(df) is False
        monkeypatch.setattr(r3.bf, "j_low_gate", lambda *a, **kw: True)
        # ③ 基本面非优（信号日 2024-04-25 公告当日不可见 ⇒ 未知）
        df2 = df.copy()
        df2.loc[df2.index[-1], "date"] = "2024-04-25"
        # 把末日换成做多集合里的 04-25
        gate3 = r3.make_resonance_gate(
            "000001", frozenset({"2024-04-25"}), pit_map, df2
        )
        assert gate3(df2) is False

    def test_tech_score_threshold(self, monkeypatch):
        pit_map, long_dates, df = self._mk(monkeypatch, score=59)
        monkeypatch.setattr(r3.bf, "j_low_gate", lambda *a, **kw: True)
        gate = r3.make_resonance_gate("000001", long_dates, pit_map, df)
        assert gate(df) is False  # 59 < 60
        pit_map, long_dates, df = self._mk(monkeypatch, score=60)
        monkeypatch.setattr(r3.bf, "j_low_gate", lambda *a, **kw: True)
        gate = r3.make_resonance_gate("000001", long_dates, pit_map, df)
        assert gate(df) is True  # 60 ≥ 60（含边界）

    def test_gate_calls_asof_with_prefix_only(self, monkeypatch):
        """无未来函数：gate 收到的 df_slice 末端 = 信号日；as-of 调用 i=len-1。"""
        pit_map, long_dates, df = self._mk(monkeypatch)
        monkeypatch.setattr(r3.bf, "j_low_gate", lambda *a, **kw: True)
        seen = {}

        def spy(df_full, index_full, i, code):
            seen["i"] = i
            seen["n"] = len(df_full)
            seen["last_date"] = str(df_full["date"].iloc[i])[:10]
            return (70, "强", {})

        monkeypatch.setattr(r3.srs, "asof_technical_score", spy)
        gate = r3.make_resonance_gate("000001", long_dates, pit_map, df)
        assert gate(df) is True
        assert seen["i"] == len(df) - 1  # 只用 ≤ 信号日的前缀
        assert seen["last_date"] == df["date"].iloc[-1]


class TestCompareArms:
    def _arm(self, wr_n, payoff, margins):
        n, n_win = wr_n
        st = {
            "n": n,
            "win_rate": n_win / n,
            "payoff_ratio": payoff,
            "avg_ret": 0.01,
            "n_win": n_win,
            "expectancy_R": 0.5,
            "margin": n_win / n - 1 / (1 + payoff),
            "wr_wilson95": list(r3.svs.wilson_wr_interval(n_win, n)),
            "half_window": {
                "split_date": "2024-01-01",
                "first": {"n": n // 2, "margin": margins[0]},
                "second": {"n": n // 2, "margin": margins[1]},
            },
            "exit_reasons": {},
        }
        return st

    def test_adds_value_logic(self):
        a = self._arm((1000, 300), 2.0, (0.05, 0.05))  # margin 0.3−1/3=−0.033
        b = self._arm((1000, 500), 2.0, (0.15, 0.15))  # margin 0.167，Wilson 不重叠
        c = r3.compare_arms(a, b)
        assert c["delta_margin"] == pytest.approx(0.2, abs=1e-3)  # 0.167−(−0.033)
        assert c["wr_wilson_overlap"] is False
        assert c["adds_value"] is True
        # 半窗翻转 ⇒ 不加值
        b2 = self._arm((1000, 500), 2.0, (0.15, 0.01))
        assert r3.compare_arms(a, b2)["adds_value"] is False
        # Wilson 重叠 ⇒ 不加值（小样本）
        a3 = self._arm((20, 6), 2.0, (0.05, 0.05))
        b3 = self._arm((20, 10), 2.0, (0.15, 0.15))
        c3 = r3.compare_arms(a3, b3)
        assert c3["wr_wilson_overlap"] is True
        assert c3["adds_value"] is False
        # margin 不升 ⇒ 不加值
        b4 = self._arm((1000, 290), 2.0, (0.01, 0.01))
        assert r3.compare_arms(a, b4)["adds_value"] is False
