"""rdd 符号感知（v0.382，审计 2026-10-15）：亏损窗口里 收益/回撤 对回撤单调递增，
rdd 相对门方向反转；改为 收益<0 时用 收益×回撤。"""

import random

from custos.research import strategy_grid as sg
from custos.research.m2_stop_sweep import _ret_over_dd


def _pf(ret, dd):
    return {"total_return": ret, "max_drawdown": dd, "return_over_maxdd": ret / dd}


def test_negative_window_gate_not_inverted():
    ref = {"ret_over_dd": _ret_over_dd(_pf(-0.20, 0.40))}
    better = {"ret_over_dd": _ret_over_dd(_pf(-0.05, 0.08))}
    worse = {"ret_over_dd": _ret_over_dd(_pf(-0.20, 0.60))}  # 旧口径 −0.333>−0.5 误放行
    assert sg.rdd_gate_ok(better, ref) is True
    assert sg.rdd_gate_ok(worse, ref) is False


def test_monotone_both_axes_and_continuous_at_zero():
    rng = random.Random(0)
    for _ in range(5000):
        r, d, e = rng.uniform(-0.5, 0.5), rng.uniform(0.01, 0.8), 1e-3
        assert _ret_over_dd(_pf(r + e, d)) >= _ret_over_dd(_pf(r, d)) - 1e-12
        assert _ret_over_dd(_pf(r, d + e)) <= _ret_over_dd(_pf(r, d)) + 1e-12
    assert _ret_over_dd(_pf(0.0, 0.3)) == 0.0


def test_positive_unchanged_and_fail_closed():
    assert _ret_over_dd(_pf(0.3, 0.1)) == 0.3 / 0.1
    assert _ret_over_dd({"return_over_maxdd": 2.0}) == 2.0
    assert _ret_over_dd({"return_over_maxdd": -0.5}) is None
    assert _ret_over_dd({}) is None
