# -*- coding: utf-8 -*-
"""#26 sector_inflow_study 的合成 fixture 测试。

小宇宙：4 只合成日线（A/D 下跌 → J≈0 在池内；B 上涨 → J 高不在池；C 下跌但板块
永不活跃），合成 daily_rank 文件与合成 members/名称表，钉住：
- 活跃集 ≥K 次口径（含窗口滑出剔除）
- as-of 无未来函数（t 日之后的榜文件不影响 t 日分组）
- 命中/未命中分组正确（含 exclude_types 剔地区板块口径）
- forward 收益计算与尾部日期剔除计数
- 前/后半稳定性输出结构
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from custos.core.factors import sector_mainstream as sm
from custos.research import sector_inflow_study as sis

# 20 个工作日：个股数据全程；研究窗口取最后 12 天（d8..d19），保证窗口内 J 均有定义。
ALL_DAYS = [str(d)[:10] for d in pd.bdate_range("2026-01-05", periods=20)]
TRADE_DAYS = ALL_DAYS[8:]  # d8..d19，共 12 天
HORIZONS = [2, 5]
WINDOW, MIN_HITS = 3, 2


def _df(closes: list[float]) -> pd.DataFrame:
    """合成日线：low=close、high=close+1。单调下跌 → RSV=0 → J≈0（池内）；
    单调上涨 → RSV≈89 → J 高（池外）。"""
    return pd.DataFrame(
        {
            "date": ALL_DAYS,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": closes,
            "close": closes,
            "volume": [1000] * len(closes),
        }
    )


# A：下跌，属活跃板块 880545 → 命中组
CLOSES_A = [100.0 - i for i in range(20)]
# B：上涨，属 880545 但 J 高 → 从不进池
CLOSES_B = [50.0 + i for i in range(20)]
# C：下跌，属 880546（永不活跃）→ 未命中组
CLOSES_C = [80.0 - 0.5 * i for i in range(20)]
# D：下跌，属 880999（只在窗口末段上榜）→ 前段未命中、末段命中
CLOSES_D = [200.0 - 2.0 * i for i in range(20)]

BARS = {
    "000001": _df(CLOSES_A),
    "000002": _df(CLOSES_B),
    "000003": _df(CLOSES_C),
    "000004": _df(CLOSES_D),
}

# 榜文件：880545 在 d8/d9 上榜；880999 在 d16/d17 上榜（window=3 ⇒ d17 起 880545 滑出）
RANK = {
    ALL_DAYS[8]: {"880545"},
    ALL_DAYS[9]: {"880545"},
    ALL_DAYS[16]: {"880999"},
    ALL_DAYS[17]: {"880999"},
}

MEMBERS = {
    "880545.SH": ["000001", "000002"],
    "880546.SH": ["000003"],
    "880999.SH": ["000004"],
    "880600.SH": ["000003"],  # 地区板块(tdx_type=3)——必须被 exclude_types 剔除
}
NAME_MAP = {
    "880545": {"name": "算力", "tdx_type": "2"},
    "880546": {"name": "医药", "tdx_type": "2"},
    "880999": {"name": "机器人", "tdx_type": "2"},
    "880600": {"name": "江西板块", "tdx_type": "3"},
}


def _code2secs() -> dict:
    return sm.invert_members(MEMBERS, exclude_types=True, name_map=NAME_MAP)


def _run() -> dict:
    return sis.run_study(
        BARS,
        RANK,
        _code2secs(),
        TRADE_DAYS,
        HORIZONS,
        window=WINDOW,
        min_hits=MIN_HITS,
        j_threshold=13.0,
    )


def _expected_rets(closes: list[float], day_idxs: list[int], h: int) -> list[float]:
    return [closes[i + h] / closes[i] - 1 for i in day_idxs]


class TestActiveSets:
    def test_min_hits_and_window_eviction(self):
        """≥K 才活跃；窗口滑出后旧榜日剔除。"""
        days = ALL_DAYS[8:12]
        rank = {days[0]: {"S1"}, days[1]: {"S1"}, days[2]: {"S1"}}
        got = dict(sis.iter_active_sets(days, rank, window=2, min_hits=2))
        assert got[days[0]] == set()  # 只上榜 1 次 < K
        assert got[days[1]] == {"S1"}
        assert got[days[2]] == {"S1"}
        assert got[days[3]] == {"S1"}  # 窗口=2 保留最近两个榜日
        got3 = dict(sis.iter_active_sets(days, rank, window=2, min_hits=3))
        assert all(v == set() for v in got3.values())  # 窗口内最多 2 次 < 3

    def test_asof_no_lookahead(self):
        """t 日之后的榜文件不得影响 t 日的活跃集。"""
        got = dict(sis.iter_active_sets(TRADE_DAYS, RANK, WINDOW, MIN_HITS))
        # 880999 只在 d16/d17 上榜：d15（含）之前绝不能活跃
        for t in TRADE_DAYS:
            if t <= ALL_DAYS[15]:
                assert "880999" not in got[t], f"{t} 不应含 880999"
        assert "880999" in got[ALL_DAYS[17]]
        # window=3：d17 时窗口=[d9,d16,d17]，880545 只剩 1 次 → 滑出
        assert "880545" not in got[ALL_DAYS[17]]
        assert "880545" in got[ALL_DAYS[16]]


class TestGrouping:
    def test_exclude_types_filters_region_sectors(self):
        """invert_members 口径：地区板块(tdx_type=3)被剔除，C 只剩 880546。"""
        c2s = _code2secs()
        assert c2s["000003"] == ["880546.SH"]
        assert set(c2s["000001"]) == {"880545.SH"}

    def test_per_day_pool_and_hit_counts(self):
        """分组正确：A 在 880545 活跃期命中，D 只在末段命中，B 永不进池，C 永不命中。"""
        res = _run()
        pd_ = res["per_day"]
        for t in TRADE_DAYS:
            assert pd_[t]["n_pool"] == 3  # A/C/D 在池；B 因 J 高不进池
        # d8：活跃集为空（880545 只 1 次）→ 0 命中
        assert pd_[ALL_DAYS[8]]["n_hit"] == 0
        # d9..d16：A 命中
        for t in TRADE_DAYS:
            if ALL_DAYS[9] <= t <= ALL_DAYS[16]:
                assert pd_[t]["n_hit"] == 1, t
        # d17..d19：880545 滑出、880999 活跃 → D 命中
        for t in TRADE_DAYS[-3:]:
            assert pd_[t]["n_hit"] == 1, t
        assert res["n_pool_samples"] == 3 * len(TRADE_DAYS)


class TestForwardReturns:
    def test_hit_miss_group_stats(self):
        """h=2 的命中/未命中组样本数与均值钉死（收益=t收盘→t+2收盘）。"""
        res = _run()
        blk = res["overall"]["2"]
        # 命中：A@d9..d16（i+2≤19 全可用）+ D@d17 → 9 个样本
        exp_hit = _expected_rets(CLOSES_A, list(range(9, 17)), 2) + _expected_rets(
            CLOSES_D, [17], 2
        )
        assert blk["hit"]["n"] == len(exp_hit) == 9
        assert blk["hit"]["mean"] == pytest.approx(
            sum(exp_hit) / len(exp_hit), abs=1e-4
        )
        # 未命中：A@d8,d17 + C@d8..d17 + D@d8..d16
        exp_miss = (
            _expected_rets(CLOSES_A, [8, 17], 2)
            + _expected_rets(CLOSES_C, list(range(8, 18)), 2)
            + _expected_rets(CLOSES_D, list(range(8, 17)), 2)
        )
        assert blk["miss"]["n"] == len(exp_miss) == 21
        assert blk["miss"]["mean"] == pytest.approx(
            sum(exp_miss) / len(exp_miss), abs=1e-4
        )
        assert blk["pool"]["n"] == 30
        assert blk["pool"]["n"] == blk["hit"]["n"] + blk["miss"]["n"]
        # 全跌样本 ⇒ 胜率 0、分位数有序
        assert blk["hit"]["win_rate"] == 0.0
        assert blk["hit"]["p25"] <= blk["hit"]["median"] <= blk["hit"]["p75"]
        assert blk["lift_hit_minus_miss_mean"] is not None

    def test_tail_exclusion(self):
        """尾部日期数据不足如实剔除并计数：h=2 剔 3股×2天，h=5 剔 3股×5天。"""
        res = _run()
        assert res["tail_excluded"] == {"2": 6, "5": 15}
        # h=5 的 pool 样本 = 3股 × (12-5) 天
        assert res["overall"]["5"]["pool"]["n"] == 21


class TestStabilityHalves:
    def test_halves_partition_overall(self):
        """前/后半按 t 对半切，两半样本数之和 = 总体。"""
        res = _run()
        assert set(res) >= {"overall", "first_half", "second_half"}
        for h in ("2", "5"):
            for g in ("hit", "miss", "pool"):
                total = res["overall"][h][g]["n"]
                halves = res["first_half"][h][g]["n"] + res["second_half"][h][g]["n"]
                assert halves == total, f"h={h} g={g}"
        # 每个半块都有完整的组结构
        for half in ("first_half", "second_half"):
            assert set(res[half]["2"]) >= {"hit", "miss", "pool"}


class TestLoadRankFiles:
    def test_reads_gainers_only_and_skips_bad_files(self, tmp_path: Path):
        d = tmp_path / "daily_rank"
        d.mkdir()
        (d / "2026-01-05.json").write_text(
            json.dumps(
                {
                    "date": "2026-01-05",
                    "gainers_top": [
                        {"rank": 1, "code": "880545.SH", "name": "算力"},
                        {"rank": 2, "code": "880546.SH", "name": "医药"},
                    ],
                    "losers_top": [{"rank": 1, "code": "880999.SH"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (d / "2026-01-06.json").write_text("{坏 json", encoding="utf-8")
        got = sis.load_rank_files(d)
        assert got == {"2026-01-05": {"880545", "880546"}}  # losers 不计、代码归一
        assert sis.load_rank_files(tmp_path / "不存在") == {}
