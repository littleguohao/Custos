# -*- coding: utf-8 -*-
"""各研究终端的零假设校准（强制两连测，owner 方法论 review #4，v0.323）。

每个接入 criteria_kit 的终端必须带：**纯噪声 confirmed_pass ≤ ~10%**
（5 种子实测须 0/5）+ **植入真 edge ⇒ confirmed_pass 可识别**——
新终端不过这两项不许合入（R40 C4 的 7/8 假阳性、R41 池子永远填不满，
都是临时写纯噪声脚本才发现的；现在变成合入门）。判定只看报告
``criteria.C4.state``（夹具见 tests/helpers_null.py）。
"""

from __future__ import annotations

import helpers_null as hn
import test_factor_exit_study as fxt
from custos.research import bear_regime_study as brs
from custos.research import plan_rules_replay as prr
from test_bear_regime_study import (
    _args as _bear_args,
    _mk_per_code as _bear_per_code,
    _rand_flat,
)
from test_plan_rules_replay import (
    _args as _prr_args,
    _mk_per_code as _prr_per_code,
)

_WINDOWS = (("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))


# ---------------------------------------------------------------------------
# bear_regime_study（R40 终端）
# ---------------------------------------------------------------------------


def _bear_run(replay, **kw):
    def run(seed):
        spec = _bear_per_code({"j_low": 120, "j_low_adx25": 4})  # 自带双窗键
        kw.setdefault("n_random", 4)
        kw.setdefault("c4_min_pool", 3)
        return brs.run_study(
            _bear_args(seed=seed, **kw),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=replay,
            random_entry_fn=_rand_flat,
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


# ---------------------------------------------------------------------------
# plan_rules_replay（R41 终端）
# ---------------------------------------------------------------------------


def _prr_run(replay, **kw):
    def run(seed):
        per_code = _prr_per_code()
        spec = {w: per_code for w in _WINDOWS}
        kw.setdefault("n_random", 4)
        kw.setdefault("c4_min_pool", 3)
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
                n_random=4,
                c4_min_pool=3,
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
                n_random=5,
                seed=seed,
            )

        hn.assert_edge_detected("factor_exit", run)
