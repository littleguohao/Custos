# -*- coding: utf-8 -*-
"""plan_rules_replay（R41 Phase 1，v0.316）钉测。

锁的契约（owner 实现指导 §5 清单）：①引擎 stop_override 钩子（缺省逐位
不变/带键生效/≥entry 被工具层剔除）；②_stop_ref 默认逐位不变 + as-of
只读 ≤ 信号日；③剔除记账两版同子集；④C1 不过=untested 优先；⑤rdd 门
不过 ⇒ C2 False；⑥C3 新子集两版重跑（配对重对齐）；⑦C4 臂种子独立
可复现/池空=indeterminate/池未满=provisional；⑧纯噪声零假设不系统性
偏高；⑨CLI pre2019 拒绝 + 空结果非零退出不落盘。
"""

from __future__ import annotations

import argparse
import json

import pandas as pd
import pytest

from custos.core.factors.b1_structure import STOP_LOOKBACK, _stop_ref
from custos.research import backtest_factors as bt
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
        # 副读数 pct7 自含；现行版读数两窗在
        assert rep["readings"]["mining"]["live_alt_pct7"] is not None

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
        """全部臂过不了 rdd 门 ⇒ 池空 = indeterminate（不放行，v0.297 族）。"""

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
        assert c4["state"] == "indeterminate"
        assert rep["verdict"] == "provisional"  # 不放行不判死

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
    def test_pre2019_overlap_rejected(self):
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
