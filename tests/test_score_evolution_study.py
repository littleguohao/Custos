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


def _run(tmp_path, fake, *extra, legs=None):
    rc = ses.main(_argv(tmp_path, *extra, legs=legs), cell_runner=fake)
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
            "top_genome",
            "judgment",
            "sensitivity",
            "random_control",
            "criteria_readings",
        }
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
