"""架构分层守卫 —— 用 AST 依赖图强制分层方向与无环。

2026-08-07 架构审查实测出的问题（本文件把结论钉住，防回归）：

  ① `factors/`（因子层，本该是最底层）依赖 `market_timing/technical_monitor`
     与 `research/backtest_factors` —— **底层依赖决策层**。
     后果：import 任一因子会拖进整个持仓状态机 + 1959 行回测器及其 40+ 依赖。
     成因：`technical_monitor` 552 行里**只有 7 个函数被模块外使用**，
     而这 7 个全是纯指标（`kdj`/`macd`/`ema`/`resample`/`bbi_state`/
     `zhixing_state`/`_infer_price_limit`），却定义在决策层模块里。
     已下移到 `indicators.py`（底层）。

  ② `factors/platform_pullback` 惰性 `import backtest_factors` 只为拿 `bt._kdj`，
     而 `bt._kdj` 就是 `technical_monitor.kdj`。构成
     `factors → screening → factors` 环。已改为直接用 `indicators.j_series`。

  ③ `news/postclose_news_digest` 从根层 `daily_report` 导入盘前情报访问器。
     已移到 `news/premarket_intel_schema`。
"""

from __future__ import annotations

import ast
import collections
import pathlib

import pytest
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "src" / "custos"

# ── 分层：数字越小越底层。同层互相依赖允许，下层依赖上层不允许。
# 2026-08-11 归堆（src/{core,datasource,pipeline,research}）后 rel 带组前缀：
# core 顶层文件名 ∈ BASE_MODULES 才是 L0；组内子目录用两级键查 LAYER_OF_DIR。
BASE_MODULES = {
    "paths.py",
    "code_utils.py",
    "indicators.py",
    "fmt.py",
    "net_retry.py",
    "pipeline_kit.py",
    "runtime_guards.py",
    # `contracts.py` 是产物 schema 的唯一来源，被 L1~L3 的生产者调用。
    # 它**只依赖 stdlib**（math + typing）—— 一条测试强制这一点，
    # 因为契约层若依赖别的模块，就可能被它校验的对象反向依赖。
    "contracts.py",
    # `b1_thresholds.py` 是 B1 反转 K 判定阈值的唯一来源，被 L2/L3 读。
    # 2026-08-07 建：同一组阈值原先散在 screening/market_timing/holdings
    # 三个 **L3** 目录里（彼此不能互相 import），只能上提到 L0。
    "b1_thresholds.py",
    # `report_audit.py` 是报告可审计块（待办 #29）的唯一实现，被 L3
    # （close_review/screening）与 L4（daily_report）的报告生成器共用。
    # 只依赖 `paths`（L0）与 stdlib。
    "report_audit.py",
}
LAYER_OF_DIR = {
    "datasource/local_tdx": 1,
    "datasource/collect": 1,
    "datasource/news": 1,
    "core/factors": 2,
    "core/trades": 2,
    "pipeline/screening": 3,
    "pipeline/market_timing": 3,
    "pipeline/close_review": 3,
    # 2026-08-07：`holdings/` 从 market_timing 拆出 —— 持仓状态与择时是不同的事，
    # 读者找「持仓状态机」不会想到去 market_timing 找。
    # `analysis/` 同日删除（两个文件各归其位后空了）。
    "pipeline/holdings": 3,
    # research/ 在生产链**之上**：回测要跑生产的因子与打分逻辑。
    # 2026-08-07 从 screening/ 拆出（研究代码占了那个目录的 70%）。
    "research": 4,
}
# datasource/ 顶层文件是**数据适配器**：性质是 L1，不是编排层。
# `s_data.py` 是 qlib/CSV 只读 loader（零内部依赖）；
# `trading_calendar.py` 是 TDX 交易日历维护（只依赖 L0）。
DATA_ADAPTERS = {"datasource/s_data.py", "datasource/trading_calendar.py"}
ROOT_LAYER = 4  # pipeline 顶层：runner 与编排


def _layer(rel: str) -> int:
    parts = rel.split("/")
    if parts[0] == "core" and len(parts) == 2 and parts[1] in BASE_MODULES:
        return 0
    if rel in DATA_ADAPTERS:
        return 1
    if parts[0] == "research":
        return LAYER_OF_DIR["research"]
    if parts[0] == "datasource" and len(parts) == 2:
        return 1  # datasource/__init__.py 等
    return LAYER_OF_DIR.get("/".join(parts[:2]), ROOT_LAYER)


def _build_graph():
    """只统计**顶层** import —— 函数内的惰性导入与 `__main__` 块不算真实依赖。

    ⚠️ 这个区分是必要的：`factors/platform_pullback` 的 `__main__` 演示块
    import `local_tdx_data` 拉数据，那不是模块依赖；而它此前在**函数体内**
    import `backtest_factors` 却是真依赖（每次调用都会执行）。
    所以判据是「是否在模块顶层或函数体内」，而不是「是否在文件里出现」。
    """
    files = sorted(p for p in TOOLS.rglob("*.py") if "__pycache__" not in str(p))
    mods = {}
    for p in files:
        # ⚠️ 必须 as_posix()：Windows 上 str(relative_to) 产反斜杠，
        # 与 import 名（点号）/ ALLOWED 白名单（正斜杠）/ _layer() 的
        # startswith('screening/') 全部对不上，多条检查会 vacuous 通过。
        rel = p.relative_to(TOOLS).as_posix()
        mods.setdefault(p.stem, rel)
        mods[rel[:-3].replace("/", ".")] = rel

    graph = collections.defaultdict(set)
    for p in files:
        rel = p.relative_to(TOOLS).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # 排除 `if __name__ == "__main__":` 块
        main_spans = [
            (n.lineno, n.end_lineno)
            for n in ast.walk(tree)
            if isinstance(n, ast.If)
            and ast.unparse(n.test).replace(" ", "")
            in ('__name__=="__main__"', "__name__=='__main__'")
        ]

        def in_main(node):
            return any(a <= node.lineno <= b for a, b in main_spans)

        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module:
                # `from custos.core import paths` 与 `from custos.core.paths import X` 都要算
                names = [n.module] + [n.module + "." + a.name for a in n.names]
            else:
                continue
            if in_main(n):
                continue
            for nm in names:
                nm = nm.removeprefix("custos.")  # 包式化后剥包名走既有映射
                for cand in (nm, nm.split(".")[0]):
                    if cand in mods and mods[cand] != rel:
                        graph[rel].add(mods[cand])
                        break
    return graph


GRAPH = _build_graph()


class TestLayerDirection:
    def test_no_downward_layer_violations(self):
        """下层不得依赖上层。"""
        bad = []
        for a, deps in GRAPH.items():
            la = _layer(a)
            for b in deps:
                lb = _layer(b)
                if lb > la:
                    bad.append(f"L{la} {a} → L{lb} {b}")
        assert not bad, "分层反转（下层依赖上层）：\n  " + "\n  ".join(sorted(bad))

    def test_factors_depend_only_on_base_and_data(self):
        """⚠️ `factors/` 是**因子实现层**（README：live 选股链与研究回测器共同依赖的下层）。

        它只许依赖基础层与数据层 —— 依赖 `screening/` 或 `market_timing/`
        会让 import 任一因子拖进整个回测器/状态机，且无法在轻量 live 路径里单独用。
        """
        bad = []
        for a, deps in GRAPH.items():
            if not a.startswith("core/factors/"):
                continue
            for b in deps:
                if not (b.startswith("core/factors/") or _layer(b) <= 1):
                    bad.append(f"{a} → {b}")
        assert not bad, "factors/ 越层依赖：\n  " + "\n  ".join(sorted(bad))

    def test_no_factor_imports_backtester(self):
        """单列一条：因子**绝不许** import 回测器（1959 行、连带 40+ 模块）。"""
        bad = [
            f"{a} → {b}"
            for a, deps in GRAPH.items()
            if a.startswith("core/factors/")
            for b in deps
            if "backtest_factors" in b
        ]
        assert not bad, "\n  ".join(bad)


class TestNoCycles:
    """允许的环：**同层**且其中一方是惰性导入（用于打破 import 期死锁）。

    不允许的：跨层环 —— 它意味着分层本身没立住。
    """

    ALLOWED = {
        (
            "datasource/local_tdx/adjust_factors.py",
            "datasource/local_tdx/local_tdx_data.py",
        ),
        (
            "pipeline/screening/enrich_candidates.py",
            "pipeline/screening/signal_labels.py",
        ),
    }

    def test_no_unexpected_cycles(self):
        seen, stack, found = set(), [], set()

        def dfs(u):
            if u in stack:
                found.add(tuple(sorted(set(stack[stack.index(u) :]))))
                return
            if u in seen:
                return
            seen.add(u)
            stack.append(u)
            for v in sorted(GRAPH.get(u, ())):
                dfs(v)
            stack.pop()

        for k in sorted(GRAPH):
            dfs(k)
        unexpected = {c for c in found if c not in self.ALLOWED}
        assert not unexpected, "新增循环依赖：\n  " + "\n  ".join(
            " ↔ ".join(c) for c in sorted(unexpected)
        )

    def test_allowed_cycles_are_same_layer(self):
        """白名单里的环必须是同层的 —— 跨层环不得进白名单。"""
        for cyc in self.ALLOWED:
            layers = {_layer(x) for x in cyc}
            assert len(layers) == 1, f"{cyc} 跨层，不得白名单化"


class TestIndicatorLayer:
    def test_indicators_is_the_indicator_home(self):
        """指标函数集中在 `indicators.py`，`technical_monitor` 不得再定义它们。"""
        moved = {
            "kdj",
            "macd",
            "ema",
            "resample",
            "bbi_state",
            "zhixing_state",
            "_infer_price_limit",
        }
        tm = ast.parse(
            (TOOLS / "pipeline" / "market_timing" / "technical_monitor.py").read_text(
                encoding="utf-8"
            )
        )
        redefined = {
            n.name
            for n in tm.body
            if isinstance(n, ast.FunctionDef) and n.name in moved
        }
        assert not redefined, (
            f"这些已下移到 indicators，不得在 technical_monitor 重新定义：{redefined}"
        )

        ind = ast.parse((TOOLS / "core" / "indicators.py").read_text(encoding="utf-8"))
        have = {n.name for n in ind.body if isinstance(n, ast.FunctionDef)}
        assert moved <= have, f"indicators 缺少：{moved - have}"

    def test_indicators_has_no_upward_dependency(self):
        """`indicators.py` 是底层，只许依赖 `code_utils`（取涨跌幅前缀基准）。"""
        assert GRAPH.get("core/indicators.py", set()) <= {"core/code_utils.py"}, (
            f"indicators 多出依赖：{GRAPH.get('core/indicators.py')}"
        )


class TestContractsLayer:
    def test_contracts_depends_on_nothing_internal(self):
        """⚠️ `contracts.py` 必须**零内部依赖**。

        它是产物 schema 的唯一来源，被 L1~L3 的生产者在落盘前调用。
        若它依赖别的项目模块，就可能出现「校验层依赖被校验对象」的环。
        """
        assert GRAPH.get("core/contracts.py", set()) == set(), (
            f"contracts.py 不得依赖项目内模块，实际: {GRAPH.get('core/contracts.py')}"
        )

    def test_money_path_producers_validate_before_write(self):
        """四个钱的路径产物的生产者必须在落盘前 `require(...)`。"""
        import re

        expect = {
            "core/runtime_guards.py": "runtime_gate",
            "pipeline/generate_risk_and_sectors.py": "risk_decision",
            "pipeline/market_timing/chief_decision_report.py": "chief_decision",
            "pipeline/holdings/b1_holding_state.py": "b1_holding_state",
            # 2026-08-07 第二批（按消费者数量排的优先级）
            "pipeline/market_timing/market_timing_collector.py": "market_timing_input",
            "pipeline/market_timing/merge_incremental_market.py": "market_timing_input",
            "pipeline/holdings/batch_holding_technical.py": "holding_technical_summary",
            # 第三批：硬失败链上其余产物
            "datasource/collect/collect_holding_quotes.py": "holding_quotes",
            "pipeline/market_timing/theme_tracker_report.py": "sector_technical_summary",
            "pipeline/close_review/execution_review.py": "execution_review",
            "pipeline/close_review/review_enrichment.py": "review_enrichment",
            # 第四批：硬失败链之外，铺完剩余
            "pipeline/screening/score_candidates.py": "stock_pool",
            "pipeline/close_review/final_close_review.py": "final_review",
            "pipeline/holdings/portfolio_review_report.py": "holding_review",
            "pipeline/close_review/calc_mfe_mae.py": "mfe_mae",
            "datasource/collect/collect_fund_flow.py": "fund_flow_rank",
            "pipeline/screening/formula_screen.py": "formula_hits",
            "pipeline/screening/enrich_candidates.py": "candidates_enriched",
            "datasource/news/rss_collector.py": "rss_evidence",
            "datasource/news/rss_filter.py": "rss_candidates",
            "datasource/news/postclose_news_digest.py": "postclose_news_digest",
            # 第五批：扫描发现的剩余产物
            "datasource/collect/collect_intraday_snapshot.py": "intraday_snapshot",
            "datasource/local_tdx/tq_sector.py": "tq_sector_map",
            "pipeline/holdings/holding_sector_mapper.py": "holding_sector_mapping",
        }
        for rel, artifact in expect.items():
            src = (TOOLS / rel).read_text(encoding="utf-8")
            # `\s*` 吃掉折行：ruff format 会把长调用在 `require(` 后换行
            assert re.search(rf"require\(\s*['\"]{artifact}['\"]", src), (
                f"{rel} 未在落盘前校验 {artifact}"
            )

    def test_sector_state_validated(self):
        import re

        src = (TOOLS / "pipeline" / "generate_risk_and_sectors.py").read_text(
            encoding="utf-8"
        )
        assert re.search(r"require\(\s*['\"]sector_state['\"]", src)  # \s*：防折行


class TestResearchProductionSplit:
    """⚠️ `research/` 与 `screening/` 的依赖方向：**只许研究 → 生产**。

    2026-08-07 拆分前实测反向为 0，所以能干净拆开。这条测试保住它 ——
    一旦生产链 import 了研究脚本，18:00 的每日选股就会依赖一个
    「探索性、可以失败、可以废弃」的模块。
    """

    def test_production_never_imports_research(self):
        bad = [
            f"{a} → {b}"
            for a, deps in GRAPH.items()
            if a.startswith("pipeline/screening/")
            for b in deps
            if b.startswith("research/")
        ]
        assert not bad, "生产选股链不得依赖研究脚本：\n  " + "\n  ".join(bad)

    def test_research_may_import_production(self):
        """反向是允许且实际存在的（`backtest_factors → enrich_candidates`）——
        这条测试确认拆分没把它切断（切断了说明研究脚本已经跑不起来）。"""
        edges = [
            f"{a} → {b}"
            for a, deps in GRAPH.items()
            if a.startswith("research/")
            for b in deps
            if b.startswith("pipeline/screening/")
        ]
        assert edges, "研究脚本应仍能依赖生产模块（回测要跑生产逻辑）"

    def test_screening_has_only_production_chain(self):
        """`screening/` 里只剩 18:00 生产链会用到的模块。"""
        got = {p.stem for p in (TOOLS / "pipeline" / "screening").glob("*.py")} - {
            "__init__"
        }
        assert got == {
            "formula_screen",
            "enrich_candidates",
            "score_candidates",
            "candidate_table",
            "manual_pools",
            "signal_labels",
            "financials",
        }, got


class TestContractCoverageOfArtifacts:
    """⚠️ 所有**按日期命名的 JSON 产物**都必须有契约，或在 `contracts.py` 里
    **写明为什么不纳入**。

    2026-08-07 铺完时的口径：24 个产物有契约，刻意不纳入的 5 类是
    run log / md 报告 / 人工输入 / 副本（`premarket_chief_decision`）/
    可选产物（`holding_sector_mapping_enriched`）—— 理由都写在 contracts.py 的
    「第五批」注释块里。

    这条测试防的是**新增产物时忘了建契约**：新产物一出现就会让它挂，
    迫使作者要么建契约、要么在那个注释块里写明理由。
    """

    # 刻意不纳入，理由见 contracts.py「第五批」注释块
    EXEMPT = {
        "daily_pipeline_log",
        "1445_review",  # 执行痕迹，非决策产物
        "collection_log",  # RSS 采集执行痕迹（rss_collector 每次运行落一份），非决策产物
        "run_log_check",  # run_log 例行核对的执行痕迹（run_log_check --json），非决策产物
        "manual_position_updates",  # 人工输入，形状由外部决定
        "premarket_chief_decision",  # chief_decision 的 copy2 副本
        "holding_sector_mapping_enriched",  # 可选产物，缺失是设计好的路径
    }

    def test_every_dated_artifact_has_contract_or_exemption(self):
        import re
        import sys
        from custos.core import contracts

        found = set()
        for p in TOOLS.rglob("*.py"):
            for m in re.finditer(
                # 引号无关：此前只认双引号，单引号写法会漏扫（ruff format
                # 统一成双引号后 collection_log 才冒出来 —— 它一直都在）
                r"f?[\"\']\{?[a-z_.]*date[a-z_]*\}?_([a-z0-9_]+)\.json[\"\']",
                p.read_text(encoding="utf-8"),
            ):
                found.add(m.group(1))
        missing = sorted(found - set(contracts.SPECS) - self.EXEMPT)
        assert not missing, (
            f"这些按日期命名的产物既没有契约、也没登记豁免：{missing}\n"
            "要么在 contracts.SPECS 里建契约，"
            "要么在 contracts.py「第五批」注释块 + 本测试的 EXEMPT 里写明理由"
        )


class TestNoStaleScriptPaths:
    """⚠️ 所有以 **Path 构造 / 字符串**形式引用的 `.py` 路径都必须真实存在。

    2026-08-07 实际漏过一次：把研究脚本从 `screening/` 移到 `research/` 时，
    替换脚本只匹配字符串路径 `"src/custos/pipeline/screening/x.py"`，漏了
    `TOOLS / "screening" / "launch_point_study.py"` 这种 **Path 构造形式**。
    `--help` 子进程冒烟也抓不到 —— 那个路径只在真正 spawn 子进程时才用到。

    ⇒ 这类「跨文件的脚本路径」必须有可执行检查，靠 grep 和冒烟都不够。
    """

    def test_all_referenced_script_paths_exist(self):
        import re

        # 归堆后（src/{core,datasource,pipeline,research}）按「目录名+文件名」对判存在：
        # `MARKET_TIMING / "x.py"` 的末两段是 stage 目录名 + 文件名。
        existing = {(p.parent.name, p.name) for p in TOOLS.rglob("*.py")}
        bad = []
        for p in sorted(TOOLS.rglob("*.py")):
            src = p.read_text(encoding="utf-8")
            # ① Path 构造形式：X / "dir" / "name.py"（允许中间多层）
            for m in re.finditer(r'/\s*"([a-z_0-9]+)"\s*/\s*"([a-z_0-9]+\.py)"', src):
                d, name = m.group(1), m.group(2)
                if (d, name) not in existing and not (ROOT / d / name).exists():
                    bad.append(f"{p.relative_to(TOOLS)}: {d}/{name}")
            # ② 字符串路径形式：src/.../name.py（任意层数）
            for m in re.finditer(r"src/((?:[a-z_0-9]+/)+)([a-z_0-9]+\.py)", src):
                if not (ROOT / "src" / m.group(1) / m.group(2)).exists():
                    bad.append(f"{p.relative_to(TOOLS)}: src/{m.group(1)}{m.group(2)}")
        assert not bad, "引用了不存在的脚本路径（移动文件时漏改）：\n  " + "\n  ".join(
            sorted(set(bad))
        )


class TestNoDuplicateModuleNames:
    """⚠️ 同一个模块名不得出现在两个目录 —— 否则**扁平 import 会拿到哪一个不确定**
    （`src` 与各子目录都在 sys.path 上，取决于插入顺序）。

    2026-08-07 实际踩到，而且是自己的搬迁脚本造成的：脚本先按**原路径**算出
    「哪些文件需要更新引用」，再 `git mv`，最后**按原路径写回** ——
    于是那些「既被移动、又需要更新引用」的文件在**旧位置被重建**，
    同时新位置留着一份引用未更新的。4 个文件同时存在两处。

    ⚠️ 已有的 `TestNoStaleScriptPaths` **抓不到这个** —— 两个路径都存在，
    所有引用都能解析。所以需要这条独立检查。

    教训：搬迁脚本必须**先移动、再按新路径重算引用**，
    或者至少在写回前确认目标路径仍然是文件的当前位置。
    """

    def test_no_module_basename_in_two_dirs(self):
        import collections

        dup = collections.defaultdict(list)
        for p in TOOLS.rglob("*.py"):
            if "__pycache__" in str(p) or p.name in {"__init__.py", "__main__.py"}:
                continue
            dup[p.name].append(str(p.relative_to(TOOLS)))
        bad = {k: v for k, v in dup.items() if len(v) > 1}
        assert not bad, (
            "同名模块出现在多个目录（扁平 import 会拿到哪一个不确定）：\n  "
            + "\n  ".join(f"{k}: {v}" for k, v in sorted(bad.items()))
        )
