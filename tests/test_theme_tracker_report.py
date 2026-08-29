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


class TestHoldingThemeMatching:
    ROWS = [
        {
            "theme_id": "chip",
            "theme_name": "半导体",
            "available": True,
            "score": 80,
            "holding_related": ["600000"],
            "representative_stocks": ["688981.SH"],
            "semantic_tags": ["芯片", "存储"],
            "trend_state": "上涨",
            "box20_position": "上沿/突破区",
        },
        {
            "theme_id": "robot",
            "theme_name": "机器人",
            "available": True,
            "score": 60,
            "holding_related": [],
            "representative_stocks": [],
            "semantic_tags": ["人形机器人"],
            "trend_state": "横盘震荡",
            "box20_position": "箱体下半区",
        },
    ]

    def test_explicit_code_wins(self):
        """显式关联优先于语义标签 —— 人工指定的映射不该被模糊匹配盖掉。"""
        r = ttr.match_holding_theme(
            {"code": "600000", "primary_themes": ["人形机器人"]}, self.ROWS
        )
        assert r["theme_id"] == "chip"

    def test_representative_stock_suffix_ignored(self):
        r = ttr.match_holding_theme({"code": "688981"}, self.ROWS)
        assert r["theme_id"] == "chip"

    def test_semantic_fallback(self):
        r = ttr.match_holding_theme(
            {"code": "000001", "primary_themes": ["人形机器人"]}, self.ROWS
        )
        assert r["theme_id"] == "robot"

    def test_nan_industry_ignored(self):
        """`industry` 是 pandas 读出来的，可能是字符串 'nan' —— 不能当成主题词。"""
        r = ttr.match_holding_theme({"code": "999999", "industry": "nan"}, self.ROWS)
        assert r == {}

    def test_no_match_returns_empty(self):
        assert ttr.match_holding_theme({"code": "999999"}, self.ROWS) == {}


class TestCompareHoldingToTheme:
    def test_theme_unavailable_is_undecided(self):
        """板块数据不足时必须「未定」，不能猜强弱。"""
        s, why = ttr.compare_holding_to_theme(
            {"trend_state": "上涨"}, {"available": False}
        )
        assert s == "未定" and "数据不足" in why

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


class TestBuildAndReport:
    @pytest.fixture(autouse=True)
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ttr, "SECTOR_MAP", tmp_path / "sector_code_map.json")
        monkeypatch.setattr(ttr, "SECTOR_DIR", tmp_path / "sectors")
        monkeypatch.setattr(ttr, "HOLDINGS_DIR", tmp_path / "holdings")
        monkeypatch.setattr(ttr, "OUT_DIR", tmp_path / "plans")
        for d in ("sectors", "holdings", "plans"):
            (tmp_path / d).mkdir()
        self.tmp = tmp_path

    def test_theme_without_primary_code_is_marked_unavailable(self, monkeypatch):
        """没有主板块代码的主题必须标 unavailable，**不能悄悄跳过** ——
        跳过会让主线清单看起来完整而其实少了条目。"""
        ttr.SECTOR_MAP.write_text(
            json.dumps(
                {
                    "themes": [
                        {
                            "theme_id": "x",
                            "theme_name": "无代码主题",
                            "priority": "high",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        rows = ttr.build_sector_summary("2026-08-07")
        assert len(rows) == 1
        assert rows[0]["available"] is False
        assert rows[0]["reason"] == "no primary sector code"

    def test_rows_sorted_unavailable_last_then_by_score(self, monkeypatch):
        """排序：可用的在前、分数降序 —— `make_report` 用 rows[0] 当「今日主线」。"""
        ttr.SECTOR_MAP.write_text(
            json.dumps(
                {
                    "themes": [
                        {
                            "theme_id": "a",
                            "theme_name": "弱",
                            "primary_sector_codes": ["880001"],
                        },
                        {
                            "theme_id": "b",
                            "theme_name": "强",
                            "primary_sector_codes": ["880002"],
                        },
                        {"theme_id": "c", "theme_name": "无码"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
        monkeypatch.setattr(
            ttr.tm,
            "analyze",
            lambda df, code: (
                _an(trend="上涨", pos20="上沿/突破区", macd_dir="扩张")
                if code == "880002"
                else _an(trend="下跌")
            ),
        )
        rows = ttr.build_sector_summary("2026-08-07")
        assert [r["theme_id"] for r in rows] == ["b", "a", "c"]

    def test_report_writes_both_artifacts(self, monkeypatch):
        ttr.SECTOR_MAP.write_text(
            json.dumps(
                {
                    "themes": [
                        {
                            "theme_id": "b",
                            "theme_name": "半导体",
                            "primary_sector_codes": ["880002"],
                            "priority": "high",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
        monkeypatch.setattr(
            ttr.tm,
            "analyze",
            lambda df, code: _an(
                trend="上涨",
                pos20="上沿/突破区",
                macd_dir="扩张",
                latest_date="2026-08-07",
            ),
        )
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        ttr.main()
        md = ttr.OUT_DIR / "2026-08-07" / "2026-08-07_theme_tracker.md"
        js = ttr.SECTOR_DIR / "2026-08-07_sector_technical_summary.json"
        assert md.exists() and js.exists()
        text = md.read_text(encoding="utf-8")
        assert "主线方向：**半导体**" in text
        assert "生命周期：**主升/加速**" in text

    def test_empty_theme_map_does_not_crash(self, monkeypatch):
        """主题表为空时**不能崩** —— 它是硬失败 stage，崩了整条盘后链就断。"""
        ttr.SECTOR_MAP.write_text(json.dumps({"themes": []}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        ttr.main()
        text = (ttr.OUT_DIR / "2026-08-07" / "2026-08-07_theme_tracker.md").read_text(
            encoding="utf-8"
        )
        assert "主线方向：**未定**" in text


class TestReverseMembershipFallback:
    """§4 持仓板块解析的反向成员关系兜底（v0.139）。

    主题映射 miss ≠ 板块行情缺失：没映射走最新 tq_sector_map 反查，
    选板块优先级 概念/行业 > 细分行业 >（区域/风格不当主线）；
    选中板块没 K 线必须如实标「板块行情缺失」，不许混进「未定/数据不足」。
    """

    SECTOR_MAP = {
        "sectors": [
            {
                "code": "880223.SH",
                "name": "四川板块",
                "category": "region",
                "stock_count": 181,
                "stocks": ["002366.SZ"],
            },
            {
                "code": "880861.SH",
                "name": "连续亏损",
                "category": "style",
                "stock_count": 1075,
                "stocks": ["002366.SZ"],
            },
            {
                "code": "880537.SH",
                "name": "核电核能",
                "category": "concept",
                "stock_count": 338,
                "stocks": ["002366.SZ"],
            },
            {
                "code": "880507.SH",
                "name": "国防军工",
                "category": "concept",
                "stock_count": 532,
                "stocks": ["002366.SZ"],
            },
            {
                "code": "881303.SH",
                "name": "专用设备",
                "category": "sub_industry",
                "stock_count": 232,
                "stocks": ["600312.SH"],
            },
        ]
    }

    def test_pick_prefers_concept_over_region_and_style(self):
        """区域/风格不当主线 —— 同票同时命中时也选概念板块。"""
        s = ttr.pick_holding_sector("002366", self.SECTOR_MAP)
        assert s is not None and s["category"] == "concept"

    def test_pick_largest_concept_first(self):
        """v0.140（owner 拍板「取大」）：同层候选取成分股最多（最大共识板块）者：
        国防军工(532) 先于 核电核能(338)——最具体≠最相关，迷你概念会带偏主线。"""
        s = ttr.pick_holding_sector("002366", self.SECTOR_MAP)
        assert s["code"] == "880507.SH"

    def test_pick_sub_industry_when_no_concept_or_industry(self):
        """没有概念/行业时才落细分行业（tdx_type=12）。"""
        s = ttr.pick_holding_sector("600312", self.SECTOR_MAP)
        assert s["code"] == "881303.SH"

    def test_region_or_style_only_is_no_mapping(self):
        """只剩区域/风格 = 无映射（返回 None），不许拿风格板块冒充主线。"""
        only_region = {"sectors": self.SECTOR_MAP["sectors"][:2]}
        assert ttr.pick_holding_sector("002366", only_region) is None
        assert ttr.pick_holding_sector("999999", self.SECTOR_MAP) is None

    def test_theme_hit_does_not_use_fallback(self, monkeypatch):
        """主题映射命中时**不绕兜底** —— 人工映射优先于反查。"""
        rows = [
            {
                "theme_id": "chip",
                "theme_name": "半导体",
                "available": True,
                "score": 80,
                "holding_related": ["002366"],
                "representative_stocks": [],
                "semantic_tags": [],
            }
        ]
        monkeypatch.setattr(
            ttr,
            "_fallback_holding_row",
            lambda h, sm, ind=None: pytest.fail("主题命中不得走兜底"),
        )
        r = ttr.resolve_holding_theme({"code": "002366"}, rows, self.SECTOR_MAP)
        assert r["theme_id"] == "chip"

    def test_fallback_row_has_real_sector(self, monkeypatch):
        """兜底出真实板块行：代码/名称/阶段/分数都来自该板块的分析。"""
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
        monkeypatch.setattr(
            ttr.tm, "analyze", lambda df, code: _an(trend="上涨", pos20="箱体上半区")
        )
        row = ttr.resolve_holding_theme({"code": "002366"}, [], self.SECTOR_MAP)
        assert row["source"] == "reverse_membership"
        # v0.140 取大：国防军工(532) > 核电核能(338)
        assert row["theme_name"] == "国防军工"
        assert row["primary_code"] == "880507.SH"
        assert row["available"] is True and "quote_missing" not in row

    def test_missing_kline_is_quote_missing_not_undecided(self, monkeypatch):
        """板块已定位但没 K 线 ⇒ 「板块行情缺失」，与「未定/数据不足」分得开。"""
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
        monkeypatch.setattr(
            ttr.tm,
            "analyze",
            lambda df, code: {"available": False, "error": "no kline data"},
        )
        row = ttr.resolve_holding_theme({"code": "002366"}, [], self.SECTOR_MAP)
        assert row["quote_missing"] is True
        assert row["stage"] == "板块行情缺失"
        s, why = ttr.compare_holding_to_theme({"trend_state": "上涨"}, row)
        assert s == "板块行情缺失" and "未定" not in s
        assert "板块行情缺失" in why

    def test_no_mapping_at_all_stays_undecided(self):
        """反查也没有 ⇒ 维持「未定/板块数据不足」（无映射 ≠ 行情缺失）。"""
        row = ttr.resolve_holding_theme({"code": "999999"}, [], self.SECTOR_MAP)
        assert row == {}
        s, why = ttr.compare_holding_to_theme({"trend_state": "上涨"}, row)
        assert s == "未定" and "数据不足" in why

    def test_section_holdings_uses_fallback(self, tmp_path, monkeypatch):
        """§4 集成：主题映射全 miss 时持仓行出反查板块，不再「未定」。"""
        monkeypatch.setattr(ttr, "HOLDINGS_DIR", tmp_path / "holdings")
        monkeypatch.setattr(ttr, "SECTOR_DIR", tmp_path / "sectors")
        (tmp_path / "holdings").mkdir()
        (tmp_path / "sectors").mkdir()
        (
            tmp_path / "holdings" / "2026-08-07_holding_technical_summary.json"
        ).write_text(
            json.dumps([{"code": "002366", "name": "融发核电", "trend_state": "上涨"}]),
            encoding="utf-8",
        )
        (tmp_path / "sectors" / "2026-08-04_tq_sector_map.json").write_text(
            json.dumps(self.SECTOR_MAP), encoding="utf-8"
        )
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
        monkeypatch.setattr(
            ttr.tm,
            "analyze",
            lambda df, code: _an(
                trend="上涨", pos20="箱体上半区", latest_date="2026-08-07"
            ),
        )
        text = ttr.make_report("2026-08-07", [])
        assert "国防军工（反查）" in text
        assert "| 002366 | 融发核电 | 未定" not in text


def test_industry_beats_concept_regardless_of_size():
    """v0.140（owner 定稿「行业优先+取大」）：行业层优先于概念层——
    即便行业板块成分股更少，也先选行业（主营）。"""
    from custos.pipeline.market_timing import theme_tracker_report as ttr

    sm = {
        "sectors": [
            {
                "code": "880446.SH",
                "name": "电气设备",
                "category": "industry",
                "stock_count": 300,
                "stocks": ["600312.SH"],
            },
            {
                "code": "880520.SH",
                "name": "智能电网",
                "category": "concept",
                "stock_count": 500,
                "stocks": ["600312.SH"],
            },
        ]
    }
    s = ttr.pick_holding_sector("600312", sm)
    assert s["code"] == "880446.SH", (
        "行业（电气设备 300 只）应压过概念（智能电网 500 只）"
    )


class TestIndustryLayer:
    """v0.140 行业层（owner 定稿「行业优先+取大」）。

    tq_sector_map 没有行业层（category 无 industry）——行业走
    holding_sector_mapping 的行业名（tdxhy 口径）→ 名称表 tdx_type=2 的 880 板块。
    名称对不上 / 行业板块无 K 线 ⇒ 落概念反查，不硬报缺失。
    """

    NAME_MAP = {
        "880446": {"name": "电气设备", "tdx_type": "2"},
        "880447": {"name": "电气设备", "tdx_type": "2"},  # 同名歧义候选
        "880520": {"name": "智能电网", "tdx_type": "4"},
        "880960": {"name": "电气设备", "tdx_type": "4"},  # 同名但非行业，不得命中
    }
    SECTOR_MAP = {
        "sectors": [
            {
                "code": "880446.SH",
                "name": "电气设备",
                "category": "industry",
                "stock_count": 300,
                "stocks": [],
            },
            {
                "code": "880447.SH",
                "name": "电气设备",
                "category": "industry",
                "stock_count": 100,
                "stocks": [],
            },
            {
                "code": "880520.SH",
                "name": "智能电网",
                "category": "concept",
                "stock_count": 500,
                "stocks": ["600312.SH"],
            },
        ]
    }

    @pytest.fixture(autouse=True)
    def env(self, monkeypatch):
        import pandas as pd

        import custos.datasource.local_tdx.tq_sector as tq_sector

        monkeypatch.setattr(tq_sector, "load_sector_names", lambda: dict(self.NAME_MAP))
        monkeypatch.setattr(
            ttr.tm,
            "read_vipdoc",
            lambda code: pd.DataFrame({"close": [1.0]}),
        )
        monkeypatch.setattr(
            ttr.tm, "analyze", lambda df, code: _an(trend="上涨", pos20="箱体上半区")
        )
        self.pd = pd

    def test_industry_beats_bigger_concept(self):
        """行业优先于概念——即便概念板块成分股更多（智能电网 500 > 电气设备 300）。"""
        row = ttr.resolve_holding_theme(
            {"code": "600312"}, [], self.SECTOR_MAP, industry_name="电气设备"
        )
        assert row["source"] == "industry"
        assert row["theme_name"] == "电气设备"
        assert row["primary_code"] == "880446.SH"

    def test_industry_name_unmatched_falls_to_concept(self):
        """行业名在名称表对不上 type=2 板块 ⇒ 落概念反查（取大：智能电网）。"""
        row = ttr.resolve_holding_theme(
            {"code": "600312"}, [], self.SECTOR_MAP, industry_name="不存在的行业"
        )
        assert row["source"] == "reverse_membership"
        assert row["primary_code"] == "880520.SH"

    def test_same_name_non_industry_type_not_matched(self):
        """同名的概念板块（880960 tdx_type=4）不许命中行业层。"""
        row = ttr.resolve_holding_theme(
            {"code": "999999"}, [], self.SECTOR_MAP, industry_name="电气设备"
        )
        # 999999 不在任何板块成员里，行业命中应仍是 880446（type=2 精确匹配）
        assert row["primary_code"] == "880446.SH"

    def test_industry_without_kline_falls_to_concept(self, monkeypatch):
        """行业板块无 K 线 ⇒ 落概念层，不硬报「板块行情缺失」。"""
        monkeypatch.setattr(
            ttr.tm,
            "read_vipdoc",
            lambda code: (
                self.pd.DataFrame()
                if code.startswith(("880446", "880447"))
                else self.pd.DataFrame({"close": [1.0]})
            ),
        )
        row = ttr.resolve_holding_theme(
            {"code": "600312"}, [], self.SECTOR_MAP, industry_name="电气设备"
        )
        assert row["source"] == "reverse_membership"
        assert row["primary_code"] == "880520.SH"

    def test_ambiguous_name_picks_largest_with_kline(self, monkeypatch):
        """同名歧义：取有 K 线且成分股最多者（880446 300 > 880447 100）。"""
        s = ttr.pick_industry_sector("电气设备", self.SECTOR_MAP)
        assert s["code"] == "880446.SH"
        # 880446 无 K 线时落 880447（而不是落概念层）
        monkeypatch.setattr(
            ttr.tm,
            "read_vipdoc",
            lambda code: (
                self.pd.DataFrame()
                if code.startswith("880446")
                else self.pd.DataFrame({"close": [1.0]})
            ),
        )
        s = ttr.pick_industry_sector("电气设备", self.SECTOR_MAP)
        assert s["code"] == "880447.SH"

    def test_missing_mapping_file_keeps_reverse_lookup(self, tmp_path, monkeypatch):
        """读不到 holding_sector_mapping ⇒ 行为不变（概念反查照旧）。"""
        monkeypatch.setattr(ttr, "HOLDINGS_DIR", tmp_path)
        assert ttr.holding_industry_names("2026-08-28") == {}
        row = ttr.resolve_holding_theme({"code": "600312"}, [], self.SECTOR_MAP)
        assert row["source"] == "reverse_membership"
        assert row["primary_code"] == "880520.SH"

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

    def test_section_holdings_marks_industry_source(self, tmp_path, monkeypatch):
        """§4 集成：行业命中标注「（行业）」，概念反查保持「（反查）」。"""
        monkeypatch.setattr(ttr, "HOLDINGS_DIR", tmp_path / "holdings")
        monkeypatch.setattr(ttr, "SECTOR_DIR", tmp_path / "sectors")
        (tmp_path / "holdings").mkdir()
        (tmp_path / "sectors").mkdir()
        (
            tmp_path / "holdings" / "2026-08-07_holding_technical_summary.json"
        ).write_text(
            json.dumps([{"code": "600312", "name": "平高电气", "trend_state": "上涨"}]),
            encoding="utf-8",
        )
        (tmp_path / "holdings" / "2026-08-07_holding_sector_mapping.json").write_text(
            json.dumps([{"code": "600312", "industry": "电气设备"}]), encoding="utf-8"
        )
        (tmp_path / "sectors" / "2026-08-04_tq_sector_map.json").write_text(
            json.dumps(self.SECTOR_MAP), encoding="utf-8"
        )
        text = ttr.make_report("2026-08-07", [])
        assert "电气设备（行业）" in text
        assert "智能电网（反查）" not in text
