# -*- coding: utf-8 -*-
"""R37-C5 终审终端钉测（exit_c5_terminal.py）。

锁的契约：基因组键解析 fail-closed、两窗合并标尺自含读取、v0.273 否决
条件三条款（含 n<200 量级停用前置）、配对 bootstrap 可复现、CLI 只接受
pre2019 段内窗口（硬拒绝镜像）。
"""

from __future__ import annotations

import argparse

import pytest

from custos.research import exit_c5_terminal as c5
from custos.research.load_window import count_for_start, resolve_count

CAND_KEY = (
    "sp8|breakeven=off|cost_zone=3x2|qsx=off|scale_out=off|time_stop=20|trail=0.08"
)


def _trade(code, day, ret):
    return {
        "code": code,
        "entry_date": day,
        "ret": ret,
        "reason": "stop",
        "holding": 5,
    }


class TestParseGenomeKey:
    def test_roundtrip_candidate_key(self):
        from custos.research.evolution import exit_genome as eg

        g = c5.parse_genome_key(CAND_KEY)
        assert eg.genome_key(g) == CAND_KEY  # 归一后键复原
        assert g["stop_pct"] == 8.0
        assert g["cost_zone_bars"] == 3.0 and g["cost_zone_pct"] == 2.0
        assert g["time_stop_bars"] == 20.0 and g["trail_pct"] == 0.08
        assert g["breakeven_trigger"] == 0.0  # 关闭家族归零

    def test_rejects_bad_inputs(self):
        for bad in (
            "cost_zone=3x2",  # 无 sp 起手
            "sp8|unknown=off",  # 未知家族
            "sp8|cost_zone=3",  # 双参家族缺一档
            "sp7|trail=0.08",  # stop_pct 不在档位
            "sp8|trail=0.07",  # 档位外取值
        ):
            with pytest.raises(ValueError):
                c5.parse_genome_key(bad)


class TestCombinedYardstick:
    def test_n_weighted_merge(self):
        rep = {
            "best_candidate": {
                "d_margin_mining": 0.0336,
                "d_margin_judgment": 0.0034,
                "mining": {"n_taken": 243},
                "judgment": {"n_taken": 146},
            }
        }
        y = c5.combined_yardstick(rep)
        assert y["combined"] == pytest.approx(
            (0.0336 * 243 + 0.0034 * 146) / 389, rel=1e-6
        )
        # 保留率 0.0034/0.0336 ≈ 10% < 0.5 ⇒ degraded ⇒ γ=0.75（v0.276 分档）
        assert y["retention"] == pytest.approx(0.0034 / 0.0336, rel=1e-3)
        assert y["candidate_degraded"] is True
        assert y["gamma"] == 0.75
        assert y["bar"] == pytest.approx(0.75 * y["combined"])

    def test_non_degraded_gets_gamma_half(self):
        rep = {
            "best_candidate": {
                "d_margin_mining": 0.0942,
                "d_margin_judgment": 0.1347,  # 保留率 143% ⇒ 非 degraded
                "mining": {"n_taken": 158},
                "judgment": {"n_taken": 101},
            }
        }
        y = c5.combined_yardstick(rep)
        assert y["candidate_degraded"] is False
        assert y["gamma"] == 0.5

    def test_missing_pieces_raise(self):
        with pytest.raises(ValueError):
            c5.combined_yardstick({"best_candidate": {"d_margin_mining": 0.01}})

    def test_nonpositive_mining_margin_raises(self):
        rep = {
            "best_candidate": {
                "d_margin_mining": 0.0,
                "d_margin_judgment": 0.01,
                "mining": {"n_taken": 100},
                "judgment": {"n_taken": 100},
            }
        }
        with pytest.raises(ValueError):
            c5.combined_yardstick(rep)


class TestApplyC5:
    """C5 判决 v0.299：a_sample 可疑闸（n<100 ⇒ 跑数可疑不出判决，双向压）
    + thr 三分（CI_hi<thr 杀 / CI_lo>0 且点估计≥thr 活 / 其余 untested）
    + 全条款不短路（would_fire 与判决同 CI 口径）。

    ⚠️ v0.281 的废因（「交易数由钉死信号集决定 ⇒ 任何候选 ~64 笔/n~21 ⇒
    恒触发必杀门」）已被 v0.288 全历史复跑**推翻**：真因是加载到达截断，
    「64 对足以给出 −3.25·SE 决定性读数」是 ~1% 碎片宇宙产物（全数据下
    符号翻转）。教训反向：bootstrap CI 只覆盖抽样误差、不覆盖样本偏差；
    当时暴露异常的恰是被废的 n 门槛 ⇒ n 远低于预期=数据完整性信号，
    升级为可疑闸（v0.299 owner review）。
    """

    YARD = {"combined": 0.0222, "bar": 0.0111, "gamma": 0.5}

    def test_r37_b1_fragment_readings_now_suspicious_no_verdict(self):
        """⚠️⚠️ 关键回归：碎片首跑读数（后被查明是加载截断产物）在新规则
        下**不出判决**——n_taken=21 远低于预期 ⇒ 跑数可疑。

        这正是可疑闸要拦的形态：CI95 全负（旧规判 killed）但样本本身不可信
        ——首跑的 killed 就是「数据坏了误判成候选坏了」的实例（全历史复跑
        r37_c5_v2 符号翻转 −0.2396→+0.0033）。diagnostics 如实留痕 b_sign
        单看会触发，但判决被可疑闸压住（双向压的杀侧）。
        """
        v = c5.apply_c5(21, -0.2396, self.YARD, [-0.407, -0.124])
        assert v["verdict"] == c5.VERDICT_UNTESTED
        assert v["sample_suspicious"] is True
        assert v["fired"] == [], "可疑跑数不产生否决依据"
        got = {d["clause"]: d["would_fire"] for d in v["diagnostics"]}
        assert got["a_sample"] is True
        assert got["b_sign"] is True, "CI 全负单看会触发——被可疑闸压住，如实留痕"

    def test_small_sample_with_clean_positive_ci_also_suspicious(self):
        """⚠️ 可疑闸**双向压**（放行侧）：小样本 + CI 全正同样不出判决。

        旧规在此放 not_vetoed，依据是「r37_c5 自证 64 对足以给出 −3.25·SE
        决定性读数」——该依据已被查明是碎片宇宙产物；CI 只覆盖抽样误差、
        不覆盖样本偏差 ⇒ n 远低于预期时两个方向都不出判决。
        """
        v = c5.apply_c5(21, 0.05, self.YARD, [0.01, 0.09])
        assert v["verdict"] == c5.VERDICT_UNTESTED
        assert v["sample_suspicious"] is True

    def test_boundary_n100_not_suspicious(self):
        """边界钉死：n_taken=100 恰好到下限 ⇒ 不触发可疑闸，正常三分。"""
        v = c5.apply_c5(100, 0.05, self.YARD, [0.01, 0.09])
        assert v["sample_suspicious"] is False
        assert v["verdict"] == c5.VERDICT_NOT_VETOED

    def test_ci_spans_zero_is_untested_not_killed(self):
        """⚠️ CI 跨 0 = 样本无法解析符号 ⇒ untested，**既不杀也不放行**。

        旧规在此恒判 killed（n<100），把「没测出来」记成「确实不行」——
        两者对档案是完全不同的结论。（n=150 隔离可疑闸，纯测跨 0 形。）
        """
        v = c5.apply_c5(150, 0.05, self.YARD, [-0.03, 0.12])
        assert v["verdict"] == c5.VERDICT_UNTESTED
        assert v["sample_suspicious"] is False
        assert v["fired"] == [], "untested 不得产生否决依据"
        assert "既不进 Phase 4" in v["note"]

    def test_ci_unavailable_is_untested(self):
        """bootstrap 失败（配对太少 ⇒ se/ci 为 None）同样是 untested。"""
        assert c5.apply_c5(150, 0.05, self.YARD, None)["verdict"] == c5.VERDICT_UNTESTED
        assert c5.apply_c5(150, 0.05, self.YARD, [])["verdict"] == c5.VERDICT_UNTESTED

    def test_magnitude_clause_disabled_below_200(self):
        v = c5.apply_c5(150, 0.005, self.YARD, [0.001, 0.01])
        assert v["verdict"] == c5.VERDICT_NOT_VETOED
        assert v["magnitude_clause_active"] is False

    def test_magnitude_clause_kills_at_200(self):
        v = c5.apply_c5(250, 0.005, self.YARD, [0.001, 0.01])
        assert v["verdict"] == c5.VERDICT_KILLED
        assert [f["clause"] for f in v["fired"]] == ["c_magnitude"]
        got = {d["clause"]: d["would_fire"] for d in v["diagnostics"]}
        assert got["c_magnitude"] is True  # CI 整体 < thr，诊断与判决一致

    def test_all_pass_not_vetoed(self):
        v = c5.apply_c5(250, 0.02, self.YARD, [0.01, 0.03])
        assert v["verdict"] == c5.VERDICT_NOT_VETOED
        assert v["fired"] == []

    def test_would_fire_aligned_to_ci_basis(self):
        """v0.299 对齐（owner review）：diagnostics 的 would_fire 改与判决同
        CI 口径——原按点估计算，「CI 全正、点估计 < bar」诊断误标
        would_fire=True 而判决 untested，两者对不上。"""
        yard = {"combined": 0.0222, "bar": 0.0111, "gamma": 0.5}
        v = c5.apply_c5(250, 0.010, yard, [0.001, 0.02])  # CI 全正、点估计<bar
        assert v["verdict"] == c5.VERDICT_UNTESTED
        got = {d["clause"]: d["would_fire"] for d in v["diagnostics"]}
        assert got["c_magnitude"] is False, "CI 乐观端越线 ⇒ 单看也不触发"
        assert got["b_sign"] is False

    def test_b_sign_would_fire_on_ci_not_point(self):
        """b_sign 同 CI 口径：点估计为正但 CI95 全负 ⇒ killed + 诊断触发
        （旧点估计口径会标 False，与判决对不上）。"""
        v = c5.apply_c5(150, 0.05, self.YARD, [-0.03, -0.01])
        assert v["verdict"] == c5.VERDICT_KILLED
        assert [f["clause"] for f in v["fired"]] == ["b_sign"]
        got = {d["clause"]: d["would_fire"] for d in v["diagnostics"]}
        assert got["b_sign"] is True

    def test_diagnostics_record_every_clause_without_short_circuit(self):
        """⚠️ 全条款独立求值——旧实现 clause(a) 触发后 b/c 不再求值，
        `fired` 只含 a_sample，r37_c5 那条更强的证据（Δ/SE=−3.25）因此
        不在 fired 里、只能靠报告顶层字段捞回。战役壳是后续战役的 generic
        载体，档案精度值得。（可疑跑数同样逐条留痕：谁触发了可疑闸、
        哪些条款单看会触发，一目了然。）"""
        v = c5.apply_c5(21, -0.2396, self.YARD, [-0.407, -0.124])
        got = {d["clause"]: d["would_fire"] for d in v["diagnostics"]}
        assert set(got) == {"a_sample", "b_sign", "c_magnitude"}
        assert got["a_sample"] is True and got["b_sign"] is True
        assert got["c_magnitude"] is False, "n<200 ⇒ 量级条款停用，不该 would_fire"

    def test_missing_margin_never_passes(self):
        """读数缺失时不得放行（保守）——CI 也缺 ⇒ untested。"""
        assert c5.apply_c5(150, None, self.YARD, None)["verdict"] == c5.VERDICT_UNTESTED

    def test_verdict_is_one_of_three(self):
        for n, dm, ci in (
            (21, -0.2, [-0.3, -0.1]),
            (150, 0.05, [-0.1, 0.2]),
            (250, 0.02, [0.01, 0.03]),
            (250, 0.005, [0.001, 0.01]),
        ):
            v = c5.apply_c5(n, dm, self.YARD, ci)
            assert v["verdict"] in (
                c5.VERDICT_KILLED,
                c5.VERDICT_NOT_VETOED,
                c5.VERDICT_UNTESTED,
            )


class TestApplyC5V298Migration:
    """v0.298 迁移的读数形（owner 逐案核过）。"""

    def test_ci_spans_zero_but_below_bar_kills(self):
        """① CI 跨 0 但整体低于 bar ⇒ killed（r37_c5_v2 读数形）。

        v0.281 里量级条款只在 CI 全正时才被咨询，这种形态漏网成 untested；
        v0.298：连 CI 乐观端（hi +0.0063）都够不到 thr（0.0167）⇒ 证据性否决。
        ⚠️ 真实 r37_c5_v2 的判决**维持 untested 不翻**（owner 拍板：看过数据
        再改判=事后判据；R37 已收口不接 live，仅档案措辞——注记在 R37 文档）。
        本钉测锁的是**新判据逻辑**对该读数形的映射，不是翻旧案。
        """
        yard = {"combined": 0.0223, "bar": 0.0167, "gamma": 0.75}
        v = c5.apply_c5(536, 0.0033, yard, [-0.0002, 0.0063])
        assert v["verdict"] == c5.VERDICT_KILLED
        assert [f["clause"] for f in v["fired"]] == ["c_magnitude"]
        assert v["threshold"] == 0.0167

    def test_all_positive_but_point_below_bar_is_untested(self):
        """② CI 全正但点估计 < bar ⇒ untested（v0.281 按点估计杀 ⇒ 放宽）。

        新规则：杀要求**整个 CI** 低于标尺——CI 乐观端越线（hi 0.02 > bar
        0.0111）就不能叫「证据性否决」；活要求 lo>0 ∧ 点估计≥bar——点估计
        不达标也不能放行。两不占 ⇒ untested。
        """
        yard = {"combined": 0.0222, "bar": 0.0111, "gamma": 0.5}
        v = c5.apply_c5(250, 0.010, yard, [0.001, 0.02])
        assert v["verdict"] == c5.VERDICT_UNTESTED
        assert v["fired"] == []

    def test_r36_c5_shape_unchanged(self):
        """R36-C5 读数形（adx_gt_60：Δ+0.0309，CI95 [−0.072,+0.127]）不受影响：
        CI_hi +0.127 > bar 0.055 ⇒ 跨 thr ⇒ 仍 untested（owner 核过）。"""
        yard = {"combined": 0.1100, "bar": 0.0550, "gamma": 0.5}
        v = c5.apply_c5(376, 0.0309, yard, [-0.072, 0.127])
        assert v["verdict"] == c5.VERDICT_UNTESTED


class TestPairBootstrap:
    def test_pairs_on_intersection(self):
        cand = [
            _trade("000001", "2012-01-04", 0.05),
            _trade("000002", "2012-01-05", -0.02),
        ]
        base = [_trade("000001", "2012-01-04", 0.03)]
        pairs = c5.pair_trades(cand, base)
        assert len(pairs) == 1 and pairs[0][1]["ret"] == 0.03

    def test_bootstrap_reproducible_and_positive_se(self):
        cand = [
            _trade(
                f"{i:06d}",
                f"2012-03-{i % 28 + 1:02d}",
                0.02 + (i % 5) * 0.01 * (1 if i % 2 else -1),
            )
            for i in range(60)
        ]
        base = [
            _trade(
                f"{i:06d}",
                f"2012-03-{i % 28 + 1:02d}",
                0.01 + (i % 3) * 0.008 * (1 if i % 2 else -1),
            )
            for i in range(60)
        ]
        pairs = c5.pair_trades(cand, base)
        r1 = c5.paired_bootstrap(pairs, seed=7, n_boot=200)
        r2 = c5.paired_bootstrap(pairs, seed=7, n_boot=200)
        assert r1 == r2  # 种子复现
        assert r1["se"] is not None and r1["se"] > 0
        lo, hi = r1["ci95"]
        assert lo <= hi
        assert r1["n_days"] == 28  # 日簇（v0.324）：簇数=不同 entry_date 数

    def test_day_cluster_moves_together(self, monkeypatch):
        """v0.324 #3：成日重抽样——同日的配对必须同进同出（iid 才会拆开）。"""
        cand = [
            _trade("D1A", "2012-01-04", 0.05),
            _trade("D1B", "2012-01-04", -0.01),  # 与 D1A 同日
            _trade("D2A", "2012-01-05", 0.03),
            _trade("D3A", "2012-01-06", 0.02),
        ]
        base = [
            _trade("D1A", "2012-01-04", 0.02),
            _trade("D1B", "2012-01-04", 0.01),
            _trade("D2A", "2012-01-05", 0.01),
            _trade("D3A", "2012-01-06", 0.01),
        ]
        pairs = c5.pair_trades(cand, base)
        seen: list[set] = []
        real = c5._margin_of

        def spy(trades):
            seen.append({t["code"] for t in trades})
            return real(trades)

        monkeypatch.setattr(c5, "_margin_of", spy)
        out = c5.paired_bootstrap(pairs, seed=3, n_boot=50)
        assert out["n_days"] == 3
        assert seen, "spy 未捕获任何一次重抽"
        for codes in seen:
            assert ("D1A" in codes) == ("D1B" in codes), (
                f"同日对被拆开: {codes}——日簇语义破（退化成 iid）"
            )


class TestPre2019Guard:
    def test_rejects_window_outside_pre2019(self):
        ap = c5._build_parser()
        args = ap.parse_args(
            [
                "--genome",
                CAND_KEY,
                "--codes-file",
                "x.txt",
                "--campaign-report",
                "y.json",
                "--start",
                "2017-01-01",
            ]
        )
        with pytest.raises(SystemExit):
            c5._check_pre2019(args, ap)

    def test_accepts_default_window(self):
        ap = c5._build_parser()
        args = ap.parse_args(
            [
                "--genome",
                CAND_KEY,
                "--codes-file",
                "x.txt",
                "--campaign-report",
                "y.json",
            ]
        )
        c5._check_pre2019(args, ap)  # 不抛即过

    def test_default_count_auto_derives(self):
        """count 是「最新向前 N 根」滚动窗——缺省按 --start 自动推算
        （v0.328，owner review #9）：推算值必须覆盖 pre2019 起点以来的
        真实交易日+预热（fail-closed 方向），显式 100000 全历史仍是
        覆盖通道；实测到达由 check_reach 兜底（r36_c5 首跑 19 笔碎片教训）。"""
        ap = c5._build_parser()
        args = ap.parse_args(
            [
                "--genome",
                CAND_KEY,
                "--codes-file",
                "x.txt",
                "--campaign-report",
                "y.json",
            ]
        )
        assert args.count is None
        derived = resolve_count(args.count, c5.PRE2019_START)
        assert derived >= count_for_start(c5.PRE2019_START) >= 4000
        assert resolve_count(100000, c5.PRE2019_START) == 100000


class TestRunC5Guards:
    def test_campaign_key_mismatch_fails(self, tmp_path):
        rep = tmp_path / "rep.json"
        rep.write_text(
            '{"best_candidate": {"key": "sp5|breakeven=off|cost_zone=off|qsx=off|scale_out=off|time_stop=off|trail=0.08"}}',
            encoding="utf-8",
        )
        args = argparse.Namespace(
            genome=CAND_KEY,
            campaign_report=str(rep),
            codes_file="x",
            cost_bps=25.0,
            top_n=20,
            seed=1,
            n_bootstrap=10,
            tag="t",
            start=c5.PRE2019_START,
            end=c5.PRE2019_END,
        )
        with pytest.raises(RuntimeError, match="对账不符"):
            c5.run_c5(args, per_code={})
