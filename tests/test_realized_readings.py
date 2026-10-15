"""margin 读数只用已实现交易（v0.383，审计 2026-10-15）：open_end* 与 *_unfillable
是窗末浮动盈亏，混进 margin 会在 open_end 偏正的窗口造假过线。"""

from custos.research import backtest_factors as bt
from custos.research import factor_exit_study as fes
from custos.research.m2_stop_sweep import _realized_view


def _t(ret, reason, i=0):
    return {
        "code": f"{i:06d}",
        "entry_date": f"2023-01-{i % 28 + 1:02d}",
        "exit_date": "2023-02-01",
        "ret": ret,
        "reason": reason,
        "risk_frac": 0.05,
        "r_multiple": ret / 0.05,
        "holding": 5,
        "score": 1.0,
    }


def test_reason_predicate():
    for r in (
        "open_end",
        "open_end+scaled",
        "stop_unfillable",
        "bbi_exit_unfillable+scaled",
    ):
        assert bt.is_unrealized_reason(r), r
    for r in ("stop", "bbi_exit", "trail+scaled", "bbi_exit_delayed", None):
        assert not bt.is_unrealized_reason(r), r


def _book():
    # 已实现：3 亏 1 赢；未实现：4 笔大浮盈（窗末强标）
    tr = [_t(-0.05, "stop", i) for i in range(3)] + [_t(0.10, "bbi_exit", 3)]
    tr += [_t(0.30, "open_end", 4 + i) for i in range(4)]
    return tr


def test_summarize_realized_excludes_unrealized():
    s = bt.summarize_realized(_book())
    assert s["n"] == 4 and s["n_unrealized"] == 4
    assert abs(s["win_rate"] - 0.25) < 1e-12


def test_combine_readings_margin_is_realized():
    rd = fes.combine_readings(_book(), top_n=5, ref="self")
    real = bt.summarize_realized(_book())
    assert rd["win_rate"] == real["win_rate"] and rd["n_unrealized"] == 4
    assert rd["margin"] < 0  # 含 open_end 的全体读数 win=5/8 会是正 margin


def test_m2_realized_view():
    s = {"n": 8, "win_rate": 0.625, "_trades": _book()}
    v = _realized_view(s)
    assert v["_realized"] is True and v["n"] == 4 and abs(v["win_rate"] - 0.25) < 1e-12
    assert _realized_view({"n": 8, "win_rate": 0.6})["_realized"] is False
