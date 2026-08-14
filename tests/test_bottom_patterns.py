# -*- coding: utf-8 -*-
"""`bottom_patterns`（v0.56，25chuhuo 讲义底部侧）——W 底与红肥绿瘦。

W 底的合成构造要点（实测调定，改动序列形态时先读这段）：
- 分型要**右确认**（左右各 2 根）⇒ 第二底之后至少还要有 2 根 K；
- MACD 底背离要「第二底收盘**更低**、DIF 抬高」⇒ 底2 收盘 < 底1 收盘
  （双底腿的容差按 low 比，背离腿按 close 比，两套判据各司其职）；
- `_infer_price_limit` 会从实际行情自纠涨跌幅制度——构造里任何一根涨跌
  超 10% 都会把票判成 20% 板，改变其他检测器的阈值（教训来自 v0.54）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from custos.core.factors import bottom_patterns as bp  # noqa: E402


def mk(closes, opens=None, highs=None, lows=None, vols=None):
    n = len(closes)
    opens = opens or list(closes)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": [float(x) for x in opens],
            "high": [
                float(x)
                for x in (highs or [max(o, c) * 1.005 for o, c in zip(opens, closes)])
            ],
            "low": [
                float(x)
                for x in (lows or [min(o, c) * 0.995 for o, c in zip(opens, closes)])
            ],
            "close": [float(x) for x in closes],
            "volume": [float(v) for v in (vols or [1000.0] * n)],
            "amount": [0.0] * n,
        }
    )


def _w_bottom_df():
    """四腿齐的 W 底（实测调定）：长跌 70 根（回撤 ~57%）→ 底1(10.0) → 弹 →
    底2(9.97，收盘更低但 DIF 抬高) → 放量弹（末根 3.1×量）。"""
    closes = [24 - i * 0.2 for i in range(70)]
    vols = [800.0] * 70
    closes += [10.0, 10.4, 10.7, 10.3, 10.02]
    vols += [900] * 5
    closes += [10.5, 10.4]
    vols += [900, 900]
    closes += [9.97, 10.2, 10.45]
    vols += [900, 1200, 2600]
    return mk(closes, vols=vols)


class TestWBottom:
    def test_all_four_legs_hit(self):
        r = bp.detect_w_bottom(_w_bottom_df(), "600000")
        assert r["available"] and r["hit"] is True
        for leg in (
            "double_bottom",
            "bottom_zone",
            "bottom_volume",
            "macd_bottom_divergence",
        ):
            assert r["legs"][leg]["hit"] is True, f"{leg} 未中：{r['legs'][leg]}"

    def test_missing_volume_leg_no_hit(self):
        df = _w_bottom_df()
        df.loc[df.index[-1], "volume"] = 800.0  # 末根不放量
        r = bp.detect_w_bottom(df, "600000")
        assert r["hit"] is False and r["legs"]["bottom_volume"]["hit"] is False
        assert r["legs"]["double_bottom"]["hit"] is True, "其余腿不受影响"

    def test_missing_divergence_leg_no_hit(self):
        """第二底收盘不更低（10.02>10.0）⇒ 背离腿不成立 ⇒ 不命中。"""
        closes = [24 - i * 0.2 for i in range(70)]
        vols = [800.0] * 70
        closes += [10.0, 10.4, 10.7, 10.3, 10.02]
        vols += [900] * 5
        closes += [10.5, 10.4]
        vols += [900, 900]
        closes += [10.02, 10.2, 10.45]  # 底2 收盘不低于底1
        vols += [900, 1200, 2600]
        r = bp.detect_w_bottom(mk(closes, vols=vols), "600000")
        assert r["hit"] is False
        assert r["legs"]["macd_bottom_divergence"]["hit"] is False

    def test_not_bottom_zone_no_hit(self):
        """高位 W 形（无深回撤）⇒ 底部区域腿不成立。"""
        closes = [10.0] * 70 + [
            10.0,
            10.4,
            10.7,
            10.3,
            10.02,
            10.5,
            10.4,
            9.97,
            10.2,
            10.45,
        ]
        vols = [800.0] * 70 + [900] * 7 + [900, 1200, 2600]
        r = bp.detect_w_bottom(mk(closes, vols=vols), "600000")
        assert r["hit"] is False and r["legs"]["bottom_zone"]["hit"] is False

    def test_never_raises(self):
        assert bp.detect_w_bottom(mk([10.0] * 10), "600000")["available"] is False
        assert bp.detect_w_bottom(mk([10.0] * 65), "600000")["hit"] is False


class TestRedFatGreenThin:
    def _bottom_df(self, seg):
        """60 根阴跌到低位 + 窗口段 seg（(open, close, vol) 三元组）。"""
        closes = [20 - i * 0.15 for i in range(50)]  # 跌到 12.65
        opens = list(closes)
        vols = [800.0] * 50
        for o, c, v in seg:
            opens.append(o)
            closes.append(c)
            vols.append(v)
        return mk(closes, opens=opens, vols=vols)

    def test_both_dimensions_hit(self):
        # 底部区间：6 阳（实体大、量大）4 阴（实体小、量小）
        seg = []
        for i in range(4):
            seg.append((12.0, 12.6, 1500.0))  # 大阳
            seg.append((12.6, 12.3, 700.0))  # 小阴
        seg.append((12.0, 12.6, 1500.0))
        seg.append((12.0, 12.6, 1500.0))
        r = bp.detect_red_fat_green_thin(self._bottom_df(seg), "600000")
        assert r["hit"] is True
        d = r["detail"]
        assert d["count_hit"] and d["area_hit"]
        assert d["bull_count"] > d["bear_count"]

    def test_count_tie_no_hit(self):
        """数量维度：阴阳各半 ⇒ 不中（阳多阴少是必要条件）。"""
        seg = []
        for i in range(5):
            seg.append((12.0, 12.6, 1500.0))
            seg.append((12.6, 11.9, 600.0))  # 大阴但量小——面积维阳胜
        # 阳 5 阴 5 → count_hit False
        r = bp.detect_red_fat_green_thin(self._bottom_df(seg), "600000")
        d = r["detail"]
        assert d["count_hit"] is False and r["hit"] is False

    def test_area_fails_no_hit(self):
        """面积维度：阳多但实体/量都不如阴 ⇒ 不中。"""
        seg = []
        for i in range(6):
            seg.append((12.0, 12.2, 700.0))  # 小阳小量
        for i in range(3):
            seg.append((12.4, 11.5, 2000.0))  # 大阴大量
        r = bp.detect_red_fat_green_thin(self._bottom_df(seg), "600000")
        d = r["detail"]
        assert d["count_hit"] is True and d["area_hit"] is False and r["hit"] is False

    def test_mirror_symmetry_with_top_version(self):
        """镜像对称性：底部红肥绿瘦与顶部绿肥红瘦在同一序列上应互斥。"""
        from custos.core.factors.distribution import detect_distribution

        seg = []
        for i in range(4):
            seg.append((12.0, 12.6, 1500.0))
            seg.append((12.6, 12.3, 700.0))
        seg.append((12.0, 12.6, 1500.0))
        seg.append((12.0, 12.6, 1500.0))
        df = self._bottom_df(seg)
        assert bp.detect_red_fat_green_thin(df, "600000")["hit"] is True
        top = detect_distribution(df, "600000")
        assert top["signals"]["top_green_heavy_red_light"]["hit"] is False

    def test_never_raises(self):
        assert (
            bp.detect_red_fat_green_thin(mk([10.0] * 10), "600000")["available"]
            is False
        )


class TestRegistryAndWiring:
    def test_factor_registered_evidence_only(self):
        from custos.core import factors

        e = factors.registry()["bottom_patterns"]
        assert e["meta"]["live_use"] == "evidence_only"
        assert e["meta"]["stage"] == "release"

    def test_enrich_passthrough_to_score(self):
        """证据列必须穿透 enrich → score_candidates 白名单（不加就丢——
        2026-08-04 signals 的教训）。"""
        from custos.pipeline.screening import score_candidates as sc

        cand = {
            "code": "600000",
            "name": "甲",
            "patterns": {},
            "w_bottom": {"available": True, "hit": True},
            "red_fat_green_thin": {"available": True, "hit": False},
        }
        scored = sc.score_candidate(cand, None, "做多")
        assert scored["w_bottom"]["hit"] is True
        assert scored["red_fat_green_thin"]["hit"] is False

    def test_never_changes_bucket_or_score(self):
        """严格证据层：两形态置真/置假的分层与总分逐位相同。"""
        from custos.pipeline.screening import score_candidates as sc

        base = {"code": "600000", "name": "甲", "patterns": {"bbi_above": True}}
        a = sc.score_candidate(
            {
                **base,
                "w_bottom": {"hit": True},
                "red_fat_green_thin": {"hit": True},
            },
            None,
            "做多",
        )
        b = sc.score_candidate({**base}, None, "做多")
        assert a["bucket"] == b["bucket"] and a["score"] == b["score"]
