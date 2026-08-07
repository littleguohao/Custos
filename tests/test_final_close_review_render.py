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
for _p in ("07_tools", "07_tools/close_review"):
    sys.path.insert(0, str(ROOT / _p))

from close_review import final_close_review as fcr  # noqa: E402


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
