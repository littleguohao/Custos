"""因子版本表（TODO #121）：源码一变必须 --bump（语义变）或 --refactor（纯重构），产物带版本表。"""

import importlib.util
from pathlib import Path

from custos.core import factor_versions as fv

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "factor_versions.py"
spec = importlib.util.spec_from_file_location("factor_versions_cli", SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def test_table_matches_sources():
    bad = cli.check()
    assert not bad, (
        "因子源码与版本表不一致——判定语义变了跑 `scripts/dev/factor_versions.py --bump <因子> --reason ...`，"
        f"纯重构跑 `--refactor`：{bad}"
    )


def test_versions_are_positive_ints_with_history():
    d = fv.load()["factors"]
    for k, v in d.items():
        assert isinstance(v["version"], int) and v["version"] >= 1, k
        assert any(h.get("version") == v["version"] for h in v["history"]), k


def test_digest_ignores_comments_and_docstrings_but_not_code(tmp_path):
    a = tmp_path / "a.py"
    a.write_text(
        '"""doc"""\nX = 1  # c\n\ndef f(x):\n    """d"""\n    return x + X\n',
        encoding="utf-8",
    )
    d0 = fv.source_digest(a)
    a.write_text(
        '"""changed doc"""\nX = 1  # other\n\n\ndef f(x):\n    return x + X\n',
        encoding="utf-8",
    )
    assert fv.source_digest(a) == d0
    a.write_text("X = 2\n\ndef f(x):\n    return x + X\n", encoding="utf-8")
    assert fv.source_digest(a) != d0


def test_digest_stable_across_python_versions(tmp_path):
    """同一源码摘要跨 Python 版本恒定（3.12+ 的空 type_params 字段剔除；本期望值 = 3.11 口径）。"""
    a = tmp_path / "a.py"
    a.write_text(
        "X = 1\n\ndef f(x):\n    return x + X\n\nclass C:\n    pass\n",
        encoding="utf-8",
    )
    assert fv.source_digest(a) == "10892e7b01a34668d017"


def test_tracked_covers_registry_and_thresholds():
    from custos.core import factors

    names = set(fv.tracked_files())
    assert "b1_thresholds" in names
    assert {
        v["module"].__name__.rsplit(".", 1)[1] for v in factors.registry().values()
    } <= names


def test_provenance_and_stock_pool_carry_versions():
    import argparse

    from custos.pipeline.screening import score_candidates as sc
    from custos.research import provenance as pv

    p = pv.build(argparse.Namespace(), unit="RX")
    assert p["factor_versions"]["b1_thresholds"] >= 1
    assert sc._factor_versions() == fv.versions()
