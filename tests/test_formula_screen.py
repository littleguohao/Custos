# -*- coding: utf-8 -*-
"""Tests for screening.formula_screen degrade paths (mocked TQ, no TdxW needed)."""
import json
from datetime import timedelta

import pytest

import formula_screen
import stock_names
from paths import cn_today


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
# 名称表非空＝ST 硬排除可用。2026-08-03 起 st_filter=unavailable 会让 status 脱离 ok
# 并追加降级原因（审计 B5），故聚焦其它降级路径的用例必须显式注入名称表，
# 否则断言会被"名称表不可用"这条无关降级污染。
NAMES = {"600000": "浦发银行", "000001": "平安银行"}


def test_tdxw_not_running_degrades_cleanly():
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(), stock_list=STOCKS, name_map=NAMES,
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
        "2026-07-21", registry=_registry(n=3), stock_list=STOCKS, name_map=NAMES,
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
    """名称是 ST 硬排除的唯一依据(enrich 用 `"ST" in name.upper()`)。

    2026-08-03 改：universe 阶段只读缓存并判时效，候选名称由 _refresh_candidate_names
    用东财 ulist 批量刷新。原实现只有 mootdx 在线源（2026-07 起持续失败），于是长期靠
    一份手动生成、永不更新、读取时不校验时效的缓存在跑——缓存非空就报 st_filter=ok，
    而旧缓存里新被 ST 的票名字还是正常的，照样通过硬排除。
    """

    def _write_cache(self, path, names, generated_at=None):
        payload = {"names": names, "source": "test", "count": len(names)}
        if generated_at:
            payload["generated_at"] = generated_at
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_fresh_cache_is_ok(self, tmp_path, monkeypatch):
        cache = tmp_path / "names.json"
        self._write_cache(cache, {"600000": "浦发银行", "000155": "*ST川化"},
                          generated_at=cn_today().isoformat())
        monkeypatch.setattr(formula_screen.stock_names, "CACHE", cache)
        diag: dict = {}
        nm = formula_screen._load_name_map(diag)
        assert nm["000155"] == "*ST川化"                     # 缓存保住 ST 识别
        assert diag["st_filter"] == "ok" and diag["name_map_age_days"] == 0

    def test_stale_cache_is_not_ok(self, tmp_path, monkeypatch, capsys):
        """陈旧缓存不得报 ok —— 这正是原实现的隐蔽失效。"""
        cache = tmp_path / "names.json"
        old_day = (cn_today() - timedelta(days=stock_names.NAME_MAP_MAX_AGE_DAYS + 5))
        self._write_cache(cache, {"600000": "浦发银行"}, generated_at=old_day.isoformat())
        monkeypatch.setattr(formula_screen.stock_names, "CACHE", cache)
        diag: dict = {}
        nm = formula_screen._load_name_map(diag)
        assert nm == {"600000": "浦发银行"}                   # 仍可用
        assert diag["st_filter"] == "stale"                  # 但不可信
        assert diag["name_map_age_days"] > stock_names.NAME_MAP_MAX_AGE_DAYS

    def test_legacy_flat_cache_is_stale(self, tmp_path, monkeypatch):
        """旧扁平格式没有 generated_at ⇒ 时效未知 ⇒ 不假定新鲜。"""
        cache = tmp_path / "names.json"
        cache.write_text(json.dumps({"600000": "浦发银行"}), encoding="utf-8")
        monkeypatch.setattr(formula_screen.stock_names, "CACHE", cache)
        diag: dict = {}
        assert formula_screen._load_name_map(diag) == {"600000": "浦发银行"}
        assert diag["st_filter"] == "stale"
        assert diag["name_map_age_days"] is None

    def test_total_unavailability_is_reported_not_silent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(formula_screen.stock_names, "CACHE", tmp_path / "missing.json")
        diag: dict = {}
        assert formula_screen._load_name_map(diag) == {}
        assert diag["st_filter"] == "unavailable"
        assert "名称缓存不可用" in capsys.readouterr().err

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
        # name_map_source 由候选名称刷新覆盖（autouse 替身报 test_stub）
        assert result["name_map_source"] == "test_stub"


class TestCandidateNameRefresh:
    """候选名称按需刷新：ST 判定真正依赖的一步，其覆盖率必须传导到 st_filter。"""

    def _run(self, resolver, hits="600000.SH"):
        return formula_screen.screen_formulas(
            "2026-07-21", registry=_registry(n=1), stock_list=["600000", "000155"],
            name_map={"600000": "旧名A", "000155": "旧名B"},
            call=lambda *a, **k: _ok({hits: {"UP3": ["1"]}}),
            running_check=lambda: True, name_resolver=resolver)

    def test_refreshed_name_overwrites_stale_one(self):
        """核心场景：一只新被 ST 的票，缓存里名字还是正常的，刷新后必须变 ST。"""
        r = self._run(lambda codes, **kw: (
            {"600000": "*ST浦发"},
            {"st_filter": "ok", "requested": 1, "name_map_size": 1, "missing_count": 0}))
        assert r["formulas"][0]["hits"][0]["name"] == "*ST浦发"

    def test_partial_coverage_degrades_status(self):
        r = self._run(lambda codes, **kw: (
            {}, {"st_filter": "partial", "requested": 1, "name_map_size": 0,
                 "missing_count": 1, "missing_codes": ["600000"]}))
        assert r["st_filter"] == "partial"
        assert r["status"] == "partial"
        assert "st_filter_partial" in r["degraded_reason"]
        assert r["candidate_name_coverage"]["missing_count"] == 1

    def test_stale_source_degrades_status(self):
        r = self._run(lambda codes, **kw: (
            {"600000": "浦发银行"},
            {"st_filter": "stale", "requested": 1, "name_map_size": 1,
             "missing_count": 0, "name_map_age_days": 99}))
        assert r["st_filter"] == "stale" and r["status"] == "partial"
        assert "st_filter_stale" in r["degraded_reason"]

    def test_unavailable_degrades_status(self):
        r = self._run(lambda codes, **kw: (
            {}, {"st_filter": "unavailable", "requested": 1, "name_map_size": 0,
                 "missing_count": 1}))
        assert r["st_filter"] == "unavailable" and r["status"] == "partial"
        assert "st_filter_unavailable" in r["degraded_reason"]

    def test_resolver_exception_does_not_break_screening(self):
        """名称刷新失败不得中断初筛——它是补强，不是前置条件。"""
        def boom(codes, **kw):
            raise RuntimeError("network down")
        r = self._run(boom)
        assert r["formulas"][0]["hits"][0]["name"] == "旧名A"   # 保留 universe 阶段的名字

    def test_no_candidates_skips_refresh(self):
        calls = []

        def spy(codes, **kw):
            calls.append(list(codes))
            return {}, {"st_filter": "ok"}
        formula_screen.screen_formulas(
            "2026-07-21", registry=_registry(n=1), stock_list=["600000"],
            name_map={"600000": "浦发银行"},
            call=lambda *a, **k: _ok({}),               # 零命中
            running_check=lambda: True, name_resolver=spy)
        assert calls == [], "没有候选就不该发请求"

    def test_only_candidate_codes_are_queried(self):
        """只查候选，不查全 universe——这是不触发限流的关键。"""
        seen = []

        def spy(codes, **kw):
            seen.append(sorted(codes))
            return {}, {"st_filter": "ok", "requested": len(codes)}
        formula_screen.screen_formulas(
            "2026-07-21", registry=_registry(n=1),
            stock_list=[f"60{i:04d}" for i in range(500)],
            name_map={},
            call=lambda *a, **k: _ok({"600000.SH": {"UP3": ["1"]}}),
            running_check=lambda: True, name_resolver=spy)
        assert seen == [["600000"]], f"应只查命中的 1 只，实际 {seen}"


def test_formulas_batched_to_avoid_tq_oom(monkeypatch):
    # 全市场分批:每次调用 stock_list ≤ FORMULA_BATCH,命中跨批合并(防 TQ 服务端 OOM)
    stocks = [f"60{i:04d}" for i in range(2500)]           # 2500 只 → 3 批(1000+1000+500)
    seen_batches = []

    def fake_call(method, params, timeout=15):
        seen_batches.append(len(params["stock_list"]))
        first = params["stock_list"][0]
        return _ok({f"{first}.SH": {"UP3": ["1"]}})        # 每批命中该批第一只

    monkeypatch.setattr(formula_screen.local_tdx_data, "normalize_code", lambda c: f"{c}.SH")
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(n=1), stock_list=stocks,
        name_map={c: f"股{c}" for c in stocks},
        call=fake_call, running_check=lambda: True,
    )
    assert result["status"] == "ok"
    assert seen_batches == [1000, 1000, 500]               # 分批上限被遵守
    hits = result["formulas"][0]["hits"]
    assert len(hits) == 3 and {h["code"] for h in hits} == {"600000", "601000", "602000"}


def test_partial_chunk_failure_keeps_hits_and_records(monkeypatch):
    stocks = [f"60{i:04d}" for i in range(2000)]           # 2 批;第 2 批失败
    calls = iter([_ok({"600000.SH": {"UP3": ["1"]}}), _err("timeout")])
    monkeypatch.setattr(formula_screen.local_tdx_data, "normalize_code", lambda c: f"{c}.SH")
    result = formula_screen.screen_formulas(
        "2026-07-21", registry=_registry(n=1), stock_list=stocks,
        call=lambda *a, **k: next(calls), running_check=lambda: True,
    )
    f0 = result["formulas"][0]
    assert len(f0["hits"]) == 1                            # 成功批的命中保留
    assert f0["error"] == "partial_chunks_failed"          # 失败显式记录(不静默)
    assert result["status"] == "partial"                     # 有批次失败 → 整体降级标记(诚实)
