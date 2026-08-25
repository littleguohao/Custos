# -*- coding: utf-8 -*-
"""score_return_study 钉测：分档统计 / top-50% 切分 / 无未来函数（as-of 截断）。"""

from __future__ import annotations

import pandas as pd
import pytest

from custos.pipeline.screening import enrich_candidates as ec
from custos.pipeline.screening import score_candidates as sc
from custos.research import score_return_study as srs


def _mk_df(n: int = 400, start: str = "2024-01-01") -> pd.DataFrame:
    """合成日线：n 根连续工作日，close 线性上行（指标只需形状，不看数值真实性）。"""
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    close = [10.0 + i * 0.01 for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "volume": [1000.0] * n,
            "amount": [10000.0] * n,
        }
    )


class TestLongIntervals:
    def test_runs_and_gaps(self):
        regime = {
            "2026-01-05": "做多",
            "2026-01-06": "做多",
            "2026-01-07": "空头",
            "2026-01-08": "做多",
            "2026-01-09": "中性",
            "2026-01-12": "做多",
        }
        assert srs.long_intervals(regime) == [
            ("2026-01-05", "2026-01-06"),
            ("2026-01-08", "2026-01-08"),
            ("2026-01-12", "2026-01-12"),
        ]

    def test_empty_and_tail_run(self):
        assert srs.long_intervals({}) == []
        assert srs.long_intervals({"2026-01-05": "做多"}) == [
            ("2026-01-05", "2026-01-05")
        ]


class TestIntervalOf:
    def test_membership(self):
        ivs = [("2026-01-05", "2026-01-06"), ("2026-01-12", "2026-01-20")]
        assert srs.interval_of("2026-01-05", ivs) == 0
        assert srs.interval_of("2026-01-15", ivs) == 1
        assert srs.interval_of("2026-01-08", ivs) is None  # 区间之间的空头日
        assert srs.interval_of("2025-12-31", ivs) is None


class TestSplitTopHalf:
    def test_even_split(self):
        trades = [{"ret": r} for r in (1, 4, 2, 3)]
        top, bottom = srs.split_top_half(trades)
        assert [t["ret"] for t in top] == [4, 3]
        assert [t["ret"] for t in bottom] == [2, 1]

    def test_odd_split_top_gets_extra(self):
        trades = [{"ret": r} for r in (5, 1, 4, 2, 3)]
        top, bottom = srs.split_top_half(trades)
        assert [t["ret"] for t in top] == [5, 4, 3]  # (5+1)//2 = 3
        assert [t["ret"] for t in bottom] == [2, 1]

    def test_empty(self):
        assert srs.split_top_half([]) == ([], [])


class TestBandStats:
    def test_bands_and_stats(self):
        trades = [
            {"tech_score": 10, "ret": -0.05},  # <30 弱
            {"tech_score": 20, "ret": 0.10},  # <30 弱
            {"tech_score": 30, "ret": 0.06},  # 30-59 中（含边界 30）
            {"tech_score": 59, "ret": 0.02},  # 30-59 中
            {"tech_score": 60, "ret": 0.20},  # >=60 强（含边界 60）
            {"tech_score": 80, "ret": 0.10},  # >=60 强
        ]
        st = srs.band_stats(trades)
        assert st["<30"]["n"] == 2
        assert st["30-59"]["n"] == 2
        assert st[">=60"]["n"] == 2
        # 弱档：一胜一负，均收 (-0.05+0.10)/2 = 0.025
        assert st["<30"]["avg_ret"] == pytest.approx(0.025)
        assert st["<30"]["win_rate"] == pytest.approx(0.5)
        assert st["<30"]["payoff_ratio"] == pytest.approx(2.0)  # 0.10/0.05
        # 强档：全胜，无亏损 ⇒ 盈亏比 None
        assert st[">=60"]["payoff_ratio"] is None
        assert st[">=60"]["win_rate"] == pytest.approx(1.0)
        assert st[">=60"]["avg_ret"] == pytest.approx(0.15)

    def test_band_of_boundaries(self):
        assert srs.band_of(29.9) == "<30"
        assert srs.band_of(30) == "30-59"
        assert srs.band_of(59.9) == "30-59"
        assert srs.band_of(60) == ">=60"


class TestCorrelations:
    def test_monotonic_positive(self):
        trades = [
            {"tech_score": s, "ret": r}
            for s, r in ((10, 0.01), (20, 0.02), (30, 0.03), (40, 0.04))
        ]
        c = srs.correlations(trades)
        assert c["spearman"] == pytest.approx(1.0)
        assert c["pearson"] == pytest.approx(1.0)

    def test_too_few(self):
        assert srs.correlations([{"tech_score": 1, "ret": 0.1}])["spearman"] is None


class TestRetStats:
    def test_basic(self):
        trades = [{"ret": 0.10}, {"ret": -0.05}, {"ret": 0.20}, {"ret": -0.10}]
        st = srs.ret_stats(trades)
        assert st["n"] == 4
        assert st["avg_ret"] == pytest.approx(0.0375)
        assert st["win_rate"] == pytest.approx(0.5)
        # 均盈 0.15 / 均亏 0.075 = 2.0
        assert st["payoff_ratio"] == pytest.approx(2.0)

    def test_all_wins_payoff_none(self):
        assert srs.ret_stats([{"ret": 0.1}, {"ret": 0.2}])["payoff_ratio"] is None

    def test_empty(self):
        assert srs.ret_stats([]) == {"n": 0}


class TestExitReasonDist:
    def test_counts_frac_avg(self):
        trades = [
            {"reason": "bbi_exit", "ret": 0.10},
            {"reason": "bbi_exit", "ret": 0.20},
            {"reason": "stop", "ret": -0.05},
            {"reason": "cost_zone_stop", "ret": -0.01},
        ]
        d = srs.exit_reason_dist(trades)
        assert d["bbi_exit"]["n"] == 2
        assert d["bbi_exit"]["frac"] == pytest.approx(0.5)
        assert d["bbi_exit"]["avg_ret"] == pytest.approx(0.15)
        assert d["stop"]["n"] == 1
        assert d["stop"]["frac"] == pytest.approx(0.25)
        assert d["cost_zone_stop"]["avg_ret"] == pytest.approx(-0.01)
        # 排序输出（bbi < cost_zone < stop），对照表可读性依赖稳定顺序
        assert list(d) == ["bbi_exit", "cost_zone_stop", "stop"]


class TestCliStopParams:
    """止损/成本区参数进 CLI：默认 5%（R10 下沿最优档），cost_zone 默认关。"""

    def test_defaults(self):
        args = srs._build_parser().parse_args([])
        assert args.stop_pct == pytest.approx(5.0)
        assert args.cost_zone_bars == 0
        assert args.cost_zone_pct == pytest.approx(3.0)

    def test_explicit(self):
        args = srs._build_parser().parse_args(
            ["--stop-pct", "50", "--cost-zone-bars", "3"]
        )
        assert args.stop_pct == pytest.approx(50.0)
        assert args.cost_zone_bars == 3


class TestAsofNoLookahead:
    """as-of 截断：compute_metrics 收到的 df 必须只含 ≤ 信号日的数据。"""

    def test_truncation(self, monkeypatch):
        n = 1500  # 超过 df_long 的 1200，验证两层 tail 都生效
        df = _mk_df(n)
        index_df = _mk_df(n)
        i = n - 200  # 信号日不在末端：其后还有 199 根未来K线
        entry_date = df["date"].iloc[i]

        captured = {}

        def fake_compute_metrics(df_arg, index_arg, code="", df_long=None):
            captured["df"] = df_arg
            captured["index"] = index_arg
            captured["df_long"] = df_long
            return {}

        monkeypatch.setattr(ec, "compute_metrics", fake_compute_metrics)
        monkeypatch.setattr(sc, "technical_score", lambda cand, w=None: (42, "中", {}))

        score, level, _ = srs.asof_technical_score(df, index_df, i, "000001")
        assert score == 42 and level == "中"

        got = captured["df"]
        # ① 无未来函数：最后一根 == 信号日，绝无其后的数据
        assert got["date"].iloc[-1] == entry_date
        assert (got["date"] <= entry_date).all()
        # ② 截断长度 = live 口径（260 / 1200）
        assert len(got) == ec.OHLCV_LOAD_BARS
        assert len(captured["df_long"]) == ec.OHLCV_LOAD_BARS_LONG
        assert (captured["df_long"]["date"] <= entry_date).all()
        assert (captured["index"]["date"] <= entry_date).all()

    def test_short_history_uses_all(self, monkeypatch):
        n = 100  # 不足 260：整段前缀（不补不造）
        df = _mk_df(n)
        index_df = _mk_df(n)
        captured = {}
        monkeypatch.setattr(
            ec,
            "compute_metrics",
            lambda d, ix, code="", df_long=None: (
                captured.update(df=d, df_long=df_long) or {}
            ),
        )
        monkeypatch.setattr(sc, "technical_score", lambda cand, w=None: (0, "弱", {}))
        srs.asof_technical_score(df, index_df, n - 1, "000001")
        assert len(captured["df"]) == n
        assert len(captured["df_long"]) == n


class TestSplitTopFrac:
    def test_half_equivalence(self):
        """frac=0.5 与 split_top_half 逐位一致（旧行为不变）。"""
        for n in (0, 1, 2, 5, 10, 11):
            trades = [{"ret": float(r)} for r in range(n)]
            a = srs.split_top_frac(trades, 0.5)
            b = srs.split_top_half(trades)
            assert [t["ret"] for t in a[0]] == [t["ret"] for t in b[0]]
            assert [t["ret"] for t in a[1]] == [t["ret"] for t in b[1]]

    def test_top20(self):
        trades = [{"ret": float(r)} for r in range(20)]  # 0..19
        top, bottom = srs.split_top_frac(trades, 0.20)
        assert [t["ret"] for t in top] == [19.0, 18.0, 17.0, 16.0]  # ceil(20×0.2)=4
        assert len(bottom) == 16

    def test_min_one_and_empty(self):
        top, bottom = srs.split_top_frac([{"ret": 0.1}], 0.20)
        assert len(top) == 1 and bottom == []
        assert srs.split_top_frac([], 0.20) == ([], [])


class TestCliV0118Params:
    """v0.118 新增 CLI：--breakeven / --scale-out / --top-frac（默认全关/0.5=旧行为）。"""

    def test_defaults_unchanged(self):
        args = srs._build_parser().parse_args([])
        assert args.breakeven == pytest.approx(0.0)
        assert args.scale_out == pytest.approx(0.0)
        assert args.top_frac == pytest.approx(0.5)

    def test_explicit(self):
        args = srs._build_parser().parse_args(
            [
                "--stop-pct",
                "12",
                "--breakeven",
                "0.05",
                "--scale-out",
                "0.5",
                "--top-frac",
                "0.20",
            ]
        )
        assert args.stop_pct == pytest.approx(12.0)
        assert args.breakeven == pytest.approx(0.05)
        assert args.scale_out == pytest.approx(0.5)
        assert args.top_frac == pytest.approx(0.20)


class TestRunStudyPassthrough:
    """run_study 把 breakeven/scale_out 透传进 evaluate_trades（形参名钉死）。"""

    def test_exit_params_forwarded(self, monkeypatch):
        captured = {}

        def fake_evaluate(bars_by_code, **kw):
            captured.update(kw)
            return [
                {
                    "code": "000001",
                    "entry_date": "2024-01-03",
                    "exit_date": "2024-01-10",
                    "ret": 0.01,
                    "reason": "bbi_exit",
                    "holding": 5,
                }
            ]

        monkeypatch.setattr(srs.bf, "evaluate_trades", fake_evaluate)
        from custos.datasource.local_tdx import local_tdx_data

        df = _mk_df(60)
        monkeypatch.setattr(local_tdx_data, "get_ohlcv_table", lambda c, count: df)
        monkeypatch.setattr(
            srs, "asof_technical_score", lambda d, ix, i, code: (50, "中", {})
        )
        trades = srs.run_study(
            ["000001"],
            {"2024-01-03": "做多"},
            _mk_df(60),
            stop_pct=12,
            breakeven_trigger=0.05,
            scale_out_frac=0.5,
        )
        assert captured["stop_pct"] == 12
        assert captured["breakeven_trigger"] == 0.05
        assert captured["scale_out_frac"] == 0.5
        assert captured["bbi_exit_consec"] == 2
        assert trades and trades[0]["tech_score"] == 50
