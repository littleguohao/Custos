# -*- coding: utf-8 -*-
"""各研究终端的零假设校准（强制两连测，owner 方法论 review #4，v0.323；
#1 返修 v0.330）。

每个接入 criteria_kit 的终端必须带：**纯噪声 confirmed_pass ≤ ~10%**
（5 种子实测须 0/5）+ **植入真 edge ⇒ confirmed_pass 可识别**——
新终端不过这两项不许合入（R40 C4 的 7/8 假阳性、R41 池子永远填不满，
都是临时写纯噪声脚本才发现的；现在变成合入门）。判定只看报告
``criteria.C4.state``（夹具见 tests/helpers_null.py）。

v0.330 返修（owner review #1）：
- 零假设改**逐笔 iid 方差**噪声（helpers_null 模块 docstring 有退化版
  教训——交替 ± 下 max-of-230 与 max-of-5 同数，选择效应不出现）；
- **池 ≥20**（原 n_random=4/min_pool=3 的 q95≈最大值，测不出 q95 校准）；
- bear 随机臂抽样改**逐次偏移** ``_rand_seq``（原 _rand_flat 对同一
  (n,w) 恒返回同一批入场 ⇒ 25 条臂全同，池退化——生产路径每臂 rng
  重抽本就有臂间方差，夹具必须复刻这一点）；
- **回归哨兵** ``TestSentinelMaxOf5``：把 R40 随机臂换回 v0.307 前的
  max-of-5 构造（monkeypatch run_arm），断言校准测试**必须失败**——
  能抓住已知 bug 才证明这道合入门有效。
"""

from __future__ import annotations

import pytest

import helpers_null as hn
import test_factor_exit_study as fxt
from custos.research import bear_regime_study as brs
from custos.research import factor_exit_study as fes
from custos.research import plan_rules_replay as prr
from test_bear_regime_study import (
    _args as _bear_args,
    _mk_per_code as _bear_per_code,
)
from test_plan_rules_replay import (
    _args as _prr_args,
    _mk_per_code as _prr_per_code,
)

_WINDOWS = (("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))

#: 校准池规模（v0.330：≥20——小池 q95≈max 测不出校准）
N_RANDOM, MIN_POOL = 25, 20


def _rand_seq(seed: int):
    """逐次偏移的随机入场（确定性但臂间有方差——生产 random_entries
    每臂 rng 重抽的夹具等价物；_rand_flat 恒同批 ⇒ 池退化，已废于此）。"""
    calls = {"n": 0}

    def _draw(n: int, w: str) -> list[dict]:
        calls["n"] += 1
        off = calls["n"] * 131 + seed * 977
        return [
            {
                "code": f"{(i + off) % 7:06d}",
                "date": f"2024-{((i + off) % 12) + 1:02d}-{((i + off) % 28) + 1:02d}",
                "i": (0 if w == "mining" else 10000) + i + off,
                "score": 50.0,
                "sig": {"i": i + off, "date": "2024-01-02"},
                "gate": "__random__",
            }
            for i in range(n)
        ]

    return _draw


# ---------------------------------------------------------------------------
# bear_regime_study（R40 终端）
# ---------------------------------------------------------------------------


def _bear_run(replay, **kw):
    def run(seed):
        spec = _bear_per_code({"j_low": 120, "j_low_adx25": 4})  # 自带双窗键
        kw.setdefault("n_random", N_RANDOM)
        kw.setdefault("c4_min_pool", MIN_POOL)
        return brs.run_study(
            _bear_args(seed=seed, **kw),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=replay,
            random_entry_fn=_rand_seq(seed),
        )

    return run


class TestBearRegimeCalibration:
    def test_noise(self):
        hn.assert_noise_calibration("bear_regime", _bear_run(hn.null_replay))

    def test_edge_detected(self):
        hn.assert_edge_detected(
            "bear_regime",
            _bear_run(
                lambda sub, p: hn.edge_replay(
                    sub, p, is_edge=lambda r, _p: r["gate"] == "j_low"
                )
            ),
        )


class TestSentinelMaxOf5:
    """回归哨兵（owner review #1）：R40 随机臂换回 v0.307 前的 max-of-5
    构造 ⇒ 零假设校准**必须失败**（top 独享 230 格选择效应、臂只有 5 格，
    标尺系统性偏低——v0.306 的 7/8 假阳性就是这个 bug）。"""

    @staticmethod
    def _run_arm_max_of_5(
        gate_counts, exits, draw_entries, replay_mining, top_n, judgment_reader=None
    ):
        """v0.307 前的退化臂构造（仅哨兵复刻，勿用于生产）。"""
        n = sum(gate_counts.values())
        entries = draw_entries(n, "mining")
        ref_rd = None
        for e in exits:
            if e["name"] == "pct5_trail08":
                ref_rd = fes.combine_readings(
                    replay_mining(entries, e["params"]), top_n, ref="self"
                )
                break
        best = None
        for e in exits:
            rd = fes.combine_readings(
                replay_mining(entries, e["params"]), top_n, ref=ref_rd
            )
            if rd and rd["objective"] is not None and rd["margin"] is not None:
                if best is None or rd["margin"] > best["readings"]["margin"]:
                    best = {
                        "n_signals": n,
                        "gate_template": "__max5__",
                        "exit": e["name"],
                        "params": e["params"],
                        "readings": rd,
                    }
        out = {"mining": best}
        if best is not None and judgment_reader is not None:
            out["judgment"] = judgment_reader(best["n_signals"], best["params"])
        return out

    def test_calibration_catches_max_of_5_bug(self, monkeypatch):
        monkeypatch.setattr(brs, "run_arm", self._run_arm_max_of_5)
        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration(
                "bear_regime[max-of-5 哨兵]",
                _bear_run(hn.null_replay),
                seeds=(0, 1, 2),
            )


# ---------------------------------------------------------------------------
# plan_rules_replay（R41 终端）
# ---------------------------------------------------------------------------


def _prr_run(replay, **kw):
    def run(seed):
        per_code = _prr_per_code()
        spec = {w: per_code for w in _WINDOWS}
        kw.setdefault("n_random", N_RANDOM)
        kw.setdefault("c4_min_pool", MIN_POOL)
        return prr.run_study(
            _prr_args(seed=seed, **kw),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=replay,
        )

    return run


class TestPlanRulesCalibration:
    def test_noise(self):
        hn.assert_noise_calibration("plan_rules", _prr_run(hn.null_replay))

    def test_edge_detected(self):
        hn.assert_edge_detected(
            "plan_rules",
            _prr_run(
                lambda sub, p: hn.edge_replay(
                    sub,
                    p,
                    is_edge=lambda r, _p: (
                        r["sig"].get("stop_override") == r.get("plan_stop")
                    ),
                )
            ),
        )


# ---------------------------------------------------------------------------
# factor_exit_study（R39 终端）
# ---------------------------------------------------------------------------


class TestFactorExitCalibration:
    def test_noise(self, monkeypatch):
        def run(seed):
            return fxt._run(
                monkeypatch,
                fxt._three_level_spec(180),
                hn.null_replay,
                n_random=N_RANDOM,
                c4_min_pool=MIN_POOL,
                seed=seed,
            )

        hn.assert_noise_calibration("factor_exit", run)

    def test_edge_detected(self, monkeypatch):
        def run(seed):
            # _scripted_replay = 真条件映射剧本（条件 margin ≫ uniform-best）
            return fxt._run(
                monkeypatch,
                fxt._three_level_spec(180),
                fxt._scripted_replay,
                n_random=N_RANDOM,
                c4_min_pool=MIN_POOL,
                seed=seed,
            )

        hn.assert_edge_detected("factor_exit", run)
