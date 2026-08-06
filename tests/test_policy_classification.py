# -*- coding: utf-8 -*-
"""政策分类口径回归测试（2026-08-03 裁定）。

口径：政策源默认算政策（不要求内容再命中「宏观政策」主题），但命中
policy_negative_keywords 且未命中「宏观政策」主题时剔除。

为什么不用"所有源都必须命中宏观政策主题"的收紧读法：原词表只有 10 个词，收紧会
误杀「国常会部署稳增长」「专精特新扶持意见」「证监会征求意见」等真政策。改为扩充
词表提全 + 负向词精确剔除。本文件把这个取舍钉住，防止日后有人"顺着直觉收紧"。
"""
from __future__ import annotations

import json

import pytest

from news import postclose_news_digest as pnd
from news import rss_filter  # noqa: F401  确保模块可导入
from paths import RSS_FILTER_CONFIG_FILE

CFG = json.loads(RSS_FILTER_CONFIG_FILE.read_text(encoding="utf-8-sig"))
MACRO_KW = CFG["theme_keywords"]["宏观政策"]
NEG_KW = CFG["policy_negative_keywords"]


def item(category="policy_official", themes=(), neg=(), mkt=()):
    return {"category": category, "matched_themes": list(themes),
            "matched_policy_negative": list(neg), "matched_market_keywords": list(mkt)}


def simulate(title, category):
    """模拟 rss_filter 的子串匹配（text = title.lower()）。"""
    t = title.lower()
    return {
        "category": category,
        "matched_themes": ["宏观政策"] if any(w.lower() in t for w in MACRO_KW) else [],
        "matched_policy_negative": [w for w in NEG_KW if w.lower() in t],
        "matched_market_keywords": [w for w in CFG["market_keywords"] if w.lower() in t],
    }


class TestPolicySourceDefaultsToPolicy:
    """① 政策源默认算政策——不得要求内容再命中宏观政策主题。"""

    def test_policy_source_without_macro_theme_still_policy(self):
        assert pnd.classify(item(category="policy_official")) == "政策"

    def test_policy_consultation_is_policy(self):
        assert pnd.classify(item(category="policy_consultation")) == "政策"

    @pytest.mark.parametrize("title", [
        "国常会部署进一步稳增长举措",
        "国务院印发专精特新中小企业扶持意见",
        "证监会就程序化交易管理规定征求意见",
        "发改委印发促消费行动方案",
        "财政部提前下达专项债额度",
    ])
    def test_real_policies_stay_in_policy_section(self, title):
        """这五条正是收紧口径会误杀的真政策。"""
        assert pnd.classify(simulate(title, "policy_official")) == "政策"


class TestNonPolicyOfficialNeedsMacroTheme:
    """② 非政策类官方源必须命中「宏观政策」主题。"""

    def test_macro_official_without_theme_is_not_policy(self):
        assert pnd.classify(item(category="macro_official")) != "政策"

    def test_macro_official_with_theme_is_policy(self):
        assert pnd.classify(item(category="macro_official", themes=["宏观政策"])) == "政策"

    def test_media_source_never_policy(self):
        assert pnd.classify(item(category="cn_financial_media",
                                 themes=["宏观政策"])) != "政策"


class TestNegativeKeywordsRemoveNoise:
    """③ 负向词剔除政策源发的非政策内容。"""

    @pytest.mark.parametrize("title", [
        "国务院任免国家工作人员",
        "中新社：某地举办文旅推介会",
        "李强会见外国企业家代表",
        "某部门召开座谈会",
        "领导慰问受灾群众",
    ])
    def test_noise_is_excluded(self, title):
        got = pnd.classify(simulate(title, "policy_official"))
        assert got != "政策", f"{title} 不该进政策节，实际 {got}"

    def test_negative_hit_alone_excludes(self):
        assert pnd.classify(item(neg=["任免"])) != "政策"


class TestPositiveEvidenceWins:
    """负向词只在没有正向证据时生效——否则会误杀实质政策。"""

    @pytest.mark.parametrize("title", [
        "中美经贸磋商双方会见，讨论关税安排",
        "央行行长会见国际投资者，重申货币政策取向",
    ])
    def test_macro_theme_overrides_negative_word(self, title):
        s = simulate(title, "policy_official")
        assert s["matched_policy_negative"], "样本应命中负向词"
        assert s["matched_themes"] == ["宏观政策"], "样本应命中宏观政策"
        assert pnd.classify(s) == "政策", "有正向证据时不得被负向词剔除"

    def test_explicit_conflict_case(self):
        assert pnd.classify(item(themes=["宏观政策"], neg=["会见"])) == "政策"


class TestOperatorPrecedenceIsExplicit:
    """原写法 `A or B and C` 靠优先级隐式分组，极易读反。"""

    def test_media_with_macro_theme_is_not_policy(self):
        """若被误读成 (A or B) and C，媒体源+宏观政策会变成政策。"""
        assert pnd.classify(item(category="cn_financial_media",
                                 themes=["宏观政策"])) != "政策"

    def test_policy_source_without_theme_is_policy(self):
        """若被误读成 (A or B) and C，政策源无主题会掉出政策节。"""
        assert pnd.classify(item(category="policy_official", themes=[])) == "政策"


class TestKeywordTableQuality:
    """词表本身的质量：足够特异，不误伤常见非政策标题。"""

    @pytest.mark.parametrize("title", [
        "公司发布年度业绩预告",
        "某地举办招聘会",
        "球队夺冠庆祝",
        "新片发布会人气爆棚",
        "个人职业规划讲座",
    ])
    def test_no_false_macro_match(self, title):
        hits = [w for w in MACRO_KW if w.lower() in title.lower()]
        assert hits == [], f"{title} 误命中宏观政策词: {hits}"

    def test_macro_table_expanded(self):
        """原 10 词覆盖不足是根因，扩充后应显著变大。"""
        assert len(MACRO_KW) >= 40
        for must in ("国常会", "稳增长", "专精特新", "征求意见", "PMI", "证监会"):
            assert must in MACRO_KW, f"缺少关键覆盖词 {must}"

    def test_negative_table_exists(self):
        for must in ("任免", "会见", "推介会", "文旅"):
            assert must in NEG_KW


class TestFilterEmitsNegativeField:
    """rss_filter 必须落痕 matched_policy_negative，否则 classify 拿不到判据。"""

    def test_config_key_present(self):
        assert isinstance(CFG.get("policy_negative_keywords"), list)

    def test_filter_source_matches_and_emits(self):
        import inspect
        src = inspect.getsource(rss_filter)
        assert "policy_negative_keywords" in src
        assert "matched_policy_negative" in src

    def test_missing_field_is_backward_compatible(self):
        """旧 filtered 产物没有该字段时，行为与改动前一致。"""
        legacy = {"category": "policy_official", "matched_themes": []}
        assert pnd.classify(legacy) == "政策"

    def test_is_policy_is_pure(self):
        """is_policy 不读文件、不依赖全局状态。"""
        assert pnd.is_policy({"category": "policy_official"}) is True
        assert pnd.is_policy({"category": "cn_financial_media"}) is False
