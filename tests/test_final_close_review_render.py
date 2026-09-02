"""`final_close_review` 的渲染层 —— 17:00 盘后复盘报告的构件。

覆盖率清点（2026-08-07）：17%、157 语句未覆盖（`main` 占 124）。
它是 `run_1700` 的硬失败 stage：一挂，整份盘后复盘出不来。

这里补的是可独立测的渲染函数（`index_name` / `sector_for` / `render_news`）；
`main` 是 210 行的报告编排，测它需要铺十来份上游产物，
既有 `test_audit_p3_review.py` 已从事故回归的角度覆盖了它的关键分支。
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.pipeline.close_review import final_close_review as fcr  # noqa: E402


class TestIndexName:
    """个股 → **市场风格代理指数**。用于「个股服从板块、板块服从大盘」的对照。"""

    @pytest.mark.parametrize(
        "code,want",
        [
            ("688111", "科创50"),
            ("300750", "创业板指"),
            ("301001", "创业板指"),
            ("920808", "北证50"),
            ("600000", "上证指数"),
            ("601398", "上证指数"),
            ("000001", "深证成指"),
            ("002415", "深证成指"),
        ],
    )
    def test_mapping(self, code, want):
        assert want in fcr.index_name(code)

    def test_all_labeled_as_proxy(self):
        """每个都标「市场风格代理」—— 提示读者这是**风格参照**而非该股所属指数。"""
        for code in ("688111", "300750", "920808", "600000", "000001"):
            assert "市场风格代理" in fcr.index_name(code)

    def test_689_falls_to_shanghai_not_star50(self):
        """⚠️ 如实记录**已知边界**：`689`（科创板 CDR）会落到「上证指数」而非科创50。

        `index_name` 只判 `688` 前缀，689 走 `market_of` → SH → 上证指数。
        风格代理选错会让「个股服从大盘」的对照参照错的指数。
        涨跌幅那条链已在 `code_utils.price_limit_pct` 修正 689，
        这里没改是因为它只影响报告里的一行参照文本、且 689 实际只有个别票；
        若将来 689 变多，改动点就在这里。
        """
        assert "上证指数" in fcr.index_name("689009")


class TestSectorFor:
    SECTORS = [
        {
            "sector": "半导体",
            "holding_related": ["600000.SH"],
            "representative_stocks": ["688111"],
        },
        {"sector": "银行", "representative_stocks": ["601398.SH"]},
    ]

    def test_matches_via_holding_related(self):
        assert fcr.sector_for("600000", self.SECTORS)["sector"] == "半导体"

    def test_matches_via_representative_stocks(self):
        assert fcr.sector_for("688111", self.SECTORS)["sector"] == "半导体"
        assert fcr.sector_for("601398", self.SECTORS)["sector"] == "银行"

    def test_suffix_normalized_on_both_sides(self):
        """两侧都过 `bare_code` —— 上游给带后缀码、持仓给裸码时仍能对上。"""
        assert fcr.sector_for("601398", self.SECTORS)["sector"] == "银行"

    def test_no_match_returns_empty_dict(self):
        """查不到返回 `{}` 而非 None —— 调用方直接 `.get()` 不必判空。"""
        assert fcr.sector_for("999999", self.SECTORS) == {}

    def test_tolerates_missing_keys(self):
        assert fcr.sector_for("600000", [{"sector": "x"}]) == {}

    def test_first_match_wins(self):
        dup = [
            {"sector": "A", "representative_stocks": ["600000"]},
            {"sector": "B", "representative_stocks": ["600000"]},
        ]
        assert fcr.sector_for("600000", dup)["sector"] == "A"


class TestRenderNews:
    """v0.57 角色定版：盘后 §2 从信息流压缩为「与今日操作相关的事实核对」。"""

    def test_section_title_is_fact_check(self):
        lines = []
        fcr.render_news(lines, {})
        assert "## 2. 新闻、政策、风向与舆情（与今日操作相关的事实核对）" in "\n".join(
            lines
        )

    def test_empty_section_says_unavailable_not_blank(self):
        """⚠️ 空节写 `unavailable` 并说明原因（没过时效/来源质量门），
        不是留空 —— 留空分不清「没消息」与「这步没跑」。"""
        lines = []
        fcr.render_news(lines, {"sections": {}})
        assert any("`unavailable`" in x and "时效和来源质量门" in x for x in lines)

    def test_only_operation_relevant_facts_kept(self):
        """核心断言：无交集的行不进表（信息流压缩），有交集的进。"""
        lines = []
        fcr.render_news(
            lines,
            {
                "sections": {
                    "政策": [
                        {
                            "published_at": "2026-08-07T09:00",
                            "title": "国常会部署",
                            "source_name": "gov.cn",
                            "fact_status": "confirmed",
                            "matched_themes": ["半导体"],
                            "trade_meaning": "利多",
                        },
                        {
                            "published_at": "2026-08-07T10:00",
                            "title": "无关国际新闻",
                            "source_name": "x",
                            "fact_status": "confirmed",
                            "matched_themes": ["地缘"],
                        },
                    ]
                }
            },
            hold_sectors={"半导体"},
        )
        text = "\n".join(lines)
        assert "国常会部署" in text and "半导体" in text and "gov.cn/confirmed" in text
        assert "无关国际新闻" not in text, "无交集的信息流条目必须被压缩掉"

    def test_no_intersection_says_so(self):
        """有证据但无交集 ⇒ 如实说「已检索无交集」，不是静默空节（v0.136 措辞：
        与「这步没跑」的 unavailable 明确区分；``evidence=[]`` 表示全量证据已扫描）。"""
        lines = []
        fcr.render_news(
            lines,
            {"sections": {"信息": [{"title": "t", "matched_themes": ["宏观政策"]}]}},
            hold_sectors={"半导体"},
            evidence=[],
        )
        assert any("信息流已检索，无与持仓/操作交集的事实" in x for x in lines)

    def test_holding_code_match_counts(self):
        """matched_codes 命中持仓代码算交集（生产者形状：matched_codes=代码、
        matched_holdings=名称——2026-08-14 前错用 matched_holdings 相交，
        生产上恒为空）。"""
        lines = []
        fcr.render_news(
            lines,
            {
                "sections": {
                    "信息": [
                        {
                            "title": "浦发银行公告",
                            "matched_holdings": ["浦发银行"],
                            "matched_codes": ["600000"],
                        }
                    ]
                }
            },
            hold_codes={"600000"},
        )
        assert any("浦发银行公告" in x for x in lines)

    def test_holding_names_alone_do_not_match_codes(self):
        """名称键（matched_holdings）不得当成代码用——名称与代码永不相交，
        若错用它做代码交集，生产数据（只有名称）会静默漏报。"""
        lines = []
        fcr.render_news(
            lines,
            {"sections": {"信息": [{"title": "t", "matched_holdings": ["浦发银行"]}]}},
            hold_codes={"600000"},
            evidence=[],
        )
        assert any("无与持仓/操作交集的事实" in x for x in lines)

    def test_caps_at_eight_rows(self):
        """有界：最多 8 条 —— 报告要有界，否则一节几十条没人读。"""
        lines = []
        fcr.render_news(
            lines,
            {
                "sections": {
                    "信息": [
                        {"title": f"第{i}条", "matched_codes": ["600000"]}
                        for i in range(12)
                    ]
                }
            },
            hold_codes={"600000"},
        )
        assert sum(1 for x in lines if "第" in x and "条" in x) == 8

    def test_missing_sources_listed(self):
        """缺哪些新闻源要写出来 —— 否则「节为空」无法归因。"""
        lines = []
        fcr.render_news(lines, {"missing": ["rss_filter", "postclose_digest"]})
        assert any("新闻数据缺失" in x and "rss_filter" in x for x in lines)


class TestExtractedUnits:
    """2026-08-07 `main`（210 行）按报告小节拆开后，这些单元**可以单测** ——
    此前要验证持仓重估或指数行，得先铺齐 8 份上游产物再读整份报告。

    重构用 AST 自由变量分析算出每个函数的签名（手写参数列表漏了 `sectors`，
    第一版直接 NameError），并以**归一化后逐字节比对** md+json 验证行为等价。
    """

    def test_index_rows_shape(self):
        rows = fcr.index_rows(
            {
                "a_share_indices": {
                    "上证指数": {
                        "latest_close": 3400.5,
                        "daily_change_pct": -1.2,
                        "above_ma25": True,
                        "above_ma60": False,
                        "above_ma144": None,
                        "above_ma240": None,
                    }
                }
            }
        )
        assert len(rows) == 1
        r = rows[0]
        assert r["name"] == "上证指数" and r["close"] == 3400.5
        assert r["change_pct"] == -1.2
        assert r["above_ma144"] is None, "缺 MA 必须保持 None，交给 ma_flag 渲染 ?"

    def test_index_rows_tolerates_non_dict(self):
        """上游给了非字典（例如只写了一个数）时**跳过该指数**而不是崩 ——
        指数块是可选证据层。"""
        assert fcr.index_rows({"a_share_indices": {"x": 3400}}) == []

    def test_intraday_preferred_over_daily(self):
        """盘中值优先于日线收盘 —— 17:00 时两者都可能在，要用更新的那个。"""
        rows = fcr.index_rows(
            {
                "a_share_indices": {
                    "上证指数": {
                        "latest_close": 3400.0,
                        "daily_change_pct": -1.0,
                        "intraday": {"now": 3410.0, "intraday_change_pct": -0.7},
                    }
                }
            }
        )
        assert rows[0]["close"] == 3410.0 and rows[0]["change_pct"] == -0.7

    def test_unavailable_index_skipped(self):
        assert (
            fcr.index_rows(
                {"a_share_indices": {"x": {"available": False, "latest_close": 1.0}}}
            )
            == []
        )

    def test_index_rows_empty(self):
        assert fcr.index_rows({}) == []

    def test_revalue_positions_uses_live_quote_not_cost(self):
        """重估必须用**当日行情**，不是持仓快照里的价格。"""
        pmap = {
            "600000": {
                "代码": "600000",
                "名称": "浦发",
                "持有数量": 1000,
                "单位成本": 10.0,
                "持有金额": 8500,
                "仓位占比": 0.25,
            }
        }
        out = fcr.revalue_positions(
            day="2026-08-07",
            ff_map={},
            mfe_map={},
            pmap=pmap,
            qmap={
                "600000": {
                    "code": "600000",
                    "price": 8.5,
                    "change_pct": -3.2,
                    "date": "2026-08-07",
                }
            },
            regime="空头",
            sectors=[],
            tmap={
                "600000": {
                    "code": "600000",
                    "latest_date": "2026-08-07",
                    "trend_state": "下跌",
                }
            },
            total_assets=34000.0,
        )
        assert len(out) == 1
        r = out[0]
        assert r["close"] == 8.5
        assert r["pnl_pct"] == pytest.approx(-0.15), "(8.5/10 - 1) = -15%"

    def test_revalue_positions_without_quote_keeps_none(self):
        """⚠️ 没有当日行情时 `close` 必须留 None —— 不得回落到成本价，
        那会让一只没行情的票在报告里显示成「盈亏 0%」。"""
        pmap = {
            "600000": {
                "代码": "600000",
                "名称": "浦发",
                "持有数量": 1000,
                "单位成本": 10.0,
            }
        }
        out = fcr.revalue_positions(
            day="2026-08-07",
            ff_map={},
            mfe_map={},
            pmap=pmap,
            qmap={},
            regime="中性",
            sectors=[],
            tmap={},
            total_assets=None,
        )
        assert out[0]["close"] is None

    def test_render_helpers_append_to_lines(self):
        """render_* 沿用本文件既有约定 `render_news(lines, ...)`：**就地追加**。"""
        lines = []
        fcr.render_sector_board(
            lines,
            {
                "market_breadth": {
                    "up_count": 3000,
                    "down_count": 2000,
                    "as_of": "2026-08-28",
                }
            },
            {
                "gainers_top": [
                    {"rank": 1, "code": "880465", "name": "半导体", "pct": 2.5}
                ],
                "losers_top": [
                    {"rank": 1, "code": "880301", "name": "电力", "pct": -1.5}
                ],
            },
            "sector_daily_rank 采集器产物",
        )
        text = "\n".join(lines)
        assert "## 4. 板块题材涨跌幅榜与市场温度" in text
        assert (
            "半导体" in text
            and "+2.50%" in text
            and "电力" in text
            and "-1.50%" in text
        )

    def test_render_next_day_returns_plan(self):
        """§6 要把 `next_plan` 交回 main —— 落盘 payload 还要用它。"""
        lines = []
        plan = fcr.render_next_day(
            lines,
            {"next_day_plan": {"total_position_range": "0%-20%", "holding_plans": []}},
        )
        assert plan["total_position_range"] == "0%-20%"
        assert any("## 6." in x for x in lines)


def _revalued_row(code, b1=None, close=9.0, cost=10.0, pnl_pct=-0.01):
    """「今日纪律检查」钉测用的最小 revalued 行（只带新节读取的字段）。

    默认 pnl_pct=-0.01（未越 −7% 线）——止损判读**默认不命中**，
    要命中扛单请显式给 pnl_pct 或 plans。"""
    return {
        "code": code,
        "name": f"测试{code}",
        "close": close,
        "cost": cost,
        "pnl_pct": pnl_pct,
        "b1_holding_state": b1 or _b1(),
    }


def _b1(priority="P3", action="条件持有", signals=None, shadow_signals=None):
    return {
        "final_priority": priority,
        "final_action": action,
        "final_reason": "x",
        "signals": signals or [],
        "shadow": {"signals": shadow_signals or []},
    }


class TestHabitCheck:
    """§1 延伸小节「今日纪律检查」（v0.136 owner 定稿）：三类旧习惯当日复发点名。

    钉的是判读口径本身：
    - 扛单不止损 = 亏损**越过止损线**（计划 stop.price 优先，无计划 −7% 减仓线）
      且当日无该票卖出成交；未越线是正常观察，不报；
    - 买入不符合策略买点 = 空头期买入 / 买入日 J≥13；
    - 应止盈未止盈 = 止盈信号命中且当日无卖出；
    - 数据缺失 fail-closed 降级（「没查」≠「查了没有」≠「无复发」）。
    """

    # ── 扛单不止损：止损线判读 ──

    def test_plan_stop_breach_without_sell_is_called_out(self):
        """有持仓计划：收盘低于计划止损价 + 当日无卖出 ⇒ 扛单点名。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [_revalued_row("600000", close=9.0, pnl_pct=-0.10)],
            {"rows": [{"code": "600000", "actual_trades": []}]},
            plans={"600000": {"stop": {"price": 9.5, "basis": "近10日最低价"}}},
            regime="中性",
            tmap={},
        )
        text = "\n".join(lines)
        assert "扛单不止损" in text and "600000" in text
        assert "计划止损价 9.50" in text and "当日无该票卖出成交" in text

    def test_plan_stop_not_breached_is_not_called_out(self):
        """有计划在止损价上方 ⇒ 不报（正常观察，不是扛单）。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [_revalued_row("600000", close=9.6, pnl_pct=-0.04)],
            {"rows": [{"code": "600000", "actual_trades": []}]},
            plans={"600000": {"stop": {"price": 9.5}}},
            regime="中性",
            tmap={},
        )
        text = "\n".join(lines)
        assert "扛单不止损" not in text and "今日无复发" in text

    def test_no_plan_uses_loss_reduction_line(self):
        """无持仓计划：盈亏越 −7% 减仓线 ⇒ 扛单点名（exit_rules 口径）。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [_revalued_row("600000", pnl_pct=-0.08)],
            {"rows": [{"code": "600000", "actual_trades": []}]},
            plans={},
            regime="中性",
            tmap={},
        )
        text = "\n".join(lines)
        assert "扛单不止损" in text and "−7% 减仓线" in text

    def test_no_plan_loss_above_line_is_not_called_out(self):
        """无计划、亏损 −5%（未越 −7% 线）⇒ 不报 —— 旧版对 P0/P1 信号都报是误报。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [
                _revalued_row(
                    "600000",
                    _b1("P0", "止损/清仓评估", [{"signal": "hard_loss"}]),
                    pnl_pct=-0.05,
                )
            ],
            {"rows": [{"code": "600000", "actual_trades": []}]},
            plans={},
            regime="中性",
            tmap={},
        )
        text = "\n".join(lines)
        assert "扛单不止损" not in text and "今日无复发" in text

    def test_stop_breach_with_sell_is_not_called_out(self):
        """越线但当日已有该票卖出成交 ⇒ 不算扛单，报「今日无复发」。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [_revalued_row("600000", pnl_pct=-0.10)],
            {
                "rows": [
                    {
                        "code": "600000",
                        "actual_trades": [{"交易类别": "卖出", "成交数量": 100}],
                    }
                ]
            },
            plans={},
            regime="中性",
            tmap={},
        )
        text = "\n".join(lines)
        assert "扛单不止损" not in text and "今日无复发" in text

    def test_missing_close_degrades_fail_closed(self):
        """⚠️ 收盘价缺失 ⇒ 止损线判读未执行，如实降级 —— 不得写成「今日无复发」。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [_revalued_row("600000", close=None, pnl_pct=None)],
            {"rows": [{"code": "600000", "actual_trades": []}]},
            plans={"600000": {"stop": {"price": 9.5}}},
            regime="中性",
            tmap={},
        )
        text = "\n".join(lines)
        assert "unavailable" in text and "判读未执行" in text
        assert "今日无复发" not in text

    # ── 买入不符合策略买点 ──

    def test_buy_in_bear_regime_is_called_out(self):
        """0AMV 空头期买入 ⇒「空头期买入，违反纪律（空头不买）」。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [],
            {
                "rows": [
                    {
                        "code": "600000",
                        "name": "测试买入",
                        "actual_trades": [{"交易类别": "买入", "成交数量": 100}],
                    }
                ]
            },
            plans={},
            regime="空头",
            tmap={"600000": {"daily_j": 5.0}},
        )
        text = "\n".join(lines)
        assert (
            "买入不符合策略买点" in text and "空头期买入，违反纪律（空头不买）" in text
        )

    def test_buy_with_j_above_13_is_called_out(self):
        """买入日 J≥13 ⇒「非 B1 买点买入」（J<13 是硬门槛）。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [],
            {
                "rows": [
                    {
                        "code": "600000",
                        "actual_trades": [{"交易类别": "买入", "成交数量": 100}],
                    }
                ]
            },
            plans={},
            regime="中性",
            tmap={"600000": {"daily_j": 27.1}},
        )
        text = "\n".join(lines)
        assert "买入不符合策略买点" in text and "非 B1 买点买入" in text
        assert "J=27.1" in text

    def test_buy_with_j_below_13_in_neutral_is_clean(self):
        """J<13 且非空头期 ⇒ 买点合规，不报。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [],
            {
                "rows": [
                    {
                        "code": "600000",
                        "actual_trades": [{"交易类别": "买入", "成交数量": 100}],
                    }
                ]
            },
            plans={},
            regime="中性",
            tmap={"600000": {"daily_j": 4.4}},
        )
        text = "\n".join(lines)
        assert "买入不符合策略买点" not in text and "今日无复发" in text

    def test_buy_with_missing_j_degrades(self):
        """买入票缺 daily_j ⇒ 买点判读未执行，如实降级（不冒充无复发）。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [],
            {
                "rows": [
                    {
                        "code": "600000",
                        "actual_trades": [{"交易类别": "买入", "成交数量": 100}],
                    }
                ]
            },
            plans={},
            regime="中性",
            tmap={},
        )
        text = "\n".join(lines)
        assert "买入不符合策略买点" not in text
        assert "unavailable" in text and "今日无复发" not in text

    # ── 应止盈未止盈（保留口径） ──

    def test_profit_take_signal_without_sell_is_called_out(self):
        """应止盈未止盈：two_bull_profit_take / 影子 plan_tp_scale_out + 无卖出 ⇒ 点名。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [
                _revalued_row(
                    "600000",
                    _b1("P2", "分批止盈", [{"signal": "two_bull_profit_take"}]),
                    pnl_pct=0.15,
                ),
                _revalued_row(
                    "688111",
                    _b1(shadow_signals=[{"signal": "plan_tp_scale_out"}]),
                    pnl_pct=0.20,
                ),
            ],
            {"rows": []},
            plans={},
            regime="中性",
            tmap={},
        )
        text = "\n".join(lines)
        assert text.count("应止盈未止盈") >= 2 and "600000" in text and "688111" in text

    # ── 通用 ──

    def test_no_signal_says_no_relapse(self):
        """三类都无命中 ⇒ 如实写「今日无复发」（查了没有，不是没查）。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [_revalued_row("600000")],
            {"rows": []},
            plans={},
            regime="中性",
            tmap={},
        )
        assert any("今日无复发" in x for x in lines)

    def test_missing_rows_degrades_fail_closed(self):
        """⚠️ execution_review 缺 `rows` ⇒ 降级如实报「未执行检查」，
        **不得**写成「今日无复发」—— 那会把「没查」显示成「查了没有」。"""
        lines = []
        fcr.render_habit_check(lines, [_revalued_row("600000")], {})
        text = "\n".join(lines)
        assert "unavailable" in text and "未执行检查" in text
        assert "今日无复发" not in text


class TestNewsEvidenceFallback:
    """§2 v0.136：digest 优先 + 全量 RSS 证据兜底。

    钉的是匹配层修复本身：digest 没选上的候选不再漏报（owner 案例：「美国发起
    电力设备检查」影响电力持仓，但该新闻不在 digest 池里）；仍无交集如实写明；
    证据文件缺失 unavailable（与「检索了没有」分开）。
    """

    EVIDENCE = [
        {
            "title": "美国发起电力设备检查",
            "summary": "涉及输变电设备出口",
            "source_name": "金十数据-快讯",
            "source_tier": "B",
            "published_at": "2026-08-28T10:00:00+00:00",
        },
        {
            "title": "无关国际新闻",
            "summary": "与持仓无关",
            "source_name": "x",
            "source_tier": "C",
            "published_at": "2026-08-28T11:00:00+00:00",
        },
    ]
    KEYWORDS = {"600312": ("平高电气", ["平高电气", "电气设备", "输变电设备", "电力"])}

    def test_fallback_hit_when_digest_missed(self):
        """核心断言：digest 无交集但全量证据按持仓关键词命中 ⇒ 列出（带来源/tier/时间）。"""
        lines = []
        fcr.render_news(
            lines,
            {
                "sections": {
                    "信息": [{"title": "digest 里的无关条", "matched_themes": ["宏观"]}]
                }
            },
            hold_sectors={"半导体"},
            evidence=self.EVIDENCE,
            hold_keywords=self.KEYWORDS,
        )
        text = "\n".join(lines)
        assert "美国发起电力设备检查" in text
        assert "金十数据-快讯/B" in text and "600312 平高电气" in text
        assert "无关国际新闻" not in text, "无交集的证据条目必须被压缩掉"

    def test_fallback_no_hit_says_searched_no_intersection(self):
        """全量证据检索后仍无交集 ⇒ 如实写「信息流已检索，无与持仓/操作交集的事实」。"""
        lines = []
        fcr.render_news(
            lines,
            {"sections": {"信息": [{"title": "t", "matched_themes": ["宏观"]}]}},
            evidence=[self.EVIDENCE[1]],
            hold_keywords=self.KEYWORDS,
        )
        assert any("信息流已检索，无与持仓/操作交集的事实" in x for x in lines)

    def test_missing_evidence_is_unavailable_not_no_intersection(self):
        """⚠️ 证据文件缺失 ⇒ unavailable「兜底匹配未执行」——不得写成「无交集」。"""
        lines = []
        fcr.render_news(
            lines,
            {"sections": {"信息": [{"title": "t", "matched_themes": ["宏观"]}]}},
            evidence=None,
            hold_keywords=self.KEYWORDS,
        )
        text = "\n".join(lines)
        assert "兜底匹配未执行" in text
        assert "无与持仓/操作交集的事实" not in text

    def test_digest_priority_and_fallback_dedupes(self):
        """digest 已展示的条目，兜底不再重复列（按标题去重）。"""
        lines = []
        fcr.render_news(
            lines,
            {
                "sections": {
                    "信息": [
                        {
                            "title": "美国发起电力设备检查",
                            "matched_themes": ["电气设备"],
                            "source_name": "金十数据-快讯",
                            "fact_status": "confirmed",
                        }
                    ]
                }
            },
            hold_sectors={"电气设备"},
            evidence=self.EVIDENCE,
            hold_keywords=self.KEYWORDS,
        )
        text = "\n".join(lines)
        assert text.count("美国发起电力设备检查") == 1

    def test_keywords_built_from_mapping_and_themes(self):
        """关键词来源钉死：股票名 + industry/BlockName/concepts + 主题名分段。

        v0.156 起主题行不再有 semantic_tags（人工主题映射表已废弃），
        关键词只从 theme_name 分段取。"""
        pmap = {"600312": {"代码": "600312", "名称": "平高电气"}}
        sectors = [
            {
                "theme_name": "电力/电网设备",
                "holding_related": ["600312.SH"],
            }
        ]
        mapping = [
            {
                "code": "600312",
                "industry": "电气设备",
                "concepts": ["储能"],
                "raw_relation": [{"BlockName": "输变电设备"}],
            }
        ]
        name, words = fcr._holding_keywords(pmap, sectors, mapping)["600312"]
        assert name == "平高电气"
        for w in (
            "平高电气",
            "电气设备",
            "输变电设备",
            "储能",
            "电力",
            "电网设备",
        ):
            assert w in words, w

    def test_news_interest_reads_theme_name(self):
        """⚠️ hold_sectors 必须读 `theme_name`（生产形状）——漏了它主题交集恒为空。"""
        sectors = [{"theme_name": "电力/电网设备", "holding_related": ["600312.SH"]}]
        _codes, hold_sectors = fcr._news_interest(
            {"600312": {"代码": "600312"}}, [], sectors
        )
        assert "电力/电网设备" in hold_sectors


class TestSectorBoard:
    """§4 v0.136：板块题材涨跌幅榜与市场温度（客观事实展示，非主线判定）。"""

    def test_collector_product_rendered(self):
        """采集器当日榜在 ⇒ 直接用其 TOP10 涨/跌幅（#26 口径复用；v0.166 起 TOP5→TOP10）。"""
        rank = {
            "gainers_top": [
                {
                    "rank": i + 1,
                    "code": f"8804{i:02d}",
                    "name": f"涨板块{i}",
                    "pct": 3.0 - i * 0.1,
                }
                for i in range(12)
            ],
            "losers_top": [{"rank": 1, "code": "880301", "name": "电力", "pct": -2.0}],
        }
        lines = []
        fcr.render_sector_board(lines, {}, rank, "sector_daily_rank 采集器产物")
        text = "\n".join(lines)
        assert "板块题材涨跌幅榜与市场温度" in text
        assert "客观事实展示，非主线判定" in text
        assert "涨幅 TOP10" in text and "跌幅 TOP10" in text
        assert "涨板块0" in text and "电力" in text and "-2.00%" in text
        assert "涨板块9" in text, "第 10 名应显示"
        assert "涨板块10" not in text, "第 11 名起应截断"
        assert "待重设计" not in text, "#26 待重设计标注必须随旧节删掉"

    def test_both_missing_is_unavailable_with_chain_note(self):
        """榜不可得 ⇒ unavailable 并注明链路现状：sector_daily_rank 已接入
        17:00 链（两个 best-effort stage，v0.158），unavailable = 当日均未产出。"""
        lines = []
        fcr.render_sector_board(lines, {}, None, None)
        text = "\n".join(lines)
        assert "unavailable" in text and "已接入 17:00 链" in text
        assert "未接入日链" not in text, "sector_daily_rank 已接入日链，旧文案不得复活"

    def test_fallback_computes_from_sector_index_cache(self, tmp_path, monkeypatch):
        """采集器榜缺失 ⇒ 用板块指数缓存自算当日涨跌幅（run_1800 每日更新的兜底）。"""
        index_dir = tmp_path / "sector_index"
        index_dir.mkdir()
        (index_dir / "880001.SH.csv").write_text(
            "date,close\n2026-08-27,100\n2026-08-28,103\n", encoding="utf-8"
        )
        (index_dir / "880002.SH.csv").write_text(
            "date,close\n2026-08-27,200\n2026-08-28,196\n", encoding="utf-8"
        )
        (index_dir / "880003.SH.csv").write_text(
            "date,close\n2026-08-27,300\n",
            encoding="utf-8",  # 无当日数据
        )
        monkeypatch.setattr(fcr, "_sector_name_map", lambda: {})
        rank = fcr._sector_rank_fallback("2026-08-28", index_dir)
        assert rank["gainers_top"][0]["code"] == "880001"
        assert rank["gainers_top"][0]["pct"] == pytest.approx(3.0)
        assert rank["losers_top"][0]["code"] == "880002"
        assert rank["losers_top"][0]["pct"] == pytest.approx(-2.0)
        assert all(
            e["code"] != "880003" for e in rank["gainers_top"] + rank["losers_top"]
        )

    def test_fallback_none_when_no_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fcr, "_sector_name_map", lambda: {})
        assert fcr._sector_rank_fallback("2026-08-28", tmp_path) is None

    def test_collector_product_preferred_when_present(self, tmp_path, monkeypatch):
        """主路径真命中：采集器当日榜存在 ⇒ 直接用它，**不走**缓存自算兜底。

        此前采集器未接入日链，data/sectors/daily_rank/{day}.json 在生产上永不
        存在，主路径恒走兜底 —— 本测钉住「产物在 ⇒ 主路径生效」（接线钉测见
        test_sector_daily_rank.py::TestWiredIntoDailyChain）。
        """
        rank_dir = tmp_path / "sectors" / "daily_rank"
        rank_dir.mkdir(parents=True)
        payload = {
            "date": "2026-08-28",
            "gainers_top": [
                {"rank": 1, "code": "880465", "name": "半导体", "pct": 2.5}
            ],
            "losers_top": [{"rank": 1, "code": "880301", "name": "电力", "pct": -1.5}],
        }
        (rank_dir / "2026-08-28.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        monkeypatch.setattr(fcr, "DATA", tmp_path)
        monkeypatch.setattr(
            fcr,
            "_sector_rank_fallback",
            lambda *a, **k: pytest.fail("主路径命中时不该走缓存自算兜底"),
        )
        rank, source = fcr._sector_rank("2026-08-28")
        assert source == "sector_daily_rank 采集器产物"
        assert rank["gainers_top"][0]["code"] == "880465"

    @pytest.mark.parametrize(
        "pct,want",
        [
            (-5.0, "冰点"),
            (30.0, "不达标"),
            (64.9, "不达标"),
            (65.0, "及格"),
            (85.0, "强势"),
            (140.0, "较佳"),
            (151.0, "警惕冲顶"),
        ],
    )
    def test_temperature_six_tiers(self, pct, want):
        """市场温度六档判定（CZ 波段战法 §三）：<0 冰点 / 0~65% 不达标 / 65%+ 及格 /
        80%+ 强势 / 130~150% 较佳 / >150% 警惕冲顶。"""
        assert want in fcr._temperature_verdict(pct)

    def test_temperature_formula_and_render(self):
        """温度 = (涨−跌)÷跌×100%：(3000−2000)/2000 = +50% ⇒ 不达标。"""
        lines = []
        fcr.render_sector_board(
            lines,
            {
                "market_breadth": {
                    "up_count": 3000,
                    "down_count": 2000,
                    "as_of": "2026-08-28",
                }
            },
            None,
            None,
        )
        text = "\n".join(lines)
        assert (
            "+50.0%" in text and "不达标" in text and "涨 3000 家 / 跌 2000 家" in text
        )

    def test_temperature_missing_down_count_degrades(self):
        """⚠️ 880005 只给涨家数、跌家数无真实来源 ⇒ 温度 unavailable，不编造、不冒充冰点。"""
        lines = []
        fcr.render_sector_board(
            lines,
            {"market_breadth": {"up_count": 3013, "down_count": None}},
            None,
            None,
        )
        text = "\n".join(lines)
        assert "unavailable" in text and "不编造" in text
        assert "温度 **" not in text, "缺跌家数不得给出温度读数"
