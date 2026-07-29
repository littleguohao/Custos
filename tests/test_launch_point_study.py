# -*- coding: utf-8 -*-
"""起涨点 vs 0AMV 研究(launch_point_study)测试。"""
import pandas as pd
import pytest

from screening import launch_point_study as lp


@pytest.fixture(autouse=True)
def _no_tdx_names(monkeypatch):
    # 本文件用虚构板块代码(880201 在真实 tdxzs.cfg 里是"黑龙江"=地区,会被板块族口径剔除);
    # 统一屏蔽名称表,剔除语义由 test_sector_mainstream 专门覆盖
    monkeypatch.setattr("tq_sector.load_sector_names", lambda path=None: {})


def test_window_return():
    dates = [f"2025-01-{d:02d}" for d in range(1, 7)]
    closes = [10, 9, 8, 12, 15, 11]
    assert abs(lp.window_return(dates, closes, "2025-01-01", "2025-01-06") - 0.1) < 1e-9
    assert lp.window_return(dates, closes, "2025-02-01", "2025-02-06") is None   # 区间外


def test_find_launch_picks_max_forward_gain():
    dates = [f"2025-01-{d:02d}" for d in range(1, 7)]
    closes = [10, 9, 8, 12, 15, 11]
    r = lp.find_launch(dates, closes, [1, 2], "2025-01-06")   # idx2(收8)前向到峰15 收益更大
    assert r["date"] == "2025-01-03" and abs(r["fwd_gain"] - (15 / 8 - 1)) < 1e-6


def test_regime_at_and_lead():
    regime = {"2025-01-01": "空头", "2025-01-02": "空头", "2025-01-03": "做多", "2025-01-06": "做多"}
    r = lp.regime_at_and_lead(regime, "2025-01-01")
    assert r["regime"] == "空头" and r["lead_days_to_long"] == 2   # 距 01-03 做多 2 个 regime 日
    r2 = lp.regime_at_and_lead(regime, "2025-01-03")
    assert r2["regime"] == "做多" and r2["lead_days_to_long"] is None


def test_analyze_smoke():
    dates = pd.date_range("2025-01-01", periods=60, freq="B")
    ds = [str(d)[:10] for d in dates]
    # 赢家:先跌后大涨;起涨点应落在早期(空头段)
    close = [20 - 0.3 * i for i in range(20)] + [14 + 0.6 * i for i in range(40)]
    win = pd.DataFrame({"date": dates, "open": close, "high": [c * 1.02 for c in close],
                        "low": [c * 0.98 for c in close], "close": close, "volume": [1e6] * 60})
    flat = pd.DataFrame({"date": dates, "open": [10] * 60, "high": [10.1] * 60,
                         "low": [9.9] * 60, "close": [10] * 60, "volume": [1e6] * 60})
    regime = {ds[i]: ("空头" if i < 25 else "做多") for i in range(60)}
    res = lp.analyze({"WIN": win, "FLAT": flat}, regime, ds[0], ds[-1],
                     entry_gate=lambda s: True, top_pct=50, buffer_days=0, min_bars=40)
    assert res["n_winners"] >= 1 and res["n_launches"] >= 1
    assert set(res["by_regime"]) <= {"做多", "空头", "中性", "未知"}


def test_sector_concentration(tmp_path):
    # 赢家集中在 880201(强板块,+40%),少量在 880900(弱板块,-10%)
    dates = [str(d)[:10] for d in pd.date_range("2024-09-02", periods=80, freq="B")]
    def _csv(name, ret):
        close = [100 * (1 + ret * i / 79) for i in range(80)]
        (tmp_path / f"{name}.csv").write_text(
            "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, close)), encoding="utf-8")
    _csv("880201.SH", 0.40)   # 强
    _csv("880900.SH", -0.10)  # 弱
    members = {"880201.SH": ["600000", "600001", "600002", "600003"], "880900.SH": ["000002"]}
    winners = ["600000", "600001", "600002", "600003", "000002"]
    r = lp.sector_concentration(winners, members, tmp_path, dates[0], dates[-1])
    assert r["distinct_sectors"] == 2 and r["n_classified"] == 5
    assert r["top_sectors"][0]["sector"] == "880201.SH" and r["top_sectors"][0]["n_winners"] == 4
    assert r["top5_winner_share"] >= 0.5           # 集中(前5板块占大头)
    assert "板块" in r["text"]


def test_sector_concentration_corr_and_zero_winner_sectors(tmp_path):
    # 修 bug 兜底:corr 必须真的算出来(曾因缺 import pandas 被静默吞成 None);零赢家板块计入相关性样本
    dates = [str(d)[:10] for d in pd.date_range("2024-09-02", periods=80, freq="B")]
    def _csv(name, ret):
        close = [100 * (1 + ret * i / 79) for i in range(80)]
        (tmp_path / f"{name}.csv").write_text(
            "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, close)), encoding="utf-8")
    _csv("880201.SH", 0.40)   # 强,有赢家
    _csv("880900.SH", -0.10)  # 弱,有赢家
    _csv("880300.SH", 0.05)   # 零赢家板块
    members = {"880201.SH": ["600000", "600001"], "880900.SH": ["000002"], "880300.SH": ["600999"]}
    winners = ["600000", "600001", "000002"]
    r = lp.sector_concentration(winners, members, tmp_path, dates[0], dates[-1])
    assert r["top_sectors"][0]["sector_return"] is not None       # 板块收益真的算了(缺 pd 时恒 None)
    assert r["corr_wincount_vs_sectorret"] is not None            # 相关性真的算了
    assert r["corr_n"] == 3                                       # 零赢家板块(880300)计入,不左截断
    assert abs(r["top_sectors"][0]["sector_return"] - 0.40) < 0.01


def test_analyze_unknown_regime_excluded_from_leads():
    # 起涨点早于 regime 历史 → "未知" 不得混入 lead 分布(此前 !=做多 的口径会把未知计入)
    launches_regime = {"2025-06-01": "做多"}
    dates = pd.date_range("2025-01-01", periods=60, freq="B")
    ds = [str(d)[:10] for d in dates]
    close = [20 - 0.3 * i for i in range(20)] + [14 + 0.6 * i for i in range(40)]
    win = pd.DataFrame({"date": dates, "open": close, "high": [c * 1.02 for c in close],
                        "low": [c * 0.98 for c in close], "close": close, "volume": [1e6] * 60})
    res = lp.analyze({"WIN": win}, launches_regime, ds[0], ds[-1],
                     entry_gate=lambda s: True, top_pct=100, buffer_days=0, min_bars=40)
    assert res["by_regime"].get("未知", 0) >= 1                    # 起涨点确实落在未知段
    assert "lead_days" not in res                                  # 但未知不进 lead 分布


def test_main_loads_with_buffered_start(tmp_path, monkeypatch):
    # buffer 修复兜底:真实加载路径的数据起点必须早于 --start(此前 buffer 被加载窗口截为 0)
    captured = {}
    def fake_load(codes, count, start=None, end=None, root=None):
        captured["start"] = start
        return {}
    monkeypatch.setattr("s_data.load_bars_qlib", fake_load)
    monkeypatch.setattr(lp.bt, "load_amv_regime", lambda since="2015-01-01", root=None: {})
    rc = lp.main(["--codes", "600000", "--start", "2025-01-01", "--end", "2025-06-30",
                  "--buffer-days", "60", "--sector-members", str(tmp_path / "none.json")])
    assert rc == 0
    assert captured["start"] < "2024-11-01"          # 60 交易日×1.6+10 ≈ 106 日历日 ≈ 2024-09-16


def test_sector_concentration_density_and_winner_rets(tmp_path):
    # 板块族口径:密度=归属数/成分数(纠大板块偏差);赢家收益聚合成板块胜率/期望
    dates = [str(d)[:10] for d in pd.date_range("2024-09-02", periods=80, freq="B")]
    def _csv(name, ret):
        close = [100 * (1 + ret * i / 79) for i in range(80)]
        (tmp_path / f"{name}.csv").write_text(
            "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, close)), encoding="utf-8")
    _csv("880201.SH", 0.40)
    _csv("880548.SH", 0.60)
    members = {"880201.SH": ["600000", "600001", "600002", "600003"],   # 大板块(4成分)
               "880548.SH": ["600000"]}                                  # 小板块(1成分):同样2归属→密度更高
    winners = ["600000", "600001"]
    winner_rets = {"600000": 0.50, "600001": 0.30}
    r = lp.sector_concentration(winners, members, tmp_path, dates[0], dates[-1],
                                winner_rets=winner_rets)
    assert r["n_classified"] == 2 and r["distinct_sectors"] == 2
    d201 = next(x for x in r["top_sectors"] if x["sector"] == "880201.SH")
    d548 = next(x for x in r["top_sectors"] if x["sector"] == "880548.SH")
    assert d201["n_winners"] == 2 and d201["density"] == 0.5          # 2/4
    assert d548["n_winners"] == 1 and d548["density"] == 1.0          # 1/1 → 密度高于大板块
    assert r["top_by_density"][0]["sector"] == "880548.SH"
    assert abs(d201["expectancy"] - 0.40) < 1e-3                       # 赢家收益均值(0.5+0.3)/2
    assert d201["name"] and d201["sector_return"] is not None


def test_capture_rank_study():
    import pandas as pd
    dates = [str(d)[:10] for d in pd.date_range("2024-09-02", periods=60, freq="B")]

    def _mk(code, ret, fire_day):
        # 线性收益 ret;在 fire_day 那天造一个"信号"(用 volume==1 标记),其余天 volume==9
        close = [10 * (1 + ret * i / 59) for i in range(60)]
        vol = [9.0] * 60
        vol[fire_day] = 1.0
        return pd.DataFrame({"date": dates, "open": close, "high": [c * 1.01 for c in close],
                             "low": [c * 0.99 for c in close], "close": close, "volume": vol})

    # 10 只:5 只赢家(高收益) + 5 只输家(低/负收益),同日触发信号→同池
    bars = {}
    for k in range(5):
        bars[f"W{k}"] = _mk(f"W{k}", 0.8 + 0.02 * k, 20 + k)     # 赢家
    for k in range(5):
        bars[f"L{k}"] = _mk(f"L{k}", -0.1 + 0.01 * k, 20 + k)    # 输家(同日触发→同池)

    gate = lambda df: float(df["volume"].iloc[-1]) == 1.0        # 信号=当天 volume==1
    scorer = lambda df, code: {"score": 100.0 if str(code).startswith("W") else 1.0}
    r = lp.capture_rank_study(bars, dates[0], dates[-1], gate, scorer=scorer,
                              top_pct=50.0, surface_top_n=3, min_bars=5)
    assert r["n_winners"] == 5 and r["captured"] == 5 and r["recall"] == 1.0
    assert r["surfaced"] == 5 and r["buried_selected_not_found"] == 0   # 好排序:赢家全进 top3
    assert r["surfaced_rate_of_captured"] == 1.0

    r2 = lp.capture_rank_study(bars, dates[0], dates[-1], gate, scorer=None,
                               top_pct=50.0, surface_top_n=3, min_bars=5)
    assert r2["recall"] == 1.0 and r2["random_surfaced_rate_of_captured"] is not None
    assert "捕捉率" in r2["text"]


def _synth_bars_10():
    import pandas as pd
    dates = [str(d)[:10] for d in pd.date_range("2024-09-02", periods=60, freq="B")]

    def _mk(code, ret, fire_day):
        close = [10 * (1 + ret * i / 59) for i in range(60)]
        vol = [9.0] * 60
        vol[fire_day] = 1.0
        return pd.DataFrame({"date": dates, "open": close, "high": [c * 1.01 for c in close],
                             "low": [c * 0.99 for c in close], "close": close, "volume": vol})
    bars = {}
    for k in range(5):
        bars[f"W{k}"] = _mk(f"W{k}", 0.8 + 0.02 * k, 20 + k)
    for k in range(5):
        bars[f"L{k}"] = _mk(f"L{k}", -0.1 + 0.01 * k, 20 + k)
    return bars, dates


def test_capture_rank_streaming_matches_dict():
    """流式(generator)输入与 dict 输入结果一致——证明省内存重构不改语义。"""
    bars, dates = _synth_bars_10()
    gate = lambda df: float(df["volume"].iloc[-1]) == 1.0
    scorer = lambda df, code: {"score": 100.0 if str(code).startswith("W") else 1.0}
    a = lp.capture_rank_study(bars, dates[0], dates[-1], gate, scorer=scorer,
                              top_pct=50.0, surface_top_n=3, min_bars=5)
    b = lp.capture_rank_study(iter(bars.items()), dates[0], dates[-1], gate, scorer=scorer,
                              top_pct=50.0, surface_top_n=3, min_bars=5)   # 生成器/迭代器
    assert a["recall"] == b["recall"] == 1.0
    assert a["surfaced"] == b["surfaced"] == 5
    assert a["captured"] == b["captured"] == 5


def test_main_capture_only_smoke(capsys):
    """main --capture-only 走流式路径(loader 注入合成数据),不触发全量载入。"""
    bars, _ = _synth_bars_10()
    rc = lp.main(["--codes", ",".join(bars), "--start", "2024-09-02", "--end", "2024-11-25",
                  "--entry-filter", "reversal_k", "--capture-only", "--capture-top-pct", "50",
                  "--surface-top-n", "3", "--rank-score", "none", "--buffer-days", "0"],
                 loader=lambda codes, _n: {c: bars[c] for c in codes if c in bars})
    out = capsys.readouterr().out
    assert rc == 0 and "赢家捕捉率" in out
    assert "起涨点 vs 0AMV" not in out          # capture-only 不跑起涨点分析
