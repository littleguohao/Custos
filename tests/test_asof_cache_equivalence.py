# -*- coding: utf-8 -*-
"""v0.175 as-of 去重缓存的**逐位等价**钉测（score_return_study.asof_candidate）。

背景见 score_return_study 模块「as-of 技术分」段头注释：per-trade 全管线重算
的优化只有「精确内容键去重」合法（tail(260) 重新播种 ⇒ 全序列点查询与 live
口径**不逐位相等**，合成数据 84 窗口实测 MACD/KDJ/ADX 全不一致）。本文件钉住：

  ① index as-of 快速路径（bisect）与旧布尔掩码路径选出逐位相同的帧
     （升序逐日断言 + 越界空帧 + 未排序回退断言）；
  ② asof_candidate 缓存命中 == 无缓存原路（真跑 compute_metrics，逐字段深对比，
     NaN 视同相等），命中时 compute_metrics 不再被调；
  ③ run_study 全流程「开缓存 vs 无缓存原路」逐笔输出完全一致
     （默认 asof_technical_score 路径与 winner_factor_study.panel_hook 路径各一臂）；
  ④ 跨轮复算去重生效：同输入第二轮 run_study 的 compute_metrics 调用数为 0
     （resonance3「两臂 + gate④ 技术分腿」复算模式的缩影），且逐笔输出与首轮一致；
  ⑤ 防「空==空」假绿：合成数据上真出交易、真发生缓存命中、真算出非零技术分。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from custos.pipeline.screening import enrich_candidates as ec
from custos.pipeline.screening import score_candidates as sc
from custos.research import score_return_study as srs
from custos.research import winner_factor_study as wfs

CODES = ["600000", "000001", "300750"]


def _bars(n: int = 700, seed: int = 7) -> pd.DataFrame:
    """确定性随机游走日线（datetime64 date + amount，与 vipdoc 加载形状一致）。

    compute_metrics 的周线 resample 依赖 datetime64 的 date 列与 amount 列，
    缺了会走不到真路径（形同空转）。
    """
    rng = np.random.default_rng(seed)
    close = np.maximum(10 + np.cumsum(rng.normal(0, 0.25, n)), 1.0)
    high = close + np.abs(rng.normal(0, 0.12, n))
    low = np.maximum(close - np.abs(rng.normal(0, 0.12, n)), 0.5)
    open_ = low + (high - low) * rng.random(n)
    vol = np.abs(rng.normal(1e6, 2e5, n))
    return pd.DataFrame(
        {
            "date": pd.date_range("2021-01-01", periods=n, freq="B"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "amount": vol * close,
        }
    )


_DF = {c: _bars(700, s) for c, s in zip(CODES, (7, 11, 13))}
_INDEX = _bars(700, 99)
_REGIME = {str(d)[:10]: "做多" for d in _INDEX["date"]}


@pytest.fixture(autouse=True)
def _clear_caches():
    srs._CAND_CACHE.clear()
    srs._IDX_DATE_CACHE.clear()
    yield
    srs._CAND_CACHE.clear()
    srs._IDX_DATE_CACHE.clear()


def _patch_loader(monkeypatch):
    from custos.datasource.local_tdx import local_tdx_data

    # 每次调用返回新对象（.copy()）⇒ 跨轮命中只能靠内容键，不是对象身份
    monkeypatch.setattr(
        local_tdx_data, "get_ohlcv_table", lambda code, count=0: _DF[code].copy()
    )


def _counting_cm(monkeypatch) -> list:
    """ec.compute_metrics 计数包装；返回 [(code, 信号日)] 调用流水。"""
    calls: list = []
    orig = ec.compute_metrics

    def wrap(df, index_df, code="", df_long=None):
        calls.append((code, str(df["date"].iloc[-1])[:10]))
        return orig(df, index_df, code=code, df_long=df_long)

    monkeypatch.setattr(ec, "compute_metrics", wrap)
    return calls


def _deep_eq(a, b, path: str = "root") -> None:
    """逐字段深对比（float NaN 视同相等）；不等即 AssertionError 带路径。"""
    if isinstance(a, float) and isinstance(b, float):
        assert a == b or (math.isnan(a) and math.isnan(b)), f"{path}: {a!r} != {b!r}"
        return
    if isinstance(a, dict) and isinstance(b, dict):
        assert list(a) == list(b), f"{path}: 键不一致 {list(a)} != {list(b)}"
        for k in a:
            _deep_eq(a[k], b[k], f"{path}.{k}")
        return
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        assert len(a) == len(b), f"{path}: 长度 {len(a)} != {len(b)}"
        for j, (x, y) in enumerate(zip(a, b)):
            _deep_eq(x, y, f"{path}[{j}]")
        return
    assert a == b, f"{path}: {a!r} != {b!r}"


# ---- ① index as-of 快速路径 == 旧布尔掩码路径 ----


def _old_index_asof(index_full: pd.DataFrame, entry_date: str) -> pd.DataFrame:
    """v0.175 前的旧口径原样（布尔掩码 + tail），作对照。"""
    idx_dates = index_full["date"].astype(str).str[:10]
    return (
        index_full[idx_dates <= entry_date]
        .tail(ec.OHLCV_LOAD_BARS)
        .reset_index(drop=True)
    )


class TestIndexAsofFastPath:
    def test_sorted_matches_mask_per_day(self):
        dates = _INDEX["date"].astype(str).str[:10].tolist()
        for d in dates[::13] + [dates[-1]]:
            fast = srs._index_asof(_INDEX, d)
            slow = _old_index_asof(_INDEX, d)
            pd.testing.assert_frame_equal(fast, slow)

    def test_out_of_range_dates(self):
        # 信号日早于全部指数日 ⇒ 空帧；晚于全部 ⇒ 最后 260 根
        for d in ("1990-01-01", "2099-12-31"):
            fast = srs._index_asof(_INDEX, d)
            slow = _old_index_asof(_INDEX, d)
            pd.testing.assert_frame_equal(fast, slow)

    def test_unsorted_falls_back_to_mask(self):
        shuffled = _INDEX.sample(frac=1, random_state=3).reset_index(drop=True)
        dates, sorted_ok = srs._index_dates(shuffled)
        assert not sorted_ok, "打乱后应判为非升序走旧路径"
        d = str(_INDEX["date"].iloc[400])[:10]
        pd.testing.assert_frame_equal(
            srs._index_asof(shuffled, d), _old_index_asof(shuffled, d)
        )

    def test_dates_cached_per_frame_object(self):
        d1, _ = srs._index_dates(_INDEX)
        d2, _ = srs._index_dates(_INDEX)
        assert d1 is d2, "同一帧对象的日期列应只字符串化一次"
        assert d1 == _INDEX["date"].astype(str).str[:10].tolist()


# ---- ② asof_candidate 缓存命中 == 无缓存原路 ----


class TestAsofCandidateCache:
    def test_hit_returns_same_object_and_skips_compute(self, monkeypatch):
        calls = _counting_cm(monkeypatch)
        df, code, i = _DF["600000"], "600000", 500
        c1 = srs.asof_candidate(df, _INDEX, i, code)
        assert calls == [("600000", str(df["date"].iloc[i])[:10])]
        c2 = srs.asof_candidate(df, _INDEX, i, code)
        assert c2 is c1, "命中应返回同一份 cand"
        assert len(calls) == 1, "命中不应重算 compute_metrics"
        # 不同信号日 ⇒ 不同键 ⇒ 真重算（防「全都命中」假绿）
        c3 = srs.asof_candidate(df, _INDEX, i - 100, code)
        assert c3 is not c1 and len(calls) == 2

    def test_cached_matches_uncached_bitwise(self):
        df, code = _DF["000001"], "000001"
        for i in (300, 450, 620, 699):
            cached = srs.asof_candidate(df, _INDEX, i, code)
            uncached = srs._asof_candidate_uncached(df, _INDEX, i, code)
            _deep_eq(cached, uncached, path=f"cand@{i}")
            assert sc.technical_score(cached, None) == sc.technical_score(
                uncached, None
            )

    def test_not_vacuous_real_scores(self):
        """真算出了非平凡技术分（不是全 0/全不可用）。"""
        df = _DF["600000"]
        scores = [
            srs.asof_technical_score(df, _INDEX, i, "600000")[0]
            for i in range(400, 700, 60)
        ]
        assert any(s > 0 for s in scores), "合成数据上技术分全 0，等价测试形同空转"


# ---- ③④ run_study 全流程：开缓存 vs 无缓存原路逐笔一致 + 跨轮去重 ----


@pytest.mark.parametrize("use_panel_hook", [False, True], ids=["default", "panel_hook"])
def test_run_study_bitwise_cache_on_off(monkeypatch, use_panel_hook):
    """③ 开缓存（asof_candidate）vs 无缓存原路（_asof_candidate_uncached）逐笔一致。"""
    _patch_loader(monkeypatch)
    hook = wfs.panel_hook if use_panel_hook else None

    orig = srs.asof_candidate
    srs.asof_candidate = srs._asof_candidate_uncached  # 旧版逐笔直算
    try:
        trades_slow = srs.run_study(CODES, _REGIME, _INDEX, trade_hook=hook)
    finally:
        srs.asof_candidate = orig

    srs._CAND_CACHE.clear()
    trades_fast = srs.run_study(CODES, _REGIME, _INDEX, trade_hook=hook)

    assert trades_slow, "合成数据上一笔都没出，等价测试形同空转"
    assert trades_fast == trades_slow, "开/关缓存 run_study 逐笔输出不一致"


def test_run_study_second_pass_fully_cached(monkeypatch):
    """④ 跨轮去重：同输入第二轮 compute_metrics 零调用，逐笔输出与首轮一致。

    这是 resonance3「臂A → 臂B（gate④ + panel_hook）」复算同一批 (票,信号日)
    的缩影：缓存键是三层截断帧内容，与帧对象身份无关（loader 每次返回新对象）。
    """
    _patch_loader(monkeypatch)
    calls = _counting_cm(monkeypatch)
    t1 = srs.run_study(CODES, _REGIME, _INDEX)
    n_first = len(calls)
    assert t1 and n_first == len(t1), "首轮应每笔一算（单遍无重复）"
    t2 = srs.run_study(CODES, _REGIME, _INDEX)
    assert len(calls) == n_first, "第二轮应全部命中缓存、零重算"
    assert t2 == t1


# ---- panel_hook 侧：缓存路径产出不变 ----


def test_panel_hook_uses_cache(monkeypatch):
    calls = _counting_cm(monkeypatch)
    df, code, i = _DF["300750"], "300750", 600
    r1 = wfs.panel_hook(df, _INDEX, i, code)
    r2 = wfs.panel_hook(df, _INDEX, i, code)
    assert len(calls) == 1, "panel_hook 第二次调用不应重算 compute_metrics"
    assert r1 == r2
    assert list(r1["panel"]) == wfs.PANEL_KEYS  # 面板键集合钉死（防空转）
    assert any(v is True for v in r1["panel"].values()), "面板全未命中，形同空转"
