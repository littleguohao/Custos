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


def test_two_pass_equals_single_pass():
    """Pass1(抽取)+Pass2(合并排名) 与单趟 capture_rank_study 结果一致。"""
    bars, dates = _synth_bars_10()
    gate = lambda df: float(df["volume"].iloc[-1]) == 1.0
    scorer = lambda df, code: {"score": 100.0 if str(code).startswith("W") else 1.0}
    single = lp.capture_rank_study(bars, dates[0], dates[-1], gate, scorer=scorer,
                                   top_pct=50.0, surface_top_n=3, min_bars=5)
    recs = lp.extract_firings(bars, dates[0], dates[-1], gate, scorer=scorer, min_bars=5, gate_window=0)
    two = lp.rank_from_firings(recs, top_pct=50.0, surface_top_n=3)
    for k in ("n_winners", "captured", "recall", "surfaced", "buried_selected_not_found"):
        assert single[k] == two[k], k


def test_sharded_pass1_merge_equals_full():
    """分片 Pass1 合并后 = 不分片(分片只切股票集合,排名在 Pass2 全域合并)。"""
    bars, dates = _synth_bars_10()
    gate = lambda df: float(df["volume"].iloc[-1]) == 1.0
    codes = sorted(bars)
    full = lp.extract_firings(bars, dates[0], dates[-1], gate, min_bars=5, gate_window=0)
    shards = []
    for i in range(3):                                   # 3 片
        sub = {c: bars[c] for k, c in enumerate(codes) if k % 3 == i}
        shards += lp.extract_firings(sub, dates[0], dates[-1], gate, min_bars=5, gate_window=0)
    a = lp.rank_from_firings(full, top_pct=50.0, surface_top_n=3)
    b = lp.rank_from_firings(shards, top_pct=50.0, surface_top_n=3)
    assert a["captured"] == b["captured"] and a["surfaced"] == b["surfaced"]
    assert a["recall"] == b["recall"]


def test_gate_window_does_not_change_firings():
    """尾窗口覆盖整段前缀时 firing 完全一致——即使 gate 递归/依赖全历史(预热语义相同)。
    ⚠️ gate_window 短于数据时递归 gate(如 KDJ)预热不同、信号可能漂移,故生产建议 ≥120;
    本测试只保证"窗口≥数据长度"这一可证明的口径。"""
    bars, dates = _synth_bars_10()
    recursive_gate = lambda df: float(df["close"].iloc[-1]) > float(df["close"].mean())  # 依赖全历史均值
    a = lp.extract_firings(bars, dates[0], dates[-1], recursive_gate, min_bars=5, gate_window=0)
    b = lp.extract_firings(bars, dates[0], dates[-1], recursive_gate, min_bars=5, gate_window=60)
    fa = {r["code"]: [d for d, _ in r["days"]] for r in a}
    fb = {r["code"]: [d for d, _ in r["days"]] for r in b}
    assert fa == fb and any(fa.values())   # 且确有 firing(不是空集恒等的水测试)


def test_rank_from_firings_tolerates_labelled_days():
    """带 horizons/特征的 firings(day 为 3 元素记录)——Pass2 不得解包崩溃。"""
    recs = [{"code": "A", "ret": 0.5, "days": [["2025-01-02", 1.0, {"fwd20": 0.3}]]},
            {"code": "B", "ret": -0.1, "days": [["2025-01-02", 0.5, {"fwd20": -0.05}]]}]
    r = lp.rank_from_firings(recs, top_pct=50.0, surface_top_n=1)
    assert r["n_winners"] == 1 and r["captured"] == 1


def test_extract_firings_feature_failure_counted():
    """恒异常的特征打分器:失败必须被计数(不得静默消失)。"""
    bars, dates = _synth_bars_10()
    gate = lambda df: float(df["volume"].iloc[-1]) == 1.0
    def bad_scorer(df, code):
        raise RuntimeError("boom")
    stats: dict = {}
    recs = lp.extract_firings(bars, dates[0], dates[-1], gate, min_bars=5, gate_window=0,
                              horizons=(5,), feature_scorers={"bad": bad_scorer}, stats=stats)
    assert stats["feature_failures"]["bad"] > 0
    assert all("f_bad" not in (d[2] if len(d) > 2 else {}) for r in recs for d in r["days"])


def test_oracle_ceiling_and_min_winner_ret():
    """oracle 上限区分'展示位不够'与'排序失败';min_winner_ret 可在 Pass2 收紧赢家口径。"""
    bars, dates = _synth_bars_10()
    gate = lambda df: float(df["volume"].iloc[-1]) == 1.0
    recs = lp.extract_firings(bars, dates[0], dates[-1], gate, min_bars=5, gate_window=0)
    # 展示位 top1、每日池最多2 → 完美排序也只能浮出部分;oracle 应被报出且 ≥ 实际 surfaced 率
    r = lp.rank_from_firings(recs, top_pct=50.0, surface_top_n=1)
    assert r["oracle_surfaced_rate_of_captured"] is not None
    assert r["oracle_surfaced_rate_of_captured"] >= r["surfaced_rate_of_captured"]
    assert "完美排序上限" in r["text"]
    # 赢家口径收紧:门槛高于所有输家收益 → 赢家只剩真高收益者
    tight = lp.rank_from_firings(recs, top_pct=50.0, surface_top_n=3, min_winner_ret=0.5)
    loose = lp.rank_from_firings(recs, top_pct=50.0, surface_top_n=3)
    assert tight["n_winners"] <= loose["n_winners"]


def test_winner_basis_profitable():
    """basis=profitable:先筛盈利股(ret>0)再取前 top_pct%,赢家不含下跌股。"""
    bars, dates = _synth_bars_10()          # W*=+80%以上, L*=负收益
    gate = lambda df: float(df["volume"].iloc[-1]) == 1.0
    recs = lp.extract_firings(bars, dates[0], dates[-1], gate, min_bars=5, gate_window=0)
    uni = lp.rank_from_firings(recs, top_pct=50.0, surface_top_n=3, winner_basis="universe")
    pro = lp.rank_from_firings(recs, top_pct=50.0, surface_top_n=3, winner_basis="profitable")
    assert uni["n_winners"] == 5                       # 全域前50% = 10只的一半
    assert pro["n_profitable"] == 5                    # 只有 5 只盈利
    assert pro["n_winners"] == 2                       # 盈利股(5)内前50% → 2 只
    assert pro["winner_ret_cutoff"] > 0                # 切点必为正收益
    assert "盈利股内前" in pro["text"]


def test_auc_basic():
    assert lp._auc([3, 4, 5], [0, 1, 2]) == 1.0          # 完全可分
    assert lp._auc([0, 1, 2], [3, 4, 5]) == 0.0          # 完全反向
    a = lp._auc([1, 1, 1], [1, 1, 1])
    assert a == 0.5                                       # 全并列=无判别力
    assert lp._auc([], [1]) is None


def test_discriminate_detects_real_and_rejects_noise():
    """判别研究:真预测特征应 AUC 高+精确率显著>基准;噪声特征应 AUC≈0.5、精确率≈基准。"""
    import random
    random.seed(7)
    recs = []
    for k in range(200):
        will_run = random.random() < 0.2                  # 约20% 会跑(与日期解耦,同日混合)
        day = random.randint(1, 20)
        fwd = 0.6 + random.random() * 0.2 if will_run else random.random() * 0.1 - 0.05
        recs.append({"code": f"C{k:03d}", "ret": fwd, "days": [[
            f"2024-09-{day:02d}", 0.0,
            {"fwd20": round(fwd, 4),
             "f_real": 0.9 + random.random() * 0.1 if will_run else random.random() * 0.5,
             "f_noise": random.random()},
        ]]})
    r = lp.discriminate_at_signal(recs, horizon=20, win_top_q=0.2, picks_per_day=1)
    byf = {f["feature"]: f for f in r["features"]}
    assert byf["real"]["auc"] > 0.9                        # 真特征被识别
    assert byf["real"]["lift_pp"] > 20                     # 精确率显著高于基准
    assert 0.4 < byf["noise"]["auc"] < 0.6                 # 噪声≈无判别力
    assert abs(byf["noise"]["lift_pp"]) < 20
    assert "信号" in r["text"]


def test_discriminate_needs_horizon_data():
    recs = [{"code": "C1", "ret": 0.5, "days": [["2024-09-02", 1.0]]}]   # 无 extra dict
    r = lp.discriminate_at_signal(recs, horizon=20)
    assert r["n"] == 0 and "Pass1" in r["text"]


def test_extract_firings_emits_horizons_and_features():
    bars, dates = _synth_bars_10()
    gate = lambda df: float(df["volume"].iloc[-1]) == 1.0
    recs = lp.extract_firings(bars, dates[0], dates[-1], gate, min_bars=5, gate_window=0,
                              horizons=(5,), feature_scorers={"const": lambda df, c: {"score": 1.5}})
    got = [d for r in recs for d in r["days"] if len(d) >= 3]
    assert got, "应带 extra 字典"
    ex = got[0][2]
    assert "fwd5" in ex and "mfe5" in ex and ex["f_const"] == 1.5


def _dis_recs(rows):
    """rows: [(code, date, y, feats)] → discriminate_at_signal 需要的 records。"""
    out = []
    for code, date, y, feats in rows:
        ex = {"fwd20": y}
        ex.update({f"f_{k}": v for k, v in feats.items()})
        out.append({"code": code, "ret": y, "days": [[date, 0.0, ex]]})
    return out


def test_fair_baseline_removes_small_pool_bias():
    """噪声特征在'每日随机公平基线'下净增益≈0(旧口径用全局基准率会凭空造出正增益)。"""
    import random
    random.seed(3)
    rows = []
    for d in range(60):
        pool = 2 if d % 2 == 0 else 30           # 小池日/大池日交替
        wr = 0.6 if pool == 2 else 0.1           # 小池日胜率高 → 制造结构偏差
        for j in range(pool):
            y = 1.0 if random.random() < wr else 0.0
            rows.append((f"C{d}_{j}", f"2024-09-{d % 28 + 1:02d}", y, {"noise": random.random()}))
    r = lp.discriminate_at_signal(_dis_recs(rows), horizon=20, win_thresh=0.5, picks_per_day=3)
    noise = {f["feature"]: f for f in r["features"]}["noise"]
    assert abs(noise["lift_pp"]) < 6                    # 相对公平基线≈0
    assert noise["fair_random_precision"] > r["base_rate"]   # 公平基线本身高于全局基准(即偏差来源)
    assert "无判别力" in r["text"]


def test_constant_feature_flagged():
    """门槛内零方差的特征(如 reversal_k 里的 reversal_quality 恒=4)被标记、不计入可用。"""
    rows = [(f"C{i}", f"2024-09-{i % 10 + 1:02d}", float(i % 4 == 0), {"const": 4.0})
            for i in range(80)]
    r = lp.discriminate_at_signal(_dis_recs(rows), horizon=20, win_thresh=0.5, picks_per_day=3)
    c = {f["feature"]: f for f in r["features"]}["const"]
    assert c["constant"] is True and c["auc"] is None
    assert "恒定" in r["text"] and "无判别力" in r["text"]


def test_within_day_auc_beats_day_effect():
    """日内有真判别力、但日期效应使全局AUC反向时,日内AUC 应正确识别(Simpson 悖论)。"""
    rows = []
    for d in range(40):
        hi_day = d % 2 == 0                       # 偶数日:特征整体高但胜率低(日期效应)
        for j in range(10):
            good = j < 3                          # 日内前3名是赢家
            base = 10.0 if hi_day else 0.0
            feat = base + (5.0 if good else 0.0) + j * 0.01
            y = 1.0 if (good and not hi_day) or (good and hi_day and j == 0) else 0.0
            rows.append((f"C{d}_{j}", f"2024-09-{d % 28 + 1:02d}", y, {"mixed": feat}))
    r = lp.discriminate_at_signal(_dis_recs(rows), horizon=20, win_thresh=0.5, picks_per_day=3)
    m = {f["feature"]: f for f in r["features"]}["mixed"]
    assert m["auc"] > m["auc_pooled"]             # 日内AUC 高于被日期效应污染的全局AUC
    assert m["auc"] > 0.6


def test_build_sector_features_and_firings_integration(tmp_path):
    # 有利板块(上行)+ 不利板块(下行):as-of 特征值正确;未分类返回空(不误标 0)
    n = 130
    dates = [str(d)[:10] for d in pd.date_range("2022-01-03", periods=n, freq="B")]
    up = [10 + 0.15 * i for i in range(n)]
    down = [30 - 0.15 * i for i in range(n)]
    for name, closes in (("880201.SH", up), ("880900.SH", down)):
        (tmp_path / f"{name}.csv").write_text(
            "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, closes)), encoding="utf-8")
    members = {"880201.SH": ["600000"], "880900.SH": ["000002"]}
    fn = lp.build_sector_features(tmp_path, members, mom_days=20)
    r_up = fn("600000", dates[-1])
    assert r_up["f_sector_favorable"] == 1 and r_up["f_sector_momentum"] > 0
    r_dn = fn("000002", dates[-1])
    assert r_dn["f_sector_favorable"] == 0 and r_dn["f_sector_momentum"] < 0
    assert fn("999999", dates[-1]) == {}
    # 接入 extract_firings:f_ 键随信号落盘(判别研究按 f_ 前缀收集)
    bars, bdates = _synth_bars_10()
    gate = lambda df: float(df["volume"].iloc[-1]) == 1.0
    recs = lp.extract_firings(bars, bdates[0], bdates[-1], gate, min_bars=5, gate_window=0,
                              extra_feature_fn=lambda code, date: {"f_sector_favorable": 1})
    assert recs and all(d[2]["f_sector_favorable"] == 1 for r in recs for d in r["days"])


def test_kdj_j_at_and_launch_stats():
    # 起涨点 J 被记录:深跌后反转的赢家,起涨点在底部,J 应深度超卖
    if getattr(lp.bt, "_kdj", None) is None:
        pytest.skip("kdj 不可用")
    dates = pd.date_range("2025-01-01", periods=60, freq="B")
    close = [20 - 0.3 * i for i in range(20)] + [14 + 0.6 * i for i in range(40)]
    df = pd.DataFrame({"date": dates, "open": close, "high": [c * 1.01 for c in close],
                       "low": [c * 0.99 for c in close], "close": close, "volume": [1e6] * 60})
    ds = [str(d)[:10] for d in dates]
    res = lp.analyze({"WIN": df}, {}, ds[0], ds[-1], entry_gate=lambda s: True,
                     top_pct=100, buffer_days=0, min_bars=15)
    L = res["launches"][0]
    st = res["j_at_launch_stats"]
    assert st["n"] == 1 and L["j_at_launch"] < 30                      # 底部起涨,J 低
    assert L["j_at_launch"] == lp._kdj_j_at(df, L["idx"])              # 与 as-of 切片口径一致
    assert "起涨点 J 值" in res["text"]
