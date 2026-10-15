# -*- coding: utf-8 -*-
"""bear_regime_study.py（R40 空头区间做多全栈研究终端）钉测。

锁的契约：regime 反转语义（仅空头日放行/无映射不放行）、230 格枚举、
随机臂**对等纪律**（挖掘窗选型冻结 ⇒ 判定窗读数由冻结配置产生——用
「判定窗池全负」结果级证明：若臂在判定窗重选，池不可能全负）+
**预算对等**（v0.307：每臂逐门 n_g 抽样复刻 top 的 max-of-230 选型，
非单抽一个 n × 5 档）、C1 不过=untested、C2 三态（池未建=None⇒
provisional 不 falsified）、C3 无可扰参数轴=not_applicable 不空转
不放行（v0.307）、CLI 护栏、产物自含。全合成注入，不发真实加载。

v2 窗口布局（TODO #90，owner 2026-10-13 拍板）：四参全 None ⇒ 三段
（挖掘 A 2000~2016 / 判定 2017~2021 / 挖掘 B 2022~2026-09-04，挖掘读数
= A∪B 合并一池；三段末各 clip_entries 一刀 = 90 自然日隔离带，剔除数
入 segments 块）；四参全显式 ⇒ legacy 单段（逐窗段校验 + 双窗不相交——
旧次序规则作废），行为与改前**逐位一致**（唯 window_usage 按 v2 口径：
判定窗在挖掘段内 ⇒ 不入台账——硬钉测 TestLegacyBitIdentical）。
"""

from __future__ import annotations

import argparse

import pytest

from helpers_golden import canon_json
from custos.research import backtest_factors as bt
from custos.research import bear_regime_study as brs
from custos.research import factor_exit_study as fes


def _args(**kw):
    base = dict(
        mining_start="2022-01-01",
        mining_end="2024-07-31",
        judgment_start="2024-08-01",
        judgment_end="2026-09-04",
        cost_bps=25.0,
        top_n=20,
        seed=7,
        n_random=4,
        c4_min_pool=3,
        tag="t",
        gates="all",
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _mk_per_code(
    gate_counts, windows=(("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))
):
    """{gate: count} → 双窗 per_code（df 占位 None——注入 replay 时不用）。"""
    out = {}
    for w_i, (s, e) in enumerate(windows):
        per_code = {}
        k = 0
        for gate, n in gate_counts.items():
            for _ in range(n):
                code = f"{(k % 30):06d}"
                day = f"2023-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}"
                i = w_i * 10000 + k
                pack = per_code.setdefault(
                    code, {"df": None, "gate_signals": {}, "scores": {}}
                )
                pack["gate_signals"].setdefault(gate, []).append({"i": i, "date": day})
                pack["scores"][day] = 50.0
                k += 1
        out[(s, e)] = per_code
    return out


def _trade_of(r, ret):
    return {
        "code": r["code"],
        "entry_date": r["date"],
        "exit_date": r["date"],
        "ret": ret,
        "reason": "stop",
        "holding": 5,
        "risk_frac": 0.05,
        "r_multiple": ret / 0.05,
        "score": r["score"],
    }


def _replay_winner_gate(subset, params):
    """j_low 门 × 含 trail_pct 的档（=pct5_trail08，可扰参数轴）两窗稳赢
    （margin≈0.3），其余门/档负（≈−0.167）；C3 扰动不改结论（ret 只认
    trail_pct 存在与否）。随机臂（__random__）贴零。"""
    out = []
    for r in subset:
        if r["gate"] == "j_low" and params.get("trail_pct"):
            ret = 0.08 if r["i"] % 2 == 0 else -0.02
        elif r["gate"] == "__random__":
            ret = 0.01 if r["i"] % 2 == 0 else -0.005
        else:
            ret = -0.01 if r["i"] % 2 == 0 else 0.005
        out.append(_trade_of(r, ret))
    return out


def _replay_base_low_wins(subset, params):
    """j_low × base_low（params={} 无可扰参数轴）两窗稳赢——C3 不适用路径。"""
    out = []
    for r in subset:
        if r["gate"] == "j_low" and not params:
            ret = 0.08 if r["i"] % 2 == 0 else -0.02
        elif r["gate"] == "__random__":
            ret = 0.01 if r["i"] % 2 == 0 else -0.005
        else:
            ret = -0.01 if r["i"] % 2 == 0 else 0.005
        out.append(_trade_of(r, ret))
    return out


def _replay_flat(subset, params):
    """全部同分布（margin≈0.167）——top 打不过随机臂 q95 ⇒ falsified。"""
    return [_trade_of(r, 0.02 if r["i"] % 2 == 0 else -0.01) for r in subset]


def _replay_frozen_probe(subset, params):
    """冻结语义探针：trail_pct>0 的档挖掘窗（i<10000）赢、判定窗大亏；
    其余档两窗小赚。若臂在判定窗**重选**，判定池不可能全负。"""
    out = []
    for r in subset:
        if params.get("trail_pct"):
            if r["i"] < 10000:
                ret = 0.08 if r["i"] % 2 == 0 else -0.02
            else:
                ret = -0.05 if r["i"] % 2 == 0 else 0.01
        else:
            ret = 0.008 if r["i"] % 2 == 0 else -0.004
        out.append(_trade_of(r, ret))
    return out


def _rand_flat(n, w):
    """随机臂合成：margin≈0.167 且权益正漂移（rdd 门要过）——候选剧本里
    top=0.3 打得过；falsified 剧本里 top 也是 0.167 ⇒ 打不过池。"""
    return [
        {
            "code": f"{i % 7:06d}",
            "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "i": (0 if w == "mining" else 10000) + i,
            "score": 50.0,
            "sig": {"i": i, "date": "2024-01-02"},
            "gate": "__random__",
        }
        for i in range(n)
    ]


def _run(spec, replay, n_random=4, c4_min_pool=3, **kw):
    warm_fn = lambda s, e: spec[(s, e)]  # noqa: E731
    return brs.run_study(
        _args(n_random=n_random, c4_min_pool=c4_min_pool, **kw),
        warm_fn=warm_fn,
        replay_fn=replay,
        random_entry_fn=_rand_flat,
    )


#: v2 三段布局段区间（与 load_window 段常量同值，测试内独立写死防漂移）
SEGS_V2 = {
    "mining_a": ("2000-01-01", "2016-12-31"),
    "judgment": ("2017-01-01", "2021-12-31"),
    "mining_b": ("2022-01-01", "2026-09-04"),
}


def _mk_per_code_v2(gate_dates):
    """{seg: {gate: [date, ...]}} → {(start, end): per_code} 三段合成夹具
    （i 段内递增生成；df 占位 None——注入 replay 时不用）。"""
    out = {}
    for seg, (s, e) in SEGS_V2.items():
        per_code = {}
        k = 0
        for gate, dates in gate_dates.get(seg, {}).items():
            for d in dates:
                code = f"{(k % 30):06d}"
                pack = per_code.setdefault(
                    code, {"df": None, "gate_signals": {}, "scores": {}}
                )
                pack["gate_signals"].setdefault(gate, []).append({"i": k, "date": d})
                pack["scores"][d] = 50.0
                k += 1
        out[(s, e)] = per_code
    return out


def _args_v2(**kw):
    """v2 布局参数（四参全 None ⇒ 三段）。"""
    return _args(
        mining_start=None, mining_end=None, judgment_start=None, judgment_end=None, **kw
    )


def _run_v2(spec, replay, n_random=4, c4_min_pool=3, warm_spy=None, **kw):
    warm_fn = lambda s, e: spec[(s, e)]  # noqa: E731
    if warm_spy is not None:
        orig = warm_fn

        def warm_fn(s, e):  # noqa: F811
            warm_spy.append((s, e))
            return orig(s, e)

    return brs.run_study(
        _args_v2(n_random=n_random, c4_min_pool=c4_min_pool, **kw),
        warm_fn=warm_fn,
        replay_fn=replay,
        random_entry_fn=_rand_flat,
    )


class TestInvertRegime:
    def test_mapping(self):
        got = brs.invert_regime_bearish(
            {"2022-01-04": "空头", "2022-01-05": "做多", "2022-01-06": "中性"}
        )
        assert got == {
            "2022-01-04": "做多",
            "2022-01-05": "空头",
            "2022-01-06": "空头",
        }

    def test_checker_semantics(self):
        """仅空头日放行；做多/中性不放行；无映射日（首条记录前）不放行。"""
        regime = brs.invert_regime_bearish({"2022-01-04": "空头", "2022-02-01": "做多"})
        chk = bt._amv_checker(regime)
        assert chk("2022-01-04") is True  # 真空头日
        assert chk("2022-01-20") is True  # 空头粘滞延续
        assert chk("2022-02-01") is False  # 真做多日
        assert chk("2021-12-31") is False  # 无映射日（as-of 无前驱）


class TestGridAndVerdicts:
    def test_grid_230_and_candidate(self):
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4, "qn_three_red": 4})
        rep = _run(spec, _replay_winner_gate)
        assert len(rep["configs"]) == 46 * 5
        assert rep["gates"] == sorted(bt.ENTRY_GATES)
        assert rep["top"]["gate"] == "j_low"
        assert rep["top"]["exit"] == "pct5_trail08"  # 可扰参数档（C3 适用）
        c = rep["criteria"]
        assert c["C1"]["ok"] is True, c["C1"]
        assert c["C2"]["ok"] is True, c["C2"]
        assert c["C2"]["pool_ok_mining"] and c["C2"]["pool_ok_judgment"]
        assert c["C3"]["ok"] is True and len(c["C3"]["draws"]) == brs.C3_DRAWS
        assert c["C4"]["state"] == "confirmed_pass"
        assert rep["verdict"] == "candidate"
        # v2 口径：legacy 判定窗（2024~2026 ⊆ 挖掘 B）⇒ 不入台账（TODO #90）
        assert rep["window_usage"] == {
            "window": "mining_segment",
            "k": None,
            "note": "判定窗在挖掘段内（非受保护段 2017~2021）——读挖掘段不入台账（v2 口径）",
        }

    def test_c3_not_applicable_for_base_low(self):
        """top 落在无可扰参数轴的出场（base_low params={}）⇒ C3 =
        not_applicable（v0.307）：不空转 4 次零信息复评、不伪装零翻转
        放行——总结局不降 falsified（无证据≠否定）也不放 candidate
        （灵敏度证据缺失）⇒ provisional。"""
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(spec, _replay_base_low_wins)
        assert rep["top"]["exit"] == "base_low"
        c3 = rep["criteria"]["C3"]
        assert c3["ok"] is None and c3["state"] == "not_applicable"
        assert c3["draws"] == [] and c3["n_flips"] is None
        assert rep["criteria"]["C2"]["ok"] is True  # 其余链条是过的
        assert rep["verdict"] == "provisional", "C3 无灵敏度证据不放行 candidate"

    def test_falsified_when_top_cannot_beat_random(self):
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(spec, _replay_flat)
        assert rep["criteria"]["C2"]["ok"] is False  # 池 q95 打不过了
        assert rep["verdict"] == "falsified"

    def test_c1_shortfall_is_untested(self):
        spec = _mk_per_code({"j_low": 60})  # 60 < 100
        rep = _run(spec, _replay_winner_gate)
        assert rep["criteria"]["C1"]["ok"] is False
        assert rep["verdict"] == "untested", "C1 不过=untested 不判 falsified（v0.301）"

    def test_provisional_when_pool_small(self):
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(spec, _replay_winner_gate, n_random=2, c4_min_pool=50)
        assert rep["criteria"]["C4"]["state"] == "provisional"
        assert rep["verdict"] == "provisional"

    def test_cost_sensitivity_block_wired(self):
        """成本副读数（owner review #6，v0.329）：top 交易集双窗定向重建
        （_replay 同路径）50bps 双报——R40 C2 绝对口径（margin>0）主战场。"""
        spec = _mk_per_code({"j_low": 120})
        rep = _run(spec, _replay_winner_gate, n_random=2, c4_min_pool=2)
        cs = rep["cost_sensitivity"]
        assert cs is not None and cs["base_bps"] == 25.0 and cs["side_bps"] == 50.0
        assert cs["arms"]["mining"]["n"] > 0 and cs["arms"]["judgment"]["n"] > 0
        assert set(cs["deltas"]) == {
            "top_margin_vs_0_mining",
            "top_margin_vs_0_judgment",
        }
        # 赢家门大胜场景：50bps 翻不动 margin 符号
        assert cs["deltas"]["top_margin_vs_0_mining"]["flip"] is False

    def test_empty_signals_guard(self):
        warm_fn = lambda s, e: {}  # noqa: E731
        with pytest.raises(RuntimeError, match="空结果护栏"):
            brs.run_study(_args(), warm_fn=warm_fn, replay_fn=_replay_winner_gate)


class TestArmFrozenSemantics:
    def test_judgment_pool_from_frozen_config(self):
        """对等纪律（v0.302）结果级证明：判定窗池全负 ⇒ 臂没有当窗重选
        （重选会挑两窗小赚的档，池不可能为负）。"""
        spec = _mk_per_code({"j_low": 120})
        rep = _run(spec, _replay_frozen_probe, n_random=4, c4_min_pool=2)
        pool_j = rep["pools"]["judgment"]
        assert len(pool_j) == 4
        assert all(m < 0 for m in pool_j), pool_j
        assert all(m > 0 for m in rep["pools"]["mining"])
        assert "冻结" in rep["pools"]["arm_construction"]

    def test_arm_budget_parity_max_of_230(self):
        """预算对等（v0.307）：每臂完整复刻 top 的 46 门 × 5 档选型——
        逐门按该门实测信号数抽样（0 信号门跳过），非单抽一个 n × 5 档
        （原 max-of-5 构造标尺系统性偏低）。"""
        draws = []

        def _spy_rand(n, w):
            draws.append((n, w))
            return _rand_flat(n, w)

        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4, "qn_three_red": 0})
        warm_fn = lambda s, e: spec[(s, e)]  # noqa: E731
        rep = brs.run_study(
            _args(n_random=3, c4_min_pool=2),
            warm_fn=warm_fn,
            replay_fn=_replay_winner_gate,
            random_entry_fn=_spy_rand,
        )
        mining_draws = [n for n, w in draws if w == "mining"]
        # 每臂 = 逐门各抽一次（120 + 4；0 信号门跳过）× 3 臂（门序字母序钉死）
        assert mining_draws == [120, 4] * 3
        judgment_draws = [n for n, w in draws if w == "judgment"]
        assert len(judgment_draws) == 3  # 每臂冻结配置判定窗一次
        assert set(judgment_draws) <= {120, 4}  # 冻结的是胜出格的 n_g
        assert "预算对等" in rep["pools"]["arm_construction"]

    def test_entry_accounting_recorded(self):
        """n_requested/n_returned 记账（v0.309）：注入路径 len==n ⇒ 两数
        相等；挖掘窗=每臂逐门 n_g 之和，判定窗=每臂冻结胜出格的 n_g。"""
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(spec, _replay_winner_gate, n_random=3, c4_min_pool=2)
        acc = rep["pools"]["entry_accounting"]
        assert acc["mining"] == {"requested": 3 * 124, "returned": 3 * 124}
        assert acc["judgment"] == {"requested": 3 * 120, "returned": 3 * 120}
        assert rep["pools"]["score_cache_size"] == {"mining": 0, "judgment": 0}


class TestRandomEntries:
    """random_entries 抽样语义（v0.308 owner review）：无放回 + 母体截断。"""

    def _per_code(self):
        import pandas as pd

        df = pd.DataFrame(
            {"date": pd.to_datetime(["2023-01-02", "2023-01-03", "2023-01-04"])}
        )
        scores = {d: 50.0 for d in ("2023-01-02", "2023-01-03", "2023-01-04")}
        return {
            "000001": {"df": df, "scores": dict(scores)},
            "000002": {"df": df, "scores": dict(scores)},
        }

    def test_without_replacement_and_truncation(self):
        """同一 (code, bar) 不重复计入；n > 母体 ⇒ 截断（有放回会出满 n
        且带重复——逐门 n_g 抽样后小门池污染最重）。"""
        import random as _r

        per_code = self._per_code()
        regime = brs.invert_regime_bearish({"2023-01-01": "空头"})  # 全段空头
        out = brs.random_entries(per_code, regime, 100, _r.Random(1))
        assert len(out) == 2 * 3, "n=100 > 母体 6 ⇒ 截断到 6（有放回会出满 100）"
        keys = {(e["code"], e["i"]) for e in out}
        assert len(keys) == len(out), "无重复 (code, bar)"
        # 母体以内正常出数：n=4 ⇒ 恰 4 条仍无重复
        out4 = brs.random_entries(per_code, regime, 4, _r.Random(2))
        assert len(out4) == 4
        assert len({(e["code"], e["i"]) for e in out4}) == 4

    def test_prebuilt_pool_bit_identical(self):
        """v0.309：预建池注入与内联建池逐位一致（纯实现优化不改结果）。"""
        import random as _r

        per_code = self._per_code()
        regime = brs.invert_regime_bearish({"2023-01-01": "空头"})
        a = brs.random_entries(per_code, regime, 4, _r.Random(7))
        b = brs.random_entries(
            per_code,
            regime,
            4,
            _r.Random(7),
            pool=brs.bear_bar_pool(per_code, regime),
        )
        assert a == b

    def test_score_cache_and_none_cached(self, monkeypatch):
        """v0.309：as-of 打分按 (code,i) 缓存——同种子第二遍零新增计算且
        结果逐位一致；打分失败（None）同样缓存（跳过语义逐位不变）。"""
        import random as _r

        import pandas as pd

        from custos.research import score_return_study as srs

        df = pd.DataFrame(
            {"date": pd.to_datetime(["2023-01-02", "2023-01-03", "2023-01-04"])}
        )
        per_code = {
            "000001": {"df": df, "scores": {}},  # scores 全缺 ⇒ 全走现算
            "000002": {"df": df, "scores": {}},
        }
        regime = brs.invert_regime_bearish({"2023-01-01": "空头"})
        calls = []

        def fake_asof(df_arg, index_df, i, code):
            calls.append((code, i))
            if i == 0:
                raise RuntimeError("打分失败")
            return 60.0 + i, "中", {}  # 真函数返回 (score, level, contrib)

        monkeypatch.setattr(srs, "asof_technical_score", fake_asof)
        cache: dict = {}
        a = brs.random_entries(
            per_code, regime, 6, _r.Random(7), index_df=object(), score_cache=cache
        )
        assert len(calls) == 6  # 抽满 6 根各算一次（失败的也算一次后缓存 None）
        assert len(a) == 4, "两票 i=0 打分失败被跳过"
        assert sum(1 for v in cache.values() if v is None) == 2
        b = brs.random_entries(
            per_code, regime, 6, _r.Random(7), index_df=object(), score_cache=cache
        )
        assert a == b, "缓存路径结果与首遍逐位一致"
        assert len(calls) == 6, "第二遍全命中缓存零新增计算"


class TestC3Perturb:
    def test_levels_params_perturbed_others_kept(self):
        import random as _r

        rng = _r.Random(1)
        out, moved = brs.perturb_exit_params(
            {"stop_mode": "pct", "stop_pct": 5, "trail_pct": 0.08}, rng
        )
        assert moved is True
        assert out["stop_mode"] == "pct", "档位外键原样保留"
        assert out["stop_pct"] in (4.0, 5.0, 6.0), "吸附档位空间"
        assert out["trail_pct"] in (0.06, 0.08, 0.10)

    def test_base_low_no_perturbable(self):
        import random as _r

        out, moved = brs.perturb_exit_params({}, _r.Random(1))
        assert moved is False and out == {}, "base_low 原样留痕"


class TestCliWindowGuards:
    """v2 布局 CLI 护栏（TODO #90）：越段/触 holdout/相交/半截 ⇒ SystemExit；
    R40 legacy 参数（⊆B 且不相交）合法；旧次序规则作废（判定窗早于挖掘窗
    但不相交 ⇒ 合法）；pre2019 交集不再是拒绝理由（段内子窗合法）。"""

    def _parse(self, *argv):
        ap = brs._build_parser()
        return ap, ap.parse_args(list(argv))

    def test_r40_legacy_params_accepted(self):
        ap, args = self._parse(
            "--tag",
            "t",
            "--mining-start",
            "2022-01-01",
            "--mining-end",
            "2024-07-31",
            "--judgment-start",
            "2024-08-01",
            "--judgment-end",
            "2026-09-04",
        )
        brs._check_windows(args, ap)  # 不拒即合法

    def test_cross_segment_window_rejected(self):
        ap, args = self._parse(
            "--tag",
            "t",
            "--mining-start",
            "2010-01-01",  # 越段（跨挖掘 A/判定）
            "--mining-end",
            "2020-12-31",
            "--judgment-start",
            "2024-08-01",
            "--judgment-end",
            "2026-09-04",
        )
        with pytest.raises(SystemExit):
            brs._check_windows(args, ap)

    def test_holdout_touch_rejected(self):
        ap, args = self._parse(
            "--tag",
            "t",
            "--mining-start",
            "2022-01-01",
            "--mining-end",
            "2024-07-31",
            "--judgment-start",
            "2024-08-01",
            "--judgment-end",
            "2026-10-08",  # 触前向 holdout 冻结段
        )
        with pytest.raises(SystemExit):
            brs._check_windows(args, ap)

    def test_intersecting_windows_rejected(self):
        """双窗均 ⊆ 挖掘 B 但相交 ⇒ 拒（不相交规则取代旧次序规则）。"""
        ap, args = self._parse(
            "--tag",
            "t",
            "--mining-start",
            "2022-01-01",
            "--mining-end",
            "2024-07-31",
            "--judgment-start",
            "2024-07-01",  # 与挖掘窗相交
            "--judgment-end",
            "2026-09-04",
        )
        with pytest.raises(SystemExit):
            brs._check_windows(args, ap)

    def test_order_rule_abolished_for_disjoint_windows(self):
        """判定窗**早于**挖掘窗但不相交（均 ⊆B）⇒ 合法（旧
        ``mining_end < judgment_start`` 次序规则作废——v2 判定段天然
        夹在挖掘 A/B 之间，次序约束已无意义）。"""
        ap, args = self._parse(
            "--tag",
            "t",
            "--mining-start",
            "2024-01-01",
            "--mining-end",
            "2024-07-31",
            "--judgment-start",
            "2022-01-01",
            "--judgment-end",
            "2023-12-31",
        )
        brs._check_windows(args, ap)  # 不拒即合法

    def test_pre2019_subwindow_no_longer_rejected(self):
        """⊆ 挖掘 A 的子窗（旧 pre2019 段）合法——v0.356 起 pre2019 交集
        硬拒绝已拆除（C5 退役、挖掘 A 与该段天然重叠）。"""
        ap, args = self._parse(
            "--tag",
            "t",
            "--mining-start",
            "2010-01-01",
            "--mining-end",
            "2016-12-31",
            "--judgment-start",
            "2017-01-01",
            "--judgment-end",
            "2021-12-31",
        )
        brs._check_windows(args, ap)  # 不拒即合法

    def test_partial_window_params_rejected(self):
        """只给一部分 ⇒ 半截布局拒绝（全 None=v2 三段才合法）。"""
        ap, args = self._parse("--tag", "t", "--mining-start", "2015-01-01")
        with pytest.raises(SystemExit):
            brs._check_windows(args, ap)

    def test_main_partial_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            brs.main(["--tag", "t", "--mining-start", "2015-01-01"])
        assert exc.value.code == 2

    def test_run_study_partial_raises(self):
        """run_study 直调（绕过 CLI）的半截布局 ⇒ ValueError（兜底分支）。"""
        spec = _mk_per_code({"j_low": 10})
        warm_fn = lambda s, e: spec[(s, e)]  # noqa: E731
        with pytest.raises(ValueError, match="半截布局"):
            brs.run_study(
                _args(mining_start=None),
                warm_fn=warm_fn,
                replay_fn=_replay_winner_gate,
            )


class TestCliAndProduct:
    def test_self_contained(self):
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(spec, _replay_winner_gate)
        for k in (
            "schema",
            "verdict",
            "gates",
            "exit_grid",
            "windows",
            "amv_mode",
            "n_signals_by_gate",
            "top",
            "configs",
            "pools",
            "criteria",
            "objective_version",
        ):
            assert k in rep, k
        from custos.research import strategy_grid as sg  # noqa: PLC0415

        assert rep["objective_version"] == sg.OBJECTIVE_VERSION
        for cfg in rep["configs"]:
            for w in ("mining", "judgment"):
                assert "taken" not in (cfg[w] or {}), "报告不落交易明细"
        assert set(rep["criteria"]) == {"C1", "C2", "C3", "C4", "rule_note"}


# ---------------------------------------------------------------------------
# 台账生产方向钉（v0.343，owner——r42_probe 漏登根因）
# ---------------------------------------------------------------------------


def _wu_spy(monkeypatch):
    """record_use 捕获桩：返回固定 k=7（不碰真台账），返回调用清单。"""
    from custos.research import window_usage as wu

    calls = []

    def spy(unit, window, tag, purpose, *, synthetic=False, path=None):
        calls.append({"unit": unit, "window": window, "synthetic": synthetic})
        return 7

    monkeypatch.setattr(wu, "record_use", spy)
    return calls


def _with_batch(args, tool):
    """v0.374 批次锁（TODO #95）：生产方向（注入件全 None）跑数须先入批——
    登记文件已被 conftest 改道 tmp，这里开批并把批次身份写进 args。"""
    from custos.research import judgment_batch as jb

    h = jb.config_hash(tool, args)
    jb.open_batch("b1", [{"id": "c1", "config_hash": h, "unit": "R", "note": "t"}])
    args.batch_id, args.candidate_id = "b1", "c1"
    return jb


class TestProductionRecordUse:
    """run_study 传 warm_fn=None（**生产路径**）+ 生产依赖全 monkeypatch 成
    合成 ⇒ v2 布局下 record_use 必须以 ``synthetic=False`` + 新键
    ``judgment_2017_2021`` 被调且 k 直通报告。防回归：生产分支重绑注入
    参数 ⇒ 入口快照 ``injected`` 变 True ⇒ 立刻红。随机臂的生产路径
    （bear_bar_pool/random_entries 需真 df）由 run_arm 最小桩旁路——
    本测试目标是 record_use/宇宙 meta 接线，不是臂逻辑。"""

    def _production_run(self, monkeypatch):
        import pandas as pd

        from custos.datasource.local_tdx import local_tdx_data

        spec = _mk_per_code_v2(
            {
                "mining_a": {"j_low": [f"2015-{m:02d}-15" for m in range(1, 13)]},
                "judgment": {"j_low": [f"2019-{m:02d}-15" for m in range(1, 13)]},
                "mining_b": {"j_low": [f"2023-{m:02d}-15" for m in range(1, 13)]},
            }
        )

        def _fake_resolve(ns, ap):
            ns.universe_meta = {
                "universe_rule": "vipdoc_default",
                "n_codes": 1,
                "universe_sha256": "0123456789abcdef",
            }
            return ["000000"]

        monkeypatch.setattr(bt, "_resolve_universe", _fake_resolve)
        monkeypatch.setattr(bt, "load_amv_regime", lambda since: {"2022-01-03": "空头"})
        monkeypatch.setattr(
            local_tdx_data,
            "get_ohlcv_table",
            lambda code, count: pd.DataFrame({"date": ["2022-01-03"]}),
        )
        monkeypatch.setattr(
            brs,
            "warm_gates",
            lambda codes, regime, index_df, *, gates, count, cost_bps, start, end, warmup_bars=0: (
                spec[(start, end)]
            ),
        )
        monkeypatch.setattr(
            fes,
            "replay_signals",
            lambda view, subset, params, regime, cost_bps: _replay_winner_gate(
                subset, params
            ),
        )
        # 随机臂最小桩：池空 ⇒ indeterminate（本测试不管臂逻辑）
        monkeypatch.setattr(brs, "run_arm", lambda *a, **kw: {"mining": None})
        # brs._args 无宇宙/count 字段（注入路径用不到）——生产分支要读
        args = _args_v2(
            gates="j_low",
            n_random=2,
            c4_min_pool=2,
            codes="",
            codes_file="",
            universe_local=False,
            universe_sample=0,
            universe_seed=42,
            count=2000,
        )
        _with_batch(args, "bear_regime_study")  # v0.374：生产方向须带批次
        return brs.run_study(
            args,
            warm_fn=None,
            replay_fn=None,
            random_entry_fn=None,
        )

    def test_production_records_with_synthetic_false(self, monkeypatch):
        calls = _wu_spy(monkeypatch)
        rep = self._production_run(monkeypatch)
        assert len(calls) == 1
        assert calls[0]["unit"] == "R40"
        assert calls[0]["window"] == "judgment_2017_2021", "v2 判定段用新键登记"
        assert calls[0]["synthetic"] is False, "生产路径必须按 synthetic=False 登记"
        assert rep["window_usage"]["window"] == "judgment_2017_2021"
        assert rep["window_usage"]["k"] == 7, "生产口径 k 直通报告"
        assert "第 7 次被读" in rep["window_usage"]["note"]

    def test_universe_meta_wired_to_provenance(self, monkeypatch):
        """宇宙 meta 回塞（v0.356 同族）：生产分支解析 codes 后
        ``args.universe_meta = ns.universe_meta`` ⇒ provenance 读到三件套。"""
        _wu_spy(monkeypatch)
        rep = self._production_run(monkeypatch)
        pv = rep["provenance"]
        assert pv["universe_rule"] == "vipdoc_default"
        assert pv["n_codes"] == 1
        assert pv["universe_sha256"] == "0123456789abcdef"

    def test_injected_v2_records_new_key_synthetic(self, monkeypatch):
        """v2 注入路径：判定窗=受保护段 ⇒ 新键登记 + synthetic=True（双向钉死）。"""
        calls = _wu_spy(monkeypatch)
        spec = _mk_per_code_v2(
            {
                "mining_a": {"j_low": [f"2015-{m:02d}-15" for m in range(1, 13)]},
                "judgment": {"j_low": [f"2019-{m:02d}-15" for m in range(1, 13)]},
                "mining_b": {"j_low": [f"2023-{m:02d}-15" for m in range(1, 13)]},
            }
        )
        rep = _run_v2(
            spec, _replay_winner_gate, n_random=2, c4_min_pool=2, gates="j_low"
        )
        assert len(calls) == 1
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is True
        assert rep["window_usage"]["k"] == 7

    def test_legacy_judgment_subwindow_registered(self, monkeypatch):
        """判定**子窗**（2018~2020 ⊆ 判定段）⇒ 相交即登记（v0.361 owner
        review——精确相等会把判定子窗放成漏网，7 入口统一 touches_judgment）。"""
        import pandas as pd

        from custos.datasource.local_tdx import local_tdx_data

        calls = _wu_spy(monkeypatch)

        def _mk_legacy_pc(dates):
            per_code = {}
            for k, d in enumerate(dates):
                code = f"{(k % 30):06d}"
                pack = per_code.setdefault(
                    code, {"df": None, "gate_signals": {}, "scores": {}}
                )
                pack["gate_signals"].setdefault("j_low", []).append({"i": k, "date": d})
                pack["scores"][d] = 50.0
            return per_code

        spec = {
            ("2022-01-01", "2024-07-31"): _mk_legacy_pc(
                [f"2023-{m:02d}-15" for m in range(1, 13)]
            ),
            ("2018-01-01", "2020-12-31"): _mk_legacy_pc(
                [f"2019-{m:02d}-15" for m in range(1, 13)]
            ),
        }

        def _fake_resolve(ns, ap):
            ns.universe_meta = {
                "universe_rule": "vipdoc_default",
                "n_codes": 1,
                "universe_sha256": "0123456789abcdef",
            }
            return ["000000"]

        monkeypatch.setattr(bt, "_resolve_universe", _fake_resolve)
        monkeypatch.setattr(bt, "load_amv_regime", lambda since: {"2022-01-03": "空头"})
        monkeypatch.setattr(
            local_tdx_data,
            "get_ohlcv_table",
            lambda code, count: pd.DataFrame({"date": ["2022-01-03"]}),
        )
        monkeypatch.setattr(
            brs,
            "warm_gates",
            lambda codes, regime, index_df, *, gates, count, cost_bps, start, end, warmup_bars=0: (
                spec[(start, end)]
            ),
        )
        monkeypatch.setattr(
            fes,
            "replay_signals",
            lambda view, subset, params, regime, cost_bps: _replay_winner_gate(
                subset, params
            ),
        )
        monkeypatch.setattr(brs, "run_arm", lambda *a, **kw: {"mining": None})
        args = _args(
            gates="j_low",
            judgment_start="2018-01-01",
            judgment_end="2020-12-31",
            n_random=2,
            c4_min_pool=2,
            codes="",
            codes_file="",
            universe_local=False,
            universe_sample=0,
            universe_seed=42,
            count=2000,
        )
        _with_batch(args, "bear_regime_study")  # v0.374：判定子窗相交 ⇒ 须带批次
        rep = brs.run_study(
            args,
            warm_fn=None,
            replay_fn=None,
            random_entry_fn=None,
        )
        assert len(calls) == 1
        assert calls[0]["unit"] == "R40"
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is False
        assert rep["window_usage"]["window"] == "judgment_2017_2021"

    def test_production_without_batch_rejected(self):
        """v0.374 批次锁（TODO #95）：v2 缺省窗 + 无批次 ⇒ 数据加载之前
        ap.error（exit 2）——门在暖身/加载之前，无需任何数据桩。"""
        with pytest.raises(SystemExit) as exc:
            brs.run_study(
                _args_v2(
                    gates="j_low",
                    n_random=2,
                    c4_min_pool=2,
                    codes="",
                    codes_file="",
                    universe_local=False,
                    universe_sample=0,
                    universe_seed=42,
                    count=2000,
                ),
                warm_fn=None,
                replay_fn=None,
                random_entry_fn=None,
            )
        assert exc.value.code == 2

    def test_mark_read_lands_and_auto_closes(self, monkeypatch):
        """带合法批次 ⇒ 过闸，收尾 mark_read 落进 tmp 登记文件；单候选批
        读完自动关批。"""
        _wu_spy(monkeypatch)
        self._production_run(monkeypatch)
        import json as _j

        from custos.research import judgment_batch as jb

        rows = [
            _j.loads(ln)
            for ln in jb.REGISTRY.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert [r["ev"] for r in rows] == ["open", "claim", "read", "close"]
        assert (
            rows[1]["candidate_id"] == "c1" and rows[1]["tool"] == "bear_regime_study"
        )

    def test_injected_legacy_not_recorded(self, monkeypatch):
        """legacy 判定窗（2024~2026 ⊆ 挖掘 B）⇒ **不登记**（读挖掘段自由，
        v2 口径）——record_use 零调用，报告写死「不入台账」注记。"""
        calls = _wu_spy(monkeypatch)
        rep = _run(_mk_per_code({"j_low": 60}), _replay_winner_gate, n_random=2)
        assert calls == [], "legacy 判定窗在挖掘段内——record_use 不得被调"
        assert rep["window_usage"] == {
            "window": "mining_segment",
            "k": None,
            "note": "判定窗在挖掘段内（非受保护段 2017~2021）——读挖掘段不入台账（v2 口径）",
        }


# ---------------------------------------------------------------------------
# v2 窗口布局（TODO #90，owner 2026-10-13 拍板）
# ---------------------------------------------------------------------------


class TestV2Layout:
    """(a) 四参全 None ⇒ v2 三段：段区间正确 + warm 逐段调用（A/判定/B）。"""

    def test_default_three_segments_and_warm_sequence(self):
        from custos.research import load_window as lw

        warm_calls: list = []
        spec = _mk_per_code_v2(
            {
                "mining_a": {"j_low": ["2015-06-15", "2015-07-15"]},
                "judgment": {"j_low": ["2019-06-15", "2019-07-15"]},
                "mining_b": {"j_low": ["2023-06-15", "2023-07-15"]},
            }
        )
        rep = _run_v2(
            spec,
            _replay_winner_gate,
            n_random=2,
            c4_min_pool=2,
            gates="j_low",
            warm_spy=warm_calls,
        )
        assert warm_calls == [
            SEGS_V2["mining_a"],
            SEGS_V2["judgment"],
            SEGS_V2["mining_b"],
        ], "mining 逐段 warm（A、B 各一次）+ 判定段，段序=A/判定/B"
        assert rep["windows"] == {
            "mining": {"start": "2000-01-01", "end": "2026-09-04"},
            "judgment": {"start": "2017-01-01", "end": "2021-12-31"},
        }
        segs = rep["segments"]
        for name, const in (
            ("mining_a", lw.SEG_MINING_A),
            ("judgment", lw.SEG_JUDGMENT),
            ("mining_b", lw.SEG_MINING_B),
        ):
            assert (segs[name]["start"], segs[name]["end"]) == const == SEGS_V2[name], (
                f"{name} 段区间与 load_window 段常量同源"
            )
        assert segs["mining_merged_n_signals"] == 4  # A 2 + B 2（判定不计入挖掘池）


class TestV2MergedPool:
    """(b) A∪B 合并一池：挖掘读数 == 手工合并两段信号（各自过隔离带）重算
    （逐位）；判定段读数 == 单段重算。"""

    def _spec(self):
        return _mk_per_code_v2(
            {
                "mining_a": {
                    "j_low": ["2016-09-30", "2016-12-15"]
                    + [f"2015-{m:02d}-15" for m in range(1, 13)] * 10
                },
                "judgment": {
                    "j_low": ["2021-09-30", "2021-12-15"]
                    + [f"2019-{m:02d}-15" for m in range(1, 13)] * 10
                },
                "mining_b": {
                    "j_low": ["2026-06-05", "2026-08-20"]
                    + [f"2023-{m:02d}-15" for m in range(1, 13)] * 10
                },
            }
        )

    def test_merged_mining_reading_matches_manual(self):
        from custos.research import strategy_grid as sg
        from custos.research.load_window import clip_entries

        spec = self._spec()
        rep = _run_v2(
            spec, _replay_winner_gate, n_random=2, c4_min_pool=2, gates="j_low"
        )
        row = next(
            c
            for c in rep["configs"]
            if c["gate"] == "j_low" and c["exit"] == "pct5_trail08"
        )
        trail = next(
            e["params"] for e in sg.DEFAULT_EXIT_GRID if e["name"] == "pct5_trail08"
        )
        # 手工合并：逐段 collect → 逐段隔离带 → 拼接（与 run_study 内部同构造）
        merged = clip_entries(
            brs.collect_gate_signals(spec[SEGS_V2["mining_a"]], "j_low"),
            SEGS_V2["mining_a"][1],
            key="date",
        ) + clip_entries(
            brs.collect_gate_signals(spec[SEGS_V2["mining_b"]], "j_low"),
            SEGS_V2["mining_b"][1],
            key="date",
        )
        exp_m = fes.combine_readings(_replay_winner_gate(merged, trail), 20, ref="self")
        exp_m.pop("taken")
        assert row["mining"] == exp_m, "A∪B 合并挖掘读数与手工合并重算逐位一致"
        jrecs = clip_entries(
            brs.collect_gate_signals(spec[SEGS_V2["judgment"]], "j_low"),
            SEGS_V2["judgment"][1],
            key="date",
        )
        exp_j = fes.combine_readings(_replay_winner_gate(jrecs, trail), 20, ref="self")
        exp_j.pop("taken")
        assert row["judgment"] == exp_j, "判定段读数与单段重算逐位一致"
        # 合并计数口径：mining = A 保留 + B 保留（121+121），judgment=121
        assert rep["n_signals_by_gate"] == {
            "mining": {"j_low": 242},
            "judgment": {"j_low": 121},
        }


class TestV2SegmentBuffer:
    """(c) 段末 90 自然日隔离带三刀：A→2016-10-02 / 判定→2021-10-02 /
    B→2026-06-06；剔除数入 segments 块；被剔信号永不进 replay/判据。"""

    def test_buffer_clips_three_segments_and_reported(self):
        seen_dates: set = set()

        def replay_spy(subset, params):
            for r in subset:
                seen_dates.add(r["date"])
            return _replay_winner_gate(subset, params)

        spec = _mk_per_code_v2(
            {
                "mining_a": {"j_low": ["2016-09-30", "2016-12-15"]},  # 保留/剔除
                "judgment": {"j_low": ["2021-08-15", "2021-09-30", "2021-12-15"]},
                "mining_b": {"j_low": ["2026-06-05", "2026-08-20"]},
            }
        )
        rep = _run_v2(spec, replay_spy, n_random=2, c4_min_pool=2, gates="j_low")
        segs = rep["segments"]
        assert segs["mining_a"] == {
            "start": "2000-01-01",
            "end": "2016-12-31",
            "entry_deadline": "2016-10-02",
            "n_signals": 1,
            "n_clipped": 1,
        }
        assert segs["judgment"] == {
            "start": "2017-01-01",
            "end": "2021-12-31",
            "entry_deadline": "2021-10-02",
            "n_signals": 2,
            "n_clipped": 1,
        }
        assert segs["mining_b"] == {
            "start": "2022-01-01",
            "end": "2026-09-04",
            "entry_deadline": "2026-06-06",
            "n_signals": 1,
            "n_clipped": 1,
        }
        assert segs["mining_merged_n_signals"] == 2
        # 被剔信号（隔离带内）永不进 replay；截止前信号照常进
        assert "2016-12-15" not in seen_dates
        assert "2021-12-15" not in seen_dates
        assert "2026-08-20" not in seen_dates
        assert {"2016-09-30", "2021-08-15", "2021-09-30", "2026-06-05"} <= seen_dates


#: (d) 硬钉测基准：v2 切换**前**同夹具（legacy R40 参数 + _replay_winner_gate
#: + _rand_flat + n_random=3/c4_min_pool=2/gates=j_low,j_low_adx25/seed=7）
#: 实测报告 JSON（provenance/window_usage 剔除——前者环境相关，后者是
#: 唯一受命变更面）。json.dumps(..., sort_keys=True, separators=(",",":"),
#: allow_nan=True) 口径。
_LEGACY_REPORT_BLOB = '{"amv_mode": "bearish（invert_regime_bearish：仅空头日放行，无映射不放行）", "configs": [{"exit": "base_low", "gate": "j_low", "judgment": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": null, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": false, "ret_over_dd": -0.0039663, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": null, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": false, "ret_over_dd": -0.0039663, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, {"exit": "pct5", "gate": "j_low", "judgment": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": null, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": false, "ret_over_dd": -0.0039663, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": null, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": false, "ret_over_dd": -0.0039663, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, {"exit": "pct5_trail08", "gate": "j_low", "judgment": {"expectancy_R": 0.6, "margin": 0.3, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": 0.3, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 4.0, "rdd_gate": true, "ret_over_dd": 26.162849872774, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": 0.6, "margin": 0.3, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": 0.3, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 4.0, "rdd_gate": true, "ret_over_dd": 26.162849872774, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, {"exit": "pct5_be05", "gate": "j_low", "judgment": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": null, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": false, "ret_over_dd": -0.0039663, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": null, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": false, "ret_over_dd": -0.0039663, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, {"exit": "pct5_cz3", "gate": "j_low", "judgment": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": null, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": false, "ret_over_dd": -0.0039663, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": null, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": false, "ret_over_dd": -0.0039663, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, {"exit": "base_low", "gate": "j_low_adx25", "judgment": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, {"exit": "pct5", "gate": "j_low_adx25", "judgment": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, {"exit": "pct5_trail08", "gate": "j_low_adx25", "judgment": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, {"exit": "pct5_be05", "gate": "j_low_adx25", "judgment": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, {"exit": "pct5_cz3", "gate": "j_low_adx25", "judgment": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": -0.05, "margin": -0.166666666667, "n": 4, "n_taken": 4, "n_unrealized": 0, "objective": -0.166666666667, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 0.5, "rdd_gate": true, "ret_over_dd": -6e-06, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}], "cost_sensitivity": {"arms": {"judgment": {"margin": 0.27497749775, "n": 120, "payoff_ratio": 3.444, "win_rate": 0.5}, "mining": {"margin": 0.27497749775, "n": 120, "payoff_ratio": 3.444, "win_rate": 0.5}}, "base_bps": 25.0, "deltas": {"top_margin_vs_0_judgment": {"base": 0.3, "flip": false, "side": 0.27497749775}, "top_margin_vs_0_mining": {"base": 0.3, "flip": false, "side": 0.27497749775}}, "note": "成本副读数（review #6）：side 档=逐笔 ret 平移 (side−base)/1e4 解析重算（不重跑引擎）；flip=True ⇒ 结论成本敏感、判读降权", "side_bps": 50.0}, "criteria": {"C1": {"judgment": {"n_taken": 120, "ok": true}, "min_n_taken": 100, "mining": {"n_taken": 120, "ok": true}, "ok": true}, "C2": {"margin_judgment": 0.3, "margin_mining": 0.3, "ok": true, "pool_ok_judgment": true, "pool_ok_mining": true, "pools_available": true, "pos_ok": true, "q95_judgment": 0.166666666667, "q95_mining": 0.166666666667, "rule": "margin 双窗同向为正 且 双窗>随机臂对应窗池 q95（基准=随机臂非空仓）"}, "C3": {"draws": [{"c2_holds": true, "draw": 0, "params_perturbed": {"stop_mode": "pct", "stop_pct": 4.0, "trail_pct": 0.06}, "perturbable": true}, {"c2_holds": true, "draw": 1, "params_perturbed": {"stop_mode": "pct", "stop_pct": 6.0, "trail_pct": 0.06}, "perturbable": true}, {"c2_holds": true, "draw": 2, "params_perturbed": {"stop_mode": "pct", "stop_pct": 6.0, "trail_pct": 0.06}, "perturbable": true}, {"c2_holds": true, "draw": 3, "params_perturbed": {"stop_mode": "pct", "stop_pct": 4.0, "trail_pct": 0.1}, "perturbable": true}], "n_flips": 0, "ok": true, "rule": "出场参数 ±50% ×4 扰动 C2 结论零翻转（池固定）"}, "C4": {"evaluated": 3, "gate_pass": 3, "gate_pass_rate": 1.0, "min_pool": 2, "note": "与 C2 挖掘半窗同池（重合是设计使然——R40 量级线与选择效应对的是同一个零假设）", "pool_size": 3, "q95": 0.166666666667, "state": "confirmed_pass"}, "rule_note": "R40-C1~C4 跑数前写死（v0.301/v0.302/v0.307 修订在案——v0.307：随机臂预算对等 max-of-230 镜像；C3 无可扰参数轴=not_applicable 不空转不放行）；C5（pre2019 同段随机池标尺）= C2 过线才启动，owner 拍板发令"}, "exit_grid": ["base_low", "pct5", "pct5_trail08", "pct5_be05", "pct5_cz3"], "forward_holdout_note": "判定窗末尾前进场的交易，出场执行会用到前向 holdout 区间的 bar（单侧机械泄漏：holdout 数据不参与选型/判据，只执行既定持仓的出场；截数据会把末段持仓强平失真——owner review #2② 注记在案）", "gates": ["j_low", "j_low_adx25"], "n_signals_by_gate": {"judgment": {"j_low": 120, "j_low_adx25": 4}, "mining": {"j_low": 120, "j_low_adx25": 4}}, "notes": ["目标函数 v2.1（margin 单量 + rdd 相对门：候选≥同窗 trail08 参照档）只相对排序；幸存者宇宙抬高绝对 margin 读数（退市票不在宇宙里）——相对结论才有效，绝对读数不进 live 决策"], "objective_version": "v2.1-margin-rddref", "pools": {"arm_construction": "对等纪律（v0.302）+ 预算对等（v0.307）：每臂完整复刻 top 的 46 门 × 5 档 max-of-230 选型（逐门 n_g 对齐抽样，0 信号门跳过），冻结出场配置进判定窗（判定窗抽样量 = 冻结门判定窗实际信号数，v0.386），禁每窗重新取 max", "entry_accounting": {"judgment": {"requested": 360, "returned": 360}, "mining": {"requested": 372, "returned": 372}}, "judgment": [0.166666666667, 0.166666666667, 0.166666666667], "mining": [0.166666666667, 0.166666666667, 0.166666666667], "q95_judgment": 0.166666666667, "q95_mining": 0.166666666667, "score_cache_size": {"judgment": 0, "mining": 0}}, "provenance": null, "schema": "bear_regime_report/v1", "tag": "t", "top": {"exit": "pct5_trail08", "gate": "j_low", "judgment": {"expectancy_R": 0.6, "margin": 0.3, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": 0.3, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 4.0, "rdd_gate": true, "ret_over_dd": 26.162849872774, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}, "mining": {"expectancy_R": 0.6, "margin": 0.3, "n": 120, "n_taken": 120, "n_unrealized": 0, "objective": 0.3, "objective_version": "v2.1-margin-rddref", "payoff_ratio": 4.0, "rdd_gate": true, "ret_over_dd": 26.162849872774, "trade_set_note": "margin=全候选交易(collect_all) / rdd=topN 组合选中子集——两批交易不同（v2.1 注明在案）", "win_rate": 0.5}}, "verdict": "candidate", "window_usage": null, "windows": {"judgment": {"end": "2026-09-04", "start": "2024-08-01"}, "mining": {"end": "2024-07-31", "start": "2022-01-01"}}}'


class TestLegacyBitIdentical:
    """(d) 硬钉测：legacy R40 参数（2022-01-01~2024-07-31 /
    2024-08-01~2026-09-04，均 ⊆ 挖掘 B 且互不相交）被接受，且合成数据
    报告与 v2 切换前**逐位一致**——唯一受命变更面 = window_usage
    （v2 口径：判定窗在挖掘段内不入台账）；legacy 不加 segments 块。"""

    def test_report_bit_identical_to_pre_v2(self):
        import json as _json

        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(
            spec,
            _replay_winner_gate,
            n_random=3,
            c4_min_pool=2,
            gates="j_low,j_low_adx25",
        )
        masked = {**rep, "provenance": None, "window_usage": None}
        assert canon_json(masked) == canon_json(_json.loads(_LEGACY_REPORT_BLOB)), (
            "legacy 报告偏离 v2 切换前基准（除 window_usage/provenance 外应同；"
            "浮点归一 1e-12——CI 异构 CPU 末位差，v0.359）"
        )
        assert rep["window_usage"] == {
            "window": "mining_segment",
            "k": None,
            "note": "判定窗在挖掘段内（非受保护段 2017~2021）——读挖掘段不入台账（v2 口径）",
        }, "唯一受命变更面：legacy 判定窗 ⊆ 挖掘 B ⇒ 不入台账"
        assert "segments" not in rep, "legacy 兼容通道不加 segments 块"
        assert set(rep) == {
            "schema",
            "tag",
            "verdict",
            "window_usage",
            "provenance",
            "gates",
            "exit_grid",
            "cost_sensitivity",
            "forward_holdout_note",
            "windows",
            "amv_mode",
            "n_signals_by_gate",
            "top",
            "configs",
            "pools",
            "criteria",
            "objective_version",
            "notes",
        }, "legacy 顶层键集与切换前一致"

    def test_legacy_run_deterministic(self):
        """对照钉：同夹具两跑逐位同一（blob 比对的确定性前提）。"""
        import json as _json

        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        kw = dict(n_random=3, c4_min_pool=2, gates="j_low,j_low_adx25")
        r1 = _run(spec, _replay_winner_gate, **kw)
        r2 = _run(spec, _replay_winner_gate, **kw)
        assert _json.dumps(r1, sort_keys=True, allow_nan=True) == _json.dumps(
            r2, sort_keys=True, allow_nan=True
        )


class TestV2RandomPoolBuffer:
    """v2 随机臂母体隔离带（v0.357 补齐：零假设与门信号同纪律）。"""

    @staticmethod
    def _pc(dates):
        import pandas as pd

        return {"600000": {"df": pd.DataFrame({"date": dates})}}

    def test_seg_bar_pool_clipped_mining_a(self):
        dates = ["2016-09-01", "2016-10-02", "2016-10-03", "2016-12-30"]
        regime = {d: "做多" for d in dates}
        pool = brs._seg_bar_pool_clipped(self._pc(dates), regime, "mining_a")
        assert [t[2] for t in pool] == ["2016-09-01", "2016-10-02"]  # 截止日当日保留

    def test_seg_bar_pool_clipped_per_segment_deadlines(self):
        dates = [
            "2021-09-01",
            "2021-10-02",
            "2021-12-30",
            "2022-01-04",
            "2026-06-06",
            "2026-08-30",
        ]
        regime = {d: "做多" for d in dates}
        pool_j = brs._seg_bar_pool_clipped(self._pc(dates), regime, "judgment")
        assert [t[2] for t in pool_j] == ["2021-09-01", "2021-10-02"]
        pool_b = brs._seg_bar_pool_clipped(self._pc(dates), regime, "mining_b")
        assert [t[2] for t in pool_b] == [
            "2022-01-04",
            "2026-06-06",
        ]  # ≥段起点（2022）∧≤截止日

    def test_legacy_pool_unclipped(self):
        """legacy 路径不滤（R40 兼容）：bear_bar_pool 原样全收。"""
        dates = ["2024-08-01", "2026-08-30"]
        regime = {d: "做多" for d in dates}
        pool = brs.bear_bar_pool(self._pc(dates), regime)
        assert [t[2] for t in pool] == dates


class TestWarmGatesWarmupFilter:
    """warm_gates 预热口径（v0.359，owner review #3）：各门信号只收 date ≥ start。"""

    def test_gate_signals_filtered_to_start(self, monkeypatch):
        import pandas as pd

        from custos.research import backtest_factors as bt
        from custos.research import score_return_study as srs

        df = pd.DataFrame({"date": ["2016-12-01", "2017-01-03"]})
        monkeypatch.setattr(bt, "_load_one_bars", lambda *a, **k: df)

        def _eval(bars, **kw):
            kw["signals_out"].extend(
                [{"i": 0, "date": "2016-12-01"}, {"i": 1, "date": "2017-01-03"}]
            )
            return []

        monkeypatch.setattr(bt, "evaluate_trades", _eval)
        monkeypatch.setattr(
            srs, "asof_technical_score", lambda *a, **k: (50.0, "中", {})
        )
        out = brs.warm_gates(
            ["600000"],
            {"2017-01-03": "做多"},
            None,
            gates=["j_low"],
            count=500,
            cost_bps=25.0,
            start="2017-01-01",
            end="2017-12-31",
            warmup_bars=300,
        )
        assert [s["date"] for s in out["600000"]["gate_signals"]["j_low"]] == [
            "2017-01-03"
        ]

    def test_warm_gates_prints_time_line(self, monkeypatch, capsys):
        """TODO #92：warm_gates 末尾打 [TIME] warm 耗时行；分子=任一门有信号的票
        （per_code 含 df 加载成功但 0 信号的母体票，不能拿 len(per_code) 充数）。"""
        import pandas as pd

        from custos.research import backtest_factors as bt
        from custos.research import score_return_study as srs

        df = pd.DataFrame({"date": ["2017-01-03"]})
        monkeypatch.setattr(bt, "_load_one_bars", lambda *a, **k: df)

        def _eval(bars, **kw):
            kw["signals_out"].extend([{"i": 0, "date": "2017-01-03"}])
            return []

        monkeypatch.setattr(bt, "evaluate_trades", _eval)
        monkeypatch.setattr(
            srs, "asof_technical_score", lambda *a, **k: (50.0, "中", {})
        )
        brs.warm_gates(
            ["600000"],
            {"2017-01-03": "做多"},
            None,
            gates=["j_low"],
            count=500,
            cost_bps=25.0,
            start="2017-01-01",
            end="2017-12-31",
        )
        err = capsys.readouterr().err
        assert "[TIME] warm 2017-01-01~2017-12-31" in err
        assert "（1/1 票有信号）" in err


class TestMiningInJudgmentSegmentRejected:
    """挖掘窗 ∩ 判定段 ⇒ 拒绝（v0.361 owner review）。"""

    def test_mining_inside_judgment_segment(self):
        ap = brs._build_parser()
        args = ap.parse_args(
            [
                "--tag",
                "t",
                "--mining-start",
                "2017-01-01",
                "--mining-end",
                "2019-06-30",
                "--judgment-start",
                "2022-01-01",
                "--judgment-end",
                "2024-07-31",
            ]
        )
        with pytest.raises(SystemExit):
            brs._check_windows(args, ap)


class TestArmJudgmentCount:
    """v0.386（审计 recent#1）：臂判定窗抽样量 = 冻结门在判定窗的实际信号数。"""

    def test_reader_gets_judgment_count(self):
        from custos.research import bear_regime_study as brs

        seen: list[int] = []

        def draw(n, w):
            return [
                {"code": f"{k:06d}", "date": "2023-01-03", "i": k} for k in range(n)
            ]

        def replay(entries, params):
            return [
                {
                    "code": e["code"],
                    "entry_date": "2023-01-03",
                    "exit_date": "2023-01-09",
                    "ret": 0.01 * (k % 3 - 1),
                    "reason": "stop",
                    "risk_frac": 0.05,
                    "r_multiple": 0.2 * (k % 3 - 1),
                    "holding": 4,
                    "score": 1.0,
                }
                for k, e in enumerate(entries)
            ]

        def reader(n, params):
            seen.append(n)
            return None

        exits = [{"name": "pct5_trail08", "params": {}}]
        out = brs.run_arm(
            {"gA": 500},
            exits,
            draw,
            replay,
            5,
            judgment_reader=reader,
            judgment_counts={"gA": 37},
        )
        assert seen == [37] and out["judgment_n"] == 37
        seen.clear()
        out = brs.run_arm(
            {"gA": 500},
            exits,
            draw,
            replay,
            5,
            judgment_reader=reader,
            judgment_counts={"gA": 0},
        )
        assert seen == [] and out["judgment"] is None


class TestBatchJudgmentLine:
    """v0.392（owner 选 B）：带 --batch-id ⇒ 判定半窗判定线 = batch_threshold(pool_j, m)。"""

    def test_batch_line(self, monkeypatch, tmp_path):
        from custos.research import criteria_kit as kit
        from custos.research import judgment_batch as jb

        reg = tmp_path / "jb.jsonl"
        jb.open_batch(
            "b1",
            [{"id": f"c{k}", "config_hash": f"t:{k}", "unit": "R40"} for k in range(3)],
            path=reg,
        )
        monkeypatch.setattr(jb, "REGISTRY", reg)
        spec = _mk_per_code({"j_low": 120, "j_low_adx25": 4})
        rep = _run(spec, _replay_winner_gate, gates="j_low,j_low_adx25", batch_id="b1")
        pools = rep["pools"]
        assert pools["batch_m"] == 3
        assert pools["q95_judgment"] == kit.batch_threshold(pools["judgment"], 3)
