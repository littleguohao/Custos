# -*- coding: utf-8 -*-
"""R43 regime_style_study 钉测 —— 全合成数据（段切分/停牌/五分位与 t 手算/
判据映射/伪段/板块层/护栏）。零假设两连测 + 哨兵登记在 test_null_calibration.py。"""

from __future__ import annotations

import argparse
import math
import struct

import numpy as np
import pandas as pd
import pytest

from custos.research import regime_style_study as rs


# ---------------------------------------------------------------------------
# 合成件
# ---------------------------------------------------------------------------


def _blocks_days(blocks, start):
    """[('空头',10),...] 块清单 + 起点 → (逐日清单, 逐日状态清单)。

    块间不共享日期（后一块从前一块最后一日的**次一工作日**起）——regime dict
    的翻转日属于新状态（段起点=状态翻转当日）。
    """
    days, states = [], []
    cur = pd.Timestamp(start)
    for state, n in blocks:
        ds = pd.bdate_range(cur, periods=n)
        days += [d.strftime("%Y-%m-%d") for d in ds]
        states += [state] * n
        cur = ds[-1] + pd.Timedelta(days=1)
    return days, states


def _regime_of(days, states):
    return dict(zip(days, states))


def _mk_df(dates, closes, amounts=None, vols=None):
    n = len(dates)
    return pd.DataFrame(
        {
            "date": dates,
            "close": [float(c) for c in closes],
            "amount": amounts if amounts is not None else [2e7] * n,
            "volume": vols if vols is not None else [3e6] * n,
        }
    )


def _args(**kw):
    base = dict(
        min_stocks=6,
        min_group=1,
        min_segs=3,
        min_seg_days=5,
        susp_bar_frac=0.8,
        n_random=10,
        c4_seed=20261014,
        c4_min_pool=5,
        focus_dim="price",
        focus_dir="bear",
        min_sectors=2,
        codes="",
        codes_file="",
        universe_sample=0,
        universe_local=False,
        seed=0,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _ctx_of(stocks, blocks_by_date, susp_bar_frac=0.8):
    """手工 ctx：stocks={code:{dates,close,board}}, blocks_by_date={date:(codes,X)}。"""
    return {
        "stocks": {
            c: {
                "dates": s["dates"],
                "close": np.asarray(s["close"], dtype="float64"),
                "ret1": np.full(len(s["dates"]), np.nan),
                "board": s.get("board", "主板"),
            }
            for c, s in stocks.items()
        },
        "blocks": blocks_by_date,
        "vipdoc_dates": None,
        "susp_bar_frac": susp_bar_frac,
        "drops": {"no_start_bar": 0, "low_bars": 0},
        "mkt_ret": {},
    }


def _hand_ctx(values_by_code, rets_by_code, *, dim="ret20", n_pre=65, seg_len=5):
    """10 票手算夹具：n_pre 根历史 + seg_len 根段内 bar；特征只在 dim 列有值。

    返回 (ctx, seg)。rets_by_code = 段收益（起点收盘→段终点收盘）。
    """
    codes = sorted(values_by_code)
    n = n_pre + seg_len
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2023-01-02", periods=n)]
    seg_days = days[n_pre:]
    seg = {
        "dir": "bear",
        "start": seg_days[0],
        "end": seg_days[-1],
        "days": seg_days,
        "n_days": seg_len,
    }
    stocks = {}
    for c in codes:
        closes = (
            [10.0] * n_pre + [100.0] * (seg_len - 1) + [100.0 * (1 + rets_by_code[c])]
        )
        stocks[c] = {"dates": days, "close": closes}
    X = np.full((len(codes), len(rs.MATRIX_DIMS)), np.nan, dtype=np.float32)
    col = rs.MATRIX_DIMS.index(dim)
    for i, c in enumerate(codes):
        X[i, col] = np.float32(values_by_code[c])
    blocks = {seg["start"]: (np.array(codes), X)}
    return _ctx_of(stocks, blocks), seg


# ---------------------------------------------------------------------------
# 段切分
# ---------------------------------------------------------------------------


class TestSegmentSplit:
    def test_flip_boundaries_and_first_segment_dropped(self):
        days, states = _blocks_days(
            [("中性", 5), ("空头", 10), ("做多", 10), ("中性", 3), ("空头", 8)],
            "2024-01-01",
        )
        segs = rs.split_regime_segments(_regime_of(days, states))
        # 首段（中性）丢弃；中性段不进方向段集
        assert [(s["dir"], s["n_days"]) for s in segs] == [
            ("bear", 10),
            ("bull", 10),
            ("bear", 8),
        ]
        b0 = segs[0]
        assert b0["start"] == days[5]  # 段起点 = 状态翻转当日
        assert b0["end"] == days[14]  # 段终点 = 下一次翻转前一交易日
        assert segs[1]["start"] == days[15]

    def test_assign_windows_short_and_crossing_dropped(self):
        # 长段在 A（2016 内）、短段在 A、跨 A/判定段边界的段、判定段内的段；
        # 同状态块之间插中性块分隔（否则 splitter 会把它们并成一段）
        specs = [
            ("中性", 5, "2016-05-25"),
            ("空头", 10, "2016-06-01"),  # 完整落 A ⇒ 保留
            ("中性", 3, "2016-06-20"),
            ("空头", 3, "2016-07-01"),  # < 5 日 ⇒ short
            ("中性", 3, "2016-07-08"),
            ("空头", 10, "2016-12-26"),  # 跨 A/判定边界（终点 2017-01）⇒ crossing
            ("中性", 5, "2017-01-16"),
            ("空头", 10, "2018-03-01"),  # 判定段内 ⇒ 不读（计入 crossing）
        ]
        days, states = [], []
        for state, n, start in specs:
            ds = pd.bdate_range(start, periods=n)
            days += [d.strftime("%Y-%m-%d") for d in ds]
            states += [state] * n
        segs = rs.split_regime_segments(_regime_of(days, states))
        kept, drops = rs.assign_windows(segs)
        assert [s["start"] for s in kept["A"]["bear"]] == ["2016-06-01"]
        assert kept["B"]["bear"] == [] and kept["A"]["bull"] == []
        assert drops["short"] == 1
        assert drops["crossing"] == 2  # 跨界段 + 判定段内段（不读）

    def test_empty_regime(self):
        assert rs.split_regime_segments({}) == []


# ---------------------------------------------------------------------------
# 段评估：停牌过滤 / 五分位 / ma_bull / board / 负值组（手算）
# ---------------------------------------------------------------------------


class TestEvalSegment:
    def test_quintile_spread_hand_calc(self):
        values = {f"c{i}": float(i + 1) for i in range(10)}  # 1..10 升序
        rets = {
            f"c{i}": r
            for i, r in enumerate(
                [-0.10, -0.08, -0.02, 0.0, 0.0, 0.02, 0.04, 0.06, 0.10, 0.12]
            )
        }
        ctx, seg = _hand_ctx(values, rets)
        out = rs.eval_segment(seg, ctx, min_stocks=10, min_group=2)
        assert out is not None and out["n_members"] == 10
        # mean(ret)=0.014；Q5=(0.10,0.12)→超额均值 0.096；Q1=(−0.10,−0.08)→−0.104
        assert abs(out["spreads"]["ret20"] - 0.2) < 1e-9
        assert out["spreads"]["price"] is None  # 其它维全 NaN ⇒ 记缺

    def test_suspension_rules(self):
        codes = [f"c{i}" for i in range(10)]
        values = {c: float(i) for i, c in enumerate(codes)}
        rets = {c: 0.01 for c in codes}
        ctx, seg = _hand_ctx(values, rets)
        days = ctx["stocks"]["c0"]["dates"]
        seg_days = seg["days"]
        # c0：段内只有 3/5 根 bar（< 80%×5=4）⇒ low_bars 剔除
        ctx["stocks"]["c0"]["dates"] = [d for d in days if d not in seg_days[1:3]]
        # c1：段起点无 bar（特征行只建在当日有 bar 的票上——从 block 同步移除）
        ctx["stocks"]["c1"]["dates"] = [d for d in days if d != seg_days[0]]
        codes_arr, X = ctx["blocks"][seg["start"]]
        keep = codes_arr != "c1"
        ctx["blocks"][seg["start"]] = (codes_arr[keep], X[keep])
        out = rs.eval_segment(seg, ctx, min_stocks=5, min_group=1)
        assert out["n_members"] == 8  # c0/c1 剔除
        assert ctx["drops"]["low_bars"] == 1
        assert ctx["drops"]["no_start_bar"] == 1

    def test_seg_end_no_bar_settles_at_last_inseg_close(self):
        # c2（特征值最大 ⇒ Q5）段终点无 bar：最后一根在段内第 4 日（4/5 ≥ 80%
        # 保留），按段内最后收盘结算（0.05 而非段末口径）+ 停牌比例 0.2
        codes = [f"c{i}" for i in range(5)]
        values = {"c0": 0.0, "c1": 1.0, "c2": 9.0, "c3": 3.0, "c4": 4.0}
        rets = {c: 0.0 for c in codes}
        ctx, seg = _hand_ctx(values, rets, n_pre=65, seg_len=5)
        seg_days = seg["days"]
        st = ctx["stocks"]["c2"]
        closes = list(st["close"])
        i_s = st["dates"].index(seg_days[0])
        closes[i_s + 3] = 105.0  # 段内第 4 日收盘 105（起点 100）
        st["close"] = closes
        st["dates"] = [d for d in st["dates"] if d != seg_days[-1]]  # 终点日无 bar
        out = rs.eval_segment(seg, ctx, min_stocks=5, min_group=1)
        assert out["n_members"] == 5
        # mean = 0.05/5 = 0.01；Q5 只含 c2 ⇒ spread = (0.05−0.01) − (0−0.01) = 0.05
        assert abs(out["spreads"]["ret20"] - 0.05) < 1e-9
        assert out["susp_by_group"]["ret20"]["Q5"] == pytest.approx(0.2)

    def test_ma_bull_two_groups(self):
        codes = [f"c{i}" for i in range(6)]
        values = {"c0": 1.0, "c1": 1.0, "c2": 1.0, "c3": 0.0, "c4": 0.0, "c5": 0.0}
        rets = {c: (0.05 if values[c] > 0.5 else -0.05) for c in codes}
        ctx, seg = _hand_ctx(values, rets, dim="ma_bull")
        out = rs.eval_segment(seg, ctx, min_stocks=6, min_group=3)
        # 均值为 0 ⇒ 组均超额 ±0.05 ⇒ spread = 0.10
        assert abs(out["spreads"]["ma_bull"] - 0.10) < 1e-9

    def test_board_max_min(self):
        codes = [f"c{i}" for i in range(6)]
        values = {c: 1.0 for c in codes}  # 无关维
        rets = {"c0": 0.10, "c1": 0.10, "c2": 0.0, "c3": 0.0, "c4": -0.10, "c5": -0.10}
        ctx, seg = _hand_ctx(values, rets, dim="ret5")
        ctx["stocks"]["c0"]["board"] = "创业板"
        ctx["stocks"]["c1"]["board"] = "创业板"
        ctx["stocks"]["c4"]["board"] = "北交所"
        ctx["stocks"]["c5"]["board"] = "北交所"
        out = rs.eval_segment(seg, ctx, min_stocks=6, min_group=2)
        # mean=0；创业板 0.10、主板 0.0、北交所 −0.10 ⇒ max−min = 0.20
        assert abs(out["spreads"]["board"] - 0.20) < 1e-9

    def test_negative_pe_excluded_and_reported(self):
        codes = [f"c{i}" for i in range(10)]
        values = {f"c{i}": (-1.0 if i < 4 else float(i)) for i in range(10)}
        rets = {c: (-0.05 if values[c] < 0 else 0.01) for c in codes}
        ctx, seg = _hand_ctx(values, rets, dim="pe_ttm")
        out = rs.eval_segment(seg, ctx, min_stocks=6, min_group=1)
        assert out["neg_groups"]["pe_ttm"]["n"] == 4  # 负值组单列
        # mean = (4×−0.05 + 6×0.01)/10 = −0.014 ⇒ 负值组均超额 = −0.05−(−0.014)
        assert out["neg_groups"]["pe_ttm"]["mean_excess"] == pytest.approx(-0.036)
        # 价差只在非负 6 票上做（≥min_stocks=6 恰过线）
        assert out["spreads"]["pe_ttm"] is not None

    def test_too_few_members_returns_none(self):
        values = {f"c{i}": float(i) for i in range(4)}
        rets = {c: 0.01 for c in values}
        ctx, seg = _hand_ctx(values, rets)
        assert rs.eval_segment(seg, ctx, min_stocks=10, min_group=1) is None


class TestTStat:
    def test_hand_calc(self):
        # mean=0.2, std=0.1, n=3 ⇒ t = 0.2/(0.1/√3)
        assert abs(rs._t_stat([0.1, 0.2, 0.3]) - 0.2 * math.sqrt(3) / 0.1) < 1e-9
        assert rs._t_stat([0.1]) is None  # n<2 记缺
        assert rs._t_stat([0.2, 0.2, 0.2]) is None  # std==0 记缺


# ---------------------------------------------------------------------------
# 伪段 / 板块层 / vipdoc 读取件
# ---------------------------------------------------------------------------


class TestPseudoPlacement:
    def test_deterministic_and_in_window(self):
        days = [
            d.strftime("%Y-%m-%d") for d in pd.bdate_range("2022-01-03", periods=60)
        ]
        real = [
            {
                "dir": "bear",
                "n_days": 8,
                "start": days[0],
                "end": days[7],
                "days": days[:8],
            }
        ] * 3
        p1 = rs.draw_pseudo_placements(real, days, n_draws=5, seed=42)
        p2 = rs.draw_pseudo_placements(real, days, n_draws=5, seed=42)
        assert p1 == p2  # 确定性
        for draw in p1["draws"]:
            assert len(draw) == 3  # 每次抽样放置数 = 真实段数
            for s in draw:
                assert s["n_days"] == 8
                assert (
                    s["days"][0] >= days[0] and s["days"][-1] <= days[-1]
                )  # 完整落窗内

    def test_dropped_when_too_long(self):
        days = [
            d.strftime("%Y-%m-%d") for d in pd.bdate_range("2022-01-03", periods=10)
        ]
        real = [
            {
                "dir": "bear",
                "n_days": 50,
                "start": days[0],
                "end": days[-1],
                "days": days,
            }
        ]
        p = rs.draw_pseudo_placements(real, days, n_draws=4, seed=1)
        assert p["n_dropped"] == 4  # 段长 > 窗长，100 次重试后丢弃并记数
        assert all(draw == [] for draw in p["draws"])


class TestSectorLayer:
    def _sector(self):
        days = [
            d.strftime("%Y-%m-%d") for d in pd.bdate_range("2023-01-02", periods=10)
        ]
        seg_start = "2023-01-06"
        return {
            "880001": {
                "dates": days,
                "close": [100, 100, 100, 100, 100, 110, 110, 110, 110, 110],
            },
            "880002": {
                "dates": days,
                "close": [100, 100, 100, 100, 100, 90, 90, 90, 90, 90],
            },
            # 880003 段起点（2023-01-06）无 bar ⇒ 当段不计
            "880003": {
                "dates": [d for d in days if d != seg_start],
                "close": [100, 100, 100, 100, 100, 100, 100, 100, 100],
            },
        }

    def test_eval_segment_sector(self):
        sector = self._sector()
        seg = {
            "start": "2023-01-06",
            "end": "2023-01-13",
            "days": [
                "2023-01-06",
                "2023-01-09",
                "2023-01-10",
                "2023-01-11",
                "2023-01-12",
                "2023-01-13",
            ],
            "n_days": 6,
        }
        out = rs.eval_segment_sector(seg, sector, min_sectors=2)
        assert out is not None and out["n_sectors"] == 2
        # ret: 880001=110/100−1=0.10, 880002=−0.10；等权 0 ⇒ 超额 ±0.10
        assert abs(out["excess"]["880001"] - 0.10) < 1e-9
        assert abs(out["excess"]["880002"] + 0.10) < 1e-9

    def test_c3_spearman(self):
        a = {"excess": {"x": 0.3, "y": 0.1, "z": -0.2}, "n_sectors": 3}
        b = {"excess": {"x": 0.2, "y": 0.05, "z": -0.3}, "n_sectors": 3}  # 排名保持
        c = {"excess": {"x": -0.3, "y": 0.0, "z": 0.3}, "n_sectors": 3}  # 排名翻转
        ok = rs.judge_sector_c3({"A": [a, b], "B": [a, b]})
        assert ok is True
        bad = rs.judge_sector_c3({"A": [a, c], "B": [a, c]})
        assert bad is False
        none = rs.judge_sector_c3({"A": [a], "B": [a, b]})  # A 窗无相邻对
        assert none is None

    def test_type_group_mapping(self):
        groups, n_excluded = rs.sector_type_groups(
            {
                "880100": "2",
                "881200": "12",
                "880500": "4",
                "880700": "5",
                "880300": "3",
                "880900": "",
            }
        )
        assert groups == {
            "880100": "行业",
            "881200": "行业",
            "880500": "概念",
            "880700": "风格",
            "880900": "未知",
        }
        assert n_excluded == 1  # 地区(3) 排除记数


class TestVipdocCloseReader:
    def test_reads_dates_close_and_caps_holdout(self, tmp_path):
        p = tmp_path / "sh880001.day"
        with p.open("wb") as f:
            for d in (20260903, 20260904, 20260905):  # 最后一根在 holdout
                f.write(struct.pack("<IIIIIfII", d, 100, 110, 90, 105, 1000.0, 5000, 0))
            f.write(b"\x00" * 10)  # 尾部残段
        dates, close = rs.read_vipdoc_close_series(p)
        assert dates.tolist() == [20260903, 20260904]  # holdout 帽截断
        assert close.tolist() == [1.05, 1.05]  # 分→元


# ---------------------------------------------------------------------------
# 基本面帧（PIT 可见性 + TTM/同比手算 + dy）
# ---------------------------------------------------------------------------


class TestFundamentalFrame:
    RECS = [
        {  # FY2014
            "code": "600000",
            "report_date": "2014-12-31",
            "notice_date": "2015-03-10",
            "net_profit": 100.0,
            "revenue": 1000.0,
            "roe_waa": 10.0,
            "bps": 5.0,
            "gross_margin": 30.0,
            "ocf_ps": 1.0,
        },
        {  # 2015Q1
            "code": "600000",
            "report_date": "2015-03-31",
            "notice_date": "2015-04-29",
            "net_profit": 30.0,
            "revenue": 300.0,
            "roe_waa": 2.5,
            "bps": 5.5,
            "gross_margin": 31.0,
            "ocf_ps": 0.3,
        },
        {  # 2014Q1（同比基数）
            "code": "600000",
            "report_date": "2014-03-31",
            "notice_date": "2014-04-25",
            "net_profit": 25.0,
            "revenue": 250.0,
            "roe_waa": 2.2,
            "bps": 4.8,
            "gross_margin": 29.0,
            "ocf_ps": 0.2,
        },
    ]

    def test_pit_visibility_and_ttm(self):
        dates = pd.Series(["2015-04-28", "2015-05-06"])
        close = pd.Series([10.0, 10.0])
        shares = pd.Series([1e8, 1e8])
        tables = rs._pit_tables(self.RECS, ["2015-04-28", "2015-05-06"])
        frame = rs._fundamental_frame(
            "600000",
            dates,
            close,
            shares,
            tables["600000"],
            [{"date": "2015-01-15", "fenhong": 0.5}],
        )
        # 04-28：Q1 公告（04-29）尚不可见 ⇒ rd0=FY2014；缺 2013FY ⇒ rev_yoy None
        assert math.isnan(frame["rev_yoy"].iloc[0])
        assert frame["pb"].iloc[0] == pytest.approx(10.0 / 5.0)
        # 05-06：rd0=2015Q1；np_ttm=30+100−25=105、rev_ttm=300+1000−250=1050
        assert frame["pe_ttm"].iloc[1] == pytest.approx(10.0 * 1e8 / 105.0)
        assert frame["ps_ttm"].iloc[1] == pytest.approx(10.0 * 1e8 / 1050.0)
        assert frame["rev_yoy"].iloc[1] == pytest.approx(300.0 / 250.0 - 1.0)
        assert frame["np_yoy"].iloc[1] == pytest.approx(30.0 / 25.0 - 1.0)
        assert frame["gross_margin"].iloc[1] == 31.0
        assert frame["roe_ttm"].iloc[1] == pytest.approx(2.5 + 10.0 - 2.2)
        assert frame["cfo_to_np"].iloc[1] == pytest.approx(
            (0.3 + 1.0 - 0.2) * 1e8 / 105.0
        )
        assert frame["dy"].iloc[1] == pytest.approx(
            0.5 / 10.0
        )  # 12 个月内 fenhong/收盘
        assert math.isnan(frame["debt_ratio"].iloc[1])  # PIT 库无字段 ⇒ 恒记缺

    def test_missing_pit_all_nan(self):
        frame = rs._fundamental_frame(
            "600000",
            pd.Series(["2015-05-06"]),
            pd.Series([10.0]),
            pd.Series([1e8]),
            None,
            None,
        )
        assert frame[rs.FUND_FAMILY[0]].isna().all()


# ---------------------------------------------------------------------------
# 判据映射（judge_style_dim 直接驱动）
# ---------------------------------------------------------------------------


def _seg_result(spread):
    return (
        {"n_days": 10},
        {
            "spreads": {"price": spread},
            "neg_groups": {},
            "susp_by_group": {},
            "n_members": 200,
            "frac_pos": 0.5,
            "early": {},
        },
    )


class TestJudges:
    def test_c1_too_few_segments_untested(self):
        per_win = {"A": [_seg_result(0.01)] * 3, "B": [_seg_result(0.01)] * 3}
        jd = rs.judge_style_dim(
            "price", per_win, {"A": [0.1] * 25, "B": [0.1] * 25}, min_segs=8
        )
        assert jd["c1_ok"] is False and jd["verdict"] == "untested"

    def test_c4_one_window_pass_other_fail_is_provisional(self):
        # A 窗池低（过）、B 窗池高（不过）⇒ 修订注记 2 ⇒ C4 provisional ⇒ 结局 provisional
        per_win = {
            "A": [_seg_result(0.10 + i * 0.001) for i in range(9)],
            "B": [_seg_result(0.10 + i * 0.001) for i in range(9)],
        }
        jd = rs.judge_style_dim(
            "price",
            per_win,
            {"A": [0.01] * 25, "B": [1e6] * 25},
            min_segs=8,
            c4_min_pool=20,
        )
        assert jd["c1_ok"] and jd["c2_ok"] is True
        assert jd["c4_by_window"] == {"A": "confirmed_pass", "B": "confirmed_fail"}
        assert jd["c4_state"] == "provisional"
        assert jd["verdict"] == "provisional"

    def test_both_pass_candidate(self):
        per_win = {
            "A": [_seg_result(0.10 + i * 0.001) for i in range(9)],
            "B": [_seg_result(0.10 + i * 0.001) for i in range(9)],
        }
        jd = rs.judge_style_dim(
            "price",
            per_win,
            {"A": [0.01] * 25, "B": [0.01] * 25},
            min_segs=8,
            c4_min_pool=20,
        )
        assert jd["verdict"] == "candidate"
        assert jd["c4_state"] == "confirmed_pass"


# ---------------------------------------------------------------------------
# run_study 级：护栏 / 不登记台账 / 结构冒烟
# ---------------------------------------------------------------------------


def _mini_fixture(seed=0):
    """12 票、A/B 各 3 空头段的小夹具（供 run_study 冒烟与台账钉测）。"""
    blocks = [("中性", 5)] + [("空头", 10), ("中性", 5)] * 3
    days_a, states_a = _blocks_days(blocks, "2016-01-04")
    days_b, states_b = _blocks_days(blocks, "2022-01-03")
    regime = _regime_of(days_a + days_b, states_a + states_b)
    full = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2015-01-05", "2022-06-30")]
    rng = np.random.RandomState(seed)
    frames = {}
    for k in range(12):
        c = f"6000{k:02d}"
        r = rng.normal(0.0002, 0.01, len(full))
        frames[c] = _mk_df(full, (10 + k) * np.exp(np.cumsum(r)))
    return regime, frames


def test_run_study_smoke():
    regime, frames = _mini_fixture()
    rep = rs.run_study(
        _args(min_segs=3),
        bars_loader=lambda codes, count, start, end: frames,
        regime=regime,
        codes=sorted(frames),
        sector_bars={},
        sector_types={},
        shares_fn=lambda c: None,
        xdxr_fn=lambda c: None,
        pit_records=[],
    )
    assert rep["n_qualified_segments"] == 6
    assert rep["segment_drops"] == {"short": 0, "crossing": 0}
    assert rep["criteria"]["C4"]["state"] in kit_states()
    assert rep["style"]["bear"]["dims"]["debt_ratio"]["verdict"] == "untested"
    assert rep["provenance"]["unit"] == "R43"
    assert "cost_sensitivity 不适用" in rep["notes"][0]


def kit_states():
    return ("confirmed_pass", "confirmed_fail", "provisional", "indeterminate")


def test_empty_guard_returns_2_and_no_write(tmp_path):
    out = tmp_path / "r43.json"
    rc = rs.main(
        ["--out", str(out)],
        regime={},  # 0 段
        bars_loader=lambda codes, count, start, end: {},
        codes=["600000"],
        sector_bars={},
        sector_types={},
        shares_fn=lambda c: None,
        xdxr_fn=lambda c: None,
        pit_records=[],
    )
    assert rc == 2 and not out.exists()


def test_no_window_usage_registration(monkeypatch):
    """生产路径不读判定段 ⇒ 本工具不登记 window_usage（钉死）。"""
    from custos.research import window_usage

    calls = []
    monkeypatch.setattr(
        window_usage, "record_use", lambda *a, **k: calls.append((a, k))
    )
    regime, frames = _mini_fixture()
    rs.run_study(
        _args(min_segs=3),
        bars_loader=lambda codes, count, start, end: frames,
        regime=regime,
        codes=sorted(frames),
        sector_bars={},
        sector_types={},
        shares_fn=lambda c: None,
        xdxr_fn=lambda c: None,
        pit_records=[],
    )
    assert calls == []


# ---------------------------------------------------------------------------
# 零假设校准夹具（test_null_calibration.py 复用——两窗各 9 空头 + 9 多头段）
# ---------------------------------------------------------------------------


def _calib_args(**kw):
    base = dict(
        min_stocks=20,
        min_group=4,
        min_segs=8,  # 与生产口径一致（9 段/窗/方向）
        min_seg_days=5,
        susp_bar_frac=0.8,
        n_random=25,  # 主用例守 25/20 契约
        c4_seed=20261014,
        c4_min_pool=20,
        focus_dim="age",
        focus_dir="bear",
        min_sectors=2,
        codes="",
        codes_file="",
        universe_sample=0,
        universe_local=False,
        seed=0,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _calib_fixture(seed: int, edge: bool):
    """40 票（4 板块组 ×10，上市日按 10 个交易日 stagger ⇒ **age 排序永不漂移**）、
    两窗各 9 空头 + 9 多头段（段长 10，中性间隔 5）。

    edge=True：空头段内**高龄半边**（k<20）+0.35%/日、低龄半边 −0.35%/日——
    regime 专属的 age 维价差（age 是静态序，不像价格水平会被收益积分打乱；
    C4 伪段随机放置大多落在非空头日 ⇒ 应 confirmed_pass）。
    """
    blocks = [("中性", 5)] + [("空头", 10), ("中性", 5), ("做多", 10), ("中性", 5)] * 9
    days_a, states_a = _blocks_days(blocks, "2015-06-01")
    days_b, states_b = _blocks_days(blocks, "2022-01-03")
    regime = _regime_of(days_a + days_b, states_a + states_b)
    full = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2012-08-27", "2023-03-31")]
    bear_days = {d for d, s in regime.items() if s == "空头"}
    prefixes = ["600", "000", "300", "920"]
    frames = {}
    rng = np.random.RandomState(seed)
    for k in range(40):
        c = f"{prefixes[k % 4]}{100 + k:03d}"
        days_k = full[10 * k :]  # stagger 上市：age 截面序 = k 序（恒定）
        r = rng.normal(0.0002, 0.008, len(days_k))
        if edge:
            bump = 0.0035 if k < 20 else -0.0035  # 高龄组/低龄组
            r = np.array(
                [ri + (bump if d in bear_days else 0.0) for d, ri in zip(days_k, r)]
            )
        close = (5.0 + k) * np.exp(np.cumsum(r))
        frames[c] = _mk_df(days_k, close)
    return regime, frames


def _calib_run(seed: int, edge: bool, **inj):
    regime, frames = _calib_fixture(seed, edge)
    kw = dict(
        bars_loader=lambda codes, count, start, end: frames,
        regime=regime,
        codes=sorted(frames),
        sector_bars={},
        sector_types={},
        # 默认股本/分红/PIT 读取走真实 xdxr 缓存（未命中会写墓碑文件）——
        # 测试一律注入空实现（conftest 的 CACHE_DIR 重定向是兜底）。
        shares_fn=lambda c: None,
        xdxr_fn=lambda c: None,
        pit_records=[],
    )
    kw.update(inj)
    return rs.run_study(_calib_args(), **kw)
