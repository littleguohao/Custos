# -*- coding: utf-8 -*-
"""审计第 4 批（P3 选股链）回归测试：坏数据/缺数据不得表现为好数据。

覆盖：
- B5 名称表挂掉 → ST 硬排除失效却仍 status=ok（ST 股静默进候选池）
- B6 自选池文件缺失/解析失败 → status 仍 ok 而整条候选通道归零
- B7 上证指数无新鲜度校验 → 相对强度用错窗口相减或静默消失
- B8 sector_state.score 为 NaN → min(100.0, nan)==100 → 板块分白送满分
- B10 manual_pools/enrich 无 A 股白名单 → ETF/B股/可转债进 A-D 分层

断言全部针对**行为**（返回结构与数值），不匹配源码文本。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "07_tools" / "screening"))

import enrich_candidates as ec
import formula_screen as fs
import manual_pools
import score_candidates as sc


# ---------------- 公共构件 ----------------

def _df(end: str = "2026-07-22", periods: int = 80, close: float = 10.0) -> pd.DataFrame:
    dates = pd.date_range(end=end, periods=periods, freq="B")
    return pd.DataFrame({
        "date": dates, "open": close, "high": close * 1.005, "low": close * 0.995,
        "close": close, "volume": 1000.0, "amount": 0.0,
    })


def _index_df(end: str = "2026-07-22", periods: int = 80) -> pd.DataFrame:
    return _df(end=end, periods=periods, close=3000.0)


def _hits(*items, **kw) -> dict:
    """items: (code, name) 元组序列。kw 可注入 st_filter 等顶层字段。"""
    data = {
        "date": kw.pop("date", "2026-07-22"),
        "status": "ok",
        "formulas": [{"id": "F1", "enabled": True,
                      "hits": [{"code": c, "name": n} for c, n in items]}],
    }
    data.update(kw)
    return data


_UNIV = {"exclude_bj": True, "exclude_st": True, "min_list_days": 60, "j_low_required": False}


def _enrich(monkeypatch, hits, dfs, date="2026-07-22", index_df=None, cfg=None):
    monkeypatch.setattr(ec, "build_stock_theme_map", lambda **k: ({}, True))
    return ec.enrich(date, hits_data=hits,
                     ohlcv_loader=lambda c: dfs[c].copy(),
                     index_loader=(lambda: index_df),
                     universe_cfg=dict(cfg or _UNIV))


# ---------------- B5：名称表不可用时 ST 硬排除必须 fail-closed ----------------

class TestB5StFilterFailClosed:
    """名称表是 ST 硬排除的唯一依据。在线+缓存双挂时 name 全空 → `"ST" in ""` 为假 →
    ST 股全部通过硬排除，且此前 status 仍是 ok，报告读起来像"今天筛出这些好票"。"""

    def test_formula_screen_not_ok_when_name_map_unavailable(self):
        r = fs.screen_formulas(
            "2026-07-22",
            registry={"universe": {}, "formulas": [
                {"id": "F1", "tq_name": "FAKE", "enabled": True}]},
            stock_list=["600000"], name_map={},          # 名称表空＝ST 判据失效
            call=lambda *a, **k: {"ok": True, "value": {"600000.SH": {"S": ["1"]}},
                                 "error": None},
            running_check=lambda: True)
        assert r["st_filter"] == "unavailable"
        assert r["status"] != "ok"                        # 不得声称本次筛选正常
        assert "st_filter_unavailable" in r["degraded_reason"]

    def test_enrich_rejects_unnamed_when_st_filter_unavailable(self, monkeypatch):
        hits = _hits(("600000", ""), st_filter="unavailable")
        r = _enrich(monkeypatch, hits, {"600000": _df()}, index_df=_index_df())
        assert r["candidates"] == []                      # 无法证明非 ST → 不得放行
        assert r["excluded"] and r["excluded"][0]["reason"].startswith("st_unverified")
        assert r["status"] == "unavailable"               # 0 候选不是"市场没票"
        assert "st_filter_unavailable" in r["degraded_reason"]

    def test_enrich_keeps_named_candidates_when_st_filter_ok(self, monkeypatch):
        hits = _hits(("600000", "浦发银行"), ("600001", "*ST测试"), st_filter="ok")
        r = _enrich(monkeypatch, hits, {"600000": _df(), "600001": _df()},
                    index_df=_index_df())
        assert [c["code"] for c in r["candidates"]] == ["600000"]
        assert r["excluded"][0]["reason"] == "st_stock"


# ---------------- B6：自选池缺失不得静默归零 ----------------

class TestB6ManualPoolFailureVisible:
    """自选池是公式之外的第二候选来源。blk 文件被改名/权限丢失时该通道整条归零，
    此前 status 仍 ok → 报告读成"池里今天没有符合条件的票"。"""

    def _registry(self):
        return {"universe": {},
                "manual_pools": [{"id": "POOL_X", "block_name": "幽灵池", "enabled": True}],
                "formulas": [{"id": "F1", "tq_name": "FAKE", "enabled": True}]}

    def test_status_degrades_when_pool_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(manual_pools, "TDX_BLOCK_DIR", tmp_path)   # 空目录＝池文件缺失
        r = fs.screen_formulas(
            "2026-07-22", registry=self._registry(),
            stock_list=["600000"], name_map={"600000": "浦发银行"},
            call=lambda *a, **k: {"ok": True, "value": {"600000.SH": {"S": ["1"]}},
                                 "error": None},
            running_check=lambda: True)
        assert r["status"] != "ok"
        assert "manual_pool" in r["degraded_reason"]
        assert r["manual_pool_status"] == "unavailable"

    def test_pool_failure_visible_even_when_tq_down(self, tmp_path, monkeypatch):
        monkeypatch.setattr(manual_pools, "TDX_BLOCK_DIR", tmp_path)
        r = fs.screen_formulas("2026-07-22", registry=self._registry(),
                               stock_list=["600000"], name_map={"600000": "浦发银行"},
                               running_check=lambda: False)
        assert "manual_pool" in r["degraded_reason"]
        assert r["manual_pool_status"] == "unavailable"


# ---------------- B7：上证指数新鲜度 ----------------

class TestB7IndexFreshness:
    """个股有 last_date==date 强校验，指数此前没有：拿昨天（或更旧）的指数序列与当日个股
    做 20 日相对强度＝错窗口相减；加载失败仅置 None → 整列 rs 消失且不留痕。"""

    def test_stale_index_marks_degraded_and_drops_rs(self, monkeypatch):
        stale = _index_df(end="2026-07-15")               # 指数序列停在一周前
        r = _enrich(monkeypatch, _hits(("600000", "浦发银行"), st_filter="ok"),
                    {"600000": _df()}, index_df=stale)
        assert r["candidates"], "个股本身当日数据完好，不应被指数问题剔除"
        assert r["candidates"][0]["relative_strength_20d_pp"] is None   # 不做错窗口相减
        assert r["index_status"].startswith("index_stale")
        assert "index" in r["degraded_reason"]
        assert r["status"] == "partial"

    def test_missing_index_is_recorded(self, monkeypatch):
        r = _enrich(monkeypatch, _hits(("600000", "浦发银行"), st_filter="ok"),
                    {"600000": _df()}, index_df=None)
        assert r["index_status"] == "index_missing"
        assert "index" in r["degraded_reason"] and r["status"] == "partial"

    def test_unsorted_index_is_sorted_before_use(self, monkeypatch):
        """mootdx Reader 返回顺序不保证；乱序会让 iloc[-1] 取到最旧那天，相对强度符号反转。"""
        idx = _index_df()
        idx.loc[:, "close"] = [3000.0 + i for i in range(len(idx))]   # 指数单调上行
        shuffled = idx.iloc[::-1].reset_index(drop=True)              # 逆序注入
        r = _enrich(monkeypatch, _hits(("600000", "浦发银行"), st_filter="ok"),
                    {"600000": _df()}, index_df=shuffled)
        assert r["index_status"] == "ok"
        rs = r["candidates"][0]["relative_strength_20d_pp"]
        # 个股走平、指数 20 日上行 → 相对强度必须为负；不排序会取成 20 日下行 → 变正数
        assert rs is not None and rs < 0

    def test_index_loader_exception_is_recorded(self, monkeypatch):
        monkeypatch.setattr(ec, "build_stock_theme_map", lambda **k: ({}, True))

        def _boom():
            raise RuntimeError("reader dead")

        r = ec.enrich("2026-07-22", hits_data=_hits(("600000", "浦发银行"), st_filter="ok"),
                      ohlcv_loader=lambda c: _df(), index_loader=_boom,
                      universe_cfg=dict(_UNIV))
        assert r["index_status"].startswith("index_load_error")
        assert r["status"] == "partial"


# ---------------- B8：板块分 NaN 不得兜成满分 ----------------

class TestB8SectorScoreNaN:
    """`float("nan")` 不会抛异常，`min(100.0, nan)` 按 IEEE 754 返回 100 →
    板块分白送满分（占总分 40%），把无评分板块的票推进 A 池。"""

    def test_nan_returns_none_not_full_score(self):
        assert sc.normalize_sector_score(float("nan")) is None
        assert sc.normalize_sector_score("nan") is None
        assert sc.normalize_sector_score(float("inf")) is None
        assert sc.normalize_sector_score(None) == 0.0     # 无评分＝最弱（原语义保留）
        assert sc.normalize_sector_score(60) == 60.0

    def test_score_candidate_nan_sector_not_rewarded(self):
        cand = {"code": "600000", "name": "浦发银行", "sector": "船舶"}
        nan_entry = {"sector": "船舶", "state": "主升", "score": float("nan")}
        good_entry = {"sector": "船舶", "state": "主升", "score": 100.0}
        nan_res = sc.score_candidate(cand, nan_entry, "中性")
        good_res = sc.score_candidate(cand, good_entry, "中性")
        assert nan_res["score"] < good_res["score"], "NaN 板块分不得等同满分"
        assert nan_res["score_detail"]["sector_score"] == 0.0
        assert "sector_score_unavailable" in nan_res["risk_flags"]

    def test_score_all_flags_nan_sector_score(self):
        enriched = {"date": "2026-07-22", "status": "ok", "candidates": [
            {"code": "600000", "name": "浦发银行", "sector": "船舶", "theme_id": "T1"}]}
        states = [{"theme_id": "T1", "sector": "船舶", "state": "主升", "score": float("nan")}]
        r = sc.score_all("2026-07-22", enriched=enriched, sector_states=states,
                         amv_state="中性", cz_preference={})
        assert r["status"] == "partial"
        assert "sector_score_unavailable" in r["degraded_reason"]


# ---------------- B10：A 股白名单 ----------------

class TestB10AShareWhitelist:
    """自选池是用户手工维护的通达信板块，里面常混 ETF/可转债/B股；此前 hits 绕过
    `_A_SHARE_RE`，enrich 又只排 BJ 前缀 → 非 A 股标的可进 StockPool 契约与 A-D 分层。"""

    def _blk_dir(self, tmp_path):
        d = tmp_path / "blocknew"
        d.mkdir()
        cfg = "混合池".encode("gbk") + b"\x00" * 88 + b"HH" + b"\x00" * 58
        (d / "blocknew.cfg").write_bytes(cfg)
        (d / "HH.blk").write_bytes(
            b"1600150\r\n1510300\r\n1900001\r\n1110059\r\n0128036\r\n0159915\r\n")
        return d

    def test_manual_pool_excludes_non_a_share(self, tmp_path):
        d = self._blk_dir(tmp_path)
        pool = manual_pools.load_pool("混合池", "2026-07-22", block_dir=d)
        assert [h["code"] for h in pool["hits"]] == ["600150"]
        excluded = {x["code"] for x in pool["excluded"]}
        assert excluded == {"510300", "900001", "110059", "128036", "159915"}
        assert all(x["reason"] == "not_a_share" for x in pool["excluded"])
        assert pool["error"] is None                      # 文件本身正常，非错误

    def test_enrich_excludes_non_a_share_codes(self, monkeypatch):
        hits = _hits(("600000", "浦发银行"), ("510300", "沪深300ETF"),
                     ("110059", "浦发转债"), st_filter="ok")
        r = _enrich(monkeypatch, hits,
                    {"600000": _df(), "510300": _df(), "110059": _df()},
                    index_df=_index_df())
        assert [c["code"] for c in r["candidates"]] == ["600000"]
        reasons = {x["code"]: x["reason"] for x in r["excluded"]}
        assert reasons["510300"] == "not_a_share" and reasons["110059"] == "not_a_share"

    def test_enrich_still_reports_bj_separately(self, monkeypatch):
        """BJ 有独立 exclude_bj 开关（可配置放开），不能被白名单吞成 not_a_share。"""
        hits = _hits(("920808", "北交所票"), st_filter="ok")
        r = _enrich(monkeypatch, hits, {"920808": _df()}, index_df=_index_df())
        assert r["excluded"][0]["reason"] == "exclude_bj"
