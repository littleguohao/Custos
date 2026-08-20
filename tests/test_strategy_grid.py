# -*- coding: utf-8 -*-
"""strategy_grid（因子 × 出场联合寻优驱动器，Phase E）钉测。

不跑真回测：subprocess.run 全部 mock 成「按 --out 落一份合成结果 JSON」。

钉住四件事：
  ① 网格展开 = 三轴笛卡尔积（组合数相乘）；
  ② cell_signature 一致 ⇒ 复用跳过（不发起子进程）；
  ③ ranked JSON / 出场参数 → EXIT_RULES 块的输出 schema 稳定（键名钉住）；
  ④ --max-runs 预算截断生效并在报告中注明。
"""

from __future__ import annotations

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


@pytest.fixture
def fake_run(monkeypatch):
    """mock subprocess.run：解析 --out 落合成结果，返回调用次数计数器。"""
    calls: list[list[str]] = []

    class _R:
        returncode = 0
        stdout = ""

    def _run(cmd, **kw):
        calls.append(list(cmd))
        out = cmd[cmd.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(_fake_summary(cmd), f)
        return _R()

    monkeypatch.setattr(sg.subprocess, "run", _run)
    return calls


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


class TestSignatureReuse:
    def test_same_signature_skips_subprocess(self, tmp_path, fake_run):
        assert sg.main(_argv(tmp_path)) == 0
        n_first = len(fake_run)
        assert n_first > 0
        # 第二轮（不 --force）：所有格子签名一致 ⇒ 零子进程，全部复用
        assert sg.main(_argv(tmp_path)) == 0
        assert len(fake_run) == n_first
        payload = json.loads((tmp_path / "_ranked__t1.json").read_text("utf-8"))
        assert payload["budget"]["reused"] == len(payload["results"])

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


class TestOutputSchema:
    """ranked JSON 键名钉住——它是后续 campaign/回流脚本的解析面。"""

    TOP_KEYS = {
        "version",
        "tag",
        "r11_warning",
        "grid",
        "budget",
        "obj_weights",
        "results",
        "failed",
        "truncated",
    }
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
        payload = json.loads((tmp_path / "_ranked__t1.json").read_text("utf-8"))
        assert set(payload) == self.TOP_KEYS
        assert set(payload["budget"]) == self.BUDGET_KEYS
        assert payload["version"] == "v1"
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
        payload = json.loads((tmp_path / "_ranked__t1.json").read_text("utf-8"))
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

    def test_margin_matches_m2_formula(self):
        """margin 口径钉死 = m2 的胜率 − 1/(1+payoff)。"""
        row = {"win": 0.4, "payoff": 1.5}
        assert sg._margin(row) == pytest.approx(0.4 - 1 / 2.5)
        assert sg._breakeven_wr(1.5) == pytest.approx(1 / 2.5)
        assert sg._breakeven_wr(0) is None


class TestBudgetAndTwoStage:
    def test_max_runs_truncates(self, tmp_path, fake_run):
        """2×2×3=12 格、预算 2 ⇒ 恰好 2 次子进程，其余截断并注明。"""
        rc = sg.main(_argv(tmp_path, "--top-k", "99", "--max-runs", "2"))
        assert rc == 0
        assert len(fake_run) == 2
        payload = json.loads((tmp_path / "_ranked__t1.json").read_text("utf-8"))
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
        payload = json.loads((tmp_path / "_ranked__t1.json").read_text("utf-8"))
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
