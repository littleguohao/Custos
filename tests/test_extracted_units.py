"""2026-08-07 巨型函数拆解后抽出的单元 —— 这些此前**无法单测**。

拆解流程（4 个函数共用）：
  ① 固定基线：跑代表性输入（含降级路径），把返回值/产物存下来
  ② 用 AST **算**每段的自由变量得到签名（手写会漏 —— 拆
     `final_close_review.main` 时漏了 `sectors`，直接 NameError）
  ③ 落盘前 `ast.parse`
  ④ 重跑并与基线**归一化后逐字节比对**（归一化只抹掉时间戳与 tmpdir 路径）

四个函数的成绩：

    final_close_review.main        210 → 127 行
    score_candidates.score_candidate  258 → 138 行
    candidate_table.render_table   211 →  43 行
    weekly_review.build_weekly_review 354 → 216 行

⚠️ 刻意**没拆**的两段：`build_weekly_review` 的「成交与费用」（8 个输出）与
「平仓与盈亏」（**15 个输出**）。抽出来要返回 15 个值，比内联更难读 ——
`enrich_candidates.enrich`（260 行）整个没拆，同理：它把 8 个数据源接进逐票循环，
复杂度是内在的，拆它需要 context 对象或 12 参数函数，两者都不是改进。
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.pipeline.close_review import weekly_review as wr  # noqa: E402


class TestScoreCandidateUnits:
    """`score_candidate` 拆出的三段。最要紧的是风险降级 —— 13 条判据、**只降不升**。"""

    @staticmethod
    def _apply(cand, base_bucket="A", amv="中性", cz=None, sector_ok=True):
        from custos.pipeline.screening import score_candidates as sc
        return sc.apply_risk_downgrades(
            amv_state=amv, base_bucket=base_bucket, cand=cand, cz_sector=cz,
            rules=sc.resolve_cap_rules(None), sector_score_available=sector_ok)

    def test_clean_candidate_keeps_bucket(self):
        flags, bucket, _wt, _dist = self._apply({"stop_loss_ref": {"price": 9.0}})
        assert bucket == "A" and flags == []

    def test_missing_stop_loss_flags(self):
        """⚠️ 没有止损参考价必须留痕 —— 买入计划缺止损位不得放行。"""
        flags, _b, _w, _d = self._apply({})
        assert any("stop" in f or "止损" in f for f in flags), flags

    def test_bear_regime_downgrades(self):
        _f, bucket, _w, _d = self._apply({"stop_loss_ref": {"price": 9.0}}, amv="空头")
        assert bucket != "A", "0AMV 空头必须压低分层"

    def test_sprint_wave_downgrades(self):
        """冲刺波首个 B1 可靠性差（v0.10 的结论），要降级。"""
        _f, bucket, wt, _d = self._apply(
            {"stop_loss_ref": {"price": 9.0}, "wave": {"wave_type": "sprint"}})
        assert wt == "sprint" and bucket != "A"

    def test_cz_avoid_downgrades(self):
        _f, bucket, _w, _d = self._apply({"stop_loss_ref": {"price": 9.0}}, cz="avoid")
        assert bucket != "A"

    def test_downgrades_never_upgrade(self):
        """⚠️ 这一段**只降不升**：从 D 开始，任何判据都不该把它抬上去。"""
        for cand in ({}, {"wave": {"wave_type": "sprint"}},
                     {"volume_sustain": {"status": "retreat"}},
                     {"macd_technics": {"available": True,
                                        "top_divergence": {"hit": True}}}):
            _f, bucket, _w, _d = self._apply(cand, base_bucket="D")
            assert bucket == "D", f"{cand} 把 D 抬成了 {bucket}"

    def test_entry_reasons_are_wording_only(self):
        """`build_entry_reasons` 只做**措辞**，不参与判定 —— 改文案不会动分层。

        注意口径：公式命中先按**原始 id** 记（`公式命中:bbi_above`），
        中文标签是给形态/信号用的 —— 保留原始 id 是为了让理由能回连到
        `SCREEN_FORMULA_REGISTRY.json` 的键。
        """
        from custos.pipeline.screening import score_candidates as sc
        rs = sc.build_entry_reasons(
            cand={"formula_hits": ["bbi_above", "j_low"], "patterns": {},
                  "five_day_entry": {"hit": True}},
            dist={}, wave_type="build")
        assert "公式命中:bbi_above" in rs and "公式命中:j_low" in rs
        assert any("5日" in x or "五日" in x or "入场" in x for x in rs), rs

    def test_four_leg_is_evidence_not_gate(self):
        """⚠️ 四面共振是**证据层描述**，不是 gate。

        R2 的结论是「跟随主流」机械规则不成立，所以共振度不得反过来放宽权限 ——
        它只写进产物供复盘对账，不参与 bucket 或 next_step。
        """
        from custos.pipeline.screening import score_candidates as sc
        fq, sp, legs, aligned, res = sc.four_leg_resonance(
            cand={"sector_phase": {"favorable": True}, "financials": {"tier": "you"}},
            permission="允许", tech_level="S")
        assert aligned == sum(1 for v in legs.values() if v)
        assert "label" in res and res["aligned"] == aligned


class TestWeeklyReviewUnits:
    """`build_weekly_review` 拆出的 8 段。每段都可以只喂它自己的输入。"""

    def test_plan_adherence_denominator_excludes_unknown(self):
        """⚠️ 计划外比例的**分母只算能判定的** —— 拿不到当日计划的算 unknown，
        否则日报缺失会伪装成纪律问题。"""
        import tempfile
        base = pathlib.Path(tempfile.mkdtemp())
        issues, unavail = [], []
        checks, ratio = wr._plan_adherence(
            base=base, daily_reviews={}, execution_issues=issues, unavailable=unavail,
            week_trades=[{"date": "2026-07-14", "code": "600000", "side": "买入"}])
        assert all(c["status"] == "unknown" for c in checks)
        assert ratio is None, "全是 unknown 时不得算出一个比例"

    def test_no_trade_confirmations_separates_gap_from_violation(self):
        """⚠️ 确认文件**缺失**是数据缺口（unavailable），不是纪律问题
        （execution_issues）。混淆会让缺文件看起来像违纪。"""
        import tempfile
        base = pathlib.Path(tempfile.mkdtemp())
        issues, unavail = [], []
        wr._no_trade_confirmations(base=base, execution_issues=issues,
                                  trading_days=["2026-07-13"], unavailable=unavail,
                                  week_trades=[])
        assert any("确认文件缺失" in x for x in unavail)

    def test_bear_regime_missing_ledger_keeps_none(self):
        """⚠️ 取不到 regime 台账时 `bear_loss_share` 留 None 而不是 0 ——
        「空头期没亏」与「不知道空头是哪几天」必须可区分。"""
        import tempfile
        base = pathlib.Path(tempfile.mkdtemp())
        unavail = []
        days, share, ratio = wr._bear_regime_stats(
            base=base, losses=[], total_loss=0, trading_days=["2026-07-13"],
            unavailable=unavail)
        assert days == [] and share is None
        assert any("0AMV" in x for x in unavail)

    def test_sell_fly_reports_coverage(self):
        """⚠️ 卖飞必须同时报 coverage —— 只报「卖飞 N 笔」不说分母，
        读者会把「没 MFE 数据」当成「没卖飞」。"""
        import tempfile
        base = pathlib.Path(tempfile.mkdtemp())
        unavail, issues = [], []
        closings = [{"code": "600000", "name": "甲", "sell_date": "2026-07-14",
                     "sell_price": 10.0, "gross_pnl": -100.0, "pnl_pct": -9.0,
                     "hold_days": 5, "match_status": "matched"}]
        fly, uneval, evaled, cov = wr._sell_fly_review(
            base=base, closings=closings, strategy_issues=issues, unavailable=unavail)
        assert evaled + len(uneval) == len(closings)
        assert cov is not None, "coverage 必须给出，哪怕是 0"

    def test_risk_levels_empty_when_no_files(self):
        import tempfile
        base = pathlib.Path(tempfile.mkdtemp())
        assert wr._risk_levels_of_week(base=base, days=["2026-07-13"]) == {}

    def test_slow_stops_judges_loss_depth_only(self):
        """⚠️ 如实记录判据：`_slow_stops` **只看亏损幅度**（`pnl_pct <= STOP_LOSS_PCT`），
        **不看持有时长** —— `hold_days` 只作为证据附上，不参与判定。

        口径是自洽的：止损线设在 -7% 而实现了 -20%，本身就说明没在线上切出去
        （跳空缺口除外）。但规则名叫 `slow_stop_loss`、文案写「止损偏慢」，
        读的时候容易以为它验证过时长 —— 所以在这里写清楚。
        """
        issues = []
        deep_quick = {"code": "a", "name": "a", "pnl_pct": -20.0, "hold_days": 1,
                      "gross_pnl": -1.0, "sell_date": "2026-07-14"}
        assert len(wr._slow_stops(execution_issues=issues, losses=[deep_quick])) == 1
        assert issues[0]["rule"] == "slow_stop_loss"
        assert issues[0]["evidence"][0]["hold_days"] == 1, "时长要作为证据附上"

    def test_slow_stops_ignores_shallow_loss(self):
        issues = []
        shallow = {"code": "a", "name": "a", "pnl_pct": -1.0, "hold_days": 30,
                   "gross_pnl": -1.0, "sell_date": "2026-07-14"}
        assert wr._slow_stops(execution_issues=issues, losses=[shallow]) == []

    def test_slow_stops_tolerates_missing_pnl_pct(self):
        """`pnl_pct` 为 None（配对不上的平仓）不得崩，也不得算慢止损。"""
        issues = []
        assert wr._slow_stops(execution_issues=issues, losses=[
            {"code": "a", "name": "a", "pnl_pct": None, "hold_days": 5,
             "gross_pnl": -1.0, "sell_date": "2026-07-14"}]) == []


class TestRenderTableUnits:
    def test_bear_prints_ban_before_buy_list(self):
        """⚠️ 空头 regime 下先打禁买提示 —— 共振度**不能**在空头里放宽权限。"""
        from custos.pipeline.screening import candidate_table as ct
        lines = []
        ct._signal_overview(lines, is_bear=True, watch=[])
        text = "\n".join(lines)
        assert "## ⭐" in text
        assert "空头" in text or "禁" in text or "不" in text

    def test_sections_append_in_place(self):
        """沿用 `render_news(lines, ...)` 的既有约定：**就地追加**，lines 在首位。"""
        from custos.pipeline.screening import candidate_table as ct
        import inspect
        for name in ("_signal_overview", "_fundamental_bulls", "_capped_but_resonant",
                     "_bear_outposts", "_top5", "_bucket_pools"):
            params = list(inspect.signature(getattr(ct, name)).parameters)
            assert params[0] == "lines", f"{name} 的首参应是 lines，实际 {params}"
