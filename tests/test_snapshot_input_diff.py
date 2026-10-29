"""#116 输入层对拍脚本 compare 纯函数（v0.465）。"""

import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "dev" / "snapshot_input_diff.py"
)
spec = importlib.util.spec_from_file_location("snapshot_input_diff", SCRIPT)
sid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sid)


def _bars(scale=1.0, bump=None):
    d = ["2016-12-29", "2016-12-30", "2022-01-04", "2022-01-05"]
    c = [10.0, 10.5, 11.0, 11.2]
    c = [x * scale for x in c]
    if bump is not None:
        c[bump] += 0.01
    return {"date": d, "close": c, "low": c}


def test_identical_constant_and_nonconstant():
    real = {"a": _bars(), "b": _bars(), "c": _bars(), "d": _bars()}
    snap = {"a": _bars(), "b": _bars(1 / 1.03), "c": _bars(bump=2), "d": {"error": "x"}}
    rep = sid.compare(real, snap)
    by = {r["code"]: r for r in rep["rows"]}
    assert (
        by["a"]["identical"] and not by["b"]["identical"] and by["b"]["constant_ratio"]
    )
    assert not by["c"]["constant_ratio"] and "error" in by["d"]
    assert (
        rep["n_identical"],
        rep["n_constant_ratio_only"],
        rep["n_nonconstant"],
        rep["n_error"],
    ) == (1, 1, 1, 1)
