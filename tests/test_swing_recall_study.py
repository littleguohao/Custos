# -*- coding: utf-8 -*-
"""R50 swing_recall_study（v0.456，TODO #119）：ZigZag 波段机钉测（除权/停牌/边界）、
as-of 纪律钉测（门判定不读未来）、B1 漏斗口径、端到端合成世界、只读挖掘段纪律、
空结果护栏。零假设校准两连测 + 逐笔置换哨兵在 tests/test_null_calibration.py。"""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

import helpers_null as hn
from custos.research import swing_recall_study as sr

SPANS = {
    "mining_a": ("2000-01-01", "2016-12-31"),
    "mining_b": ("2022-01-01", "2026-09-04"),
}
#: 合成段的实际 bar 覆盖（段常量的子区间——T0 自然落段内；值=起始日）
SEG_BARS = {
    "mining_a": "2011-01-03",
    "mining_b": "2022-06-01",
}


def _u(*key) -> float:
    return (
        int.from_bytes(
            hashlib.md5("|".join(map(str, key)).encode()).digest()[:4], "little"
        )
        / 2**32
    )


def _days(n, start="2010-01-04"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


# ---------------------------------------------------------------------------
# ZigZag 波段机（纯函数钉测）
# ---------------------------------------------------------------------------


def _vpath(warm=300, cycles=1):
    """预热平台 + 逐周期「跌 30 根 → +31% 拉 25 根 → −11% 回 12 根 → 阴跌 43 根」。"""
    px = [10.0] * warm
    p = 10.0
    for _ in range(cycles):
        for _ in range(30):
            p *= 0.994
            px.append(p)
        for _ in range(25):
            p *= 1.011
            px.append(p)
        for _ in range(12):
            p *= 0.990
            px.append(p)
        for _ in range(43):
            p *= 0.998
            px.append(p)
    return px


class TestZigzag:
    def test_basic_swing(self):
        px = _vpath(cycles=1)
        sw = sr.zigzag(px)
        assert len(sw) == 1
        t0, t1 = sw[0]["t0"], sw[0]["t1"]
        assert px[t0] == min(px)  # T0 = 全局最低
        assert px[t1] == max(px)  # T1 = 波段内最高收盘（回撤确认前的峰）
        assert sw[0]["truncated"] is False
        assert any(c >= px[t0] * 1.2 for c in px[t0 + 1 : t1 + 1])  # +20% 成立过

    def test_rise_below_20pct_no_swing(self):
        px = [10.0] * 300
        p = 10.0
        for _ in range(20):
            p *= 0.995
            px.append(p)  # 跌到 ~9.05
        for _ in range(10):
            p *= 1.018
            px.append(p)  # 只弹 +19.6%（<20% ⇒ 不成立）
        for _ in range(15):
            p *= 0.99
            px.append(p)
        assert sr.zigzag(px) == []

    def test_shallow_pullback_does_not_confirm(self):
        # 回撤 8%（<10%）后继续新高——T1 应取到后面的峰
        px = [10.0] * 300
        p = 10.0
        for _ in range(20):
            p *= 0.995
            px.append(p)
        low = p
        for _ in range(15):
            p *= 1.02
            px.append(p)  # +34%
        peak1 = p
        for _ in range(8):
            p *= 0.99
            px.append(p)  # −7.7%（不足 −10%，不确认）
        for _ in range(8):
            p *= 1.015
            px.append(p)  # 新高
        peak2 = max(px)
        for _ in range(12):
            p *= 0.988
            px.append(p)  # −13.4% 确认
        sw = sr.zigzag(px)
        assert len(sw) == 1
        assert px[sw[0]["t0"]] == low
        assert px[sw[0]["t1"]] == peak2 > peak1

    def test_truncated_at_data_end(self):
        px = [10.0] * 300
        p = 10.0
        for _ in range(20):
            p *= 0.995
            px.append(p)
        for _ in range(15):
            p *= 1.02
            px.append(p)  # 成立且未确认即数据结束
        sw = sr.zigzag(px)
        assert len(sw) == 1 and sw[0]["truncated"] is True
        assert sw[0]["t1"] == len(px) - 1

    def test_consecutive_swings(self):
        px = _vpath(cycles=3)
        sw = sr.zigzag(px)
        assert len(sw) == 3
        for a, b in zip(sw, sw[1:]):
            assert b["t0"] > a["t1"]  # 下一个波段从 T1 之后重新找低点


class TestSwingFilter:
    def _identify(self, px, days=None, seg=("2010-01-01", "2011-12-31"), **kw):
        days = days or _days(len(px))
        return sr.identify_swings(days, px, seg_start=seg[0], seg_end=seg[1], **kw)

    def test_short_history_dropped(self):
        px = _vpath(warm=100, cycles=1)  # T0 前只有 ~130 根 < 250
        kept, dr = self._identify(px)
        assert kept == [] and dr["short_history"] == 1

    def test_tail_buffer_dropped(self):
        # T0 ≈ bar 330（2011-04-08）；隔离带 = seg_end − 90 自然日：
        # seg_end=2011-06-30 ⇒ deadline=2011-04-01 < T0 ⇒ 剔除
        px = _vpath(warm=300, cycles=1)
        kept, dr = self._identify(px, seg=("2010-01-01", "2011-06-30"))
        assert kept == [] and dr["tail_buffer"] == 1

    def test_out_of_segment_dropped(self):
        px = _vpath(cycles=1)
        kept, dr = self._identify(px, seg=("2010-01-01", "2010-06-30"))
        assert kept == [] and dr["out_of_segment"] == 1

    def test_suspension_gap_dropped(self):
        px = _vpath(cycles=1)
        days = _days(len(px))
        cut = 340  # 波段拉升段中间
        days = days[:cut] + [
            (pd.Timestamp(days[cut]) + pd.Timedelta(days=25 + k)).strftime("%Y-%m-%d")
            for k in range(len(days) - cut)
        ]
        kept, dr = self._identify(px, days)
        assert kept == [] and dr["suspension"] == 1

    def test_small_gap_kept(self):
        px = _vpath(cycles=1)
        days = _days(len(px))
        cut = 340
        days = days[:cut] + [
            (pd.Timestamp(days[cut]) + pd.Timedelta(days=8 + k)).strftime("%Y-%m-%d")
            for k in range(len(days) - cut)
        ]
        kept, dr = self._identify(px, days)
        assert len(kept) == 1 and dr["suspension"] == 0

    def test_qfq_continuity_no_phantom_swing(self):
        """除权口径钉测：持有途中的 −50% 除权假跳空，未复权序列上会幻影确认 T1
        （瞬时 −50% 击穿 −10% 回撤线）并再造一个幻影波段；前复权连续序列则只有
        一个波段走到真峰（R50 注记 2 的钉住形态——写死用前复权收盘）。"""
        px = [10.0] * 300
        p = 10.0
        for _ in range(30):
            p *= 0.994
            px.append(p)  # 跌 −16.5%（T0）
        for _ in range(25):
            p *= 1.011
            px.append(p)  # +31%（波段成立，峰 1）
        for _ in range(8):
            p *= 0.995
            px.append(p)  # 浅回 −4%（不确认）
        gap_bar = len(px)  # 假跳空点（bar 363，持有途中）
        for _ in range(20):
            p *= 1.015
            px.append(p)  # 新高（真峰）
        for _ in range(12):
            p *= 0.988
            px.append(p)  # −13.4%（真确认）
        kept, _ = self._identify(px)
        assert len(kept) == 1 and kept[0]["amp"] > 0.5
        raw = px[:gap_bar] + [c * 0.5 for c in px[gap_bar:]]
        kept_raw, _ = self._identify(raw)
        assert len(kept_raw) == 2  # 幻影确认 + 幻影第二波段——口径差异真实存在
        import inspect  # noqa: PLC0415

        from custos.datasource.local_tdx import local_tdx_data  # noqa: PLC0415

        sig = inspect.signature(local_tdx_data.get_ohlcv_table)
        assert sig.parameters["adjust"].default == "qfq"  # 生产加载默认前复权

    def test_entry_deadline_single_source(self):
        from custos.research import load_window as lw

        assert lw.entry_deadline("2016-12-31") < "2016-12-31"
        px = _vpath(cycles=1)
        kept, _dr = self._identify(px, seg=("2000-01-01", "2016-12-31"))
        assert len(kept) == 1  # T0 在 2011 年，远离挖掘 A 隔离带


# ---------------------------------------------------------------------------
# as-of 纪律钉测（R50 风险注记 1：门判定不得读 T0 之后的数据）
# ---------------------------------------------------------------------------


def _rw_df(n=500, seed=7):
    """随机游走合成帧（含 weekly/monthly 预计算所需列），埋两段急跌反弹。"""
    rng = np.random.RandomState(seed)
    r = rng.normal(0.0005, 0.02, n)
    r[100:115] = -0.03
    r[115:125] = 0.025
    r[300:320] = -0.025
    px = 10 * np.exp(np.cumsum(r))
    return pd.DataFrame(
        {
            "date": _days(n, "2012-01-04"),
            "open": px * (1 + rng.normal(0, 0.003, n)),
            "high": px * 1.012,
            "low": px * 0.988,
            "close": px,
            "volume": rng.uniform(5e5, 2e6, n),
            "amount": rng.uniform(5e6, 2e7, n),
        }
    )


class TestAsofDiscipline:
    def test_qn_scan_prefix_invariant(self):
        """生产 QN 扫描是纯 as-of：截断未来 bar 后，截断点之前的命中逐位不变。"""
        df = _rw_df()
        scan = sr.default_qn_scan_fn()
        full = scan("000001", df, "2012-01-04", "2013-12-31")
        for cut in (200, 350, len(df) - 1):
            part = scan("000001", df.iloc[:cut], "2012-01-04", "2013-12-31")
            for g in sr.QN_GATES:
                assert {i for i in full[g] if i < cut} == set(part[g]), (
                    f"{g} 在 cut={cut} 处读到未来"
                )

    def test_qn_scan_slice_free_matches_sliced(self):
        """本扫描的无切片快速路径与真切片逐 bar 等价（自证，不依赖上游钉测）。"""
        from custos.research import backtest_factors as bt

        df = _rw_df()
        pre = bt._precompute_gate_series(df)
        for g in sr.QN_GATES:
            fn = bt.ENTRY_GATES[g]
            if not bt._slice_free_ok(fn, pre):
                continue
            for i in (100, 250, 400):
                assert fn(bt._PrefixLen(i + 1), pre) == fn(df.iloc[: i + 1], pre), (
                    f"{g}@{i} 快慢路径分歧"
                )

    def test_labels_use_future_but_signals_do_not(self):
        """标签侧允许读未来（写死口径）：T0 之后的价格改动 ⇒ 波段标签变、
        T0 之前的 QN 信号命中不变。"""
        df = _rw_df()
        t0_like = 320
        df2 = df.copy()
        df2.loc[t0_like:, "close"] = df["close"].iloc[t0_like] * (
            1.001 ** np.arange(len(df) - t0_like)
        )
        scan = sr.default_qn_scan_fn()
        a = scan("000001", df, "2012-01-04", "2013-12-31")
        b = scan("000001", df2, "2012-01-04", "2013-12-31")
        for g in sr.QN_GATES:
            assert {i for i in a[g] if i < t0_like} == {
                i for i in b[g] if i < t0_like
            }, f"{g} 的信号判定读到 T0 之后的数据"
        sw_a, _ = sr.identify_swings(
            df["date"].tolist(),
            df["close"].astype(float).tolist(),
            seg_start="2012-01-04",
            seg_end="2013-12-31",
        )
        sw_b, _ = sr.identify_swings(
            df2["date"].tolist(),
            df2["close"].astype(float).tolist(),
            seg_start="2012-01-04",
            seg_end="2013-12-31",
        )
        assert [(s["t0"], s["t1"]) for s in sw_a] != [
            (s["t0"], s["t1"]) for s in sw_b
        ], "波段标签本应读未来价格（标签侧写死允许）"


# ---------------------------------------------------------------------------
# 合成世界（端到端 + 校准共用）
# ---------------------------------------------------------------------------


def _fastpath(warm=300, cycles=12):
    """短周期波段路径（校准世界用）：78 根/周期「16 跌 −11% → 16 拉 +27%（+20% 成立）
    → 9 回 −12%（−10% 确认）→ 37 阴跌」，12 周期。几何（周期内偏移）：
    T0=+15；窗口 [+5,+20]；确认 +40；两个隔离冷槽 X=[+27,+43) / Y=[+49,+65)
    （相互及与前后窗口间隔 ≥6 根 > 簇合并门 5 ⇒ 冷热簇永不合并、同长 16）。"""
    px = [10.0] * warm
    p = 10.0
    for _ in range(cycles):
        for _ in range(16):
            p *= 0.9925
            px.append(p)
        for _ in range(16):
            p *= 1.015
            px.append(p)
        for _ in range(9):
            p *= 0.986
            px.append(p)
        for _ in range(37):
            p *= 0.997
            px.append(p)
    return px


def _mk_df(days, closes):
    return pd.DataFrame(
        {
            "date": days,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": 1e6,
            "amount": 1e7,
        }
    )


def _world(seed=0, edge=False, clustered=True, n_codes=12):
    """两挖掘段合成世界：每码 300 根预热 + 12 周期波段（每周期 1 个 ≥20% 波段；
    挖掘 B 末周期 T0 落段末隔离带被剔——双窗构造同构，读数自查即可）。

    信号按「纯块标签」构造（校准分辨力的关键）：簇 = 16 根连号孤立块，**整簇全中或
    全不中**起涨窗口——热簇 = 窗口本体，冷簇 = 阴跌段隔离槽 X/Y（同长、互不合并——
    长度分层与热度严格无关；早期版本热簇孤立、冷簇重叠成链，长度分层把热度锁进
    16 长度层，臂复现了 edge，实测在案）。B1 = 7 簇/码（热概率 0.20 ≈ 窗口 bar 覆盖
    率 ⇒ 零假设中性；edge=0.85 ⇒ 「热概率系统性抬高」而非全热——全热会把池灌满、
    臂轻易复现）；QN 12 门各 1 簇/码（热概率恒 0.20）。簇级置换下块标签整体换位 ⇒
    方差按簇数算；逐笔置换拆簇 ⇒ q95 系统性偏低（哨兵据此必须失败）。
    clustered=False ⇒ 全部逐 bar iid（密度 0.10，随机放置 ⇒ 精度期望=覆盖率）。
    """
    out: dict = {"warm": {}, "bars": {}, "qn": {}, "regime": {"2000-01-01": "做多"}}
    codes = [f"{k:06d}" for k in range(n_codes)]
    n_cyc = 12

    def _place(swings, ki, w, key, cnt, p_hot):
        """cnt 簇：逐簇定周期 → 热（该周期窗口本体）/ 冷（槽 X 或 Y）。"""
        bursts = {}
        for b in range(cnt):
            c = int(12 * _u("cyc", key, seed, ki, b, w)) % n_cyc
            base = 300 + 78 * c
            if _u("hot", key, seed, ki, b, w) < p_hot and swings:
                st = base + 5  # 窗口 [t0−10, t0+5] = 周期 +5 起 16 根
            else:
                st = base + (27 if _u("slot", key, seed, ki, b, w) < 0.5 else 49)
            bursts[st] = list(range(st, st + 16))
        return bursts

    for w, (seg, d0) in enumerate(SEG_BARS.items()):
        bars = {}
        warm = {}
        qn_seg = {}
        for ki, code in enumerate(codes):
            px = [p * (1 + 0.05 * ki) for p in _fastpath(warm=300, cycles=n_cyc)]
            days = _days(len(px), d0)
            df = _mk_df(days, px)
            bars[code] = df
            swings, _ = sr.identify_swings(
                days, px, seg_start=SPANS[seg][0], seg_end=SPANS[seg][1]
            )
            n = len(days)
            bursts = _place(swings, ki, w, "b1", 7, 0.85 if edge else 0.20)
            if clustered:
                sigs = [
                    {"i": i, "date": days[i]} for ids in bursts.values() for i in ids
                ]
            else:
                sigs = [
                    {"i": i, "date": days[i]}
                    for i in range(320, n - 2)
                    if _u("iid", seed, ki, i, w) < 0.10
                ]
            sigs.sort(key=lambda s: s["i"])
            # 分数按簇恒定（保纯块结构）：~60% 簇过 1800 观察线
            bk_of = {i: b for b, ids in enumerate(bursts.values()) for i in ids}
            scores = {
                sg["date"]: (
                    70.0 if _u("sc", code, bk_of.get(sg["i"], sg["i"])) < 0.6 else 40.0
                )
                for sg in sigs
            }
            warm[code] = {"df": df, "signals": sigs, "scores": scores}
            if clustered:
                qn_seg[code] = {
                    g: sorted(
                        i
                        for ids in _place(swings, ki, w, g, 1, 0.20).values()
                        for i in ids
                    )
                    for g in sr.QN_GATES
                }
            else:
                qn_seg[code] = {
                    g: [
                        sg["i"]
                        for sg in sigs
                        if _u("qn", seed, g, ki, sg["i"], w) < 0.7
                    ]
                    for g in sr.QN_GATES
                }
        out["bars"][SPANS[seg]] = bars
        out["warm"][SPANS[seg]] = warm
        out["qn"][seg] = qn_seg
    return out


def _replay(subset, params):
    out = []
    for r in subset:
        u = _u("ret", r["code"], r["date"])
        ret = 0.25 if u < 0.3 else (-0.08 if u < 0.7 else 0.05)
        t = hn.mk_trade(r["code"], r["date"], ret, r.get("score") or 50.0)
        t["exit_date"] = r["date"]
        out.append(t)
    return out


def _args(**kw):
    import argparse

    base = dict(
        tag="t",
        cost_bps=25.0,
        top_n=20,
        seed=3,
        n_random=25,
        c4_min_pool=20,
        min_n=50,
        codes="",
        codes_file="",
        count=2000,
        universe_sample=0,
        universe_local=False,
        universe_seed=42,
        load_workers=1,
        score_workers=1,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _run_spec(spec, seed=0, **kw):
    def qn_scan(code, df, start, end):
        seg = next(k for k, s in SPANS.items() if s[0] <= start and end <= s[1])
        return spec["qn"][seg][code]

    def mcap(code, df):
        base = 1e8 * (2 ** (int(code) % 12))
        return {
            str(d)[:10]: base * (1 + 0.1 * _u("mc", code, str(d)[:7]))
            for d in df["date"]
        }

    return sr.run_study(
        _args(seed=seed, **kw),
        warm_fn=lambda s, e: spec["warm"][(s, e)],
        bars_fn=lambda s, e: spec["bars"][(s, e)],
        qn_scan_fn=qn_scan,
        mcap_fn=mcap,
        replay_fn=_replay,
        regime=spec["regime"],
    )


def _world_run(seed, edge=False, clustered=True, **kw):
    return _run_spec(_world(seed, edge, clustered), seed, **kw)


def _unit_c4(rep):
    """校准口径：任一对象 lead_robust 记 confirmed_pass（16 对象里挑最好的误报率）。"""
    return {
        "criteria": {
            "C4": {
                "state": "confirmed_pass" if rep["robust_leads"] else "confirmed_fail"
            }
        }
    }


# ---------------------------------------------------------------------------
# 端到端与口径
# ---------------------------------------------------------------------------


def test_end_to_end_shape():
    rep = _world_run(0)
    assert rep["unit"] == "R50" and rep["schema"] == "swing_recall_study/v1"
    assert set(rep["objects"]) == set(sr.OBJECTS)
    assert rep["window_usage"]["window"] == "not_read"
    assert rep["window_usage"]["k"] is None
    for w in ("mining_a", "mining_b"):
        assert rep["n_swings"][w] > 0
        f = rep["funnel"][w]
        assert f["n_l1"] >= f["n_l2"] >= f["n_l3"] >= f["n_l4"] >= 0
        assert f["loss"]["no_j13"] == f["n_swings"] - f["n_l1"]
        assert rep["realization"][w]["n_realized"] > 0
        assert rep["by_year"][w] and rep["by_month"][w]
    # 簇级置换池（臂恒有值 ⇒ 必满 N）
    assert len(rep["c4_pool"]["pool"]) == 25
    assert rep["c4_pool"]["q95"] is not None


def test_edge_world_robust_lead():
    """B1 簇热概率 0.18→0.85（双窗同构）⇒ b1_l1 复现且过 q95 = lead_robust。"""
    rep = _world_run(0, edge=True)
    assert "b1_l1" in rep["robust_leads"]
    l1 = rep["objects"]["b1_l1"]
    assert l1["replicated"] and l1["stat"] > 1.2
    for w in ("mining_a", "mining_b"):
        assert l1["windows"][w]["precision"] > 0.6  # 热簇占比系统性抬高
        assert l1["windows"][w]["recall"] > 0.1  # 热点覆盖部分波段（不追求全召回）


def test_matching_math_exact():
    """手造单码双波段：召回 / 精度 / 提前量逐位核对。"""
    days = _days(600)
    px = _vpath(warm=300, cycles=2)[:600]
    days = days[: len(px)]
    df = _mk_df(days, px)
    swings, _ = sr.identify_swings(
        days, px, seg_start="2000-01-01", seg_end="2016-12-31"
    )
    assert len(swings) == 2
    t0a, t0b = swings[0]["t0"], swings[1]["t0"]
    # 信号：t0a−10（窗口左沿，命中）/ t0a+6（窗外右侧）/ t0b+5（窗口右沿，命中）
    sig_idx = [t0a - 10, t0a + 6, t0b + 5]
    rows = [
        {"code": "000001", "i": i, "date": days[i], "_seg": "mining_a"} for i in sig_idx
    ]
    win = {
        "name": "mining_a",
        "swings_of": {"000001": swings},
        "dates_of": {"000001": days},
        "sig_rows": {o: (rows if o == "b1_l1" else []) for o in sr.OBJECTS},
        "rows_of": {o: ({"000001": rows} if o == "b1_l1" else {}) for o in sr.OBJECTS},
        "year_bars": {},
        "year_lowbars": {},
        "mcap_rep": {},
        "q_edges": {},
        "strata_codes": {},
        "codes_by_year": {},
    }
    readings, _aux = sr.window_readings(win, seed=0)
    r = readings["b1_l1"]
    assert r["n_signals"] == 3 and r["n_matched"] == 2
    assert r["precision"] == pytest.approx(2 / 3)
    assert r["recall"] == 1.0  # 两个波段窗口内都有信号（首个分别是 t0a−10 / t0b+5）
    assert r["lead_quantiles"]["p50"] == pytest.approx(-2.5)  # [-10, +5] 的中点
    # 空对象如实记缺
    assert readings["qn_three_red"]["n_signals"] == 0
    assert readings["qn_three_red"]["precision"] is None
    assert readings["qn_three_red"]["lift"] is None


def test_b1_funnel_layers():
    """逐层语义：L2=做多日（regime 挡掉非做多）/ L3=分线 / L4=当日 top-N 排序键。"""
    per_code = {
        "000001": {
            "signals": [
                {"i": 10, "date": "2020-01-10"},
                {"i": 20, "date": "2020-02-10"},
                {"i": 30, "date": "2020-03-10"},
            ],
            "scores": {"2020-01-10": 70.0, "2020-02-10": 50.0, "2020-03-10": 80.0},
        },
        "000002": {
            "signals": [{"i": 5, "date": "2020-03-10"}],
            "scores": {"2020-03-10": 90.0},
        },
    }
    regime = {"2020-01-01": "做多", "2020-02-01": "空头", "2020-03-01": "做多"}
    lay = sr.collect_b1_layers(per_code, regime, top_n=1)
    assert len(lay["b1_l1"]) == 4
    assert {(r["code"], r["date"]) for r in lay["b1_l2"]} == {
        ("000001", "2020-01-10"),
        ("000001", "2020-03-10"),
        ("000002", "2020-03-10"),
    }
    # L3：分数 ≥60 → 01-10(70) + 两个 03-10(80/90)
    assert len(lay["b1_l3"]) == 3
    # L4：top_n=1，2020-03-10 当日 90 分码压过 80 分码（同排序键）
    assert {(r["code"], r["date"]) for r in lay["b1_l4"]} == {
        ("000001", "2020-01-10"),
        ("000002", "2020-03-10"),
    }


def test_score_line_matches_live():
    from custos.pipeline.screening import score_candidates as sc

    assert sr.B1_SCORE_LINE == sc.TECH_STRONG_FALLBACK
    assert len(sr.OBJECTS) == 16


class TestMiningOnlyDiscipline:
    """只读挖掘段：不触 window_usage 台账、不过判定段批次门（R44 扫描模式同款钉测）。"""

    def test_no_ledger_no_batch_gate(self, monkeypatch):
        from custos.research import judgment_batch as jb
        from custos.research import window_usage as wu

        calls = []
        monkeypatch.setattr(
            wu, "record_use", lambda *a, **k: calls.append(("record_use", a, k))
        )
        monkeypatch.setattr(
            jb,
            "gate_judgment_read",
            lambda *a, **k: calls.append(("gate", a, k)) or False,
        )
        rep = _world_run(0)
        assert calls == [], "本单元不得触 record_use / 批次门（只读挖掘 A∪B）"
        # 台账零新增的强形式：conftest 已把台账改道 tmp——有任何写入文件即存在
        from pathlib import Path  # noqa: PLC0415

        assert not Path(wu.LEDGER).exists(), "window_usage 台账不得有新增行"
        assert rep["window_usage"] == {
            "window": "not_read",
            "k": None,
            "note": "只读挖掘 A∪B——判定段不读、批次门不触发、window_usage 不登记",
        }

    def test_window_constants_still_mining(self):
        """段常量漂移守卫：写死的两段必须仍过 mining_violation（fail-closed）。"""
        from custos.research import load_window as lw

        for seg in (lw.SEG_MINING_A, lw.SEG_MINING_B):
            assert lw.mining_violation(*seg) is None


def test_empty_guard_no_swings(tmp_path):
    spec = _world(0)
    # 全平价格 ⇒ 0 波段
    for seg in list(spec["bars"]):
        for code, df in spec["bars"][seg].items():
            spec["bars"][seg][code] = _mk_df(df["date"].tolist(), [10.0] * len(df))
    with pytest.raises(RuntimeError, match="空结果护栏"):
        _run_spec(spec, 0)
    # main 入口：非零退出且不写产物
    out = tmp_path / "out"

    def qn_scan(code, df, start, end):
        seg = next(k for k, s in SPANS.items() if s[0] <= start and end <= s[1])
        return spec["qn"][seg][code]

    rc = sr.main(
        ["--out-dir", str(out), "--tag", "x"],
        warm_fn=lambda s, e: spec["warm"][(s, e)],
        bars_fn=lambda s, e: spec["bars"][(s, e)],
        qn_scan_fn=qn_scan,
        mcap_fn=lambda c, df: {},
        replay_fn=_replay,
        regime=spec["regime"],
    )
    assert rc == 2 and not list(out.rglob("*.json"))


def test_main_writes_report(tmp_path):
    out = tmp_path / "out"
    spec = _world(0)

    def qn_scan(code, df, start, end):
        seg = next(k for k, s in SPANS.items() if s[0] <= start and end <= s[1])
        return spec["qn"][seg][code]

    rc = sr.main(
        ["--out-dir", str(out), "--tag", "t0"],
        warm_fn=lambda s, e: spec["warm"][(s, e)],
        bars_fn=lambda s, e: spec["bars"][(s, e)],
        qn_scan_fn=qn_scan,
        mcap_fn=lambda c, df: {},
        replay_fn=_replay,
        regime=spec["regime"],
    )
    assert rc == 0
    rep = json.loads((out / "_swing_recall__t0.json").read_text(encoding="utf-8"))
    assert rep["unit"] == "R50"
    # mcap 全缺 ⇒ 分层落 na 桶，基线仍出
    assert rep["objects"]["b1_l1"]["windows"]["mining_a"]["baseline"] is not None
