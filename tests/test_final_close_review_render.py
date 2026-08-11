"""`final_close_review` 的渲染层 —— 17:00 盘后复盘报告的构件。

覆盖率清点（2026-08-07）：17%、157 语句未覆盖（`main` 占 124）。
它是 `run_1700` 的硬失败 stage：一挂，整份盘后复盘出不来。

这里补的是可独立测的渲染函数（`index_name` / `sector_for` / `render_news`）；
`main` 是 210 行的报告编排，测它需要铺十来份上游产物，
既有 `test_audit_p3_review.py` 已从事故回归的角度覆盖了它的关键分支。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("src", "src/custos/pipeline/close_review"):
    sys.path.insert(0, str(ROOT / _p))

from custos.pipeline.close_review import final_close_review as fcr  # noqa: E402


class TestIndexName:
    """个股 → **市场风格代理指数**。用于「个股服从板块、板块服从大盘」的对照。"""

    @pytest.mark.parametrize("code,want", [
        ("688111", "科创50"), ("300750", "创业板指"), ("301001", "创业板指"),
        ("920808", "北证50"), ("600000", "上证指数"), ("601398", "上证指数"),
        ("000001", "深证成指"), ("002415", "深证成指"),
    ])
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
        {"sector": "半导体", "holding_related": ["600000.SH"],
         "representative_stocks": ["688111"]},
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
        dup = [{"sector": "A", "representative_stocks": ["600000"]},
               {"sector": "B", "representative_stocks": ["600000"]}]
        assert fcr.sector_for("600000", dup)["sector"] == "A"


class TestRenderNews:
    def test_four_fixed_sections_always_present(self):
        """四节（信息/政策/风向/舆情）**恒定出现** ——
        缺节会让读者以为报告残缺，而空节能明确表达「这个窗口没有证据」。"""
        lines = []
        fcr.render_news(lines, {})
        text = "\n".join(lines)
        for i, name in enumerate(["信息", "政策", "风向", "舆情"], 1):
            assert f"### 2.{i} {name}" in text

    def test_empty_section_says_unavailable_not_blank(self):
        """⚠️ 空节写 `unavailable` 并说明原因（没过时效/来源质量门），
        不是留空 —— 留空分不清「没消息」与「这步没跑」。"""
        lines = []
        fcr.render_news(lines, {"sections": {}})
        assert any("`unavailable`" in x and "时效和来源质量门" in x for x in lines)

    def test_rows_rendered_with_source_and_fact_status(self):
        """每条都带**来源/质量** —— 读者要能判断这条能不能当既成事实。"""
        lines = []
        fcr.render_news(lines, {"sections": {"政策": [{
            "published_at": "2026-08-07T09:00", "title": "国常会部署",
            "source_name": "gov.cn", "fact_status": "confirmed",
            "matched_themes": ["宏观政策"], "trade_meaning": "利多"}]}})
        row = [x for x in lines if "国常会部署" in x][0]
        assert "gov.cn/confirmed" in row and "宏观政策" in row and "利多" in row

    def test_affected_defaults_to_pending(self):
        lines = []
        fcr.render_news(lines, {"sections": {"信息": [{"title": "t"}]}})
        assert any("待确认" in x for x in lines if "t" in x)

    def test_caps_at_five_rows_per_section(self):
        """每节最多 5 条 —— 报告要有界，否则一节几十条没人读。"""
        lines = []
        fcr.render_news(lines, {"sections": {"信息": [
            {"title": f"第{i}条"} for i in range(9)]}})
        assert sum(1 for x in lines if "第" in x and "条" in x) == 5

    def test_missing_sources_listed(self):
        """缺哪些新闻源要写出来 —— 否则「政策节为空」无法归因。"""
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
        rows = fcr.index_rows({"a_share_indices": {
            "上证指数": {"latest_close": 3400.5, "daily_change_pct": -1.2,
                       "above_ma25": True, "above_ma60": False,
                       "above_ma144": None, "above_ma240": None}}})
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
        rows = fcr.index_rows({"a_share_indices": {"上证指数": {
            "latest_close": 3400.0, "daily_change_pct": -1.0,
            "intraday": {"now": 3410.0, "intraday_change_pct": -0.7}}}})
        assert rows[0]["close"] == 3410.0 and rows[0]["change_pct"] == -0.7

    def test_unavailable_index_skipped(self):
        assert fcr.index_rows({"a_share_indices": {
            "x": {"available": False, "latest_close": 1.0}}}) == []

    def test_index_rows_empty(self):
        assert fcr.index_rows({}) == []

    def test_revalue_positions_uses_live_quote_not_cost(self):
        """重估必须用**当日行情**，不是持仓快照里的价格。"""
        pmap = {"600000": {"代码": "600000", "名称": "浦发", "持有数量": 1000,
                           "单位成本": 10.0, "持有金额": 8500, "仓位占比": 0.25}}
        out = fcr.revalue_positions(
            day="2026-08-07", ff_map={}, mfe_map={}, pmap=pmap,
            qmap={"600000": {"code": "600000", "price": 8.5, "change_pct": -3.2,
                             "date": "2026-08-07"}},
            regime="空头", sectors=[],
            tmap={"600000": {"code": "600000", "latest_date": "2026-08-07",
                             "trend_state": "下跌"}},
            total_assets=34000.0)
        assert len(out) == 1
        r = out[0]
        assert r["close"] == 8.5
        assert r["pnl_pct"] == pytest.approx(-0.15), "(8.5/10 - 1) = -15%"

    def test_revalue_positions_without_quote_keeps_none(self):
        """⚠️ 没有当日行情时 `close` 必须留 None —— 不得回落到成本价，
        那会让一只没行情的票在报告里显示成「盈亏 0%」。"""
        pmap = {"600000": {"代码": "600000", "名称": "浦发", "持有数量": 1000,
                           "单位成本": 10.0}}
        out = fcr.revalue_positions(day="2026-08-07", ff_map={}, mfe_map={}, pmap=pmap,
                                    qmap={}, regime="中性", sectors=[], tmap={},
                                    total_assets=None)
        assert out[0]["close"] is None

    def test_render_helpers_append_to_lines(self):
        """render_* 沿用本文件既有约定 `render_news(lines, ...)`：**就地追加**。"""
        lines = []
        fcr.render_themes(lines, {"theme_lifecycles": [
            {"theme_name": "半导体", "phase": "退潮", "technical_stage": "退潮/下跌",
             "score": 45, "event_evidence_count": 2}]})
        text = "\n".join(lines)
        assert "## 4." in text and "半导体" in text and "退潮" in text

    def test_render_next_day_returns_plan(self):
        """§6 要把 `next_plan` 交回 main —— 落盘 payload 还要用它。"""
        lines = []
        plan = fcr.render_next_day(lines, {"next_day_plan": {
            "total_position_range": "0%-20%", "holding_plans": []}})
        assert plan["total_position_range"] == "0%-20%"
        assert any("## 6." in x for x in lines)

    def test_render_discipline_returns_rules(self):
        lines = []
        rules = fcr.render_discipline(lines, {"rule_review": {
            "effective": ["e1"], "failed": [], "pending": ["p1"]}}, {})
        assert rules["effective"] == ["e1"]
        assert any("### 7.2" in x for x in lines)
