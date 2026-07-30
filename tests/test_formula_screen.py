# -*- coding: utf-8 -*-
"""Tests for screening.formula_screen degrade paths (mocked TQ, no TdxW needed)."""
import json
import formula_screen


def _registry(n=2):
    return {
        "version": "test",
        "universe": {"exclude_st": True, "exclude_bj": True, "min_list_days": 60},
        "formulas": [
            {"id": f"F{i}", "tq_name": f"FAKE{i}", "args": "", "stock_period": "1d",
             "enabled": True, "category": "test", "note": ""}
            for i in range(n)
        ],
    }


def _ok(value):
    return {"ok": True, "value": value, "error": None}


def _err(code):
    return {"ok": False, "value": None, "error": {"code": code, "detail": "x"}}


STOCKS = ["600000", "000001"]


def test_tdxw_not_running_degrades_cleanly():
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(), stock_list=STOCKS,
        running_check=lambda: False,
    )
    assert result["status"] == "unavailable"
    assert result["degraded_reason"] == "tdxw_not_running"
    assert len(result["formulas"]) == 2
    assert all(f["hits"] == [] for f in result["formulas"])


def test_tq_error_id_recorded_and_partial():
    calls = iter([_ok({"600000.SH": {"UP3": ["0", "1"]}}), _err("tq_error")])
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(), stock_list=STOCKS,
        call=lambda *a, **k: next(calls), running_check=lambda: True,
    )
    assert result["status"] == "partial"
    assert result["formulas"][0]["error"] is None
    assert result["formulas"][1]["error"] == "tq_error"


def test_circuit_breaker_after_two_consecutive_failures():
    def always_fail(*a, **k):
        return _err("timeout")

    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(n=3), stock_list=STOCKS,
        call=always_fail, running_check=lambda: True,
    )
    errors = [f["error"] for f in result["formulas"]]
    assert errors == ["timeout", "timeout", "circuit_open_skipped"]
    assert result["status"] == "unavailable"
    assert result["degraded_reason"] == "all_formulas_failed"


def test_hit_extraction_last_bar_only():
    value = {
        "600000.SH": {"UP3": ["0", "1"]},   # 当日命中
        "000001.SZ": {"UP3": ["1", "0"]},   # 昨日命中当日未命中 → 不算
    }
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(n=1), stock_list=STOCKS,
        name_map={"600000": "浦发银行"},
        call=lambda *a, **k: _ok(value), running_check=lambda: True,
    )
    assert result["status"] == "ok"
    hits = result["formulas"][0]["hits"]
    assert hits == [{"code": "600000", "name": "浦发银行", "signal_date": "2026-07-21"}]


def test_empty_universe_degrades():
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(), stock_list=[],
        running_check=lambda: True,
    )
    assert result["status"] == "unavailable"
    # 降级原因必须写明"没扫市场、命中仅来自自选池",否则会被读成"今天没有好标的"
    assert result["degraded_reason"].startswith("universe_unavailable")
    assert "全市场公式初筛整段跳过" in result["degraded_reason"]
    assert "自选池" in result["degraded_reason"]


class TestBuildUniverseLocalFirst:
    """2026-07-30 事故:build_universe 只走 mootdx **在线**接口,在线一挂 universe 就空,
    screen_formulas 提前 return → 全市场公式初筛整段跳过,报告看似"只有 D 池"实则没扫市场。
    修复后必须**本地 vipdoc 优先**,仅在本地为空时回退在线。"""

    def test_local_vipdoc_preferred_online_not_called(self, monkeypatch):
        called = []
        monkeypatch.setattr(formula_screen.local_tdx_data, "list_local_vipdoc_codes",
                            lambda ashare_only=True: ["600000", "000001", "300750"])
        monkeypatch.setattr(formula_screen.local_tdx_data, "get_stock_list",
                            lambda *a, **k: called.append(1) or [])
        monkeypatch.setattr(formula_screen, "_load_name_map", lambda diag=None: {})
        diag: dict = {}
        codes, _ = formula_screen.build_universe({}, diag=diag)
        assert codes == ["600000", "000001", "300750"]
        assert called == [], "本地可用时不应再打在线接口"
        assert diag["universe_source"] == "vipdoc" and diag["universe_size"] == 3

    def test_falls_back_online_when_local_empty(self, monkeypatch):
        monkeypatch.setattr(formula_screen.local_tdx_data, "list_local_vipdoc_codes",
                            lambda ashare_only=True: [])
        monkeypatch.setattr(formula_screen.local_tdx_data, "get_stock_list",
                            lambda *a, **k: ["600519"])
        monkeypatch.setattr(formula_screen, "_load_name_map", lambda diag=None: {})
        diag: dict = {}
        codes, _ = formula_screen.build_universe({}, diag=diag)
        assert codes == ["600519"] and diag["universe_source"] == "online"

    def test_both_sources_raising_does_not_propagate(self, monkeypatch):
        def _boom(*a, **k):
            raise ValueError("'>' not supported between instances of 'NoneType' and 'int'")
        monkeypatch.setattr(formula_screen.local_tdx_data, "list_local_vipdoc_codes", _boom)
        monkeypatch.setattr(formula_screen.local_tdx_data, "get_stock_list", _boom)
        monkeypatch.setattr(formula_screen, "_load_name_map", lambda diag=None: {})
        diag: dict = {}
        codes, _ = formula_screen.build_universe({}, diag=diag)
        assert codes == [] and diag["universe_source"] == "unavailable"

    def test_bj_and_non_ashare_filtered(self, monkeypatch):
        monkeypatch.setattr(formula_screen.local_tdx_data, "list_local_vipdoc_codes",
                            lambda ashare_only=True: ["600000", "920123", "999999"])
        monkeypatch.setattr(formula_screen, "_load_name_map", lambda diag=None: {})
        codes, _ = formula_screen.build_universe({"exclude_bj": True})
        assert codes == ["600000"]


class TestNameMapCacheAndStFilter:
    """名称是 ST 硬排除的唯一依据(enrich 用 `"ST" in name.upper()`)。在线表挂掉必须回退缓存;
    彻底不可用必须显式报出,否则 `"ST" in ""` 为假 → ST 股静默通过硬排除。"""

    def test_online_success_writes_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(formula_screen, "NAME_MAP_CACHE", tmp_path / "names.json")
        monkeypatch.setattr(formula_screen.local_tdx_data, "get_stock_name_map",
                            lambda *a, **k: {"600000": "浦发银行"})
        diag: dict = {}
        nm = formula_screen._load_name_map(diag)
        assert nm == {"600000": "浦发银行"} and diag["name_map_source"] == "online"
        cached = json.loads((tmp_path / "names.json").read_text(encoding="utf-8"))
        assert cached["600000"] == "浦发银行"

    def test_online_failure_falls_back_to_cache(self, tmp_path, monkeypatch):
        cache = tmp_path / "names.json"
        cache.write_text(json.dumps({"600000": "浦发银行", "000155": "*ST川化"}), encoding="utf-8")
        monkeypatch.setattr(formula_screen, "NAME_MAP_CACHE", cache)
        monkeypatch.setattr(formula_screen.local_tdx_data, "get_stock_name_map", lambda *a, **k: {})
        diag: dict = {}
        nm = formula_screen._load_name_map(diag)
        assert nm["000155"] == "*ST川化"                     # 缓存保住 ST 识别
        assert diag["name_map_source"] == "cache" and diag["st_filter"] == "ok"

    def test_total_unavailability_is_reported_not_silent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(formula_screen, "NAME_MAP_CACHE", tmp_path / "missing.json")
        monkeypatch.setattr(formula_screen.local_tdx_data, "get_stock_name_map", lambda *a, **k: {})
        diag: dict = {}
        assert formula_screen._load_name_map(diag) == {}
        assert diag["st_filter"] == "unavailable"
        assert "ST 硬排除失效" in capsys.readouterr().err

    def test_screen_result_exposes_sources(self, monkeypatch):
        monkeypatch.setattr(formula_screen, "build_universe",
                            lambda cfg=None, diag=None: (diag.update(
                                {"universe_source": "vipdoc", "universe_size": 1,
                                 "name_map_source": "cache", "st_filter": "ok"}) or
                                (["600000"], {"600000": "浦发银行"})))
        result = formula_screen.screen_formulas(
            "2026-07-21", registry=_registry(n=1),
            call=lambda *a, **k: _ok({"600000.SH": {"UP3": ["0", "1"]}}),
            running_check=lambda: True)
        assert result["universe_source"] == "vipdoc" and result["st_filter"] == "ok"
        assert result["name_map_source"] == "cache"
