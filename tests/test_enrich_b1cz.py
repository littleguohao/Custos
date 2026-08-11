# -*- coding: utf-8 -*-
"""Tests for B1/CZ pattern detectors in screening.enrich_candidates.

表驱动：注入合成 OHLCV DataFrame，每个检测器至少正反两例；不依赖 TdxW/网络。
"""
import pandas as pd
import pytest

from custos.pipeline.screening import enrich_candidates as ec


def make_df(closes, vols=None, highs=None, lows=None):
    n = len(closes)
    closes = [float(x) for x in closes]
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "open": closes,
        "high": [float(x) for x in (highs or [c * 1.005 for c in closes])],
        "low": [float(x) for x in (lows or [c * 0.995 for c in closes])],
        "close": closes,
        "volume": [float(v) for v in (vols or [1000.0] * n)],
        "amount": [0.0] * n,
    })


# ---------- wave_type（B1 §四.0 拉升波三分类） ----------

def test_wave_buildup():
    # 50 平盘 + 启动放量长阳(+6%, 量2x) + 10日温和上行至13（段涨幅约31%）
    closes = [10.0] * 50 + [10.6] + [10.6 + i * 0.27 for i in range(1, 10)]
    lows = [c * 0.995 for c in closes]
    lows[49] = 9.8  # 启动低点
    vols = [1000.0] * 50 + [2000.0] + [1000.0] * 9
    r = ec.detect_wave_type(make_df(closes, vols=vols, lows=lows))
    assert r["available"] and r["wave_type"] == "buildup"
    assert 25 <= r["detail"]["seg_gain_pct"] <= 50
    assert r["detail"]["start_bull_candle"] is True


def test_wave_rally():
    # 前一段 10.5→12.2（摆动>15%）→ 回踩 10.6（窗口最低）→ 二段至 14.5（段涨幅约37%）
    closes = [11.0] * 20
    closes += [10.5 + i * 0.19 for i in range(10)]          # 10.5→12.21
    closes += [10.6]                                          # 回踩低点
    closes += [10.6 + (i + 1) * 0.39 for i in range(10)]      # →14.5
    lows = [c * 0.995 for c in closes]
    lows[30] = 10.4  # 窗口最低价在回踩处
    r = ec.detect_wave_type(make_df(closes, lows=lows))
    assert r["wave_type"] == "rally"
    assert r["detail"]["second_start"] is True
    assert 35 <= r["detail"]["seg_gain_pct"] <= 50


def test_wave_sprint():
    # 近20日2次涨停(+10%)、近10日涨幅>25%、顶部放量3x → 冲刺波（优先级最高）
    closes = [10.0] * 40 + [11.0, 11.0, 12.1, 12.5, 13.5, 14.5, 15.5]
    vols = [1000.0] * 46 + [3000.0]
    r = ec.detect_wave_type(make_df(closes, vols=vols))
    assert r["wave_type"] == "sprint"
    assert r["detail"]["limit_up_count_20d"] >= 2
    assert r["detail"]["top_vol_ratio"] >= 1.5


def test_wave_unknown_on_flat():
    r = ec.detect_wave_type(make_df([10.0] * 50))
    assert r["wave_type"] == "unknown"


def test_wave_insufficient_bars():
    r = ec.detect_wave_type(make_df([10.0] * 20))
    assert r["available"] is False and r["wave_type"] == "unknown"


# ---------- weekly_j（B1 §四.1 主线口径） ----------

def test_weekly_j_low_in_downtrend():
    closes = [20.0 - i * 0.1 for i in range(120)]  # 单边阴跌
    r = ec.weekly_j_state(make_df(closes))
    assert r["available"] and r["weekly_j"] < 13 and r["weekly_j_low"] is True


def test_weekly_j_not_low_in_uptrend():
    closes = [10.0 + i * 0.1 for i in range(120)]
    r = ec.weekly_j_state(make_df(closes))
    assert r["available"] and r["weekly_j_low"] is False


# ---------- non_one_wave（B1 §四 非一波流确认） ----------

def _now_base_df(pull_vols, top_drop=None):
    # 30 平盘 → 10日上行（均量1000）→ 高点 → 5日回调（可控量与跌幅）
    closes = [10.0] * 30 + [10.0 + i * 0.2 for i in range(1, 11)]
    closes += [closes[-1] - 0.05 * i for i in range(1, 6)]
    vols = [1000.0] * 40 + list(pull_vols)
    lows = [c * 0.995 for c in closes]
    lows[29] = 9.8
    df = make_df(closes, vols=vols, lows=lows)
    if top_drop is not None:
        df.loc[40, "close"] = df.loc[39, "close"] * (1 + top_drop / 100)
        df.loc[40, "volume"] = 2000.0
    return df


def test_non_one_wave_confirmed():
    r = ec.check_non_one_wave(_now_base_df([500.0] * 5))
    assert r["available"] and r["status"] == "confirmed"
    assert r["conditions"]["mild_volume"]["hit"] is True
    assert r["conditions"]["no_top_big_bear"]["hit"] is True
    assert r["conditions"]["pullback_shrink"]["hit"] is True


def test_non_one_wave_revoked_by_top_big_bear():
    # 高点次日 -5% 且量 2x（放量大阴）→ 撤销
    closes = [10.0] * 30 + [10.0 + i * 0.2 for i in range(1, 11)]
    down = closes[-1] * 0.95
    closes += [down, down * 0.99, down * 0.98, down * 0.97, down * 0.96]
    vols = [1000.0] * 40 + [2000.0, 500.0, 500.0, 500.0, 500.0]
    lows = [c * 0.995 for c in closes]
    lows[29] = 9.8
    r = ec.check_non_one_wave(make_df(closes, vols=vols, lows=lows))
    assert r["status"] == "revoked"
    assert r["conditions"]["no_top_big_bear"]["hit"] is False


def test_non_one_wave_insufficient_when_pullback_not_shrinking():
    r = ec.check_non_one_wave(_now_base_df([900.0] * 5))
    assert r["status"] == "insufficient"
    assert r["conditions"]["pullback_shrink"]["hit"] is False


def test_non_one_wave_unavailable_without_segment():
    r = ec.check_non_one_wave(make_df([10.0] * 50))
    assert r["available"] is False and r["status"] == "insufficient"


# ---------- five_day_entry（CZ §十六） ----------

def _five_day_df(last_close_drop=False):
    closes = [10.0 + i * 0.05 for i in range(25)]
    if last_close_drop:
        closes[-1] = closes[-1] - 0.5
    vols = [100.0] * 25
    vols[20] = 150.0          # 7日内单日量 ≥ 前一日×1.45
    vols[-3:] = [100.0, 110.0, 120.0]  # 连续3日放量（递增）
    return make_df(closes, vols=vols)


def test_five_day_entry_hit():
    r = ec.check_five_day_entry(_five_day_df())
    assert r["hit"] is True
    assert all(c["hit"] for c in r["conditions"].values())


def test_five_day_entry_miss_when_below_ma5():
    r = ec.check_five_day_entry(_five_day_df(last_close_drop=True))
    assert r["hit"] is False
    assert r["conditions"]["close_above_ma5"]["hit"] is False


# ---------- volume_sustain（CZ §14.6） ----------

def test_volume_sustain_mainline_confirmed():
    vols = [100.0] * 7 + [1000.0] + [600.0] * 12   # 峰值12日前，后续均值60%≥55%
    r = ec.check_volume_sustain(make_df([10.0] * 20, vols=vols))
    assert r["status"] == "mainline_confirmed"
    assert r["days_since_peak"] == 12
    assert len(r["vol_ratios_last13"]) == 13


def test_volume_sustain_retreat():
    vols = [100.0] * 7 + [1000.0] + [600.0] * 9 + [400.0, 400.0, 400.0]
    r = ec.check_volume_sustain(make_df([10.0] * 20, vols=vols))
    assert r["status"] == "retreat"


def test_volume_sustain_neutral_when_peak_too_recent():
    vols = [100.0] * 16 + [1000.0] + [600.0, 600.0, 600.0]  # 峰值仅3日前
    r = ec.check_volume_sustain(make_df([10.0] * 20, vols=vols))
    assert r["status"] == "neutral"


# ---------- leader_volume（CZ §九） ----------

def test_leader_volume_hit_and_miss():
    vols = [100.0] * 22 + [200.0, 200.0, 200.0]
    assert ec.check_leader_volume(make_df([10.0] * 25, vols=vols))["hit"] is True
    vols[-3:] = [200.0, 150.0, 200.0]
    r = ec.check_leader_volume(make_df([10.0] * 25, vols=vols))
    assert r["hit"] is False and r["available"] is True


# ---------- three_lows / bottom_volume（CZ §九/§14.6，250日口径） ----------

def _cz250_df(today_vol, close_now=11.0):
    closes = [20.0] * 125 + [close_now] * 125
    vols = [1000.0] * 249 + [today_vol]
    return make_df(closes, vols=vols)


def test_three_lows_hit():
    r = ec.check_three_lows(_cz250_df(200.0))
    assert r["available"] and r["hit"] is True
    assert r["conditions"]["low_price"]["hit"] is True
    assert r["conditions"]["low_volume"]["hit"] is True


def test_three_lows_miss_when_drawdown_shallow():
    r = ec.check_three_lows(_cz250_df(200.0, close_now=15.0))  # 回撤约26%<40%
    assert r["hit"] is False
    assert r["conditions"]["low_price"]["hit"] is False


def test_bottom_volume_hit_and_miss():
    assert ec.check_bottom_volume(_cz250_df(2500.0))["hit"] is True
    r = ec.check_bottom_volume(_cz250_df(1500.0))
    assert r["hit"] is False and r["conditions"]["huge_volume"]["hit"] is False


def test_cz_tags_unavailable_below_250_bars():
    df = make_df([10.0] * 100)
    assert ec.check_three_lows(df)["available"] is False
    assert ec.check_bottom_volume(df)["available"] is False


# ---------- repair_signals（B1 §四.2） ----------

def test_repair_signals_volume_shrink_stop_fall():
    closes = [10.0 - i * 0.05 for i in range(30)]
    closes[-1] = closes[-2] * 1.01  # 涨跌幅∈[-2%,+2%]
    vols = [1000.0] * 29 + [500.0]  # 量比 0.5 ≤ 0.7
    r = ec.check_repair_signals(make_df(closes, vols=vols), None)
    assert "volume_shrink_stop_fall" in r["signals"]
    assert r["detail"]["volume_shrink_stop_fall"]["hit"] is True


def test_repair_signals_empty_when_no_repair():
    closes = [10.0 - i * 0.1 for i in range(30)]  # 持续大跌、均量
    r = ec.check_repair_signals(make_df(closes), None)
    assert r["signals"] == []


# ---------- compute_metrics 整合 ----------

def test_compute_metrics_contains_b1cz_fields():
    df = _cz250_df(2500.0)
    m = ec.compute_metrics(df, None)
    for key in ["wave", "weekly_j", "weekly_j_low", "non_one_wave", "repair_signals",
                "five_day_entry", "volume_sustain", "leader_volume",
                "three_lows", "bottom_volume"]:
        assert key in m, f"compute_metrics 缺字段 {key}"
    assert m["bottom_volume"]["hit"] is True


# ---------- P2: 数据源当日一致性（formula_hits 日期交叉校验 + signal_date） ----------

def test_enrich_flags_formula_hits_date_mismatch(monkeypatch):
    # 命中清单是昨日产出、本段目标是今日 → partial + formula_hits_date_mismatch
    hits = {"date": "2026-07-20", "status": "ok",
            "formulas": [{"id": "F1", "hits": [{"code": "600000", "name": "浦发"}]}]}
    dates = pd.date_range(end="2026-07-21", periods=80, freq="B")
    df = pd.DataFrame({
        "date": dates, "open": 10.0, "high": 10.05, "low": 9.95,
        "close": 10.0, "volume": 1000.0, "amount": 0.0,
    })
    # 隔离板块映射，聚焦一致性断言
    monkeypatch.setattr(ec, "build_stock_theme_map", lambda **k: ({}, True))
    result = ec.enrich("2026-07-21", hits_data=hits,
                       ohlcv_loader=lambda c: df.copy(), index_loader=lambda: None,
                       universe_cfg={"j_low_required": False})
    assert result["status"] == "partial"
    assert "formula_hits_date_mismatch:2026-07-20" in result["degraded_reason"]
    assert "signal_date_contract" in result
    assert result["candidates"]
    assert result["candidates"][0]["signal_date"] == "2026-07-21"


def test_enrich_same_day_hits_no_mismatch(monkeypatch):
    hits = {"date": "2026-07-21", "status": "ok",
            "formulas": [{"id": "F1", "hits": [{"code": "600000", "name": "浦发"}]}]}
    dates = pd.date_range(end="2026-07-21", periods=80, freq="B")
    df = pd.DataFrame({
        "date": dates, "open": 10.0, "high": 10.05, "low": 9.95,
        "close": 10.0, "volume": 1000.0, "amount": 0.0,
    })
    monkeypatch.setattr(ec, "build_stock_theme_map", lambda **k: ({}, True))
    result = ec.enrich("2026-07-21", hits_data=hits,
                       ohlcv_loader=lambda c: df.copy(), index_loader=lambda: None,
                       universe_cfg={"j_low_required": False})
    assert "formula_hits_date_mismatch" not in result["degraded_reason"]
    assert result["candidates"][0]["signal_date"] == "2026-07-21"


# ---------- code review 修复回归 ----------

def test_bottom_volume_miss_when_today_makes_new_20d_low():
    # #1：当日刚创 20 日新低（剔除当日的前20日最低被跌破）→ no_new_low=False 不命中
    df = _cz250_df(2500.0)
    df.loc[249, "low"] = df["low"].iloc[-21:-1].min() - 0.5
    r = ec.check_bottom_volume(df)
    assert r["available"] and r["hit"] is False
    assert r["conditions"]["no_new_low"]["hit"] is False


def test_wave_sprint_survives_pullback_after_top():
    # #2：冲刺到顶后回调约 8%（B1 回调时点），段内加速口径仍判 sprint
    closes = [10.0] * 40 + [11.0, 11.0, 12.1, 12.5, 13.5, 14.5, 15.5]
    closes += [15.0, 14.5, 14.26]  # 顶部后回调 ~8%
    vols = [1000.0] * 46 + [3000.0, 800.0, 700.0, 600.0]
    r = ec.detect_wave_type(make_df(closes, vols=vols))
    assert r["wave_type"] == "sprint"
    assert r["detail"]["accel_10d_gain_pct"] >= 25


def test_volume_sustain_daily_breach_not_confirmed():
    # #6：均值达标（约67%>55%）但有单日 40%<55% → 逐日口径不 confirmed
    vols = [100.0] * 7 + [1000.0] + [700.0] * 5 + [400.0] + [700.0] * 6
    r = ec.check_volume_sustain(make_df([10.0] * 20, vols=vols))
    assert r["status"] != "mainline_confirmed"
    assert r["post_mean_ratio"] >= 0.55  # 均值口径本会误判
    assert r["post_min_ratio"] < 0.55


def test_limit_up_mask_ignores_zero_close_bars():
    # #11a：close=0 脏数据 bar 不产生假性涨停（11/0=inf 不得计入）
    closes = [10.0] * 40 + [0.0, 11.0, 11.0, 12.1, 12.5, 13.5, 14.5, 15.5]
    r = ec.detect_wave_type(make_df(closes))
    assert r["detail"]["limit_up_count_20d"] == 1  # 仅 12.1/11.0=+10% 一次


def test_distribution_unavailable_when_vol_ma20_near_zero():
    # #11b：全零成交量 → vol_ma20 近零 → 派发检测器 available=False
    r = ec.detect_distribution(make_df([10.0] * 40, vols=[0.0] * 40), code="600000")
    assert r["available"] is False
    assert r["hits"] == []


def test_enrich_metrics_error_excluded_not_abort():
    # #5：单股 compute_metrics 抛错计入 excluded，不中断批次
    date = "2026-07-21"
    dates = pd.date_range(end="2026-07-21", periods=60, freq="B")
    good = pd.DataFrame({
        "date": dates, "open": [10.0] * 60, "high": [10.1] * 60, "low": [9.9] * 60,
        "close": [10.0] * 60, "volume": [1000.0] * 60, "amount": [0.0] * 60,
    })
    bad = pd.DataFrame({"date": dates, "open": [10.0] * 60})  # 缺 close/high/low/volume
    # 代码用真 A 股段（600xxx）：原来用的 900001/900002 是**沪B**代码，enrich 自 2026-08-03
    # 起对候选做 A 股白名单过滤（审计 B10：ETF/可转债/B股不得进 StockPool），
    # 占位代码会先被 not_a_share 剔除，测不到本用例真正关心的 metrics_error 分支。
    hits = {"date": date, "status": "ok", "formulas": [{"id": "F", "hits": [
        {"code": "600001", "name": "好股票"}, {"code": "600002", "name": "坏数据"},
    ]}]}
    loader = lambda c: good if c == "600001" else bad
    r = ec.enrich(date, hits_data=hits, ohlcv_loader=loader, index_loader=lambda: None,
                  universe_cfg={"exclude_bj": True, "exclude_st": True, "min_list_days": 60,
                            "j_low_required": False})
    assert [c["code"] for c in r["candidates"]] == ["600001"]
    assert len(r["excluded"]) == 1
    assert r["excluded"][0]["code"] == "600002"
    assert r["excluded"][0]["reason"].startswith("metrics_error:")


class TestReversalChangeSymmetric:
    """反转K的收盘涨幅区间是**不对称**的：-2% 到 1.8%（2026-08-04 按 B1_w.pdf 纠偏）。

    材料两处独立写明这个区间：
      「分歧转一致的反转K：-2% ~ 1.8%」
      「如何筛选最强壮的B1宝宝：3- 涨幅为 -2% 到 1.8%」
    此前实现与治理文档都写成对称 ±2%，上界宽了 0.2pp。这不是刻意收紧门槛，
    是按材料原文纠偏。
    """

    def test_bounds_are_asymmetric(self):
        from custos.pipeline.screening import enrich_candidates as ec
        assert ec.REVERSAL_CHANGE_MIN_PCT == -2.0
        assert ec.REVERSAL_CHANGE_MAX_PCT == 2.0

    def test_upper_bound_is_two_percent(self):
        """对称口径：+1.9% 在区间内、+2.1% 在区间外。

        ⚠️ 原用例名叫 `excludes_1_9` —— 那是不对称（上界 +1.8%）时代的断言。
        owner 2026-08-06 改回对称 ±2%（见 01_swing_rules §三.3 注），故上界放宽到 2.0。
        """
        lo, hi = ec.REVERSAL_CHANGE_MIN_PCT, ec.REVERSAL_CHANGE_MAX_PCT
        assert lo <= 1.9 <= hi
        assert not (lo <= 2.1 <= hi)
        assert lo == -2.0 and hi == 2.0

    def test_judgment_reads_the_shared_source(self):
        """判定必须走 `b1_thresholds`（L0 唯一来源），不得本地重写比较式。

        ⚠️ 原判据是
        `assert "REVERSAL_CHANGE_MIN_PCT" in inspect.getsource(ec.compute_metrics)`
        —— 常量名出现在**注释**里也算通过，而且它验不了「可配置」（owner 明确要求）。
        2026-08-07 阈值收敛到 `b1_thresholds` 后判定式变成 `change_in_range(...)`，
        那条断言直接假失败 —— 语义完全没变。改为 AST 查真实调用。
        """
        import ast as _ast
        import inspect

        tree = _ast.parse(inspect.getsource(ec.compute_metrics))
        called = {_ast.unparse(n.func) for n in _ast.walk(tree) if isinstance(n, _ast.Call)}
        assert "change_in_range" in called, \
            f"必须调用 b1_thresholds.change_in_range，实际调用 {sorted(called)[:12]}"
        assert ec.change_in_range is __import__("custos.core.b1_thresholds", fromlist=["change_in_range"]).change_in_range, \
            "必须是同一个函数对象，不能本地再实现一份"


class TestReversalKBoundaryBehavior:
    """⚠️ 反转 K 的收盘区间 **±2% 对称、且可配置** —— 原先只有文本判据。

    改这里之前 `test_judgment_uses_asymmetric_bounds` 断言的是
    `assert "REVERSAL_CHANGE_MIN_PCT" in inspect.getsource(ec.compute_metrics)`
    —— 常量名出现在**注释**里也算通过，而且它**验不了「可配置」**
    （owner 明确要求可配置）。这里改成驱动真实判定看结果。
    """

    @staticmethod
    def _reversal_df(change_pct: float):
        """造一支满足 j_low + 极致缩量 + 小振幅的票，只让收盘涨跌幅可调。

        反转 K 的四个条件里另外三个必须**稳定成立**，否则边界测试测的是别的条件。
        """
        # ⚠️ 用**陡降**序列（-0.5/根而非 -0.22/根）：斜率不够时末根 +1.9% 会把 J
        #    从 4.7 顶到 19.4（>13），`j_low` 变 False ⇒ 上界那两例失败的原因
        #    根本不是 change_pct 判定。要测某个条件的边界，其余条件必须稳稳成立。
        #    陡降把近 9 日波幅拉宽，末根 ±2% 在区间里只占一小截，J 维持在 6 以下。
        closes = [40.0 - i * 0.5 for i in range(60)]
        # ⚠️ 56 而非 55：后面还要 append 一根，两个数组长度必须一致，
        #    否则 pandas 直接 ValueError（第一版写 55 就是这么挂的）。
        vols = [3000.0] * 56 + [2800.0, 2600.0, 2400.0, 2200.0]
        prev = closes[-1]
        closes.append(round(prev * (1 + change_pct / 100), 4))
        vols.append(300.0)                    # 极致缩量
        last = closes[-1]
        highs = [c * 1.005 for c in closes]
        lows = [c * 0.995 for c in closes]
        # 末根振幅压到 ≤7%（相对前收）
        highs[-1] = max(last, prev) * 1.005
        lows[-1] = min(last, prev) * 0.995
        return make_df(closes, vols=vols, highs=highs, lows=lows)

    @staticmethod
    def _flag(change_pct, mod=None):
        """⚠️ 标志位在 `patterns` 子字典里，不在顶层。

        第一版读 `m.get("reversal_k_candidate")` 恒得 None ⇒ 「+2.1% 应为 False」
        会**空转通过**，只有 `test_preconditions_hold_at_zero_change`
        （断言 0% 处为 True）才把它揪出来。这就是为什么必须有那条前置断言。
        """
        m = (mod or ec).compute_metrics((mod or ec) and
                                        TestReversalKBoundaryBehavior._reversal_df(change_pct), None)
        return m["patterns"]["reversal_k_candidate"]

    def test_preconditions_hold_at_zero_change(self):
        """先确认另外三个条件在 0% 处成立 —— 否则下面的边界断言全是空转。

        ⚠️ 这一条是必须的：如果 j_low 或缩量没成立，`_flag()` 会**恒为 False**，
        「+2.1% 被拒绝」照样通过，而测试什么都没验。今天已经因为这类空转
        踩过四次（桩不真 ⇒ 测试静默变 skip）。
        """
        assert self._flag(0.0) is True, \
            "0% 处反转 K 应成立；不成立说明合成数据没满足 j_low/缩量/振幅"

    @pytest.mark.parametrize("chg,expect", [
        (-2.1, False), (-2.0, True), (-1.9, True),
        (0.0, True),
        (1.9, True), (2.0, True), (2.1, False),
    ])
    def test_symmetric_bounds_inclusive(self, chg, expect):
        """±2.0 **含端点**，两侧对称。

        对称性是 owner 2026-08-06 的决定，动因是研究侧
        `factors/reversal_quality` 一直用对称 ±2%，两边不一致会让
        「reversal_quality 与 live 的反转 K 不是同一个东西」。
        """
        assert self._flag(chg) is expect, f"change_pct={chg} 应为 {expect}"

    def test_bounds_are_configurable_via_env(self, reversal_thresholds):
        """⚠️ 区间可由 `B1_REVK_CHG_PCT` 配置 —— 但只在**模块导入时**生效。

        常量是模块级 `float(os.environ.get(...))`，运行中改环境变量对已导入的模块
        无效，必须 reload。这条同时钉住「可配置」与「配置的时机」：
        改了环境变量却不 reload 就以为改了，是个真实的踩坑姿势。
        """
        from custos.core import b1_thresholds as bt

        assert bt.REVERSAL_CHANGE_MAX_PCT == 2.0, "默认必须对称 ±2%"

        mods = reversal_thresholds(B1_REVK_CHG_PCT="1.0")
        assert (mods["b1_thresholds"].REVERSAL_CHANGE_MIN_PCT,
                mods["b1_thresholds"].REVERSAL_CHANGE_MAX_PCT) == (-1.0, 1.0)
        assert mods["enrich_candidates"].REVERSAL_CHANGE_MAX_PCT == 1.0, "转出的名字要跟着变"
        # 收紧后 1.5% 应被拒（默认 ±2% 时它通过）
        assert self._flag(1.5, mod=mods["enrich_candidates"]) is False


    def test_min_max_can_be_set_independently(self, reversal_thresholds):
        """`B1_REVK_CHG_MIN` / `_MAX` 可各自覆盖 —— 留了做不对称实验的口子。

        默认对称，但研究时可能想单独放宽一侧；不测的话这两个变量形同虚设。
        """
        mods = reversal_thresholds(B1_REVK_CHG_MIN="-3.5", B1_REVK_CHG_MAX="0.5")
        for name in ("b1_thresholds", "enrich_candidates"):
            assert (mods[name].REVERSAL_CHANGE_MIN_PCT,
                    mods[name].REVERSAL_CHANGE_MAX_PCT) == (-3.5, 0.5), name

        # ⚠️ 只断言常量值**不够** —— 2026-08-07 的变异测试证明了这点：
        #    把判定从 `MIN <= x <= MAX` 改成 `abs(x) <= MAX` 之后，
        #    3595 条测试**全部通过**（默认对称时两者等价，而本条只看常量）。
        #    所以必须在**不对称**配置下断言行为：-3.0 在 [-3.5, 0.5] 内、
        #    但 abs(-3.0)=3.0 > 0.5，两种实现在这里给出相反答案。
        cir = mods["b1_thresholds"].change_in_range
        assert cir(-3.0) is True, "下界应放宽到 -3.5"
        assert cir(1.0) is False, "上界应收紧到 0.5"
        assert cir(-3.6) is False and cir(0.5) is True



class TestReversalKThresholdSingleSource:
    """⚠️ 反转 K 阈值的**唯一来源**边界：live 两链跟随配置，研究侧刻意钉死。

    2026-08-07 实测查出的分散：

        screening/enrich_candidates.py      读环境变量（唯一可配置的那份）
        market_timing/technical_monitor.py  硬编码 -2 <= change_pct <= 2、amp <= 7
        holdings/b1_holding_state.py        硬编码 j < 13
        factors/reversal_quality.py         REVK_CHG_PCT = 2.0（刻意钉死）

    后果：owner 2026-08-06 要求「对称 ±2% **且可配置**」，但设
    `B1_REVK_CHG_PCT=1.0` 只收紧了选股链，14:45/17:00 报告走的持仓链仍按 ±2 判，
    而 `technical_monitor` 的 `thresholds` 字典还会上报 `[-2.0, 2.0]` 当作
    「当前阈值」—— 配置一改它就在谎报。
    """

    def test_live_chains_share_one_source(self, reversal_thresholds):
        """选股链与持仓链读的必须是同一个模块对象。

        ⚠️ 用 `reversal_thresholds` fixture 先把整条链按依赖顺序刷新 ——
        直接比 `is` 会受**前序测试**影响：`importlib.reload(b1_thresholds)`
        造出新的函数对象，而 `enrich_candidates.change_in_range` 仍绑在旧对象上。
        2026-08-07 实测：单文件跑通过、全量跑失败，正是这个顺序污染。
        """
        mods = reversal_thresholds()
        bt = mods["b1_thresholds"]
        assert mods["technical_monitor"].change_in_range is bt.change_in_range
        assert mods["enrich_candidates"].change_in_range is bt.change_in_range
        assert mods["technical_monitor"].REVERSAL_AMPLITUDE_PCT == bt.REVERSAL_AMPLITUDE_PCT
        assert mods["b1_holding_state"].J_LOW_THRESHOLD == bt.J_LOW_THRESHOLD


    def test_env_override_reaches_both_live_chains(self, reversal_thresholds):
        """⚠️ 覆盖环境变量后**两条链一起变** —— 这是 2026-08-07 之前不成立的那条。"""
        mods = reversal_thresholds(B1_REVK_CHG_PCT="1.0")
        assert mods["b1_thresholds"].REVERSAL_CHANGE_MAX_PCT == 1.0
        assert mods["enrich_candidates"].REVERSAL_CHANGE_MAX_PCT == 1.0, "选股链没跟上"
        assert mods["technical_monitor"].change_in_range(1.5) is False, \
            "持仓链没跟上（原先就是这里漏了）"


    def test_research_factor_deliberately_pinned(self):
        """⚠️ 研究因子**不跟随**环境变量 —— 这是**有意的**，不是漏改。

        `factors/reversal_quality` 的阈值钉死才能复现既有回测数字
        （R2 P1 重跑清单依赖那些数字）。若哪天决定让它跟随，
        改的同时必须作废并重跑相关回测。
        """
        import importlib
        import os

        from custos.core.factors import reversal_quality as rq

        assert rq.REVK_CHG_PCT == 2.0, "默认值必须与 live 一致（对称 ±2%）"
        os.environ["B1_REVK_CHG_PCT"] = "3.0"
        try:
            r = importlib.reload(rq)
            assert r.REVK_CHG_PCT == 2.0, \
                "研究因子跟随了环境变量 —— 会作废已有回测数字，需先重跑 R2"
        finally:
            del os.environ["B1_REVK_CHG_PCT"]
            importlib.reload(rq)

    def test_j_vol_thresholds_also_single_source(self, reversal_thresholds, monkeypatch):
        """J 低位与极致缩量三阈值同样走唯一来源。

        2026-08-07 前 `enrich_candidates` 本地硬编码 `J_LOW_THRESHOLD=13.0 /
        VOL_RATIO_MAX=0.5 / VOL_PCTILE_MAX=10.0`：REVERSAL_* 收敛后这三个仍各写
        一份，设 `B1_J_LOW` 只改到持仓链、选股链不动 —— 与上面 chg_pct 同款分歧。
        """
        mods = reversal_thresholds()
        bt, ec = mods["b1_thresholds"], mods["enrich_candidates"]
        assert (ec.J_LOW_THRESHOLD, ec.VOL_RATIO_MAX, ec.VOL_PCTILE_MAX) == \
               (bt.J_LOW_THRESHOLD, bt.VOL_RATIO_MAX, bt.VOL_PCTILE_MAX), "默认值两边就不一致"

        # vol 两个 env 不在 fixture 的还原清单里 ⇒ 用 monkeypatch 管还原
        # （monkeypatch 先于 fixture finalizer 拆，还原 env 后 fixture 会再 reload 一次）。
        monkeypatch.setenv("B1_J_LOW", "10")
        monkeypatch.setenv("B1_REVK_VOL_RATIO", "0.4")
        monkeypatch.setenv("B1_REVK_VOL_PCTILE", "5")
        mods = reversal_thresholds()
        bt2, ec2 = mods["b1_thresholds"], mods["enrich_candidates"]
        assert ec2.J_LOW_THRESHOLD == bt2.J_LOW_THRESHOLD == 10.0, "选股链 J 阈值没跟上"
        assert ec2.VOL_RATIO_MAX == bt2.VOL_RATIO_MAX == 0.4, "选股链量比阈值没跟上"
        assert ec2.VOL_PCTILE_MAX == bt2.VOL_PCTILE_MAX == 5.0, "选股链量分位阈值没跟上"
        assert mods["b1_holding_state"].J_LOW_THRESHOLD == 10.0, "持仓链 J 阈值没跟上"
        # ⚠️ 只比常量不够：`j_below_threshold` 的默认参数在 def 时绑定 ——
        #    reload 后必须重新绑定到新阈值，否则门槛仍按 13 判。
        assert ec2.j_below_threshold(11.0) is False and ec2.j_below_threshold(9.0) is True
        # 持仓链反转K的理由文案也要随配置变 —— 硬编码「J<13」会在改配置后谎报依据。
        s = mods["b1_holding_state"].evaluate(
            {"price_volume": {"available": True, "reversal_k_candidate_without_j": True},
             "daily_j": 8.0})
        sig = [x for x in s["signals"] if x["signal"] == "reversal_k_candidate"][0]
        assert "J<10" in sig["reason"], f"理由文案没跟上配置：{sig['reason']}"

    def test_thresholds_dict_reports_effective_values(self, reversal_thresholds):
        """⚠️ `technical_monitor` 上报的 `thresholds` 必须是**实际生效值**。

        原先写死 `[-2.0, 2.0]`，环境变量一改就成假话 —— 而这个字典正是下游
        解释「为什么这支票没标反转 K」的依据。
        """
        mods = reversal_thresholds(B1_REVK_CHG_MIN="-1.5", B1_REVK_CHG_MAX="0.5")
        closes = [40.0 - i * 0.5 for i in range(60)]
        vols = [3000.0] * 56 + [2800.0, 2600.0, 2400.0, 2200.0]
        closes.append(round(closes[-1], 4))
        vols.append(300.0)
        r = mods["technical_monitor"].analyze(make_df(closes, vols=vols), "600000")
        got = r["price_volume"]["thresholds"]["reversal_close_change_pct"]
        assert got == [-1.5, 0.5], f"上报的阈值不是生效值：{got}"

    def test_research_backtest_revk_pinned_to_live_defaults(self, reversal_thresholds):
        """研究回测器 `backtest_factors` 的 REVK_*/J_LOW 钉死 = live 默认值。

        2026-08-09 登记进 `b1_thresholds` 豁免清单：判定逻辑（round-2 涨跌幅、
        prev_close 振幅分母、`<` 量分位）已与 live 对齐，只有「不读 env」是刻意的
        （复现既有回测数字，同 reversal_quality 的理由）。
        ⚠️ 单位注意：研究侧量分位是小数（0.10），live 是百分数（10.0）。
        """
        mods = reversal_thresholds()
        thr = mods["b1_thresholds"]
        from custos.research import backtest_factors as bf
        assert bf.REVK_VOL_RATIO == thr.VOL_RATIO_MAX
        assert bf.REVK_VOL_PCTILE * 100 == thr.VOL_PCTILE_MAX, "单位：研究侧小数 vs live 百分数"
        assert bf.REVK_CHG_PCT == thr.REVERSAL_CHANGE_PCT
        assert bf.REVK_AMP_PCT == thr.REVERSAL_AMPLITUDE_PCT
        assert bf.J_LOW_THRESHOLD == thr.J_LOW_THRESHOLD

    def test_release_label_factors_j_low_follow_live(self, reversal_thresholds):
        """release 标注因子（落 live 候选表的标签）的 J 阈值必须**跟随** live。

        2026-08-09：`b1_dual_factor.J_LOW_THRESHOLD` / `b2_surge_factor.B2_J_LOW`
        改从 `b1_thresholds` 导入（L2→L0 合规）—— 标注应反映 live 口径（含 env 覆盖）。
        本条钉住：reload 后两者与阈值模块当前值相等，且默认是 13.0（回测可复现性）。
        不在这里做 env 覆盖实验：那会 reload `b1_thresholds` 造出新函数对象，
        污染不做整链刷新的其它用例（本 fixture 存在的理由）。
        """
        import importlib

        from custos.core.factors import b1_dual_factor
        from custos.core.factors import b2_surge_factor
        mods = reversal_thresholds()
        thr = mods["b1_thresholds"]
        d1 = importlib.reload(b1_dual_factor)
        d2 = importlib.reload(b2_surge_factor)
        assert d1.J_LOW_THRESHOLD == thr.J_LOW_THRESHOLD == 13.0, "b1_dual_factor 没跟上 live"
        assert d2.B2_J_LOW == thr.J_LOW_THRESHOLD == 13.0, "b2_surge_factor 没跟上 live"
