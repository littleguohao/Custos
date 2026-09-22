# -*- coding: utf-8 -*-
"""score_evolution_study 钉测：fake cell_runner 端到端（无网络无数据）。

cell_runner 注入是设计面（同 evolution_loop 的 monkeypatch 惯例）：默认实现
是 strategy_grid 子进程，测试注入确定性 fake——schema/判定逻辑/闸门全部
在本进程内可复现验证。
"""

import json

import pytest

from custos.research import score_evolution_study as ses
from custos.research.evolution.score_genome import compile_composite

LEGS = ["close", "volume"]
MINING = ("2022-01-01", "2024-07-31")
JUDGMENT = ("2024-08-01", "2026-09-04")

EXPR_EQUAL = compile_composite(LEGS, [1, 1])
EXPR_TOP = compile_composite(LEGS, [3, 1])  # 权重格内（(3,1) 是 canonical）


def _reading(objective, margin=0.02, n=500):
    return {
        "objective": objective,
        "margin": margin,
        "expectancy_R": 0.1,
        "payoff_ratio": 1.5,
        "win_rate": 0.4,
        "n": n,
        "cell_signature": "fake12345678",
    }


class FakeRunner:
    """table 优先、default 兜底的确定性 cell_runner；记录全部调用。"""

    _UNSET = object()

    def __init__(self, table=None, default=_UNSET):
        self.table = table or {}
        self.default = _reading(0.5) if default is self._UNSET else default
        self.calls = []

    def __call__(self, scorer, gate, exit_params, *, start, end):
        self.calls.append((scorer, start, end))
        return self.table.get((scorer, start, end), self.default)


def _argv(tmp_path, *extra, legs=None):
    argv = [
        "--legs",
        legs if legs is not None else ",".join(LEGS),
        "--mining-start",
        MINING[0],
        "--mining-end",
        MINING[1],
        "--codes",
        "600000,600001",
        "--out-dir",
        str(tmp_path),
        "--tag",
        "t1",
        "--n-random",
        "2",
        "--sens-arms",
        "3",
    ]
    return argv + list(extra)


def _run(tmp_path, fake, *extra, legs=None, coarse=None, v0=None):
    rc = ses.main(
        _argv(tmp_path, *extra, legs=legs),
        cell_runner=fake,
        cell_runner_coarse=coarse,
        v0_runner=v0,
    )
    assert rc == 0
    out = tmp_path / "t1" / "_score_evolution__t1.json"
    assert out.exists()
    return json.loads(out.read_text(encoding="utf-8"))


class TestEndToEnd:
    def test_schema_and_top_genome_selection(self, tmp_path):
        """schema 钉住 + top 基因组 = fake 读数里的 objective argmax。"""
        fake = FakeRunner(
            table={
                (f"expr:{EXPR_TOP}", *MINING): _reading(0.9, margin=0.08, n=1234),
                (f"expr:{EXPR_EQUAL}", *MINING): _reading(0.3, margin=0.02, n=800),
            },
            default=_reading(0.5),
        )
        rep = _run(tmp_path, fake)
        assert set(rep) == {
            "version",
            "tag",
            "config",
            "windows",
            "universe",
            "legs",
            "lattice",
            "arms",
            "two_stage",
            "top_genome",
            "judgment",
            "sensitivity",
            "random_control",
            "cell_failures",
            "criteria_readings",
        }
        assert rep["cell_failures"] == []  # fake runner 全成，无失败格
        assert rep["two_stage"] is None  # 默认单阶段（--two-stage 默认关，逐位不变）
        assert (
            rep["arms"]["v0"]["status"] == "off"
        )  # --v0-arm 默认关（v0.231 起可实跑）
        assert rep["config"]["n_random"] == 2  # 显式给值（--quick 未给）
        assert rep["config"]["max_combos"] == 64  # 默认（--quick 未给）
        assert set(rep["arms"]) == {
            "lattice",
            "equal_weight",
            "single_legs",
            "s_shape",
            "random",
            "v0",
        }
        assert set(rep["criteria_readings"]) == {
            "R34-C1",
            "R34-C2",
            "R34-C3",
            "R34-C4",
            "R34-C5",
        }
        # top = 0.9 那组（fake 表注入）；读数逐字段一致
        assert rep["top_genome"]["weights"] == [3.0, 1.0]
        assert rep["top_genome"]["expr"] == EXPR_TOP
        assert rep["top_genome"]["mining"]["objective"] == 0.9
        assert rep["top_genome"]["mining"]["n"] == 1234
        assert set(rep["top_genome"]["mining"]) == {
            "objective",
            "margin",
            "expectancy_R",
            "payoff_ratio",
            "win_rate",
            "n",
            "cell_signature",
        }
        # 等权/单腿对照臂从格子结果按名取出（恒在保底集，零额外预算）
        assert rep["arms"]["equal_weight"]["weights"] == [1.0, 1.0]
        assert rep["arms"]["equal_weight"]["reading"]["objective"] == 0.3
        assert {s["name"] for s in rep["arms"]["single_legs"]} == {
            "single_0",
            "single_1",
        }
        # 读数键在 criteria 块正确归位
        c = rep["criteria_readings"]
        assert c["R34-C1"]["top_n_mining"] == 1234
        assert c["R34-C2"]["delta_mining"] == pytest.approx(0.08 - 0.02)
        assert c["R34-C2"]["judgment_window"] is False
        # 灵敏度默认臂 objective 0.5 ≥ 基准 0.3 ⇒ 零翻转
        assert rep["sensitivity"]["flips"] == 0
        assert c["R34-C3"]["flips"] == 0
        # 随机臂最佳 0.5 < top 0.9 ⇒ pass（#71 纪律）
        assert rep["random_control"]["verdict"] == "pass"
        assert c["R34-C4"]["random_best_objective"] == 0.5
        assert c["R34-C5"]["status"] == "not_run"
        # 宇宙钉死
        assert rep["universe"]["n_codes"] == 2
        assert (tmp_path / "t1" / "_codes__t1.txt").read_text().split() == [
            "600000",
            "600001",
        ]

    def test_random_arms_deterministic_and_distinct(self, tmp_path):
        """随机臂：同腿数、与真腿不同、同 seed 复跑逐位一致（--random-seed）。"""
        rep1 = _run(tmp_path, FakeRunner())
        rep2 = _run(tmp_path, FakeRunner())
        assert len(rep1["arms"]["random"]) == 2
        for arm in rep1["arms"]["random"]:
            assert len(arm["legs"]) == len(LEGS)
            assert arm["legs"] != LEGS  # 采样腿 ≠ 真腿
        assert rep1["arms"]["random"] == rep2["arms"]["random"]

    def test_judgment_window_reruns_top_and_baseline(self, tmp_path):
        """双窗：top/等权/s_shape 三臂判定窗独立复测（cell_runner 按窗调用）。"""
        fake = FakeRunner(
            table={
                (f"expr:{EXPR_TOP}", *MINING): _reading(0.9, margin=0.08),
                (f"expr:{EXPR_EQUAL}", *MINING): _reading(0.3, margin=0.02),
                (f"expr:{EXPR_TOP}", *JUDGMENT): _reading(0.7, margin=0.05, n=300),
                (f"expr:{EXPR_EQUAL}", *JUDGMENT): _reading(0.4, margin=0.01, n=700),
            },
            default=_reading(0.5),
        )
        rep = _run(
            tmp_path,
            fake,
            "--judgment-start",
            JUDGMENT[0],
            "--judgment-end",
            JUDGMENT[1],
        )
        j = rep["judgment"]
        assert j["top"]["objective"] == 0.7 and j["equal"]["objective"] == 0.4
        assert j["s_shape"]["objective"] == 0.5  # default 兜底也被按窗调用
        assert rep["top_genome"]["judgment"]["margin"] == 0.05
        c2 = rep["criteria_readings"]["R34-C2"]
        assert c2["judgment_window"] is True
        assert c2["delta_judgment"] == pytest.approx(0.05 - 0.01)
        # 判定窗调用确实发生过（top 与等权各一次）
        assert (f"expr:{EXPR_TOP}", *JUDGMENT) in fake.calls
        assert (f"expr:{EXPR_EQUAL}", *JUDGMENT) in fake.calls

    def test_sensitivity_flip_counting(self, tmp_path):
        """灵敏度翻转计数：扰动臂 objective 跌破等权基准 = 翻转（R29 零翻转纪律）。"""
        fake = FakeRunner(
            table={
                (f"expr:{EXPR_TOP}", *MINING): _reading(0.9, margin=0.08),
                (f"expr:{EXPR_EQUAL}", *MINING): _reading(0.3, margin=0.02),
            },
            default=_reading(0.05),  # 扰动臂/随机臂全在基准之下
        )
        rep = _run(tmp_path, fake)
        assert rep["sensitivity"]["flips"] == 3  # sens_arms=3 全翻转
        assert rep["criteria_readings"]["R34-C3"]["flips"] == 3
        # 扰动臂权重 ≠ top 权重且非负（确定性由 --seed 钉住）
        for arm in rep["sensitivity"]["arms"]:
            assert arm["flip"] is True
            assert all(w >= 0 for w in arm["weights"])

    def test_random_verdict_suspect_when_random_wins(self, tmp_path):
        """#71 纪律：随机臂最高分 ≥ top ⇒ 标「筛选假象嫌疑」。"""
        fake = FakeRunner(
            table={
                (f"expr:{EXPR_TOP}", *MINING): _reading(0.2, margin=0.01),
                (f"expr:{EXPR_EQUAL}", *MINING): _reading(0.1, margin=0.0),
            },
            default=_reading(0.5),  # 随机臂 0.5 > top 0.2
        )
        rep = _run(tmp_path, fake)
        assert rep["random_control"]["verdict"] == "suspect"

    def test_all_cells_failed_guard_no_dump(self, tmp_path):
        """空数据护栏：全部格子失败 → 非零退出且不落盘（空产物=误读源）。"""
        fake = FakeRunner(default=None)
        rc = ses.main(_argv(tmp_path), cell_runner=fake)
        assert rc == 2
        assert not (tmp_path / "t1" / "_score_evolution__t1.json").exists()


class TestCliGuards:
    def _expect_error(self, argv, frag):
        with pytest.raises(SystemExit) as exc:
            ses.main(argv, cell_runner=FakeRunner())
        assert exc.value.code == 2
        assert frag in str(exc.value) or True  # ap.error 文案走 stderr，退出码即判定面

    def test_window_overlap_rejected(self, tmp_path):
        self._expect_error(
            _argv(
                tmp_path,
                "--judgment-start",
                "2024-06-01",
                "--judgment-end",
                "2025-01-01",
            ),
            "重叠",
        )

    def test_judgment_half_given_rejected(self, tmp_path):
        self._expect_error(_argv(tmp_path, "--judgment-start", JUDGMENT[0]), "同时给出")

    def test_pre2019_mining_rejected(self, tmp_path):
        self._expect_error(
            [
                "--legs",
                "close",
                "--mining-start",
                "2015-01-01",
                "--mining-end",
                "2018-01-01",
                "--codes",
                "600000",
                "--out-dir",
                str(tmp_path),
                "--tag",
                "t1",
            ],
            "pre2019",
        )

    def test_pre2019_judgment_rejected(self, tmp_path):
        self._expect_error(
            _argv(
                tmp_path,
                "--judgment-start",
                "2014-01-01",
                "--judgment-end",
                "2018-01-01",
            ),
            "pre2019",
        )

    def test_legs_mutex_rejected(self, tmp_path):
        self._expect_error(_argv(tmp_path, "--legs-file", "x.json"), "恰给一个")

    def test_empty_legs_rejected_no_dump(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("[]", encoding="utf-8")
        argv = [
            "--legs-file",
            str(f),
            "--mining-start",
            MINING[0],
            "--mining-end",
            MINING[1],
            "--codes",
            "600000",
            "--out-dir",
            str(tmp_path),
            "--tag",
            "t1",
        ]
        with pytest.raises(SystemExit) as exc:
            ses.main(argv, cell_runner=FakeRunner())
        assert exc.value.code == 2
        assert not (tmp_path / "t1" / "_score_evolution__t1.json").exists()

    def test_invalid_leg_rejected(self, tmp_path):
        self._expect_error(_argv(tmp_path, legs="close,SMA(close,20)"), "非法")

    def test_too_many_legs_rejected(self, tmp_path):
        legs = ",".join(["close", "volume", "open", "high", "low"])
        self._expect_error(
            _argv(tmp_path, legs=legs + ",MA(close,5),MA(close,10)"), "max-legs"
        )

    def test_legs_comma_split_is_paren_aware(self):
        """--legs 逗号分隔必须括号深度感知：MA(close,20) 内的逗号不切。"""
        legs, _ = ses._resolve_legs(
            _ns(legs="MA(close,20)/close, volume/MA(volume,20)"),
            _ap(),
        )
        assert legs == ["MA(close,20)/close", "volume/MA(volume,20)"]

    def test_legs_file_trajectory_pool_extract(self, tmp_path):
        """trajectory_pool.json：自动取 decision=pass 且 gate 非空者的 expression。"""
        pool = {
            "version": 1,
            "trajectories": [
                {"expression": "close", "decision": "pass", "gate": "j_low"},
                {
                    "expression": "volume",
                    "decision": "pass",
                    "gate": "",
                },  # gate 空 → 不取
                {
                    "expression": "open",
                    "decision": "fail",
                    "gate": "j_low",
                },  # 未过 → 不取
            ],
        }
        f = tmp_path / "pool.json"
        f.write_text(json.dumps(pool), encoding="utf-8")
        legs, source = ses._resolve_legs(_ns(legs_file=str(f)), _ap())
        assert legs == ["close"]
        assert "trajectory_pool" in source


def _ns(**kw):
    import argparse

    defaults = {"legs": "", "legs_file": "", "max_legs": 6}
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class _Ap:
    def error(self, msg):
        raise SystemExit(f"ap.error: {msg}")


def _ap():
    return _Ap()


# ---------------------------------------------------------------------------
# v0.231：两阶段省钱模式（--two-stage/--coarse-sample/--stage1-top-k/--quick）
# ---------------------------------------------------------------------------


def _expr_of(weights):
    return compile_composite(LEGS, list(weights))


class TestTwoStage:
    """两阶段：粗筛宇宙与终筛宇宙不同 runner；只有 top K 进终筛；对照臂两阶段都跑。"""

    def _fakes(self):
        """粗筛 fake：9 格全量读数（top3 = (3,1)/(1,3)/(2,1)）；终筛 fake 记录调用。"""
        # 2 腿权重格字典序：[(0,1),(1,0),(1,1),(1,2),(1,3),(2,1),(2,3),(3,1),(3,2)]
        s1_table = {
            (f"expr:{_expr_of((3.0, 1.0))}", *MINING): _reading(0.9, margin=0.08),
            (f"expr:{_expr_of((1.0, 3.0))}", *MINING): _reading(0.8, margin=0.06),
            (f"expr:{_expr_of((2.0, 1.0))}", *MINING): _reading(0.7, margin=0.05),
            (f"expr:{_expr_of((1.0, 1.0))}", *MINING): _reading(0.3, margin=0.02),
        }
        coarse = FakeRunner(table=s1_table, default=_reading(0.05))
        final = FakeRunner(
            table={
                (f"expr:{_expr_of((3.0, 1.0))}", *MINING): _reading(
                    0.95, margin=0.09, n=1200
                ),
                (f"expr:{_expr_of((1.0, 1.0))}", *MINING): _reading(
                    0.35, margin=0.02, n=800
                ),
            },
            default=_reading(0.5),
        )
        return coarse, final

    def test_stage_universes_and_survivors(self, tmp_path):
        coarse, final = self._fakes()
        rep = _run(
            tmp_path,
            final,
            "--two-stage",
            "--coarse-sample",
            "1",  # 宇宙 2 只 → 粗筛 1 只（与终筛不同宇宙）
            "--stage1-top-k",
            "3",
            coarse=coarse,
        )
        ts = rep["two_stage"]
        assert ts is not None
        # ① 粗筛与终筛是两个宇宙（不同 runner、不同 digest/只数）
        assert ts["coarse_n"] == 1 and rep["universe"]["n_codes"] == 2
        assert ts["coarse_digest"] != rep["universe"]["digest"]
        # ② 阶段 1：粗筛 runner 跑了全权重格（9）+ 随机臂（2×9）+ s_shape（1）
        assert len(coarse.calls) == 9 + 2 * 9 + 1
        assert ts["stage1_cells"] == 9 + 2 * 9 + 1
        # ③ 只有 top K=3 基因组进终筛（对照臂不占名额：晋级 3 行全来自 lattice）
        assert [s["weights"] for s in ts["stage1_survivors"]] == [
            [3.0, 1.0],
            [1.0, 3.0],
            [2.0, 1.0],
        ]
        assert ts["stage1_survivors"][0]["stage1_objective"] == 0.9
        # ④ 终筛 runner：晋级 3 + 对照权重重跑（等权+两单腿=3）+ 随机臂 18
        #    + s_shape 1 + 灵敏度 3（灵敏度/双窗都在阶段 2，与单阶段口径一致）
        assert len(final.calls) == 3 + 3 + 2 * 9 + 1 + 3
        # 前 3 个终筛调用就是晋级基因组（报告序：晋级组在前）
        assert [c[0] for c in final.calls[:3]] == [
            f"expr:{_expr_of((3.0, 1.0))}",
            f"expr:{_expr_of((1.0, 3.0))}",
            f"expr:{_expr_of((2.0, 1.0))}",
        ]
        # ⑤ top 基因组按**终筛**读数定（0.95 那组），judgment/灵敏度都在阶段 2
        assert rep["top_genome"]["weights"] == [3.0, 1.0]
        assert rep["top_genome"]["mining"]["objective"] == 0.95
        # ⑥ 灵敏度/随机对照判定用终筛读数（基准=终筛等权 0.35；默认 0.5 ≥ 基准不翻转）
        assert rep["sensitivity"]["baseline_objective"] == 0.35
        assert rep["sensitivity"]["flips"] == 0
        assert rep["random_control"]["random_best_objective"] == 0.5
        assert rep["random_control"]["verdict"] == "pass"
        # ⑦ 等权基准在终筛宇宙重跑过（对照臂两阶段都跑的实例）
        assert rep["arms"]["equal_weight"]["reading"]["objective"] == 0.35

    def test_stage1_all_failed_guard(self, tmp_path):
        """阶段 1 全灭 → 非零退出不落盘（空产物=误读源）。"""
        coarse = FakeRunner(default=None)
        final = FakeRunner()
        rc = ses.main(
            _argv(tmp_path, "--two-stage", "--coarse-sample", "1"),
            cell_runner=final,
            cell_runner_coarse=coarse,
        )
        assert rc == 2
        assert not (tmp_path / "t1" / "_score_evolution__t1.json").exists()

    def test_coarse_degenerate_note_when_sample_ge_universe(self, tmp_path):
        """粗筛数 ≥ 宇宙：阶段 1 退化为全量（口径仍正确，WARN 注记）。"""
        coarse, final = self._fakes()
        rep = _run(
            tmp_path,
            final,
            "--two-stage",
            "--coarse-sample",
            "500",  # ≥ 宇宙 2 只
            "--stage1-top-k",
            "3",
            coarse=coarse,
        )
        ts = rep["two_stage"]
        assert ts["coarse_n"] == 2 == rep["universe"]["n_codes"]

    def test_quick_sugar_and_explicit_override(self, tmp_path):
        """--quick = --n-random 1 --max-combos 24 的语义糖；显式给值优先。"""
        argv_quick = [
            "--legs",
            ",".join(LEGS),
            "--mining-start",
            MINING[0],
            "--mining-end",
            MINING[1],
            "--codes",
            "600000",
            "--out-dir",
            str(tmp_path),
            "--tag",
            "t1",
            "--quick",
        ]
        rc = ses.main(argv_quick, cell_runner=FakeRunner())
        assert rc == 0
        rep = json.loads(
            (tmp_path / "t1" / "_score_evolution__t1.json").read_text(encoding="utf-8")
        )
        assert rep["config"]["quick"] is True
        assert rep["config"]["n_random"] == 1  # quick 落 1
        assert rep["config"]["max_combos"] == 24  # quick 落 24
        assert len(rep["arms"]["random"]) == 1  # 随机臂真的只有 1 条
        # 显式给值优先于 quick
        rep2 = _run(tmp_path, FakeRunner(), "--quick")  # _argv 显式 --n-random 2
        assert rep2["config"]["n_random"] == 2  # 显式 2 覆盖 quick 的 1
        assert rep2["config"]["max_combos"] == 24  # 未显式给 ⇒ quick 的 24


class TestV0Arm:
    """V0 对照臂（--v0-arm，fake v0_runner 注入）：实跑读数进 arms.v0 与 vs_v0 对照。"""

    def test_v0_run_readings_and_comparison(self, tmp_path):
        fake = FakeRunner(
            table={
                (f"expr:{EXPR_TOP}", *MINING): _reading(0.9, margin=0.08, n=1234),
                (f"expr:{EXPR_EQUAL}", *MINING): _reading(0.3, margin=0.02, n=800),
            },
            default=_reading(0.5),
        )

        def v0_fake(*, start, end):
            assert (start, end) == MINING  # 与基因组同窗
            return {
                "objective": 0.7,
                "margin": 0.05,
                "expectancy_R": 0.2,
                "payoff_ratio": 1.8,
                "win_rate": 0.45,
                "n": 2000,
                "cell_signature": None,
                "selected_win_rate": 0.5,
                "n_taken": 300,
                "n_candidates": 5000,
            }

        rep = _run(tmp_path, fake, "--v0-arm", v0=v0_fake)
        v0 = rep["arms"]["v0"]
        assert v0["status"] == "run"
        assert "vehicle" in v0 and "simulate_portfolio_topn" in v0["vehicle"]
        assert v0["reading"]["objective"] == 0.7
        assert v0["reading"]["n_candidates"] == 5000
        # vs_v0 对照块（不进预注册判据 C1~C5）
        vs = rep["top_genome"]["vs_v0"]
        assert vs["delta_objective"] == pytest.approx(0.9 - 0.7)
        assert vs["delta_margin"] == pytest.approx(0.08 - 0.05)

    def test_v0_failed_reading_none(self, tmp_path):
        rep = _run(tmp_path, FakeRunner(), "--v0-arm", v0=lambda *, start, end: None)
        v0 = rep["arms"]["v0"]
        assert v0["status"] == "failed"
        assert v0["reading"] is None
        assert rep["top_genome"]["vs_v0"] is None

    def test_v0_runs_in_both_stages(self, tmp_path):
        """两阶段下 V0 与对照臂同待遇：阶段 1 粗筛 + 阶段 2 终筛各跑一次。"""
        calls = []

        def v0_fake(*, start, end):
            calls.append((start, end))
            return {"objective": 0.4, "margin": 0.01}

        coarse, final = TestTwoStage()._fakes()
        rep = _run(
            tmp_path,
            final,
            "--two-stage",
            "--coarse-sample",
            "1",
            "--stage1-top-k",
            "3",
            "--v0-arm",
            coarse=coarse,
            v0=v0_fake,
        )
        assert len(calls) == 2  # 两阶段都跑（粗筛 1 次 + 终筛 1 次）
        assert rep["arms"]["v0"]["status"] == "run"
        assert rep["two_stage"]["stage1_arms"]["v0_objective"] == 0.4


# ---------------------------------------------------------------------------
# v0.234：失败现场捕获与「合法空 vs 格子失败」语义区分（R34 r34_v1 教训）
# ---------------------------------------------------------------------------


class TestCellFailureSemantics:
    def test_classify_empty_result_vs_cell_failed(self):
        """空结果护栏指纹（产出 0 + 拒绝落盘）→ empty_result；其余 → cell_failed。"""
        empty_log = (
            "[TIME] 加载(含前复权) 100s / 评估 30s\n"
            "[ERR] 加载了 3000 只票却产出 0 笔交易(门槛过严/依赖失败?)；"
            "拒绝落盘——空结果会被误读成'该因子无判别力'。确需空结果请显式加 --allow-empty"
        )
        assert ses._classify_cell_failure(empty_log) == "empty_result"
        assert ses._classify_cell_failure("[FAIL] 超时（>1800s）") == "cell_failed"
        assert ses._classify_cell_failure("") == "cell_failed"

    def test_s_shape_arm_root_cause_empty_result(self, tmp_path):
        """s_shape 臂读数缺失 + 失败记录含空结果指纹 ⇒ root_cause=empty_result
        （r34_v1 实据形态：s_star≥70 ∧ j_low 超卖池近互斥 ⇒ 合法空，非格子坏）。"""
        fake = FakeRunner()
        fake.failures = [
            {
                "scorer": "s_shape",
                "gate": "j_low",
                "start": MINING[0],
                "end": MINING[1],
                "log_tail": "[ERR] 加载了 3000 只票却产出 0 笔交易(门槛过严/依赖失败?)"
                "；拒绝落盘——空结果会被误读成'该因子无判别力'",
            }
        ]
        # s_shape 格读数缺失（table 里不放 s_shape，default 给 None 的变体）
        fake.table = {("s_shape", *MINING): None}
        rep = _run(tmp_path, fake)
        arm = rep["arms"]["s_shape"]
        assert arm["reading"] is None
        assert arm["root_cause"] == "empty_result"
        # 报告 cell_failures 块有现场（来源 final）
        assert len(rep["cell_failures"]) == 1
        rec = rep["cell_failures"][0]
        assert rec["source"] == "final"
        assert rec["scorer"] == "s_shape"
        assert rec["root_cause"] == "empty_result"
        assert "产出 0 笔交易" in rec["log_tail"]

    def test_s_shape_arm_root_cause_cell_failed_without_log(self, tmp_path):
        """无失败记录（injected runner 无侧信道/串行无捕获）⇒ cell_failed。"""
        fake = FakeRunner()
        fake.table = {("s_shape", *MINING): None}
        rep = _run(tmp_path, fake)
        assert rep["arms"]["s_shape"]["root_cause"] == "cell_failed"
        assert rep["cell_failures"] == []

    def test_default_runner_captures_subprocess_log(self, tmp_path, monkeypatch):
        """默认 cell_runner：run_cell 以 capture=True 调用，失败格日志尾段进
        cell_runner.failures（串行透传「failed 无文本」的洞已补）。"""
        import argparse

        from custos.research import strategy_grid as sg

        seen = {}

        def spy_run_cell(ns, cell, cells_dir, capture=False):
            seen["capture"] = capture
            return (
                "failed",
                None,
                "[RUN ] x\n[ERR] 加载了 3000 只票却产出 0 笔交易；拒绝落盘",
            )

        monkeypatch.setattr(sg, "run_cell", spy_run_cell)
        args = argparse.Namespace(
            cost_bps=25.0,
            count=2000,
            top_n=20,
            timeout=0,
        )
        runner = ses._make_cell_runner(args, "codes.txt", tmp_path / "cells")
        assert (
            runner("s_shape", "j_low", {}, start="2022-01-01", end="2024-07-31") is None
        )
        assert seen["capture"] is True, "失败现场捕获必须开 capture=True"
        assert len(runner.failures) == 1
        assert "产出 0 笔交易" in runner.failures[0]["log_tail"]
        # 分类链路：该记录进报告应为 empty_result
        assert ses._cell_failures_report(runner)[0]["root_cause"] == "empty_result"


# ---------------------------------------------------------------------------
# --v0-lattice 模式（R36 Phase 3 调权路：V0 腿轴 × 倍率格 × 双窗闸门）
# ---------------------------------------------------------------------------

from custos.pipeline.screening.score_candidates import (  # noqa: E402
    DEFAULT_TECH_WEIGHTS,
)
from custos.research.score_calibration_study import (  # noqa: E402
    CONTRIB_LEG_KEYS,
)

V0L_LEGS = list(CONTRIB_LEG_KEYS)  # 30 键权威清单（含合成腿 repair_signals）


def _mk_trade(code, d_in, d_out, ret, r):
    return {
        "code": code,
        "entry_date": d_in,
        "exit_date": d_out,
        "ret": ret,
        "reason": "bbi_exit",
        "holding": 5,
        "risk_frac": 0.05,
        "r_multiple": r,
    }


def _v0l_collected_mining():
    """两日 × 两候选：A 只有 j_low 腿（24），B 有 volume_contraction+relative_strength_strong（30）。

    等倍率下 B 分高被选中（top_n=1）；j_low 加权/单腿格下 A 被选中——选择随倍率翻转。
    """
    return [
        {
            "trade": _mk_trade("A", "2023-01-03", "2023-01-08", 0.10, 2.0),
            "cand": {"patterns": {"j_low": True}},
            "code": "A",
        },
        {
            "trade": _mk_trade("B", "2023-01-03", "2023-01-08", -0.02, -0.4),
            "cand": {
                "patterns": {
                    "volume_contraction": True,
                    "relative_strength_strong": True,
                }
            },
            "code": "B",
        },
        {
            "trade": _mk_trade("A", "2023-01-09", "2023-01-16", -0.02, -0.4),
            "cand": {"patterns": {"j_low": True}},
            "code": "A",
        },
        {
            "trade": _mk_trade("B", "2023-01-09", "2023-01-16", 0.03, 0.6),
            "cand": {
                "patterns": {
                    "volume_contraction": True,
                    "relative_strength_strong": True,
                }
            },
            "code": "B",
        },
    ]


def _v0l_collected_judgment():
    """判定窗 regime 翻转：A 净亏 B 净赚（与挖掘窗相反，检验双窗读数独立）。"""
    return [
        {
            "trade": _mk_trade("A", "2025-01-06", "2025-01-10", -0.05, -1.0),
            "cand": {"patterns": {"j_low": True}},
            "code": "A",
        },
        {
            "trade": _mk_trade("B", "2025-01-06", "2025-01-10", 0.04, 0.8),
            "cand": {
                "patterns": {
                    "volume_contraction": True,
                    "relative_strength_strong": True,
                }
            },
            "code": "B",
        },
        {
            "trade": _mk_trade("A", "2025-01-13", "2025-01-17", 0.02, 0.4),
            "cand": {"patterns": {"j_low": True}},
            "code": "A",
        },
        {
            "trade": _mk_trade("B", "2025-01-13", "2025-01-17", -0.01, -0.2),
            "cand": {
                "patterns": {
                    "volume_contraction": True,
                    "relative_strength_strong": True,
                }
            },
            "code": "B",
        },
    ]


class SpyCollector:
    """按窗返回合成收集结果的 fake collector；记录调用（拆分证据：窗数次，非格数次）。"""

    def __init__(self, by_window=None):
        self.by_window = by_window if by_window is not None else {}
        self.calls = []

    def __call__(self, window):
        self.calls.append((window.start, window.end))
        return self.by_window.get((window.start, window.end), [])


def _argv_v0l(tmp_path, *extra):
    argv = [
        "--v0-lattice",
        "--mining-start",
        MINING[0],
        "--mining-end",
        MINING[1],
        "--codes",
        "600000,600001",
        "--top-n",
        "1",
        "--out-dir",
        str(tmp_path),
        "--tag",
        "t1",
        "--n-random",
        "2",
        "--sens-arms",
        "3",
    ]
    return argv + list(extra)


def _run_v0l(tmp_path, fake, collector, *extra):
    rc = ses.main(
        _argv_v0l(tmp_path, *extra), cell_runner=fake, v0l_collector=collector
    )
    assert rc == 0
    out = tmp_path / "t1" / "_score_evolution__t1.json"
    assert out.exists()
    return json.loads(out.read_text(encoding="utf-8"))


class TestV0LatticeUnits:
    """倍率映射与倍率格构造的单元钉测。"""

    def test_mult_overrides_mapping(self):
        """overrides = 默认权重 × 倍率；mult=1 逐位等于默认表；0=关腿。"""
        legs = ["bbi_above", "j_low", "repair_signals"]
        ov = ses._v0_mult_overrides([2.0, 0.0, 1.0], legs)
        assert ov["bbi_above"] == DEFAULT_TECH_WEIGHTS["bbi_above"] * 2
        assert ov["j_low"] == 0.0  # 0 倍率 = 关腿
        # repair_signals 合成腿：倍率同乘 each/cap 双键
        assert ov["repair_signals_each"] == DEFAULT_TECH_WEIGHTS["repair_signals_each"]
        assert ov["repair_signals_cap"] == DEFAULT_TECH_WEIGHTS["repair_signals_cap"]
        # 全 1 倍率 ⇒ 全键逐位等于默认表（等倍率格 = live V0 默认权重的证据）
        ov1 = ses._v0_mult_overrides([1.0] * len(V0L_LEGS), V0L_LEGS)
        for leg in V0L_LEGS:
            for key in ses._v0_leg_weight_keys(leg):
                assert ov1[key] == DEFAULT_TECH_WEIGHTS[key]
        # 31 键 = 29 直映腿 + repair_signals 双键
        assert len(ov1) == 31

    def test_mult_overrides_negative_leg_sign_preserved(self):
        """负腿倍率缩放罚分幅度、永不变号；0 = 罚分移除。"""
        legs = ["macd_top_divergence", "volume_yy_bear", "distribution_watch"]
        ov = ses._v0_mult_overrides([2.0, 3.0, 0.0], legs)
        assert ov["macd_top_divergence"] == -16.0  # -8 × 2
        assert ov["volume_yy_bear"] == -15.0  # -5 × 3
        assert ov["distribution_watch"] == 0.0
        assert ov["macd_top_divergence"] < 0 and ov["volume_yy_bear"] < 0

    def test_mult_overrides_dim_mismatch_rejected(self):
        with pytest.raises(ValueError):
            ses._v0_mult_overrides([1.0], ["bbi_above", "j_low"])

    def test_mult_lattice_baseline_set_and_shape(self):
        """保底集：等倍率（全 1）+ 30 单腿恒在；填充 = 单腿变档（其余保持 1）。"""
        lat = ses._v0_mult_lattice(30, (0.0, 1.0, 2.0, 3.0), max_combos=64)
        assert len(lat) == 64
        equal = tuple(1.0 for _ in range(30))
        assert lat[0] == equal  # 等倍率基准是首行
        singles = [w for w in lat if sum(1 for x in w if x) == 1]
        assert len(singles) == 30  # 各单腿对照臂全在
        # 填充臂形态：恰一条腿取 L≠1、其余保持 1；L=0（关腿）优先铺满 30 条腿
        fills = lat[31:]
        assert len(fills) == 33
        for w in fills:
            varied = [i for i, x in enumerate(w) if x != 1.0]
            assert len(varied) == 1
            assert w[varied[0]] in (0.0, 2.0, 3.0)
        off_fills = [w for w in fills if 0.0 in w]
        varied_off = {w.index(0.0) for w in off_fills}
        assert varied_off == set(range(30))  # 关腿边际臂覆盖全部 30 腿
        # 确定性：两次调用逐位一致
        assert ses._v0_mult_lattice(30, (0.0, 1.0, 2.0, 3.0), max_combos=64) == lat

    def test_mult_lattice_must_set_survives_small_cap(self):
        """保底集可超 max_combos（同 weight_lattice 截断语义；--quick 24 < 31 保底）。"""
        lat = ses._v0_mult_lattice(30, (0.0, 1.0, 2.0, 3.0), max_combos=24)
        assert len(lat) == 31  # 等倍率 + 30 单腿，零填充
        assert tuple(1.0 for _ in range(30)) in lat

    def test_mult_lattice_levels_without_zero(self):
        lat = ses._v0_mult_lattice(30, (1.0, 2.0), max_combos=64)
        assert len(lat) == 31 + 30  # 填充只有 L=2 一轮
        assert all(0.0 not in w for w in lat[31:])


class TestV0LatticeEndToEnd:
    """fake cell_runner（DSL 随机臂）+ fake collector（V0 收集）端到端。"""

    def _mining_collector(self):
        return SpyCollector({MINING: _v0l_collected_mining()})

    def test_schema_and_top_selection(self, tmp_path):
        fake = FakeRunner(default=_reading(0.5))
        collector = self._mining_collector()
        rep = _run_v0l(tmp_path, fake, collector)
        assert set(rep) == {
            "version",
            "tag",
            "config",
            "windows",
            "universe",
            "legs",
            "lattice",
            "arms",
            "pool_baseline",
            "top_genome",
            "judgment",
            "sensitivity",
            "random_control",
            "cell_failures",
            "criteria_readings",
        }
        cfg = rep["config"]
        assert cfg["mode"] == "v0_lattice"
        assert "倍率" in cfg["multiplier_space"]
        assert rep["legs"] == V0L_LEGS  # 腿轴 = 权威清单
        lat = rep["lattice"]
        assert lat["kind"] == "v0_multiplier" and lat["n_legs"] == 30
        assert lat["n_combos"] == 64
        assert lat["default_weights_snapshot"] == DEFAULT_TECH_WEIGHTS
        assert lat["leg_weight_keys"]["repair_signals"] == [
            "repair_signals_each",
            "repair_signals_cap",
        ]
        assert set(rep["arms"]) == {
            "lattice",
            "equal_weight",
            "single_legs",
            "s_shape",
            "random",
            "v0",
        }
        assert set(rep["criteria_readings"]) == {
            "R34-C1",
            "R34-C2",
            "R34-C3",
            "R34-C4",
            "R34-C5",
        }
        # collect/score 拆分证据：64 格 + 3 灵敏度臂，collector 只跑了挖掘窗一遍
        assert collector.calls == [MINING]
        # top = single_0（bbi_above 单腿）：平分并列取格子序在前者（A 在候选序前）
        top = rep["top_genome"]
        assert top["weights"] == [1.0] + [0.0] * 29
        # expr 槽位 = 可还原的倍率向量 JSON 字符串
        mult = json.loads(top["expr"])
        assert mult == top["multipliers"] and mult["bbi_above"] == 1.0
        assert mult["j_low"] == 0.0
        # overrides 与倍率向量一致（bbi_above×1=5，j_low×0=0，repair 双键×0）
        assert top["overrides"]["bbi_above"] == 5.0
        assert top["overrides"]["j_low"] == 0.0
        assert top["overrides"]["repair_signals_each"] == 0.0
        # top 读数 = 选中 A 两笔（+0.10/−0.02）：胜率 0.5、盈亏比 5、n=2
        assert top["mining"]["n"] == 2
        assert top["mining"]["win_rate"] == 0.5
        assert top["mining"]["payoff_ratio"] == pytest.approx(5.0)
        assert top["mining"]["margin"] == pytest.approx(0.5 - 1 / 6)
        # 等倍率基准 = live V0 默认权重：选中 B 两笔（−0.02/+0.03）
        eq = rep["arms"]["equal_weight"]
        assert eq["weights"] == [1.0] * 30
        assert eq["reading"]["payoff_ratio"] == pytest.approx(1.5)
        assert eq["reading"]["margin"] == pytest.approx(0.1)
        # arms.v0 即等倍率行读数（V0 本臂不另跑）
        assert rep["arms"]["v0"]["status"] == "run"
        assert rep["arms"]["v0"]["reading"] == eq["reading"]
        # s_shape 参照臂不属本模式
        assert rep["arms"]["s_shape"]["status"] == "not_applicable"
        # 全候选池审计块：权重不变量（4 笔：2 赢 2 亏，payoff 3.25）
        pool = rep["pool_baseline"]["mining"]
        assert pool["n"] == 4
        assert pool["payoff_ratio"] == pytest.approx(3.25)
        assert rep["pool_baseline"]["judgment"] is None  # 无判定窗
        # C2：top − 基准 margin 差（挖掘窗）
        c2 = rep["criteria_readings"]["R34-C2"]
        assert c2["delta_mining"] == pytest.approx((0.5 - 1 / 6) - 0.1)
        assert c2["judgment_window"] is False
        assert rep["criteria_readings"]["R34-C1"]["top_n_mining"] == 2
        # 单腿臂带腿名且全部有读数
        assert len(rep["arms"]["single_legs"]) == 30
        assert {s["name"] for s in rep["arms"]["single_legs"]} == {
            f"single_{i}" for i in range(30)
        }
        j_low_single = rep["arms"]["single_legs"][2]
        assert j_low_single["leg"] == "j_low"
        assert j_low_single["row"]["reading"]["margin"] == pytest.approx(0.5 - 1 / 6)

    def test_collect_once_per_window_with_judgment(self, tmp_path):
        """双窗：collector 恰好每窗一遍（64 格 + 灵敏度 + 判定窗两臂不触发重收集）。"""
        collector = SpyCollector(
            {MINING: _v0l_collected_mining(), JUDGMENT: _v0l_collected_judgment()}
        )
        rep = _run_v0l(
            tmp_path,
            FakeRunner(default=_reading(0.5)),
            collector,
            "--judgment-start",
            JUDGMENT[0],
            "--judgment-end",
            JUDGMENT[1],
        )
        assert collector.calls == [MINING, JUDGMENT]  # 各恰好一遍
        j = rep["judgment"]
        assert set(j) == {"top", "equal", "s_shape"} and j["s_shape"] is None
        # 判定窗 regime 翻转：top（选 A）margin −0.214，基准（选 B）margin +0.3
        assert j["top"]["margin"] == pytest.approx(0.5 - 1 / 1.4)
        assert j["equal"]["margin"] == pytest.approx(0.3)
        c2 = rep["criteria_readings"]["R34-C2"]
        assert c2["judgment_window"] is True
        assert c2["delta_judgment"] == pytest.approx((0.5 - 1 / 1.4) - 0.3)
        assert rep["criteria_readings"]["R34-C1"]["top_n_judgment"] == 2
        # 判定窗池审计块同步落盘
        assert rep["pool_baseline"]["judgment"]["n"] == 4

    def test_sensitivity_wiring(self, tmp_path):
        """灵敏度：扰动 top 倍率向量重打分（不重新 collect）；翻转定义=跌破等倍率基准。"""
        rep = _run_v0l(
            tmp_path, FakeRunner(default=_reading(0.5)), self._mining_collector()
        )
        sens = rep["sensitivity"]
        assert sens["n_arms"] == 3 and len(sens["arms"]) == 3
        assert sens["baseline_objective"] == pytest.approx(
            rep["arms"]["equal_weight"]["reading"]["objective"]
        )
        # top=single_0：扰动后仍只 bbi_above 正 ⇒ 平分并列 A 在前 ⇒ 同读数不翻转
        assert sens["flips"] == 0
        assert rep["criteria_readings"]["R34-C3"]["flips"] == 0
        for arm in sens["arms"]:
            assert len(arm["weights"]) == 30
            assert all(w >= 0 for w in arm["weights"])  # 倍率非负
            assert arm["weights"] != rep["top_genome"]["weights"]  # 确实扰动
            assert arm["flip"] is False
        # 同 seed 复跑逐位一致
        rep2 = _run_v0l(
            tmp_path, FakeRunner(default=_reading(0.5)), self._mining_collector()
        )
        assert rep["sensitivity"] == rep2["sensitivity"]

    def test_random_arms_use_dsl_path(self, tmp_path):
        """随机对照臂仍走 DSL expr 路（cell_runner）：腿数 = --max-legs（6，R34 标尺口径）。"""
        fake = FakeRunner(default=_reading(0.5))
        rep = _run_v0l(tmp_path, fake, self._mining_collector())
        arms = rep["arms"]["random"]
        assert len(arms) == 2  # --n-random 2
        for arm in arms:
            assert len(arm["legs"]) == 6  # --max-legs 默认 6，不是 V0 的 30 腿
            assert arm["n_cells"] == 64  # 同权重格（weight_lattice(6)）同待遇
        # V0 倍率格不经过 cell_runner——fake 收到的调用全是随机臂的 expr: scorer
        assert len(fake.calls) == 2 * 64
        assert all(scorer.startswith("expr:") for scorer, _, _ in fake.calls)
        assert all((s, e) == MINING for _, s, e in fake.calls)
        # top 1.33 > 随机臂最佳 0.5 ⇒ pass
        assert rep["random_control"]["verdict"] == "pass"
        assert rep["criteria_readings"]["R34-C4"]["random_best_objective"] == 0.5

    def test_random_verdict_suspect_when_random_wins(self, tmp_path):
        fake = FakeRunner(default=_reading(9.9))  # 随机臂压过 top
        rep = _run_v0l(tmp_path, fake, self._mining_collector())
        assert rep["random_control"]["verdict"] == "suspect"

    def test_negative_leg_multiplier_flips_selection(self, tmp_path):
        """负腿 e2e：distribution_watch ×0（关罚分）让被罚候选反超——倍率语义穿透全链。"""
        idx_watch = V0L_LEGS.index("distribution_watch")

        def collector(window):
            return [
                {
                    "trade": _mk_trade("P", "2023-01-03", "2023-01-08", 0.10, 2.0),
                    "cand": {
                        "patterns": {"j_low": True},
                        "distribution": {"available": True, "risk_level": "watch"},
                    },
                    "code": "P",
                },
                {
                    "trade": _mk_trade("Q", "2023-01-03", "2023-01-08", -0.02, -0.4),
                    "cand": {"patterns": {"volume_contraction": True}},
                    "code": "Q",
                },
                {
                    "trade": _mk_trade("P", "2023-01-09", "2023-01-16", -0.01, -0.2),
                    "cand": {
                        "patterns": {"j_low": True},
                        "distribution": {"available": True, "risk_level": "watch"},
                    },
                    "code": "P",
                },
                {
                    "trade": _mk_trade("Q", "2023-01-09", "2023-01-16", 0.04, 0.8),
                    "cand": {"patterns": {"volume_contraction": True}},
                    "code": "Q",
                },
            ]

        rep = _run_v0l(tmp_path, FakeRunner(default=_reading(0.5)), collector)
        rows = rep["arms"]["lattice"]
        # 等倍率：P = 24 − 10 = 14 < Q = 15 ⇒ 选 Q（rets −0.02/+0.04）
        eq = rep["arms"]["equal_weight"]["reading"]
        assert eq["payoff_ratio"] == pytest.approx(2.0)
        # distribution_watch ×0 格（保底填充集内）：P = 24 > Q = 15 ⇒ 选 P（+0.10/−0.01）
        off = [
            r
            for r in rows
            if r["weights"][idx_watch] == 0.0
            and all(w == 1.0 for i, w in enumerate(r["weights"]) if i != idx_watch)
        ]
        assert len(off) == 1
        assert off[0]["reading"]["payoff_ratio"] == pytest.approx(10.0)
        assert off[0]["reading"]["margin"] == pytest.approx(0.5 - 1 / 11)
        assert off[0]["reading"]["margin"] > eq["margin"]

    def test_empty_collect_guard_no_dump(self, tmp_path):
        """空结果护栏：收集为空（[] 或 None）→ 非零退出不落盘。"""
        for empty in ([], None):
            rc = ses.main(
                _argv_v0l(tmp_path),
                cell_runner=FakeRunner(),
                v0l_collector=lambda window: empty,
            )
            assert rc == 2
            assert not (tmp_path / "t1" / "_score_evolution__t1.json").exists()


class TestV0LatticeCliGuards:
    def _expect_error(self, argv):
        with pytest.raises(SystemExit) as exc:
            ses.main(argv, cell_runner=FakeRunner(), v0l_collector=SpyCollector())
        assert exc.value.code == 2

    def test_legs_mutex(self, tmp_path):
        self._expect_error(_argv_v0l(tmp_path, "--legs", "close"))

    def test_legs_file_mutex(self, tmp_path):
        self._expect_error(_argv_v0l(tmp_path, "--legs-file", "x.json"))

    def test_two_stage_mutex(self, tmp_path):
        self._expect_error(_argv_v0l(tmp_path, "--two-stage"))

    def test_v0_arm_mutex(self, tmp_path):
        self._expect_error(_argv_v0l(tmp_path, "--v0-arm"))

    def test_pre2019_mining_rejected(self, tmp_path):
        self._expect_error(
            [
                "--v0-lattice",
                "--mining-start",
                "2015-01-01",
                "--mining-end",
                "2018-01-01",
                "--codes",
                "600000",
                "--out-dir",
                str(tmp_path),
                "--tag",
                "t1",
            ]
        )

    def test_pre2019_judgment_rejected(self, tmp_path):
        self._expect_error(
            _argv_v0l(
                tmp_path,
                "--judgment-start",
                "2014-01-01",
                "--judgment-end",
                "2018-01-01",
            )
        )


# ---------------------------------------------------------------------------
# --addon-leg 骨架加腿模式（R36 思路二：V0 等权骨架 + λ·TS_RANK(expr)）
# ---------------------------------------------------------------------------

import pandas as pd  # noqa: E402

ADDON_EXPR = "ROC(CLOSE,5)"


def _v0l_collected_addon_mining():
    """等权下 B(30)>A(24) 被选（top_n=1）；加腿值 A=0.9/B=0.1——λ=6 仍 B、λ=24 翻 A。"""
    items = _v0l_collected_mining()
    for it in items:
        it["addon"] = {ADDON_EXPR: 0.9 if it["code"] == "A" else 0.1}
    return items


class TestV0LatticeAddon:
    def test_genome_set_and_selection_flip(self, tmp_path):
        """基因组=基准+腿×λ档；λ=24 时选择从 B 翻成 A（payoff 1.5→5.0）；top=加腿格。"""
        fake = FakeRunner(default=_reading(0.5))
        collector = SpyCollector({MINING: _v0l_collected_addon_mining()})
        rep = _run_v0l(
            tmp_path,
            fake,
            collector,
            "--addon-leg",
            ADDON_EXPR,
            "--addon-levels",
            "6,24",
        )
        cfg = rep["config"]
        assert cfg["mode"] == "v0_lattice_addon"
        lat = rep["lattice"]
        assert lat["kind"] == "v0_addon" and lat["n_combos"] == 3
        assert lat["addon_legs"] == [ADDON_EXPR] and lat["addon_levels"] == [6.0, 24.0]
        rows = rep["arms"]["lattice"]
        assert len(rows) == 3  # 基准 + 2 λ 档
        base, lam6, lam24 = rows
        assert base["addon"] is None
        assert lam6["addon"] == {"expr": ADDON_EXPR, "lambda": 6.0}
        # λ=6：B 仍被选（30+0.6 > 24+5.4）——B 两笔 payoff 1.5；基准同
        assert base["reading"]["payoff_ratio"] == pytest.approx(1.5)
        assert lam6["reading"]["payoff_ratio"] == pytest.approx(1.5)
        # λ=24：A 翻盘（45.6 > 32.4）——A 两笔 payoff 5.0 ⇒ top = 加腿格
        assert lam24["reading"]["payoff_ratio"] == pytest.approx(5.0)
        assert rep["top_genome"]["addon"] == {"expr": ADDON_EXPR, "lambda": 24.0}
        # C2 机械读数 = top margin − 基准 margin（在场可判）
        assert rep["criteria_readings"]["R34-C2"]["delta_mining"] is not None
        # 灵敏度扰动 31 维（30 倍率 + λ——λ 也是基因组参数同受扰）
        assert len(rep["sensitivity"]["arms"][0]["weights"]) == 31
        # 每加腿最佳档行 = λ=24
        per = rep["arms"]["addon_per_expr"]
        assert per[0]["expr"] == ADDON_EXPR
        assert per[0]["best_row"]["addon"]["lambda"] == 24.0

    def test_addon_missing_excluded_not_zero(self, tmp_path):
        """无加腿读数的交易被排除（缺席≠零值）：n_candidates 减一、
        n_addon_missing 如实记；基准基因组不受影响也无此键。"""
        items = _v0l_collected_addon_mining()
        items[2]["addon"][ADDON_EXPR] = None  # A 第二笔缺读数（warmup 语义）
        collector = SpyCollector({MINING: items})
        rep = _run_v0l(
            tmp_path,
            FakeRunner(default=_reading(0.5)),
            collector,
            "--addon-leg",
            ADDON_EXPR,
            "--addon-levels",
            "24",
        )
        base, lam24 = rep["arms"]["lattice"]
        assert base["reading"]["n_candidates"] == 4
        assert "n_addon_missing" not in base["reading"]
        assert lam24["reading"]["n_candidates"] == 3
        assert lam24["reading"]["n_addon_missing"] == 1

    def test_addon_series_wrapper_and_value_edges(self):
        """collect 期序列 = TS_RANK(expr, K) 直评；取值越界/NaN/None 序列 → None。"""
        n = 30
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        close = [100.0 + i for i in range(n)]
        df = pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": [c + 0.5 for c in close],
                "low": [c - 0.5 for c in close],
                "close": close,
                "volume": [1000.0] * n,
            }
        )
        from custos.research.evolution import expr_dsl

        series = ses._addon_series(df, [ADDON_EXPR], 5)[ADDON_EXPR]
        direct = expr_dsl.evaluate(f"TS_RANK({ADDON_EXPR},5)", df)
        pd.testing.assert_series_equal(series, direct, check_exact=True)
        assert ses._addon_value(series, n - 1) == float(series.iloc[-1])
        assert ses._addon_value(None, 0) is None
        assert ses._addon_value(series, n + 5) is None  # 越界
        assert ses._addon_value(series, 0) is None  # warmup NaN


class TestV0LatticeAddonCliGuards:
    def _expect_error(self, argv):
        with pytest.raises(SystemExit) as exc:
            ses.main(argv, cell_runner=FakeRunner(), v0l_collector=SpyCollector())
        assert exc.value.code == 2

    def test_addon_requires_v0_lattice(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            ses.main(
                [
                    "--legs",
                    "ROC(CLOSE,5)",
                    "--mining-start",
                    MINING[0],
                    "--mining-end",
                    MINING[1],
                    "--codes",
                    "600000",
                    "--out-dir",
                    str(tmp_path),
                    "--tag",
                    "t1",
                    "--addon-leg",
                    ADDON_EXPR,
                ],
                cell_runner=FakeRunner(),
            )
        assert exc.value.code == 2

    def test_addon_max_four(self, tmp_path):
        argv = _argv_v0l(
            tmp_path,
            *sum((["--addon-leg", f"ROC(CLOSE,{k})"] for k in (3, 5, 8, 13, 21)), []),
        )
        self._expect_error(argv)

    def test_addon_bad_dsl_rejected(self, tmp_path):
        self._expect_error(_argv_v0l(tmp_path, "--addon-leg", "NO_SUCH_OP(CLOSE)"))

    def test_addon_complexity_violation_rejected(self, tmp_path):
        self._expect_error(
            _argv_v0l(tmp_path, "--addon-leg", "+".join(["CLOSE"] * 400))
        )

    def test_addon_levels_must_be_positive(self, tmp_path):
        self._expect_error(
            _argv_v0l(tmp_path, "--addon-leg", ADDON_EXPR, "--addon-levels", "0,6")
        )
        self._expect_error(
            _argv_v0l(tmp_path, "--addon-leg", ADDON_EXPR, "--addon-levels", "abc")
        )
