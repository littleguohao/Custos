"""`final_close_review` 的渲染层 —— 17:00 盘后复盘报告的构件。

覆盖率清点（2026-08-07）：17%、157 语句未覆盖（`main` 占 124）。
它是 `run_1700` 的硬失败 stage：一挂，整份盘后复盘出不来。

这里补的是可独立测的渲染函数（`index_name` / `sector_for` / `render_news`）；
`main` 是 210 行的报告编排，测它需要铺十来份上游产物，
既有 `test_audit_p3_review.py` 已从事故回归的角度覆盖了它的关键分支。
"""

from __future__ import annotations

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
        """有证据但无交集 ⇒ 如实说「无交集」，不是静默空节。"""
        lines = []
        fcr.render_news(
            lines,
            {"sections": {"信息": [{"title": "t", "matched_themes": ["宏观政策"]}]}},
            hold_sectors={"半导体"},
        )
        assert any("无与今日持仓/操作的交集" in x for x in lines)

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
        )
        assert any("无与今日持仓/操作的交集" in x for x in lines)

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
        fcr.render_themes(
            lines,
            {
                "theme_lifecycles": [
                    {
                        "theme_name": "半导体",
                        "phase": "退潮",
                        "technical_stage": "退潮/下跌",
                        "score": 45,
                        "event_evidence_count": 2,
                    }
                ]
            },
        )
        text = "\n".join(lines)
        assert "## 4." in text and "半导体" in text and "退潮" in text

    def test_render_next_day_returns_plan(self):
        """§6 要把 `next_plan` 交回 main —— 落盘 payload 还要用它。"""
        lines = []
        plan = fcr.render_next_day(
            lines,
            {"next_day_plan": {"total_position_range": "0%-20%", "holding_plans": []}},
        )
        assert plan["total_position_range"] == "0%-20%"
        assert any("## 6." in x for x in lines)

    def test_render_discipline_returns_rules(self):
        lines = []
        rules = fcr.render_discipline(
            lines,
            {"rule_review": {"effective": ["e1"], "failed": [], "pending": ["p1"]}},
            {},
        )
        assert rules["effective"] == ["e1"]
        assert any("### 7.2" in x for x in lines)


def _revalued_row(code, b1):
    """「今日纪律检查」钉测用的最小 revalued 行（只带新节读取的字段）。"""
    return {"code": code, "name": f"测试{code}", "b1_holding_state": b1}


def _b1(priority="P3", action="条件持有", signals=None, shadow_signals=None):
    return {
        "final_priority": priority,
        "final_action": action,
        "final_reason": "x",
        "signals": signals or [],
        "shadow": {"signals": shadow_signals or []},
    }


class TestHabitCheck:
    """§1 延伸小节「今日纪律检查」（v0.127 owner 定稿）：止损/止盈旧习惯当日复发点名。

    钉的是判读口径本身：信号出现**且当日无该票卖出成交**才点名；已卖出不算复发；
    execution_review 缺 `rows` 时 fail-closed 降级（「没查」≠「查了没有」）。
    """

    def test_stop_signal_without_sell_is_called_out(self):
        """扛单不止损：B1 P0 止损/清仓级信号 + 当日无卖出 ⇒ 点名。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [
                _revalued_row(
                    "600000",
                    _b1(
                        "P0",
                        "止损/清仓评估",
                        [{"signal": "hard_loss", "priority": "P0"}],
                    ),
                )
            ],
            {"rows": [{"code": "600000", "actual_trades": []}]},
        )
        text = "\n".join(lines)
        assert (
            "扛单不止损" in text and "600000" in text and "当日无该票卖出成交" in text
        )

    def test_stop_signal_with_sell_is_not_called_out(self):
        """当日已有该票卖出成交 ⇒ 不算扛单，报「今日无复发」。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [
                _revalued_row(
                    "600000",
                    _b1(
                        "P0",
                        "止损/清仓评估",
                        [{"signal": "hard_loss", "priority": "P0"}],
                    ),
                )
            ],
            {
                "rows": [
                    {
                        "code": "600000",
                        "actual_trades": [{"交易类别": "卖出", "成交数量": 100}],
                    }
                ]
            },
        )
        text = "\n".join(lines)
        assert "扛单不止损" not in text and "今日无复发" in text

    def test_tail_p0p1_without_sell_is_called_out(self):
        """14:45 P0/P1 动作 + 当日无卖出 ⇒ 同样按扛单不止损点名（owner 口径）。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [_revalued_row("920808", _b1())],
            {
                "rows": [
                    {
                        "code": "920808",
                        "tail_priority": "P0",
                        "tail_action": "止损/清仓评估",
                        "actual_trades": [],
                    }
                ]
            },
        )
        text = "\n".join(lines)
        assert "扛单不止损" in text and "920808" in text and "14:45 P0" in text

    def test_buy_trade_does_not_count_as_sell(self):
        """当日只有**买入**成交不算卖出 —— 止损信号下买入正是「扛单还加仓」。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [
                _revalued_row(
                    "600000",
                    _b1(
                        "P0",
                        "止损/清仓评估",
                        [{"signal": "hard_loss", "priority": "P0"}],
                    ),
                )
            ],
            {
                "rows": [
                    {
                        "code": "600000",
                        "actual_trades": [{"交易类别": "买入", "成交数量": 100}],
                    }
                ]
            },
        )
        assert any("扛单不止损" in x for x in lines)

    def test_profit_take_signal_without_sell_is_called_out(self):
        """不止盈：two_bull_profit_take / 影子 plan_tp_scale_out + 无卖出 ⇒ 点名。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [
                _revalued_row(
                    "600000",
                    _b1(
                        "P2",
                        "分批止盈",
                        [{"signal": "two_bull_profit_take", "priority": "P2"}],
                    ),
                ),
                _revalued_row(
                    "688111",
                    _b1(
                        shadow_signals=[
                            {"signal": "plan_tp_scale_out", "priority": "P2"}
                        ]
                    ),
                ),
            ],
            {"rows": []},
        )
        text = "\n".join(lines)
        assert text.count("不止盈") >= 2 and "600000" in text and "688111" in text

    def test_no_signal_says_no_relapse(self):
        """无信号 ⇒ 如实写「今日无复发」（查了没有，不是没查）。"""
        lines = []
        fcr.render_habit_check(lines, [_revalued_row("600000", _b1())], {"rows": []})
        assert any("今日无复发" in x for x in lines)

    def test_missing_rows_degrades_fail_closed(self):
        """⚠️ execution_review 缺 `rows` ⇒ 降级如实报「未执行检查」，
        **不得**写成「今日无复发」—— 那会把「没查」显示成「查了没有」。"""
        lines = []
        fcr.render_habit_check(lines, [_revalued_row("600000", _b1())], {})
        text = "\n".join(lines)
        assert "unavailable" in text and "未执行检查" in text
        assert "今日无复发" not in text

    def test_only_stop_and_profit_habits_judged(self):
        """判读口径钉死：本节只判止损/止盈两类习惯（减仓类 P1 信号不算扛单）。"""
        lines = []
        fcr.render_habit_check(
            lines,
            [
                _revalued_row(
                    "600000",
                    _b1(
                        "P1",
                        "减仓评估",
                        [{"signal": "loss_reduction", "priority": "P1"}],
                    ),
                )
            ],
            {"rows": [{"code": "600000", "actual_trades": []}]},
        )
        assert any("今日无复发" in x for x in lines)
