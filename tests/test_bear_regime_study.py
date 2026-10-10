# -*- coding: utf-8 -*-
"""bear_regime_study.py（R40 空头区间做多全栈研究终端）钉测。

锁的契约：regime 反转语义（仅空头日放行/无映射不放行）、230 格枚举、
随机臂**对等纪律**（挖掘窗选型冻结 ⇒ 判定窗读数由冻结配置产生——用
「判定窗池全负」结果级证明：若臂在判定窗重选，池不可能全负）+
**预算对等**（v0.307：每臂逐门 n_g 抽样复刻 top 的 max-of-230 选型，
非单抽一个 n × 5 档）、C1 不过=untested、C2 三态（池未建=None⇒
provisional 不 falsified）、C3 无可扰参数轴=not_applicable 不空转
不放行（v0.307）、CLI 护栏、产物自含。全合成注入，不发真实加载。
"""

from __future__ import annotations

import argparse

import pytest

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
        assert rep["window_usage"]["k"] == 1  # 判定窗台账（v0.321）

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


class TestCliAndProduct:
    def test_pre2019_overlap_rejected(self):
        ap = brs._build_parser()
        args = ap.parse_args(["--tag", "t", "--mining-start", "2015-01-01"])
        with pytest.raises(SystemExit):
            fes._check_windows(args, ap)

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
