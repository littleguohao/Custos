"""`00_governance/strategy/` 的结构、注册表与一致性约束。

2026-08-06 重组：**一个策略 = 一个上下文目录**（`b1/` `cz/`），
非策略用 `_` 前缀（`_factors/` 可复用因子、`_shared/` 跨策略规则）。
动机是 owner 明确的扩展需求：「后续可能会引入更多的策略，有的是策略，
有的可能只是单个因子」。

这份测试守三件事：
① **注册表与目录必须一致** —— 新增策略不登记就会被拦住（扩展机制的强制点）；
② **文档写的阈值必须与代码常量一致** —— 不一致时文档就不是规则而是谎言，
   而读文档的人不会知道；
③ **三处已知问题必须留在索引里**直到真正解决 —— 这类问题最容易随时间被
   当作「已经这样了」而接受。
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "00_governance" / "strategy"
INDEX = STRATEGY / "README.md"
REGISTRY = STRATEGY / "STRATEGY_REGISTRY.json"

ROLES = {"primary", "secondary", "experimental"}
STATES = {"live", "advisory", "unwired", "partially_stale", "deprecated"}


def reg():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


class TestLayout:
    def test_root_holds_only_index_and_registry(self):
        """根目录只放索引与注册表——规则文档一律进上下文目录。"""
        files = sorted(p.name for p in STRATEGY.glob("*") if p.is_file())
        assert files == ["README.md", "STRATEGY_REGISTRY.json"], files

    def test_underscore_prefix_means_non_strategy(self):
        """`_` 前缀 = 非策略上下文，这样 ls 一眼能分开。"""
        dirs = sorted(p.name for p in STRATEGY.iterdir() if p.is_dir())
        strat_ids = {s["id"] for s in reg()["strategies"]}
        for d in dirs:
            if d.startswith("_"):
                assert d not in strat_ids, f"{d} 带 _ 前缀却登记成策略"
            else:
                assert d in strat_ids, f"{d} 是无前缀目录但未登记为策略"

    def test_doc_naming_convention(self):
        """规则文档 `NN_lower_snake.md`；已废把状态写进文件名。"""
        bad = []
        for s in reg()["strategies"]:
            for d in (STRATEGY / s["dir"]).glob("*.md"):
                if d.name == "README.md":
                    continue
                if not re.fullmatch(r"\d{2}_[a-z0-9_]+\.md", d.name):
                    bad.append(str(d.relative_to(STRATEGY)))
        assert not bad, f"不符合 NN_lower_snake.md 的文档：{bad}"

    def test_deprecated_is_visible_in_filename(self):
        """废弃状态写在文件名里，读目录就能看见。"""
        names = [p.name for p in (STRATEGY / "b1").glob("*.md")]
        assert any(n.startswith("99_deprecated_") for n in names), \
            "已废文档应命名为 99_deprecated_*"

    def test_configs_keep_upper_snake(self):
        """代码消费的配置保持 UPPER_SNAKE（与 contracts/ 一致，一眼看出代码在读它）。"""
        for s in reg()["strategies"]:
            for c in s["configs"]:
                name = pathlib.Path(c).name
                assert re.fullmatch(r"[A-Z0-9_]+\.json", name), f"{c} 应为 UPPER_SNAKE.json"


class TestRegistry:
    def test_exists_and_parses(self):
        assert REGISTRY.exists()
        r = reg()
        assert r["strategies"] and "conventions" in r

    def test_exactly_one_primary(self):
        """主策略当前只有 B1。变多说明架构变了，要有意识地改。"""
        prim = [s["id"] for s in reg()["strategies"] if s["role"] == "primary"]
        assert prim == ["b1"], f"primary 集合变了：{prim}"

    @pytest.mark.parametrize("kind", ["strategies", "factors", "shared_rules"])
    def test_declared_paths_exist(self, kind):
        """注册表里声明的每个路径都必须真实存在——否则注册表本身就是谎言。"""
        bad = []
        for item in reg()[kind]:
            for key in ("entry", "rule_doc", "doc"):
                v = item.get(key)
                if v and not (STRATEGY / v).exists():
                    bad.append(f"{item['id']}.{key}={v}")
            for key in ("docs", "configs"):
                for v in item.get(key, []):
                    if not (STRATEGY / v).exists():
                        bad.append(f"{item['id']}.{key}={v}")
            for v in item.get("code", []):
                if not (ROOT / v).exists():
                    bad.append(f"{item['id']}.code={v}")
        assert not bad, f"注册表里的路径不存在：{bad}"

    def test_every_doc_is_registered(self):
        """**防孤岛**：目录里的文档必须在注册表里登记。

        实例：重组前 `technical_trend.md`(183 行) 与 `03_execution_discipline.md`(105 行)
        代码/治理文档/README/contracts 全都没引用过 —— 288 行无人可达。
        """
        declared = set()
        r = reg()
        for s in r["strategies"]:
            declared |= set(s["docs"]) | set(s["configs"])
        for f in r["factors"]:
            declared.add(f["doc"])
        for x in r["shared_rules"]:
            declared.add(x["doc"])
        actual = {str(p.relative_to(STRATEGY)) for p in STRATEGY.rglob("*")
                  if p.is_file() and p.name not in ("README.md", "STRATEGY_REGISTRY.json")}
        assert actual <= declared, f"未登记的文件：{sorted(actual - declared)}"

    def test_roles_and_statuses_valid(self):
        r = reg()
        for s in r["strategies"]:
            assert s["role"] in ROLES, f"{s['id']} role={s['role']}"
            assert s["status"] in STATES, f"{s['id']} status={s['status']}"

    def test_experimental_never_live(self):
        """`role=experimental` 不得 `status=live` —— 实验中的策略不许进 live。"""
        for s in reg()["strategies"]:
            if s["role"] == "experimental":
                assert s["status"] != "live", f"{s['id']} 是实验策略却标 live"


class TestContextReadmes:
    @pytest.mark.parametrize("d", ["b1", "cz", "_factors", "_shared"])
    def test_each_dir_has_readme(self, d):
        assert (STRATEGY / d / "README.md").exists(), f"{d}/ 缺入口 README"

    @pytest.mark.parametrize("d", ["b1", "cz"])
    def test_strategy_readme_has_fixed_sections(self, d):
        """固定小节让新增策略有模板可照抄。"""
        s = (STRATEGY / d / "README.md").read_text(encoding="utf-8")
        for sec in ("## 定位", "## 代码依赖"):
            assert sec in s, f"{d}/README.md 缺 {sec}"


class TestDocHeaders:
    """每份规则文档必须有统一头部块，「执行者」是最重要的一栏——它决定改动代价。"""

    DOCS = sorted(p for p in STRATEGY.rglob("*.md") if p.name != "README.md")

    @pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(STRATEGY)))
    def test_header_present(self, doc):
        s = doc.read_text(encoding="utf-8")
        head = s[:1200]
        for field in ("**上下文**：", "**执行者**：", "**状态**：", "**代码依赖**："):
            assert field in head, f"{doc.name} 头部缺 {field}"

    @pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(STRATEGY)))
    def test_no_bom(self, doc):
        assert not doc.read_text(encoding="utf-8").startswith("\ufeff"), \
            f"{doc.name} 带 BOM"


class TestReversalKMatchesCode:
    """文档写的反转K阈值必须与代码常量**逐项一致**。

    这是本目录唯一「代码执行」的规则，也是唯一可机械验证的。
    文档与代码不符时**以代码为准**（代码是实际在跑的），并记为一次口径修正。
    """

    CONSTS = {
        "J_LOW_THRESHOLD": 13.0,
        "VOL_RATIO_MAX": 0.5,
        "VOL_PCTILE_MAX": 10.0,
        "REVERSAL_CHANGE_MIN_PCT": -2.0,
        "REVERSAL_CHANGE_MAX_PCT": 1.8,
        "REVERSAL_AMPLITUDE_PCT": 7.0,
    }

    def _code(self):
        return (ROOT / "07_tools" / "screening"
                / "enrich_candidates.py").read_text(encoding="utf-8")

    @pytest.mark.parametrize("name,want", sorted(CONSTS.items()))
    def test_constant_value(self, name, want):
        m = re.search(rf"^{name} *= *(-?[\d.]+)", self._code(), re.M)
        assert m, f"代码里找不到常量 {name}"
        assert float(m.group(1)) == want, \
            f"{name} 代码是 {m.group(1)}、索引记的是 {want} —— 两者必须一起改"

    def test_doc_states_asymmetric_range(self):
        """涨跌幅区间是**不对称**的 −2%~+1.8%。

        曾经写错成对称 ±2%（`REVERSAL_CHANGE_PCT = 2.0` 是旧值残留），
        所以文档必须明确「不对称」，避免被顺手改回。
        """
        doc = (STRATEGY / "b1" / "01_swing_rules.md").read_text(encoding="utf-8")
        assert "-2% 至 +1.8%" in doc or "−2% ~ +1.8%" in doc
        assert "不对称" in doc

    def test_stale_symmetric_constant_not_used_in_logic(self):
        code = self._code()
        assert "REVERSAL_CHANGE_PCT = 2.0" in code, "残留常量被删了？同步更新索引说明"
        assert "abs(change_pct) <= REVERSAL_CHANGE_PCT" not in code


class TestKnownIssuesStayVisible:
    """三处待处理问题必须留在索引里，直到被真正解决。

    尤其②止损层级需要 owner 拍板 —— 没拍之前不该消失。
    """

    def test_zero_impl_dependency_flagged(self):
        s = INDEX.read_text(encoding="utf-8")
        assert "04_pullback_rotation.md" in s and "零实现" in s
        # 也必须标在文档自己头部，读文档的人才看得到
        doc = (STRATEGY / "b1" / "04_pullback_rotation.md").read_text(encoding="utf-8")
        assert "零实现" in doc[:1200]

    def test_stop_hierarchy_flagged(self):
        s = INDEX.read_text(encoding="utf-8")
        assert "层级" in s and "20%" in s and "8%" in s
        assert "owner 拍板" in s
        cz = (STRATEGY / "cz" / "01_cognition_framework.md").read_text(encoding="utf-8")
        assert "层级关系" in cz[:1500], "CZ 文档头部要写清它的止损不是 B1 的执行止损"

    def test_unreachable_docs_now_reachable(self):
        """曾经入口不可达的两份，现在必须能从索引/注册表到达。"""
        s = INDEX.read_text(encoding="utf-8")
        for name in ("technical_trend.md", "03_execution_discipline.md"):
            assert name in s, f"{name} 未在索引里"


class TestPathsConstants:
    """新目录必须在 paths.py 有常量——模块不得自己拼 strategy 子目录。"""

    def test_constants_exist(self):
        s = (ROOT / "07_tools" / "paths.py").read_text(encoding="utf-8")
        for c in ("B1_DIR", "CZ_DIR", "FACTORS_DIR", "STRATEGY_REGISTRY_FILE"):
            assert c in s, f"paths.py 缺 {c}"

    def test_cz_config_points_into_cz_dir(self):
        s = (ROOT / "07_tools" / "paths.py").read_text(encoding="utf-8")
        assert 'CZ_SECTOR_PREFERENCE_FILE = CZ_DIR / "CZ_SECTOR_PREFERENCE.json"' in s
