# -*- coding: utf-8 -*-
"""M2 扫描报表的判定逻辑测试。

这套测试存在的理由是第一轮判读犯过的两个口径错误：

  ① **拿累计 R 跨 stop_mode 比较**。R = ret/risk_frac，基准 stop_mode=low 的
     risk_frac 中位仅 0.65%，换 --stop-pct 12 后固定 12%，分母差 18 倍 ⇒ R 崩。
     于是「胜率 18%→51.2%、期望 +0.43%→+1.42%」这组明显更好的结果被判成否决。
  ② **用大赢家绝对笔数比较**。择时类方案（--amv-long-only）会过滤掉部分信号，
     样本量下降时绝对数必然下降 ⇒ 把它们全部误杀。
"""
from __future__ import annotations

import json

import pytest

from screening import m2_stop_sweep as m2


def _write(tmp, group, name, **kw):
    n = kw.pop("n", 1000)
    big = kw.pop("big", 20)
    pf = kw.pop("pf", None)
    o = {"n": n, "win_rate": kw.get("wr", 0.18), "expectancy": kw.get("exp", 0.004),
         "expectancy_R": kw.get("expR", 0.35), "total_R": kw.get("totR", 300.0),
         "payoff_ratio": kw.get("payoff", 5.6), "avg_win": kw.get("aw", 0.2),
         "avg_loss": 0.035, "avg_holding": 4.2, "median_return": -0.01,
         "exit_reasons": {}, "trades": [{"ret": 0.3}] * big + [{"ret": 0.01}] * (n - big)}
    if pf:
        o["portfolio"] = pf
    (tmp / f"{group}__{name}.json").write_text(json.dumps(o), encoding="utf-8")


class TestGroupIsolation:
    """R 只在组内可比——不同 stop_mode 必须分属不同组。"""

    def test_stop_modes_are_separate_groups(self):
        assert "A_stop_low" in m2.GROUPS and "B_stop_pct" in m2.GROUPS
        assert m2.GROUPS["A_stop_low"]["common"] == []
        assert "--stop-mode" in m2.GROUPS["B_stop_pct"]["common"]
        assert "pct" in m2.GROUPS["B_stop_pct"]["common"]

    def test_each_trade_group_has_own_baseline(self):
        for g in ("A_stop_low", "B_stop_pct"):
            base = m2.GROUPS[g]["baseline"]
            assert base in m2.GROUPS[g]["runs"], f"{g} 的基准必须是本组内的方案"

    def test_portfolio_group_has_no_r_baseline(self):
        """组合级不比 R，不该设 R 基准。"""
        assert m2.GROUPS["C_portfolio"]["baseline"] is None

    def test_collect_separates_groups(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline")
        _write(tmp_path, "B_stop_pct", "pct_12", expR=0.118)
        got = m2._collect(cross=False)
        assert [r["name"] for r in got["A_stop_low"]] == ["00_baseline"]
        assert [r["name"] for r in got["B_stop_pct"]] == ["pct_12"]


class TestBigWinnerRate:
    """大赢家用**占比**，不用绝对数（否则择时类方案被误杀）。"""

    def test_filtered_sample_not_penalized(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        # 基准 32/1345 = 2.38%；择时后 28/900 = 3.11%（占比上升，绝对数下降）
        _write(tmp_path, "A_stop_low", "00_baseline", n=1345, big=32, expR=0.352)
        _write(tmp_path, "A_stop_low", "amv_long_only", n=900, big=28, expR=0.410)
        m2.report(cross=False)
        out = capsys.readouterr().out
        line = next(ln for ln in out.split("\n") if "amv_long_only" in ln and "期望R" in ln)
        assert "✅ 通过" in line, f"占比上升却被否决: {line}"
        assert "2.38%" in line and "3.11%" in line, "应显示占比而非只有绝对数"

    def test_真削大赢家_is_rejected(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", n=1345, big=32, expR=0.352, aw=0.198)
        # trail 砍掉趋势单：占比 2.38%→0.67%，均盈 -38.9%
        _write(tmp_path, "A_stop_low", "trail_08", n=1345, big=9, expR=0.180, aw=0.121)
        m2.report(cross=False)
        out = capsys.readouterr().out
        line = next(ln for ln in out.split("\n") if "trail_08" in ln and "期望R" in ln)
        assert "❌ 否决" in line
        assert "削大赢家" in line or "大赢家占比" in line


class TestCrossGroupUsesReturnsOnly:
    """跨组表**只能出现收益率口径**，R 一律不出现。"""

    def test_no_r_columns_in_cross_table(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", wr=0.18, exp=0.0043,
               payoff=5.661, expR=0.352, totR=332.0)
        _write(tmp_path, "B_stop_pct", "pct_12", wr=0.512, exp=0.0142,
               payoff=1.27, expR=0.118, totR=135.0)
        m2.report(cross=False)
        out = capsys.readouterr().out
        seg = out.split("跨组比较")[1].split("【C_portfolio】")[0]
        assert "累计R" not in seg and "期望R" not in seg
        assert "盈亏比" in seg and "平衡胜率" in seg and "margin" in seg

    def test_higher_expectancy_ranks_first(self, tmp_path, monkeypatch, capsys):
        """跨组按期望收益率排序——这才是可比的量。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", exp=0.0043, payoff=5.661, wr=0.18)
        _write(tmp_path, "B_stop_pct", "pct_12", exp=0.0142, payoff=1.27, wr=0.512)
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("跨组比较")[1]
        i_pct = seg.index("pct_12")
        i_base = seg.index("00_baseline")
        assert i_pct < i_base, "期望 +1.42% 应排在 +0.43% 之前"


class TestBreakevenMargin:
    """margin = 实际胜率 − 盈亏平衡胜率。它比胜率或盈亏比单独看都有意义。"""

    @pytest.mark.parametrize("payoff,expect", [(5.661, 0.1503), (1.27, 0.4405),
                                               (1.0, 0.5)])
    def test_breakeven_formula(self, payoff, expect):
        assert m2._breakeven_wr(payoff) == pytest.approx(expect, abs=1e-3)

    def test_none_on_bad_payoff(self):
        assert m2._breakeven_wr(None) is None
        assert m2._breakeven_wr(0) is None
        assert m2._breakeven_wr(-1) is None

    def test_negative_margin_surfaces_losing_setup(self, tmp_path, monkeypatch, capsys):
        """trail_08 实测盈亏比 4.10、胜率 18% ⇒ 平衡胜率 19.6% ⇒ margin 为负（亏钱）。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "trail_08", wr=0.18, payoff=4.10, exp=0.0018)
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("跨组比较")[1]
        line = next(ln for ln in seg.split("\n") if "trail_08" in ln)
        assert "-1." in line or "-2." in line, f"margin 应为负: {line}"


class TestPortfolioTable:
    def test_shows_exposure_and_fill_rate(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "C_portfolio", "pf_c5_p20",
               pf={"total_return": -0.234, "cagr": -0.155, "max_drawdown": 0.361,
                   "n_taken": 299, "n_skipped": 1046})
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "总敞口" in out, "必须提示决定因素是总敞口而非持仓数"
        assert "22.2%" in out, "应算出执行率"
        assert "top-n" in out or "择优" in out

    def test_ranked_by_total_return(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "C_portfolio", "bad",
               pf={"total_return": -0.30, "cagr": -0.2, "max_drawdown": 0.33,
                   "n_taken": 299, "n_skipped": 1046})
        _write(tmp_path, "C_portfolio", "good",
               pf={"total_return": 0.185, "cagr": 0.12, "max_drawdown": 0.115,
                   "n_taken": 150, "n_skipped": 1195})
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("C_portfolio")[1]
        assert seg.index("good") < seg.index("bad")


class TestNewRunsWired:
    """第一轮遗漏的两个能力必须进扫描矩阵。"""

    def test_amv_long_only_present(self):
        allruns = {n: e for m in m2.GROUPS.values() for n, e in m["runs"].items()}
        assert any("--amv-long-only" in e for e in allruns.values())

    def test_top_n_present_in_portfolio(self):
        runs = m2.GROUPS["C_portfolio"]["runs"]
        assert any("--top-n" in e for e in runs.values())

    def test_low_exposure_variants_present(self):
        """必须有低敞口方案——受控实验显示敞口是决定因素。"""
        runs = m2.GROUPS["C_portfolio"]["runs"]
        exposures = []
        for e in runs.values():
            c = int(e[e.index("--max-concurrent") + 1]) if "--max-concurrent" in e else 5
            p = int(e[e.index("--max-pos") + 1]) if "--max-pos" in e else 20
            exposures.append(c * p / 100)
        assert min(exposures) <= 0.4, f"缺低敞口方案，实际最低 {min(exposures):.0%}"
        assert max(exposures) >= 1.0, "应保留满仓方案作对照"
