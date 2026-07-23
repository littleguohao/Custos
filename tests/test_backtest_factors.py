# -*- coding: utf-8 -*-
"""S_shape 因子走查回测校准工具测试（注入合成 bars，验证无未来函数 + 数学 + 聚合）。"""
import pandas as pd

from screening import backtest_factors as bt


def make_df(closes, highs=None, lows=None, vols=None):
    n = len(closes)
    closes = [float(x) for x in closes]
    highs = [float(x) for x in (highs if highs is not None else [c * 1.01 for c in closes])]
    lows = [float(x) for x in (lows if lows is not None else [c * 0.99 for c in closes])]
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": [float(v) for v in (vols or [1000.0] * n)], "amount": [0.0] * n,
    })


# ---------- forward_metrics 数学正确 + 只看未来 ----------

def test_forward_metrics_math():
    closes = [10.0, 11.0, 9.0, 12.0]
    highs = [10.0, 11.5, 9.5, 12.5]
    lows = [10.0, 10.5, 8.5, 11.5]
    df = make_df(closes, highs=highs, lows=lows)
    fm = bt.forward_metrics(df, 0, 3)  # 入场=close[0]=10；未来=bars1..3
    assert fm["available"] and fm["bars"] == 3
    assert abs(fm["fwd_return"] - (12.0 / 10 - 1)) < 1e-9      # 末收盘/入场
    assert abs(fm["mfe"] - (12.5 / 10 - 1)) < 1e-9            # 未来最高/入场
    assert abs(fm["mae"] - (8.5 / 10 - 1)) < 1e-9             # 未来最低/入场


def test_forward_metrics_excludes_entry_bar():
    # 入场当根是极端高/低，若误纳入 entry 根，mfe/mae 会被污染
    df = make_df([10.0, 10.1, 10.2], highs=[99.0, 10.2, 10.3], lows=[1.0, 10.0, 10.1])
    fm = bt.forward_metrics(df, 0, 2)
    # 未来窗口是 bar1..2，最高应来自 10.2/10.3 而非 entry 的 99；最低不含 entry 的 1.0
    assert fm["mfe"] < 0.1 and fm["mae"] > -0.5


def test_forward_metrics_no_future_bars():
    df = make_df([10.0, 10.1, 10.2])
    assert bt.forward_metrics(df, 2, 5)["available"] is False  # 最后一根无未来


# ---------- evaluate 无未来函数：as-of 切片不含未来 ----------

def test_evaluate_no_future_leak(monkeypatch):
    """通过 monkeypatch 断言 compute_s_shape 每次拿到的切片末日 == as-of 日，且长度== i+1。"""
    seen = []

    def spy(df_slice, code):
        seen.append((code, len(df_slice), str(df_slice["date"].iloc[-1])[:10]))
        return {"available": True, "s_star": 50.0, "s_shape": 45.0, "delta": 5.0,
                "penalty": 0.0, "suggestion": "不买", "components": {}}

    monkeypatch.setattr(bt, "compute_s_shape", spy)
    df = make_df([10.0 + i * 0.1 for i in range(70)])
    recs = bt.evaluate({"600000": df}, horizons=(5,), min_bars=60, step=1)
    # 每个 as-of i：切片长度 == i+1（即只含 0..i，无未来）
    assert seen, "应至少产生一条"
    for code, length, last_date in seen:
        i = length - 1
        assert str(df["date"].iloc[i])[:10] == last_date  # 切片末日==as-of日
    # 记录数 == 可评估的 as-of 数（min_bars..n-2，因末根无未来被 forward 判 None 但仍入记录）
    assert len(recs) == len(seen)


def test_evaluate_records_shape():
    df = make_df([10.0 + i * 0.08 for i in range(80)])
    recs = bt.evaluate({"600000": df}, horizons=(5, 10), min_bars=60, step=5)
    assert recs
    r = recs[0]
    for key in ("code", "date", "s_star", "suggestion", "ret5", "mfe5", "mae5", "ret10"):
        assert key in r
    # 分项以 c_ 前缀落盘
    assert any(k.startswith("c_") for k in r)


# ---------- summarize 分组聚合正确 ----------

def _rec(s_star, suggestion, ret10, c_pivot=0.0):
    return {"code": "x", "date": "d", "s_star": s_star, "suggestion": suggestion,
            "ret10": ret10, "mfe10": abs(ret10) + 0.01, "mae10": -abs(ret10) - 0.01,
            "c_pivot": c_pivot}


def test_summarize_bands_and_winrate():
    records = [
        _rec(80, "可买", 0.10), _rec(75, "可买", -0.02),   # 可买: 2 条, 1 胜
        _rec(65, "观望", 0.03),                             # 观望: 1 条
        _rec(30, "不买", -0.05), _rec(20, "不买", -0.08),  # 不买: 2 条, 0 胜
    ]
    s = bt.summarize(records, horizon=10)
    assert s["total_signals"] == 5
    band = {b["band"]: b for b in s["by_sstar_band"]}
    assert band["A_可买(>=70)"]["n"] == 2
    assert band["A_可买(>=70)"]["win_rate"] == 0.5
    assert band["D_弱(<40)"]["n"] == 2 and band["D_弱(<40)"]["win_rate"] == 0.0
    assert s["by_suggestion"]["可买"]["n"] == 2
    assert s["by_suggestion"]["不买"]["win_rate"] == 0.0


def test_summarize_component_lift():
    records = [_rec(70, "可买", 0.08, c_pivot=15.0), _rec(50, "中", -0.03, c_pivot=0.0)]
    s = bt.summarize(records, horizon=10)
    comp = s["by_component_hit"]["c_pivot"]
    assert comp["hit"]["n"] == 1 and comp["miss"]["n"] == 1
    assert comp["hit"]["avg_return"] > comp["miss"]["avg_return"]
