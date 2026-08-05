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


def _write(tmp, group, name, *, fp="s1000", **kw):
    """写一个结果文件。**文件名必须含样本量指纹**——见 m2._fingerprint 的注释：
    第一版没有指纹，owner 先跑 300 再跑 1000 时旧文件被 SKIP 复用，
    汇总表把 ~400 笔与 ~1300 笔混在一起比。"""
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
    (tmp / f"{group}__{name}__{fp}.json").write_text(json.dumps(o), encoding="utf-8")


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
        """「削大赢家」判据只对**入场类**生效（笔数显著变化）。

        出场类改用累计R 为主判据——见 TestExitVsEntrySideVerdict 的说明。
        所以这里构造入场类场景（笔数腰斩）才能验证这条判据。
        """
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", n=1345, big=32, expR=0.352, aw=0.198)
        # 入场过滤把样本砍半，且大赢家占比从 2.38% 崩到 0.67%
        _write(tmp_path, "A_stop_low", "some_filter", n=600, big=4, expR=0.50, aw=0.121)
        m2.report(cross=False)
        out = capsys.readouterr().out
        line = next(ln for ln in out.split("\n") if "some_filter" in ln and "期望R" in ln)
        assert "❌ 否决" in line and "[入场]" in line
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


class TestLoadKeyName:
    """键名必须对上 backtest_factors 的实际输出 `trade_summary`。

    我第一版写成 trade_sim/summary/trade_simulation 全都对不上，owner 跑完 25 个方案后
    报表生成不出来、只能手工汇总。这类"读不到就静默返回空"的失效最难发现——
    脚本不报错，只是表格是空的。
    """

    def test_reads_trade_summary_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "A_stop_low__x__s1000.json").write_text(json.dumps({
            "trade_summary": {"n": 410, "win_rate": 0.363, "expectancy": 0.006,
                              "expectancy_R": 0.769, "total_R": 304.0,
                              "payoff_ratio": 3.2, "avg_win": 0.1212,
                              "avg_loss": 0.04, "avg_holding": 5.0,
                              "exit_reasons": {}},
            "trades": [{"ret": 0.3}] * 20}), encoding="utf-8")
        got = m2._collect(cross=False)["A_stop_low"]
        assert len(got) == 1
        assert got[0]["expR"] == 0.769 and got[0]["totR"] == 304.0
        assert got[0]["big"] == 20

    def test_portfolio_at_top_level(self, tmp_path, monkeypatch):
        """组合级结果的 portfolio 在顶层，与 trade_summary 平级。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "C_portfolio__p__s1000.json").write_text(json.dumps({
            "trade_summary": {"n": 342, "expectancy": 0.01, "expectancy_R": 0.2},
            "portfolio": {"total_return": 0.171, "cagr": 0.108,
                          "max_drawdown": 0.029, "n_taken": 150,
                          "n_skipped": 1195}}), encoding="utf-8")
        got = m2._collect(cross=False)["C_portfolio"]
        assert got and got[0]["pf"]["total_return"] == 0.171

    def test_unknown_key_falls_back(self, tmp_path, monkeypatch, capsys):
        """键名再改也要能活——扫一层子字典兜底。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "A_stop_low__y__s1000.json").write_text(json.dumps({
            "some_new_name": {"n": 100, "expectancy": 0.005, "expectancy_R": 0.5,
                              "total_R": 50.0, "avg_win": 0.1}}), encoding="utf-8")
        got = m2._collect(cross=False)["A_stop_low"]
        assert got and got[0]["expR"] == 0.5
        assert "兜底" in capsys.readouterr().out

    def test_missing_summary_warns_not_silent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "A_stop_low__z__s1000.json").write_text(
            json.dumps({"codes": ["600000"], "count": 500}), encoding="utf-8")
        m2._collect(cross=False)
        assert "找不到交易摘要" in capsys.readouterr().out


class TestSampleFingerprint:
    """结果文件名必须含样本量指纹——**这是实盘踩过的坑**。

    owner 先跑 `--sample 300`、再跑 `--sample 1000`，而第一版文件名只有
    `{组}__{方案}.json`，于是 300 的旧结果被 `[SKIP]` 直接复用。汇总表里
    ~400 笔的方案与 ~1300 笔的基准混在一起比，A 组一半方案的判定全部无效
    （cost_zone_3 / tick_buffer_3 / trail_18 / trigger_intraday / amv_long_only）。
    """

    def test_fingerprint_includes_sample(self):
        assert m2._fingerprint(1000, False) == "s1000"
        assert m2._fingerprint(300, True) == "s300_cw"
        assert m2._fingerprint(300, False) != m2._fingerprint(1000, False)

    def test_only_largest_sample_collected(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000", n=1294, expR=0.202)
        _write(tmp_path, "A_stop_low", "trail_18", fp="s300", n=409, expR=0.686)
        got = m2._collect(cross=False)["A_stop_low"]
        assert [r["name"] for r in got] == ["00_baseline"], "300 样本的残留必须被排除"
        assert "检测到多个样本量" in capsys.readouterr().out

    def test_explicit_sample_selects_batch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000", n=1294)
        _write(tmp_path, "A_stop_low", "trail_18", fp="s300", n=409)
        got = m2._collect(cross=False, sample=300)["A_stop_low"]
        assert [r["name"] for r in got] == ["trail_18"]

    def test_cross_window_separate_from_normal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000", n=1294)
        _write(tmp_path, "A_stop_low", "00_baseline_cw", fp="s1000_cw", n=900)
        assert len(m2._collect(cross=False)["A_stop_low"]) == 1
        assert len(m2._collect(cross=True)["A_stop_low"]) == 1

    def test_legacy_files_warn(self, tmp_path, monkeypatch, capsys):
        """无指纹的旧文件要明确告警，不能静默当成有效数据。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "A_stop_low__old.json").write_text(
            json.dumps({"trade_summary": {"n": 400, "expectancy": 0.01}}),
            encoding="utf-8")
        m2._collect(cross=False)
        out = capsys.readouterr().out
        assert "样本量指纹" in out and "旧结果文件" in out


class TestMixedSampleGuard:
    """笔数一致性检查——指纹之外的最后一道防线。"""

    def test_warns_on_diverging_counts(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", n=1294, expR=0.202)
        _write(tmp_path, "A_stop_low", "trail_18", n=409, expR=0.686)
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "笔数不一致" in out and "trail_18(409)" in out

    def test_amv_excluded_from_check(self, tmp_path, monkeypatch, capsys):
        """择时方案本来就会大幅减少笔数（0AMV 做多期仅占约 18% 时间），不该告警。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", n=1294, expR=0.202)
        _write(tmp_path, "A_stop_low", "amv_long_only", n=230, expR=0.9)
        m2.report(cross=False)
        assert "笔数不一致" not in capsys.readouterr().out

    def test_no_warning_when_consistent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", n=1294, expR=0.202)
        _write(tmp_path, "A_stop_low", "be_08", n=1296, expR=0.223)
        m2.report(cross=False)
        assert "笔数不一致" not in capsys.readouterr().out


def _write_tr(tmp, group, name, *, n, expR, totR, aw, big, tail=0.7, fp="s1000"):
    """带逐笔 r_multiple 的结果文件（尾部 R 占比可控）。"""
    tail_r = totR * tail
    trades = [{"ret": 0.35, "r_multiple": tail_r / big} for _ in range(big)]
    trades += [{"ret": 0.01, "r_multiple": (totR - tail_r) / (n - big)}
               for _ in range(n - big)]
    (tmp / f"{group}__{name}__{fp}.json").write_text(json.dumps({
        "trade_summary": {"n": n, "win_rate": 0.3, "expectancy": 0.004,
                          "expectancy_R": expR, "total_R": totR, "payoff_ratio": 2.7,
                          "avg_win": aw, "avg_loss": 0.04, "avg_holding": 5.0,
                          "exit_reasons": {}},
        "trades": trades}), encoding="utf-8")


class TestExitVsEntrySideVerdict:
    """出场类与入场类分开判（2026-08-05 调整）。

    「削大赢家」这条判据的初衷是防止**为提高胜率而筛掉大赢家**——那些收益会
    **永久消失**。但出场机制不筛信号（`trail_08` 笔数 1294→1298），它只改离场时点，
    用「少赚一点尾部」换「多一些赢家」。对它硬套「大赢家占比不降」，会把
    累计R +43.1% 的方案否掉——实测就发生了。
    """

    def test_classifies_by_trade_count(self):
        base = {"n": 1294}
        assert m2._is_exit_side({"n": 1298}, base) is True     # 笔数几乎不变 ⇒ 出场
        assert m2._is_exit_side({"n": 230}, base) is False     # 择时大幅减少 ⇒ 入场
        assert m2._is_exit_side({"n": 409}, base) is False

    def test_exit_side_passes_on_total_r(self, tmp_path, monkeypatch, capsys):
        """出场类：累计R 提升即通过，即使均盈/大赢家占比略降。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(tmp_path, "A_stop_low", "00_baseline", n=1294, expR=0.202,
                  totR=250.5, aw=0.1098, big=61)
        _write_tr(tmp_path, "A_stop_low", "trail_08", n=1298, expR=0.288,
                  totR=358.4, aw=0.1015, big=52)
        m2.report(cross=False)
        line = next(ln for ln in capsys.readouterr().out.split("\n")
                    if "trail_08" in ln and "累计R" in ln)
        assert "✅ 通过" in line and "[出场]" in line
        assert "+43" in line

    def test_exit_side_rejected_when_total_r_flat(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(tmp_path, "A_stop_low", "00_baseline", n=1294, expR=0.202,
                  totR=250.5, aw=0.1098, big=61)
        _write_tr(tmp_path, "A_stop_low", "cost_zone_3", n=1290, expR=0.19,
                  totR=245.0, aw=0.0976, big=45)
        m2.report(cross=False)
        line = next(ln for ln in capsys.readouterr().out.split("\n")
                    if "cost_zone_3" in ln and "累计R" in ln)
        assert "❌ 否决" in line

    def test_entry_side_keeps_strict_rule(self, tmp_path, monkeypatch, capsys):
        """入场类仍严格：筛掉大赢家的收益永久消失，不能只看总量。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(tmp_path, "A_stop_low", "00_baseline", n=1294, expR=0.202,
                  totR=250.5, aw=0.1098, big=61)
        # 笔数腰斩且大赢家占比下降 ⇒ 入场类应否决
        _write_tr(tmp_path, "A_stop_low", "some_filter", n=600, expR=0.30,
                  totR=180.0, aw=0.09, big=15)
        m2.report(cross=False)
        line = next(ln for ln in capsys.readouterr().out.split("\n")
                    if "some_filter" in ln and "期望R" in ln)
        assert "❌ 否决" in line and "[入场]" in line

    def test_tail_share_warning(self, tmp_path, monkeypatch, capsys):
        """尾部R占比崩塌要警示——收益转为依赖中等赢家，而中等赢家更易被成本吃掉。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(tmp_path, "A_stop_low", "00_baseline", n=1294, expR=0.202,
                  totR=250.5, aw=0.1098, big=61, tail=0.70)
        _write_tr(tmp_path, "A_stop_low", "trail_08", n=1298, expR=0.288,
                  totR=358.4, aw=0.1015, big=52, tail=0.35)
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "尾部R占比 70%→35%" in out
        assert "收益更依赖中等赢家" in out

    def test_tail_share_no_warning_when_stable(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(tmp_path, "A_stop_low", "00_baseline", n=1294, expR=0.202,
                  totR=250.5, aw=0.1098, big=61, tail=0.70)
        _write_tr(tmp_path, "A_stop_low", "trail_12", n=1295, expR=0.236,
                  totR=293.9, aw=0.1070, big=60, tail=0.68)
        m2.report(cross=False)
        # 只看警示行（缩进 + ⚠️），表头的规则说明里本来就含"尾部R占比"字样
        warn = [ln for ln in capsys.readouterr().out.split("\n")
                if ln.startswith("      ⚠️") and "尾部R占比" in ln]
        assert not warn, f"占比稳定不该警示: {warn}"


class TestTailRShare:
    def test_computes_share_of_total_r(self):
        trades = [{"ret": 0.3, "r_multiple": 7.0}, {"ret": 0.3, "r_multiple": 7.0},
                  {"ret": 0.01, "r_multiple": 3.0}, {"ret": -0.02, "r_multiple": -1.0}]
        # 尾部 14 / 总 16
        assert m2._tail_r_share(trades) == pytest.approx(14 / 16)

    def test_none_without_r_multiple(self):
        """--summary-only 时逐笔没有 r_multiple，要返回 None 而不是 0。"""
        assert m2._tail_r_share([{"ret": 0.3}]) is None
        assert m2._tail_r_share([]) is None

    def test_none_on_zero_total(self):
        assert m2._tail_r_share([{"ret": 0.3, "r_multiple": 5.0},
                                 {"ret": -0.1, "r_multiple": -5.0}]) is None
