# -*- coding: utf-8 -*-
"""strategy_grid（因子 × 出场联合寻优驱动器，Phase E）钉测。

不跑真回测：subprocess.run 全部 mock——格子回测按 --out 落一份合成结果 JSON，
--dump-codes 宇宙探针落一份固定 codes 文件。

钉住：
  ① 网格展开 = 三轴笛卡尔积，且 expand_grid 与 _cells_for 收敛为一份实现；
  ② cell_signature 一致 ⇒ 复用跳过（不发起格子子进程）；
     窗口漂移（隔天新 K 线）/宇宙漂移（新票上市）⇒ 签名变 ⇒ 旧格不复用；
  ③ ranked JSON / 出场参数 → EXIT_RULES 块的输出 schema 稳定（键名钉住）；
  ④ --max-runs 预算截断生效并在报告中注明；
  ⑤ rc=0 但输出残缺 / 子进程超时 ⇒ 计 failed，不留全 None 行进榜单；
  ⑥ ret_over_dd 缺失 ⇒ objective=None 垫底（不是按 0 排在真实负值之上）。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from custos.research import strategy_grid as sg


# 合成结果 JSON：m2._load 认的键（trade_summary 含 expectancy/n + portfolio 块）。
# scorer=b1_dual 的格子期望R 更高 ⇒ 两阶段 top-1 必选 (b1_dual, *)。
def _fake_summary(cmd):
    scorer = cmd[cmd.index("--scorer") + 1]
    exp_r = 0.20 if scorer == "b1_dual" else 0.10
    return {
        "trade_summary": {
            "n": 100,
            "win_rate": 0.30,
            "expectancy": 0.006,
            "expectancy_R": exp_r,
            "payoff_ratio": 2.5,
        },
        "portfolio": {
            "total_return": 0.15,
            "max_drawdown": 0.10,
            "return_over_maxdd": 1.5,
        },
        "trades": [],
    }


class _R:
    returncode = 0
    stdout = ""


class _FakeRun:
    """mock subprocess.run：--out 落合成结果；--dump-codes 落固定 codes 文件。

    ``calls`` 只记真正的格子回测调用（含 --out）；宇宙探针记 ``probes``
    （探针不占 --max-runs 预算，也不该混进「子进程调用次数」断言）。
    改 ``codes`` 可模拟「新票上市 ⇒ 抽样宇宙漂移」。
    """

    def __init__(self):
        self.calls: list[list[str]] = []
        self.probes: list[list[str]] = []
        self.codes = ["600000.SH", "000001.SZ"]

    def __len__(self):
        return len(self.calls)

    def __call__(self, cmd, **kw):
        cmd = list(cmd)
        if "--dump-codes" in cmd:
            self.probes.append(cmd)
            p = cmd[cmd.index("--dump-codes") + 1]
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(self.codes) + "\n")
            return _R()
        self.calls.append(cmd)
        out = cmd[cmd.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(_fake_summary(cmd), f)
        return _R()


@pytest.fixture
def fake_run(monkeypatch):
    fr = _FakeRun()
    monkeypatch.setattr(sg.subprocess, "run", fr)
    return fr


@pytest.fixture(autouse=True)
def _no_data_calendar(monkeypatch):
    """测试机无通达信数据：窗口解析默认钉成「失败 ⇒ 不钉窗口」（降级分支）。

    需要验证窗口解析行为的用例自行 monkeypatch 覆盖 ``sg._resolve_data_dates``。
    """
    monkeypatch.setattr(sg, "_resolve_data_dates", lambda a: None)


def _argv(out_dir, *extra):
    return [
        "--scorers",
        "b1_dual,kdj_j",
        "--gates",
        "j_low,j_macd_turn",
        "--exit-grid",
        json.dumps(
            [
                {"name": "e0", "params": {}},
                {"name": "e1", "params": {"stop_mode": "pct", "stop_pct": 5}},
                {
                    "name": "e2",
                    "params": {"stop_mode": "pct", "stop_pct": 5, "trail_pct": 0.08},
                },
            ]
        ),
        "--out-dir",
        str(out_dir),
        "--tag",
        "t1",
        *extra,
    ]


def _payload(out_dir):
    return json.loads((out_dir / "_ranked__t1.json").read_text("utf-8"))


class TestGridExpansion:
    def test_cartesian_product(self):
        exits = [{"name": f"e{i}", "params": {}} for i in range(3)]
        cells = sg.expand_grid(["s1", "s2"], ["g1", "g2"], exits)
        assert len(cells) == 2 * 2 * 3
        got = {(c["scorer"], c["gate"], c["exit"]) for c in cells}
        assert got == {
            (s, g, e)
            for s in ("s1", "s2")
            for g in ("g1", "g2")
            for e in ("e0", "e1", "e2")
        }

    def test_expand_grid_delegates_to_cells_for(self):
        """展开逻辑只此一份：expand_grid == _cells_for(全组合)（防改一漏一）。"""
        exits = [
            {"name": "e0", "params": {}},
            {"name": "e1", "params": {"stop_pct": 5}},
        ]
        via_grid = sg.expand_grid(["s1", "s2"], ["g1", "g2"], exits)
        via_cells = sg._cells_for(sg._factor_combos(["s1", "s2"], ["g1", "g2"]), exits)
        assert via_grid == via_cells

    def test_default_grid_small_and_first_is_baseline(self):
        """默认网格必须小（两阶段第一阶段只跑出场轴第一档 = 基准档）。"""
        cells = sg.expand_grid(
            sg.DEFAULT_SCORERS, sg.DEFAULT_GATES, sg.DEFAULT_EXIT_GRID
        )
        assert len(cells) == len(sg.DEFAULT_SCORERS) * len(sg.DEFAULT_GATES) * len(
            sg.DEFAULT_EXIT_GRID
        )
        assert sg.DEFAULT_EXIT_GRID[0]["params"] == {}

    def test_validate_rejects_unknown_param(self):
        err = sg.validate_exit_grid([{"name": "x", "params": {"bogus": 1}}])
        assert err and "bogus" in err
        # cost_zone_grace 无 CLI 对应参数，非默认值必须报错
        err = sg.validate_exit_grid([{"name": "x", "params": {"cost_zone_grace": 2}}])
        assert err and "cost_zone_grace" in err
        assert sg.validate_exit_grid(sg.DEFAULT_EXIT_GRID) is None


class TestDefaultResearchBase:
    """v0.93（owner）：0AMV 做多区间 + J<13 是默认研究基底，钉死不作扫描变量。"""

    def test_amv_pin_in_every_cell_by_default(self):
        a = sg._build_parser().parse_args([])
        cell = {"scorer": "s", "gate": "g", "exit": "e", "params": {}}
        assert "--amv-long-only" in sg._cell_args(a, cell)

    def test_no_amv_pin_escape_hatch(self):
        """--no-amv-pin 仅对照实验用：显式解除基底钉。"""
        a = sg._build_parser().parse_args(["--no-amv-pin"])
        cell = {"scorer": "s", "gate": "g", "exit": "e", "params": {}}
        assert "--amv-long-only" not in sg._cell_args(a, cell)

    def test_amv_pin_part_of_signature(self):
        """基底钉进签名：解除钉 ⇒ 签名变 ⇒ 不会误复用异口径结果。"""
        c = {"scorer": "s", "gate": "g", "exit": "e", "params": {}}
        a1 = sg._build_parser().parse_args([])
        a2 = sg._build_parser().parse_args(["--no-amv-pin"])
        assert sg.cell_signature(sg._cell_args(a1, c)) != sg.cell_signature(
            sg._cell_args(a2, c)
        )

    def test_default_gates_all_j_low_based(self):
        """gate 轴默认只含 j_low（基底行）及「J<13 ∧ 其他因子」叠加变体。"""
        assert sg.DEFAULT_GATES[0] == "j_low"
        assert all(g.startswith("j_low") for g in sg.DEFAULT_GATES)


class TestSignatureReuse:
    def test_same_signature_skips_subprocess(self, tmp_path, fake_run):
        assert sg.main(_argv(tmp_path)) == 0
        n_first = len(fake_run)
        assert n_first > 0
        # 第二轮（不 --force）：所有格子签名一致 ⇒ 零格子子进程，全部复用
        assert sg.main(_argv(tmp_path)) == 0
        assert len(fake_run) == n_first
        payload = _payload(tmp_path)
        assert payload["budget"]["reused"] == len(payload["results"])

    def test_reuse_prints_skip(self, tmp_path, fake_run, capsys):
        """复用分支必须打 [SKIP]——静默复用没法从日志区分「跑了」与「跳了」。"""
        sg.main(_argv(tmp_path))
        capsys.readouterr()
        sg.main(_argv(tmp_path))
        assert "[SKIP]" in capsys.readouterr().out

    def test_force_reruns(self, tmp_path, fake_run):
        sg.main(_argv(tmp_path))
        n_first = len(fake_run)
        sg.main(_argv(tmp_path, "--force"))
        assert len(fake_run) > n_first

    def test_signature_changes_with_params(self):
        """CLI 参数（签名）必须随出场参数变化——否则异口径结果会被误复用。"""
        a = sg._build_parser().parse_args([])
        c1 = {"scorer": "s", "gate": "g", "exit": "e", "params": {"stop_pct": 5}}
        c2 = {"scorer": "s", "gate": "g", "exit": "e", "params": {"stop_pct": 8}}
        assert sg.cell_signature(sg._cell_args(a, c1)) != sg.cell_signature(
            sg._cell_args(a, c2)
        )

    def test_cell_out_path_sanitizes_names(self, tmp_path):
        """出场档名带路径/空白字符 ⇒ 文件名清洗；签名仍按原 CLI 参数算。"""
        cell = {"scorer": "s", "gate": "g", "exit": "a/b c\\d", "params": {}}
        p = sg.cell_out_path(tmp_path, cell, "sig123456789")
        assert p.name == "s__g__a_b_c_d__sig123456789.json"


class TestWindowAndUniversePinning:
    """隐式窗口/宇宙转显式：隔天数据漂移 ⇒ 签名变 ⇒ 旧格不被静默误复用。"""

    def test_implicit_window_resolved_into_cli_and_report(
        self, tmp_path, fake_run, monkeypatch
    ):
        monkeypatch.setattr(
            sg, "_resolve_data_dates", lambda a: ("2024-08-01", "2026-08-05")
        )
        assert sg.main(_argv(tmp_path, "--top-k", "99", "--max-runs", "2")) == 0
        w = _payload(tmp_path)["data_quality"]["window"]
        assert w == {"start": "2024-08-01", "end": "2026-08-05", "source": "resolved"}
        # 子进程 CLI 真的带上了显式窗口（隐式转显式，不只是签名里写写）
        assert fake_run.calls
        for cmd in fake_run.calls:
            assert cmd[cmd.index("--start") + 1] == "2024-08-01"
            assert cmd[cmd.index("--end") + 1] == "2026-08-05"

    def test_window_drift_changes_signature_no_reuse(
        self, tmp_path, fake_run, monkeypatch
    ):
        """同参数隔天：数据最后交易日前移 ⇒ 解析窗口变 ⇒ 签名变 ⇒ 全部重跑。"""
        monkeypatch.setattr(
            sg, "_resolve_data_dates", lambda a: ("2024-08-01", "2026-08-05")
        )
        assert sg.main(_argv(tmp_path)) == 0
        n_first = len(fake_run)
        assert n_first > 0
        # 同一天续跑：窗口不变 ⇒ 全部复用
        assert sg.main(_argv(tmp_path)) == 0
        assert len(fake_run) == n_first
        # 隔天：窗口漂移 ⇒ 签名不同 ⇒ 旧格不复用
        monkeypatch.setattr(
            sg, "_resolve_data_dates", lambda a: ("2024-08-02", "2026-08-06")
        )
        assert sg.main(_argv(tmp_path)) == 0
        assert len(fake_run) > n_first

    def test_explicit_window_skips_resolution(self, tmp_path, fake_run, monkeypatch):
        def _boom(a):
            raise AssertionError("显式 --start/--end 不应触发数据日历解析")

        monkeypatch.setattr(sg, "_resolve_data_dates", _boom)
        rc = sg.main(_argv(tmp_path, "--start", "2024-01-01", "--end", "2026-01-01"))
        assert rc == 0
        w = _payload(tmp_path)["data_quality"]["window"]
        assert w["source"] == "explicit"
        assert (w["start"], w["end"]) == ("2024-01-01", "2026-01-01")

    def test_universe_digest_in_signature_and_report(self, tmp_path, fake_run):
        assert sg.main(_argv(tmp_path)) == 0
        assert fake_run.probes, "未给 --codes-file 时应先跑 --dump-codes 探针"
        uni = _payload(tmp_path)["data_quality"]["universe"]
        assert uni["n_codes"] == 2
        assert uni["pinned"] is True
        expect = hashlib.sha1(",".join(fake_run.codes).encode("utf-8")).hexdigest()[:12]
        assert uni["digest"] == expect

    def test_universe_drift_changes_signature_no_reuse(self, tmp_path, fake_run):
        assert sg.main(_argv(tmp_path)) == 0
        n_first = len(fake_run)
        fake_run.codes = ["600000.SH", "000002.SZ"]  # 新票上市 ⇒ 抽样宇宙漂移
        assert sg.main(_argv(tmp_path)) == 0
        assert len(fake_run) > n_first

    def test_unpinned_window_warns_in_report(self, tmp_path, fake_run):
        """解析失败（无数据环境）降级 ⇒ 报告 data_quality 必须有警告（m2 口径）。"""
        assert sg.main(_argv(tmp_path)) == 0
        dq = _payload(tmp_path)["data_quality"]
        assert dq["window"]["source"] == "unpinned"
        assert any("窗口未钉死" in w for w in dq["warnings"])
        md = (tmp_path / "_report__t1.md").read_text("utf-8")
        assert "窗口未钉死" in md

    def test_date_and_end_both_given_warns(self, tmp_path, fake_run, capsys):
        """--date 与 --end 同给：--date 被忽略必须 WARN（不许静默）。"""
        rc = sg.main(_argv(tmp_path, "--date", "2026-01-01", "--end", "2026-06-01"))
        assert rc == 0
        err = capsys.readouterr().err
        assert "--date" in err and "忽略" in err
        assert _payload(tmp_path)["data_quality"]["window"]["end"] == "2026-06-01"


class TestFailureAccounting:
    """静默失效防护：rc=0 但输出残缺、子进程超时都必须计 failed（m2 教训）。"""

    def test_incomplete_output_counts_failed(self, tmp_path, monkeypatch):
        fr = _FakeRun()

        def _run(cmd, **kw):
            cmd = list(cmd)
            if "--dump-codes" in cmd:
                return fr(cmd, **kw)
            fr.calls.append(cmd)
            out = cmd[cmd.index("--out") + 1]
            with open(out, "w", encoding="utf-8") as f:
                # 残缺：trade_summary 里没有 n/expectancy（rc=0 但输出不可用）
                json.dump({"trade_summary": {}, "trades": []}, f)
            return _R()

        monkeypatch.setattr(sg.subprocess, "run", _run)
        assert sg.main(_argv(tmp_path, "--top-k", "99")) == 0
        payload = _payload(tmp_path)
        assert payload["results"] == [], "残缺输出不许留全 None 行进榜单"
        assert payload["budget"]["failed"] == 12
        assert len(payload["failed"]) == 12
        # 毒文件已删（否则下轮被「签名一致」误复用），只剩 ranked 报告
        assert sorted(p.name for p in tmp_path.glob("*.json")) == ["_ranked__t1.json"]

    def test_timeout_counts_failed(self, tmp_path, monkeypatch):
        fr = _FakeRun()

        def _run(cmd, **kw):
            cmd = list(cmd)
            if "--dump-codes" in cmd:
                return fr(cmd, **kw)
            fr.calls.append(cmd)
            raise sg.subprocess.TimeoutExpired(cmd, kw.get("timeout"))

        monkeypatch.setattr(sg.subprocess, "run", _run)
        rc = sg.main(
            _argv(tmp_path, "--top-k", "99", "--max-runs", "3", "--timeout", "5")
        )
        assert rc == 0
        payload = _payload(tmp_path)
        assert payload["budget"]["failed"] == 3
        assert payload["budget"]["ran"] == 0
        assert payload["budget"]["truncated"] == 12 - 3


class TestObjective:
    def test_missing_ret_over_dd_ranks_below_real_negative(self):
        """ret_over_dd 缺失（None）必须垫底——按 0 会排在真实负值之上。"""
        rows = [
            {
                "scorer": "s",
                "gate": "g",
                "exit": "neg",
                "margin": 0.1,
                "expectancy_R": 0.1,
                "ret_over_dd": -5.0,
            },
            {
                "scorer": "s",
                "gate": "g",
                "exit": "miss",
                "margin": 0.1,
                "expectancy_R": 0.1,
                "ret_over_dd": None,
            },
        ]
        ranked = sg.rank_rows(rows, (1.0, 1.0, 0.05))
        assert ranked[0]["exit"] == "neg"
        assert ranked[1]["exit"] == "miss"
        assert ranked[1]["objective"] is None

    def test_zero_ret_over_dd_kept(self):
        row = {"margin": 0.1, "expectancy_R": 0.1, "ret_over_dd": 0.0}
        assert sg.objective_of(row, (1.0, 1.0, 0.05)) == pytest.approx(0.2)


class TestOutputSchema:
    """ranked JSON 键名钉住——它是后续 campaign/回流脚本的解析面。"""

    TOP_KEYS = {
        "version",
        "tag",
        "r11_warning",
        "data_quality",
        "grid",
        "budget",
        "obj_weights",
        "results",
        "failed",
        "truncated",
    }
    DQ_KEYS = {"window", "universe", "warnings"}
    ROW_KEYS = {
        "rank",
        "scorer",
        "gate",
        "exit",
        "params",
        "n",
        "win_rate",
        "expectancy",
        "expectancy_R",
        "payoff_ratio",
        "breakeven_wr",
        "margin",
        "total_return",
        "max_drawdown",
        "ret_over_dd",
        "objective",
        "reused",
        "result_file",
        "exit_rules",
    }
    BUDGET_KEYS = {"max_runs", "top_k", "ran", "reused", "failed", "truncated"}

    def test_json_schema_pinned(self, tmp_path, fake_run):
        assert sg.main(_argv(tmp_path)) == 0
        payload = _payload(tmp_path)
        assert set(payload) == self.TOP_KEYS
        assert set(payload["data_quality"]) == self.DQ_KEYS
        assert set(payload["budget"]) == self.BUDGET_KEYS
        assert payload["version"] == "v2"
        assert payload["results"], "结果不能为空"
        for row in payload["results"]:
            assert set(row) == self.ROW_KEYS
        # 排名按 objective 降序
        objs = [r["objective"] for r in payload["results"]]
        assert objs == sorted(objs, reverse=True)
        assert [r["rank"] for r in payload["results"]] == list(range(1, len(objs) + 1))

    def test_exit_rules_block_aligns_with_live_schema(self, tmp_path, fake_run):
        """exit_rules 块的 rule_id 必须 ⊆ 真实 EXIT_RULES.json（回流通道）。"""
        from custos.core.paths import EXIT_RULES_FILE

        live = json.loads(EXIT_RULES_FILE.read_text(encoding="utf-8"))
        live_ids = set(live["stop_rules"]) | set(live["take_profit_rules"])
        sg.main(_argv(tmp_path))
        payload = _payload(tmp_path)
        for row in payload["results"]:
            er = row["exit_rules"]
            assert set(er) <= {"stop_rules", "take_profit_rules", "research_only"}
            for section in ("stop_rules", "take_profit_rules"):
                for rule_id, rule in er.get(section, {}).items():
                    assert rule_id in live_ids
                    assert set(rule) == {"rule_id", "enabled", "params"}

    def test_markdown_has_r11_warning_and_rank_table(self, tmp_path, fake_run):
        sg.main(_argv(tmp_path))
        md = (tmp_path / "_report__t1.md").read_text("utf-8")
        assert "R11" in md and "已实现口径为负" in md
        assert "不得引用绝对量级" in md
        assert "| 排名 | scorer | gate | 出场档 |" in md
        assert "配置明细" in md

    def test_markdown_notes_research_only_params(self, tmp_path, fake_run):
        """纯 research_only 的优胜配置（stop_mode/stop_pct）必须有「live 不生效」提示。"""
        sg.main(_argv(tmp_path))
        md = (tmp_path / "_report__t1.md").read_text("utf-8")
        assert "live 无法表达" in md
        assert "research_only" in md

    def test_margin_matches_m2_formula(self):
        """margin 口径钉死 = m2 的胜率 − 1/(1+payoff)。"""
        row = {"win": 0.4, "payoff": 1.5}
        assert sg._margin(row) == pytest.approx(0.4 - 1 / 2.5)
        assert sg._breakeven_wr(1.5) == pytest.approx(1 / 2.5)
        assert sg._breakeven_wr(0) is None


class TestBudgetAndTwoStage:
    def test_max_runs_truncates(self, tmp_path, fake_run):
        """2×2×3=12 格、预算 2 ⇒ 恰好 2 次格子子进程，其余截断并注明。"""
        rc = sg.main(_argv(tmp_path, "--top-k", "99", "--max-runs", "2"))
        assert rc == 0
        assert len(fake_run) == 2
        payload = _payload(tmp_path)
        assert payload["budget"]["ran"] == 2
        assert payload["budget"]["truncated"] == 12 - 2
        md = (tmp_path / "_report__t1.md").read_text("utf-8")
        assert "截断" in md

    def test_two_stage_topk(self, tmp_path, fake_run):
        """top-k=1：阶段一 4 格（4 因子组合 × 基准档），阶段二 top-1 × 3 档。

        阶段二里基准档签名一致复用 ⇒ 新子进程只有 2 个；kdj_j 的出场细化不跑。
        """
        rc = sg.main(_argv(tmp_path, "--top-k", "1", "--max-runs", "50"))
        assert rc == 0
        assert len(fake_run) == 4 + 2
        payload = _payload(tmp_path)
        rows = payload["results"]
        # 4（阶段一）+ 2（sc_a 的两个非基准出场档）= 6 行
        assert len(rows) == 6
        assert {r["exit"] for r in rows if r["scorer"] == "b1_dual"} == {
            "e0",
            "e1",
            "e2",
        }
        assert {r["exit"] for r in rows if r["scorer"] == "kdj_j"} == {"e0"}
        assert rows[0]["scorer"] == "b1_dual"  # 合成数据里 b1_dual 期望R 更高
