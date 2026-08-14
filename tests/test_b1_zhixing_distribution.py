# -*- coding: utf-8 -*-
"""知行量价（good_b1）正向因子 + 主力出货五方式负向因子 + score 接线测试。

表驱动：注入合成 OHLCV，不依赖 TdxW/网络。
"""

import pandas as pd

from custos.pipeline.screening import enrich_candidates as ec
from custos.pipeline.screening import score_candidates as sc


def make_df(closes, vols=None, opens=None, highs=None, lows=None):
    n = len(closes)
    closes = [float(x) for x in closes]
    opens = [float(x) for x in (opens if opens is not None else closes)]
    highs = [
        float(x)
        for x in (
            highs
            if highs is not None
            else [max(o, c) * 1.005 for o, c in zip(opens, closes)]
        )
    ]
    lows = [
        float(x)
        for x in (
            lows
            if lows is not None
            else [min(o, c) * 0.995 for o, c in zip(opens, closes)]
        )
    ]
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [float(v) for v in (vols or [1000.0] * n)],
            "amount": [0.0] * n,
        }
    )


# ---------- 知行趋势线 zhixing_state（QSX/DKS） ----------


def test_zhixing_bull_and_golden_cross():
    closes = [10.0] * 120 + [10 + i * 0.3 for i in range(1, 30)]
    r = ec.zhixing_state(make_df(closes))
    assert r["available"] and r["qsx_gt_dks"] and r["close_above_qsx"]
    assert r["days_since_golden_cross"] is not None


def test_zhixing_bear_and_unavailable():
    closes = [30.0 - i * 0.12 for i in range(150)]
    r = ec.zhixing_state(make_df(closes))
    assert r["available"] and r["qsx_gt_dks"] is False
    assert ec.zhixing_state(make_df([10.0] * 100))["available"] is False


# ---------- 放量点火 check_ignition ----------


def test_ignition_hit():
    closes = [10.0] * 30
    closes[-1] = closes[-2] * 1.04
    opens = list(closes)
    opens[-1] = closes[-2]  # 收阳
    vols = [1000.0] * 20 + [500.0] * 9 + [1300.0]  # 前段缩量后放量
    r = ec.check_ignition(make_df(closes, vols=vols, opens=opens))
    assert r["hit"] is True
    assert r["detail"]["vol_ratio5"] >= 1.5


def test_ignition_miss_on_flat():
    r = ec.check_ignition(make_df([10.0] * 30))
    assert r["available"] and r["hit"] is False


# ---------- 回调缩量企稳 check_pullback_shrink ----------


def test_pullback_shrink_hit():
    closes = (
        [10.0] * 10
        + [10 + i * 0.33 for i in range(15)]
        + [14.2, 14.0, 13.9, 13.85, 13.8]
    )
    vols = [1000.0] * 10 + [2000.0] * 15 + [600.0, 550.0, 500.0, 480.0, 470.0]
    r = ec.check_pullback_shrink(make_df(closes, vols=vols), dks_last=None)
    assert r["hit"] is True
    assert r["detail"]["drop_from_high_pct"] >= 3.0


def test_pullback_shrink_miss_when_volume_not_shrinking():
    closes = (
        [10.0] * 10
        + [10 + i * 0.33 for i in range(15)]
        + [14.2, 14.0, 13.9, 13.85, 13.8]
    )
    vols = [1000.0] * 30  # 回调不缩量
    r = ec.check_pullback_shrink(make_df(closes, vols=vols), dks_last=None)
    assert r["hit"] is False


# ---------- 出货五方式 detect_distribution ----------


def test_distribution_top_huge_vol_bear():
    # 20 平 + 10 快涨(~+35%) + 顶部天量大阴(-6%, 4x量)
    base = [10.0] * 20
    up = [10.0 * (1.031**i) for i in range(1, 11)]
    red_open = up[-1]
    red_close = up[-1] * 0.94
    closes = base + up + [red_close]
    opens = base + up + [red_open]
    vols = [1000.0] * 20 + [1500.0] * 10 + [4200.0]
    r = ec.detect_distribution(make_df(closes, vols=vols, opens=opens), code="600000")
    assert r["available"]
    assert r["signals"]["top_huge_vol_bear"]["hit"] is True
    assert r["severe"] is True and r["risk_level"] == "high"


def test_distribution_green_heavy_red_light():
    base = [13.0] * 20
    closes, opens, vols = list(base), list(base), [1000.0] * 20
    price = 13.0
    for _ in range(5):
        opens.append(price)
        closes.append(price * 0.97)
        vols.append(2200.0)
        price = price * 0.97
        opens.append(price)
        closes.append(price * 1.008)
        vols.append(700.0)
        price = price * 1.008
    r = ec.detect_distribution(make_df(closes, vols=vols, opens=opens), code="600000")
    assert r["signals"]["top_green_heavy_red_light"]["hit"] is True
    assert r["risk_level"] in ("watch", "high")


def test_distribution_none_on_healthy_uptrend():
    closes = [10.0 + i * 0.15 for i in range(60)]
    vols = [1000.0 + i * 5 for i in range(60)]
    r = ec.detect_distribution(make_df(closes, vols=vols), code="600000")
    assert r["available"] and r["hits"] == [] and r["risk_level"] == "none"


# ---------- compute_metrics 落盘新字段 ----------


def test_compute_metrics_has_new_fields():
    closes = [10.0] * 120 + [10 + i * 0.2 for i in range(1, 20)]
    m = ec.compute_metrics(make_df(closes), None, code="600000")
    for key in [
        "zhixing",
        "ignition",
        "pullback_shrink",
        "ride_above_fast",
        "b1_ignition",
        "distribution",
        "distribution_confirm",  # 2026-08-13：次日确认豁免层（25chuhuo 缺口）
    ]:
        assert key in m, f"缺字段 {key}"
    assert m["zhixing"]["available"] is True


# ---------- score_candidates 接线 ----------


def _scand(**extra):
    base = dict(
        code="600000",
        name="示例",
        sector="半导体",
        theme_id="t",
        formula_hits=[],
        patterns={
            "bbi_above": True,
            "j_low": True,
            "volume_contraction": True,
            "reversal_k_candidate": True,
            "relative_strength_strong": True,
        },
        daily_j=10.0,
        stop_loss_ref={"price": 10.0, "basis": "x"},
        is_holding=False,
        # 技术强(bbi+反转K+RS=65) + 资金意图强(b1点火+量能主线) → base 强×强 = A
        b1_ignition={"hit": True},
        volume_sustain={"status": "mainline_confirmed"},
    )
    base.update(extra)
    return base


SECTOR = {"state": "主升", "score": 80}


def test_score_positive_zhixing_and_ignition_add():
    s0 = sc.score_candidate(_scand(), SECTOR, "做多")
    s1 = sc.score_candidate(
        _scand(
            zhixing={"available": True, "qsx_gt_dks": True},
            ignition={"hit": True},
            pullback_shrink={"hit": True},
            b1_ignition={"hit": True},
        ),
        SECTOR,
        "做多",
    )
    c = s1["score_detail"]["factor_contrib"]
    assert c.get("zhixing_bull") == 6 and c.get("b1_ignition") == 8
    assert c.get("ignition") == 4 and c.get("pullback_shrink") == 3
    assert (
        s1["score_detail"]["technical_score"] >= s0["score_detail"]["technical_score"]
    )


def test_distribution_cap_high_forces_d():
    s = sc.score_candidate(
        _scand(
            distribution={
                "available": True,
                "hits": ["top_huge_vol_bear"],
                "risk_level": "high",
            }
        ),
        SECTOR,
        "做多",
    )
    assert s["bucket"] == "D" and "distribution_high" in s["risk_flags"]


def test_distribution_cap_watch_caps_c():
    s = sc.score_candidate(
        _scand(
            distribution={
                "available": True,
                "hits": ["top_green_heavy_red_light"],
                "risk_level": "watch",
            }
        ),
        SECTOR,
        "做多",
    )
    assert s["bucket"] == "C" and "distribution_watch" in s["risk_flags"]


def test_distribution_cap_disabled_keeps_a():
    s = sc.score_candidate(
        _scand(
            distribution={
                "available": True,
                "hits": ["top_huge_vol_bear"],
                "risk_level": "high",
            }
        ),
        SECTOR,
        "做多",
        cap_rules={"distribution_cap": False},
    )
    assert (
        s["bucket"] == "A" and "distribution_detected_cap_disabled" in s["risk_flags"]
    )


# ---------- 25chuhuo 覆盖度缺口（2026-08-13，owner 批准）----------


def _sig1_bars(next_close=None, next_open=None, next_low=None):
    """① 顶部天量大阴的构造（沿用 test_distribution_top_huge_vol_bear），
    可选追加一根「次日」K（T+1 判定的对象）。

    返回 (df,) —— 信号 K 的开/收/低由调用方按常量自算：
    red_open = 10×1.031**10，red_close = red_open×0.94，sig_low = min×0.995。
    """
    base = [10.0] * 20
    up = [10.0 * (1.031**i) for i in range(1, 11)]
    red_open = up[-1]
    red_close = up[-1] * 0.94
    closes = base + up + [red_close]
    opens = base + up + [red_open]
    vols = [1000.0] * 20 + [1500.0] * 10 + [4200.0]
    if next_close is not None:
        closes.append(next_close)
        opens.append(next_open if next_open is not None else next_close)
        vols.append(1000.0)
    highs = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    if next_low is not None:
        lows[-1] = next_low
    return make_df(closes, vols=vols, opens=opens, highs=highs, lows=lows)


_RED_OPEN = 10.0 * (1.031**10)
_RED_CLOSE = _RED_OPEN * 0.94
_SIG_LOW = min(_RED_OPEN, _RED_CLOSE) * 0.995


class TestNextDayConfirmExemption:
    """①/② 命中后的 T+1 豁免状态机（pending/confirmed/revoked）。

    讲义依据：中国中铁 14.12.22（无加速段放量=试盘非顶）、15.6.8（天量长阴后
    次日反包涨停=换庄）；01_swing_rules §七.2 的 T+1 收盘后判定条款。
    """

    def test_signal_on_last_bar_is_pending(self):
        df = _sig1_bars()
        r = ec.confirm_distribution(df, code="600000")
        assert r["available"]
        assert r["confirmations"]["top_huge_vol_bear"] == "pending", "次日未收盘"

    def test_next_day_recovers_is_revoked(self):
        """次日反包（收复信号 K 实体上沿）⇒ 豁免（换庄/假出货）。

        ⚠️ 反包根不能涨过 +10%：`_infer_price_limit` 会从实际行情自纠涨跌幅制度，
        一根 +12% 的 K 会把该票判成 20% 板 ⇒ ①的大阴阈值变 10%、信号 K 不再命中。
        """
        df = _sig1_bars(next_close=13.6, next_open=_RED_CLOSE)  # +6.6% 收复实体上沿
        r = ec.confirm_distribution(df, code="600000")
        assert r["confirmations"]["top_huge_vol_bear"] == "revoked"
        assert r["revoked"] == ["top_huge_vol_bear"]

    def test_next_day_breaks_low_is_confirmed(self):
        """次日破信号 K 低点且不收复 ⇒ 确认派发。"""
        df = _sig1_bars(
            next_close=_RED_CLOSE * 0.96,  # 继续大跌
            next_open=_RED_CLOSE * 0.97,
            next_low=_RED_CLOSE * 0.95,  # 破信号 K 低点
        )
        r = ec.confirm_distribution(df, code="600000")
        assert r["confirmations"]["top_huge_vol_bear"] == "confirmed"
        assert r["revoked"] == []

    def test_detect_semantics_untouched(self):
        """豁免层不改 detect_distribution 的命中语义（hits/risk_level 照旧）。"""
        df = _sig1_bars(next_close=13.6, next_open=_RED_CLOSE)  # 见上：不超过 +10%
        r = ec.detect_distribution(df, code="600000")
        assert r["signals"]["top_huge_vol_bear"]["hit"] is True  # 豁免不影响命中
        assert r["severe"] is True and r["risk_level"] == "high"


class TestStairstepCompleted:
    """③ 补全：平量阴也计数 + DKS（黄线）跌破也判。"""

    def test_flat_volume_bears_count(self):
        """连续阴里的**平量阴**（量不增、量比<1）不再中断计数（讲师：平量阴线也算）。"""
        # 长期走平（趋势线≈12）后 3 根平量/缩量阴 + 1 根放量跌破
        closes = [12.0] * 120 + [11.95, 11.9, 11.85, 11.7]
        opens = [12.0] * 120 + [12.0, 11.95, 11.9, 11.85]
        vols = [1000.0] * 120 + [1000.0, 900.0, 850.0, 1500.0]
        r = ec.detect_distribution(
            make_df(closes, vols=vols, opens=opens), code="600000"
        )
        d = r["signals"]["stairstep_vol_decline"]
        assert d["hit"] is True, f"平量阴应计数：{d}"
        # 第 3 根是明显缩量阴（850 < 900 且量比<1）——旧口径在此中断计数且只认
        # QSX 放量跌破 ⇒ 整例不命中；新口径（平量阴也算 + DKS 跌破也判）命中，
        # 命中点在 DKS 跌破发生的 t=122（量足跌破在 123，循环取首个命中）
        assert d["detail"]["consecutive_bears"] >= 3
        assert d["detail"]["of_which_vol_up"] < d["detail"]["consecutive_bears"]

    def test_dks_break_counts(self):
        """跌破黄线（DKS）也判——平量跌破（量比<1.2）走 DKS 路径。"""
        # 长期走平（DKS≈12）后连续小阴缓跌，量比 1.0 <1.2（QSX 放量路径不命中），
        # DKS 跌破路径（平量也判）应命中
        closes = [12.0] * 120 + [11.9, 11.8, 11.7, 11.5]
        opens = [12.0] * 120 + [11.95, 11.9, 11.8, 11.7]
        vols = [1000.0] * 124
        r = ec.detect_distribution(
            make_df(closes, vols=vols, opens=opens), code="600000"
        )
        d = r["signals"]["stairstep_vol_decline"]
        assert d["hit"] is True, f"DKS 平量跌破应命中：{d}"
        assert d["detail"]["below"] == "dks"


class TestTopWindmill:
    """顶部大风车：高位 + 长上影/宽幅震荡 K + 次日不反包确认。"""

    def _windmill_df(self, next_close=None):
        # 60 根上行到 16，最后一根大风车 K：开 16.3 收 16.2（实体 0.1）高 16.8（上影 0.6）
        closes = [10.0 + i * 0.1 for i in range(60)]
        opens = list(closes)
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        closes.append(16.2)
        opens.append(16.3)
        highs.append(16.8)
        lows.append(16.1)
        if next_close is not None:
            closes.append(next_close)
            opens.append(next_close)
            highs.append(next_close * 1.01)
            lows.append(next_close * 0.99)
        return make_df(closes, opens=opens, highs=highs, lows=lows)

    def test_last_bar_windmill_is_pending(self):
        r = ec.detect_top_windmill(self._windmill_df(), code="600000")
        assert r["hit"] is True and r["status"] == "pending"

    def test_next_day_no_recovery_confirms(self):
        r = ec.detect_top_windmill(self._windmill_df(next_close=15.8), code="600000")
        assert r["hit"] is True and r["status"] == "confirmed"

    def test_next_day_recovery_revokes(self):
        r = ec.detect_top_windmill(self._windmill_df(next_close=16.5), code="600000")
        assert r["hit"] is True and r["status"] == "revoked"

    def test_not_near_top_no_hit(self):
        # 同样的大风车 K 但出现在下跌途中（非高位）⇒ 不命中
        closes = [20.0 - i * 0.1 for i in range(60)]
        opens = list(closes)
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        closes.append(14.2)
        opens.append(14.3)
        highs.append(14.8)
        lows.append(14.1)
        r = ec.detect_top_windmill(
            make_df(closes, opens=opens, highs=highs, lows=lows), code="600000"
        )
        assert r["hit"] is False

    def test_confirm_aggregates_windmill(self):
        r = ec.confirm_distribution(self._windmill_df(), code="600000")
        assert r["available"] and r["top_windmill"]["hit"] is True
        assert r["top_windmill"]["status"] == "pending"
