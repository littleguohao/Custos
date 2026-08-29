"""`theme_tracker_report` —— `daily_pipeline` 里**最大的零覆盖硬失败 stage**。

覆盖率清点（2026-08-07）：225 语句、**0% 覆盖**、`required=True`（挂了整条 17:00 链失败）。

这个模块决定「**板块支不支持加仓**」：
`classify_stage` 判板块阶段（退潮/下跌 ⇒ 不支持）、`score_sector` 打 0–100 分、
`action_bias` 落到「回避/禁止加仓 … 可关注核心股」。
而 R4 的结论是**板块相位择时是 0AMV 之后第二个 OOS 站得住的增强**（熊市减亏 ~4–6pp）
⇒ 这套判定错了，那条 edge 就无从兑现。

测的重点是**判据边界**（阈值恰好命中/差一点），不是冒烟。
"""

from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.pipeline.market_timing import theme_tracker_report as ttr  # noqa: E402
import sys


def _an(
    trend=None,
    pos20=None,
    j=None,
    j_prev=None,
    macd_dir=None,
    weekly_hist=None,
    available=True,
    **kw,
):
    """造一份 `technical_monitor.analyze` 形状的分析结果。"""
    return {
        "available": available,
        "trend": {"state": trend, "close": 10.0},
        "box_20d": {"position": pos20, "upper": 11.0, "lower": 9.0},
        "box_60d": {},
        "daily": {
            "kdj": {"j": j, "j_prev": j_prev, "state": ""},
            "macd": {"hist": 0.1, "hist_direction": macd_dir},
        },
        "weekly": {"macd": {"hist": weekly_hist}},
        **kw,
    }


class TestClassifyStage:
    """板块阶段判定 —— 决定「板块支不支持加仓」。"""

    def test_unavailable_is_data_shortage_not_a_verdict(self):
        """⚠️ 数据不足**不等于**任何一种阶段判断，必须单独一档。

        若把它归到「震荡」，一个取不到 K 线的板块会被当成正常震荡放行。
        """
        stage, why = ttr.classify_stage({"available": False, "error": "无K线"})
        assert stage == "数据不足" and why == "无K线"

    def test_main_rally_needs_all_three(self):
        """主升/加速要求**三条同时**：上涨 + 箱体上沿/突破 + MACD 扩张。"""
        assert (
            ttr.classify_stage(_an(trend="上涨", pos20="上沿/突破区", macd_dir="扩张"))[
                0
            ]
            == "主升/加速"
        )

    @pytest.mark.parametrize(
        "pos,macd", [("箱体上半区", "扩张"), ("上沿/突破区", "收缩")]
    )
    def test_uptrend_without_all_three_is_only_repair(self, pos, macd):
        """缺任一条就只能是「修复/上行」—— 不许升级成主升。"""
        assert (
            ttr.classify_stage(_an(trend="上涨", pos20=pos, macd_dir=macd))[0]
            == "修复/上行"
        )

    def test_downtrend_says_no_add(self):
        stage, why = ttr.classify_stage(_an(trend="下跌"))
        assert stage == "退潮/下跌" and "不支持加仓" in why

    def test_range_at_lower_bound_is_divergence(self):
        assert (
            ttr.classify_stage(_an(trend="横盘震荡", pos20="下沿/破位区"))[0]
            == "分歧/弱震荡"
        )

    def test_high_j_only_when_trend_undecided(self):
        """J>90 的「高位分歧」只在趋势未定时生效 —— 趋势判定优先于指标。"""
        assert ttr.classify_stage(_an(trend=None, j=95.0))[0] == "高位分歧观察"
        assert ttr.classify_stage(_an(trend="上涨", j=95.0))[0] == "修复/上行"

    def test_weak_weekly_falls_back_to_range(self):
        assert ttr.classify_stage(_an(trend=None, weekly_hist=-0.2))[0] == "震荡"

    def test_default_is_range(self):
        assert ttr.classify_stage(_an())[0] == "震荡"


class TestScoreSector:
    def test_unavailable_scores_zero(self):
        assert ttr.score_sector({"available": False}, "high") == 0.0

    def test_base_is_fifty(self):
        assert ttr.score_sector(_an(), "") == 50.0

    def test_uptrend_and_breakout_and_expansion_stack(self):
        """加分项可叠加：50 + 18(上涨) + 12(上沿) + 8(扩张) = 88。"""
        assert (
            ttr.score_sector(
                _an(trend="上涨", pos20="上沿/突破区", macd_dir="扩张"), ""
            )
            == 88.0
        )

    def test_downtrend_and_breakdown_subtract(self):
        assert ttr.score_sector(_an(trend="下跌", pos20="下沿/破位区"), "") == 18.0

    def test_clamped_to_0_100(self):
        """必须夹在 [0,100] —— 越界分数会让排序与「强板块」阈值失去意义。"""
        s = ttr.score_sector(
            _an(
                trend="上涨",
                pos20="上沿/突破区",
                macd_dir="扩张",
                j=85.0,
                weekly_hist=0.5,
            ),
            "high",
        )
        assert 0 <= s <= 100

    def test_low_j_turning_up_gets_bonus(self):
        """J<30 且在抬头 → +5（低位转强）；单纯 J<12 → −3（可能是接飞刀）。"""
        up = ttr.score_sector(_an(j=20.0, j_prev=15.0), "")
        deep = ttr.score_sector(_an(j=8.0, j_prev=5.0), "")
        assert up == 55.0 and deep == 47.0

    def test_high_priority_bonus(self):
        assert ttr.score_sector(_an(), "high") - ttr.score_sector(_an(), "low") == 3.0


class TestActionBias:
    """动作倾向 —— 这是**风控闸**，措辞与阈值都要准。"""

    def test_ebb_always_forbids_add(self):
        """退潮**无论分数多高**都禁止加仓 —— 阶段判定优先于分数。"""
        assert ttr.action_bias("退潮/下跌", 95.0, "进攻") == "回避/禁止加仓"

    def test_low_score_forbids_add(self):
        assert ttr.action_bias("震荡", 34.9) == "回避/禁止加仓"

    def test_core_stock_needs_stage_score_and_market(self):
        """「可关注核心股」要求**三条同时**：主升 + ≥70 分 + 大盘进攻/偏强。"""
        assert ttr.action_bias("主升/加速", 70.0, "进攻") == "可关注核心股"
        assert ttr.action_bias("主升/加速", 69.9, "进攻") == "观察核心低吸，不追高"
        assert ttr.action_bias("主升/加速", 70.0, "震荡偏弱") == "观察核心低吸，不追高"

    def test_tiers(self):
        assert ttr.action_bias("震荡", 65.0) == "观察核心低吸，不追高"
        assert ttr.action_bias("震荡", 50.0) == "观察"
        assert ttr.action_bias("震荡", 49.9) == "谨慎观察"


class TestCompareHoldingToTheme:
    def test_theme_unavailable_is_undecided(self):
        """板块不可用时必须「未定」，不能猜强弱。"""
        s, why = ttr.compare_holding_to_theme(
            {"trend_state": "上涨"}, {"available": False}
        )
        assert s == "未定" and "无板块映射" in why

    def test_fit_insufficient_is_undecided_with_honest_reason(self):
        """贴合数据不足 ⇒「未定」+ 如实报原因（与无映射文案分开），不猜板块。"""
        s, why = ttr.compare_holding_to_theme(
            {"trend_state": "上涨"}, {"fit_insufficient": True}
        )
        assert s == "未定" and "贴合数据不足" in why

    def test_stronger_trend(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "上涨"}, {"available": True, "trend_state": "横盘震荡"}
        )
        assert s == "强于板块"

    def test_weaker_trend(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "下跌"}, {"available": True, "trend_state": "上涨"}
        )
        assert s == "弱于板块"

    def test_same_trend_but_worse_position_is_weaker(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "横盘震荡", "box20_position": "下沿/破位区"},
            {
                "available": True,
                "trend_state": "横盘震荡",
                "box20_position": "箱体上半区",
            },
        )
        assert s == "弱于板块"

    def test_same_trend_better_position_is_stronger(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "横盘震荡", "box20_position": "上沿/突破区"},
            {
                "available": True,
                "trend_state": "横盘震荡",
                "box20_position": "下沿/破位区",
            },
        )
        assert s == "强于板块"

    def test_synced(self):
        s, _ = ttr.compare_holding_to_theme(
            {"trend_state": "上涨", "box20_position": "箱体上半区"},
            {"available": True, "trend_state": "上涨", "box20_position": "箱体上半区"},
        )
        assert s == "同步"


def _sector(code, name, category, count, stocks):
    """合成 tq_sector_map 的板块条目。"""
    return {
        "code": code,
        "name": name,
        "category": category,
        "stock_count": count,
        "stocks": stocks,
    }


def _kline(closes, start="2026-01-01"):
    """合成 K 线（date/close 两列，贴合计算只用这两列）。"""
    import pandas as pd

    return pd.DataFrame(
        {"date": pd.date_range(start, periods=len(closes), freq="B"), "close": closes}
    )


def _stock_and(stock_code, corr_map):
    """个股收益 = base 噪声；corr_map {板块码: 混合比}（1.0=同源，0.0=独立噪声）。"""
    import numpy as np

    rng = np.random.default_rng(7)
    n = rng.normal(0, 1, 80)
    out = {stock_code: _kline(100 + np.cumsum(n))}
    for code, ratio in corr_map.items():
        mix = ratio * n + (1 - ratio) * rng.normal(0, 1, 80)
        out[code] = _kline(500 + np.cumsum(mix))
    return out


class TestSectorFitResolution:
    """v0.148：自动链只有贴合一档（分层兜底链已删）。

    贴合池 = 全部所属板块（概念/细分反查命中 + 行业名匹配的 880 行业板块）；
    贴合无有效数据 ⇒ 未定（「贴合数据不足」，与「无映射」分开），不再猜。
    """

    SECTOR_MAP = {
        "sectors": [
            _sector("880520.SH", "智能电网", "concept", 277, ["600312.SH"]),
            _sector("880964.SH", "特高压", "concept", 132, ["600312.SH"]),
            _sector("880213.SH", "河南板块", "region", 116, ["600312.SH"]),
            _sector("880861.SH", "连续亏损", "style", 1075, ["600312.SH"]),
            _sector("881467.SH", "燃气", "sub_industry", 232, ["605090.SH"]),
            _sector("880705.SH", "天然气", "concept", 100, ["605090.SH"]),
        ]
    }
    NAME_MAP = {"880446": {"name": "电气设备", "tdx_type": "2"}}

    @pytest.fixture(autouse=True)
    def env(self, monkeypatch):
        import custos.datasource.local_tdx.tq_sector as tq_sector

        monkeypatch.setattr(tq_sector, "load_sector_names", lambda: dict(self.NAME_MAP))

    def _klines(self, monkeypatch, series_by_code):
        monkeypatch.setattr(
            ttr.tm, "read_vipdoc", lambda code: series_by_code.get(code)
        )

    def test_fit_is_the_only_criterion_beats_industry(self, monkeypatch):
        """贴合是唯一判据：行业命中（电气设备）但贴合输给概念 ⇒ 概念胜。"""
        self._klines(
            monkeypatch,
            _stock_and(
                "600312.SH", {"880520.SH": 0.95, "880964.SH": 0.3, "880446.SH": 0.3}
            ),
        )
        sector, status = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, "电气设备"
        )
        assert status == "fit" and sector["code"] == "880520.SH"
        assert sector["fit"] > 0.9

    def test_industry_candidate_can_win_fit(self, monkeypatch):
        """行业候选在池里平等竞争：电气设备贴合最高 ⇒ 它胜（source=fit，非行业层）。"""
        self._klines(
            monkeypatch,
            _stock_and(
                "600312.SH", {"880446.SH": 0.95, "880520.SH": 0.3, "880964.SH": 0.2}
            ),
        )
        sector, status = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, "电气设备"
        )
        assert status == "fit" and sector["code"] == "880446.SH"

    def test_region_style_never_in_fit_pool(self, monkeypatch):
        """区域/风格仍不入池：河南板块（region）即便贴合同源也不参选。"""
        self._klines(
            monkeypatch,
            _stock_and(
                "600312.SH", {"880213.SH": 0.99, "880520.SH": 0.5, "880964.SH": 0.2}
            ),
        )
        sector, status = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, None
        )
        assert status == "fit" and sector["code"] == "880520.SH"

    def test_sub_industry_can_win_fit(self, monkeypatch):
        """细分行业平等参选（owner 实测：九丰能源 燃气0.485 > 供气供热0.423）。"""
        self._klines(
            monkeypatch,
            _stock_and("605090.SH", {"881467.SH": 0.9, "880705.SH": 0.2}),
        )
        sector, status = ttr.resolve_holding_sector(
            {"code": "605090", "name": "九丰能源"}, self.SECTOR_MAP, None
        )
        assert status == "fit" and sector["code"] == "881467.SH"

    def test_fit_insufficient_is_undecided_not_guessed(self, monkeypatch):
        """贴合无有效数据（个股 <20 根）⇒ fit_insufficient，**不再回落猜板块**。"""
        self._klines(
            monkeypatch,
            {
                "600312.SH": _kline(range(10)),
                "880520.SH": _kline(range(50)),
                "880964.SH": _kline(range(50)),
                "880446.SH": _kline(range(50)),
            },
        )
        sector, status = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, "电气设备"
        )
        assert sector is None and status == "fit_insufficient"

    def test_stock_without_kline_is_fit_insufficient(self, monkeypatch):
        """个股无 K 线 ⇒ fit_insufficient（不再回落共词/取大/行业）。"""
        self._klines(monkeypatch, {"600312.SH": None})
        sector, status = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, "电气设备"
        )
        assert sector is None and status == "fit_insufficient"

    def test_no_pool_is_no_mapping(self):
        """不在任何板块（行业名也对不上）⇒ no_mapping，与贴合不足分开。"""
        sector, status = ttr.resolve_holding_sector(
            {"code": "999999", "name": "x"}, self.SECTOR_MAP, "不存在的行业"
        )
        assert sector is None and status == "no_mapping"

    def test_sector_series_cached_per_report(self, monkeypatch):
        """同一报告内板块收益序列只读一次：两只持仓共享候选板块时，
        板块文件读取次数 = 板块数（不是 持仓数×板块数）。"""
        reads = []
        klines = _stock_and(
            "600312.SH", {"880520.SH": 0.9, "880964.SH": 0.3, "880446.SH": 0.2}
        )
        klines["605090.SH"] = _kline(range(200, 280))
        klines["881467.SH"] = _kline(range(300, 380))
        klines["880705.SH"] = _kline(range(400, 480))

        def counting_read(code):
            reads.append(code)
            return klines.get(code)

        monkeypatch.setattr(ttr.tm, "read_vipdoc", counting_read)
        cache: dict = {}
        ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"},
            self.SECTOR_MAP,
            "电气设备",
            cache,
        )
        ttr.resolve_holding_sector(
            {"code": "605090", "name": "九丰能源"}, self.SECTOR_MAP, None, cache
        )
        sector_reads = [c for c in reads if c.startswith(("880", "881"))]
        assert len(sector_reads) == len(set(sector_reads)), (
            f"板块被重复读：{sector_reads}"
        )

    def test_fit_goes_into_report_render(self, monkeypatch):
        """§4 渲染三分标注之贴合：「智能电网（贴合0.9x）」带系数。"""
        self._klines(
            monkeypatch,
            _stock_and("600312.SH", {"880520.SH": 0.95, "880964.SH": 0.3}),
        )
        monkeypatch.setattr(
            ttr.tm, "analyze", lambda df, code: _an(trend="上涨", pos20="箱体上半区")
        )
        sector, status = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, None
        )
        row = ttr._sector_analysis_row(sector, status, ["600312"])
        text = ttr._section_holdings(
            [{"code": "600312", "name": "平高电气", "trend_state": "上涨"}],
            {"600312": row},
        )
        line = next(x for x in text if x.startswith("| 600312"))
        assert "智能电网（贴合" in line

    def test_no_fallback_chain_remnants(self):
        """grep 守卫：分层兜底已整段删除——源码不再有旧兜底入口。"""
        src = pathlib.Path(ttr.__file__).read_text(encoding="utf-8")
        for gone in (
            "pick_holding_sector",
            "_pick_concept",
            "_shared_token",
            "_largest",
        ):
            assert gone not in src, f"{gone} 应已删除"
        assert "SECTOR_MAP" not in src
        assert 'SECTORS_DIR / "sector_code_map.json"' not in src


class TestIndustryCandidate:
    """pick_industry_sector（贴合池的行业候选来源）：名称匹配与歧义规则不变。"""

    NAME_MAP = {
        "880446": {"name": "电气设备", "tdx_type": "2"},
        "880447": {"name": "电气设备", "tdx_type": "2"},  # 同名歧义候选
        "880960": {"name": "电气设备", "tdx_type": "4"},  # 同名但非行业，不得命中
    }
    SECTOR_MAP = {
        "sectors": [
            _sector("880446.SH", "电气设备", "industry", 300, []),
            _sector("880447.SH", "电气设备", "industry", 100, []),
        ]
    }

    @pytest.fixture(autouse=True)
    def env(self, monkeypatch):
        import custos.datasource.local_tdx.tq_sector as tq_sector

        monkeypatch.setattr(tq_sector, "load_sector_names", lambda: dict(self.NAME_MAP))
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: _kline(range(50)))

    def test_name_must_match_type2_exactly(self):
        assert ttr.pick_industry_sector("电气设备", self.SECTOR_MAP)["code"] == (
            "880446.SH"
        )
        assert ttr.pick_industry_sector("不存在的行业", self.SECTOR_MAP) is None

    def test_ambiguous_name_picks_largest_with_kline(self, monkeypatch):
        """同名歧义：取有 K 线且成分股最多者（880446 300 > 880447 100）。"""
        s = ttr.pick_industry_sector("电气设备", self.SECTOR_MAP)
        assert s["code"] == "880446.SH"
        # 880446 无 K 线时落 880447
        monkeypatch.setattr(
            ttr.tm,
            "read_vipdoc",
            lambda code: (
                pd.DataFrame() if code.startswith("880446") else _kline(range(50))
            ),
        )
        s = ttr.pick_industry_sector("电气设备", self.SECTOR_MAP)
        assert s["code"] == "880447.SH"

    def test_no_kline_at_all_returns_none(self, monkeypatch):
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: pd.DataFrame())
        assert ttr.pick_industry_sector("电气设备", self.SECTOR_MAP) is None

    def test_mapping_file_backtracks_to_latest_before_date(self, tmp_path, monkeypatch):
        """mapping 取 ≤ 报告日的最近一份（同 positions_history 回溯语义）。"""
        monkeypatch.setattr(ttr, "HOLDINGS_DIR", tmp_path)
        (tmp_path / "2026-08-26_holding_sector_mapping.json").write_text(
            json.dumps([{"code": "600312", "industry": "电气设备"}]), encoding="utf-8"
        )
        (tmp_path / "2026-08-28_holding_sector_mapping.json").write_text(
            json.dumps([{"code": "600312", "industry": "半导体"}]), encoding="utf-8"
        )
        assert ttr.holding_industry_names("2026-08-27") == {"600312": "电气设备"}
        assert ttr.holding_industry_names("2026-08-28") == {"600312": "半导体"}
        monkeypatch.setattr(ttr, "HOLDINGS_DIR", tmp_path / "empty")
        assert ttr.holding_industry_names("2026-08-28") == {}


class TestBuildAndReport:
    """报告构建：rows 来自持仓板块（指定>贴合），无有效贴合的持仓不进 rows。"""

    SECTOR_MAP = {
        "sectors": [
            _sector("880537.SH", "核电核能", "concept", 338, ["002366.SZ"]),
            _sector("880520.SH", "智能电网", "concept", 277, ["600312.SH"]),
        ]
    }

    @pytest.fixture(autouse=True)
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ttr, "SECTOR_DIR", tmp_path / "sectors")
        monkeypatch.setattr(ttr, "HOLDINGS_DIR", tmp_path / "holdings")
        monkeypatch.setattr(ttr, "OUT_DIR", tmp_path / "plans")
        for d in ("sectors", "holdings", "plans"):
            (tmp_path / d).mkdir()
        (
            tmp_path / "holdings" / "2026-08-07_holding_technical_summary.json"
        ).write_text(
            json.dumps(
                [
                    {"code": "002366", "name": "融发核电", "trend_state": "上涨"},
                    {"code": "600312", "name": "平高电气", "trend_state": "横盘震荡"},
                    {"code": "999999", "name": "无映射股", "trend_state": "上涨"},
                ]
            ),
            encoding="utf-8",
        )
        (tmp_path / "sectors" / "2026-08-04_tq_sector_map.json").write_text(
            json.dumps(self.SECTOR_MAP), encoding="utf-8"
        )
        klines = _stock_and("002366.SZ", {"880537.SH": 0.95})
        klines.update(_stock_and("600312.SH", {"880520.SH": 0.9}))
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: klines.get(code))
        monkeypatch.setattr(
            ttr.tm,
            "analyze",
            lambda df, code: (
                _an(
                    trend="上涨",
                    pos20="上沿/突破区",
                    macd_dir="扩张",
                    latest_date="2026-08-07",
                )
                if code == "880537.SH"
                else _an(trend="下跌", latest_date="2026-08-07")
            ),
        )
        self.tmp = tmp_path

    def test_rows_built_from_holding_sectors(self):
        """贴合有效的持仓出板块行：theme_id=板块代码、theme_name=板块名；
        无映射股（999999）不产生行。"""
        rows = ttr.build_sector_summary("2026-08-07")
        assert [r["theme_id"] for r in rows] == ["880537.SH", "880520.SH"]
        assert rows[0]["theme_name"] == "核电核能" and rows[0]["source"] == "fit"
        assert rows[0]["representative_stocks"] == ["002366"]
        assert rows[0]["fit"] > 0.9
        assert all(isinstance(r["available"], bool) for r in rows)

    def test_fit_insufficient_holding_not_in_rows(self, monkeypatch):
        """贴合无有效数据的持仓不产生板块行（不再回落猜）。"""
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
        assert ttr.build_sector_summary("2026-08-07") == []

    def test_main_writes_both_artifacts(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        ttr.main()
        md = ttr.OUT_DIR / "2026-08-07" / "2026-08-07_theme_tracker.md"
        js = ttr.SECTOR_DIR / "2026-08-07_sector_technical_summary.json"
        assert md.exists() and js.exists()
        text = md.read_text(encoding="utf-8")
        assert "主线方向：**核电核能**" in text
        assert "贴合最高者" in text  # §1 口径声明（v0.148）
        assert "核电核能（贴合" in text  # §4 贴合行带系数
        assert "| 999999 | 无映射股 | 未定 | 未定" in text
        rows = json.loads(js.read_text(encoding="utf-8"))
        assert {r["theme_id"] for r in rows} == {"880537.SH", "880520.SH"}

    def test_no_holdings_degrades_honestly(self, monkeypatch):
        """无持仓 ⇒ 主线如实「未定」，不编主线；摘要为空数组（契约允许）。"""
        (
            self.tmp / "holdings" / "2026-08-07_holding_technical_summary.json"
        ).write_text("[]", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        ttr.main()
        text = (ttr.OUT_DIR / "2026-08-07" / "2026-08-07_theme_tracker.md").read_text(
            encoding="utf-8"
        )
        assert "主线方向：**未定**" in text
        assert "如实降级" in text


def test_override_layer_fully_removed():
    """grep 守卫：owner 指定层 v0.149 整段移除——源码与治理目录不再有它的痕迹。"""
    src = pathlib.Path(ttr.__file__).read_text(encoding="utf-8")
    # 文件名在 docstring 里作为历史记述允许出现；代码级引用（常量/函数/分支）必须零残留
    for gone in (
        "load_mainline_overrides",
        "HOLDING_MAINLINE_OVERRIDES_FILE",
        "owner_override",
        "（指定）",
    ):
        assert gone not in src, f"{gone} 应已随指定层删除"
    import custos.core.paths as paths

    assert not hasattr(paths, "HOLDING_MAINLINE_OVERRIDES_FILE")
    override_file = paths.STRATEGY_DIR / "_shared" / "holding_mainline_overrides.json"
    assert not override_file.exists(), "指定表文件应已删除"
    import json as _json

    reg = _json.loads(paths.STRATEGY_REGISTRY_FILE.read_text(encoding="utf-8"))
    assert all(x["id"] != "holding_mainline_overrides" for x in reg["shared_rules"]), (
        "注册表残留指定层登记"
    )
