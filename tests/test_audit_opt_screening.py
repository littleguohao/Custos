# -*- coding: utf-8 -*-
"""审计【建议优化】批次 —— 07_tools/screening 域回归测试。

覆盖：边界与数值防御（NaN 绕过 J 门槛、未知模式名下标、天量阈值自旁路、
板块 2 字前缀误配、list_days 被 tail 截断、窗内新上市污染、summarize 分项键
只取首条、0.0 被真值判否）、性能（evaluate 尾窗口、theme map 候选集缩小、
kdj/macd/DKS 去重）、契约（weekly_j 顶层、技术分层阈值单一定义）。

全部为**行为断言**，不做源码文本匹配；字典按键比较。
"""
from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from research import backtest_factors as bt
from screening import candidate_table as ct
from screening import enrich_candidates as ec
from research import launch_point_study as lp
from research import run_bear_to_long_study as bl
from screening import score_candidates as sc
from factors import s_shape as ss


# ---------------------------------------------------------------- helpers

def _bars(n=140, close=10.0, vol=1000.0, end="2026-07-22"):
    return pd.DataFrame({
        "date": pd.date_range(end=end, periods=n, freq="B"),
        "open": [close] * n, "high": [close * 1.005] * n, "low": [close * 0.995] * n,
        "close": [close] * n, "volume": [vol] * n, "amount": [close * vol] * n,
    })


def _hits(*codes, date="2026-07-22"):
    return {"date": date, "status": "ok",
            "formulas": [{"id": "F1", "hits": [{"code": c, "name": "测试股"} for c in codes]}]}


def _run_enrich(monkeypatch, df_by_code, cfg=None, date="2026-07-22"):
    monkeypatch.setattr(ec, "build_stock_theme_map", lambda **k: ({}, True))
    return ec.enrich(date, hits_data=_hits(*df_by_code, date=date),
                     ohlcv_loader=lambda c: df_by_code[c].copy(),
                     index_loader=lambda: None,
                     universe_cfg=cfg if cfg is not None else {"j_low_required": True})


# ---------------------------------------------------------------- 1. NaN 绕过 J 门槛

def test_j_gate_rejects_nan_j(monkeypatch):
    """J=NaN 时 `NaN>=13` 为 False → 旧实现把"没有 J"当成"J<13 满足买点"放行。"""
    monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": float("nan"),
                                               "j_prev": float("nan")})
    r = _run_enrich(monkeypatch, {"600000": _bars()})
    assert r["candidates"] == []
    assert r["excluded"] and r["excluded"][0]["reason"].startswith("j_not_low")


def test_j_gate_still_accepts_low_j_and_rejects_high(monkeypatch):
    monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 5.0, "j_prev": 4.0})
    assert len(_run_enrich(monkeypatch, {"600000": _bars()})["candidates"]) == 1
    monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 50.0, "j_prev": 49.0})
    assert _run_enrich(monkeypatch, {"600000": _bars()})["candidates"] == []


def test_perfect_b1_fit_nan_j_scores_zero():
    """贴合度的 J 深度分同样不得被 NaN 蒙过（NaN<0/NaN<7/NaN<13 全 False → 已是 0，钉住）。"""
    df = _bars(200)
    fit = ec.compute_perfect_b1_fit(df, daily_j=float("nan"), zx={"available": False},
                                    pullback={"available": False})
    assert fit["components"]["j_depth"]["points"] == 0.0


# ---------------------------------------------------------------- 2. 未知模式名直接下标

def test_render_table_unknown_pattern_key_no_crash():
    """上游新增一个 patterns 键（或脏数据）不得让整张备选表 KeyError。"""
    pool = {"status": "ok", "bucket_counts": {"A": 1},
            "candidates": [{"code": "600000", "name": "测试股", "bucket": "A",
                            "patterns": {"bbi_above": True, "brand_new_pattern": True},
                            "score_detail": {"total": 80, "technical_score": 70}}]}
    text = ct.render_table(pool, "2026-07-22")
    assert "brand_new_pattern" in text          # 未知值留痕（原样落表）
    assert "BBI上" in text                       # 已知标签仍翻译


def test_score_candidate_unknown_wave_type_no_crash():
    """wave_type 出现未登记值时 `{...}[wave_type]` 会 KeyError 打挂整段打分。"""
    cand = {"code": "600000", "name": "测试股",
            "patterns": {}, "wave": {"wave_type": "brand_new_wave", "available": True},
            "stop_loss_ref": {"price": 9.0, "basis": "近10日最低价"}}
    out = sc.score_candidate(cand, None, "中性")
    assert out["bucket"] in ("A", "B", "C", "D")
    assert any("brand_new_wave" in r for r in out["entry_reason"])


# ---------------------------------------------------------------- 3. 天量 or 分支含自身

def _dist_closes():
    """构造 ①顶部天量大阴 的全部前置条件（阴线前 10 日加速 +34%），只留"天量"可调。"""
    closes = [10.0] * 40
    closes += [10.0 * (1.03 ** i) for i in range(1, 21)]      # 40..59：连续加速
    closes.append(closes[-1] * 0.93)                          # 末根 -7% 大阴
    return closes


def test_distribution_huge_volume_requires_ma20_threshold():
    """"或 20 日量新高（窗口含自身）"恒等于"当日是窗口最大量"，旁路了 2×MA20。"""
    closes = _dist_closes()
    n = len(closes)
    vols = [1000.0] * n
    vols[-1] = 1400.0        # 是 20 日最大量，但只有 1.4×MA20 < 2.0×MA20
    df = pd.DataFrame({
        "date": pd.date_range(end="2026-07-22", periods=n, freq="B"),
        "open": [c * 1.001 for c in closes],                  # 收 < 开 → 阴线
        "high": [c * 1.002 for c in closes], "low": [c * 0.999 for c in closes],
        "close": closes, "volume": vols, "amount": [0.0] * n,
    })
    r = ec.detect_distribution(df, "600000")
    assert r["available"]
    assert r["signals"]["top_huge_vol_bear"]["hit"] is False   # 未达 2×MA20 → 不算天量

    df2 = df.copy()
    df2["volume"] = vols[:-1] + [3000.0]                       # 3×MA20 → 真天量
    r2 = ec.detect_distribution(df2, "600000")
    assert r2["signals"]["top_huge_vol_bear"]["hit"] is True


# ---------------------------------------------------------------- 4. fund_flow 2 字前缀误配

_FF = {"available": True, "by_code": {},
       "sectors": [{"name": "工程建设", "main_net_inflow": 5e8}]}


def test_fund_flow_sector_prefix_false_match_rejected():
    """「工程建设」与「工程机械」是两个板块，2 字前缀却互相命中并白送资金意图 +2。"""
    r = ec.fund_flow_of("600000", "工程机械", _FF)
    assert r["available"] is True
    assert r["sector_matched"] is None
    assert r["sector_inflow_positive"] is False


def test_fund_flow_sector_real_match_kept():
    exact = {"available": True, "by_code": {},
             "sectors": [{"name": "光伏设备", "main_net_inflow": 5e8}]}
    assert ec.fund_flow_of("600000", "光伏设备", exact)["sector_matched"] == "光伏设备"
    contained = {"available": True, "by_code": {},
                 "sectors": [{"name": "光伏", "main_net_inflow": 5e8}]}
    assert ec.fund_flow_of("600000", "光伏设备", contained)["sector_matched"] == "光伏"
    wider = {"available": True, "by_code": {},
             "sectors": [{"name": "光伏设备及组件", "main_net_inflow": 5e8}]}
    assert ec.fund_flow_of("600000", "光伏设备", wider)["sector_matched"] == "光伏设备及组件"


def test_fund_flow_false_match_does_not_grant_capital_point():
    """误配直接改分层（资金意图 +2 → 中/强档）——这是它必须精确的原因。"""
    cand = {"code": "600000", "patterns": {}, "sector": "工程机械",
            "fund_flow": ec.fund_flow_of("600000", "工程机械", _FF)}
    level, score, detail = sc.capital_intent_strength(cand)
    assert detail["fund_flow_inflow"]["hit"] is False
    assert detail["fund_flow_inflow"]["points"] == 0


# ---------------------------------------------------------------- 5. list_days 被 tail 截断

def test_list_days_marks_censored_when_loader_truncates(monkeypatch):
    monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 5.0, "j_prev": 4.0})
    full = _run_enrich(monkeypatch, {"600000": _bars(ec.OHLCV_LOAD_BARS)})
    cand = full["candidates"][0]
    assert cand["list_days"] == ec.OHLCV_LOAD_BARS
    assert cand["list_days_exact"] is False          # 只是"≥260"，不是真实上市日数
    assert cand["list_days_basis"] == "loaded_bars_censored"

    short = _run_enrich(monkeypatch, {"600000": _bars(100)})
    cand2 = short["candidates"][0]
    assert cand2["list_days"] == 100
    assert cand2["list_days_exact"] is True
    assert cand2["list_days_basis"] == "loaded_bars"


# ---------------------------------------------------------------- 6. window_return 窗内新上市

def test_window_return_excludes_new_listing_when_asked():
    dates = ["2026-03-02", "2026-03-03", "2026-03-04"]
    closes = [10.0, 15.0, 18.0]
    # 窗口从 2026-01-01 开始，但这只票第一根 K 线在 3 月 → 窗内新上市
    assert lp.window_return(dates, closes, "2026-01-01", "2026-06-30") == pytest.approx(0.8)
    assert lp.window_return(dates, closes, "2026-01-01", "2026-06-30",
                            exclude_new_listing=True) is None
    # 老票（窗前已有 K 线）不受影响
    dates2 = ["2025-12-01"] + dates
    closes2 = [9.0] + closes
    assert lp.window_return(dates2, closes2, "2026-01-01", "2026-06-30",
                            exclude_new_listing=True) == pytest.approx(0.8)


def test_window_return_new_listing_flag_default_off():
    """默认关：不动既有研究口径（打开与否是策略 owner 的决定）。"""
    assert lp.window_return(["2026-03-02", "2026-03-03"], [10.0, 12.0],
                            "2026-01-01", "2026-06-30") == pytest.approx(0.2)


# ---------------------------------------------------------------- 7. summarize 分项键只取首条

def test_summarize_collects_component_keys_from_all_records():
    """第 1 条没有 c_pivot 时，旧实现把后续所有记录的 c_pivot 静默丢掉。"""
    recs = [
        {"s_star": 80.0, "suggestion": "可买", "ret10": 0.05, "mfe10": 0.08, "mae10": -0.01},
        {"s_star": 30.0, "suggestion": "不买", "ret10": -0.03, "mfe10": 0.01, "mae10": -0.05,
         "c_pivot": 5.0},
        {"s_star": 50.0, "suggestion": "观望", "ret10": 0.01, "mfe10": 0.02, "mae10": -0.02,
         "c_vcp": 0.0},
    ]
    out = bt.summarize(recs, horizon=10)
    assert set(out["by_component_hit"]) >= {"c_pivot", "c_vcp"}
    assert out["by_component_hit"]["c_pivot"]["hit"]["n"] == 1
    assert out["by_component_hit"]["c_vcp"]["hit"]["n"] == 0


# ---------------------------------------------------------------- 8. 0.0 被真值判否

def test_perfect_b1_fit_zero_score_recorded_in_contrib():
    """贴合度 0 分是"算过且为 0"，与"没算"不是一回事；旧 `if fit:` 把它当缺失。"""
    cand = {"code": "600000", "patterns": {"bbi_above": True},
            "perfect_b1_fit": {"score": 0.0, "max_score": 8, "components": {}}}
    score, level, contrib = sc.technical_score(cand)
    assert "perfect_b1_fit" in contrib
    assert contrib["perfect_b1_fit"] == 0.0
    assert score == 25                       # 分值不变（0 分就是不加分）


def test_perfect_b1_fit_missing_not_recorded():
    cand = {"code": "600000", "patterns": {"bbi_above": True}}
    _, _, contrib = sc.technical_score(cand)
    assert "perfect_b1_fit" not in contrib


# ---------------------------------------------------------------- 9. evaluate 尾窗口

def _wavy(n=320):
    closes = [10.0 + 3 * math.sin(i / 9.0) + i * 0.02 for i in range(n)]
    vols = [1000.0 + 300 * math.sin(i / 5.0) for i in range(n)]
    return pd.DataFrame({
        "date": pd.date_range(end="2026-07-22", periods=n, freq="B"),
        "open": [c * 0.998 for c in closes], "high": [c * 1.02 for c in closes],
        "low": [c * 0.98 for c in closes], "close": closes, "volume": vols,
        "amount": [c * v for c, v in zip(closes, vols)],
    })


@pytest.mark.parametrize("scorer_name", ["s_shape", "s_reversal", "momentum", "reversal_quality"])
def test_evaluate_gate_window_matches_full_prefix(scorer_name):
    """尾窗口只为省时间，绝不许改因子值：预热足够时逐字段与全前缀一致。"""
    bars = {"600000": _wavy()}
    scorer = bt.SCORERS[scorer_name]
    full = bt.evaluate(bars, horizons=(5, 10), min_bars=130, scorer=scorer)
    win = bt.evaluate(bars, horizons=(5, 10), min_bars=130, scorer=scorer,
                      gate_window=bt.GATE_WINDOW_SAFE)
    assert len(full) == len(win) and full
    for a, b in zip(full, win):
        assert set(a) == set(b)
        for k in a:
            assert a[k] == pytest.approx(b[k]) if isinstance(a[k], float) else a[k] == b[k]


def test_evaluate_gate_window_zero_is_full_prefix():
    bars = {"600000": _wavy(200)}
    a = bt.evaluate(bars, horizons=(5,), min_bars=130)
    b = bt.evaluate(bars, horizons=(5,), min_bars=130, gate_window=0)
    assert [r["date"] for r in a] == [r["date"] for r in b]
    assert [r["s_star"] for r in a] == [r["s_star"] for r in b]


def test_evaluate_gate_window_respects_entry_gate_slice():
    """entry_gate 也只看尾窗口——预热足时判定不变。"""
    bars = {"600000": _wavy()}
    gate = bt.ENTRY_GATES["j_low"]
    full = bt.evaluate(bars, horizons=(5,), min_bars=130, entry_gate=gate)
    win = bt.evaluate(bars, horizons=(5,), min_bars=130, entry_gate=gate,
                      gate_window=bt.GATE_WINDOW_SAFE)
    assert [r["date"] for r in full] == [r["date"] for r in win]


def test_evaluate_tail_window_does_not_grow_slice():
    """行为证据：尾窗口下传给 scorer 的切片长度有界（O(n·W) 而非 O(n²)）。"""
    seen: list[int] = []

    def probe(df, code):
        seen.append(len(df))
        return {"score": 1.0, "suggestion": "可买", "aux": {}, "components": {}}

    bt.evaluate({"600000": _wavy(300)}, horizons=(5,), min_bars=130, scorer=probe,
                gate_window=150)
    assert max(seen) <= 150
    seen.clear()
    bt.evaluate({"600000": _wavy(300)}, horizons=(5,), min_bars=130, scorer=probe)
    assert max(seen) > 150          # 默认仍是全前缀（向后兼容）


# ---------------------------------------------------------------- 10. theme map 候选集缩小

_THEMES = {"themes": [{"theme_id": "T1", "theme_name": "光伏", "semantic_tags": ["光伏"]},
                      {"theme_id": "T2", "theme_name": "军工", "semantic_tags": ["军工"]}]}


def test_build_stock_theme_map_codes_filter_same_result(monkeypatch):
    tags = {f"{600000 + i:06d}": (["光伏"] if i % 2 == 0 else ["军工"]) for i in range(200)}
    monkeypatch.setattr(ec, "_load_json", lambda p, d: _THEMES if "code_map" in str(p) else d)
    monkeypatch.setattr(ec.concept_tags, "load_tags", lambda: tags)
    monkeypatch.setattr(ec.concept_tags, "load_tags_meta", lambda: ({}, {}))

    full, ok_full = ec.build_stock_theme_map()
    want = {"600000", "600001", "600002"}
    part, ok_part = ec.build_stock_theme_map(codes=want)
    assert ok_full is True and ok_part is True
    assert set(part) == want
    for c in want:
        assert set(part[c]) == set(full[c])
        assert part[c]["theme_id"] == full[c]["theme_id"]
        assert part[c]["sector"] == full[c]["sector"]


def test_build_stock_theme_map_codes_filter_avoids_full_market_scan(monkeypatch):
    tags = {f"{600000 + i:06d}": ["光伏"] for i in range(500)}
    monkeypatch.setattr(ec, "_load_json", lambda p, d: _THEMES if "code_map" in str(p) else d)
    monkeypatch.setattr(ec.concept_tags, "load_tags", lambda: tags)
    monkeypatch.setattr(ec.concept_tags, "load_tags_meta", lambda: ({}, {}))
    calls: list[int] = []
    orig = ec._match_theme_tags
    monkeypatch.setattr(ec, "_match_theme_tags",
                        lambda st, sem: (calls.append(1), orig(st, sem))[1])

    ec.build_stock_theme_map(codes={"600000", "600001"})
    assert len(calls) <= 2 * len(_THEMES["themes"])      # 只扫候选，不扫全市场


def test_build_stock_theme_map_codes_no_tag_hit_keeps_concept_path(monkeypatch):
    """候选一只都没匹配上时不得偷偷回退 880 反查（那是"概念标签不可用"才走的路）。"""
    tags = {f"{600000 + i:06d}": ["光伏"] for i in range(50)}
    monkeypatch.setattr(ec, "_load_json", lambda p, d: _THEMES if "code_map" in str(p) else d)
    monkeypatch.setattr(ec.concept_tags, "load_tags", lambda: tags)
    monkeypatch.setattr(ec.concept_tags, "load_tags_meta", lambda: ({}, {}))
    called = []
    monkeypatch.setattr(ec, "latest_tq_sector_map", lambda: called.append(1) or {})

    m, ok = ec.build_stock_theme_map(codes={"999998"})
    assert m == {} and ok is True
    assert called == []


# ---------------------------------------------------------------- 11. kdj/macd/DKS 去重与一致性

def test_dks_single_definition_matches_zhixing_state():
    """DKS 曾有两份实现（technical_monitor.zhixing_state 与 perfect_b1_fit 内联）。"""
    from indicators import dks_series
    from technical_monitor import zhixing_state
    df = _wavy(200)
    zx = zhixing_state(df)
    # 共享实现现在直取 `indicators.dks_series` —— 原先经 `enrich_candidates` 顶层
    # 偶然再导出，2026-08-08 死代码清理时随 `_j_series` 一并删掉。
    series = dks_series(df["close"].astype(float).reset_index(drop=True))
    assert zx["available"]
    assert round(float(series.iloc[-1]), 4) == zx["dks"]


def test_perfect_b1_fit_dks_uses_shared_series():
    from indicators import dks_series
    df = _wavy(200)
    fit = ec.compute_perfect_b1_fit(df, daily_j=5.0, zx={"available": False},
                                    pullback={"available": False})
    series = dks_series(df["close"].astype(float).reset_index(drop=True))
    assert fit["components"]["dks_rising"]["dks"] == pytest.approx(float(series.iloc[-1]))


def test_repair_signals_kdj_injection_identical():
    """复用已算好的 KDJ 不得改变结果（去重只省时间）。"""
    from technical_monitor import kdj as _kdj
    df = _wavy(160)
    idx = _wavy(160)
    a = ec.check_repair_signals(df, idx)
    b = ec.check_repair_signals(df, idx, kdj_state=_kdj(df))
    assert set(a) == set(b)
    assert a["signals"] == b["signals"]
    assert a["detail"]["j_turn_up"] == b["detail"]["j_turn_up"]


def test_perfect_b1_fit_macd_injection_identical():
    df = _wavy(200)
    mt = ec.check_macd_technics(df)
    zx = {"available": False}
    pull = {"available": False}
    a = ec.compute_perfect_b1_fit(df, 5.0, zx, pull)
    b = ec.compute_perfect_b1_fit(df, 5.0, zx, pull, macd_state=mt)
    assert set(a["components"]) == set(b["components"])
    assert a["components"]["macd_above_zero"] == b["components"]["macd_above_zero"]
    assert a["score"] == b["score"]


def test_compute_metrics_computes_daily_kdj_once(monkeypatch):
    """日线 KDJ 曾被重复算：compute_metrics + check_repair_signals 各一次。"""
    from technical_monitor import kdj as _kdj
    calls = []

    def counting(df, *a, **k):
        calls.append(len(df))
        return _kdj(df, *a, **k)

    monkeypatch.setattr(ec, "kdj", counting)
    df = _wavy(200)
    ec.compute_metrics(df, None, code="600000")
    daily = [n for n in calls if n == len(df)]
    assert len(daily) == 1, f"日线 KDJ 被算了 {len(daily)} 次"


# ---------------------------------------------------------------- 12. firings_reusable 只读头部

class _Args:
    entry_filter = "j_low"
    feature_scores = "none"
    delisted_ret = -1.0
    sector_features = False
    style_features = False
    trade_sim = True
    pit_features = False
    pit_visible_same_day = False
    pit_ledger = ""
    stop_pct = 8.0
    bbi_consec = 2
    gate_window = 120


def _firings_payload(n_days=3, extra=None):
    head = {"start": "2026-01-01", "end": "2026-06-30",
            "entry_filter": "j_low", "feature_scores": "none", "delisted_ret": -1.0,
            "universe": "sdata", "sector_features": False, "style_features": False,
            "trade_sim": True, "pit_features": False, "pit_visible_same_day": False,
            "pit_ledger": "", "stop_pct": 8.0, "bbi_consec": 2,
            "n_signal_days": n_days, "rank_score": "none", "shard": ""}
    head.update(extra or {})
    head["records"] = [{"code": "600000", "days": [["2026-02-02", 1.0]] * n_days}]
    return json.dumps(head, ensure_ascii=False)


def test_firings_reusable_accepts_complete_file(tmp_path):
    f = tmp_path / "firings.json"
    f.write_text(_firings_payload(), encoding="utf-8")
    assert bl.firings_reusable(f, _Args()) is True


def test_firings_reusable_rejects_truncated(tmp_path, capsys):
    f = tmp_path / "firings.json"
    f.write_text(_firings_payload()[:-40], encoding="utf-8")
    assert bl.firings_reusable(f, _Args()) is False


def test_firings_reusable_rejects_empty(tmp_path):
    f = tmp_path / "firings.json"
    f.write_text(_firings_payload(n_days=0), encoding="utf-8")
    assert bl.firings_reusable(f, _Args()) is False


def test_firings_reusable_rejects_param_mismatch(tmp_path):
    f = tmp_path / "firings.json"
    f.write_text(_firings_payload(extra={"entry_filter": "reversal_k"}), encoding="utf-8")
    assert bl.firings_reusable(f, _Args()) is False


def test_firings_reusable_does_not_parse_records_body(tmp_path, monkeypatch):
    """大 JSON 只为判定"能否复用"而全量解析是纯浪费：头部已带 n_signal_days。"""
    f = tmp_path / "firings.json"
    f.write_text(_firings_payload(), encoding="utf-8")
    sizes: list[int] = []
    real = json.loads
    monkeypatch.setattr(bl.json, "loads", lambda s, *a, **k: (sizes.append(len(s)),
                                                             real(s, *a, **k))[1])
    assert bl.firings_reusable(f, _Args()) is True
    assert sizes, "应至少解析一次头部"
    assert max(sizes) < f.stat().st_size, "不得把整份 records 交给 json.loads"


# ---------------------------------------------------------------- 13. weekly_j 契约

def test_weekly_j_reaches_candidate_top_level(monkeypatch):
    monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 5.0, "j_prev": 4.0})
    r = _run_enrich(monkeypatch, {"600000": _bars(200)})
    cand = r["candidates"][0]
    assert "weekly_j" in cand and "weekly_j_low" in cand
    entry = sc.score_candidate(cand, None, "中性")
    assert "weekly_j" in entry and "weekly_j_low" in entry


def test_weekly_j_state_available_flag_is_namespaced(monkeypatch):
    """weekly_j_state 的裸 available 键**不得**污染候选顶层（会被误读成"候选可用"）。"""
    st = ec.weekly_j_state(_wavy(200))
    assert "weekly_j_available" in st
    assert st["weekly_j_available"] == st["available"]      # 直接调用方仍读得到旧键
    monkeypatch.setattr(ec, "kdj", lambda df: {"available": True, "j": 5.0, "j_prev": 4.0})
    cand = _run_enrich(monkeypatch, {"600000": _bars(200)})["candidates"][0]
    assert "available" not in cand                          # 顶层不再有裸 available
    assert cand["weekly_j_available"] is True


# ---------------------------------------------------------------- 15. 技术分层阈值单一定义

def test_technical_level_thresholds_single_definition():
    """两条打分路径各有一套阈值，必须都来自命名常量、可一处改。"""
    assert sc.TECH_STRONG_FALLBACK == 60 and sc.TECH_MID_FALLBACK == 30
    # 回退路径（无 s_shape）：60/30
    def _cand(total_patterns):
        return {"code": "600000", "patterns": total_patterns}
    lo = sc.technical_score(_cand({"bbi_above": True}))          # 25 分
    assert lo[1] == "弱"
    mid = sc.technical_score(_cand({"bbi_above": True, "j_low": True}))   # 45 分
    assert mid[1] == "中"
    # s_shape 路径：65/40（sstar_level 单一定义）
    assert ss.sstar_level(64.9) == "中" and ss.sstar_level(65.0) == "强"
    assert ss.sstar_level(39.9) == "弱" and ss.sstar_level(40.0) == "中"
    out = sc.technical_score({"code": "600000", "patterns": {},
                              "s_shape": {"available": True, "s_star": 62.0,
                                          "components": {}, "suggestion": "观望"}})
    assert out[1] == "中"        # 62 在 s_shape 路径是"中"，在回退路径会是"强"


def test_build_stock_industry_map_only_sub_industry(monkeypatch):
    """每股 → TDX 细分行业：只收 sub_industry 板块,一股多行业时先到先得。"""
    fake = {"sectors": [
        {"code": "881386.SH", "name": "全国性银行", "category": "sub_industry",
         "stocks": ["601939.SH", "600030.SH"]},
        {"code": "881352.SH", "name": "IT设备", "category": "sub_industry",
         "stocks": ["000977.SZ"]},
        {"code": "880951.SH", "name": "央企改革", "category": "concept",
         "stocks": ["601939.SH"]},          # 概念板块不得混入行业口径
        {"code": "881999.SH", "name": "", "category": "sub_industry",
         "stocks": ["999999.SZ"]},          # 无名板块跳过
    ]}
    monkeypatch.setattr(ec, "latest_tq_sector_map", lambda: fake)
    m = ec.build_stock_industry_map()
    assert m == {"601939": "全国性银行", "600030": "全国性银行", "000977": "IT设备"}


def test_build_stock_industry_map_never_raises(monkeypatch):
    monkeypatch.setattr(ec, "latest_tq_sector_map",
                        lambda: (_ for _ in ()).throw(RuntimeError("disk gone")))
    assert ec.build_stock_industry_map() == {}
