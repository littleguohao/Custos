"""`00_governance/contracts/` 的契约一致性。

⚠️ **契约的第一属性是「真的被遵守」。** 契约说 X 而代码做 Y，契约就不是契约而是谎言 ——
而读它的人不会知道。2026-08-06 逐字段核查查出 7 处失真，最严重的一条是
**`RiskDecision.cooldown_list` 声明了一个从未实现的风控机制**：
读契约的人会以为「触发止损的票会自动进冷却、不会被重复买入」，而那个机制不存在。

这份测试的核心是**双向约束**：
① 契约里未标注的字段，必须在代码里真实出现（否则是空头承诺）；
② 已知无生产者的实体/字段，必须显式标 🔴（否则下游会以为可以依赖）。
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "00_governance" / "contracts"
DFC = CONTRACTS / "DATA_FLOW_CONTRACT.md"
MW = CONTRACTS / "MASTER_WORKFLOW.md"
INDEX = CONTRACTS / "README.md"

# 已删除的实体（2026-08-06）：无生产者，且独有内容已抢救 ⇒ 不许再出现在契约里
DELETED_ENTITIES = {"SkillEvidence", "BuyPlan"}


def _all_code() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                     for p in (ROOT / "07_tools").rglob("*.py"))


def _entities() -> dict[str, str]:
    t = DFC.read_text(encoding="utf-8")
    return {b.split("\n")[0].strip(): b
            for b in re.split(r"^### ", t, flags=re.M)[1:]}


def _fields(block: str) -> set[str]:
    """只取**未被注释掉**的字段——`//` 开头的行是说明，不是契约。"""
    body = "\n".join(l for l in block.split("\n") if not l.strip().startswith("//"))
    return set(re.findall(r'"([a-z][a-z0-9_]{2,30})"\s*:', body))


class TestFieldsExistInCode:
    """契约声明的字段必须在代码里真实出现。

    这是**防空头承诺**的主检查。契约里写一个没人产的字段，比不写更糟 ——
    它让下游以为可以依赖。
    """

    @pytest.mark.parametrize("ent", sorted(_entities()))
    def test_entity_fields_are_produced(self, ent):
        code = _all_code()
        missing = sorted(f for f in _fields(_entities()[ent]) if f not in code)
        assert not missing, (
            f"{ent} 的这些字段在 07_tools/ 里零命中 —— 要么改契约（以代码为准），"
            f"要么显式标 🔴 未实现：{missing}")

    @pytest.mark.parametrize("ent", sorted(DELETED_ENTITIES))
    def test_deleted_entities_are_gone(self, ent):
        """无生产者的实体已删除，不许悄悄回来。

        `SkillEvidence`（Skill 架构遗留，且它描述的「统一证据信封」实际不存在）与
        `BuyPlan`（`buy_strategy` 代码已移除）已于 2026-08-06 删除。
        **删掉比标注更彻底** —— 契约里没有它，就不会有人以为它存在。
        独有内容（结论四档 / 买入方式五类 / 最大亏损比例）已抢救到
        `strategy/b1/03_execution_discipline.md`。
        """
        assert ent not in _entities(), f"{ent} 无生产者，不该作为实体存在"
        # 但删除记录要留着，否则下次有人会重新加回来
        assert ent in DFC.read_text(encoding="utf-8"), \
            f"{ent} 的删除记录应保留在实现状态表里"


class TestUnimplementedMechanismsFlagged:
    """被声明过但没实现的机制，必须显式标出来。"""

    def test_cooldown_field_removed_not_just_marked(self):
        """⚠️ 安全相关：冷却机制不存在，字段已从契约**删除**。

        删掉比标注更彻底 —— 读契约的人不会再以为「触发止损的票会自动进冷却、
        不会被重复买入」。**契约里没有它，就不会有人依赖它。**
        是否要真做冷却见 TODO #31。
        """
        code = _all_code()
        for kw in ("cooldown", "冷却", "blacklist", "banned"):
            assert kw not in code, (
                f"代码里出现了 {kw} —— 冷却机制若已实现，请更新契约与本测试")
        s = DFC.read_text(encoding="utf-8")
        # 字段本体不许出现在任何 JSON 示例里
        for l in s.splitlines():
            if l.strip().startswith('"cooldown_list"'):
                raise AssertionError("cooldown_list 字段应已删除，不是保留加注释")
        assert "从未实现" in s, "删除记录要说明原因，否则下次有人会加回来"

    def test_monthly_review_marked_unimplemented(self):
        code = _all_code()
        assert "month_review" not in code
        s = MW.read_text(encoding="utf-8")
        k = s.index("## 七、正式报告五：月度复盘")
        assert "🔴" in s[k:k + 300] and "未实现" in s[k:k + 300]


class TestWorkflowMatchesReality:
    """工作流文档描述的调度必须与实际 runner 对得上。"""

    def test_premarket_time_matches_runners(self):
        s = MW.read_text(encoding="utf-8")
        assert "08:50" in s and "09:05" in s, "盘前时间应写实际的 08:50/09:05"
        # 原先写的 08:30 没有任何 runner/cron 对应
        assert not re.search(r"交易日 08:30；", s), "08:30 与实际调度不符，已更正"

    def test_every_mentioned_runner_exists(self):
        s = MW.read_text(encoding="utf-8") + (CONTRACTS / "SCREENING_WORKFLOW.md").read_text(encoding="utf-8")
        for m in sorted({x for x in re.findall(r"run_\d{4}\.py", s)}):
            assert (ROOT / "07_tools" / m).exists(), f"文档提到 {m} 但文件不存在"

    def test_no_todo_buried_in_contract(self):
        """待办不许埋在契约文档里——找不到，也不会被跟踪。

        ⚠️ **只查标题行。** 第一版扫全文，被正文里那句「原先这里挂着 8 条
        『当前需要调整的旧设计』」的历史说明误判 —— 这是「检查器不区分**结构**与
        **谈论结构的文字**」的第 4 次同类错误（前三次：docstring 里的 urlopen、
        docstring 里的原文件名、markdown 链接的显示文字）。
        **凡是查文档结构，就用标题/AST 之类的结构化锚点，别用全文字符串。**
        """
        heads = [l for l in MW.read_text(encoding="utf-8").splitlines()
                 if l.startswith("## ")]
        assert not any("当前需要调整的旧设计" in h for h in heads), \
            "这一节原是 8 条待办，应移入 05_strategy_versions/TODO.md"
        assert any("已移出" in h or "已完成" in h for h in heads), \
            "该节标题应说明待办已移出"


class TestConfigsHavePathsConstants:
    """代码直接读的 JSON 必须走 paths.py 常量，不许模块自己拼路径。"""

    @pytest.mark.parametrize("name", [
        "CN_TRADING_CALENDAR.json",
        "SCREEN_FORMULA_REGISTRY.json",
        "RSS_SOURCE_REGISTRY.json",
        "RSS_FILTER_CONFIG.json",
        "RSSHUB_PRIVATE_ROUTE_CANDIDATES.json",
    ])
    def test_has_constant(self, name):
        pv = (ROOT / "07_tools" / "paths.py").read_text(encoding="utf-8")
        assert name in pv, f"{name} 未在 paths.py 定义常量"


class TestIndex:
    def test_exists_and_classifies(self):
        s = INDEX.read_text(encoding="utf-8")
        for sec in ("代码直接读的配置", "数据契约", "工作流文档"):
            assert sec in s, f"索引缺分类：{sec}"

    @pytest.mark.parametrize("f", sorted(p.name for p in CONTRACTS.iterdir()
                                        if p.name != "README.md"))
    def test_every_file_indexed(self, f):
        """防孤岛：不在索引里的契约没人知道谁在用它。"""
        assert f in INDEX.read_text(encoding="utf-8"), f"{f} 未列入 contracts/README.md"

    def test_records_the_audit(self):
        """核查结论要留在索引里，否则下次还要重查一遍。"""
        s = INDEX.read_text(encoding="utf-8")
        assert "已删除的两个实体" in s or "无生产者" in s
        assert "以代码为准" in s
