# -*- coding: utf-8 -*-
"""信号标注层回归测试（owner 2026-08-04 裁定的 A 类改动）。

设计边界：
    A. 纯标注（本层）  不改分层/next_step/不筛候选 → 风险≈0，直接上线
    B. 加分/减分       改 total → 改 A/B/C/D → 改"可买"清单 → **必须先回测**

本文件钉住三件事：
  ① 标注是**三态**（hit/miss/unavailable），命中率分母是**可评估数**——`min_list_days=60`
     而 qsx_gt_dks 需 120 根、surge_then_b1 需 200 根，大量候选算不出来；把"算不出来"
     混进分母会让"数据不足"被误读成"不符合条件"（本次审计反复出现的失效模式）。
  ② 标注**逐个列出命中的票**，不是只报数量。
  ③ 无论标注命中什么，**选股输出逐字节不变**。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from custos.pipeline.screening import candidate_table as ct
from custos.pipeline.screening import signal_labels as sl


def _mk(rows):
    c = np.array([r[0] for r in rows], float)
    v = np.array([r[1] for r in rows], float)
    o = np.concatenate(([c[0]], c[:-1]))
    return pd.DataFrame({
        "date": pd.bdate_range("2023-06-01", periods=len(c)),
        "open": o, "high": np.maximum(c, o) * 1.012,
        "low": np.minimum(c, o) * 0.99, "close": c, "volume": v, "amount": v * c,
    })


def _sig(hits=(), na=(), neg=()):
    """构造 signals 字典（含 summary）。"""
    s = {}
    for k in sl.SIGNAL_META:
        state = "hit" if (k in hits or k in neg) else ("unavailable" if k in na else "miss")
        s[k] = {"state": state}
    s["summary"] = sl.summarize_signals(s)
    return s


def _cand(code, name, bucket="A", **kw):
    return {"code": code, "name": name, "bucket": bucket,
            "next_step": "generate_buy_plan" if bucket == "A" else "observe_price",
            "fundamental_quality": {"tier": "优"},
            "resonance_4leg": {"sector": True, "technical": True, "market": True,
                               "aligned": 4, "bull_candidate": True},
            "score_detail": {"total": 80}, "sector": "半导体", "sector_state": "主升",
            "patterns": {}, "wave": {}, "stop_loss_ref": {"price": 9.5}, **kw}


class TestThreeStates:
    """三态是硬要求：缺数据不能表现为"未命中"。"""

    def test_states_are_three_valued(self):
        s = _sig(hits={"qsx_gt_dks"}, na={"bottom_surge"})
        assert s["qsx_gt_dks"]["state"] == "hit"
        assert s["weekly_j_low"]["state"] == "miss"
        assert s["bottom_surge"]["state"] == "unavailable"

    def test_denominator_excludes_unavailable(self):
        """新股只能评估 4 项、命中 3 项 → 显示 3/4 而不是 3/12。"""
        s = _sig(hits={"rsi_strong", "rsi_deep_oversold", "rsi_ideal_b1"},
                 na={"qsx_gt_dks", "bottom_surge", "surge_then_b1", "main_rally",
                     "breakout_pullback_b1", "b2", "distribution_risk", "rsi_bull_div",
                     "weekly_j_low"})
        sm = s["summary"]
        assert sm["positive_hit_count"] == 3
        assert sm["positive_evaluable"] == 3, "分母只数 hit+miss"
        assert sm["label"] == "3/3"
        assert sm["unavailable_count"] == 9

    def test_negative_counted_separately(self):
        s = _sig(hits={"qsx_gt_dks"}, neg={"distribution_risk"})
        sm = s["summary"]
        assert sm["positive_hit_count"] == 1
        assert sm["negative_hit_count"] == 1
        assert sm["neg_abbrs"] == ["⚠出货"]

    def test_short_history_yields_unavailable(self):
        """70 根的票：qsx_gt_dks(需120) / bottom_surge(需200) 必须是 unavailable。"""
        df = _mk([(10.0 + 0.2 * np.sin(i / 3), 4e5) for i in range(70)])
        s = sl.compute_signals(df, "600000")
        assert s["qsx_gt_dks"]["state"] == "unavailable"
        assert s["bottom_surge"]["state"] == "unavailable"
        assert s["surge_then_b1"]["state"] == "unavailable"

    def test_long_history_is_evaluable(self):
        rng = np.random.default_rng(11)
        p, seq = 10.0, []
        for i in range(240):
            p *= 1.004 * (1 + 0.014 * np.sin(i / 3) + rng.normal(0, 0.006))
            seq.append((p, 4e5))
        s = sl.compute_signals(_mk(seq), "600000")
        for k in ("qsx_gt_dks", "bottom_surge", "surge_then_b1", "main_rally"):
            assert s[k]["state"] in ("hit", "miss"), f"{k} 应可评估"


class TestReuseAvoidsRecompute:
    """复用调用方已算的结果——重复 resample 白付 2.3ms/票。"""

    def test_injected_values_are_used(self):
        df = _mk([(10.0 + 0.2 * np.sin(i / 3), 4e5) for i in range(150)])
        s = sl.compute_signals(
            df, "600000",
            zx={"available": True, "qsx_gt_dks": True, "qsx": 11.0, "dks": 10.0},
            weekly_j_low=True, weekly_j_available=True,
            distribution={"available": True, "risk_level": "high", "hits": ["x"]})
        assert s["qsx_gt_dks"]["state"] == "hit" and s["qsx_gt_dks"]["qsx"] == 11.0
        assert s["weekly_j_low"]["state"] == "hit"
        assert s["distribution_risk"]["state"] == "hit"
        assert s["distribution_risk"]["risk_level"] == "high"

    def test_falls_back_to_self_compute(self):
        """不注入时自己算（回测/单点调用场景）。"""
        df = _mk([(10.0 + 0.2 * np.sin(i / 3), 4e5) for i in range(150)])
        s = sl.compute_signals(df, "600000")
        assert s["qsx_gt_dks"]["state"] in ("hit", "miss")

    def test_never_raises_on_garbage(self):
        for df in (_mk([(10.0, 4e5)] * 3), _mk([(10.0, 0.0)] * 80)):
            s = sl.compute_signals(df, "600000")
            assert isinstance(s.get("summary"), dict)


class TestEnrichIntegration:
    def test_compute_metrics_emits_signals(self):
        from custos.pipeline.screening.enrich_candidates import compute_metrics
        rng = np.random.default_rng(7)
        p, c = 20.0, []
        for i in range(260):
            p *= (1 + 0.001 + 0.02 * np.sin(i / 5) + rng.normal(0, 0.012))
            c.append(p)
        df = _mk([(x, 5e5) for x in c])
        m = compute_metrics(df, df[["date", "close"]].copy(), "600000")
        assert isinstance(m.get("signals"), dict)
        assert "summary" in m["signals"]
        # distribution 已注入 → 不该是 unavailable
        assert m["signals"]["distribution_risk"]["state"] in ("hit", "miss")


class TestTableRendering:
    """展示要求：**逐个标注列出命中的票**，不是只报数量。"""

    def _pool(self):
        return {"date": "2026-08-04", "status": "ok", "amv_state": "做多",
                "market_permission": "允许", "bucket_counts": {"A": 2, "B": 2},
                "candidates": [
                    _cand("600000", "浦发银行", "A",
                          signals=_sig({"qsx_gt_dks", "rsi_strong", "b2"})),
                    _cand("300750", "宁德时代", "A",
                          signals=_sig({"qsx_gt_dks", "rsi_ideal_b1"})),
                    _cand("002100", "新票", "B",
                          signals=_sig({"rsi_bull_div"},
                                       na={"qsx_gt_dks", "bottom_surge", "surge_then_b1"})),
                    _cand("000555", "风险票", "B",
                          signals=_sig({"qsx_gt_dks"}, neg={"distribution_risk"})),
                ]}

    def test_lists_actual_codes_not_just_counts(self):
        txt = ct.render_table(self._pool(), "2026-08-04",
                             gate={"market_quality": {"status": "pass", "amv_ok": True,
                                                      "limitations": []}})
        assert "🏷️ 信号标注一览" in txt
        # 必须出现具体代码+名称，而不是只有数量
        assert "600000 浦发银行" in txt and "300750 宁德时代" in txt

    def test_negative_signal_is_marked(self):
        txt = ct.render_table(self._pool(), "2026-08-04")
        assert "⚠️ **主力出货形态**" in txt
        assert "000555 风险票" in txt

    def test_denominator_is_evaluable_count(self):
        txt = ct.render_table(self._pool(), "2026-08-04")
        # qsx_gt_dks: 3 只命中 / 3 只可评估（新票 unavailable 被排除）
        assert "`QD`（3/3）" in txt

    def test_carries_terminal_review_disclaimer(self):
        """必须写明这些因子已被跨窗终审否决——否则读者会把标注当交易依据。

        终审(research/R6_hypothesis_H1_dual_axis.md + R7)显示它们的 edge 只存在于 2025-2026
        单一 regime,所以"标注多⇒确信度高"这个推论已被证伪,不得据命中数定仓位。
        """
        txt = ct.render_table(self._pool(), "2026-08-04")
        assert "终审" in txt and "观察记录" in txt
        assert "不得据命中数决定仓位" in txt

    def test_unavailable_explained(self):
        txt = ct.render_table(self._pool(), "2026-08-04")
        assert "数据不足" in txt and "不等于不符合条件" in txt

    def test_main_table_has_label_column(self):
        txt = ct.render_table(self._pool(), "2026-08-04")
        assert "| 4面共振 | 平台回踩 | 标注 | 分层 |" in txt
        assert "3/11 QD·RS·B2" in txt or "3/11" in txt

    def test_no_signals_section_when_absent(self):
        """候选没有 signals 字段（旧产物）时不渲染该区块，且不报错。"""
        pool = self._pool()
        for c in pool["candidates"]:
            c.pop("signals", None)
        txt = ct.render_table(pool, "2026-08-04")
        assert "🏷️ 信号标注一览" not in txt


class TestLabelsNeverAlterSelection:
    """**核心断言**：标注命中什么都不能改变选股输出（A 类改动的定义）。"""

    def _pool_with(self, signals):
        return {"date": "2026-08-04", "status": "ok", "amv_state": "做多",
                "market_permission": "允许", "bucket_counts": {"A": 1, "B": 1},
                "candidates": [_cand("600000", "浦发银行", "A", signals=signals),
                               _cand("000001", "平安银行", "B", signals=signals)]}

    def _selection_part(self, text: str) -> str:
        """剥掉标注区块与主表标注列，只留选股输出。"""
        out, skipping = [], False
        for line in text.split("\n"):
            if line.startswith("## 🏷️"):
                skipping = True
                continue
            if skipping:
                if line.startswith("## "):
                    skipping = False
                else:
                    continue
            if line.startswith("| 600000") or line.startswith("| 000001"):
                cells = line.split("|")
                if len(cells) > 18:
                    del cells[17]           # 抹掉标注列
                    line = "|".join(cells)
            out.append(line)
        return "\n".join(out)

    VARIANTS = [
        _sig(),                                             # 全 miss
        _sig({"qsx_gt_dks", "rsi_strong", "b2", "surge_then_b1", "main_rally"}),
        _sig(na=set(sl.SIGNAL_META)),                        # 全 unavailable
        _sig({"qsx_gt_dks"}, neg={"distribution_risk"}),     # 含负向
    ]

    @pytest.mark.parametrize("signals", VARIANTS)
    def test_selection_output_identical(self, signals):
        base = self._selection_part(ct.render_table(self._pool_with(_sig()), "2026-08-04"))
        got = self._selection_part(ct.render_table(self._pool_with(signals), "2026-08-04"))
        assert got == base, "标注改变了选股输出——违反 A 类改动的定义"

    @pytest.mark.parametrize("signals", VARIANTS)
    def test_bucket_and_next_step_unchanged(self, signals):
        """只看 A/B 池主表（20 列）——报告里还有 10 列的「牛股候选」表，会撞列号。"""
        txt = ct.render_table(self._pool_with(signals), "2026-08-04")
        checked = 0
        for ln in txt.split("\n"):
            if not ln.startswith("| 600000"):
                continue
            cells = [c.strip() for c in ln.split("|")[1:-1]]
            if len(cells) != 20:
                continue
            assert cells[17] == "A", f"分层被改写: {cells[17]}"
            assert cells[19] == "generate_buy_plan", f"next_step 被改写: {cells[19]}"
            checked += 1
        assert checked >= 1, "未找到 A 池主表行（20 列）"

    def test_score_candidates_passes_signals_through(self):
        """必须**透传** signals——它是显式字段白名单，不加就会被丢掉。

        2026-08-04 实盘踩过：157 只候选、信号标注区块全空，因为 enrich 落盘的
        signals 没进 score_candidates 的输出白名单。传递与消费是两件事。
        """
        import inspect

        from custos.pipeline.screening import score_candidates as sc
        src = inspect.getsource(sc)
        assert '"signals": cand.get("signals")' in src, "signals 必须透传，否则标注层失效"

    def test_score_candidates_does_not_consume_signals(self):
        """但**不得消费**：一旦读进打分逻辑就从 A 类（纯标注）变成 B 类（改分层）。

        判据：直接从候选取 signals 的表达式（`cand.get("signals")` / `cand["signals"]`）
        全文**只允许出现一次**，即返回字典里的那行透传。
        注意不能按行搜 "signals" —— 既有的 `repair_signals` 字典**内部**也有个
        `signals` 键（`(cand.get("repair_signals") or {}).get("signals")`），会误报。
        """
        import inspect

        from custos.pipeline.screening import score_candidates as sc
        src = inspect.getsource(sc)
        # 只查真正的依赖形式。不能搜字符串 "signal_labels"——
        # score_candidates 的注释里会提到 tests/test_signal_labels.py（本测试自己）。
        for bad in ("import signal_labels", "from signal_labels"):
            assert bad not in src, f"打分层不得依赖 signal_labels: {bad}"
        pats = ('cand.get("signals")', "cand.get('signals')",
                'cand["signals"]', "cand['signals']")
        direct = sum(src.count(x) for x in pats)
        assert direct == 1, (
            f"直接取候选 signals 的地方应恰好 1 处（白名单透传），实际 {direct} 处——"
            "多出来的很可能是把标注读进了打分逻辑")
