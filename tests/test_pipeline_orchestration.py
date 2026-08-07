"""管线**编排逻辑**测试：stage 顺序、失败传播、session 分支、门控码。

## 为什么必须有这一层

2026-08-07 实测覆盖率：

    run_0850   96.7%    run_1700   78.4%    run_0905   51.8%
    run_1445  **22.2%**  run_1800  **21.4%**  daily_pipeline **23.3%**（172 语句缺 132）

而**唯一**跑 runner 的 `test_runners_smoke.py` **只在非交易日跑** ——
runner 在日历门后立刻退出，所以它测的是那 5 行门检查，交易日一律 skip。
⇒ **编排逻辑（哪个 stage 先跑、失败了怎么办、session 怎么分支）从未被执行过。**

这类 bug 的代价高且静默。最具体的例子写在 `run_1700` 自己的注释里：

    sync_compass_amv **必须在** merge_incremental_market 之前 ——
    merge 据 amv_0day/confirmed 观测自动把 amv_0.quality 置 confirmed，
    随后 daily_pipeline 的 amv_state 才能据真值切换 regime

顺序反了不会报错，只会让 **regime 永远不切换** —— 而 0AMV regime 决定全链方向。

## 怎么测

所有 runner 都用模块级 `run_stage_quiet as _stage`（`daily_pipeline` 用 `run_stage`），
是**单一可注入接缝**。打桩成 recorder 后跑 `main()`，就能断言顺序与分支，
且**不真的 spawn 任何子进程**。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "07_tools"
sys.path.insert(0, str(TOOLS))


class Recorder:
    """替代 `_stage` / `run_stage`：记录调用顺序，按 `fail` 指定哪些 stage 失败。"""

    def __init__(self, fail: set[str] | None = None, gate_code: int = 0):
        self.calls: list[tuple[str, list[str]]] = []
        self.fail = fail or set()
        self.gate_code = gate_code

    def __call__(self, cmd, name, *a, **kw):
        self.calls.append((name, list(cmd)))
        ok = name not in self.fail
        if name == "runtime_gate":
            # ⚠️ 键名必须是 `returncode` ——`daily_pipeline` 读的是
            # `gate_stage["returncode"]`，且只在 `not gate_stage["ok"]` 时才传播。
            # 第一版用了 `code` 并让 ok=True ⇒ 三条门控测试全挂，
            # 而挂的原因是**桩不真**，不是被测代码有问题。
            return self._shape(name, self.gate_code == 0, self.gate_code)
        return self._shape(name, ok, 0 if ok else 1)

    @staticmethod
    def _shape(name, ok, rc):
        """⚠️ 键要**齐全**：`run_stage_quiet` 返回 out/stdout/stderr/returncode，
        而 `run_1700` 读的是 `r["stdout"]`。第一版只给了 `out` ⇒ KeyError
        让 run_1700 在 `sync_compass_amv` 之后中止，于是「merge 在 sync 之后」
        这条断言**永远走 skip 分支** —— 桩不真会把测试悄悄变成空转。"""
        return {"stage": name, "ok": ok, "out": "", "stdout": "", "stderr": "",
                "returncode": rc}

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def index(self, name: str) -> int:
        return self.names.index(name)


@pytest.fixture()
def pipeline(monkeypatch, tmp_path):
    import daily_pipeline as dp

    # ⚠️ **必须 patch 全部路径常量**。第一版漏了 `PLAN_DIR` / `SUPPORT_DIR`，
    # 测试在**真实仓库**里建出了 `03_daily_plans/_supporting/2026-08-07/`
    # （空目录、且被 gitignore，所以没污染 git —— 但这正是今天
    # `2026-07-16/` 那次事故的同一形态：脚本往仓库里写东西）。
    monkeypatch.setattr(dp, "BASE", tmp_path, raising=False)
    for attr in ("DATA_DIR", "MARKET_DIR", "HOLD_DIR", "PLAN_DIR",
                 "SUPPORT_DIR", "LOG_DIR"):
        assert hasattr(dp, attr), f"daily_pipeline 少了路径常量 {attr}（改名了？）"
        d = tmp_path / attr.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(dp, attr, d)
    return dp


def _run_pipeline(dp, monkeypatch, rec, session="premarket", extra=()):
    monkeypatch.setattr(dp, "run_stage", rec)
    monkeypatch.setattr(sys, "argv",
                        ["x", "--date", "2026-08-07", "--session-type", session, *extra])
    try:
        dp.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    return 0


class TestDecisionChainOrder:
    """⚠️ 决策链的顺序是**语义要求**，不是巧合。

    每一步都消费上一步的产物（契约里 24 个产物的生产者顺序就是这条链）：
    技术面 → B1 状态 → 持仓复盘 → 风控 → 总控。
    顺序反了不会报错 —— 下游读到的是**上一交易日**的文件。
    """

    def test_holding_to_decision_order(self, pipeline, monkeypatch):
        rec = Recorder()
        _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        chain = ["batch_holding_technical", "b1_holding_state",
                 "portfolio_review_report", "generate_risk_and_sectors",
                 "chief_decision_report"]
        present = [c for c in chain if c in rec.names]
        idx = [rec.index(c) for c in present]
        assert idx == sorted(idx), f"决策链顺序错乱：{present} → {idx}"

    def test_collector_precedes_amv_state(self, pipeline, monkeypatch):
        """`amv_state` 读 `market_timing_input.json` ⇒ collector 必须先跑。"""
        rec = Recorder()
        _run_pipeline(pipeline, monkeypatch, rec, "premarket")
        if "market_timing_collector" in rec.names and "amv_state" in rec.names:
            assert rec.index("market_timing_collector") < rec.index("amv_state")

    def test_gate_runs_before_reports(self, pipeline, monkeypatch):
        """门控要在报告之前 —— 报告要引用门控结论。"""
        rec = Recorder()
        _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        if "runtime_gate" in rec.names and "chief_decision_report" in rec.names:
            assert rec.index("runtime_gate") < rec.index("chief_decision_report")


class TestSessionBranching:
    """premarket 与 postclose 跑的 stage 集合不同 —— 分支错了会漏跑或多跑。"""

    def test_postclose_only_stages(self, pipeline, monkeypatch):
        pre, post = Recorder(), Recorder()
        _run_pipeline(pipeline, monkeypatch, pre, "premarket")
        _run_pipeline(pipeline, monkeypatch, post, "postclose")
        only_post = set(post.names) - set(pre.names)
        assert "postclose_news_digest" in only_post, \
            f"盘后新闻摘要应只在 postclose 跑，实际 postclose 独有={only_post}"

    def test_premarket_snapshot_stage_is_declared(self):
        """⚠️ premarket 会把 chief_decision **快照**一份
        （`{date}_premarket_chief_decision.json`）—— 17:00 复盘要拿盘前计划与
        实际执行对照，没有快照就无法复盘「计划外交易」。

        它是 `shutil.copy2` + 直接 append 一条 dict，**不走 `run_stage`**，
        所以 Recorder 记不到 ⇒ 查源码。
        （第一版为了「让它过」写成 `assert ... or True` —— 那种永真断言
        比没有测试更糟：看着有覆盖，实际什么都没验。）
        """
        src = (TOOLS / "daily_pipeline.py").read_text(encoding="utf-8")
        assert 'session_type == "premarket"' in src
        assert "premarket_chief_decision" in src
        assert "snapshot_premarket_chief_decision" in src

    def test_both_sessions_run_decision_chain(self, pipeline, monkeypatch):
        """两个 session 都要跑决策链 —— 09:05 也产出当日 risk_decision
        （依据是 T-1 收盘，见 v0.35）。"""
        for session in ("premarket", "postclose"):
            rec = Recorder()
            _run_pipeline(pipeline, monkeypatch, rec, session)
            assert "generate_risk_and_sectors" in rec.names, session
            assert "chief_decision_report" in rec.names, session


class TestFailurePropagation:
    """⚠️ `required=True` 的 stage 失败必须让整条链失败；`required=False` 不得。

    区分错的代价：把硬失败当 best-effort ⇒ 报告缺内容却报成功；
    反之 ⇒ 一个可选数据源抖动就毁掉整份报告。
    """

    def test_optional_stage_failure_does_not_abort(self, pipeline, monkeypatch):
        rec = Recorder(fail={"rss_collector", "overseas_market_collector"})
        _run_pipeline(pipeline, monkeypatch, rec, "premarket")
        assert "chief_decision_report" in rec.names, \
            "可选采集失败不应中断决策链"

    def test_required_stage_failure_is_visible(self, pipeline, monkeypatch):
        """硬失败 stage 挂掉时，`run_stage` 的 required 语义由 pipeline_kit 处理，
        这里只断言它**确实被标成 required** —— 即调用时没传 `required=False`。"""
        rec = Recorder()
        _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        hard = {"batch_holding_technical", "b1_holding_state", "generate_risk_and_sectors",
                "chief_decision_report", "theme_tracker_report"}
        for name, _cmd in rec.calls:
            if name in hard:
                # required 默认 True；调用点若显式传 False 会出现在 kwargs 里，
                # Recorder 收不到 —— 所以改查源码更可靠，见下面的源码断言
                pass
        src = (TOOLS / "daily_pipeline.py").read_text(encoding="utf-8")
        import re
        for name in hard:
            m = re.search(rf'"{name}"\s*,\s*required=False', src)
            assert not m, f"{name} 是硬失败 stage，不得标 required=False"


class TestGateCodePropagation:
    """⚠️ 门控退出码必须穿透 `daily_pipeline`（cron 直接按码判定），
    且**只放行 3/4/5** —— 别的码不能被当成门控结论。"""

    @pytest.mark.parametrize("code", [3, 4, 5])
    def test_gate_codes_propagate(self, pipeline, monkeypatch, code):
        rec = Recorder(gate_code=code)
        rc = _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        assert rc == code, f"门控码 {code} 应穿透，实际 exit={rc}"

    @pytest.mark.parametrize("code", [1, 2, 6, 127])
    def test_non_gate_codes_not_propagated_as_gate(self, pipeline, monkeypatch, code):
        """非门控码不得冒充门控结论 —— `propagate_gate_code` 只放行 3/4/5。"""
        rec = Recorder(gate_code=code)
        rc = _run_pipeline(pipeline, monkeypatch, rec, "postclose")
        assert rc not in {3, 4, 5} or rc == 0, f"code={code} 被当成门控码 {rc}"


# ══════════════════════════════════════════════════════════════════════════
# runner 级编排（run_1445 / run_1700 / run_1800 覆盖率原为 22% / 78% / 21%）
# 所有 runner 都用模块级 `run_stage_quiet as _stage` ⇒ 单一接缝
# ══════════════════════════════════════════════════════════════════════════

def _run_runner(mod, monkeypatch, rec, tmp_path, argv=()):
    """跑 runner 的 main()，绕过日历门、隔离所有路径、注入 stage recorder。"""
    monkeypatch.setattr(mod, "_stage", rec)
    # 绕过日历门：让它当交易日（返回 exit_code=None 表示继续）
    import pipeline_kit

    # ⚠️ 桩必须带 `cal` 字段 —— runner 会读 `_cg.cal`（日历检查的解析结果）。
    # 第一版只给了 exit_code/stages ⇒ AttributeError，4 条测试静默变成 skip
    # （因为 `_run_runner` 把异常吞成 "raised:..." 返回值）。
    # 教训：**skip 不是通过**，看到 skip 要先确认是「条件不满足」还是「桩不真」。
    gate = pipeline_kit.CalendarGate(
        cal={"is_trading_day": True, "date": "2026-08-07"}, exit_code=None)
    monkeypatch.setattr(mod, "calendar_gate", lambda *a, **kw: gate, raising=False)
    for attr in [a for a in dir(mod) if a.isupper() and "DIR" in a] + ["BASE"]:
        if hasattr(mod, attr) and isinstance(getattr(mod, attr), pathlib.Path):
            d = tmp_path / attr.lower()
            d.mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(mod, attr, d)
    monkeypatch.setattr(mod.os, "chdir", lambda *a: None, raising=False)
    monkeypatch.setattr(sys, "argv", ["x", *argv])
    try:
        mod.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    except Exception as e:                    # 编排之外的失败（缺数据）不该淹没顺序断言
        return f"raised:{type(e).__name__}"
    return 0


class TestRun1700Order:
    """⚠️ `run_1700` 里有一条**写在注释里的顺序要求**，正是本文件存在的理由：

        sync_compass_amv **必须在** merge_incremental_market 之前 ——
        merge 据 amv_0day/confirmed 观测把 amv_0.quality 置 confirmed，
        随后 daily_pipeline 的 amv_state 才能据真值切换 regime

    顺序反了**不会报错**，只会让 regime 永远不切换 —— 而 0AMV regime 决定全链方向。
    """

    def test_sync_compass_precedes_merge(self, monkeypatch, tmp_path):
        import run_1700

        rec = Recorder()
        _run_runner(run_1700, monkeypatch, rec, tmp_path)
        assert "sync_compass_amv" in rec.names, rec.names
        assert "merge_incremental_market" in rec.names, rec.names
        assert rec.index("sync_compass_amv") < rec.index("merge_incremental_market"), \
            "sync_compass_amv 必须先跑，否则 amv quality 永远不会被置 confirmed"

    def test_collect_precedes_mfe_and_review(self, monkeypatch, tmp_path):
        """行情采集要在 MFE/MAE 与复盘之前 —— 它们都读当日行情。"""
        import run_1700

        rec = Recorder()
        _run_runner(run_1700, monkeypatch, rec, tmp_path)
        # stage 名实测自 run_1700.main()：collect_holding_quotes /
        # collect_incremental_market / calc_mfe_mae / ledger_reconcile /
        # collect_fund_flow / refresh_eod_klines / refresh_market_indices /
        # sync_compass_amv / merge_incremental_market / daily_pipeline /
        # final_close_review / final_review_validator
        for later in ("calc_mfe_mae", "final_close_review"):
            if "collect_holding_quotes" in rec.names and later in rec.names:
                assert rec.index("collect_holding_quotes") < rec.index(later), later

    def test_daily_pipeline_precedes_final_review(self, monkeypatch, tmp_path):
        """`final_close_review` 的 8 个强制输入全由 daily_pipeline 产出。"""
        import run_1700

        rec = Recorder()
        _run_runner(run_1700, monkeypatch, rec, tmp_path)
        if "daily_pipeline" in rec.names and "final_close_review" in rec.names:
            assert rec.index("daily_pipeline") < rec.index("final_close_review")


class TestRun1445Order:
    def test_quotes_precede_review(self, monkeypatch, tmp_path):
        """⚠️ 14:45 的核心语义：**先取当日实时行情，再算尾盘动作**。

        顺序反了会让 `review_core` 拿不到当日行情 ⇒ 走「等待当日行情」分支，
        整份 14:45 报告失去意义（而不会报错）。
        """
        import run_1445

        rec = Recorder()
        _run_runner(run_1445, monkeypatch, rec, tmp_path)
        # ⚠️ run_1445 的 stage 名带后缀：`collect_holding_quotes intraday`、
        # 复盘那步叫 `close_review`（不是 review_core）—— 实测得来。
        assert "collect_holding_quotes intraday" in rec.names, rec.names
        assert "close_review" in rec.names, rec.names
        assert rec.index("collect_holding_quotes intraday") < rec.index("close_review")

    def test_gate_recorded_but_not_blocking(self, monkeypatch, tmp_path):
        """14:45 门控**只留痕不阻断**（0AMV 与宽度本就要等收盘）。"""
        import run_1445

        rec = Recorder(gate_code=4)     # blocked
        rc = _run_runner(run_1445, monkeypatch, rec, tmp_path)
        assert rc != 4, "14:45 不得因门控 blocked 而退出 4"


class TestRun1800Order:
    """18:00 选股链。⚠️ 门控在这里**只提示、不得影响选股结果** ——
    否则 live 候选无法与回测对照（v0.29）。"""

    def test_names_refresh_precedes_screening(self, monkeypatch, tmp_path):
        """股票名称表刷新要在选股之前 —— 它是 **ST 硬排除的唯一依据**。"""
        import run_1800

        rec = Recorder()
        _run_runner(run_1800, monkeypatch, rec, tmp_path)
        assert "refresh_stock_names" in rec.names, rec.names
        assert "screening_formula_screen" in rec.names, rec.names
        assert rec.index("refresh_stock_names") < rec.index("screening_formula_screen")

    def test_screening_chain_order(self, monkeypatch, tmp_path):
        """公式初筛 → 充实 → 打分 → 表格，每步消费上一步产物。"""
        import run_1800

        rec = Recorder()
        _run_runner(run_1800, monkeypatch, rec, tmp_path)
        # ⚠️ 名字带 `screening_` 前缀 —— 实测得来。第一版按无前缀写，
        # 条件不满足直接 skip，而 **skip 不是通过**。
        chain = ["screening_formula_screen", "screening_enrich_candidates",
                 "screening_score_candidates", "screening_candidate_table"]
        present = [c for c in chain if c in rec.names]
        assert len(present) == len(chain), f"选股链 stage 缺失：{rec.names}"
        idx = [rec.index(c) for c in present]
        assert idx == sorted(idx), f"选股链顺序错乱：{present} → {idx}"

    def test_gate_blocked_does_not_stop_screening(self, monkeypatch, tmp_path):
        """⚠️ v0.29：18:00 门控 blocked 时**选股照跑** ——
        门控若改写分层，live 候选就无法与回测对照。"""
        import run_1800

        rec = Recorder(gate_code=4)
        rc = _run_runner(run_1800, monkeypatch, rec, tmp_path)
        assert rc != 4, "18:00 不得因门控 blocked 而退出"


class TestRunnerNamesResolve:
    """⚠️ 回归（2026-08-07 发现）：**runner 里用到的模块级名字必须真的存在**。

    `run_1445.py` 从 **2026-08-06** 起就是坏的：那天的提交
    「收敛 07_tools 路径推导并修一处 TOOLS 误名」把 `TOOLS` 加进了**注释**
    却没加进**导入列表** ——

        -from paths import BASE, cn_today  # strategy_team/
        +from paths import BASE, cn_today  # strategy_team/, TOOLS

    于是 `main()` 在日历门后的**第一个 stage** 就 `NameError`，
    交易日完全产不出 14:45 报告。**整整一天没人发现**，因为：

      · `test_runners_smoke.py` **只在非交易日跑**（runner 在门后立刻退出）
      · `test_run_1445.py` 的 2 条测试**从不调 `main()`**
      · `--help` 冒烟也过 —— argparse 在 NameError 之前就 return 了

    ⇒ 这就是编排测试存在的理由：它是唯一会**执行 main() 主体**的一层。
    """

    @pytest.mark.parametrize("name", ["run_0850", "run_0905", "run_1445",
                                      "run_1700", "run_1800", "daily_pipeline"])
    def test_module_level_names_used_in_main_are_defined(self, name):
        """AST 扫 `main()` 里读取的全局名，逐个确认模块命名空间里有。"""
        import ast as _ast
        import builtins

        mod = __import__(name)
        src = (TOOLS / f"{name}.py").read_text(encoding="utf-8")
        tree = _ast.parse(src)
        fn = next((n for n in tree.body
                   if isinstance(n, _ast.FunctionDef) and n.name == "main"), None)
        assert fn is not None, f"{name} 没有 main()"
        local = set()
        for n in _ast.walk(fn):
            if isinstance(n, _ast.Name) and isinstance(n.ctx, (_ast.Store,)):
                local.add(n.id)
            elif isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                local.add(n.name)
                local |= {a.arg for a in n.args.args}
            elif isinstance(n, _ast.ExceptHandler) and n.name:
                local.add(n.name)
        known = set(dir(builtins)) | set(vars(mod)) | local | {a.arg for a in fn.args.args}
        missing = sorted({n.id for n in _ast.walk(fn)
                          if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Load)}
                         - known)
        assert not missing, (f"{name}.main() 用到未定义的名字：{missing}\n"
                             "很可能是重构时改了注释没改导入（run_1445 的 TOOLS 就是这样）")
