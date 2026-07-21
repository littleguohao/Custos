# -*- coding: utf-8 -*-
"""Tests for 00_governance/SCREEN_FORMULA_REGISTRY.json shape and invariants."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REGISTRY = BASE / "00_governance" / "SCREEN_FORMULA_REGISTRY.json"


def _load():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_registry_exists_and_has_top_level_keys():
    data = _load()
    assert data["version"]
    assert isinstance(data["universe"], dict)
    assert isinstance(data["formulas"], list) and data["formulas"]


def test_universe_filters():
    u = _load()["universe"]
    assert u["exclude_st"] is True
    assert u["exclude_bj"] is True
    assert int(u["min_list_days"]) >= 60


def test_enabled_formulas_well_formed():
    formulas = _load()["formulas"]
    enabled = [f for f in formulas if f.get("enabled")]
    assert enabled, "至少需要一个 enabled 公式"
    ids = [f["id"] for f in formulas]
    assert len(ids) == len(set(ids)), "公式 id 必须唯一"
    for f in enabled:
        assert f.get("tq_name"), f"{f['id']} 缺 tq_name"
        assert f.get("stock_period") == "1d"
        assert "args" in f and "category" in f


def test_b1_reversal_k_placeholder_disabled():
    formulas = {f["id"]: f for f in _load()["formulas"]}
    b1 = formulas.get("B1_REVERSAL_K")
    assert b1 is not None
    assert b1["enabled"] is False
    assert b1["tq_name"] == "TODO_CLIENT_FORMULA"
    assert "客户端" in b1.get("note", "")
