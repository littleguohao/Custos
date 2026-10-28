"""#116 验收①对拍脚本（v0.462 放宽口径）。"""

import importlib.util
import json
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "dev"
    / "snapshot_acceptance_diff.py"
)
spec = importlib.util.spec_from_file_location("snapshot_acceptance_diff", SCRIPT)
sad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sad)


def _rep(**over):
    base = {
        "verdict": "falsified",
        "accounting": {"mining_a": {"n_signals": 100, "n_subset": 90}},
        "windows": {
            "mining_a": {
                "x10": {
                    "margin": 0.05,
                    "win_rate": 0.4,
                    "n": 90,
                    "n_taken": 30,
                    "ret_over_dd": 1.2,
                    "rdd_gate": True,
                    "portfolio": {"cagr": 0.1},
                }
            }
        },
        "criteria": {"C2": {"ok": False, "delta_margin": 0.001}},
        "provenance": {"git_sha": "aaa", "data_source": None},
    }
    for path, v in over.items():
        node = base
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = v
    return base


def test_identical_passes():
    assert sad.compare(_rep(), _rep())["passed"]


def test_score_dependent_diff_allowed_and_provenance_ignored():
    r = sad.compare(
        _rep(),
        _rep(
            **{
                "windows.mining_a.x10.ret_over_dd": 1.2001,
                "windows.mining_a.x10.n_taken": 31,
                "windows.mining_a.x10.portfolio.cagr": 0.1001,
                "provenance.git_sha": "bbb",
            }
        ),
    )
    assert r["passed"] and len(r["score_dependent_diff"]) == 3


def test_strict_field_diff_fails():
    r = sad.compare(_rep(), _rep(**{"windows.mining_a.x10.win_rate": 0.4001}))
    assert not r["passed"] and r["strict_mismatch"][0]["path"].endswith("win_rate")
    assert not sad.compare(_rep(), _rep(**{"criteria.C2.delta_margin": 0.0011}))[
        "passed"
    ]


def test_verdict_flip_fails_even_if_score_dependent():
    assert not sad.compare(_rep(), _rep(**{"criteria.C2.ok": True}))["passed"]
    assert not sad.compare(_rep(), _rep(verdict="candidate"))["passed"]


def test_cli_exit_codes(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_rep()), encoding="utf-8")
    b.write_text(
        json.dumps(_rep(**{"windows.mining_a.x10.margin": 0.06})), encoding="utf-8"
    )
    assert sad.main([str(a), str(a)]) == 0
    assert sad.main([str(a), str(b), "--out", str(tmp_path / "o.json")]) == 1
    assert sad.main([str(a), str(tmp_path / "nope.json")]) == 2
