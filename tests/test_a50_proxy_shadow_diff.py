"""A50 历史代理对账纯函数测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "dev" / "a50_proxy_shadow_diff.py"
)
spec = importlib.util.spec_from_file_location("a50_proxy_shadow_diff", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_compare_exact_series_and_date_normalization():
    assert mod._day("20261030") == "2026-10-30"
    rows = [
        {"date": f"2026-10-{day:02d}", "close": 100.0 + day} for day in (28, 29, 30)
    ]
    report = mod.compare(rows, rows)
    assert report["common_days"] == 3
    assert report["exact_close_days"] == 3
    assert report["return_exact_days"] == 2
    assert report["return_mae_pp"] == 0.0
    assert report["return_correlation"] == 1.0


def test_compare_reports_missing_dates_and_bias():
    wind = [
        {"date": "2026-10-28", "close": 100.0},
        {"date": "2026-10-29", "close": 102.0},
    ]
    other = [
        {"date": "2026-10-28", "close": 101.0},
        {"date": "2026-10-29", "close": 103.0},
    ]
    report = mod.compare(wind, other)
    assert report["common_days"] == 2
    assert report["exact_close_days"] == 0
    assert report["ratio_mean"] == pytest.approx((100 / 101 + 102 / 103) / 2)
    assert report["return_mae_pp"] > 0


def test_compare_requires_overlap():
    with pytest.raises(ValueError, match="共同"):
        mod.compare(
            [{"date": "2026-01-01", "close": 1.0}],
            [{"date": "2026-01-02", "close": 1.0}],
        )
