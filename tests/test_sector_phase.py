# -*- coding: utf-8 -*-
"""板块相位(sector_phase)测试。"""
import numpy as np
import pytest

from screening import sector_phase as sp


@pytest.fixture(autouse=True)
def _fixture_sector_names(monkeypatch):
    # 本文件用 880201/880900 做虚构板块;真实 tdxzs.cfg 里 880201="黑龙江"(地区,type3)
    # 会被"剔除地区/风格"口径排除 → 测试结果随机器环境漂移。统一注入名称表,
    # 把 fixture 板块标为概念(type4),剔除语义本身由 test_sector_mainstream 覆盖。
    monkeypatch.setattr("tq_sector.load_sector_names",
                        lambda path=None: {"880201": {"name": "测试概念A", "tdx_type": "4"},
                                           "880900": {"name": "测试概念B", "tdx_type": "4"}})


def test_phase_unavailable_short():
    assert sp.compute_sector_phase([10.0] * 10)["available"] is False


def test_phase_favorable_uptrend():
    # 稳步上行 → DIF>0、无顶背离 → 有利
    close = list(10 + 0.15 * np.arange(120))
    r = sp.compute_sector_phase(close)
    assert r["available"] and r["above_zero"] and r["favorable"] and not r["exhausted"]


def test_phase_downtrend_not_favorable():
    close = list(30 - 0.15 * np.arange(120))     # 单边下行 → DIF<0
    r = sp.compute_sector_phase(close)
    assert r["available"] and not r["above_zero"] and not r["favorable"]
    assert r["phase"] == "水下/调整"


def test_phase_top_divergence_filtered():
    # 顶背离检测(用较长干净序列+显式 lookback,避免 MACD 预热/回看窗口干扰):
    # 峰1陡(DIF高)→深回调→峰2缓升创新高(DIF低);末尾回调右确认峰2。
    up1 = 10 + 0.30 * np.arange(60)                # 陡升60根,MACD充分预热,峰≈27.7
    pull1 = up1[-1] - 0.25 * np.arange(1, 21)      # 深回调→≈22.7(DIF大降)
    up2 = pull1[-1] + 0.10 * np.arange(1, 61)      # 缓升创新高≈28.7(DIF更低)
    pull2 = up2[-1] - 0.30 * np.arange(1, 9)       # 末尾回调 → 确认峰2
    close = list(up1) + list(pull1) + list(up2) + list(pull2)
    r = sp.compute_sector_phase(close, lookback=200)
    assert r["available"] and r["above_zero"]
    assert r["exhausted"] and not r["favorable"]   # 顶背离/三打 → 过滤


def test_favorable_series_causal_and_gate(tmp_path):
    import numpy as np
    import pandas as pd
    # 有利板块:稳步上行(DIF>0,无顶背离);不利板块:单边下行(DIF<0)
    n = 130
    dates = [str(d)[:10] for d in pd.date_range("2022-01-03", periods=n, freq="B")]
    up = list(10 + 0.15 * np.arange(n))
    down = list(30 - 0.15 * np.arange(n))
    (tmp_path / "880201.SH.csv").write_text(
        "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, up)), encoding="utf-8")
    (tmp_path / "880900.SH.csv").write_text(
        "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, down)), encoding="utf-8")
    fav = sp.favorable_series(dates, up)
    assert fav[dates[-1]] is True and all(v in (True, False) for v in fav.values())   # 因果布尔
    members = {"880201.SH": ["600000"], "880900.SH": ["000002"]}
    gate = sp.load_sector_gate(tmp_path, members)
    last = dates[-1]
    assert gate("600000", last) is True     # 有利板块成员 → 放行
    assert gate("000002", last) is False    # 不利板块(DIF<0)成员 → 拦截
    assert gate("999999", last) is True     # 未分类 → 不过滤

    # build_phase_resolver(LIVE hint):当前相位
    resolve = sp.build_phase_resolver(tmp_path, members)
    assert resolve("600000")["favorable"] is True and resolve("600000")["available"] is True
    assert resolve("000002")["favorable"] is False
    assert resolve("999999")["available"] is False   # 未分类 → 无相位


def test_favorable_series_no_lookahead():
    # 因果性兜底:在序列尾部追加暴跌/尖峰,已有日期的 fav 不得改变
    import pandas as pd
    n = 120
    dates = [str(d)[:10] for d in pd.date_range("2022-01-03", periods=n, freq="B")]
    close = list(10 + 0.12 * np.arange(n))
    fav1 = sp.favorable_series(dates, close)
    ext_dates = dates + [str(d)[:10] for d in pd.date_range("2022-06-27", periods=15, freq="B")]
    crash = close + [close[-1] * (0.9 ** i) for i in range(1, 16)]          # 尾部崩 15 根
    fav2 = sp.favorable_series(ext_dates, crash)
    assert all(fav2[d] == v for d, v in fav1.items())                       # 历史结论不被未来改写
    spike = close + [close[-1] * (1.2 ** i) for i in range(1, 16)]          # 尾部暴拉出新摆动高点
    fav3 = sp.favorable_series(ext_dates, spike)
    assert all(fav3[d] == v for d, v in fav1.items())


def test_gate_metadata_and_norm6(tmp_path):
    import pandas as pd
    dates = [str(d)[:10] for d in pd.date_range("2022-01-03", periods=130, freq="B")]
    up = list(10 + 0.15 * np.arange(130))
    (tmp_path / "880201.SH.csv").write_text(
        "date,close\n" + "\n".join(f"{d},{c}" for d, c in zip(dates, up)), encoding="utf-8")
    members = {"880201.SH": ["SH600000"], "880900.SH": ["000002"]}   # 880900 无 CSV
    gate = sp.load_sector_gate(tmp_path, members)
    assert gate.n_sectors == 1                       # 只有 1 个板块真有数据(防"看似生效"假象)
    assert gate.effective_start == dates[0]
    assert gate("600000", dates[-1]) is True         # "SH600000" 归一化为 600000 命中映射
    # 数据起点之前的日期 → 已分类个股被拦(语义显式,调用方负责提示)
    assert gate("600000", "2021-01-04") is False


def test_phase_dirty_input_no_raise():
    # 非数值/NaN 输入:不 raise,且 dif 不输出 NaN(否则下游 json.dumps 出非法 JSON)
    r = sp.compute_sector_phase(["10", "x", None, 10.5] + [10 + 0.1 * i for i in range(80)])
    assert r["available"] and r["dif"] == r["dif"]
    r2 = sp.compute_sector_phase(["a", "b", "c"])
    assert r2["available"] is False


def test_fetcher_to_close_frame():
    import pandas as pd
    from local_tdx import fetch_sector_index_history as fsh
    df = pd.DataFrame({"Close": [1.0, 2.0]},
                      index=pd.to_datetime(["2022-01-03", "2022-01-04"]))
    out = fsh._to_close_frame({"880201.SH": df}, "880201.SH")
    assert list(out["date"]) == ["2022-01-03", "2022-01-04"] and list(out["close"]) == [1.0, 2.0]
    # RangeIndex 垃圾输入 → 日期不可解析 → None(不得静默落盘)
    junk = pd.DataFrame({"Close": [1.0, 2.0]})
    assert fsh._to_close_frame(junk, "X") is None
    assert fsh._to_close_frame({"X": None}, "X") is None
