# -*- coding: utf-8 -*-
"""signal_context_study 钉测：格子发现/去重、三维度 as-of 判定、分组统计口径、
两窗同向预注册判读、fail-closed（缺格子 WARN 不崩）。

三个维度的判定在测试里全部替身化：regime 用合成 dict、PIT 用合成台账记录
（r3.build_pit_map 真实构造）、技术分用 dict 查表替身——不碰真实数据/网络。
"""

from __future__ import annotations

import json
import os

import pytest

from custos.research import resonance3_study as r3
from custos.research import signal_context_study as scs

CW = ("2022-01-01", "2024-12-31")  # 跨窗
MW = ("2024-08-01", "2026-09-04")  # 主窗


# ---------------------------------------------------------------------------
# 合成数据助手
# ---------------------------------------------------------------------------


def _trade(code, entry, ret):
    return {
        "code": code,
        "entry_date": entry,
        "exit_date": entry,
        "ret": ret,
        "risk_frac": 0.12,
        "r_multiple": round(ret / 0.12, 3),
        "holding": 3,
        "reason": "bbi_exit",
    }


def _write_cell(
    dir_,
    gate,
    start,
    end,
    pin,
    trades,
    *,
    hash_="deadbeef1234",
    stop_pct=12.0,
    scale_out=0.5,
    mtime=None,
):
    meta = {
        "entry_filter": gate,
        "start": start,
        "end": end,
        "amv_long_only": pin,
        "cost_bps": 25.0,
        "stop_mode": "pct",
        "stop_pct": stop_pct,
        "bbi_consec": 2,
        "time_stop": 0,
        "trades_signature": {
            "scale_out": scale_out,
            "breakeven": 0.0,
            "trail": 0.0,
            "cost_zone_bars": 0,
        },
        "trades": trades,
    }
    p = dir_ / f"baseline__{gate}__pct12_so5_bbi2__{hash_}.json"
    p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _write_ranked(dir_, tag, rows):
    """rows = [(gate, exit_tier, result_file)]。"""
    payload = {
        "tag": tag,
        "results": [{"gate": g, "exit": e, "result_file": f} for g, e, f in rows],
    }
    (dir_ / f"_ranked__{tag}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _pit(code, nd, np_=1e8, ocf=0.5, roe=8.0, rd="2021-12-31"):
    return {
        "code": code,
        "report_date": rd,
        "notice_date": nd,
        "net_profit": np_,
        "ocf_ps": ocf,
        "roe_waa": roe,
    }


def _ctx(regime, pit_records, scores):
    """合成上下文：scores = {(code, day): 技术分}，查不到 ⇒ None。"""
    return {
        "regime": regime,
        "pit_map": r3.build_pit_map(pit_records),
        "tech_fn": lambda code, day: scores.get((code, day)),
    }


# ---------------------------------------------------------------------------
# 批次族名（窗口标记剥离）
# ---------------------------------------------------------------------------


class TestFamilyOf:
    def test_strip_window_marker(self):
        """_main/_cw 后缀与中缀都剥，且只剥第一个；cw 前缀（无引导下划线）不剥。"""
        assert scs._family_of("rsi_family_main") == "rsi_family"
        assert scs._family_of("rsi_family_cw") == "rsi_family"
        assert scs._family_of("ctx_cw_nopin") == "ctx_nopin"
        assert scs._family_of("ctx_main_nopin") == "ctx_nopin"
        assert scs._family_of("cw_rsi_deep") == "cw_rsi_deep"
        assert scs._family_of("r20_first") == "r20_first"


# ---------------------------------------------------------------------------
# 格子发现：元数据过滤 + mtime 去重
# ---------------------------------------------------------------------------


class TestDiscoverCells:
    def test_metadata_filter(self, tmp_path):
        """只收 8 信号 ∧ 出场档元数据==pct12_so5_bbi2 ∧ 双窗的格子；范围外跳过不 WARN。"""
        _write_cell(
            tmp_path, "j_low_rsi_deep", *CW, True, [_trade("000001", "2022-03-01", 0.1)]
        )
        _write_cell(
            tmp_path, "j_low", *CW, True, [], hash_="0000jlow0000jl"
        )  # gate 不在 8 信号
        _write_cell(
            tmp_path, "b2", *CW, True, [], hash_="stop5stop5stop", stop_pct=5.0
        )  # 出场档不符（顶层）
        _write_cell(
            tmp_path, "b2", *CW, True, [], hash_="aaaabbbb0001", scale_out=0.0
        )  # 不符（签名）
        _write_cell(
            tmp_path,
            "b2",
            "2020-01-01",
            "2021-12-31",
            True,
            [],
            hash_="win0win0win0win0",
        )  # 窗口不符
        cells, warnings, skipped = scs.discover_cells(tmp_path)
        assert list(cells) == [("j_low_rsi_deep", "跨窗", True)]
        assert not warnings  # 范围外是正常存在，不打 WARN
        assert sum(skipped.values()) == 4

    def test_dedup_latest_mtime(self, tmp_path):
        """同 (gate, 窗口, pin) 多 hash 取 mtime 最新者，被弃的 WARN 留痕。"""
        _write_cell(tmp_path, "b2", *CW, True, [], hash_="111122223333", mtime=1000)
        _write_cell(
            tmp_path,
            "b2",
            *CW,
            True,
            [_trade("000001", "2022-03-01", 0.1)],
            hash_="444455556666",
            mtime=2000,
        )
        cells, warnings, _ = scs.discover_cells(tmp_path)
        assert cells[("b2", "跨窗", True)]["file"].endswith("444455556666.json")
        assert any("多 hash" in w and "111122223333" in w for w in warnings)

    def test_broken_json_warn_not_crash(self, tmp_path):
        """坏格子文件：WARN + 跳过，不崩（fail-closed）。"""
        (tmp_path / "baseline__b2__pct12_so5_bbi2__bad0bad0bad0.json").write_text(
            "{broken", encoding="utf-8"
        )
        cells, warnings, _ = scs.discover_cells(tmp_path)
        assert cells == {}
        assert any("读不了" in w for w in warnings)


class TestAssignFamilies:
    def test_ranked_membership_and_fallback(self, tmp_path):
        """ranked 引用归族（窗口标记剥离）；无引用的格子按 pin 落兜底族 nopin/adhoc_pin。"""
        p1 = _write_cell(tmp_path, "j_low_rsi_deep", *CW, True, [])
        p2 = _write_cell(
            tmp_path, "j_low_rsi_deep", *MW, True, [], hash_="abab0000abab0000"
        )
        p3 = _write_cell(tmp_path, "qg", *CW, False, [], hash_="cdcd0000cdcd0000")
        _write_ranked(
            tmp_path,
            "rsi_family_cw",
            [("j_low_rsi_deep", "pct12_so5_bbi2", p1.name)],
        )
        _write_ranked(
            tmp_path,
            "rsi_family_main",
            [("j_low_rsi_deep", "pct12_so5_bbi2", p2.name)],
        )
        cells, _, _ = scs.discover_cells(tmp_path)
        ranked, _ = scs.load_ranked_batches(tmp_path)
        fams = scs.assign_families(cells, ranked)
        assert sorted(fams["rsi_family"]) == [
            ("j_low_rsi_deep", "主窗", True),
            ("j_low_rsi_deep", "跨窗", True),
        ]
        assert fams["nopin"] == [("qg", "跨窗", False)]  # p3 无引用 + pin=False
        _ = p3


# ---------------------------------------------------------------------------
# regime as-of / 三维度逐笔判定
# ---------------------------------------------------------------------------


class TestRegimeAt:
    def test_asof(self):
        """as-of 最近 ≤entry 的读数；首个读数之前 ⇒ None。"""
        regime = {"2022-01-10": "做多", "2022-06-01": "空头", "2022-06-10": "做多"}
        dates = sorted(regime)
        assert scs.regime_at(regime, dates, "2022-01-05") is None
        assert scs.regime_at(regime, dates, "2022-01-10") == "做多"
        assert scs.regime_at(regime, dates, "2022-06-05") == "空头"
        assert scs.regime_at(regime, dates, "2022-12-31") == "做多"
        assert scs.regime_at({}, [], "2022-01-10") is None


class TestAnnotateDimensions:
    """三维度布尔值：regime 腿 / PIT tier 优 / 技术分 ≥60 边界，逐腿钉住。"""

    def _run(self, trades, regime, pit_records, scores):
        return scs.annotate_trades(trades, **_ctx(regime, pit_records, scores))

    def test_three_dims(self):
        regime = {"2022-01-01": "做多", "2022-06-01": "空头"}
        pit = [
            _pit("C1", "2021-12-30"),
            _pit("C2", "2021-12-30", np_=-1.0),
        ]  # C1 优 / C2 差
        trades = [
            _trade("C1", "2022-03-01", 0.10),  # 做多+优+强 → 三面共振
            _trade("C1", "2022-06-05", -0.04),  # 空头+优+强 → 空头前哨
            _trade("C2", "2022-03-01", 0.05),  # 做多+差+强 → 仅技术高分
            _trade("C1", "2022-03-02", 0.03),  # 做多+优+59 → 全 False（弱）
        ]
        scores = {
            ("C1", "2022-03-01"): 70.0,
            ("C1", "2022-06-05"): 70.0,
            ("C2", "2022-03-01"): 65.0,
            ("C1", "2022-03-02"): 59.0,
        }
        rows, info = self._run(trades, regime, pit, scores)
        assert info["n_scored"] == 4 and info["n_unscored"] == 0
        r0, r1, r2, r3_ = rows
        assert (r0["resonance3"], r0["bear_outpost"], r0["tech_high"]) == (
            True,
            False,
            True,
        )
        assert (r1["resonance3"], r1["bear_outpost"], r1["tech_high"]) == (
            False,
            True,
            True,
        )
        assert r2["tech_high"] is True and r2["resonance3"] is False
        assert (r3_["tech_high"], r3_["resonance3"], r3_["bear_outpost"]) == (
            False,
            False,
            False,
        )

    def test_tech_score_60_boundary(self):
        """60 含边界（TECH_STRONG=60，resonance3 同口径）。"""
        regime = {"2022-01-01": "做多"}
        pit = [_pit("C1", "2021-12-30")]
        trades = [_trade("C1", "2022-03-01", 0.1), _trade("C1", "2022-03-02", 0.1)]
        scores = {("C1", "2022-03-01"): 60.0, ("C1", "2022-03-02"): 59.9}
        rows, _ = self._run(trades, regime, pit, scores)
        assert rows[0]["tech_high"] is True
        assert rows[1]["tech_high"] is False

    def test_pit_notice_day_not_visible(self):
        """公告当日不可见（次日口径）⇒ 未知 ⇒ 非优 ⇒ 组外（live 同语义）。"""
        regime = {"2022-01-01": "做多"}
        pit = [_pit("C1", "2022-03-01")]  # notice 03-01
        trades = [_trade("C1", "2022-03-01", 0.1), _trade("C1", "2022-03-02", 0.1)]
        scores = {("C1", "2022-03-01"): 70.0, ("C1", "2022-03-02"): 70.0}
        rows, _ = self._run(trades, regime, pit, scores)
        assert rows[0]["tier"] == "未知" and rows[0]["resonance3"] is False
        assert rows[1]["tier"] == "优" and rows[1]["resonance3"] is True

    def test_unscored_not_silent_outgroup(self):
        """技术分缺失的笔不进任何分组（计 n_unscored），不静默算组外。"""
        regime = {"2022-01-01": "做多"}
        pit = [_pit("C1", "2021-12-30")]
        trades = [_trade("C1", "2022-03-01", 0.1), _trade("C1", "2022-03-02", -0.1)]
        scores = {("C1", "2022-03-01"): 70.0}  # 第二笔缺失
        rows, info = self._run(trades, regime, pit, scores)
        assert len(rows) == 1 and info["n_unscored"] == 1

    def test_neutral_regime_neither_dim(self):
        """中性 regime：市场腿不成立也不是空头 ⇒ 两复合维度都 False。"""
        regime = {"2022-01-01": "中性"}
        pit = [_pit("C1", "2021-12-30")]
        trades = [_trade("C1", "2022-03-01", 0.1)]
        rows, _ = self._run(trades, regime, pit, {("C1", "2022-03-01"): 70.0})
        assert rows[0]["resonance3"] is False and rows[0]["bear_outpost"] is False


# ---------------------------------------------------------------------------
# 分组统计口径（手算小样本）
# ---------------------------------------------------------------------------


class TestBearRegimeCompare:
    def test_count_only_render(self):
        """regime 无读数的笔只计数不进统计；md 渲染走 count_only 分支（钉 KeyError 修复）。"""
        rows = [{"regime": None, "ret": 0.1}, {"regime": "空头", "ret": -0.1}]
        out = scs.bear_regime_compare(rows)
        assert out["无读数"] == {"n": 1, "count_only": True}
        assert out["空头"]["n"] == 1
        rep = {
            "family": "t",
            "r11_warning": "",
            "preregistered_criterion": "",
            "exit_tier": "",
            "exit_tier_params": {},
            "windows": {},
            "min_n": 30,
            "cost_note": "",
            "tech_note": "",
            "pinned_market_leg_note": "",
            "cells": [],
            "gates": {},
            "bear_regime_compare": {
                "b2|跨窗": {
                    "gate": "b2",
                    "window": "跨窗",
                    "pin": False,
                    "by_regime": out,
                }
            },
            "warnings": [],
            "missing_cells": [],
            "skipped_at_discovery": {},
        }
        md = scs.render_markdown(rep)
        assert "| 无读数 | 1笔 |" in md


class TestGroupStats:
    def test_hand_computed(self):
        """4 笔 [0.1, 0.2, -0.1, -0.3]：胜率 0.5 / 盈亏比 0.75 / margin 0.5−1/1.75。"""
        rows = [{"ret": r} for r in (0.1, 0.2, -0.1, -0.3)]
        st = scs.group_stats(rows)
        assert st["n"] == 4
        assert st["win_rate"] == 0.5
        assert st["avg_win"] == pytest.approx(0.15)
        assert st["avg_loss"] == pytest.approx(0.2)
        assert st["payoff_ratio"] == 0.75
        # margin 用舍入后输入（win=0.5, payoff=0.75）：0.5 − 1/1.75
        assert st["margin"] == pytest.approx(round(0.5 - 1 / 1.75, 4))

    def test_zero_loss_payoff_none(self):
        """全赢 ⇒ avg_loss=0 ⇒ 盈亏比 None ⇒ margin None（除零如实缺）。"""
        st = scs.group_stats([{"ret": 0.1}, {"ret": 0.0}])  # ret=0 非赢非亏
        assert st["n"] == 2 and st["win_rate"] == 0.5
        assert st["payoff_ratio"] is None and st["margin"] is None

    def test_empty(self):
        st = scs.group_stats([])
        assert st["n"] == 0 and st["margin"] is None


# ---------------------------------------------------------------------------
# 预注册判读：两窗同向 + 样本门槛
# ---------------------------------------------------------------------------


def _wpair(n_in, margin_in, n_out=100, margin_out=0.0):
    return {
        "in": {"n": n_in, "margin": margin_in},
        "out": {"n": n_out, "margin": margin_out},
    }


class TestJudgeDimension:
    def test_both_windows_positive(self):
        j = scs.judge_dimension(
            {"跨窗": _wpair(50, 0.10), "主窗": _wpair(40, 0.05)}, 30
        )
        assert j["verdict"] == "✅"
        assert j["windows"]["跨窗"]["delta_margin"] == pytest.approx(0.10)
        assert j["windows"]["主窗"]["delta_margin"] == pytest.approx(0.05)

    def test_direction_flip_rejected(self):
        """一窗正一窗负 ⇒ ❌（两窗不同向）。"""
        j = scs.judge_dimension(
            {"跨窗": _wpair(50, 0.10), "主窗": _wpair(40, -0.05)}, 30
        )
        assert j["verdict"] == "❌"

    def test_zero_delta_rejected(self):
        """Δmargin=0 不算加值（严格 >0）。"""
        j = scs.judge_dimension({"跨窗": _wpair(50, 0.0), "主窗": _wpair(40, 0.0)}, 30)
        assert j["verdict"] == "❌"

    def test_insufficient_n(self):
        """任一窗组内 n<min-n ⇒ ⚠️（另一窗再正也不判 ✅）。"""
        j = scs.judge_dimension(
            {"跨窗": _wpair(50, 0.10), "主窗": _wpair(29, 0.20)}, 30
        )
        assert j["verdict"] == "⚠️"
        assert j["windows"]["主窗"]["status"] == "insufficient"
        assert j["windows"]["跨窗"]["status"] == "pos"

    def test_missing_window(self):
        j = scs.judge_dimension({"跨窗": _wpair(50, 0.10)}, 30)
        assert j["verdict"] == "⚠️"
        assert j["windows"]["主窗"]["status"] == "missing"

    def test_margin_none(self):
        j = scs.judge_dimension(
            {"跨窗": _wpair(50, None), "主窗": _wpair(40, 0.05)}, 30
        )
        assert j["verdict"] == "⚠️"
        assert j["windows"]["跨窗"]["status"] == "no_margin"


# ---------------------------------------------------------------------------
# TechScorer：每股只加载一次 + as-of 行号定位（替身钉行为）
# ---------------------------------------------------------------------------


class TestTechScorer:
    def test_load_once_and_asof_index(self, monkeypatch):
        """同票两笔只调一次 get_ohlcv_table；entry_date 映射到正确行号喂 as-of 打分。"""
        import pandas as pd

        from custos.datasource.local_tdx import local_tdx_data as ltd

        df = pd.DataFrame(
            {
                "date": pd.bdate_range("2022-01-03", periods=10),
                "open": [10.0] * 10,
                "high": [10.0] * 10,
                "low": [10.0] * 10,
                "close": [10.0] * 10,
                "volume": [1000.0] * 10,
            }
        )
        calls = {"n": 0}
        monkeypatch.setattr(
            ltd,
            "get_ohlcv_table",
            lambda code, count=0: calls.__setitem__("n", calls["n"] + 1) or df.copy(),
        )
        seen = []
        monkeypatch.setattr(
            scs.srs,
            "asof_technical_score",
            lambda df_full, index_full, i, code: (
                seen.append((i, code)) or (70, "强", {})
            ),
        )
        scorer = scs.TechScorer(index_df=df)
        d0 = str(df["date"].iloc[3])[:10]
        d1 = str(df["date"].iloc[7])[:10]
        assert scorer.score_at("000001", d0) == 70.0
        assert scorer.score_at("000001", d1) == 70.0
        assert calls["n"] == 1  # 每股加载一次
        assert seen == [(3, "000001"), (7, "000001")]  # as-of 行号 = entry 日所在行
        assert scorer.score_at("000001", "1999-01-04") is None  # 无此交易日 ⇒ 缺失


# ---------------------------------------------------------------------------
# 端到端：合成格子 + 替身上下文跑 main（fail-closed + 判读 + 手算数值）
# ---------------------------------------------------------------------------


def _pinned_trades_cw():
    """跨窗 pinned：组内（做多+优+强）30 笔 20W@+0.10/10L@−0.05；组外 40 笔 10W@+0.05/30L@−0.05。"""
    trades = []
    # 组内：C1（优）score 70，做多日 2022-03-01
    trades += [_trade("C1", "2022-03-01", 0.10)] * 20
    trades += [_trade("C1", "2022-03-01", -0.05)] * 10
    # 组外 a：C1 score 50（弱）做多日；组外 b：C2（差）score 70 做多日
    trades += [_trade("C1", "2022-03-02", 0.05)] * 5
    trades += [_trade("C1", "2022-03-02", -0.05)] * 15
    trades += [_trade("C2", "2022-03-01", 0.05)] * 5
    trades += [_trade("C2", "2022-03-01", -0.05)] * 15
    return trades


def _pinned_trades_mw():
    """主窗 pinned：组内 10 笔 7W@+0.10/3L@−0.05；组外 20 笔 5W@+0.05/15L@−0.05。"""
    trades = []
    trades += [_trade("C1", "2025-03-03", 0.10)] * 7
    trades += [_trade("C1", "2025-03-03", -0.05)] * 3
    trades += [_trade("C1", "2025-03-04", 0.05)] * 5
    trades += [_trade("C1", "2025-03-04", -0.05)] * 15
    return trades


def _nopin_trades():
    """跨窗 nopin：空头期 C1 优+强 6 笔 4W@+0.08/2L@−0.04（空头前哨组内）；
    做多期 C1 优+强 4 笔 1W@+0.03/3L@−0.03；空头期 C2 差+强 4 笔全亏 −0.02（组外）。"""
    trades = []
    trades += [_trade("C1", "2022-06-05", 0.08)] * 4  # 空头
    trades += [_trade("C1", "2022-06-05", -0.04)] * 2
    trades += [_trade("C1", "2022-06-15", 0.03)] * 1  # 做多
    trades += [_trade("C1", "2022-06-15", -0.03)] * 3
    trades += [_trade("C2", "2022-06-05", -0.02)] * 4  # 空头但 tier 差
    return trades


class TestMainEndToEnd:
    def _fake_ctx(self):
        regime = {
            "2022-01-04": "做多",
            "2022-06-01": "空头",
            "2022-06-10": "做多",
            "2025-01-02": "做多",
        }
        pit = [_pit("C1", "2021-12-30"), _pit("C2", "2021-12-30", np_=-1.0)]
        scores = {
            ("C1", "2022-03-01"): 70.0,
            ("C1", "2022-03-02"): 50.0,  # 弱 ⇒ 组外
            ("C2", "2022-03-01"): 70.0,  # 差 ⇒ 组外
            ("C1", "2025-03-03"): 70.0,
            ("C1", "2025-03-04"): 50.0,
            ("C1", "2022-06-05"): 70.0,
            ("C1", "2022-06-15"): 70.0,
            ("C2", "2022-06-05"): 70.0,
        }
        return _ctx(regime, pit, scores)

    def test_run(self, tmp_path, monkeypatch, capsys):
        cells_dir = tmp_path / "cells"
        out_dir = tmp_path / "out"
        cells_dir.mkdir()
        _write_cell(
            cells_dir,
            "j_low_rsi_deep",
            *CW,
            True,
            _pinned_trades_cw(),
            hash_="aaaa0000aaaa0000",
        )
        _write_cell(
            cells_dir,
            "j_low_rsi_deep",
            *MW,
            True,
            _pinned_trades_mw(),
            hash_="bbbb0000bbbb0000",
        )
        _write_cell(
            cells_dir, "qg", *CW, False, _nopin_trades(), hash_="cccc0000cccc0000"
        )
        _write_ranked(
            cells_dir,
            "rsi_family_cw",
            [
                (
                    "j_low_rsi_deep",
                    "pct12_so5_bbi2",
                    "baseline__j_low_rsi_deep__pct12_so5_bbi2__aaaa0000aaaa0000.json",
                )
            ],
        )
        _write_ranked(
            cells_dir,
            "rsi_family_main",
            [
                (
                    "j_low_rsi_deep",
                    "pct12_so5_bbi2",
                    "baseline__j_low_rsi_deep__pct12_so5_bbi2__bbbb0000bbbb0000.json",
                )
            ],
        )
        monkeypatch.setattr(scs, "load_context", lambda args: self._fake_ctx())

        rc = scs.main(
            ["--cells-dir", str(cells_dir), "--out-dir", str(out_dir), "--min-n", "5"]
        )
        assert rc == 0
        # pinned 族（ranked 引用 ⇒ rsi_family）+ nopin 兜底族
        rep = json.loads(
            (out_dir / "rsi_family_context.json").read_text(encoding="utf-8")
        )
        rep_nopin = json.loads(
            (out_dir / "nopin_context.json").read_text(encoding="utf-8")
        )
        assert (out_dir / "rsi_family_context.md").is_file()

        # ── pinned rsi_deep：三面共振两窗 Δmargin 手算 ──
        dims = rep["gates"]["j_low_rsi_deep"]["dims"]["resonance3"]
        cw, mw = dims["windows"]["跨窗"], dims["windows"]["主窗"]
        # 跨窗组内：20W/30 → 胜率 0.6667，盈亏比 2.0，margin 0.6667−1/3≈0.3334
        assert cw["in"]["n"] == 30 and cw["in"]["payoff_ratio"] == 2.0
        assert cw["in"]["margin"] == pytest.approx(round(0.6667 - 1 / 3, 4))
        # 跨窗组外：10W/40，盈亏比 1.0，margin 0.25−0.5=−0.25
        assert cw["out"]["n"] == 40 and cw["out"]["margin"] == -0.25
        assert cw["delta_margin"] == pytest.approx(round(0.6667 - 1 / 3, 4) + 0.25)
        assert mw["in"]["n"] == 10 and mw["delta_margin"] > 0
        assert dims["verdict"] == "✅"  # 两窗同正 ⇒ 加值
        # pinned 批无空头期成交 ⇒ 空头前哨组内 n=0 ⇒ ⚠️样本不足
        assert rep["gates"]["j_low_rsi_deep"]["dims"]["bear_outpost"]["verdict"] == "⚠️"
        assert rep["bear_regime_compare"] == {}  # pinned 族无空头成交 ⇒ 不出对照表

        # ── nopin 族：空头前哨 + 对照表 + 缺主窗 ⇒ ⚠️ ──
        d_nop = rep_nopin["gates"]["qg"]["dims"]["bear_outpost"]
        cw_n = d_nop["windows"]["跨窗"]
        # 组内 6 笔 4W@0.08/2L@0.04：胜率 0.6667，盈亏比 2.0
        assert cw_n["in"]["n"] == 6 and cw_n["in"]["margin"] == pytest.approx(
            round(0.6667 - 1 / 3, 4)
        )
        assert cw_n["out"]["n"] == 8  # 4 做多 + 4 空头tier差
        assert d_nop["verdict"] == "⚠️"  # 主窗缺 ⇒ 证据不足
        assert d_nop["windows"]["主窗"]["status"] == "missing"
        assert rep_nopin["missing_cells"] == ["qg(QG) 主窗"]
        cmp_ = rep_nopin["bear_regime_compare"]["qg|跨窗"]["by_regime"]
        assert cmp_["空头"]["n"] == 10 and cmp_["做多"]["n"] == 4
        # 缺格子 WARN 上 stderr，不崩
        assert "缺格子" in capsys.readouterr().err or any(
            "缺格子" in w for w in rep_nopin["warnings"]
        )

    def test_tag_filter(self, tmp_path, monkeypatch):
        """--tag 只处理匹配族；匹配不到 ⇒ WARN + 返回 1（不崩）。"""
        cells_dir = tmp_path / "cells"
        cells_dir.mkdir()
        _write_cell(cells_dir, "j_low_rsi_deep", *CW, True, _pinned_trades_cw())
        monkeypatch.setattr(scs, "load_context", lambda args: self._fake_ctx())
        rc = scs.main(
            [
                "--cells-dir",
                str(cells_dir),
                "--out-dir",
                str(tmp_path / "out"),
                "--tag",
                "不存在的批次",
            ]
        )
        assert rc == 1
        assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*"))

    def test_no_cells_returns_1(self, tmp_path, monkeypatch):
        """目录里一个合格格子都没有 ⇒ 返回 1 不崩（load_context 都省了）。"""
        cells_dir = tmp_path / "cells"
        cells_dir.mkdir()
        _write_cell(cells_dir, "j_low", *CW, True, [])  # gate 不在 8 信号
        called = {"ctx": False}
        monkeypatch.setattr(
            scs,
            "load_context",
            lambda args: called.__setitem__("ctx", True) or self._fake_ctx(),
        )
        assert (
            scs.main(["--cells-dir", str(cells_dir), "--out-dir", str(tmp_path / "o")])
            == 1
        )
        assert called["ctx"] is False

    def test_missing_dir(self, tmp_path):
        assert scs.main(["--cells-dir", str(tmp_path / "nope")]) == 2


def test_cost_bps_mismatch_rejected(tmp_path):
    """cost_bps≠25 的格子不混进同一对照（v0.187 起硬校验，ret 净额口径不同）。"""
    _write_cell(
        tmp_path, "j_low_rsi_deep", *CW, True, [_trade("000001", "2022-03-01", 0.1)]
    )
    p = _write_cell(tmp_path, "qg", *CW, True, [], hash_="c05t20c05t20c0")
    d = json.loads(p.read_text(encoding="utf-8"))
    d["cost_bps"] = 20.0
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    cells, warnings, skipped = scs.discover_cells(tmp_path)
    assert list(cells) == [("j_low_rsi_deep", "跨窗", True)]
    assert skipped.get("cost_bps≠25") == 1


class TestAnnCache:
    """_ann 断点缓存：源格子 mtime+大小签名绑定（v0.187）——同名格子被覆盖
    重跑后旧标注不得静默复用；源未变时命中缓存、不重复标注。"""

    def _setup(self, tmp_path, monkeypatch):
        cells_dir = tmp_path / "cells"
        out_dir = tmp_path / "out"
        cells_dir.mkdir()
        _write_cell(
            cells_dir,
            "j_low_rsi_deep",
            *CW,
            True,
            _pinned_trades_cw(),
            hash_="aaaa0000aaaa0000",
        )
        calls = {"n": 0}
        real_annotate = scs.annotate_trades

        def counting(*a, **kw):
            calls["n"] += 1
            return real_annotate(*a, **kw)

        monkeypatch.setattr(scs, "annotate_trades", counting)
        monkeypatch.setattr(
            scs,
            "load_context",
            lambda args: {
                "regime": {"2022-01-04": "做多"},
                "pit_map": r3.build_pit_map([_pit("C1", "2021-12-30")]),
                "tech_fn": lambda code, day: 70.0,
                "n_pit_records": 1,
            },
        )
        argv = [
            "--cells-dir",
            str(cells_dir),
            "--out-dir",
            str(out_dir),
            "--min-n",
            "1",
        ]
        return cells_dir, out_dir, calls, argv

    def test_cache_hit_skips_annotate(self, tmp_path, monkeypatch):
        _cells_dir, _out_dir, calls, argv = self._setup(tmp_path, monkeypatch)
        assert scs.main(argv) == 0 and calls["n"] == 1
        assert scs.main(argv) == 0
        assert calls["n"] == 1, "源格子未变 ⇒ 命中 _ann 缓存，不重复标注"

    def test_stale_cache_recomputes(self, tmp_path, monkeypatch):
        cells_dir, _out_dir, calls, argv = self._setup(tmp_path, monkeypatch)
        assert scs.main(argv) == 0 and calls["n"] == 1
        # 同名格子被覆盖重跑（内容变 ⇒ mtime+大小签名变）⇒ 旧标注必须作废重算
        _write_cell(
            cells_dir,
            "j_low_rsi_deep",
            *CW,
            True,
            _pinned_trades_cw() + [_trade("C1", "2022-07-01", 0.05)],
            hash_="aaaa0000aaaa0000",
        )
        assert scs.main(argv) == 0
        assert calls["n"] == 2, "源格子变更后旧标注被静默复用了"
