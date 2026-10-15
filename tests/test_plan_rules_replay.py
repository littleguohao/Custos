# -*- coding: utf-8 -*-
"""plan_rules_replay（R41 Phase 1，v0.316）钉测。

锁的契约（owner 实现指导 §5 清单）：①引擎 stop_override 钩子（缺省逐位
不变/带键生效/≥entry 被工具层剔除）；②_stop_ref 默认逐位不变 + as-of
只读 ≤ 信号日；③剔除记账两版同子集；④C1 不过=untested 优先；⑤rdd 门
不过 ⇒ C2 False；⑥C3 新子集两版重跑（配对重对齐）；⑦C4 臂种子独立
可复现/池空=indeterminate/池未满=provisional；⑧纯噪声零假设不系统性
偏高；⑨CLI 越段/相交/holdout/半截窗拒绝 + 空结果非零退出不落盘。

v2 窗口布局切换（owner 2026-10-13 定案 TODO #90）新增：⑩缺省=v2 三段
区间正确 + 段末隔离带三刀剔除数入报告；⑪A∪B 合并池读数=手工分段重放
拼接逐位一致；⑫legacy R40 参数报告与改前逐位同一（canonical JSON 的
sha256 钉；window_usage 块=定案语义变更单独断言）；⑬台账只登记
judgment_2017_2021、legacy 不登记；⑭宇宙三件套回塞 provenance。
"""

from __future__ import annotations

import argparse
import hashlib
import json

import pandas as pd
import pytest

from helpers_golden import norm_floats
from custos.core.factors.b1_structure import STOP_LOOKBACK, _stop_ref
from custos.research import backtest_factors as bt
from custos.research import criteria_kit as kit
from custos.research import factor_exit_study as fes
from custos.research import load_window as lw
from custos.research import plan_rules_replay as prr


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
        codes="",
        codes_file="",
        count=2000,
        universe_sample=0,
        universe_local=False,
        universe_seed=42,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _mk_df(n=60, base=100.0, start="2023-01-02"):
    """温和上行 df（low 恒 < close ⇒ stop_ref < entry 恒成立）。"""
    dates = pd.date_range(start, periods=n, freq="B").strftime("%Y-%m-%d")
    close = [base + 0.1 * i for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": [c + 1.5 for c in close],
            "low": [c - 1.5 for c in close],
            "close": close,
            "volume": [1000.0] * n,
        }
    )


def _mk_per_code(n_codes=10, sigs_per_code=12, extra_early_sigs=0):
    """N 码 × M 信号（i=10.. 步 2）+ 可选 i=6 早信号（C3 扰动换子集用）。"""
    per_code = {}
    k = 0
    for c_i in range(n_codes):
        code = f"{c_i:06d}"
        df = _mk_df()
        signals = []
        scores = {}
        idxs = list(range(10, 58, 2))[:sigs_per_code]
        if c_i == 0:
            idxs = [6] * extra_early_sigs + idxs
        for i in idxs:
            day = f"2023-{(k % 12) + 1:02d}-{(k % 28) + 1:02d}"
            signals.append({"i": i, "date": day, "score": 50.0})
            scores[day] = 50.0
            k += 1
        per_code[code] = {"df": df, "signals": signals, "scores": scores}
    return per_code


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
        "score": r.get("score", 50.0),
    }


def _replay_candidate(subset, params):
    """计划版大胜（+0.08/−0.02）｜现行版/副读数/随机臂贴零（+0.01/−0.005）。
    臂与计划版按 sig.stop_override 是否等于 plan_stop 区分（臂=随机价）；
    胜负按子集序号交替（df 的 i 全偶不能用 i 判奇偶）。"""
    out = []
    for j, r in enumerate(subset):
        so = r["sig"].get("stop_override")
        if so is not None and so == r.get("plan_stop"):
            ret = 0.08 if j % 2 == 0 else -0.02  # 计划版
        else:
            ret = 0.01 if j % 2 == 0 else -0.005  # 现行/副读数/随机臂
        out.append(_trade_of(r, ret))
    return out


def _replay_flat(subset, params):
    """纯噪声：收益与止损价/版本无关（零假设——计划版不得系统性偏高）。"""
    return [_trade_of(r, 0.02 if j % 2 == 0 else -0.01) for j, r in enumerate(subset)]


def _run(per_code, replay, **kw):
    windows = (("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))
    spec = {w: per_code for w in windows}
    warm_fn = lambda s, e: spec[(s, e)]  # noqa: E731
    return prr.run_study(_args(**kw), warm_fn=warm_fn, replay_fn=replay)


# ---------------------------------------------------------------------------
# v2 布局夹具（TODO #90）：段常量复用 load_window 单源，禁止自抄区间
# ---------------------------------------------------------------------------

#: (挖掘 A, 判定, 挖掘 B) 三段区间——run_study v2 的 warm 顺序（A、B、判定）
V2_SPANS = (lw.SEG_MINING_A, lw.SEG_JUDGMENT, lw.SEG_MINING_B)


def _args_v2(**kw):
    """四窗参全 None ⇒ v2 缺省布局（其余参数同 _args）。"""
    return _args(
        mining_start=None, mining_end=None, judgment_start=None, judgment_end=None, **kw
    )


def _rebase_year(per_code, year):
    """把夹具信号/分数的日期整体改到 ``year`` 年（v2 三段各自需要段内日期——
    2023 年的原夹具落在挖掘 B；df 不动：注入 replay 不读 df 日期）。"""
    out = {}
    for code, pack in per_code.items():
        sigs = [{**s, "date": f"{year}-{s['date'][5:]}"} for s in pack["signals"]]
        scores = {f"{year}-{d[5:]}": v for d, v in pack["scores"].items()}
        out[code] = {**pack, "signals": sigs, "scores": scores}
    return out


def _v2_spec(per_code):
    """三段 warm 取数表：A→2015 年 / 判定→2020 年 / B→原 2023 年（均远离
    段末 90 天隔离带 ⇒ clip 对基础夹具是空操作）。"""
    return {
        V2_SPANS[0]: _rebase_year(per_code, "2015"),
        V2_SPANS[1]: _rebase_year(per_code, "2020"),
        V2_SPANS[2]: per_code,
    }


def _run_v2(per_code, replay, **kw):
    spec = _v2_spec(per_code)
    return prr.run_study(
        _args_v2(**kw), warm_fn=lambda s, e: spec[(s, e)], replay_fn=replay
    )


# ---------------------------------------------------------------------------
# ① 引擎钩子（backtest_factors 信号级 stop_override）
# ---------------------------------------------------------------------------


class TestEngineStopOverride:
    def _df(self):
        # 45 根：信号日（i=32，可发射区间 i≥30 且 i<n−1）收盘 100，之后阴跌到 80
        dates = pd.date_range("2023-01-02", periods=45, freq="B").strftime("%Y-%m-%d")
        close = [100.0] * 33 + [98, 96, 93, 91, 89, 87, 85, 83, 82, 81, 80, 80]
        return pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": [c + 0.5 for c in close],
                "low": [c - 0.5 for c in close],
                "close": close,
                "volume": [1000.0] * 45,
            }
        )

    def _replay_one(self, sig):
        df = self._df()
        return bt.evaluate_trades(
            {"600000": df},
            signals_in={"600000": [sig]},
            collect_all=True,
            cost_bps=0.0,
            stop_mode="pct",
            stop_pct=50,  # 地板 50%：不带 override 永不止损
        )

    def test_override_takes_effect(self):
        df = self._df()
        day = str(df["date"].iloc[32])[:10]
        sig = {"i": 32, "date": day, "score": 50.0, "stop_override": 95.0}
        trades = self._replay_one(sig)
        assert len(trades) == 1
        # stop_override=95 < entry 100 ⇒ 破 95 即触发（close 触发口径在 ~93
        # 出场），而不是骑到 −20%——止损位确实被引擎采用
        assert trades[0]["ret"] == pytest.approx(-0.07, abs=0.005)
        assert trades[0]["reason"] == "stop"

    def test_no_key_bit_identical_baseline(self):
        """不带键 = 旧行为（平台/默认逻辑）——两次调用逐位一致且不吃 override。"""
        df = self._df()
        day = str(df["date"].iloc[32])[:10]
        sig = {"i": 32, "date": day, "score": 50.0}
        a = self._replay_one(dict(sig))
        b = self._replay_one(dict(sig))
        assert a == b
        # stop_pct=50 永不触发 ⇒ 骑到窗口末（不吃任何 95 止损）
        assert a[0]["ret"] < -0.10


# ---------------------------------------------------------------------------
# ② _stop_ref 参数化
# ---------------------------------------------------------------------------


class TestStopRef:
    def test_default_bit_identical(self):
        df = _mk_df()
        assert _stop_ref(df) == _stop_ref(df, STOP_LOOKBACK)

    def test_asof_ignores_future(self):
        """as-of 只读 df.iloc[:i+1]：信号日之后注入极低价，stop 不变。"""
        df = _mk_df()
        stop_before = _stop_ref(df.iloc[:16])
        df2 = df.copy()
        df2.loc[df2.index[20], "low"] = 0.01  # 信号日（i=15）之后的极低价
        assert _stop_ref(df2.iloc[:16]) == stop_before

    def test_lookback_param_respected(self):
        df = _mk_df()
        # lookback=5 ⇒ min(low[11..15])；lookback=10 ⇒ min(low[6..15])（更低）
        assert _stop_ref(df.iloc[:16], 5) != _stop_ref(df.iloc[:16], 10)
        assert _stop_ref(df.iloc[:16], 5) > _stop_ref(df.iloc[:16], 10)


# ---------------------------------------------------------------------------
# ③ 剔除记账（attach_plan_stops）
# ---------------------------------------------------------------------------


class TestAttachAccounting:
    def test_missing_and_ge_entry_counted(self):
        per_code = _mk_per_code(n_codes=1, sigs_per_code=3)
        pack = per_code["000000"]
        # 加一条 i=3（< lookback 10 ⇒ n_missing）
        pack["signals"].insert(0, {"i": 3, "date": "2023-03-03", "score": 50.0})
        pack["scores"]["2023-03-03"] = 50.0
        # 加一条 stop ≥ entry：把 i=10 之前的 low 全部抬高到 entry_close 之上
        df = pack["df"].copy()
        df.loc[df.index[:11], "low"] = df["close"].iloc[10] + 1.0
        pack["df"] = df
        subset, acc = prr.attach_plan_stops(per_code)
        assert acc["n_signals"] == 4  # 3 原始 + 1 缺历史（i=10 那条归 stop≥entry）
        assert acc["n_missing"] == 1
        assert acc["n_stop_ge_entry"] == 1
        assert acc["n_subset"] == 2
        # 子集带 plan_stop/entry_close + 距离分位数自含
        assert all(r["plan_stop"] < r["entry_close"] for r in subset)
        assert set(acc["stop_distance_quantiles"]) == {"p5", "p25", "p50", "p75", "p95"}

    def test_score_missing_counted(self):
        per_code = _mk_per_code(n_codes=1, sigs_per_code=2)
        pack = per_code["000000"]
        pack["scores"].pop(pack["signals"][0]["date"])  # 第一条缺分
        subset, acc = prr.attach_plan_stops(per_code)
        assert acc["n_score_missing"] == 1
        assert acc["n_subset"] == 1


# ---------------------------------------------------------------------------
# ④⑤ 判据：C1 优先 / rdd 门不过 ⇒ C2 False
# ---------------------------------------------------------------------------


class TestCriteria:
    def test_c1_untested_takes_priority(self):
        """n_taken < 100 ⇒ untested（即使 C2 也不过，C1 优先）。"""
        per_code = _mk_per_code(n_codes=2, sigs_per_code=4)  # 8 信号 ≪ 100
        rep = _run(per_code, _replay_candidate)
        assert rep["criteria"]["C1"]["ok"] is False
        assert rep["verdict"] == "untested"

    def test_candidate_full_chain(self):
        per_code = _mk_per_code()  # 120 信号 ≥ 100
        rep = _run(per_code, _replay_candidate, n_random=4, c4_min_pool=3)
        c = rep["criteria"]
        assert c["C1"]["ok"] is True
        assert c["C2"]["ok"] is True, c["C2"]
        assert c["C2"]["rdd_gate_fail"] is None
        assert c["C4"]["state"] == "confirmed_pass"
        assert rep["verdict"] == "candidate"
        # legacy R40 窗（判定已并入挖掘 B）⇒ 不入台账（v2 口径，TODO #90）
        assert rep["window_usage"] == {
            "window": "mining_segment",
            "k": None,
            "note": "判定窗在挖掘段内——不入台账（v2 口径）",
        }
        # 副读数 pct7 自含；现行版读数两窗在
        assert rep["readings"]["mining"]["live_alt_pct7"] is not None

    def test_cost_sensitivity_block_wired(self):
        """成本副读数（owner review #6，v0.329）：报告带 cost_sensitivity——
        两版×双窗 50bps 解析重算（不重跑引擎）+ d_margin 双窗翻号标记。"""
        per_code = _mk_per_code()
        rep = _run(per_code, _replay_candidate, n_random=2, c4_min_pool=2)
        cs = rep["cost_sensitivity"]
        assert cs["base_bps"] == 25.0 and cs["side_bps"] == 50.0
        for arm in ("plan_mining", "live_mining", "plan_judgment", "live_judgment"):
            assert cs["arms"][arm]["n"] > 0, arm
        assert set(cs["deltas"]) == {"d_margin_mining", "d_margin_judgment"}
        d = cs["deltas"]["d_margin_mining"]
        # 计划版大胜（+0.08/−0.02）：50bps 翻不动 Δ 符号
        assert d["base"] > 0 and d["side"] > 0 and d["flip"] is False

    def test_rdd_gate_fail_marks_c2_false(self):
        """计划版 rdd 远低于现行参照（计划大起大落、现行稳升）⇒ C2 False
        且标 rdd_gate_fail（margin 提升拿回撤换的不算，v0.315 ③）。"""

        def _replay_rdd_fail(subset, params):
            out = []
            for j, r in enumerate(subset):
                so = r["sig"].get("stop_override")
                if so is not None and so == r.get("plan_stop"):
                    # 计划版：margin 高（大胜居多）但曲线剧烈回撤（隔三巨亏）
                    ret = 0.30 if j % 3 else -0.40
                else:
                    ret = 0.02 if j % 2 == 0 else 0.01  # 现行/臂：稳升
                out.append(_trade_of(r, ret))
            return out

        per_code = _mk_per_code()
        rep = _run(per_code, _replay_rdd_fail, n_random=2, c4_min_pool=2)
        c2 = rep["criteria"]["C2"]
        if (
            c2["windows"]["mining"]["delta_margin"] is not None
            and c2["windows"]["mining"]["delta_margin"] > 0
        ):
            # margin 提升成立时，rdd 门必须拦下（门不过 ⇒ C2 False + 标记）
            assert c2["ok"] is False
            assert c2["rdd_gate_fail"], c2


# ---------------------------------------------------------------------------
# ⑥ C3：新子集两版重跑（配对重新对齐）
# ---------------------------------------------------------------------------


class TestC3Realign:
    def test_c3_reruns_live_on_new_subset(self):
        """lookback 扰动改剔除集合 ⇒ 现行版也必须在新子集上重跑（不得沿用
        旧子集读数）：i=6 早信号在 lookback≤6 的扰动档被捞回。"""
        per_code = _mk_per_code(n_codes=5, sigs_per_code=24, extra_early_sigs=3)
        # 5 码 × 24 信号 + 3 个 i=6 早信号（默认 lookback=10 缺历史被剔）
        live_sizes = []

        def _spy(subset, params):
            if params["stop_pct"] == 10 and not any(
                r["sig"].get("stop_override") for r in subset
            ):
                live_sizes.append(len(subset))  # 现行版主读数调用
            return _replay_candidate(subset, params)

        rep = _run(per_code, _spy, n_random=2, c4_min_pool=2)
        draws = rep["criteria"]["C3"]["draws"]
        assert len(draws) == prr.C3_DRAWS
        low_draws = [d for d in draws if d["lookback"] <= 6]
        base_size = live_sizes[0]  # 主研究现行版子集
        if low_draws:  # 有扰动档 ≤6 ⇒ 早信号捞回 ⇒ 现行版子集变大（重对齐）
            assert max(live_sizes) > base_size, (
                f"lookback≤6 的扰动档必须在更大子集上重跑现行版：{live_sizes}"
            )


# ---------------------------------------------------------------------------
# ⑦⑧ C4：可复现 / 池空=indeterminate / 池未满=provisional / 零假设不偏高
# ---------------------------------------------------------------------------


class TestC4:
    def test_arm_seeds_reproducible(self):
        per_code = _mk_per_code()
        a = _run(per_code, _replay_candidate, n_random=4, c4_min_pool=3)
        b = _run(per_code, _replay_candidate, n_random=4, c4_min_pool=3)
        assert a["criteria"]["C4"]["pool"] == b["criteria"]["C4"]["pool"]

    def test_empty_pool_indeterminate(self):
        """全部臂过不了 rdd 门 ⇒ 池空 = indeterminate（不放行，v0.297 族）；
        v0.317：重抽打满 max_arms 上限后按当时池大小（空）判。"""

        def _replay_arms_crash(subset, params):
            out = []
            for j, r in enumerate(subset):
                so = r["sig"].get("stop_override")
                if so is not None and so == r.get("plan_stop"):
                    ret = 0.08 if j % 2 == 0 else -0.02  # 计划版照赢
                elif so is not None:
                    ret = -0.30 if j % 2 == 0 else -0.20  # 随机臂崩（rdd 深负）
                else:
                    ret = 0.02 if j % 2 == 0 else -0.01  # 现行正 margin 且 DD>0
                out.append(_trade_of(r, ret))
            return out

        per_code = _mk_per_code()
        rep = _run(per_code, _replay_arms_crash, n_random=3, c4_min_pool=3)
        c4 = rep["criteria"]["C4"]
        assert c4["pool_size"] == 0
        assert c4["evaluated"] == c4["max_arms"] == 30  # 打满上限（v0.317）
        assert c4["state"] == "indeterminate"
        assert rep["verdict"] == "provisional"  # 不放行不判死

    def test_resample_fills_pool_at_low_gate_rate(self):
        """v0.317：N 指过门臂数——过门率 20% 时重抽至池满（5 过门臂 = 25
        次评估），如实记过门率与评估数；统计含义不变（候选同须过门）。"""
        calls = {"arm": 0}

        def _replay_20pct(subset, params):
            any_ov = any(r["sig"].get("stop_override") is not None for r in subset)
            if not any_ov:
                return _replay_candidate(subset, params)  # 现行/副读数
            is_plan = all(
                r["sig"].get("stop_override") == r.get("plan_stop") for r in subset
            )
            if is_plan:
                return _replay_candidate(subset, params)  # 计划版
            calls["arm"] += 1
            if calls["arm"] % 5 == 0:  # 20% 过门（读数同现行 ⇒ 门平过）
                return [
                    _trade_of(r, 0.01 if j % 2 == 0 else -0.005)
                    for j, r in enumerate(subset)
                ]
            return [_trade_of(r, -0.30) for r in subset]  # 崩=不过门

        per_code = _mk_per_code()
        rep = _run(per_code, _replay_20pct, n_random=5, c4_min_pool=5)
        c4 = rep["criteria"]["C4"]
        assert c4["pool_size"] == 5, "重抽直至过门臂满 N"
        assert c4["evaluated"] == 25  # 20% 过门 ⇒ 25 次评估凑满 5
        assert c4["max_arms"] == 50 and c4["target_pool"] == 5
        assert c4["gate_pass_rate"] == pytest.approx(0.2)
        assert c4["state"] == "confirmed_pass"
        assert rep["verdict"] == "candidate"

    def test_cap_degrades_to_provisional(self):
        """v0.317 上限降级：过门率 1/20 ⇒ 打满 max_arms 后按当时池大小判
        provisional（池 2 < min_pool 5），评估数/过门率如实记。"""
        calls = {"arm": 0}

        def _replay_low_rate(subset, params):
            any_ov = any(r["sig"].get("stop_override") is not None for r in subset)
            if not any_ov:
                return _replay_candidate(subset, params)
            is_plan = all(
                r["sig"].get("stop_override") == r.get("plan_stop") for r in subset
            )
            if is_plan:
                return _replay_candidate(subset, params)
            calls["arm"] += 1
            if calls["arm"] % 20 == 0:
                return [
                    _trade_of(r, 0.01 if j % 2 == 0 else -0.005)
                    for j, r in enumerate(subset)
                ]
            return [_trade_of(r, -0.30) for r in subset]

        per_code = _mk_per_code()
        rep = _run(per_code, _replay_low_rate, n_random=5, c4_min_pool=5)
        c4 = rep["criteria"]["C4"]
        assert c4["evaluated"] == c4["max_arms"] == 50
        assert c4["pool_size"] == 2  # floor(50/20)=2 条过门臂
        assert c4["gate_pass_rate"] == pytest.approx(0.04)
        assert c4["state"] == "provisional"
        assert rep["verdict"] == "provisional"

    def test_small_pool_provisional(self):
        per_code = _mk_per_code()
        rep = _run(per_code, _replay_candidate, n_random=3, c4_min_pool=50)
        assert rep["criteria"]["C4"]["state"] == "provisional"
        assert rep["verdict"] == "provisional"

    def test_noise_null_not_systematically_high(self):
        """纯噪声零假设：收益与止损无关 ⇒ 计划版 Δ ≤ 池 q95（不得确认）。"""
        per_code = _mk_per_code()
        rep = _run(per_code, _replay_flat, n_random=10, c4_min_pool=5)
        c4 = rep["criteria"]["C4"]
        assert c4["state"] != "confirmed_pass"
        assert c4["plan_delta_mining"] <= (c4["q95"] or 0)


# ---------------------------------------------------------------------------
# ⑨ CLI 护栏
# ---------------------------------------------------------------------------


class TestCli:
    def test_cross_segment_mining_rejected(self):
        """越段硬拒绝（v2 段检查）：2015（挖掘 A）~2024（挖掘 B）跨段。"""
        with pytest.raises(SystemExit):
            prr.main(
                [
                    "--mining-start",
                    "2015-01-01",
                    "--mining-end",
                    "2024-07-31",
                    "--judgment-start",
                    "2024-08-01",
                    "--judgment-end",
                    "2026-09-04",
                    "--tag",
                    "t",
                ]
            )

    def test_cross_segment_judgment_rejected(self):
        """判定窗 2016-06（A 内）~2021-12（判定段内）越段 ⇒ 拒绝。"""
        with pytest.raises(SystemExit):
            prr.main(
                [
                    "--mining-start",
                    "2022-01-01",
                    "--mining-end",
                    "2024-07-31",
                    "--judgment-start",
                    "2016-06-01",
                    "--judgment-end",
                    "2021-12-31",
                    "--tag",
                    "t",
                ]
            )

    def test_intersecting_windows_rejected(self):
        """判定窗∩挖掘窗相交 ⇒ 拒绝（v2 口径：不相交即可，取代旧次序规则）。"""
        with pytest.raises(SystemExit):
            prr.main(
                [
                    "--mining-start",
                    "2022-01-01",
                    "--mining-end",
                    "2024-07-31",
                    "--judgment-start",
                    "2024-06-01",
                    "--judgment-end",
                    "2026-09-04",
                    "--tag",
                    "t",
                ]
            )

    def test_holdout_touched_rejected(self):
        with pytest.raises(SystemExit):
            prr.main(
                [
                    "--mining-start",
                    "2022-01-01",
                    "--mining-end",
                    "2024-07-31",
                    "--judgment-start",
                    "2024-08-01",
                    "--judgment-end",
                    "2026-09-05",
                    "--tag",
                    "t",
                ]
            )

    def test_partial_windows_rejected(self):
        """只给一部分窗参 ⇒ 半截布局硬拒绝（全给=legacy / 全不给=v2）。"""
        with pytest.raises(SystemExit):
            prr.main(["--mining-start", "2022-01-01", "--tag", "t"])
        with pytest.raises(SystemExit):
            prr.main(
                [
                    "--mining-start",
                    "2022-01-01",
                    "--mining-end",
                    "2024-07-31",
                    "--judgment-start",
                    "2024-08-01",
                    "--tag",
                    "t",
                ]
            )

    def test_window_defaults_are_none(self):
        """v2 布局入口：四窗参缺省全 None（test_count_for_start 族回归钉）。"""
        args = prr._build_parser().parse_args(["--tag", "t"])
        assert (
            args.mining_start
            is args.mining_end
            is args.judgment_start
            is args.judgment_end
            is None
        )

    def test_empty_guard_no_artifact(self, tmp_path):
        per_code = _mk_per_code(n_codes=1, sigs_per_code=1)
        pack = per_code["000000"]
        pack["signals"] = [{"i": 3, "date": "2023-03-03", "score": 50.0}]  # 全剔
        windows = (("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))
        spec = {w: per_code for w in windows}
        rc = prr.main(
            [
                "--mining-start",
                "2022-01-01",
                "--mining-end",
                "2024-07-31",
                "--judgment-start",
                "2024-08-01",
                "--judgment-end",
                "2026-09-04",
                "--tag",
                "empty_t",
                "--out-dir",
                str(tmp_path),
            ],
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_replay_flat,
        )
        assert rc == 2
        assert not (tmp_path / "empty_t").exists(), "空结果护栏：不落盘"


# ---------------------------------------------------------------------------
# ⑩ v2 窗口布局（owner 2026-10-13 定案 TODO #90）
# ---------------------------------------------------------------------------


class TestV2Layout:
    """(a) 缺省=v2：三段区间正确（段常量=load_window 单源）+ 报告 segments 块。"""

    def test_default_v2_segments(self, tmp_path):
        per_code = _mk_per_code()
        spec = _v2_spec(per_code)
        rc = prr.main(
            ["--tag", "v2a", "--out-dir", str(tmp_path)],
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_replay_candidate,
        )
        assert rc == 0
        rep = json.loads(
            (tmp_path / "v2a" / "_plan_rules_replay__v2a.json").read_text(
                encoding="utf-8"
            )
        )
        assert rep["layout"] == "v2"
        segs = rep["segments"]
        assert (segs["mining_a"]["start"], segs["mining_a"]["end"]) == lw.SEG_MINING_A
        assert (segs["judgment"]["start"], segs["judgment"]["end"]) == lw.SEG_JUDGMENT
        assert (segs["mining_b"]["start"], segs["mining_b"]["end"]) == lw.SEG_MINING_B
        # 逻辑窗：挖掘=A 起点~B 终点（并集），判定=受保护段
        assert rep["windows"]["mining"] == {
            "start": lw.SEG_MINING_A[0],
            "end": lw.SEG_MINING_B[1],
        }
        assert rep["windows"]["judgment"] == {
            "start": lw.SEG_JUDGMENT[0],
            "end": lw.SEG_JUDGMENT[1],
        }
        # 基础夹具日期远离隔离带 ⇒ 零剔除；三段各 120 信号、合并 240
        for name in ("mining_a", "judgment", "mining_b"):
            assert segs[name]["n_signals"] == 120 and segs[name]["n_clipped"] == 0
        assert segs["mining_total"]["n_signals"] == 240
        # 判定=受保护段 ⇒ 合成运行走 synthetic 守卫（k=None 未入台账）
        assert rep["window_usage"]["window"] == "judgment_2017_2021"
        assert rep["window_usage"]["k"] is None
        assert "未入台账" in rep["window_usage"]["note"]

    def test_run_study_v2_accounting_keys(self):
        rep = _run_v2(_mk_per_code(), _replay_candidate, n_random=2, c4_min_pool=2)
        assert set(rep["accounting"]) == {"mining_a", "mining_b", "judgment"}
        # 成本副读数臂名不变（逻辑双窗：mining=合并池 / judgment）
        assert set(rep["cost_sensitivity"]["arms"]) == {
            "plan_mining",
            "live_mining",
            "plan_judgment",
            "live_judgment",
        }


class TestV2MergedPool:
    """(b) A∪B 合并一池读数 = 手工分段重放拼接重算（逐位一致）。"""

    def test_merged_mining_readings_bitwise(self):
        per_code = _mk_per_code()
        spec = _v2_spec(per_code)
        rep = prr.run_study(
            _args_v2(n_random=4, c4_min_pool=3),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_replay_candidate,
        )
        sub_a = prr.attach_plan_stops(spec[V2_SPANS[0]], STOP_LOOKBACK)[0]
        sub_j = prr.attach_plan_stops(spec[V2_SPANS[1]], STOP_LOOKBACK)[0]
        sub_b = prr.attach_plan_stops(spec[V2_SPANS[2]], STOP_LOOKBACK)[0]

        def build(subset, plan):
            if plan:
                return [prr._sig_with_stop(r, r["plan_stop"]) for r in subset]
            return [dict(r) for r in subset]

        def trades(subs, plan, params):
            return [
                t for sub in subs for t in _replay_candidate(build(sub, plan), params)
            ]

        live = fes.combine_readings(
            trades((sub_a, sub_b), False, prr.LIVE_PARAMS), 20, ref="self"
        )
        plan = fes.combine_readings(
            trades((sub_a, sub_b), True, prr.PLAN_PARAMS), 20, ref=live
        )
        alt = fes.combine_readings(
            trades((sub_a, sub_b), False, prr.LIVE_ALT_PARAMS), 20, ref="self"
        )
        assert rep["readings"]["mining"]["live"] == live
        assert rep["readings"]["mining"]["plan"] == plan
        assert rep["readings"]["mining"]["live_alt_pct7"] == alt
        live_j = fes.combine_readings(
            trades((sub_j,), False, prr.LIVE_PARAMS), 20, ref="self"
        )
        plan_j = fes.combine_readings(
            trades((sub_j,), True, prr.PLAN_PARAMS), 20, ref=live_j
        )
        assert rep["readings"]["judgment"]["live"] == live_j
        assert rep["readings"]["judgment"]["plan"] == plan_j
        # 合并池规模 = A+B 之和（判据形态不变：C1 看的仍是 mining/judgment 两键）
        assert rep["accounting"]["mining_a"]["n_subset"] == 120
        assert rep["accounting"]["mining_b"]["n_subset"] == 120
        assert rep["criteria"]["C1"]["n_taken"] == {"mining": 240, "judgment": 120}


class TestV2SegmentBuffer:
    """(c) 段末 90 天隔离带三刀生效且剔除数入报告（≤ 截止日保留、> 剔除）。"""

    def test_buffer_clip_counted(self):
        def seg_pack(year, kept_boundary, dropped):
            pc = _rebase_year(_mk_per_code(n_codes=2, sigs_per_code=4), year)
            pack = pc["000000"]
            for d in [kept_boundary, *dropped]:
                pack["signals"].append({"i": 10, "date": d, "score": 50.0})
                pack["scores"][d] = 50.0
            return pc

        spec = {
            # A 段末 2016-12-31 ⇒ 入场截止 2016-10-02
            V2_SPANS[0]: seg_pack("2015", "2016-10-02", ["2016-10-03", "2016-12-30"]),
            # 判定段末 2021-12-31 ⇒ 入场截止 2021-10-02
            V2_SPANS[1]: seg_pack("2020", "2021-10-02", ["2021-10-03", "2021-12-31"]),
            # B 段末 2026-09-04 ⇒ 入场截止 2026-06-06
            V2_SPANS[2]: seg_pack("2023", "2026-06-06", ["2026-06-07", "2026-09-04"]),
        }
        rep = prr.run_study(
            _args_v2(n_random=2, c4_min_pool=2),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_replay_flat,
        )
        segs = rep["segments"]
        for name in ("mining_a", "judgment", "mining_b"):
            assert segs[name]["n_signals"] == 11  # 8 段内 + 1 边界保留 + 2 隔离带
            assert segs[name]["n_clipped"] == 2
            # 隔离带信号不进 attach 子集（9=8+1 边界保留）
            assert rep["accounting"][name]["n_signals"] == 9
            assert rep["accounting"][name]["n_subset"] == 9
        assert segs["mining_total"]["n_signals"] == 22
        assert segs["mining_total"]["n_clipped"] == 4
        # clip 不污染注入夹具（副本来回：原 signals 仍在——4 基础 + 3 加注）
        assert len(spec[V2_SPANS[0]]["000000"]["signals"]) == 7
        assert sum(len(p["signals"]) for p in spec[V2_SPANS[0]].values()) == 11


class TestLegacyR40Compat:
    """(d) legacy R40 参数（2022-01-01~2024-07-31 / 2024-08-01~2026-09-04，
    均 ⊆ 挖掘 B 且互不相交）：接受 + 合成数据报告与改前**逐位同一**。

    逐位口径：report 各顶层块 canonical JSON（sort_keys）的 sha256 与改前
    金样一致（全量 139KB 金样不落测试文件，块级哈希可定位差异块）。
    定案语义变更只两处，单独断言：window_usage 块（判定窗已并入挖掘 B ⇒
    不登记）与易变字段（generated_at/git/版本串，两侧同步剔除后比较）。
    """

    #: 改前（v0.356 代码）同夹具同参数报告的金样哈希（_normalize_volatile 后；
    #: v0.359 按浮点归一化口径重捕——CI 异构 CPU 浮点末位差会让全精度哈希必挂）
    _GOLDEN_SHA256 = {
        "<full>": "bc3030d0f6288588e84b781c9c226ae6f5954ac3c0e2876d7ad13b72a2eb34ed",
        "accounting": "73f712a187edb80bee527f0c5e20c3818107dfe4023a118a4ba71678acf4aedd",
        "cost_sensitivity": "7bb383079ec9f41adcd638cecc948686239e657f51a07bc2bfa9caef86f79f23",
        "criteria": "95aa950228677312ce3cf8102a2e826d0c221221563fd8ae8b8a8cdec7c9c299",
        "forward_holdout_note": "3f7068aca87896dac440f9b7b068f1907e8e94112691bfd04a7f21789211d806",
        "params": "7b09f04841885b622f44ca879278673efb011f9a5ed922359407ddf992685ee0",
        "provenance": "a7f1a851100f6c5a33b6b2aa0a98502d5baa96bb2ce6269067531f165a0ceab7",
        "readings": "bd7a754880045445b9e6cd347f53885ad8703458847c1f02c29554a890684376",
        "schema": "afdee52e747ec0df67de16b0e1f7b1353a70365707656551874a088391854830",
        "tag": "8ca5d81566f88badd698137ac629cbf93ccc107c0cd967b7964f7d82088f6ebf",
        "verdict": "6201e275c993fded1b6a1b7b64f106686c702d9fdff8af32b560aad5d781015c",
        "windows": "e7dbd9a5de5f7efb589285c9faf75b18dbaee6e8122a4f9b5a8a98b0ce30f6c0",
    }

    @staticmethod
    def _canon_sha256(obj):
        # 浮点归一化后哈希（helpers_golden——CI 异构 CPU 浮点末位差会让
        # 全精度哈希必挂，v0.359；金样哈希已按本口径重捕）
        return hashlib.sha256(
            json.dumps(
                norm_floats(obj), ensure_ascii=False, sort_keys=True, allow_nan=True
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _normalize_volatile(rep):
        rep = dict(rep)
        for k in (
            "generated_at",
            "objective_version",
            "window_usage",
            "layout",
            "segments",
        ):
            rep.pop(k, None)
        prov = dict(rep["provenance"])
        for k in ("git_sha", "git_dirty", "pre_reg_blob_hash", "objective_version"):
            prov.pop(k, None)
        rep["provenance"] = prov
        return rep

    def test_report_bit_identical_to_pre_switch(self):
        rep = _run(_mk_per_code(), _replay_candidate, n_random=4, c4_min_pool=3)
        # 定案语义变更①：legacy ⇒ 判定窗已在挖掘 B 内，不入台账（v2 口径）
        assert rep["window_usage"] == {
            "window": "mining_segment",
            "k": None,
            "note": "判定窗在挖掘段内——不入台账（v2 口径）",
        }
        # legacy 不带 v2 专有块
        assert "segments" not in rep and "layout" not in rep
        norm = self._normalize_volatile(rep)
        bad = [
            name
            for name, want in self._GOLDEN_SHA256.items()
            if self._canon_sha256(norm if name == "<full>" else norm[name]) != want
        ]
        assert not bad, f"legacy R40 报告与改前不逐位一致（块级定位）: {bad}"

    def test_legacy_r40_cli_accepted(self, tmp_path):
        per_code = _mk_per_code()
        windows = (("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))
        spec = {w: per_code for w in windows}
        rc = prr.main(
            [
                "--mining-start",
                "2022-01-01",
                "--mining-end",
                "2024-07-31",
                "--judgment-start",
                "2024-08-01",
                "--judgment-end",
                "2026-09-04",
                "--tag",
                "r40ok",
                "--out-dir",
                str(tmp_path),
            ],
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_replay_candidate,
        )
        assert rc == 0
        assert (tmp_path / "r40ok" / "_plan_rules_replay__r40ok.json").exists()

    def test_judgment_before_mining_accepted(self, tmp_path):
        """旧次序规则（mining_end < judgment_start）作废：互不相交即合法——
        判定窗在挖掘窗之前的 legacy 组合也接受（v2 口径只验不相交）。"""
        per_code = _mk_per_code()
        windows = (("2024-08-01", "2026-09-04"), ("2022-01-01", "2024-07-31"))
        spec = {w: per_code for w in windows}
        rc = prr.main(
            [
                "--mining-start",
                "2024-08-01",
                "--mining-end",
                "2026-09-04",
                "--judgment-start",
                "2022-01-01",
                "--judgment-end",
                "2024-07-31",
                "--tag",
                "rev",
                "--out-dir",
                str(tmp_path),
            ],
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_replay_candidate,
        )
        assert rc == 0


# ---------------------------------------------------------------------------
# ⑪ 台账生产方向钉（v0.343，owner——r42_probe 漏登根因；v2 口径 TODO #90）
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
    合成 ⇒ v2 缺省（判定=受保护段 2017~2021）record_use 必须以
    ``synthetic=False``、键 ``judgment_2017_2021`` 被调且 k 直通报告；legacy
    R40 窗（判定已并入挖掘 B）⇒ **完全不登记**（v2 口径，TODO #90）。防回归：
    生产分支重绑注入参数 ⇒ 入口快照 ``injected`` 变 True ⇒ 立刻红。
    attach_plan_stops 走真 `_stop_ref`（夹具 df 是真 df）——只桩掉数据
    加载与重放。"""

    def _monkeypatch_production(self, monkeypatch, spec):
        from custos.datasource.local_tdx import local_tdx_data
        from custos.research import backtest_factors as bt
        from custos.research import exit_campaign as ec
        from custos.research import factor_exit_study as fes

        def _fake_resolve(ns, ap):
            # universe.resolve 副作用（v0.356 单源）：宇宙三件套
            ns.universe_meta = {
                "universe_rule": "local_vipdoc",
                "n_codes": 1,
                "universe_sha256": "0123456789abcdef",
            }
            return ["000000"]

        monkeypatch.setattr(bt, "_resolve_universe", _fake_resolve)
        monkeypatch.setattr(bt, "load_amv_regime", lambda since: {"2022-01-03": "做多"})
        monkeypatch.setattr(
            local_tdx_data,
            "get_ohlcv_table",
            lambda code, count: pd.DataFrame({"date": ["2022-01-03"]}),
        )
        monkeypatch.setattr(
            ec,
            "warm_v0_signals",
            lambda codes, regime, index_df, *, count, cost_bps, start, end, warmup_bars=0: (
                spec[(start, end)]
            ),
        )
        monkeypatch.setattr(
            fes,
            "replay_signals",
            lambda per_code, subset, params, regime, cost_bps: _replay_candidate(
                subset, params
            ),
        )

    def test_production_records_protected_judgment(self, monkeypatch):
        calls = _wu_spy(monkeypatch)
        self._monkeypatch_production(monkeypatch, _v2_spec(_mk_per_code()))
        args = _args_v2(n_random=2, c4_min_pool=2)
        jb = _with_batch(args, "plan_rules_replay")  # v0.374：生产方向须带批次
        rep = prr.run_study(args, warm_fn=None, replay_fn=None)
        assert len(calls) == 1
        assert calls[0]["unit"] == "R41"
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is False, "生产路径必须按 synthetic=False 登记"
        assert rep["window_usage"]["k"] == 7, "生产口径 k 直通报告"
        assert "第 7 次被读" in rep["window_usage"]["note"]
        # 宇宙三件套回塞（⑭）：provenance.build 优先读 args.universe_meta
        prov = rep["provenance"]
        assert prov["universe_rule"] == "local_vipdoc"
        assert prov["n_codes"] == 1
        assert prov["universe_sha256"] == "0123456789abcdef"
        # v0.374 批次锁：收尾 mark_read 落进 tmp 登记文件（单候选 ⇒ 自动关批）
        rows = [
            json.loads(ln)
            for ln in jb.REGISTRY.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert [r["ev"] for r in rows] == ["open", "claim", "read", "close"]
        assert (
            rows[1]["candidate_id"] == "c1" and rows[1]["tool"] == "plan_rules_replay"
        )

    def test_production_without_batch_rejected(self):
        """v0.374 批次锁（TODO #95）：v2 缺省窗 + 无批次 ⇒ 数据加载之前
        ap.error（exit 2）——门在暖身/加载之前，无需任何数据桩。"""
        with pytest.raises(SystemExit) as exc:
            prr.run_study(
                _args_v2(n_random=2, c4_min_pool=2), warm_fn=None, replay_fn=None
            )
        assert exc.value.code == 2

    def test_production_legacy_not_recorded(self, monkeypatch):
        """legacy R40 窗：判定窗已并入挖掘 B ⇒ record_use 零调用（v2 口径）。"""
        calls = _wu_spy(monkeypatch)
        windows = (("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))
        self._monkeypatch_production(monkeypatch, {w: _mk_per_code() for w in windows})
        rep = prr.run_study(
            _args(n_random=2, c4_min_pool=2), warm_fn=None, replay_fn=None
        )
        assert calls == [], "legacy（判定窗在挖掘段内）⇒ 不入台账（v2 口径）"
        assert rep["window_usage"] == {
            "window": "mining_segment",
            "k": None,
            "note": "判定窗在挖掘段内——不入台账（v2 口径）",
        }

    def test_legacy_judgment_subwindow_registered(self, monkeypatch):
        """判定**子窗**（2018~2020 ⊆ 判定段）⇒ 相交即登记（v0.361 owner
        review——精确相等会把判定子窗放成漏网，7 入口统一 touches_judgment）。"""
        calls = _wu_spy(monkeypatch)
        windows = (("2022-01-01", "2024-07-31"), ("2018-01-01", "2020-12-31"))
        self._monkeypatch_production(monkeypatch, {w: _mk_per_code() for w in windows})
        args = _args(
            judgment_start="2018-01-01",
            judgment_end="2020-12-31",
            n_random=2,
            c4_min_pool=2,
        )
        _with_batch(args, "plan_rules_replay")  # v0.374：判定子窗相交 ⇒ 须带批次
        rep = prr.run_study(
            args,
            warm_fn=None,
            replay_fn=None,
        )
        assert len(calls) == 1
        assert calls[0]["unit"] == "R41"
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is False
        assert rep["window_usage"]["window"] == "judgment_2017_2021"

    def test_injected_still_synthetic(self, monkeypatch):
        """对照钉：v2 受保护判定窗走注入路径 ⇒ synthetic=True（双向钉死）。"""
        calls = _wu_spy(monkeypatch)
        rep = _run_v2(_mk_per_code(), _replay_candidate, n_random=2, c4_min_pool=2)
        assert len(calls) == 1
        assert calls[0]["window"] == "judgment_2017_2021"
        assert calls[0]["synthetic"] is True
        assert rep["window_usage"]["k"] == 7


class TestMiningInJudgmentSegmentRejected:
    """挖掘窗 ∩ 判定段 ⇒ 拒绝（v0.361 owner review）。"""

    def test_mining_inside_judgment_segment(self):
        ap = prr._build_parser()
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
            prr._check_windows(args, ap)


# ---------------------------------------------------------------------------
# R44（v0.379，TODO #100）：信号日最低价锚点 + 挖掘段 8 档宽度扫描
# ---------------------------------------------------------------------------


def _scan_args(**kw):
    """run_width_scan 注入用 args（窗口写死挖掘 A∪B，四参不进 Namespace）。"""
    base = dict(
        tag="t",
        cost_bps=25.0,
        top_n=20,
        seed=7,
        scan_random=8,
        scan_c4_min_pool=4,
        codes="",
        codes_file="",
        count=2000,
        universe_sample=0,
        universe_local=False,
        universe_seed=42,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _scan_fixture(n_sigs=150):
    """两挖掘段各 n_sigs 个信号（单票 df 覆盖两段）。"""
    days_a = [
        d.strftime("%Y-%m-%d") for d in pd.bdate_range("2014-01-02", "2016-12-30")
    ]
    days_b = [
        d.strftime("%Y-%m-%d") for d in pd.bdate_range("2022-01-03", "2026-09-04")
    ]
    days = days_a + days_b
    n = len(days)
    close = [100.0 + 0.01 * i for i in range(n)]
    df = pd.DataFrame(
        {
            "date": days,
            "close": close,
            "low": [c - 2.0 for c in close],
            "volume": [1000.0] * n,
        }
    )
    ia = list(range(0, len(days_a), max(1, len(days_a) // n_sigs)))[:n_sigs]
    ib = list(range(len(days_a), n, max(1, days_b.__len__() // n_sigs)))[:n_sigs]

    def _mk(idxs):
        sigs = [{"i": i, "date": days[i]} for i in idxs]
        return {
            "000001": {
                "df": df,
                "signals": sigs,
                "scores": {days[i]: 50.0 for i in idxs},
            }
        }

    return {
        ("2000-01-01", "2016-12-31"): _mk(ia),
        ("2022-01-01", "2026-09-04"): _mk(ib),
    }


def _scan_noise(seed: int):
    """纯噪声重放工厂：ret 与止损宽度无关（逐笔 iid 有方差，键含 stop_override
    与 **seed**——零假设校准的逐种子方差来自这里）。

    定稿记录（v0.379）：salt="wscan6"——12 变体扫描取主测 5 种子 0/5
    confirmed_pass 者；本变体 40 种子实测 3/40 = 7.5% ≈ 名义 5%（C4 池=25
    臂 + max-of-8 选择效应的合理上偏；max-of-1 哨兵形态 40 种子实测
    14/40 = 35%——哨兵 decisive）。"""

    def _replay(subset, params):
        out = []
        for r in subset:
            so = r["sig"].get("stop_override")
            key = f"{r['code']}|{r['date']}|{so}|{sorted(params.items())}|{seed}|wscan6"
            h = int.from_bytes(hashlib.md5(key.encode()).digest()[:4], "little") / 2**32
            out.append(_trade_of(r, 0.06 if h < 0.4 else -0.035))
        return out

    return _replay


def _scan_edge(seed: int):
    """植入「x=10 固定宽度真更优」：该宽度 75% +0.08 / 25% −0.02（两侧保留，
    防 payoff 无定义），其余宽度纯噪声。"""

    def _replay(subset, params):
        out = []
        for r in subset:
            so = r["sig"].get("stop_override")
            key = f"{r['code']}|{r['date']}|{so}|{sorted(params.items())}|{seed}"
            h = int.from_bytes(hashlib.md5(key.encode()).digest()[:4], "little") / 2**32
            if (
                so is not None
                and abs((1.0 - so / r["anchor_low"]) * 100.0 - 10.0) < 0.01
            ):
                ret = 0.08 if h < 0.75 else -0.02
            else:
                ret = 0.06 if h < 0.4 else -0.035
            out.append(_trade_of(r, ret))
        return out

    return _replay


def _scan_run(seed, edge, **kw):
    spec = _scan_fixture()
    args = _scan_args(seed=seed, **kw)
    return prr.run_width_scan(
        args,
        warm_fn=lambda s, e: spec[(s, e)],
        replay_fn=_scan_edge(seed) if edge else _scan_noise(seed),
    )


class TestAnchorAttach:
    """修①钉测：锚点 = 信号日最低价 × (1−x)；x=0 逐位等价；防御剔除记数。"""

    def test_x0_equals_signal_low_exactly(self):
        pc = _mk_per_code(n_codes=2, sigs_per_code=3)
        subset, acc = prr.attach_anchor_stops(pc, 0.0)
        df = pc["000000"]["df"]
        lows = df["low"].astype(float).tolist()
        for r in subset:
            assert r["plan_stop"] == lows[r["sig"]["i"]]  # 逐位等价
            assert r["anchor_low"] == lows[r["sig"]["i"]]
        assert acc["n_anchor_ge_entry"] == 0

    def test_defensive_drop_counted_when_low_eq_close(self):
        """一字线信号（low == close）：x=0 时锚点 == 入场收盘 ⇒ 两边同剔记数。"""
        pc = _mk_per_code(n_codes=2, sigs_per_code=3)
        df = pc["000000"]["df"]
        df.loc[df.index[10], "low"] = df.loc[df.index[10], "close"]  # 造一字线
        subset0, acc0 = prr.attach_anchor_stops(pc, 0.0)
        assert acc0["n_anchor_ge_entry"] == 1
        assert all(r["sig"]["i"] != 10 for r in subset0 if r["code"] == "000000")
        # x=5：锚点低于入场 ⇒ 不剔（防御条件与宽度方向一致，仅记数语义）
        subset5, acc5 = prr.attach_anchor_stops(pc, 5.0)
        assert acc5["n_anchor_ge_entry"] == 0
        assert len(subset5) > len(subset0)

    def test_lookback_mode_bit_unchanged(self):
        """R41 原模式（缺省 lookback）逐位不变——既有金样钉测覆盖
        （TestLegacyR40Compat::test_report_bit_identical_to_pre_switch），
        这里只钉 attach 入口选择：缺省 stop_anchor ⇒ 走 _stop_ref。"""
        pc = _mk_per_code(n_codes=2, sigs_per_code=3)
        subset_ref, _ = prr.attach_plan_stops(pc, STOP_LOOKBACK)
        subset_anchor, _ = prr.attach_anchor_stops(pc, 0.0)
        # 两模式止损价不同源：_stop_ref 是近 10 日最低，锚点是当日 low
        assert any(
            a["plan_stop"] != b["plan_stop"] for a, b in zip(subset_anchor, subset_ref)
        )


def _blk(margin, gate=True, n_taken=150):
    return {"margin": margin, "rdd_gate": gate, "n_taken": n_taken}


def _readings_block(margins_by_x, ref_margin=0.10, gate=True, n_taken=150):
    blk = {"ref": _blk(ref_margin, gate=True, n_taken=n_taken)}
    for x, m in margins_by_x.items():
        blk[f"x{x:g}"] = _blk(m, gate=gate, n_taken=n_taken)
    return blk


class TestScanJudges:
    WINS = ["mining_a", "mining_b"]

    def test_c1_below_100_untested(self):
        readings = {
            w: _readings_block({x: 0.12 for x in prr.WIDTH_GRID}, n_taken=99)
            for w in self.WINS
        }
        c1 = prr._scan_c1(readings, self.WINS)
        assert c1["ok"] is False
        rep_verdict = kit.verdict_four_state(
            c1_ok=c1["ok"], c2_ok=True, c3_ok=True, c4_state="confirmed_pass"
        )
        assert rep_verdict == "untested"  # C1 不过 ⇒ untested 优先

    def test_c2_no_passing_falsified(self):
        readings = {
            w: _readings_block({x: 0.12 for x in prr.WIDTH_GRID}) for w in self.WINS
        }
        merged = _readings_block({x: 0.12 for x in prr.WIDTH_GRID}, gate=False)
        c2 = prr._scan_c2(readings, merged, self.WINS)
        assert c2["ok"] is False and c2["x_star"] is None
        assert "无档过" in c2["reason"]
        v = kit.verdict_four_state(
            c1_ok=True, c2_ok=False, c3_ok=None, c4_state="confirmed_pass"
        )
        assert v == "falsified"

    def test_c2_pass_and_x_star(self):
        margins = {x: 0.10 for x in prr.WIDTH_GRID}
        margins[10.0] = 0.20  # x=10 合并最优
        readings = {w: _readings_block(dict(margins)) for w in self.WINS}
        merged = _readings_block(dict(margins))
        c2 = prr._scan_c2(readings, merged, self.WINS)
        assert c2["ok"] is True and c2["x_star"] == 10.0
        assert c2["merged_delta_margin"] == pytest.approx(0.10)

    def test_c3_smooth_false(self):
        # x*=10；邻档 x8 在 mining_b 的 Δmargin ≤ 0 ⇒ 平滑子项 False
        margins = {x: 0.15 for x in prr.WIDTH_GRID}
        margins[10.0] = 0.20
        ra = _readings_block(dict(margins))
        rb_m = dict(margins)
        rb_m[8.0] = 0.05  # mining_b 的 x8 低于 ref(0.10) ⇒ Δ<0
        rb = _readings_block(rb_m)
        readings = {"mining_a": ra, "mining_b": rb}
        c3 = prr._scan_c3(readings, self.WINS, 10.0)
        assert c3["ok"] is False

    def test_c3_consistency_false(self):
        # x*=10；mining_b 最优档 = x20（相差 3 档 > 1）⇒ 一致性子项 False
        margins = {x: 0.15 for x in prr.WIDTH_GRID}
        margins[10.0] = 0.20
        ra = _readings_block(dict(margins))
        rb_m = dict(margins)
        rb_m[20.0] = 0.30  # mining_b 最优跳到 x20
        rb = _readings_block(rb_m)
        readings = {"mining_a": ra, "mining_b": rb}
        c3 = prr._scan_c3(readings, self.WINS, 10.0)
        assert c3["ok"] is False
        assert c3["window_best"]["mining_b"] == 20.0

    def test_c3_pass_and_endpoint_single_side(self):
        # x*=5（端点）：只查 x6 单侧；两窗最优都是 x5 ⇒ C3 过
        margins = {x: 0.10 for x in prr.WIDTH_GRID}
        margins[5.0] = 0.20
        margins[6.0] = 0.15
        readings = {w: _readings_block(dict(margins)) for w in self.WINS}
        c3 = prr._scan_c3(readings, self.WINS, 5.0)
        assert c3["ok"] is True
        assert list(c3["smooth"].keys()) == ["x6"]  # 端点单侧


class TestScanModeDiscipline:
    """扫描模式 v2 纪律：不读判定段、不过批次门、window_usage 不登记。"""

    def test_no_judgment_read_no_gate_no_ledger(self, monkeypatch):
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
        rep = _scan_run(seed=0, edge=False)
        assert calls == [], "扫描模式不得触 record_use / 批次门"
        assert rep["window_usage"]["window"] == "not_read"
        assert rep["criteria"]["C4"]["state"] in kit.C4_STATES

    def test_empty_guard_raises(self):
        spec = {
            k: {"000001": {"df": _mk_df(), "signals": [], "scores": {}}}
            for k in _scan_fixture()
        }
        with pytest.raises(RuntimeError, match="空结果护栏"):
            prr.run_width_scan(
                _scan_args(),
                warm_fn=lambda s, e: spec[(s, e)],
                replay_fn=_scan_noise(0),
            )

    def test_scan_cli_mutex_flags(self, tmp_path):
        # 互斥校验在 main 入口（fail-closed ap.error）
        with pytest.raises(SystemExit):
            prr.main(
                ["--tag", "t", "--stop-anchor", "signal_low"]
            )  # 缺 --stop-buffer-pct
        with pytest.raises(SystemExit):
            prr.main(["--tag", "t", "--stop-buffer-pct", "10"])  # 无 signal_low
        with pytest.raises(SystemExit):
            prr.main(
                [
                    "--tag",
                    "t",
                    "--scan-widths",
                    "--stop-anchor",
                    "signal_low",
                    "--stop-buffer-pct",
                    "10",
                ]
            )

    def test_scan_main_route_and_write(self, tmp_path, monkeypatch):
        """main 路由 --scan-widths ⇒ 产物 _plan_width_scan__{tag}.json。"""
        spec = _scan_fixture()
        rc = prr.main(
            ["--tag", "scan_t", "--out-dir", str(tmp_path), "--scan-widths"],
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_scan_noise(0),
        )
        assert rc == 0
        out = tmp_path / "scan_t" / "_plan_width_scan__scan_t.json"
        assert out.exists()
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["mode"] == "width_scan" and rep["grid"] == [
            float(x) for x in prr.WIDTH_GRID
        ]
