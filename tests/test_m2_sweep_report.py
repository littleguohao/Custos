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
import pathlib
import sys

import pytest

from custos.research import m2_stop_sweep as m2


def _write(tmp, group, name, *, fp="s1000", **kw):
    """写一个结果文件。**文件名必须含样本量指纹**——见 m2._fingerprint 的注释：
    第一版没有指纹，owner 先跑 300 再跑 1000 时旧文件被 SKIP 复用，
    汇总表把 ~400 笔与 ~1300 笔混在一起比。"""
    n = kw.pop("n", 1000)
    big = kw.pop("big", 20)
    pf = kw.pop("pf", None)
    o = {
        "n": n,
        "win_rate": kw.get("wr", 0.18),
        "expectancy": kw.get("exp", 0.004),
        "expectancy_R": kw.get("expR", 0.35),
        "total_R": kw.get("totR", 300.0),
        "payoff_ratio": kw.get("payoff", 5.6),
        "avg_win": kw.get("aw", 0.2),
        "avg_loss": 0.035,
        "avg_holding": 4.2,
        "median_return": -0.01,
        "exit_reasons": {},
        "trades": [{"ret": 0.3}] * big + [{"ret": 0.01}] * (n - big),
    }
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
        line = next(
            ln for ln in out.split("\n") if "amv_long_only" in ln and "期望R" in ln
        )
        assert "✅ 通过" in line, f"占比上升却被否决: {line}"
        assert "2.38%" in line and "3.11%" in line, "应显示占比而非只有绝对数"

    def test_真削大赢家_is_rejected(self, tmp_path, monkeypatch, capsys):
        """「削大赢家」判据只对**入场类**生效（笔数显著变化）。

        出场类改用累计R 为主判据——见 TestExitVsEntrySideVerdict 的说明。
        所以这里构造入场类场景（笔数腰斩）才能验证这条判据。
        """
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(
            tmp_path, "A_stop_low", "00_baseline", n=1345, big=32, expR=0.352, aw=0.198
        )
        # 入场过滤把样本砍半，且大赢家占比从 2.38% 崩到 0.67%
        _write(tmp_path, "A_stop_low", "some_filter", n=600, big=4, expR=0.50, aw=0.121)
        m2.report(cross=False)
        out = capsys.readouterr().out
        line = next(
            ln for ln in out.split("\n") if "some_filter" in ln and "期望R" in ln
        )
        assert "❌ 否决" in line and "[入场]" in line
        assert "削大赢家" in line or "大赢家占比" in line


class TestCrossGroupUsesReturnsOnly:
    """跨组表**只能出现收益率口径**，R 一律不出现。"""

    def test_no_r_columns_in_cross_table(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            wr=0.18,
            exp=0.0043,
            payoff=5.661,
            expR=0.352,
            totR=332.0,
        )
        _write(
            tmp_path,
            "B_stop_pct",
            "pct_12",
            wr=0.512,
            exp=0.0142,
            payoff=1.27,
            expR=0.118,
            totR=135.0,
        )
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

    @pytest.mark.parametrize(
        "payoff,expect", [(5.661, 0.1503), (1.27, 0.4405), (1.0, 0.5)]
    )
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
        _write(
            tmp_path,
            "C_portfolio",
            "pf_c5_p20",
            pf={
                "total_return": -0.234,
                "cagr": -0.155,
                "max_drawdown": 0.361,
                "n_taken": 299,
                "n_skipped": 1046,
            },
        )
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "总敞口" in out, "必须提示决定因素是总敞口而非持仓数"
        assert "22.2%" in out, "应算出执行率"
        assert "top-n" in out or "择优" in out

    def test_ranked_by_total_return(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(
            tmp_path,
            "C_portfolio",
            "bad",
            pf={
                "total_return": -0.30,
                "cagr": -0.2,
                "max_drawdown": 0.33,
                "n_taken": 299,
                "n_skipped": 1046,
            },
        )
        _write(
            tmp_path,
            "C_portfolio",
            "good",
            pf={
                "total_return": 0.185,
                "cagr": 0.12,
                "max_drawdown": 0.115,
                "n_taken": 150,
                "n_skipped": 1195,
            },
        )
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("C_portfolio")[1]
        assert seg.index("good") < seg.index("bad")


class TestStopPctLowerBound:
    """实测 5%→8%→12% 的期望% 是 0.67/0.64/0.63——单调但极平，下界没探到。"""

    def test_tighter_stops_added(self):
        runs = m2.GROUPS["B_stop_pct"]["runs"]
        assert "pct_03" in runs and "pct_04" in runs

    def test_stop_pct_ladder_spans_a_range(self):
        """纯档位方案（不含择时/移动止盈变体）必须覆盖到 5% 以下，才能探到拐点。"""
        pcts = sorted(
            float(e[e.index("--stop-pct") + 1])
            for n, e in m2.GROUPS["B_stop_pct"]["runs"].items()
            if "--stop-pct" in e and len(e) == 2
        )  # 只有 --stop-pct 一项
        assert pcts == [3.0, 4.0, 5.0, 8.0, 12.0]
        assert min(pcts) < 5.0, "下界必须比上一轮的 5% 更紧，否则探不到拐点"

    def test_new_tiers_are_exit_side_with_changed_denominator(self):
        """新档位必须归出场类、且被标成 R 口径变——3% 档的期望R 天然是 8% 档的 2.7 倍。"""
        base = m2.GROUPS["B_stop_pct"]["baseline"]
        for n in ("pct_03", "pct_04"):
            assert m2._side_from_flags("B_stop_pct", n, base) == "exit"
            assert m2._same_r_denom("B_stop_pct", n, base) is False

    def test_real_backtest_count(self):
        """39 个方案但只有 32 次真回测——C 组 7 个走 trades 复用。
        （2026-08-12 #20：35/28 → 37/30，A 组加 low_pct_03/atr_02 两格；
        2026-08-13 #25：37/30 → 39/32，B 组加 pct_05_amv_trail_08/pct_05_amv_cz3 两格）"""
        total = sum(len(v["runs"]) for v in m2.GROUPS.values())
        real = sum(
            1
            for g, v in m2.GROUPS.items()
            for n in v["runs"]
            if n not in (v.get("reuse") or {})
        )
        assert total == 39 and real == 32


class TestScaleOutHasControlArm:
    """`--scale-out 0.5` 写在 `_base_args` 里、每个方案都开着 ⇒ 没有对照臂时，
    这轮扫描对「分批止盈有没有用」**零信息**。

    M1（2026-08-04）验过 0.5 vs 0，但那是**旧止损口径**（盘中止损，胜率 18%、
    盈亏比 5.525）；f156a0a 改成收盘止损后胜率升到 29.8%、盈亏比降到 2.678。
    而 ∂E/∂b = p ⇒ p 变大则盈亏比杠杆更值钱，但能触发 `+scaled` 的交易占比也变了
    ⇒ 方向不确定，必须重验。
    """

    def test_base_args_turns_scale_out_on(self):
        a = m2._base_args(1000, False)
        assert "--scale-out" in a and a[a.index("--scale-out") + 1] == "0.5"

    def test_control_arm_exists(self):
        runs = m2.GROUPS["A_stop_low"]["runs"]
        offs = [
            n
            for n, e in runs.items()
            if "--scale-out" in e and float(e[e.index("--scale-out") + 1]) == 0.0
        ]
        assert offs, "缺 scale_out=0 对照臂 ⇒ 分批止盈的价值无法验证"

    def test_control_arm_overrides_base_args(self):
        """argparse 后出现的值覆盖前面的——对照臂靠这个盖掉 _base_args 的 0.5。"""
        base = m2._base_args(1000, False)
        extra = m2.GROUPS["A_stop_low"]["runs"]["scale_out_0"]
        cmd = base + extra
        assert cmd.index("--scale-out") < len(base), "基准值必须在前"
        assert cmd[len(cmd) - cmd[::-1].index("--scale-out")] == "0", "覆盖值必须在后"

    def test_scale_out_arms_keep_r_denominator(self):
        """分批止盈不动初始止损 ⇒ risk_frac 不变 ⇒ R 可比，按累计R 判。"""
        base = m2.GROUPS["A_stop_low"]["baseline"]
        for n in ("scale_out_0", "scale_out_03", "scale_out_08"):
            assert m2._same_r_denom("A_stop_low", n, base) is True
            assert m2._side_from_flags("A_stop_low", n, base) == "exit"


def _write_reasons(tmp, group, name, reasons, *, expR=0.2, fp="s1000"):
    """按 ``{reason: (笔数, ret, r_multiple)}`` 构造结果文件——出场结构矩阵用。"""
    trades = []
    for rs, (cnt, ret, r) in reasons.items():
        trades += [{"reason": rs, "ret": ret, "r_multiple": r} for _ in range(cnt)]
    n = len(trades)
    (tmp / f"{group}__{name}__{fp}.json").write_text(
        json.dumps(
            {
                "trade_summary": {
                    "n": n,
                    "win_rate": 0.3,
                    "expectancy": 0.004,
                    "expectancy_R": expR,
                    "total_R": round(expR * n, 1),
                    "payoff_ratio": 2.7,
                    "avg_win": 0.11,
                    "avg_loss": 0.04,
                    "avg_holding": 5.0,
                    "exit_reasons": {},
                },
                "trades": trades,
            }
        ),
        encoding="utf-8",
    )


class TestExitReasonBreakdown:
    """离场原因是**结果**，不是可选参数——报表必须把这点写明，否则会被读成「选它收益最高」。

    `+scaled` 只在站上 BBI 且出两根中大阳线时才挂上（backtest_factors:1326/1390），
    `bbi_exit` 也要求曾站上 BBI；而 stop/trail_stop 按定义就是跌下来的交易。
    ⇒ 按均收给离场原因排序，`bbi_exit+scaled` **永远第一**，那是定义不是发现。
    """

    def test_sums_r_not_just_avg_return(self):
        """可加的是 sum_r，不是均收——exit_reasons 里只有 {n, avg_return}，不够用。"""
        trades = [
            {"reason": "bbi_exit+scaled", "ret": 0.36, "r_multiple": 40.0},
            {"reason": "bbi_exit+scaled", "ret": 0.36, "r_multiple": 40.0},
            {"reason": "stop", "ret": -0.03, "r_multiple": -1.0},
        ] * 1
        st = m2._reason_stats(trades)
        assert st["bbi_exit+scaled"]["n"] == 2
        assert st["bbi_exit+scaled"]["sum_r"] == pytest.approx(80.0)
        assert st["bbi_exit+scaled"]["avg_ret"] == pytest.approx(0.36)
        assert st["stop"]["sum_r"] == pytest.approx(-1.0)

    def test_ignores_trades_without_reason(self):
        assert m2._reason_stats([{"ret": 0.1}, {}, "junk"]) == {}

    def test_ranked_by_r_contribution_not_avg_return(
        self, tmp_path, monkeypatch, capsys
    ):
        """均收最高但只 20 笔的桶，R 贡献可能不如均收平平的 900 笔 ⇒ 按 R 排序。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        trades = [{"reason": "bbi_exit+scaled", "ret": 0.36, "r_multiple": 1.0}] * 20
        trades += [{"reason": "bbi_exit", "ret": 0.09, "r_multiple": 5.0}] * 900
        (tmp_path / "A_stop_low__00_baseline__s1000.json").write_text(
            json.dumps(
                {
                    "trade_summary": {
                        "n": 920,
                        "win_rate": 0.3,
                        "expectancy": 0.004,
                        "expectancy_R": 0.2,
                        "total_R": 4520.0,
                        "payoff_ratio": 2.7,
                        "avg_win": 0.11,
                        "avg_loss": 0.04,
                        "avg_holding": 5.0,
                        "exit_reasons": {},
                    },
                    "trades": trades,
                }
            ),
            encoding="utf-8",
        )
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("离场原因分布")[1]
        i_scaled = seg.index("bbi_exit+scaled")
        i_plain = seg.index("\n    bbi_exit ")
        assert i_plain < i_scaled, "R 贡献 4500 的桶应排在 20 的前面，即使后者均收更高"

    def test_warns_that_reason_is_an_outcome(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "A_stop_low__00_baseline__s1000.json").write_text(
            json.dumps(
                {
                    "trade_summary": {
                        "n": 2,
                        "win_rate": 0.5,
                        "expectancy": 0.004,
                        "expectancy_R": 0.2,
                        "total_R": 1.0,
                        "payoff_ratio": 2.7,
                        "avg_win": 0.11,
                        "avg_loss": 0.04,
                        "avg_holding": 5.0,
                        "exit_reasons": {},
                    },
                    "trades": [
                        {"reason": "bbi_exit+scaled", "ret": 0.36, "r_multiple": 2.0},
                        {"reason": "stop", "ret": -0.03, "r_multiple": -1.0},
                    ],
                }
            ),
            encoding="utf-8",
        )
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "这是结果分组，不是可选参数" in out
        assert "那是定义不是发现" in out
        assert "分布迁移" in out

    def test_silent_when_no_reasons(self, tmp_path, monkeypatch, capsys):
        """逐笔没有 reason（--summary-only）时不打空表。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline")
        m2.report(cross=False)
        assert "离场原因分布" not in capsys.readouterr().out


class TestExitStructureMatrix:
    """跨方案的出场结构矩阵——单方案的原因表没有判别力（见 TestExitReasonBreakdown）。

    每个方案单看都是「`bbi_exit+scaled` 均收最高」，那是恒等式。
    有判别力的是**行与行的差**：哪个机制把交易从 `stop` 桶搬走了、期望从哪补回来。
    """

    @pytest.mark.parametrize(
        "reason,want",
        [
            ("bbi_exit", ("bbi", False)),
            ("bbi_exit+scaled", ("bbi", True)),
            ("bbi_exit_delayed", ("bbi", False)),
            ("stop", ("stop", False)),
            ("stop_delayed", ("stop", False)),  # 跳空次日成交，仍是止损
            ("trail_stop", ("trail", False)),
            ("breakeven_stop", ("be", False)),
            ("cost_zone_stop", ("cz", False)),
            ("open_end", ("末持", False)),
            ("open_end+scaled", ("末持", True)),
            ("某个新原因", ("其它", False)),  # 词表扩了也不炸
        ],
    )
    def test_family_mapping(self, reason, want):
        assert m2._reason_family(reason) == want

    def test_scaled_is_an_overlay_not_a_family(self):
        """`+scaled` 与基础族**重叠**：一笔 bbi_exit+scaled 同时计入 bbi 和 scaled。"""
        st = m2._family_stats(
            [
                {"reason": "bbi_exit+scaled", "ret": 0.36, "r_multiple": 3.0},
                {"reason": "bbi_exit", "ret": 0.04, "r_multiple": 0.4},
                {"reason": "stop", "ret": -0.15, "r_multiple": -1.2},
            ]
        )
        assert st["bbi"]["n"] == 2  # 含 +scaled 那笔
        assert st["scaled"]["n"] == 1
        assert st["stop"]["n"] == 1
        # 族的笔数合计（不含 scaled）= 总笔数
        assert sum(d["n"] for f, d in st.items() if f != "scaled") == 3

    def test_matrix_needs_two_schemes(self, tmp_path, monkeypatch, capsys):
        """单方案没有可比性，不打矩阵。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            {"bbi_exit": (50, 0.05, 0.5), "stop": (50, -0.03, -1.0)},
        )
        m2.report(cross=False)
        assert "出场结构对比" not in capsys.readouterr().out

    def test_distribution_shift_is_visible(self, tmp_path, monkeypatch, capsys):
        """cost_zone 把 40% 的交易从 stop 桶搬进 cz 桶——这才是要看的东西。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            {"bbi_exit": (60, 0.05, 0.5), "stop": (40, -0.15, -1.2)},
        )
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "cost_zone_3",
            {
                "bbi_exit": (55, 0.09, 0.8),
                "cost_zone_stop": (40, -0.044, -0.37),
                "stop": (5, -0.14, -1.2),
            },
        )
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("出场结构对比")[1]
        assert "cz" in seg
        base_line = next(ln for ln in seg.split("\n") if "00_baseline" in ln)
        cz_line = next(ln for ln in seg.split("\n") if "cost_zone_3" in ln)
        assert "—" in base_line, "基准没有 cz 桶，应显示占位符"
        assert "40.0" in base_line and "5.0" in cz_line, "stop 占比 40%→5% 的迁移要可见"

    def test_r_row_sums_to_expectancy_r(self, tmp_path, monkeypatch, capsys):
        """表② 行合计必须等于期望R——这是它比「占总R 百分比」好的地方。

        占比的分母是 total_R，而 total_R 可以很小（pct_12 只有 58R，而 bbi 桶 +383R /
        stop 桶 -335R）⇒ 占比会炸到 660% / -578%，跨方案还不可比。
        """
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        # 80 笔 × +0.5R + 20 笔 × -1.2R = 40 - 24 = 16R / 100 笔 = +0.16R/笔
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            {"bbi_exit": (80, 0.05, 0.5), "stop": (20, -0.15, -1.2)},
            expR=0.16,
        )
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "trail_08",
            {"bbi_exit": (70, 0.06, 0.6), "trail_stop": (30, -0.02, -0.3)},
            expR=0.33,
        )
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("每笔 R 贡献")[1]
        base = next(ln for ln in seg.split("\n") if "00_baseline" in ln)
        assert base.split()[-1] == "+0.160", f"行合计应等于期望R: {base}"
        tr = next(ln for ln in seg.split("\n") if "trail_08" in ln)
        assert tr.split()[-1] == "+0.330", f"行合计应等于期望R: {tr}"

    def test_warns_r_only_comparable_within_denominator(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_reasons(
            tmp_path,
            "B_stop_pct",
            "pct_08",
            {"bbi_exit": (80, 0.05, 0.6), "stop": (20, -0.08, -1.0)},
        )
        _write_reasons(
            tmp_path,
            "B_stop_pct",
            "pct_12",
            {"bbi_exit": (75, 0.05, 0.4), "stop": (25, -0.12, -1.0)},
        )
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("每笔 R 贡献")[1]
        assert "risk_frac 相同的方案之间可比" in seg
        assert "跨档位只看表①的分布迁移" in seg


class TestRealizedVsUnrealized:
    """`open_end` 是样本期末仍持仓、按最后一根收盘价标记的**未实现**盈亏
    （backtest_factors:1430）。

    ⚠️ **实测 3000 样本基准**：含未实现累计R **+288**，其中末持 57 笔贡献 **+320R**
    ⇒ 剔掉后 **-32R**，已实现口径是**负期望**。只看「合计」会把一个已实现负期望的策略
    读成正期望——所以报表必须把两个口径分开，并在翻号时明确点出来。
    """

    def test_realized_excludes_open_end_from_both_numerator_and_denominator(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        # 90 笔已平仓净 -9R，10 笔末持 +20R ⇒ 合计 +11R/100 = +0.11；已实现 -9/90 = -0.1
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            {
                "bbi_exit": (30, 0.05, 1.0),
                "stop": (60, -0.03, -0.65),
                "open_end": (10, 0.12, 2.0),
            },
            expR=0.11,
        )
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "trail_08",
            {
                "bbi_exit": (35, 0.06, 1.2),
                "stop": (55, -0.03, -0.6),
                "open_end": (10, 0.12, 2.0),
            },
            expR=0.31,
        )
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("每笔 R 贡献")[1]
        line = next(ln for ln in seg.split("\n") if "00_baseline" in ln)
        assert "+0.110" in line, f"合计应含未实现: {line}"
        assert "-0.100" in line, f"已实现应剔除末持的分子与分母: {line}"

    def test_flip_is_flagged(self, tmp_path, monkeypatch, capsys):
        """含未实现为正、已实现非正 ⇒ 必须点出「没兑现的边际不是边际」。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            {
                "bbi_exit": (30, 0.05, 1.0),
                "stop": (60, -0.03, -0.65),
                "open_end": (10, 0.12, 2.0),
            },
            expR=0.11,
        )
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "trail_08",
            {
                "bbi_exit": (35, 0.06, 1.2),
                "stop": (55, -0.03, -0.6),
                "open_end": (10, 0.12, 2.0),
            },
            expR=0.31,
        )
        out = (m2.report(cross=False), capsys.readouterr().out)[1]
        assert "正期望全部来自未平仓浮盈" in out
        assert "没兑现的边际不是边际" in out

    def test_no_flip_when_realized_positive(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            {
                "bbi_exit": (50, 0.08, 2.0),
                "stop": (40, -0.03, -0.6),
                "open_end": (10, 0.12, 2.0),
            },
            expR=1.0,
        )
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "trail_08",
            {
                "bbi_exit": (55, 0.09, 2.2),
                "stop": (35, -0.03, -0.6),
                "open_end": (10, 0.12, 2.0),
            },
            expR=1.2,
        )
        m2.report(cross=False)
        assert "没兑现的边际不是边际" not in capsys.readouterr().out

    def test_baseline_block_shows_realized(self, tmp_path, monkeypatch, capsys):
        """基准结构块也要打已实现口径——那是判读所有「改进」的绝对水平基线。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_reasons(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            {
                "bbi_exit": (30, 0.05, 1.0),
                "stop": (60, -0.03, -0.65),
                "open_end": (10, 0.12, 2.0),
            },
            expR=0.11,
        )
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "基准已实现口径" in out
        assert "剔除末持(open_end) 10 笔" in out
        assert "相对提升再大，绝对水平仍可能是负的" in out


class TestStopIsTooTightHypothesis:
    """本轮最强结论：**B1 的止损普遍太紧**，两条独立证据都指向放宽有效。

    · A 组 `tick_buffer_3`（当日最低下方留 3 个价位）期望% **+33.3%**、margin +2.6→+3.4pp
    · B 组 4%→5% 放宽，期望% **+81%**（0.37→0.67）；4% 以下大赢家从 70 掉到 61~62

    所以矩阵必须能沿这个方向继续探，而不是停在单点。
    """

    def test_tick_buffer_ladder(self):
        """B1_w.pdf 说「或向下 3-5 个价位」，5 是它给的上界，8 用来看斜率是否续。"""
        buf = sorted(
            int(e[e.index("--stop-tick-buffer") + 1])
            for e in m2.GROUPS["A_stop_low"]["runs"].values()
            if "--stop-tick-buffer" in e and "--trail" not in e
        )
        assert buf == [3, 5, 8]

    def test_buffer_unit_alternatives_present(self):
        """#20（2026-08-12）：pct/atr 风险单位余量进 A 组对照表，默认档位即
        对照口径（pct 0.3% ≈ 10 元股 tick_3；atr 0.2×ATR14）；两者改分母 ⇒ 不按 R 判。"""
        runs = m2.GROUPS["A_stop_low"]["runs"]
        assert runs["low_pct_03"] == ["--stop-buffer", "pct"]
        assert runs["atr_02"] == ["--stop-buffer", "atr"]
        base = m2.GROUPS["A_stop_low"]["baseline"]
        for n in ("low_pct_03", "atr_02"):
            assert m2._side_from_flags("A_stop_low", n, base) == "exit"
            assert m2._same_r_denom("A_stop_low", n, base) is False

    def test_best_pct_tier_has_timing_variant(self):
        """最优档必须有择时变体——跨组表前三全是 amv，却都配 8%/12% 止损。"""
        runs = m2.GROUPS["B_stop_pct"]["runs"]
        amv_tiers = {
            float(e[e.index("--stop-pct") + 1])
            for e in runs.values()
            if "--amv-long-only" in e and "--stop-pct" in e
        }
        assert 5.0 in amv_tiers, f"最优档 5% 缺 amv 变体，实有 {sorted(amv_tiers)}"

    def test_orthogonal_mechanisms_are_stacked(self):
        """单变量扫完要试叠加：trail(移动止盈) 与 tick-buffer(初始止损位) 机制正交。

        正交**不等于**可叠加（可能互相抵消），所以必须有实测方案而不是靠推断。
        """
        runs = m2.GROUPS["A_stop_low"]["runs"]
        stacked = [
            n for n, e in runs.items() if "--trail" in e and "--stop-tick-buffer" in e
        ]
        assert stacked, "缺 trail × tick-buffer 的叠加方案"
        b_runs = m2.GROUPS["B_stop_pct"]["runs"]
        assert any("--trail" in e and "--stop-pct" in e for e in b_runs.values()), (
            "缺「可执行止损 × 移动止盈」的叠加方案"
        )


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
            c = (
                int(e[e.index("--max-concurrent") + 1])
                if "--max-concurrent" in e
                else 5
            )
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
        (tmp_path / "A_stop_low__x__s1000.json").write_text(
            json.dumps(
                {
                    "trade_summary": {
                        "n": 410,
                        "win_rate": 0.363,
                        "expectancy": 0.006,
                        "expectancy_R": 0.769,
                        "total_R": 304.0,
                        "payoff_ratio": 3.2,
                        "avg_win": 0.1212,
                        "avg_loss": 0.04,
                        "avg_holding": 5.0,
                        "exit_reasons": {},
                    },
                    "trades": [{"ret": 0.3}] * 20,
                }
            ),
            encoding="utf-8",
        )
        got = m2._collect(cross=False)["A_stop_low"]
        assert len(got) == 1
        assert got[0]["expR"] == 0.769 and got[0]["totR"] == 304.0
        assert got[0]["big"] == 20

    def test_portfolio_at_top_level(self, tmp_path, monkeypatch):
        """组合级结果的 portfolio 在顶层，与 trade_summary 平级。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "C_portfolio__p__s1000.json").write_text(
            json.dumps(
                {
                    "trade_summary": {
                        "n": 342,
                        "expectancy": 0.01,
                        "expectancy_R": 0.2,
                    },
                    "portfolio": {
                        "total_return": 0.171,
                        "cagr": 0.108,
                        "max_drawdown": 0.029,
                        "n_taken": 150,
                        "n_skipped": 1195,
                    },
                }
            ),
            encoding="utf-8",
        )
        got = m2._collect(cross=False)["C_portfolio"]
        assert got and got[0]["pf"]["total_return"] == 0.171

    def test_unknown_key_falls_back(self, tmp_path, monkeypatch, capsys):
        """键名再改也要能活——扫一层子字典兜底。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "A_stop_low__y__s1000.json").write_text(
            json.dumps(
                {
                    "some_new_name": {
                        "n": 100,
                        "expectancy": 0.005,
                        "expectancy_R": 0.5,
                        "total_R": 50.0,
                        "avg_win": 0.1,
                    }
                }
            ),
            encoding="utf-8",
        )
        got = m2._collect(cross=False)["A_stop_low"]
        assert got and got[0]["expR"] == 0.5
        assert "兜底" in capsys.readouterr().out

    def test_missing_summary_warns_not_silent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "A_stop_low__z__s1000.json").write_text(
            json.dumps({"codes": ["600000"], "count": 500}), encoding="utf-8"
        )
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
            encoding="utf-8",
        )
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
    trades += [
        {"ret": 0.01, "r_multiple": (totR - tail_r) / (n - big)} for _ in range(n - big)
    ]
    (tmp / f"{group}__{name}__{fp}.json").write_text(
        json.dumps(
            {
                "trade_summary": {
                    "n": n,
                    "win_rate": 0.3,
                    "expectancy": 0.004,
                    "expectancy_R": expR,
                    "total_R": totR,
                    "payoff_ratio": 2.7,
                    "avg_win": aw,
                    "avg_loss": 0.04,
                    "avg_holding": 5.0,
                    "exit_reasons": {},
                },
                "trades": trades,
            }
        ),
        encoding="utf-8",
    )


def _write_split(tmp, group, name, *, n, expR, aw, big, tail_r, nontail_r, fp="s1000"):
    """按**尾部R / 非尾部R 绝对量**构造结果文件。

    非尾部为负是实测常态（基准 -673R），比值口径在这里会 >1 并把方向读反，
    所以测试也必须按绝对量构造。
    """
    trades = [{"ret": 0.35, "r_multiple": tail_r / big} for _ in range(big)]
    trades += [
        {"ret": 0.01, "r_multiple": nontail_r / (n - big)} for _ in range(n - big)
    ]
    (tmp / f"{group}__{name}__{fp}.json").write_text(
        json.dumps(
            {
                "trade_summary": {
                    "n": n,
                    "win_rate": 0.3,
                    "expectancy": 0.004,
                    "expectancy_R": expR,
                    "total_R": tail_r + nontail_r,
                    "payoff_ratio": 2.7,
                    "avg_win": aw,
                    "avg_loss": 0.04,
                    "avg_holding": 5.0,
                    "exit_reasons": {},
                },
                "trades": trades,
            }
        ),
        encoding="utf-8",
    )


def _write_pct(
    tmp, name, *, n, exp, stop, win, payoff, big, group="B_stop_pct", fp="s1000"
):
    """按「期望% + 固定 risk_frac」构造结果——期望R 由二者算出，与实盘口径一致。

    pct 模式下 risk_frac 恒等于 stop_pct（backtest_factors:1294）⇒
    期望R = 期望% ÷ stop_pct、累计R = 期望R × 笔数。测试必须这样构造，
    否则就测不出「R 差异里混着分母」这件事。
    """
    expR = exp / stop
    trades = [{"ret": 0.35, "r_multiple": 0.35 / stop} for _ in range(big)]
    trades += [{"ret": 0.01, "r_multiple": 0.01 / stop} for _ in range(n - big)]
    (tmp / f"{group}__{name}__{fp}.json").write_text(
        json.dumps(
            {
                "trade_summary": {
                    "n": n,
                    "win_rate": win,
                    "expectancy": exp,
                    "expectancy_R": round(expR, 3),
                    "total_R": round(expR * n, 1),
                    "payoff_ratio": payoff,
                    "avg_win": 0.104,
                    "avg_loss": 0.04,
                    "avg_holding": 5.0,
                    "exit_reasons": {},
                },
                "trades": trades,
            }
        ),
        encoding="utf-8",
    )


class TestExitVsEntrySideVerdict:
    """出场类与入场类分开判（2026-08-05 调整）。

    「削大赢家」这条判据的初衷是防止**为提高胜率而筛掉大赢家**——那些收益会
    **永久消失**。但出场机制不筛信号（`trail_08` 笔数 1294→1298），它只改离场时点，
    用「少赚一点尾部」换「多一些赢家」。对它硬套「大赢家占比不降」，会把
    累计R +43.1% 的方案否掉——实测就发生了。
    """

    def test_classifies_by_trade_count(self):
        base = {"n": 1294}
        assert m2._is_exit_side({"n": 1298}, base) is True  # 笔数几乎不变 ⇒ 出场
        assert m2._is_exit_side({"n": 230}, base) is False  # 择时大幅减少 ⇒ 入场
        assert m2._is_exit_side({"n": 409}, base) is False

    def test_exit_side_passes_on_total_r(self, tmp_path, monkeypatch, capsys):
        """出场类：累计R 提升即通过，即使均盈/大赢家占比略降。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            n=1294,
            expR=0.202,
            totR=250.5,
            aw=0.1098,
            big=61,
        )
        _write_tr(
            tmp_path,
            "A_stop_low",
            "trail_08",
            n=1298,
            expR=0.288,
            totR=358.4,
            aw=0.1015,
            big=52,
        )
        m2.report(cross=False)
        line = next(
            ln
            for ln in capsys.readouterr().out.split("\n")
            if "trail_08" in ln and "累计R" in ln
        )
        assert "✅ 通过" in line and "[出场]" in line
        assert "+43" in line

    def test_exit_side_rejected_when_total_r_flat(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            n=1294,
            expR=0.202,
            totR=250.5,
            aw=0.1098,
            big=61,
        )
        _write_tr(
            tmp_path,
            "A_stop_low",
            "cost_zone_3",
            n=1290,
            expR=0.19,
            totR=245.0,
            aw=0.0976,
            big=45,
        )
        m2.report(cross=False)
        line = next(
            ln
            for ln in capsys.readouterr().out.split("\n")
            if "cost_zone_3" in ln and "累计R" in ln
        )
        assert "❌ 否决" in line

    def test_entry_side_keeps_strict_rule(self, tmp_path, monkeypatch, capsys):
        """入场类仍严格：筛掉大赢家的收益永久消失，不能只看总量。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            n=1294,
            expR=0.202,
            totR=250.5,
            aw=0.1098,
            big=61,
        )
        # 笔数腰斩且大赢家占比下降 ⇒ 入场类应否决
        _write_tr(
            tmp_path,
            "A_stop_low",
            "some_filter",
            n=600,
            expR=0.30,
            totR=180.0,
            aw=0.09,
            big=15,
        )
        m2.report(cross=False)
        line = next(
            ln
            for ln in capsys.readouterr().out.split("\n")
            if "some_filter" in ln and "期望R" in ln
        )
        assert "❌ 否决" in line and "[入场]" in line

    def test_no_warning_when_only_nontail_improves(self, tmp_path, monkeypatch, capsys):
        """**实测 trail_08 场景**：尾部 924→846(-8%)、非尾部 -673→-488(少亏 185R)。

        旧比值口径（369%→236%）会警示「收益更依赖中等赢家」——方向反了。
        中部失血减少正是这个出场机制该干的事，不得警示。
        """
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_split(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            n=1294,
            expR=0.202,
            aw=0.1098,
            big=61,
            tail_r=924.0,
            nontail_r=-673.5,
        )
        _write_split(
            tmp_path,
            "A_stop_low",
            "trail_08",
            n=1298,
            expR=0.288,
            aw=0.1015,
            big=52,
            tail_r=846.0,
            nontail_r=-487.6,
        )
        m2.report(cross=False)
        out = capsys.readouterr().out
        line = next(ln for ln in out.split("\n") if "trail_08" in ln and "累计R" in ln)
        assert "✅ 通过" in line
        warn = [
            ln for ln in out.split("\n") if ln.startswith("      ⚠️") and "尾部R" in ln
        ]
        assert not warn, f"中部改善不该被警示成退化: {warn}"

    def test_warns_when_tail_r_actually_shrinks(self, tmp_path, monkeypatch, capsys):
        """尾部R 绝对量缩水 >30% 才警示——那才是真的「削大赢家」。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_split(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            n=1294,
            expR=0.202,
            aw=0.1098,
            big=61,
            tail_r=924.0,
            nontail_r=-673.5,
        )
        _write_split(
            tmp_path,
            "A_stop_low",
            "trail_12",
            n=1295,
            expR=0.236,
            aw=0.1070,
            big=30,
            tail_r=500.0,
            nontail_r=-200.0,
        )
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "尾部R 924→500" in out and "大赢家贡献显著缩水" in out
        assert "非尾部R -674→-200" in out, "同时打出非尾部变化，供判断净效果"

    def test_baseline_structure_surfaces_negative_nontail(
        self, tmp_path, monkeypatch, capsys
    ):
        """基准收益结构要显式打出来——「非尾部整体亏损」是这套策略最关键的事实。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_split(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            n=1294,
            expR=0.202,
            aw=0.1098,
            big=61,
            tail_r=924.0,
            nontail_r=-673.5,
        )
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "基准收益结构" in out
        assert "尾部R +924" in out and "非尾部R -674" in out
        assert "非尾部整体亏损" in out
        assert "漏掉几只大赢家" in out


class TestTailSplit:
    """尾部R 按**绝对量**拆分，不用比值。

    比值 `尾部R/总R` 在「非尾部为负」时 >1，且**分母越小比值越大** ⇒ 无法区分
    「尾部变小」与「中部变好」。实测基准：总R 250.5 = 尾部 924 + 非尾部 **-673**，
    比值 369%；trail_08 是 846 + (-488)，比值 236%——旧文案把它读成
    「收益更依赖中等赢家」，恰好说反：它是**中部失血减少**。
    """

    def test_splits_tail_and_nontail(self):
        trades = [
            {"ret": 0.3, "r_multiple": 7.0},
            {"ret": 0.3, "r_multiple": 7.0},
            {"ret": 0.01, "r_multiple": 3.0},
            {"ret": -0.02, "r_multiple": -1.0},
        ]
        assert m2._tail_split(trades) == pytest.approx((14.0, 2.0))

    def test_nontail_can_be_negative(self):
        """非尾部为负是**实测常态**（基准 -673R），不能当异常处理掉。"""
        trades = [{"ret": 0.35, "r_multiple": 40.0}] + [
            {"ret": -0.03, "r_multiple": -1.5}
        ] * 20
        tail, non = m2._tail_split(trades)
        assert tail == pytest.approx(40.0) and non == pytest.approx(-30.0)

    def test_none_without_r_multiple(self):
        """--summary-only 时逐笔没有 r_multiple，要返回 None 而不是 (0,0)。"""
        assert m2._tail_split([{"ret": 0.3}]) is None
        assert m2._tail_split([]) is None

    def test_zero_total_still_splits(self):
        """总R 为 0 也要能拆——比值口径在这里会除零，绝对量不会。"""
        assert m2._tail_split(
            [{"ret": 0.3, "r_multiple": 5.0}, {"ret": -0.1, "r_multiple": -5.0}]
        ) == (5.0, -5.0)


class TestSideClassifiedByFlags:
    """出场/入场按**参数语义**分类，不按笔数（2026-08-05 修）。

    笔数判据对 `--trail`（1294→1298）成立，但对**止损距离**不成立：非重叠去重下
    止损越紧 → 离场越早 → 后续还能再进场 ⇒ 笔数系统性增加。实测 300 样本
    pct_05=364 vs pct_12=342 差 6.4%，已越过 5% 线 ⇒ pct_05 会被误判成入场类，
    去过「大赢家占比不降」——正是 6785724 刚修掉的那类误否。
    """

    @pytest.mark.parametrize(
        "group,name,want",
        [
            ("A_stop_low", "trail_08", "exit"),
            ("A_stop_low", "be_05", "exit"),
            ("A_stop_low", "cost_zone_3", "exit"),
            ("A_stop_low", "tick_buffer_3", "exit"),
            ("A_stop_low", "trigger_intraday", "exit"),
            ("A_stop_low", "amv_long_only", "entry"),  # 择时筛信号
            ("B_stop_pct", "pct_05", "exit"),  # 关键：止损距离仍是出场类
            ("B_stop_pct", "pct_12", "exit"),
            ("B_stop_pct", "pct_12_amv", "entry"),  # 带 amv ⇒ 按入场类严格判
            ("B_stop_pct", "pct_08_amv", "entry"),
        ],
    )
    def test_flag_semantics(self, group, name, want):
        base = m2.GROUPS[group]["baseline"]
        assert m2._side_from_flags(group, name, base) == want

    def test_pct_05_not_misjudged_despite_trade_count_gap(
        self, tmp_path, monkeypatch, capsys
    ):
        """笔数差 6.4%（越过旧 5% 线）也必须归为出场机制，不能判成入场类。

        但它同时**改了 R 分母**（stop_pct 5 vs 8）⇒ 标 [出场·R口径变]，改用期望% 判。
        """
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(
            tmp_path,
            "B_stop_pct",
            "pct_08",
            n=342,
            expR=0.24,
            totR=83.0,
            aw=0.11,
            big=40,
        )
        _write_tr(
            tmp_path,
            "B_stop_pct",
            "pct_05",
            n=364,
            expR=0.433,
            totR=157.7,
            aw=0.095,
            big=38,
        )
        m2.report(cross=False)
        line = next(
            ln for ln in capsys.readouterr().out.split("\n") if "  pct_05  " in ln
        )
        assert "[出场·R口径变]" in line, f"止损距离是出场机制但改了 R 分母: {line}"


class TestRDenominatorGuard:
    """改动**止损距离**就改动了 R 的分母 ⇒ R 一律不可比（2026-08-05 修）。

    `backtest_factors.py:1294` 是 `stop = entry * (1 - stop_pct/100)`，所以 pct 模式下
    `risk_frac` **恒等于 stop_pct**，期望R = 期望% ÷ stop_pct。实测：

        pct_05  期望 +0.67%  期望R 0.134  累计R 157.5
        pct_08  期望 +0.64%  期望R 0.080  累计R  90.6

    「累计R +73.8%」里 **1.6 倍纯粹是分母 8%/5%**，真实期望率几乎持平。
    这就是本模块开头声明要防的「跨 R 口径比 R」，只是发生在 B 组**内部**
    ——分组只按 stop_mode 分，没按 stop_pct 分。
    """

    @pytest.mark.parametrize(
        "group,name,same",
        [
            # 改止损距离 ⇒ 分母变
            ("B_stop_pct", "pct_05", False),
            ("B_stop_pct", "pct_12", False),
            ("A_stop_low", "tick_buffer_3", False),
            # 只改离场时点 ⇒ risk_frac 在 1297 行按**初始**止损算完，分母不变
            ("A_stop_low", "trail_08", True),
            ("A_stop_low", "be_03", True),
            ("A_stop_low", "cost_zone_3", True),
            ("A_stop_low", "trigger_intraday", True),
            ("A_stop_low", "amv_long_only", True),
            # 与基准同档位的择时变体 ⇒ 分母一致，R 可比
            ("B_stop_pct", "pct_08_amv", True),
        ],
    )
    def test_denominator_identity(self, group, name, same):
        base = m2.GROUPS[group]["baseline"]
        assert m2._same_r_denom(group, name, base) is same

    def test_unknown_scheme_assumed_same(self):
        """查不到参数时假定同口径——误判成「口径不同」会让判定变宽松，更危险。"""
        assert m2._same_r_denom("A_stop_low", "手工方案", "00_baseline") is True

    def test_diff_denominator_judged_by_expectancy(self, tmp_path, monkeypatch, capsys):
        """实测 pct_05 vs pct_08：期望 0.67% vs 0.64% ⇒ **+4.7%**，不是 +73.8%。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_pct(
            tmp_path,
            "pct_08",
            n=1136,
            exp=0.0064,
            stop=0.08,
            win=0.482,
            payoff=1.227,
            big=76,
        )
        _write_pct(
            tmp_path,
            "pct_05",
            n=1177,
            exp=0.0067,
            stop=0.05,
            win=0.446,
            payoff=1.453,
            big=70,
        )
        m2.report(cross=False)
        out = capsys.readouterr().out
        line = next(ln for ln in out.split("\n") if "  pct_05  " in ln)
        assert "期望% +0.64→+0.67" in line and "+4.7%" in line
        assert "累计R" not in line, "异口径判定行不该出现 R"
        assert "R 口径不同" in out
        assert "✅ 通过" in line

    def test_diff_denominator_rejects_on_thin_margin(
        self, tmp_path, monkeypatch, capsys
    ):
        """异口径的出场类还要看 margin：期望% 涨一点但安全边际变薄 ⇒ 否决。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_pct(
            tmp_path,
            "pct_08",
            n=1136,
            exp=0.0064,
            stop=0.08,
            win=0.482,
            payoff=1.227,
            big=76,
        )
        # 期望 +9%，但胜率掉到勉强够本 ⇒ margin 从 +3.3pp 缩到 +0.5pp
        _write_pct(
            tmp_path,
            "pct_05",
            n=1177,
            exp=0.0070,
            stop=0.05,
            win=0.413,
            payoff=1.325,
            big=70,
        )
        m2.report(cross=False)
        line = next(
            ln for ln in capsys.readouterr().out.split("\n") if "  pct_05  " in ln
        )
        assert "❌ 否决" in line and "margin" in line and "变薄" in line

    def test_tick_buffer_no_longer_judged_by_r(self, tmp_path, monkeypatch, capsys):
        """实测 tick_buffer_3：期望 +0.52% 明显优于基准 +0.39%，却因累计R -3.0% 被否。

        stop 从当日最低再往下 3 个价位，而 A 组 risk_frac 中位仅 0.65%
        ⇒ 分母可能多出近 50%，R 被系统性压低。**那个否决是分母造成的。**
        """
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_pct(
            tmp_path,
            "00_baseline",
            n=1294,
            exp=0.0039,
            stop=0.0065,
            win=0.298,
            payoff=2.678,
            big=61,
            group="A_stop_low",
        )
        _write_pct(
            tmp_path,
            "tick_buffer_3",
            n=1283,
            exp=0.0052,
            stop=0.0095,
            win=0.324,
            payoff=2.450,
            big=63,
            group="A_stop_low",
        )
        m2.report(cross=False)
        out = capsys.readouterr().out
        line = next(ln for ln in out.split("\n") if "  tick_buffer_3  " in ln)
        assert "[出场·R口径变]" in line
        assert "✅ 通过" in line, f"期望% +33% 应通过: {line}"

    def test_unknown_scheme_falls_back_to_trade_count(self):
        """GROUPS 里查不到的方案（手工拷进来的文件）回落到笔数启发式。"""
        assert m2._side_from_flags("A_stop_low", "手工方案", "00_baseline") is None
        assert (
            m2._side_of(
                "A_stop_low",
                {"name": "手工方案", "n": 1298},
                {"n": 1294},
                "00_baseline",
            )
            == "exit"
        )
        assert (
            m2._side_of(
                "A_stop_low", {"name": "手工方案", "n": 230}, {"n": 1294}, "00_baseline"
            )
            == "entry"
        )

    def test_baseline_flags_subtracted(self):
        """与基准做差：B 组基准自带 --stop-pct，不能因此把所有方案都算成出场。"""
        # pct_12_amv 相对 pct_08 的差集含 --amv-long-only ⇒ entry
        assert m2._side_from_flags("B_stop_pct", "pct_12_amv", "pct_08") == "entry"

    def test_flag_pairs_keeps_values(self):
        """只比 flag 名会把 `--stop-pct 5` 和 `--stop-pct 8` 看成「没差异」。"""
        assert m2._flag_pairs(["--stop-pct", "5"]) == {"--stop-pct": "5"}
        assert m2._flag_pairs(["--amv-long-only"]) == {"--amv-long-only": None}
        assert m2._flag_pairs(
            ["--stop-pct", "12", "--amv-long-only", "--cost-zone-bars", "3"]
        ) == {"--stop-pct": "12", "--amv-long-only": None, "--cost-zone-bars": "3"}
        assert m2._flag_pairs([]) == {}


class TestExitSideNeedsExpectancyToo:
    """出场类不能只看累计R——**止损越紧笔数越多**，纯摊薄就能凑够 +2%。"""

    def test_total_r_gain_from_trade_count_is_rejected(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        # 期望R 略降(0.202→0.198)，但笔数 +6% ⇒ 累计R +3.9% 达标
        _write_tr(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            n=1294,
            expR=0.202,
            totR=261.4,
            aw=0.11,
            big=61,
        )
        _write_tr(
            tmp_path,
            "A_stop_low",
            "cost_zone_3",
            n=1372,
            expR=0.198,
            totR=271.7,
            aw=0.11,
            big=64,
        )
        m2.report(cross=False)
        line = next(
            ln
            for ln in capsys.readouterr().out.split("\n")
            if "cost_zone_3" in ln and "累计R" in ln
        )
        assert "❌ 否决" in line, f"累计R 靠笔数摊薄不算改进: {line}"
        assert "摊薄" in line

    def test_real_improvement_still_passes(self, tmp_path, monkeypatch, capsys):
        """trail_08 实测：期望R +42.6% 且累计R +43.1% ⇒ 必须通过。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            n=1294,
            expR=0.202,
            totR=250.5,
            aw=0.1098,
            big=61,
        )
        _write_tr(
            tmp_path,
            "A_stop_low",
            "trail_08",
            n=1298,
            expR=0.288,
            totR=358.4,
            aw=0.1015,
            big=52,
        )
        m2.report(cross=False)
        line = next(
            ln
            for ln in capsys.readouterr().out.split("\n")
            if "trail_08" in ln and "累计R" in ln
        )
        assert "✅ 通过" in line and "笔数" in line

    def test_trade_count_delta_is_shown(self, tmp_path, monkeypatch, capsys):
        """笔数变化必须显式打出来——它是累计R 判读的前提。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(
            tmp_path,
            "A_stop_low",
            "00_baseline",
            n=1000,
            expR=0.2,
            totR=200.0,
            aw=0.11,
            big=50,
        )
        _write_tr(
            tmp_path,
            "A_stop_low",
            "trail_12",
            n=1100,
            expR=0.25,
            totR=275.0,
            aw=0.11,
            big=55,
        )
        m2.report(cross=False)
        line = next(
            ln
            for ln in capsys.readouterr().out.split("\n")
            if "trail_12" in ln and "累计R" in ln
        )
        assert "+10.0%" in line


class TestBGroupBaselineIsMiddleTier:
    """B 组基准取中间档。拿已认定最差的 pct_12 当基准，✅/❌ 退化成「比最差的好」。"""

    def test_baseline_is_pct_08(self):
        assert m2.GROUPS["B_stop_pct"]["baseline"] == "pct_08"

    def test_baseline_is_not_an_extreme(self):
        pcts = sorted(
            float(e[e.index("--stop-pct") + 1])
            for n, e in m2.GROUPS["B_stop_pct"]["runs"].items()
            if "--stop-pct" in e and "amv" not in n
        )
        base = m2.GROUPS["B_stop_pct"]["baseline"]
        bp = float(
            m2.GROUPS["B_stop_pct"]["runs"][base][
                m2.GROUPS["B_stop_pct"]["runs"][base].index("--stop-pct") + 1
            ]
        )
        assert min(pcts) < bp < max(pcts), f"基准 {bp} 不该是极端档位（{pcts}）"

    def test_report_says_relative(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write_tr(
            tmp_path,
            "B_stop_pct",
            "pct_08",
            n=342,
            expR=0.24,
            totR=83.0,
            aw=0.11,
            big=40,
        )
        _write_tr(
            tmp_path,
            "B_stop_pct",
            "pct_12",
            n=330,
            expR=0.17,
            totR=57.2,
            aw=0.12,
            big=38,
        )
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "参数扫描" in out and "相对中间档" in out


class TestMissingSchemesSurfaced:
    """缺失方案必须在报表里点名——否则 [FAIL] 滚屏之后，报表只是少一行。"""

    def test_lists_absent_schemes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline")
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "缺" in out and "个方案的结果文件" in out
        assert "A_stop_low/trail_08" in out
        assert "C_portfolio/pf_c5_p20" in out
        assert "A_stop_low/00_baseline" not in out.split("缺")[-1]

    def test_silent_when_complete(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        for g, meta in m2.GROUPS.items():
            for n in meta["runs"]:
                _write(
                    tmp_path,
                    g,
                    n,
                    pf={
                        "total_return": 0.1,
                        "cagr": 0.05,
                        "max_drawdown": 0.1,
                        "n_taken": 100,
                        "n_skipped": 100,
                    },
                )
        m2.report(cross=False)
        assert "个方案的结果文件" not in capsys.readouterr().out


class TestPortfolioRankedByReturnOverDD:
    """组合表按**收益/回撤**排序。按总收益排会系统性偏袒高敞口方案。"""

    def test_ret_over_dd_prefers_recorded_value(self):
        assert m2._ret_over_dd({"return_over_maxdd": 1.61}) == pytest.approx(1.61)

    def test_ret_over_dd_computed_when_absent(self):
        assert m2._ret_over_dd(
            {"total_return": 0.2, "max_drawdown": 0.1}
        ) == pytest.approx(2.0)

    def test_ret_over_dd_none_on_zero_dd(self):
        assert m2._ret_over_dd({"total_return": 0.2, "max_drawdown": 0}) is None
        assert m2._ret_over_dd({}) is None

    def test_high_exposure_not_ranked_first(self, tmp_path, monkeypatch, capsys):
        """高敞口：总收益更高但回撤更大 ⇒ 不该排第一。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(
            tmp_path,
            "C_portfolio",
            "pf_c5_p20",  # 满仓：收益高、回撤更高
            pf={
                "total_return": 0.40,
                "cagr": 0.20,
                "max_drawdown": 0.47,
                "n_taken": 299,
                "n_skipped": 1046,
            },
        )
        _write(
            tmp_path,
            "C_portfolio",
            "pf_c5_p05",  # 低敞口：收益低但回撤小
            pf={
                "total_return": 0.18,
                "cagr": 0.11,
                "max_drawdown": 0.09,
                "n_taken": 150,
                "n_skipped": 1195,
            },
        )
        m2.report(cross=False)
        seg = capsys.readouterr().out.split("【C_portfolio】")[1]
        assert seg.index("pf_c5_p05") < seg.index("pf_c5_p20")
        assert "收益/回撤" in seg

    def test_topn_excluded_from_cross_table(self, tmp_path, monkeypatch, capsys):
        """--top-n 的逐笔是未去重全候选，不能与其它方案并列比收益率。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", exp=0.004)
        got = m2._collect(cross=False)
        assert got["A_stop_low"][0]["topn"] is False


class TestReuseTradesForPortfolio:
    """C 组 8 个方案只做 1 次真回测——回测参数与 B 组 pct_12/pct_12_amv 完全相同，
    差异全在资金曲线层；只有 `--top-n`（collect_all，逐笔口径不同）必须自己跑。"""

    def test_reuse_map_points_at_same_backtest_params(self):
        pf_layer = {
            "--max-concurrent",
            "--max-pos",
            "--risk-pct",
            "--top-n",
            "--portfolio",
        }
        for g, meta in m2.GROUPS.items():
            for dst, ref in (meta.get("reuse") or {}).items():
                sg, sn = ref.split("/", 1) if "/" in ref else (g, ref)
                # 必须含 common：C 组的 --stop-pct 12 写在 common 里
                a = m2._flag_pairs(list(meta["common"]) + list(meta["runs"][dst]))
                b = m2._flag_pairs(
                    list(m2.GROUPS[sg]["common"]) + list(m2.GROUPS[sg]["runs"][sn])
                )
                changed = {
                    f for f in set(a) | set(b) if a.get(f, "\0") != b.get(f, "\0")
                }
                assert changed <= pf_layer, (
                    f"{g}/{dst} 与 {ref} 差异不止资金曲线层: {changed}"
                )
                # collect_all 由 --top-n 决定，两边必须一致，否则逐笔口径不同
                assert ("--top-n" in a) == ("--top-n" in b), (
                    f"{g}/{dst} vs {ref} top-n 不一致"
                )

    def test_reuse_sources_are_not_themselves_derived(self):
        for g, meta in m2.GROUPS.items():
            reuse = meta.get("reuse") or {}
            for ref in reuse.values():
                sg, sn = ref.split("/", 1) if "/" in ref else (g, ref)
                assert sn not in (m2.GROUPS[sg].get("reuse") or {}), (
                    f"{ref} 既是源又是派生，会形成依赖链"
                )

    def test_only_one_real_backtest_left_in_c_group(self):
        """C 组 8 个方案里只该剩 1 个真回测（collect_all 那条）。"""
        meta = m2.GROUPS["C_portfolio"]
        real = [n for n in meta["runs"] if n not in meta["reuse"]]
        assert real == ["pf_top2_c2_amv"], real
        assert m2._is_heavy("C_portfolio", "pf_top2_c2_amv") is True

    def test_from_trades_injected_when_source_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        src = tmp_path / "B_stop_pct__pct_12__s1000.json"
        src.write_text("{}", encoding="utf-8")
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            (tmp_path / "C_portfolio__pf_c2_p20__s1000.json").write_text(
                "{}", encoding="utf-8"
            )
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(m2.subprocess, "run", fake_run)
        m2._run(
            "C_portfolio", "pf_c2_p20", ["--max-concurrent", "2"], 1000, False, False
        )
        assert "--from-trades" in seen["cmd"]
        assert str(src) in seen["cmd"], "跨组复用要指向 B 组的结果文件"

    def test_full_backtest_when_source_missing(self, tmp_path, monkeypatch):
        """源不存在（--only 单跑派生方案）⇒ 全量回测。全量永远正确，只是慢。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            (tmp_path / "C_portfolio__pf_c2_p20__s1000.json").write_text(
                "{}", encoding="utf-8"
            )
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(m2.subprocess, "run", fake_run)
        _, _, log = m2._run(
            "C_portfolio",
            "pf_c2_p20",
            ["--max-concurrent", "2"],
            1000,
            False,
            False,
            True,
        )  # capture=True：日志进返回值
        assert "--from-trades" not in seen["cmd"]
        assert "改为全量回测" in log

    def test_retry_full_backtest_when_reuse_rejected(self, tmp_path, monkeypatch):
        """复用被拒（口径核对不过）⇒ 自动退回全量回测。

        全量回测永远正确，只是慢。不能让「省时间」变成「少一个方案」——
        少一行的报表正是这套脚本一直在防的失效模式。
        """
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        (tmp_path / "B_stop_pct__pct_12__s1000.json").write_text("{}", encoding="utf-8")
        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "--from-trades" in cmd:  # 第一次：复用失败
                return type("R", (), {"returncode": 2, "stdout": "口径不一致"})()
            (tmp_path / "C_portfolio__pf_c2_p20__s1000.json").write_text(
                "{}", encoding="utf-8"
            )
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(m2.subprocess, "run", fake_run)
        _, path, log = m2._run(
            "C_portfolio",
            "pf_c2_p20",
            ["--max-concurrent", "2", "--max-pos", "20"],
            1000,
            False,
            False,
            True,
        )
        assert len(calls) == 2, "应重试一次"
        assert "--from-trades" in calls[0] and "--from-trades" not in calls[1]
        # 退回后 --max-concurrent 等原参数必须还在
        assert "--max-concurrent" in calls[1] and "2" in calls[1]
        assert path is not None and "退回全量回测" in log

    def test_sources_run_before_derived(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        order: list[str] = []
        monkeypatch.setattr(
            m2,
            "_run",
            lambda g, n, e, s, c, f, cap=False, ds="tdx", w=None, cf=None: (
                order.append(n),
                (f"{g}/{n}", tmp_path / "x", ""),
            )[1],
        )
        todo = [
            ("C_portfolio", "pf_c2_p20", []),  # 派生（复用 B 组）
            ("B_stop_pct", "pct_12", []),
        ]  # 源（故意排在后面）
        m2._run_all(todo, 1000, False, False, 1)
        assert order.index("pct_12") < order.index("pf_c2_p20")

    def test_heavy_runs_isolated_from_light(self, tmp_path, monkeypatch, capsys):
        """collect_all 方案单独串行——它的逐笔条数高一个量级，并行会撞 OOM。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        order: list[str] = []
        monkeypatch.setattr(
            m2,
            "_run",
            lambda g, n, e, s, c, f, cap=False, ds="tdx", w=None, cf=None: (
                order.append(n),
                (f"{g}/{n}", tmp_path / "x", ""),
            )[1],
        )
        todo = [
            ("C_portfolio", "pf_top2_c2_amv", []),  # 重活
            ("A_stop_low", "be_03", []),
        ]  # 轻活
        m2._run_all(todo, 1000, False, False, 4)
        assert order.index("be_03") < order.index("pf_top2_c2_amv")
        assert "单独串行" in capsys.readouterr().out


class TestFailureRecap:
    def test_failed_runs_listed_at_end(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        monkeypatch.setattr(
            m2,
            "_run",
            lambda g, n, e, s, c, f, cap=False, ds="tdx", w=None, cf=None: (
                f"{g}/{n}",
                None,
                "[FAIL] boom",
            ),
        )
        m2._run_all([("A_stop_low", "be_03", [])], 1000, False, False, 1)
        out = capsys.readouterr().out
        assert "1 个方案失败" in out and "A_stop_low/be_03" in out
        assert "OOM" in out, "失败汇总要提示 OOM 的排查方向（退出码 137/-9）"

    def test_serial_prints_each_line_once(self, tmp_path, monkeypatch, capsys):
        """串行实时打印、并行整块返回——两种模式都不能重复也不能丢行。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)

        def fake_run(cmd, **kw):
            (tmp_path / "A_stop_low__be_03__s1000.json").write_text(
                "{}", encoding="utf-8"
            )
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(m2.subprocess, "run", fake_run)
        m2._run_all(
            [("A_stop_low", "be_03", ["--breakeven", "0.03"])], 1000, False, False, 1
        )
        out = capsys.readouterr().out
        assert out.count("[RUN ] A_stop_low/be_03") == 1
        assert out.count("[DONE] A_stop_low/be_03") == 1

    def test_parallel_prints_heartbeat_on_start(self, tmp_path, monkeypatch, capsys):
        """并行波要有**立刻可见**的心跳。

        ⚠️ 没有它时「在跑」与「已死」在日志上长得一模一样，而并行波要等一整个方案
        （3000 只约 60 分钟）才有输出 —— owner 实测因此以为卡住、去 ps 找进程。
        """
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        monkeypatch.setattr(m2, "_avail_mem_mb", lambda: 64_000.0)
        monkeypatch.setattr(m2.os, "cpu_count", lambda: 8)

        def fake_run(cmd, **kw):
            out = next(x for i, x in enumerate(cmd) if cmd[i - 1] == "--out")
            pathlib.Path(out).write_text("{}", encoding="utf-8")
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(m2.subprocess, "run", fake_run)
        m2._run_all(
            [
                ("A_stop_low", "be_03", ["--breakeven", "0.03"]),
                ("A_stop_low", "be_05", ["--breakeven", "0.05"]),
            ],
            1000,
            False,
            False,
            2,
        )
        out = capsys.readouterr().out
        assert out.count("[START] A_stop_low/be_03") == 1
        assert out.count("[START] A_stop_low/be_05") == 1
        # 心跳在结果块之前
        assert out.index("[START] A_stop_low/be_03") < out.index(
            "[DONE] A_stop_low/be_03"
        )

    def test_serial_has_no_duplicate_heartbeat(self, tmp_path, monkeypatch, capsys):
        """串行本来就实时打印，不该再多一行心跳。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)

        def fake_run(cmd, **kw):
            (tmp_path / "A_stop_low__be_03__s1000.json").write_text(
                "{}", encoding="utf-8"
            )
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(m2.subprocess, "run", fake_run)
        m2._run_all(
            [("A_stop_low", "be_03", ["--breakeven", "0.03"])], 1000, False, False, 1
        )
        assert "[START]" not in capsys.readouterr().out

    def test_parallel_prints_each_line_once(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        monkeypatch.setattr(m2, "_avail_mem_mb", lambda: 64_000.0)

        def fake_run(cmd, **kw):
            out = next(x for i, x in enumerate(cmd) if cmd[i - 1] == "--out")
            pathlib.Path(out).write_text("{}", encoding="utf-8")
            return type("R", (), {"returncode": 0, "stdout": "子进程输出"})()

        monkeypatch.setattr(m2.subprocess, "run", fake_run)
        m2._run_all(
            [
                ("A_stop_low", "be_03", ["--breakeven", "0.03"]),
                ("A_stop_low", "be_05", ["--breakeven", "0.05"]),
            ],
            1000,
            False,
            False,
            2,
        )
        out = capsys.readouterr().out
        assert out.count("[RUN ] A_stop_low/be_03") == 1
        assert out.count("[DONE] A_stop_low/be_03") == 1
        assert out.count("子进程输出") == 2, "并行时子进程输出必须收集后打印，不能丢"


class TestReportUsesTheBatchJustRun:
    """实跑时报表必须汇总**刚跑的那批**。

    原先 `report(cross, a.sample if a.report_only else None)` ⇒ 实跑传 None ⇒
    自动取「最大样本量」批次：跑 `--sample 300` 试跑，报表却显示 s1000 的旧结果，
    而它看起来跟新结果一模一样。
    """

    def test_run_reports_its_own_sample(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        monkeypatch.setattr(m2, "_run_all", lambda *a, **k: None)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s300", n=409)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s2000", n=2600)
        # --no-window/--no-pin-universe（2026-08-12 #17 起钉死是默认）：本测试聚焦
        # 「汇总刚跑的那批样本」，与钉死无关；不关默认钉死则会去找 s300_w*_u 指纹，
        # 且 _prepare_universe 会真跑子进程。
        monkeypatch.setattr(
            sys, "argv", ["m2", "--sample", "300", "--no-window", "--no-pin-universe"]
        )
        m2.main()
        out = capsys.readouterr().out
        assert "样本 300 只" in out, "应汇总刚跑的 s300，而非最大的 s2000"

    def test_report_only_without_sample_takes_largest(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s300", n=409)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s2000", n=2600)
        # 同上：--no-* 退回旧默认（未钉死），本测试只验「--report-only 取最大批」。
        monkeypatch.setattr(
            sys, "argv", ["m2", "--report-only", "--no-window", "--no-pin-universe"]
        )
        m2.main()
        assert "样本 2000 只" in capsys.readouterr().out

    def test_default_sample_constant(self):
        assert m2.DEFAULT_SAMPLE == 1000


class TestMemoryGuards:
    """OOM Kill 是这套回测的老问题（research/R17_infra_tooling.md「全市场 OOM」），
    而 `--jobs N` 会把内存**乘 N** ⇒ 并行必须有闸。
    被 kill 掉的方案在报表里只是少一行，比跑得慢糟得多。"""

    def test_cap_jobs_reduces_on_low_memory(self, monkeypatch, capsys):
        monkeypatch.setattr(m2.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(m2, "_avail_mem_mb", lambda: 3_000.0)
        monkeypatch.setattr(m2, "MEM_PER_JOB_MB", 1200)
        assert m2._cap_jobs(8, 20) == 2  # 3000*0.8/1200 = 2
        assert "降到 2" in capsys.readouterr().out

    def test_cap_jobs_bounded_by_cpu_count(self, monkeypatch, capsys):
        """99% 时间在逐 bar 评估（纯 CPU-bound）⇒ 进程数超过核数只会互相抢时间片，
        还会挤掉 TdxW（它要服务 xdxr 权息请求）。"""
        monkeypatch.setattr(m2.os, "cpu_count", lambda: 4)
        monkeypatch.setattr(m2, "_avail_mem_mb", lambda: 64_000.0)
        assert m2._cap_jobs(12, 20) == 4
        assert "超过 CPU 核数 4" in capsys.readouterr().out

    def test_cap_jobs_keeps_when_memory_ample(self, monkeypatch):
        # CPU 核数也要钉：CI runner 只有 4 核，_cap_jobs 先按核数收敛，
        # 不钉会在「内存充足」用例里拿到 4 而非 6。
        monkeypatch.setattr(m2.os, "cpu_count", lambda: 64)
        monkeypatch.setattr(m2, "_avail_mem_mb", lambda: 64_000.0)
        assert m2._cap_jobs(6, 20) == 6

    def test_cap_jobs_never_below_one(self, monkeypatch):
        monkeypatch.setattr(m2, "_avail_mem_mb", lambda: 100.0)
        assert m2._cap_jobs(8, 20) == 1

    def test_cap_jobs_bounded_by_task_count(self, monkeypatch):
        monkeypatch.setattr(m2, "_avail_mem_mb", lambda: 64_000.0)
        assert m2._cap_jobs(8, 3) == 3

    def test_cap_jobs_warns_when_memory_unknown(self, monkeypatch, capsys):
        monkeypatch.setattr(m2, "_avail_mem_mb", lambda: None)
        assert m2._cap_jobs(4, 20) == 4
        assert "读不到可用内存" in capsys.readouterr().out

    def test_serial_skips_memory_check(self, monkeypatch):
        monkeypatch.setattr(
            m2, "_avail_mem_mb", lambda: pytest.fail("串行不该去探内存")
        )
        assert m2._cap_jobs(1, 20) == 1

    def test_avail_mem_returns_positive_or_none(self):
        v = m2._avail_mem_mb()
        assert v is None or v > 0

    def test_is_heavy_only_for_collect_all(self):
        """--top-n 走 collect_all：`i += step` 而非跳到出场后 ⇒ 逐笔高一个量级。"""
        assert m2._is_heavy("C_portfolio", "pf_top2_c2_amv") is True
        assert m2._is_heavy("C_portfolio", "pf_c5_p20") is False
        assert m2._is_heavy("A_stop_low", "trail_08") is False
        assert m2._is_heavy("不存在的组", "x") is False


class TestPinnedWindowAndUniverse:
    """扫描期间宇宙与 K 线窗口会漂移（实测：universe 5535→5536、同参数笔数
    1106/1092/1087），必须能钉死。

    ⚠️ **只给 `--end` 钉不住**：`get_ohlcv_table`(local_tdx_data:674) 先做
    `df.tail(count)`，`_load_bars_local` 才在之后按 start/end 过滤 ⇒ 文件从 N 根变 N+1 根
    时 tail(500) 取 [N-499, N+1]，end 过滤掉最新那根 ⇒ 只剩 499 根，**且最早那根往前挪了
    一天**：窗口既缩水又滑动。两端都给 + 放大 count 才由日期决定窗口。
    """

    def test_window_sets_both_bounds_and_big_count(self):
        a = m2._base_args(1000, False, "tdx", ("2024-08-01", "2026-08-05"))
        assert a[a.index("--start") + 1] == "2024-08-01"
        assert a[a.index("--end") + 1] == "2026-08-05"
        assert int(a[a.index("--count") + 1]) == m2.WINDOW_COUNT
        assert m2.WINDOW_COUNT > 500, (
            "count 必须大于窗口内 K 线根数，否则 tail 又来定窗口"
        )

    def test_no_window_keeps_default_count(self):
        a = m2._base_args(1000, False, "tdx")
        assert "--start" not in a and a[a.index("--count") + 1] == "500"

    def test_codes_file_replaces_universe_sampling(self):
        """钉宇宙时不能再传 --universe-sample，否则又去抽一次（池子可能已变）。"""
        a = m2._base_args(1000, False, "tdx", None, "/tmp/u.txt")
        assert a[a.index("--codes-file") + 1] == "/tmp/u.txt"
        assert "--universe-local" not in a and "--universe-sample" not in a

    def test_fingerprint_separates_pinned_batches(self):
        """钉过的批次与没钉的不是同一件事（前者可复现）⇒ 不能混着汇总。"""
        plain = m2._fingerprint(1000, False)
        win = m2._fingerprint(1000, False, "tdx", ("2024-08-01", "2026-08-05"))
        both = m2._fingerprint(1000, False, "tdx", ("2024-08-01", "2026-08-05"), True)
        assert plain == "s1000"
        assert win == "s1000_w20240801-20260805"
        assert both == "s1000_w20240801-20260805_u"
        assert len({plain, win, both}) == 3

    def test_collect_isolates_pinned_from_unpinned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000", n=1106)
        _write(
            tmp_path, "A_stop_low", "trail_08", fp="s1000_w20240801-20260805_u", n=1150
        )
        assert [r["name"] for r in m2._collect(cross=False)["A_stop_low"]] == [
            "00_baseline"
        ]
        got = m2._collect(
            cross=False, window=("2024-08-01", "2026-08-05"), pin_universe=True
        )["A_stop_low"]
        assert [r["name"] for r in got] == ["trail_08"]

    def test_report_warns_when_not_reproducible(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000")
        m2.report(cross=False)
        out = capsys.readouterr().out
        assert "本批不可复现" in out and "5535→5536" in out

    def test_report_no_warning_when_pinned(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000_w20240801-20260805_u")
        m2.report(cross=False, window=("2024-08-01", "2026-08-05"), pin_universe=True)
        out = capsys.readouterr().out
        assert "本批不可复现" not in out
        assert "已钉死" in out

    def test_prepare_universe_reuses_existing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        fp = m2._fingerprint(1000, False, "tdx", None, True)
        (tmp_path / f"_universe__{fp}.txt").write_text(
            "600000\n000001\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            m2.subprocess, "run", lambda *a, **k: pytest.fail("已有代码表不该再跑一次")
        )
        got = m2._prepare_universe(1000, False, "tdx", None)
        assert got and "2 只" in capsys.readouterr().out

    def test_prepare_universe_degrades_on_failure(self, tmp_path, monkeypatch, capsys):
        """落盘失败不能中断扫描——退回各自抽样（可能漂移）并明确告警。"""
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        monkeypatch.setattr(
            m2.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1})()
        )
        assert m2._prepare_universe(1000, False, "tdx", None) is None
        assert "本轮不钉宇宙" in capsys.readouterr().out

    def test_window_conflicts_with_cross_window(self, monkeypatch, capsys):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "m2",
                "--report-only",
                "--cross-window",
                "--window",
                "2024-01-01",
                "2025-01-01",
            ],
        )
        assert m2.main() == 2
        assert "冲突" in capsys.readouterr().out

    @staticmethod
    def _capture_report(monkeypatch):
        """把 main() 尾部的 report(...) 调用参数截下来（不真出报表）。"""
        got: dict = {}
        monkeypatch.setattr(
            m2,
            "report",
            lambda cross, sample=None, data_source="tdx", window=None, pin_universe=False: (
                got.update(cross=cross, window=window, pin_universe=pin_universe)
            ),
        )
        return got

    def test_defaults_pin_window_and_universe(self, monkeypatch):
        """#17（2026-08-12 owner 拍板）：不给任何开关时，窗口钉 DEFAULT_WINDOW、宇宙钉死。"""
        monkeypatch.setattr(m2, "_prepare_universe", lambda *a, **k: "/tmp/u.txt")
        monkeypatch.setattr(m2, "_run_all", lambda *a, **k: None)
        got = self._capture_report(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["m2", "--sample", "300"])
        assert m2.main() == 0
        assert got["window"] == m2.DEFAULT_WINDOW and got["pin_universe"] is True

    def test_no_flags_restore_unpinned(self, monkeypatch):
        """--no-window/--no-pin-universe 必须能显式关回旧默认（否则反向 flag 形同虚设）。"""
        got = self._capture_report(monkeypatch)
        monkeypatch.setattr(
            sys, "argv", ["m2", "--report-only", "--no-window", "--no-pin-universe"]
        )
        assert m2.main() == 0
        assert got["window"] is None and got["pin_universe"] is False

    def test_cross_window_overrides_default_window(self, monkeypatch):
        """--cross-window 自带 2022-2024 窗口 ⇒ 默认钉死让位，**不报错**；
        只有显式 --window 与 --cross-window 同给才冲突（上方用例）。"""
        got = self._capture_report(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["m2", "--report-only", "--cross-window"])
        assert m2.main() == 0
        assert got["cross"] is True and got["window"] is None

    def test_no_window_conflicts_with_window(self, monkeypatch, capsys):
        """--window 与 --no-window 同给 = 一个钉一个拆，fail-closed 报错而非猜。"""
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "m2",
                "--report-only",
                "--window",
                "2024-01-01",
                "2025-01-01",
                "--no-window",
            ],
        )
        assert m2.main() == 2
        assert "冲突" in capsys.readouterr().out


class TestDataSourceIsolation:
    """tdx（本地 vipdoc，只有当前挂牌股）与 qlib（S_DATA，含退市股、已前复权）
    是**两个不同的宇宙**，笔数与收益都不可比 ⇒ 必须进文件名指纹，
    否则重演「混批」事故（见 TestSampleFingerprint）。"""

    def test_fingerprint_includes_data_source(self):
        assert m2._fingerprint(1000, False) == "s1000"  # tdx 不加后缀
        assert m2._fingerprint(1000, False, "tdx") == "s1000"
        assert m2._fingerprint(1000, False, "qlib") == "s1000_qlib"
        assert m2._fingerprint(1000, True, "qlib") == "s1000_qlib_cw"
        assert m2._fingerprint(1000, False, "csv") == "s1000_csv"

    def test_collect_separates_data_sources(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000", n=1294)
        _write(tmp_path, "A_stop_low", "trail_08", fp="s1000_qlib", n=1600)
        assert [r["name"] for r in m2._collect(cross=False)["A_stop_low"]] == [
            "00_baseline"
        ]
        assert [
            r["name"]
            for r in m2._collect(cross=False, data_source="qlib")["A_stop_low"]
        ] == ["trail_08"]

    def test_qlib_cross_window_separate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000_qlib", n=1600)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000_qlib_cw", n=900)
        assert len(m2._collect(cross=False, data_source="qlib")["A_stop_low"]) == 1
        assert len(m2._collect(cross=True, data_source="qlib")["A_stop_low"]) == 1

    def test_base_args_tdx_uses_local_vipdoc(self):
        a = m2._base_args(1000, False, "tdx")
        assert "--universe-local" in a and "--data-source" not in a

    def test_base_args_qlib_uses_sdata_universe(self):
        """qlib 必须配 --universe-sdata：那才是含退市股的 point-in-time 宇宙，
        用 --universe-local 会退回通达信目录 ⇒ 幸存者偏差照旧。"""
        a = m2._base_args(1000, False, "qlib")
        assert "--universe-sdata" in a and "--universe-local" not in a
        assert a[a.index("--data-source") + 1] == "qlib"

    def test_report_header_states_data_source(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m2, "OUTDIR", tmp_path)
        _write(tmp_path, "A_stop_low", "00_baseline", fp="s1000_qlib", n=1600)
        m2.report(cross=False, data_source="qlib")
        out = capsys.readouterr().out
        assert "数据源 qlib" in out and "含退市股" in out
