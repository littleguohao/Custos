"""定义校准清单覆盖率：P0/P1（live 判定 / 展示）参数必须全部在 calibration_inventory.md 登记。"""

import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "dev" / "calibration_inventory.py"
)
spec = importlib.util.spec_from_file_location("calibration_inventory", SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_live_params_all_registered():
    miss = ci.check(ci.inventory())
    assert not miss, (
        f"新增 live 参数未登记定义校准清单（跑 calibration_inventory.py 补表）: {miss}"
    )


def test_tiers_follow_registry():
    rows = ci.inventory()
    by = {(r["factor"], r["param"]): r["tier"] for r in rows}
    assert by[("b1_thresholds", "J_LOW_THRESHOLD")] == "P0"
    assert by[("distribution", "DIST_RECENT")] == "P0"  # gate
    assert all(r["tier"] == "P2" for r in rows if r["factor"].startswith("qn_"))
