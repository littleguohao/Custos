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


def _sector(code, name, category, count, stocks):
    """合成 tq_sector_map 的板块条目。"""
    return {
        "code": code,
        "name": name,
        "category": category,
        "stock_count": count,
        "stocks": stocks,
    }


class TestSectorResolutionChain:
    """v0.142 四层解析链（人工主题表已删）：

    行业（pick_industry_sector）> 概念（共词优先取大）> 细分行业 > 无映射。
    区域/风格/统计指数不当主线。
    """

    SECTOR_MAP = {
        "sectors": [
            _sector("880223.SH", "四川板块", "region", 181, ["002366.SZ"]),
            _sector("880861.SH", "连续亏损", "style", 1075, ["002366.SZ"]),
            _sector("880507.SH", "国防军工", "concept", 532, ["002366.SZ"]),
            _sector("880537.SH", "核电核能", "concept", 338, ["002366.SZ"]),
            _sector("880666.SH", "可控核聚变", "concept", 118, ["002366.SZ"]),
            _sector("880611.SH", "核污染防治", "concept", 64, ["002366.SZ"]),
            _sector("880904.SH", "机器人概念", "concept", 1209, ["002366.SZ"]),
            _sector("880520.SH", "智能电网", "concept", 277, ["600312.SH"]),
            _sector("880964.SH", "特高压", "concept", 132, ["600312.SH"]),
            _sector("881303.SH", "专用设备", "sub_industry", 232, ["688001.SH"]),
        ]
    }

    def test_shared_token(self):
        """共词 = ≥2 字公共子串：「融发核电」∩「核电核能」=「核电」。"""
        assert ttr._shared_token("核电核能", "融发核电")
        assert not ttr._shared_token("可控核聚变", "融发核电")  # 只共用单字「核」
        assert not ttr._shared_token("核污染防治", "融发核电")
        assert not ttr._shared_token("核", "融发核电")  # 单字不算共词

    def test_shared_token_beats_larger_generic_concept(self):
        """共词优先：机器人概念（1209）虽最大但无共词，不可取；
        共词命中核电核能/可控核聚变/核污染防治里只有核电核能真有 ≥2 字共词。"""
        s = ttr.pick_holding_sector("002366", self.SECTOR_MAP, "融发核电专用机械")
        assert s["code"] == "880537.SH" and s["name"] == "核电核能"

    def test_shared_token_pool_picks_largest(self):
        """共词候选多个时取成分股最多者。"""
        sm = dict(self.SECTOR_MAP)
        sm["sectors"] = self.SECTOR_MAP["sectors"] + [
            _sector("880999.SH", "核电设备", "concept", 900, ["002366.SZ"])
        ]
        s = ttr.pick_holding_sector("002366", sm, "融发核电")
        assert s["code"] == "880999.SH"  # 核电设备(900) > 核电核能(338)，同属共词

    def test_no_shared_token_picks_largest_concept(self):
        """无共词候选 ⇒ 概念层取成分股最大者（机器人概念 1209）。"""
        s = ttr.pick_holding_sector("002366", self.SECTOR_MAP, "无关的名字")
        assert s["code"] == "880904.SH"

    def test_sub_industry_is_last_resort(self):
        """没有概念命中时才落细分行业。"""
        s = ttr.pick_holding_sector("688001", self.SECTOR_MAP, "任意名")
        assert s["code"] == "881303.SH"

    def test_region_or_style_only_is_no_mapping(self):
        """只剩区域/风格 = 无映射（返回 None），不许拿风格板块冒充主线。"""
        only_region = {"sectors": self.SECTOR_MAP["sectors"][:2]}
        assert ttr.pick_holding_sector("002366", only_region, "融发核电") is None
        assert ttr.pick_holding_sector("999999", self.SECTOR_MAP, "x") is None

    def test_industry_layer_precedes_concept(self, monkeypatch):
        """四层顺序：行业命中时**不进概念层**——即便概念有共词且更大。"""
        import pandas as pd

        import custos.datasource.local_tdx.tq_sector as tq_sector

        monkeypatch.setattr(
            tq_sector,
            "load_sector_names",
            lambda: {"880446": {"name": "电气设备", "tdx_type": "2"}},
        )
        monkeypatch.setattr(
            ttr.tm, "read_vipdoc", lambda code: pd.DataFrame({"close": [1.0]})
        )
        resolved = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, "电气设备"
        )
        sector, source = resolved
        assert source == "industry" and sector["code"] == "880446.SH"

    def test_industry_miss_falls_through_to_concept(self, monkeypatch):
        """行业名对不上名称表 ⇒ 落概念层（共词优先取大）。"""
        import custos.datasource.local_tdx.tq_sector as tq_sector

        monkeypatch.setattr(tq_sector, "load_sector_names", lambda: {})
        resolved = ttr.resolve_holding_sector(
            {"code": "002366", "name": "融发核电"}, self.SECTOR_MAP, "专用机械"
        )
        sector, source = resolved
        assert source == "reverse_membership" and sector["code"] == "880537.SH"

    def test_no_layer_hits_returns_none(self):
        """四层全空 ⇒ None（无映射，区别于行情缺失）。"""
        assert (
            ttr.resolve_holding_sector({"code": "999999", "name": "x"}, {}, None)
            is None
        )


class TestBuildAndReport:
    """报告构建：rows 来自持仓板块（四层链），不再是人工主题表。"""

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
        # 真实 owner 指定表（002366/600312 在表里）不得渗进合成用例
        monkeypatch.setattr(ttr, "load_mainline_overrides", lambda path=None: {})
        for d in ("sectors", "holdings", "plans"):
            (tmp_path / d).mkdir()
        (
            tmp_path / "holdings" / "2026-08-07_holding_technical_summary.json"
        ).write_text(
            json.dumps(
                [
                    {"code": "002366", "name": "融发核电", "trend_state": "上涨"},
                    {"code": "600312", "name": "平高电气", "trend_state": "横盘震荡"},
                ]
            ),
            encoding="utf-8",
        )
        (tmp_path / "sectors" / "2026-08-04_tq_sector_map.json").write_text(
            json.dumps(self.SECTOR_MAP), encoding="utf-8"
        )
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
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
        """每个持仓解析出的板块去重成行：theme_id=板块代码、theme_name=板块名。"""
        rows = ttr.build_sector_summary("2026-08-07")
        assert [r["theme_id"] for r in rows] == ["880537.SH", "880520.SH"]
        assert rows[0]["theme_name"] == "核电核能"
        assert rows[0]["source"] == "reverse_membership"
        assert rows[0]["representative_stocks"] == ["002366"]
        assert all(isinstance(r["available"], bool) for r in rows)

    def test_same_sector_merges_member_stocks(self):
        """两只持仓同属一板块 ⇒ 一行，代表股合并。"""
        sm = dict(self.SECTOR_MAP)
        sm["sectors"] = self.SECTOR_MAP["sectors"] + [
            _sector("880537.SH", "核电核能", "concept", 338, ["600312.SH"])
        ]
        (self.tmp / "sectors" / "2026-08-04_tq_sector_map.json").write_text(
            json.dumps(sm), encoding="utf-8"
        )
        rows = ttr.build_sector_summary("2026-08-07")
        assert len(rows) == 1
        assert sorted(rows[0]["representative_stocks"]) == ["002366", "600312"]

    def test_quote_missing_row_marks_unavailable(self, monkeypatch):
        """板块已定位但无 K 线 ⇒ quote_missing 行（available=False），
        与「无映射」（该持仓不进 rows）分得开。"""
        monkeypatch.setattr(
            ttr.tm,
            "analyze",
            lambda df, code: {"available": False, "error": "no kline data"},
        )
        rows = ttr.build_sector_summary("2026-08-07")
        assert len(rows) == 2
        assert all(r["available"] is False and r["quote_missing"] for r in rows)
        assert all(r["stage"] == "板块行情缺失" for r in rows)

    def test_unresolvable_holding_not_in_rows(self):
        """四层全空的持仓不产生板块行（rows 里没有它的位置，§4 才显示未定）。"""
        (
            self.tmp / "holdings" / "2026-08-07_holding_technical_summary.json"
        ).write_text(
            json.dumps([{"code": "999999", "name": "无映射股", "trend_state": "上涨"}]),
            encoding="utf-8",
        )
        assert ttr.build_sector_summary("2026-08-07") == []

    def test_main_writes_both_artifacts(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["x", "--date", "2026-08-07"])
        ttr.main()
        md = ttr.OUT_DIR / "2026-08-07" / "2026-08-07_theme_tracker.md"
        js = ttr.SECTOR_DIR / "2026-08-07_sector_technical_summary.json"
        assert md.exists() and js.exists()
        text = md.read_text(encoding="utf-8")
        assert "主线方向：**核电核能**" in text
        assert "仅覆盖持仓相关板块" in text  # §1 口径声明
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

    def test_module_does_not_read_deleted_theme_map(self):
        """反向钉测：人工主题表已删，本模块不许再引用它的路径/常量。"""
        src = pathlib.Path(ttr.__file__).read_text(encoding="utf-8")
        assert "SECTOR_MAP" not in src
        assert 'SECTORS_DIR / "sector_code_map.json"' not in src


class TestIndustryLayer:
    """行业层（v0.140）：holding_sector_mapping 行业名 → 880 行业板块（tdx_type=2）。

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
            _sector("880446.SH", "电气设备", "industry", 300, []),
            _sector("880447.SH", "电气设备", "industry", 100, []),
            _sector("880520.SH", "智能电网", "concept", 500, ["600312.SH"]),
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
        resolved = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, "电气设备"
        )
        sector, source = resolved
        assert source == "industry"
        assert sector["name"] == "电气设备" and sector["code"] == "880446.SH"

    def test_industry_name_unmatched_falls_to_concept(self):
        """行业名在名称表对不上 type=2 板块 ⇒ 落概念反查（取大：智能电网）。"""
        resolved = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, "不存在的行业"
        )
        sector, source = resolved
        assert source == "reverse_membership" and sector["code"] == "880520.SH"

    def test_same_name_non_industry_type_not_matched(self):
        """同名的概念板块（880960 tdx_type=4）不许命中行业层。"""
        resolved = ttr.resolve_holding_sector(
            {"code": "999999", "name": "x"}, self.SECTOR_MAP, "电气设备"
        )
        sector, _ = resolved
        assert sector["code"] == "880446.SH"  # type=2 精确匹配

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
        resolved = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP, "电气设备"
        )
        sector, source = resolved
        assert source == "reverse_membership" and sector["code"] == "880520.SH"

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
        resolved = ttr.resolve_holding_sector(
            {"code": "600312", "name": "平高电气"}, self.SECTOR_MAP
        )
        sector, source = resolved
        assert source == "reverse_membership" and sector["code"] == "880520.SH"

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


class TestQuoteMissingVsNoMapping:
    """「板块行情缺失」与「未定/无映射」两种文案必须分得开（v0.139 语义沿用）。"""

    SECTOR_MAP = {
        "sectors": [
            _sector("880537.SH", "核电核能", "concept", 338, ["002366.SZ"]),
        ]
    }

    def test_missing_kline_is_quote_missing_not_undecided(self, monkeypatch):
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: None)
        monkeypatch.setattr(
            ttr.tm,
            "analyze",
            lambda df, code: {"available": False, "error": "no kline data"},
        )
        resolved = ttr.resolve_holding_sector(
            {"code": "002366", "name": "融发核电"}, self.SECTOR_MAP
        )
        sector, source = resolved
        row = ttr._sector_analysis_row(sector, source, ["002366"])
        assert row["quote_missing"] is True
        assert row["stage"] == "板块行情缺失"
        s, why = ttr.compare_holding_to_theme({"trend_state": "上涨"}, row)
        assert s == "板块行情缺失" and "未定" not in s
        assert "板块行情缺失" in why

    def test_no_mapping_at_all_stays_undecided(self):
        resolved = ttr.resolve_holding_sector({"code": "999999", "name": "x"}, {}, None)
        assert resolved is None
        s, why = ttr.compare_holding_to_theme({"trend_state": "上涨"}, {})
        assert s == "未定" and "数据不足" in why


class TestHoldingsSection:
    """§4 集成：行业命中标「（行业）」，概念反查标「（反查）」，无映射显示「未定」。"""

    SECTOR_MAP = {
        "sectors": [
            _sector("880446.SH", "电气设备", "industry", 300, []),
            _sector("880537.SH", "核电核能", "concept", 338, ["002366.SZ"]),
        ]
    }

    @pytest.fixture(autouse=True)
    def env(self, tmp_path, monkeypatch):
        import pandas as pd

        import custos.datasource.local_tdx.tq_sector as tq_sector

        monkeypatch.setattr(ttr, "HOLDINGS_DIR", tmp_path / "holdings")
        monkeypatch.setattr(ttr, "SECTOR_DIR", tmp_path / "sectors")
        # 真实 owner 指定表（002366/600312 在表里）不得渗进合成用例
        monkeypatch.setattr(ttr, "load_mainline_overrides", lambda path=None: {})
        (tmp_path / "holdings").mkdir()
        (tmp_path / "sectors").mkdir()
        (
            tmp_path / "holdings" / "2026-08-07_holding_technical_summary.json"
        ).write_text(
            json.dumps(
                [
                    {"code": "600312", "name": "平高电气", "trend_state": "上涨"},
                    {"code": "002366", "name": "融发核电", "trend_state": "上涨"},
                    {"code": "999999", "name": "无映射股", "trend_state": "上涨"},
                ]
            ),
            encoding="utf-8",
        )
        (tmp_path / "holdings" / "2026-08-07_holding_sector_mapping.json").write_text(
            json.dumps([{"code": "600312", "industry": "电气设备"}]), encoding="utf-8"
        )
        (tmp_path / "sectors" / "2026-08-04_tq_sector_map.json").write_text(
            json.dumps(self.SECTOR_MAP), encoding="utf-8"
        )
        monkeypatch.setattr(
            tq_sector,
            "load_sector_names",
            lambda: {"880446": {"name": "电气设备", "tdx_type": "2"}},
        )
        monkeypatch.setattr(
            ttr.tm, "read_vipdoc", lambda code: pd.DataFrame({"close": [1.0]})
        )
        monkeypatch.setattr(
            ttr.tm,
            "analyze",
            lambda df, code: _an(
                trend="上涨", pos20="箱体上半区", latest_date="2026-08-07"
            ),
        )

    def test_section_marks(self):
        text = ttr.make_report("2026-08-07", [])
        assert "电气设备（行业）" in text
        assert "核电核能（反查）" in text
        assert "| 999999 | 无映射股 | 未定 |" in text

    def test_report_uses_given_holding_rows_without_recompute(self, monkeypatch):
        """make_report 传入 holding_rows 时不得重复解析（K 线只读一遍）。"""
        monkeypatch.setattr(
            ttr,
            "resolve_holding_rows",
            lambda date: pytest.fail("传入 holding_rows 后不得重算"),
        )
        row = {
            "theme_id": "880537.SH",
            "theme_name": "核电核能",
            "available": True,
            "stage": "修复",
            "score": 60.0,
            "source": "reverse_membership",
        }
        text = ttr.make_report("2026-08-07", [], {"002366": row})
        assert "核电核能（反查）" in text


class TestMainlineOverrides:
    """owner 指定层（v0.145）：解析链第①层，高于行业/概念一切自动解析。"""

    OVERRIDES = {
        "002366": {
            "sector_code": "880537.SH",
            "sector_name": "核电核能",
            "note": "注册行业专用机械不是主营，owner 指定核电核能。",
            "date": "2026-08-29",
        }
    }
    SECTOR_MAP = {
        "sectors": [
            _sector("880446.SH", "专用机械", "industry", 300, ["002366.SZ"]),
            _sector("880507.SH", "国防军工", "concept", 532, ["002366.SZ"]),
        ]
    }

    @pytest.fixture(autouse=True)
    def env(self, monkeypatch):
        import pandas as pd

        import custos.datasource.local_tdx.tq_sector as tq_sector

        monkeypatch.setattr(
            tq_sector,
            "load_sector_names",
            lambda: {"880446": {"name": "专用机械", "tdx_type": "2"}},
        )
        monkeypatch.setattr(
            ttr.tm, "read_vipdoc", lambda code: pd.DataFrame({"close": [1.0]})
        )
        monkeypatch.setattr(
            ttr.tm, "analyze", lambda df, code: _an(trend="上涨", pos20="箱体上半区")
        )
        self.pd = pd

    def test_override_beats_industry_and_concept(self):
        """指定优先于行业/概念：注册行业是专用机械（行业层可命中 880446），
        但 owner 指定核电核能 ⇒ 用指定。"""
        resolved = ttr.resolve_holding_sector(
            {"code": "002366", "name": "融发核电"},
            self.SECTOR_MAP,
            "专用机械",
            self.OVERRIDES,
        )
        sector, source = resolved
        assert source == "owner_override"
        assert sector["code"] == "880537.SH" and sector["name"] == "核电核能"
        assert "专用机械不是主营" in sector["note"]

    def test_override_miss_falls_through_to_auto_chain(self):
        """指定表里没有的持仓 ⇒ 走原自动链（行业层命中专用机械）。"""
        resolved = ttr.resolve_holding_sector(
            {"code": "002366", "name": "融发核电"}, self.SECTOR_MAP, "专用机械", {}
        )
        sector, source = resolved
        assert source == "industry" and sector["code"] == "880446.SH"

    def test_missing_override_file_keeps_behavior(self, tmp_path):
        """指定表文件缺失 ⇒ load 返回 {}，行为不变。"""
        assert ttr.load_mainline_overrides(tmp_path / "nonexistent.json") == {}
        resolved = ttr.resolve_holding_sector(
            {"code": "002366", "name": "融发核电"},
            self.SECTOR_MAP,
            "专用机械",
            ttr.load_mainline_overrides(tmp_path / "nonexistent.json"),
        )
        assert resolved[1] == "industry"

    def test_override_with_missing_kline_is_quote_missing(self, monkeypatch):
        """指定板块无 K 线 ⇒ quote_missing「板块行情缺失」（不回落自动链——
        owner 指定的板块没行情要如实暴露，不能悄悄换成自动解析结果）。"""
        monkeypatch.setattr(ttr.tm, "read_vipdoc", lambda code: self.pd.DataFrame())
        monkeypatch.setattr(
            ttr.tm,
            "analyze",
            lambda df, code: {"available": False, "error": "no kline data"},
        )
        resolved = ttr.resolve_holding_sector(
            {"code": "002366", "name": "融发核电"},
            self.SECTOR_MAP,
            "专用机械",
            self.OVERRIDES,
        )
        sector, source = resolved
        row = ttr._sector_analysis_row(sector, source, ["002366"])
        assert row["quote_missing"] is True and row["stage"] == "板块行情缺失"
        assert "owner 指定" in row["stage_reason"]

    def test_section_marks_override(self, tmp_path, monkeypatch):
        """§4 集成：指定行标注「（指定）」。"""
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
        monkeypatch.setattr(
            ttr, "load_mainline_overrides", lambda path=None: dict(self.OVERRIDES)
        )
        text = ttr.make_report("2026-08-07", [])
        assert "核电核能（指定）" in text
        assert "专用机械（行业）" not in text


def test_override_table_schema():
    """真实指定表 schema：code 6 位数字；sector_code 必须在板块名称表存在。

    名称表依赖本机 tdxzs3.cfg——不存在时跳过（CI 无 TDX 安装）。
    """
    import re

    from custos.datasource.local_tdx.tq_sector import load_sector_names

    name_map = load_sector_names()
    if not name_map:
        pytest.skip("本机无 tdxzs 名称表")
    overrides = ttr.load_mainline_overrides()
    assert overrides, "指定表为空"
    for code, ov in overrides.items():
        assert re.fullmatch(r"\d{6}", code), f"key 应为 6 位代码：{code}"
        bare = ov["sector_code"].split(".")[0]
        assert bare in name_map, f"{code} 的 sector_code {bare} 不在名称表"
        for field in ("sector_name", "note", "date"):
            assert ov.get(field), f"{code} 缺字段 {field}"
