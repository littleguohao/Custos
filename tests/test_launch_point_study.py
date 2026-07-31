# -*- coding: utf-8 -*-
"""起涨点 vs 0AMV 研究(launch_point_study)测试。"""
import json
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


def test_load_margin_covers_gate_window(tmp_path, monkeypatch):
    """加载裕量必须 ≥ gate_window(默认120 > buffer 60):否则窗首 ~45 个交易日的信号
    KDJ 预热不足,且截断程度随信号位置变化。裕量 = max(buffer, gate)×1.6+10 日历日。"""
    captured = {}
    def fake_load(codes, count, start=None, end=None, root=None):
        captured["start"] = start
        return {}
    monkeypatch.setattr("s_data.load_bars_qlib", fake_load)
    monkeypatch.setattr(lp.bt, "load_amv_regime", lambda since="2015-01-01", root=None: {})
    rc = lp.main(["--codes", "600000", "--start", "2025-01-01", "--end", "2025-06-30",
                  "--buffer-days", "60", "--gate-window", "120",
                  "--sector-members", str(tmp_path / "none.json")])
    assert rc == 0
    # max(60,120)×1.6+10 = 202 日历日 → 2025-01-01 回溯至 2024-06-13 或更早
    assert captured["start"] <= "2024-06-13"


def test_emit_firings_atomic_write_with_param_header(tmp_path):
    """firings 写盘:原子写(不留 .tmp)且头部带断点续跑校验所需的关键参数。"""
    bars, dates = _synth_bars_10()
    out = tmp_path / "f.json"
    rc = lp.main(["--codes", ",".join(sorted(bars)), "--start", dates[0], "--end", dates[-1],
                  "--entry-filter", "reversal_k", "--rank-score", "none", "--buffer-days", "0",
                  "--feature-scores", "momentum", "--delisted-ret", "-1.0",
                  "--emit-firings", str(out),
                  "--sector-members", str(tmp_path / "none.json")],
                 loader=lambda codes, _n: {c: bars[c] for c in codes if c in bars})
    assert rc == 0
    head = json.loads(out.read_text(encoding="utf-8"))       # 完整可解析(原子写,无半截)
    assert "records" in head and head["entry_filter"] == "reversal_k"
    assert head["rank_score"] == "none" and head["feature_scores"] == "momentum"
    assert head["delisted_ret"] == -1.0 and head["universe"] == "codes"
    assert not (tmp_path / "f.json.tmp").exists()            # tmp 已 replace,无残留


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


def test_inverse_predictor_not_killed():
    """完美**反向**预测特征(越小越会跑)必须被识别为可用(取反方向),
    且 AUC=0.0 不能因 `v or '-'` 的假零被渲染成缺失。"""
    rows = []
    for d in range(1, 21):
        for j in range(10):
            y = 1.0 if j < 3 else 0.0                    # 日内前3名会跑
            rows.append((f"C{d}_{j}", f"2024-09-{d:02d}", y, {"inv": float(j)}))  # 特征越大越差
    r = lp.discriminate_at_signal(_dis_recs(rows), horizon=20, win_thresh=0.5, picks_per_day=3)
    f = {x["feature"]: x for x in r["features"]}["inv"]
    assert f["auc"] == 0.0 and f["auc_edge"] == 0.5 and f["direction"] == "low"
    assert f["precision_at_daily_top"] == 0.0            # 同向选=全错
    assert f["precision_at_daily_bottom"] == 1.0         # 取反选=全对
    assert f["lift_pp_effective"] > 20 and f["split_consistent"] is True
    assert "无判别力" not in r["text"] and "取反" in r["text"]
    assert " 0.0 " in r["text"].replace("\n", " ")       # AUC 0.0 真的印出来了,不是 '-'


def test_split_inconsistent_marked_not_usable():
    """前半程强正、后半程反向 → 即使全样本 AUC/增益达标,也只能标'疑过拟合',不进弱可用。"""
    rows = []
    for d in range(1, 21):                                # 前半程:特征越大越会跑(AUC=1)
        for j in range(10):
            rows.append((f"A{d}_{j}", f"2024-09-{d:02d}", 1.0 if j < 3 else 0.0, {"flip": -float(j)}))
    for d in range(1, 21):                                # 后半程:反向(AUC<0.5)
        for j in range(10):
            win = j < 3
            feat = 0.4 if win else (0.3 if j < 6 else 0.5)
            rows.append((f"B{d}_{j}", f"2024-10-{d:02d}", 1.0 if win else 0.0, {"flip": feat}))
    r = lp.discriminate_at_signal(_dis_recs(rows), horizon=20, win_thresh=0.5, picks_per_day=3)
    f = {x["feature"]: x for x in r["features"]}["flip"]
    assert f["auc"] > 0.53 and (f["lift_pp_effective"] or 0) >= 2   # 全样本口径达标
    assert f["auc_first_half"] > 0.5 > f["auc_second_half"]
    assert f["split_consistent"] is False
    assert "疑过拟合" in r["text"] and "半程不同号" in r["text"]


def test_usable_verdict_marks_in_sample_only():
    """真特征进弱可用时,文案必须显式标注'仅样本内,未 OOS'(防止被当成实盘结论)。"""
    rows = []
    for d in range(1, 41):
        for j in range(8):
            y = 1.0 if j < 2 else 0.0
            rows.append((f"C{d}_{j}", f"2024-{9 if d <= 20 else 10}-{(d - 1) % 20 + 1:02d}",
                         y, {"real": 10.0 - j}))
    r = lp.discriminate_at_signal(_dis_recs(rows), horizon=20, win_thresh=0.5, picks_per_day=2)
    f = {x["feature"]: x for x in r["features"]}["real"]
    assert f["direction"] == "high" and f["split_consistent"] is True
    assert "弱可用候选" in r["text"] and "仅样本内" in r["text"]


# --------------------------------------------------------------------------
# 「在空头段就识别未来赢家」研究:窗口枚举 / 两窗解耦 / winner 标签 / 跨窗共同点
# 依据结论#11(赢家起涨点 73% 落在空头)与 §3/§5(含退市宇宙 + 跨窗为准入门槛)
# --------------------------------------------------------------------------

def _regime(days: list[str], states: list[str]) -> dict:
    return dict(zip(days, states))


def _bdays(n: int, start: str = "2024-01-01") -> list[str]:
    return [str(d)[:10] for d in pd.date_range(start, periods=n, freq="B")]


def test_long_regime_windows_segments_and_min_days():
    d = _bdays(40)
    regime = _regime(d, ["做多"] * 10 + ["空头"] * 10 + ["做多"] * 3 + ["中性"] * 5 + ["做多"] * 12)
    segs = lp.long_regime_windows(regime, min_days=5)
    assert [(a, b, n) for a, b, n in segs] == [(d[0], d[9], 10), (d[28], d[39], 12)]   # 3日碎片被剔除
    assert len(lp.long_regime_windows(regime, min_days=1)) == 3
    assert lp.long_regime_windows(regime, min_days=5, state="空头") == [(d[10], d[19], 10)]


def test_bear_to_long_pairs_pairs_signal_and_label_windows():
    """空头段=信号窗,紧随做多段=赢家窗;末尾没有后继做多段的空头段被丢(右删失)。"""
    d = _bdays(60)
    regime = _regime(d, ["空头"] * 15 + ["做多"] * 20 + ["空头"] * 15 + ["中性"] * 10)
    pairs = lp.bear_to_long_pairs(regime, min_bear_days=10, min_long_days=15)
    assert len(pairs) == 1                                   # 第二段空头之后无做多段 → 不成对
    p = pairs[0]
    assert (p["signal_start"], p["signal_end"]) == (d[0], d[14])
    assert (p["label_start"], p["label_end"]) == (d[15], d[34])
    assert p["bear_days"] == 15 and p["long_days"] == 20 and p["signal_days"] == 15


def test_bear_to_long_pairs_never_bridges_across_a_short_long_segment():
    """回归:紧邻的做多段若短于 min_long_days,该空头段直接丢弃,**不得**跨接到更晚的长段。

    2026-07-30 首轮枚举实际踩到:2015-04 的 17 日空头被接到 2016-06 的做多段(隔一年)。
    """
    d = _bdays(120)
    regime = _regime(d, ["空头"] * 20            # 0-19   信号候选
                        + ["做多"] * 5           # 20-24  太短(<15)
                        + ["中性"] * 30          # 25-54
                        + ["空头"] * 20          # 55-74  这段才该配对
                        + ["做多"] * 25          # 75-99
                        + ["中性"] * 20)         # 100-119
    pairs = lp.bear_to_long_pairs(regime, min_bear_days=10, min_long_days=15)
    assert len(pairs) == 1, "紧邻做多段太短应丢弃,而非跨年接到后面的长段"
    assert pairs[0]["signal_start"] == d[55] and pairs[0]["label_start"] == d[75]


def test_bear_to_long_pairs_dedupes_per_label_window():
    """回归:被中性段隔开的多个空头段会指向同一个做多段 → 每个赢家窗只保留一对(取最贴近的)。

    否则同一段行情在跨窗一致性判定里被计多次,一致性虚高(§3 窗口敏感)。
    """
    d = _bdays(120)
    regime = _regime(d, ["空头"] * 15            # 0-14
                        + ["中性"] * 10          # 15-24
                        + ["空头"] * 15          # 25-39  最贴近做多段
                        + ["做多"] * 25          # 40-64
                        + ["中性"] * 55)
    pairs = lp.bear_to_long_pairs(regime, min_bear_days=10, min_long_days=15)
    assert len(pairs) == 1
    assert pairs[0]["signal_start"] == d[25] and pairs[0]["label_start"] == d[40]


def test_signal_span_since_prev_long_extends_back_over_neutral():
    """since-prev-long:信号窗前伸到上一段做多结束之后,覆盖整段下跌+筑底的建仓期。"""
    d = _bdays(120)
    regime = _regime(d, ["做多"] * 10            # 0-9   上一段做多
                        + ["中性"] * 20          # 10-29
                        + ["空头"] * 15          # 30-44
                        + ["做多"] * 25          # 45-69
                        + ["中性"] * 50)
    adj = lp.bear_to_long_pairs(regime, min_bear_days=10, min_long_days=15)[0]
    ext = lp.bear_to_long_pairs(regime, min_bear_days=10, min_long_days=15,
                                signal_span="since-prev-long")[0]
    assert adj["signal_start"] == d[30] and adj["signal_days"] == 15
    assert ext["signal_start"] == d[10] and ext["signal_days"] == 35    # 含中性段
    assert ext["label_start"] == adj["label_start"] == d[45]


def test_degenerate_melt_up_window_is_flagged():
    """普涨窗(如 2015 春:98% 上涨)里"盈利前50%"退化为中位数以上 → 必须显式标出。

    否则读表时会把 beta 当识别力(结论#12:top50% 实际等于"中位数以上")。
    """
    recs = []
    for i in range(100):
        ret = 0.9 - i * 0.008 if i < 98 else -0.2       # 98% 上涨
        recs.append({"code": f"C{i:03d}", "ret": round(ret, 4),
                     "days": [[f"2015-02-{d:02d}", 0.0, {"f_x": float(i % 7)}] for d in range(2, 6)]})
    r = lp.discriminate_at_signal(recs, label_basis="winner", winner_top_pct=50.0,
                                  winner_basis="profitable")
    wm = r["winner_meta"]
    assert wm["up_ratio"] >= 0.95 and wm["degenerate_label"] is True
    assert "普涨窗" in r["text"] and "beta" in r["text"]
    assert 0.4 < r["base_rate"] < 0.6                   # 基准率≈50%,任务退化成"挑上半区"


def test_normal_window_not_flagged_as_degenerate():
    recs = []
    for i in range(100):
        ret = 0.5 - i * 0.02                            # ~25% 上涨
        recs.append({"code": f"C{i:03d}", "ret": round(ret, 4),
                     "days": [[f"2022-06-{d:02d}", 0.0, {"f_x": float(i % 7)}] for d in range(2, 6)]})
    r = lp.discriminate_at_signal(recs, label_basis="winner", winner_top_pct=50.0,
                                  winner_basis="profitable")
    assert r["winner_meta"]["degenerate_label"] is False
    assert "退化为中位数以上" not in r["text"]                 # 非普涨窗不打退化标
    assert "无信号退市股" in r["text"] and "上涨占比偏高" in r["text"]   # 分母口径局限必须显式注明


def test_aggregate_reports_per_window_environment_and_degeneracy():
    """跨窗汇总必须先列各窗上涨占比/切点/基准率,并点名普涨窗——共同点若靠它们撑起即为 beta。"""
    melt = {"winner_meta": {"up_ratio": 0.98, "winner_ret_cutoff": 0.32,
                            "degenerate_label": True, "n_universe_all": 100, "n_profitable": 98},
            "base_rate": 0.5, "n": 400,
            "features": [{"feature": "x", "constant": False, "auc": 0.56, "direction": "high",
                          "lift_pp_effective": 5.0, "median_diff": 1.0, "n_pos": 10}]}
    normal = {"winner_meta": {"up_ratio": 0.3, "winner_ret_cutoff": 0.1,
                              "degenerate_label": False, "n_universe_all": 100, "n_profitable": 30},
              "base_rate": 0.15, "n": 300,
              "features": [{"feature": "x", "constant": False, "auc": 0.55, "direction": "high",
                            "lift_pp_effective": 4.0, "median_diff": 0.8, "n_pos": 8}]}
    agg = lp.aggregate_discriminate({"2015春": melt, "2022夏": normal})
    assert agg["degenerate_windows"] == ["2015春"]
    assert [w["window"] for w in agg["windows"]] == ["2015春", "2022夏"]
    assert agg["windows"][0]["up_ratio"] == 0.98
    assert "各窗环境" in agg["text"] and "普涨窗" in agg["text"] and "1/2" in agg["text"]
    row = {r["feature"]: r for r in agg["features"]}["x"]
    assert row["n_windows"] == 1                       # 普涨窗不计票(若计入则为 2)
    assert "已排除普涨窗" in agg["text"] and "2015春" in agg["text"]   # 被排除且点名


def _win_res(auc: float, consistent: bool = True, degenerate: bool = False):
    """单窗 discriminate 输出的最小替身(只含 aggregate 读取的字段)。"""
    wm = ({"up_ratio": 0.98, "degenerate_label": True, "n_universe_all": 100, "n_profitable": 98}
          if degenerate else
          {"up_ratio": 0.3, "degenerate_label": False, "n_universe_all": 100, "n_profitable": 30})
    return {"winner_meta": wm, "base_rate": 0.2, "n": 100,
            "features": [{"feature": "x", "constant": False, "auc": auc,
                          "direction": "high" if auc >= 0.5 else "low",
                          "lift_pp_effective": 5.0, "median_diff": 1.0, "n_pos": 10,
                          "split_consistent": consistent}]}


def test_aggregate_excludes_overfit_feature_from_voting():
    """单窗被判疑过拟合(split_consistent=False)的特征在该窗不参与跨窗计票,且被点名。"""
    res = {f"w{k}": _win_res(0.6) for k in range(4)}
    res["w3"]["features"][0]["split_consistent"] = False     # 仅 w3 疑过拟合
    agg = lp.aggregate_discriminate(res)
    row = {r["feature"]: r for r in agg["features"]}["x"]
    assert row["n_windows"] == 3 and row["hit_ratio"] == 1.0  # w3 不计票
    assert row["overfit_excluded_windows"] == ["w3"]
    assert agg["overfit_excluded"] == {"x": ["w3"]}
    assert "疑过拟合" in agg["text"] and "w3" in agg["text"]


def test_aggregate_excludes_degenerate_window_from_voting():
    """普涨窗不计入 hit_ratio 分子分母(高分 beta 不得把中位 AUC 抬高),文本点名说明。"""
    res = {f"w{k}": _win_res(0.55) for k in range(2)}
    res["2015春"] = _win_res(0.9, degenerate=True)           # 普涨窗的 0.9 不得参与
    agg = lp.aggregate_discriminate(res)
    row = {r["feature"]: r for r in agg["features"]}["x"]
    assert row["n_windows"] == 2 and row["median_auc"] == 0.55
    assert agg["degenerate_windows"] == ["2015春"]
    assert "已排除普涨窗" in agg["text"] and "2015春" in agg["text"]


def test_all_windows_degenerate_reports_not_tested_not_no_common():
    """回归:所有窗都被剔除时不能打"无跨窗共同点"——那是"一个窗都没算",不是"算了但分不出来"。

    两句在报告里是完全不同的结论;混淆会把"未能检验"写成"判别不出来"。
    """
    melt = {"winner_meta": {"up_ratio": 0.98, "degenerate_label": True,
                            "n_universe_all": 100, "n_profitable": 98},
            "base_rate": 0.5, "n": 400,
            "features": [{"feature": "x", "constant": False, "auc": 0.6, "direction": "high",
                          "lift_pp_effective": 9.0, "split_consistent": True, "n_pos": 10}]}
    agg = lp.aggregate_discriminate({"2015春": melt, "2015夏": dict(melt)})
    assert agg["n_eligible_windows"] == 0 and agg["verdict_kind"] == "not_tested"
    assert "未能检验" in agg["text"] and "无有效计票窗" in agg["text"]
    assert "无跨窗共同点" not in agg["text"]
    assert "min-winner-ret" in agg["text"]          # 给出下一步口径建议


def test_all_features_overfit_excluded_reports_not_tested():
    """有计票窗但所有特征都被判疑过拟合 → 同样是"未能检验",不构成结论。"""
    res = {"w1": {"winner_meta": {"up_ratio": 0.3, "degenerate_label": False},
                  "base_rate": 0.2, "n": 100,
                  "features": [{"feature": "x", "constant": False, "auc": 0.6, "direction": "high",
                                "lift_pp_effective": 5.0, "split_consistent": False, "n_pos": 5}]},
           "w2": {"winner_meta": {"up_ratio": 0.3, "degenerate_label": False},
                  "base_rate": 0.2, "n": 100,
                  "features": [{"feature": "x", "constant": False, "auc": 0.58, "direction": "high",
                                "lift_pp_effective": 4.0, "split_consistent": False, "n_pos": 5}]}}
    agg = lp.aggregate_discriminate(res)
    assert agg["n_eligible_windows"] == 2 and agg["verdict_kind"] == "not_tested"
    assert "没有任何特征拿到有效计票" in agg["text"]
    assert "无跨窗共同点" not in agg["text"]


def test_genuine_no_common_still_says_no_common():
    """真的算过了但没特征过线 → 仍应给出"无跨窗共同点"的实质结论。"""
    def _w(auc):
        return {"winner_meta": {"up_ratio": 0.3, "degenerate_label": False},
                "base_rate": 0.2, "n": 100,
                "features": [{"feature": "x", "constant": False, "auc": auc,
                              "direction": "high" if auc >= 0.5 else "low",
                              "lift_pp_effective": 0.2, "split_consistent": True, "n_pos": 5}]}
    agg = lp.aggregate_discriminate({"w1": _w(0.505), "w2": _w(0.502)})
    assert agg["verdict_kind"] == "no_common"
    assert "无跨窗共同点" in agg["text"] and "未能检验" not in agg["text"]


def test_explain_aggregate_separates_three_window_kinds():
    """诊断输出必须把三类窗分开:计票 / 疑过拟合被剔(反面证据) / 未测(恒定或缺失,中性)。

    实跑里 alpha101 只在 5/9 窗有效,而"另外 4 窗为何缺"决定结论方向完全相反。
    """
    agg = {
        "n_windows": 10, "n_eligible_windows": 9, "degenerate_windows": ["W普涨"],
        "windows": [{"window": f"W{i}", "degenerate_label": False} for i in range(1, 10)]
                   + [{"window": "W普涨", "degenerate_label": True}],
        "features": [{"feature": "alpha101", "median_auc": 0.5405, "median_edge": 0.0405,
                      "median_lift_pp": 7.6, "hit_ratio": 1.0, "same_direction_windows": 5,
                      "overfit_excluded_windows": ["W6", "W7"],
                      "per_window": [{"window": f"W{i}", "auc": 0.54, "lift_pp_effective": 7.6,
                                      "direction": "high"} for i in range(1, 6)]}],
    }
    txt = lp.explain_aggregate(agg)
    assert "覆盖门槛 = 6 窗" in txt and "⚠️覆盖不足(5<6)" in txt
    assert "纯噪声下 100% 同号概率 0.062" in txt          # 0.5^(5-1)
    assert txt.count("计票  W") == 5
    assert "剔除  W6" in txt and "反面证据" in txt
    assert "剔除  W7" in txt
    assert "未测  W8" in txt and "未测  W9" in txt         # 既非支持也非反对
    assert "W普涨" not in txt                              # 普涨窗不参与,不列入


def test_explain_aggregate_reports_noise_expectation_across_features():
    agg = {"n_eligible_windows": 9, "degenerate_windows": [],
           "windows": [{"window": f"W{i}", "degenerate_label": False} for i in range(1, 10)],
           "features": [
               {"feature": "a", "median_edge": 0.04, "hit_ratio": 1.0, "same_direction_windows": 4,
                "per_window": [{"window": f"W{i}", "auc": 0.54, "direction": "high"}
                               for i in range(1, 5)]},
               {"feature": "b", "median_edge": 0.02, "hit_ratio": 0.6, "same_direction_windows": 5,
                "per_window": [{"window": f"W{i}", "auc": 0.52, "direction": "high"}
                               for i in range(1, 9)]}]}
    txt = lp.explain_aggregate(agg)
    # 0.5^3 + 0.5^7 = 0.125 + 0.0078 ≈ 0.13
    assert "期望出现 0.13 个 100% 同号" in txt and "多重比较" in txt


def test_explain_single_feature_filter():
    agg = {"n_eligible_windows": 4, "degenerate_windows": [],
           "windows": [{"window": "W1", "degenerate_label": False}],
           "features": [{"feature": "keep", "median_edge": 0.05, "hit_ratio": 1.0,
                         "same_direction_windows": 1,
                         "per_window": [{"window": "W1", "auc": 0.55, "direction": "high"}]},
                        {"feature": "drop", "median_edge": 0.01, "hit_ratio": 1.0,
                         "same_direction_windows": 1,
                         "per_window": [{"window": "W1", "auc": 0.51, "direction": "high"}]}]}
    txt = lp.explain_aggregate(agg, feature="keep")
    assert "[keep]" in txt and "[drop]" not in txt


def test_main_explain_agg_reads_json_without_computation(tmp_path, capsys):
    p = tmp_path / "agg.json"
    p.write_text(json.dumps({"aggregate": {
        "n_eligible_windows": 2, "degenerate_windows": [],
        "windows": [{"window": "W1", "degenerate_label": False},
                    {"window": "W2", "degenerate_label": False}],
        "features": [{"feature": "x", "median_auc": 0.54, "median_edge": 0.04,
                      "median_lift_pp": 5.0, "hit_ratio": 1.0, "same_direction_windows": 2,
                      "per_window": [{"window": "W1", "auc": 0.54, "direction": "high"},
                                     {"window": "W2", "auc": 0.55, "direction": "high"}]}]}},
        ensure_ascii=False), encoding="utf-8")
    rc = lp.main(["--explain-agg", str(p)])
    out = capsys.readouterr().out
    assert rc == 0 and "[x] 计票 2 窗" in out and "覆盖门槛 = 2 窗" in out


def test_bear_to_long_pairs_can_include_long_head():
    """可把做多段头部 N 日纳入信号窗——覆盖那 ~27% 起涨点落在做多的情况(结论#11)。"""
    d = _bdays(60)
    regime = _regime(d, ["空头"] * 15 + ["做多"] * 20 + ["中性"] * 25)
    p = lp.bear_to_long_pairs(regime, min_bear_days=10, min_long_days=15,
                              include_long_head_days=5)[0]
    assert p["signal_end"] == d[19]                           # 空头末 + 做多头部5日
    assert p["label_start"] == d[15]                          # 赢家窗仍是整个做多段


def _two_window_bars():
    """两段行情:空头段(前30根,普跌) + 做多段(后30根)。
    WIN 在做多段大涨且空头段末尾特征值高;LOSE 平淡;DEAD 空头段就退市(数据在第30根断掉)。"""
    d = _bdays(60, "2024-01-01")
    bars = {}
    win = [30 - 0.4 * i for i in range(30)] + [18 + 0.9 * i for i in range(30)]
    lose = [30 - 0.3 * i for i in range(30)] + [21 + 0.02 * i for i in range(30)]
    bars["WIN"] = pd.DataFrame({"date": d, "open": win, "high": [c * 1.01 for c in win],
                                "low": [c * 0.99 for c in win], "close": win, "volume": [1e6] * 60})
    bars["LOSE"] = pd.DataFrame({"date": d, "open": lose, "high": [c * 1.01 for c in lose],
                                 "low": [c * 0.99 for c in lose], "close": lose, "volume": [1e6] * 60})
    dead_c = [30 - 0.9 * i for i in range(30)]
    bars["DEAD"] = pd.DataFrame({"date": d[:30], "open": dead_c, "high": [c * 1.01 for c in dead_c],
                                 "low": [c * 0.99 for c in dead_c], "close": dead_c,
                                 "volume": [1e6] * 30})
    return bars, d


def test_extract_firings_decouples_signal_and_label_windows():
    """核心:信号在空头段采集,ret 按随后做多段算(两窗混用会把赢家定义成'空头里跌得少')。"""
    bars, d = _two_window_bars()
    gate = lambda df: True                                    # 每根都算信号,便于校验窗口切分
    recs = lp.extract_firings(bars, d[0], d[29], gate, min_bars=5, gate_window=0,
                              ret_start=d[30], ret_end=d[59])
    by = {r["code"]: r for r in recs}
    assert all(x[0] <= d[29] for x in by["WIN"]["days"])       # 信号只在空头段
    assert by["WIN"]["ret"] > 1.0                              # 赢家窗(做多段)大涨
    assert by["LOSE"]["ret"] < 0.05
    # 同一份 bars 若不解耦(ret 用信号窗),赢家会变成"空头里跌得少"的那只 → 结论反转
    same = {r["code"]: r for r in lp.extract_firings(bars, d[0], d[29], gate, min_bars=5,
                                                     gate_window=0)}
    assert same["WIN"]["ret"] < 0 and same["LOSE"]["ret"] < 0
    assert same["LOSE"]["ret"] > same["WIN"]["ret"]            # 口径错就会选出 LOSE


def test_delisted_kept_as_loser_removes_survivorship_bias():
    """空头段就退市的票:默认被丢(重新引入幸存者偏差),传 --delisted-ret 后按大亏计入非赢家。"""
    bars, d = _two_window_bars()
    gate = lambda df: True
    dropped = {r["code"]: r for r in lp.extract_firings(bars, d[0], d[29], gate, min_bars=5,
                                                        gate_window=0, ret_start=d[30], ret_end=d[59])}
    assert dropped["DEAD"]["ret"] is None                      # 无赢家窗价格 → 无标签
    kept = {r["code"]: r for r in lp.extract_firings(bars, d[0], d[29], gate, min_bars=5,
                                                     gate_window=0, ret_start=d[30], ret_end=d[59],
                                                     delisted_ret=-1.0)}
    assert kept["DEAD"]["ret"] == -1.0 and kept["DEAD"]["delisted"] is True
    r = lp.discriminate_at_signal(list(kept.values()), label_basis="winner", winner_top_pct=50.0)
    assert r["winner_meta"]["n_universe_all"] == 3              # 退市股进入分母,不再消失
    assert r["winner_meta"]["n_profitable"] == 2                # WIN 大涨 / LOSE 微涨 / DEAD 清零
    assert (r["winner_meta"]["winner_ret_cutoff"] or 0) > 0     # 赢家切点为正,DEAD 不可能入选


def test_winner_label_basis_labels_by_window_winner():
    """label_basis=winner:标签=该股是否为赢家窗赢家(盈利前 top%),不需要 --horizons。"""
    recs = []
    for i in range(40):
        winner = i < 10
        ret = 0.8 - i * 0.01 if winner else (0.05 - i * 0.002)
        days = [[f"2024-09-{d:02d}", 0.0, {"f_x": (5.0 if winner else 1.0) + d * 0.01}]
                for d in range(1, 11)]
        recs.append({"code": f"C{i:03d}", "ret": round(ret, 4), "days": days})
    r = lp.discriminate_at_signal(recs, label_basis="winner", winner_top_pct=50.0,
                                  winner_basis="profitable", picks_per_day=3)
    f = {x["feature"]: x for x in r["features"]}["x"]
    assert r["label_basis"] == "winner" and r["n_censored"] == 0
    assert r["winner_meta"]["winner_basis"] == "profitable"
    assert f["auc"] > 0.9 and f["direction"] == "high"
    assert f["median_win"] > f["median_lose"]                  # 共同点画像:赢家中位显著更高
    assert "赢家" in r["text"] and "winner" in r["text"]


def test_winner_label_needs_window_return():
    r = lp.discriminate_at_signal([{"code": "C1", "days": [["2024-09-02", 0.0, {"f_x": 1.0}]]}],
                                  label_basis="winner")
    assert r["n"] == 0 and "窗口收益" in r["text"]


def _win_recs(seed_high: bool, tag: str):
    """构造一窗记录:seed_high=True 时赢家特征高(同向),False 时赢家特征低(反向)。
    输家收益含负值(上涨占比 ~27% < 80%),避免被 aggregate 判为普涨窗而整窗剔出计票。"""
    recs = []
    for i in range(30):
        winner = i < 8
        ret = 0.6 - i * 0.01 if winner else (0.04 - i * 0.006)
        hi = 5.0 if winner == seed_high else 1.0
        days = [[f"{tag}-{d:02d}", 0.0, {"f_x": hi + d * 0.01}] for d in range(1, 9)]
        recs.append({"code": f"{tag}C{i:03d}", "ret": round(ret, 4), "days": days})
    return recs


def test_aggregate_requires_cross_window_consistency():
    """三窗同向 → 跨窗共同点;再加一窗反向(同号率 75%→ 仍需 median 达标)则按同号率判定。"""
    same = {t: lp.discriminate_at_signal(_win_recs(True, t), label_basis="winner")
            for t in ("2024-09", "2024-10", "2024-11")}
    agg = lp.aggregate_discriminate(same)
    row = {r["feature"]: r for r in agg["features"]}["x"]
    assert row["cross_window_common"] is True and row["hit_ratio"] == 1.0 and row["n_windows"] == 3
    assert "x" in agg["common"] and "跨窗共同点候选" in agg["text"] and "须 walk-forward" in agg["text"]

    mixed = dict(same)
    mixed["2024-12"] = lp.discriminate_at_signal(_win_recs(False, "2024-12"), label_basis="winner")
    mixed["2025-01"] = lp.discriminate_at_signal(_win_recs(False, "2025-01"), label_basis="winner")
    agg2 = lp.aggregate_discriminate(mixed)
    row2 = {r["feature"]: r for r in agg2["features"]}["x"]
    assert row2["hit_ratio"] < 0.75 and row2["cross_window_common"] is False
    assert agg2["common"] == [] and "无跨窗共同点" in agg2["text"]


def test_aggregate_skips_constant_and_unusable():
    agg = lp.aggregate_discriminate({"w1": {"features": [
        {"feature": "c", "constant": True, "auc": 0.9},
        {"feature": "d", "constant": False, "auc": None}]}})
    assert agg["features"] == [] and agg["common"] == []


def test_main_list_window_pairs(tmp_path, monkeypatch, capsys):
    d = _bdays(60)
    regime = _regime(d, ["空头"] * 15 + ["做多"] * 20 + ["中性"] * 25)
    monkeypatch.setattr(lp.bt, "load_amv_regime", lambda since=None: regime)
    out = tmp_path / "pairs.json"
    rc = lp.main(["--list-window-pairs", "--min-bear-days", "10", "--min-window-days", "15",
                  "--out", str(out)])
    assert rc == 0
    txt = capsys.readouterr().out
    assert "空头(信号窗) → 紧邻做多段(赢家窗)" in txt and "结论#11" in txt
    assert "每个赢家窗只留一对" in txt and "signal_span=adjacent" in txt
    p = json.loads(out.read_text(encoding="utf-8"))["window_pairs"][0]
    assert p["signal_end"] == d[14] and p["label_start"] == d[15]


def test_main_per_window_discriminate_labels_two_windows(tmp_path, capsys):
    """--per-window:每个 firings 文件=一对窗口,分窗输出 + 跨窗汇总;标签体现信号窗→赢家窗。"""
    files = []
    for t in ("2024-09", "2024-10"):
        p = tmp_path / f"f_{t}.json"
        p.write_text(json.dumps({"start": f"{t}-01", "end": f"{t}-15",
                                 "ret_start": f"{t}-16", "ret_end": f"{t}-28",
                                 "records": _win_recs(True, t)}, ensure_ascii=False),
                     encoding="utf-8")
        files.append(str(p))
    out = tmp_path / "agg.json"
    rc = lp.main(["--from-firings", ",".join(files), "--discriminate", "--per-window",
                  "--label-basis", "winner", "--out", str(out)])
    assert rc == 0
    txt = capsys.readouterr().out
    assert "信号2024-09-01~2024-09-15→赢家2024-09-16~2024-09-28" in txt
    assert "跨多头区间" in txt
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["aggregate"]["n_windows"] == 2 and len(data["per_window"]) == 2


# --------------------------------------------------------------------------
# 风格因子(上市板/成交额代理)、收益分布画像、双口径覆盖度
# --------------------------------------------------------------------------

def test_board_of_covers_all_boards():
    assert lp.board_of("688111") == "科创板" and lp.board_of("689009") == "科创板"
    assert lp.board_of("300750") == "创业板" and lp.board_of("301111") == "创业板"
    assert lp.board_of("920123") == "北交所" and lp.board_of("830799") == "北交所"
    assert lp.board_of("600000") == "沪主板" and lp.board_of("605100") == "沪主板"
    assert lp.board_of("000001") == "深主板" and lp.board_of("002594") == "深主板"
    assert lp.board_of("999999") == "其他" and lp.board_of("") == "其他"


def _dist_recs():
    """构造:创业板整体涨得猛、沪主板平淡;部分票有信号。"""
    recs = []
    for i in range(50):
        recs.append({"code": f"3007{i:02d}", "ret": 0.6 - i * 0.005,
                     "days": [["2024-09-02", 0.0, {}]] if i % 2 == 0 else []})
    for i in range(50):
        recs.append({"code": f"6000{i:02d}", "ret": 0.05 - i * 0.004,
                     "days": [["2024-09-02", 0.0, {}]] if i % 5 == 0 else []})
    return recs


def test_distribution_report_bands_and_boards():
    """收益分布画像:分位/涨幅带/按上市板分组,且区分'全域'与'有信号子集'。"""
    r = lp.distribution_report(_dist_recs())
    assert r["n"] == 100
    assert r["all"]["median"] is not None and r["all"]["p90"] > r["all"]["median"]
    shares = [r["all"]["bands"][f">={b:.0%}"]["n"] for b in (0.0, 0.1, 0.2, 0.3, 0.5, 1.0)]
    assert shares == sorted(shares, reverse=True)          # 涨幅带只数单调不增
    boards = r["by_board"]
    assert set(boards) == {"创业板", "沪主板"}
    assert boards["创业板"]["median"] > boards["沪主板"]["median"]
    assert boards["创业板"]["n_with_signal"] == 25 and boards["沪主板"]["n_with_signal"] == 10
    assert abs(sum(b["share_of_universe"] for b in boards.values()) - 1.0) < 1e-6
    assert "按上市板" in r["text"] and "非本策略买卖规则" in r["text"]


def test_distribution_report_empty():
    assert lp.distribution_report([{"code": "600000"}])["n"] == 0


def _sim_recs(sim_rets, reasons=None, ret=0.5):
    reasons = reasons or ["bbi_exit"] * len(sim_rets)
    return [{"code": f"C{i:03d}", "ret": ret - i * 0.001,
             "days": [["2024-09-02", 0.0, {"sim_ret": sr, "sim_reason": rs, "sim_holding": 5}]]}
            for i, (sr, rs) in enumerate(zip(sim_rets, reasons))]


def test_coverage_report_flags_stop_dominated_winners():
    """赢家里多数在规则下被止损扫出 → 结论指向交易管理而非选股(结论#3/#5)。"""
    recs = _sim_recs([-0.08] * 8 + [0.3, 0.4], ["stop"] * 8 + ["bbi_exit"] * 2)
    r = lp.coverage_report(recs, winner_top_pct=100.0, winner_basis="profitable")
    assert r["n_winner_with_signal"] == 10 and r["n_winner_rule_profitable"] == 2
    assert r["coverage"] == 0.2 and r["exit_reasons"]["stop"] == 8
    assert "止损离场" in r["text"] and "交易管理" in r["text"]


def test_coverage_report_high_coverage_points_to_selection():
    recs = _sim_recs([0.25] * 9 + [-0.08], ["bbi_exit"] * 9 + ["stop"])
    r = lp.coverage_report(recs, winner_top_pct=100.0, winner_basis="profitable")
    assert r["coverage"] == 0.9 and "事前选不出" in r["text"]


def test_coverage_capture_ratio_and_best_trade_per_code():
    """捕获率=规则收益中位/区间涨幅中位;同一只多笔信号取**最好一笔**(最乐观口径)。"""
    recs = [{"code": "C1", "ret": 1.0,
             "days": [["2024-09-02", 0.0, {"sim_ret": -0.08, "sim_reason": "stop"}],
                      ["2024-09-10", 0.0, {"sim_ret": 0.5, "sim_reason": "bbi_exit"}]]}]
    r = lp.coverage_report(recs, winner_top_pct=100.0, winner_basis="profitable")
    assert r["median_sim_ret"] == 0.5 and r["median_window_ret"] == 1.0
    assert r["capture_ratio"] == 0.5 and r["exit_reasons"] == {"bbi_exit": 1}


def test_coverage_report_requires_trade_sim():
    r = lp.coverage_report([{"code": "C1", "ret": 0.5, "days": [["2024-09-02", 0.0, {}]]}])
    assert "需带 --trade-sim" in r["text"]


def test_extract_firings_trade_sim_and_style_features():
    """Pass1:--trade-sim 记 sim_ret/reason/holding;--style-features 记上市板与成交额代理。"""
    d = _bdays(80, "2024-01-01")
    close = [10 + 0.1 * i for i in range(80)]
    df = pd.DataFrame({"date": d, "open": close, "high": [c * 1.02 for c in close],
                       "low": [c * 0.98 for c in close], "close": close,
                       "volume": [1e6] * 80})
    recs = lp.extract_firings({"300750": df}, d[40], d[45], lambda x: True,
                              min_bars=30, gate_window=0,
                              trade_sim=True, style_features=True)
    ex = recs[0]["days"][0][2]
    assert "sim_ret" in ex and "sim_reason" in ex and "sim_holding" in ex
    assert ex["f_board_code"] == 1.0                      # BOARDS 里创业板序号
    assert 6.0 < ex["f_amount20"] < 8.0                   # log10(10~18 × 1e6) ≈ 7


def test_recall_by_band_detects_negative_selection_on_big_winners():
    """核心:召回率随涨幅单调下降 ⇒ 入场门槛对大牛股是负选择,瓶颈在召回而非排序。

    构造:小涨票大多有信号,大涨票大多没有(复刻实跑形态:≥100% 召回 15.6% vs 基准 25%)。
    """
    recs = []
    for i in range(200):                                  # 小涨(5%):80% 有信号
        recs.append({"code": f"6000{i:02d}", "ret": 0.05,
                     "days": [["2024-09-02", 0.0, {}]] if i % 5 else []})
    for i in range(100):                                  # 大涨(120%):10% 有信号
        recs.append({"code": f"3007{i:02d}", "ret": 1.2,
                     "days": [["2024-09-02", 0.0, {}]] if i % 10 == 0 else []})
    r = lp.distribution_report(recs)
    rb = r["recall_by_band"]
    assert rb[">=0%"]["recall"] > rb[">=50%"]["recall"] > 0
    assert rb[">=100%"]["recall"] == 0.1
    assert rb[">=100%"]["vs_base_pct"] < -0.5             # 相对基准显著不足
    assert "大涨幅段召回不足" in r["text"] and "负选择" in r["text"]
    assert "召回" in r["text"] and "结论#2" in r["text"]


def test_recall_high_on_big_winners_does_not_warn():
    recs = [{"code": f"6000{i:02d}", "ret": 1.2, "days": [["2024-09-02", 0.0, {}]]}
            for i in range(50)]
    r = lp.distribution_report(recs)
    assert r["recall_by_band"][">=100%"]["recall"] == 1.0
    assert "大涨幅段召回不足" not in r["text"]


def test_zero_return_zombies_flagged():
    """收益恰好为 0 的样本占比超 2% 必须列出;但"僵尸样本"口径已被实测证伪
    (--zero-ret-report 抽样 177 只 100% 是正常成交的直线回位,停牌 0 只),
    文案必须告诫**勿剔除**、只单列观察 —— 剔除会让 up_ratio 单向上升、把窗错打成普涨窗。"""
    recs = [{"code": f"6000{i:02d}", "ret": 0.0, "days": []} for i in range(10)]
    recs += [{"code": f"3007{i:02d}", "ret": 0.2, "days": []} for i in range(90)]
    r = lp.distribution_report(recs)
    assert r["all"]["n_zero"] == 10 and r["all"]["zero_ratio"] == 0.1
    assert r["all"]["up_ratio"] == 0.9                     # ret>0 严格,零收益不计入上涨
    assert "恰好为 0" in r["text"] and "勿剔除" in r["text"] and "单列观察" in r["text"]
    assert "僵尸样本嫌疑" not in r["text"]                 # 旧口径(停牌嫌疑、建议剔除)不得复活


def test_zero_return_below_threshold_not_flagged():
    recs = [{"code": "600000", "ret": 0.0, "days": []}]
    recs += [{"code": f"3007{i:02d}", "ret": 0.2, "days": []} for i in range(199)]
    assert "僵尸样本嫌疑" not in lp.distribution_report(recs)["text"]


def test_drop_zero_ret_keeps_delisted_losers():
    """剔僵尸只针对 ret 恰好=0;按 --delisted-ret 计入的 -1.0 是真飞刀,必须留下。"""
    recs = [{"code": "A", "ret": 0.0}, {"code": "B", "ret": -1.0, "delisted": True},
            {"code": "C", "ret": 0.2}, {"code": "D", "ret": None}]
    keep, n = lp.drop_zero_ret(recs)
    assert n == 1 and {r["code"] for r in keep} == {"B", "C", "D"}


def test_exclude_zero_ret_can_flip_degenerate_window_flag():
    """核心验证点:僵尸样本进分母会压低上涨率 → 普涨窗漏标;剔除后可能翻成普涨窗。

    构造:78 只上涨 / 5 只下跌 / 17 只僵尸 → 含僵尸 up_ratio=78%(未达80%,漏标);
    剔僵尸后 78/83=94% → 正确标为普涨窗。
    """
    recs = ([{"code": f"U{i:03d}", "ret": 0.3, "days": [["2024-09-02", 0.0, {"f_x": 1.0}]]}
             for i in range(78)]
            + [{"code": f"D{i:03d}", "ret": -0.2, "days": [["2024-09-02", 0.0, {"f_x": 2.0}]]}
               for i in range(5)]
            + [{"code": f"Z{i:03d}", "ret": 0.0, "days": [["2024-09-02", 0.0, {"f_x": 3.0}]]}
               for i in range(17)])
    keep_all = lp.discriminate_at_signal(recs, label_basis="winner", winner_top_pct=50.0)
    assert keep_all["winner_meta"]["up_ratio"] == 0.78
    assert keep_all["winner_meta"]["degenerate_label"] is False        # 漏标
    cleaned = lp.discriminate_at_signal(recs, label_basis="winner", winner_top_pct=50.0,
                                        exclude_zero_ret=True)
    assert cleaned["winner_meta"]["n_zero_excluded"] == 17
    assert cleaned["winner_meta"]["up_ratio"] > 0.9
    assert cleaned["winner_meta"]["degenerate_label"] is True          # 剔除后正确识别
    assert "已剔除零收益僵尸 17 只" in cleaned["text"] and "普涨窗" in cleaned["text"]


def test_distribution_exclude_zero_ret_changes_up_ratio():
    recs = ([{"code": f"Z{i:03d}", "ret": 0.0, "days": []} for i in range(20)]
            + [{"code": f"U{i:03d}", "ret": 0.2, "days": []} for i in range(80)])
    withz = lp.distribution_report(recs)
    without = lp.distribution_report(recs, exclude_zero_ret=True)
    assert withz["all"]["up_ratio"] == 0.8 and withz["n_zero_excluded"] == 0
    assert without["all"]["up_ratio"] == 1.0 and without["n_zero_excluded"] == 20
    assert without["n"] == 80 and "已剔除零收益僵尸样本 20 只" in without["text"]


def test_coverage_exclude_zero_ret_drops_zombie_winners():
    recs = [{"code": "Z1", "ret": 0.0,
             "days": [["2024-09-02", 0.0, {"sim_ret": 0.0, "sim_reason": "open_end"}]]},
            {"code": "W1", "ret": 0.5,
             "days": [["2024-09-02", 0.0, {"sim_ret": 0.2, "sim_reason": "bbi_exit"}]]}]
    withz = lp.coverage_report(recs, winner_top_pct=100.0)
    without = lp.coverage_report(recs, winner_top_pct=100.0, exclude_zero_ret=True)
    assert withz["n_winner_with_signal"] == 1 and without["n_winner_with_signal"] == 1
    assert without["coverage"] == 1.0                       # 僵尸不再混进赢家池
