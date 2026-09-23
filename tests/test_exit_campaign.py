# -*- coding: utf-8 -*-
"""R37 战役壳钉测：CTL-1~5 状态机 / 台账契约 / 端到端 fake 战役 / CLI 护栏。

全程合成注入 fake 评估器，不碰数据（本机无通达信）；预注册语义逐条钉：
- CTL-4 过线停（C2+C3+C4 全过 ⇒ candidate_found，C5 注记随行）；
- CTL-2 转向（家族全部变体连死 2 批关闭，死刑入台账）；
- CTL-3 证伪停（连 3 批无 C2 或全家族关闭 ⇒ falsified=结局②）；
- CTL-5 预算帽（耗尽无候选 ⇒ budget_exhausted 按证伪读）；
- CTL-1 继续（幸存者 top-k 排序进变异母体）；
- 台账每批原子重写、--resume 从状态续跑（v0.258 教训）。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from custos.research import exit_campaign as ec
from custos.research import score_evolution_study as ses
from custos.research.evolution import exit_genome as eg


def _rd(margin, objective, n_taken=200):
    """合成读数块（键 = ec.READING_KEYS 契约）。"""
    return {
        "objective": objective,
        "margin": margin,
        "expectancy_R": 0.1,
        "payoff_ratio": 2.0,
        "win_rate": 0.4,
        "n": 500,
        "n_taken": n_taken,
        "n_candidates": 800,
        "ret_over_dd": 1.0,
    }


def _baseline(state, margin=0.10, objective=0.20):
    state.baseline = {
        "mining": _rd(margin, objective),
        "judgment": _rd(margin, objective),
    }


def _dead_eval(*a, **k):  # 不应被调到（无 C2 过线 ⇒ 无 C3）
    raise AssertionError("无 C2 过线者时不许跑 C3")


def _both(margin, objective, n_taken=200):
    return {
        "mining": _rd(margin, objective, n_taken),
        "judgment": _rd(margin, objective, n_taken),
    }


class TestContract:
    def test_v0_portfolio_no_drift(self):
        # 组合层参数与 score_evolution_study V0 臂同源（漂移即红）
        assert ec.V0_PORTFOLIO == ses._V0_PORTFOLIO

    def test_reading_keys_pinned(self):
        assert ec.READING_KEYS == (
            "objective",
            "margin",
            "expectancy_R",
            "payoff_ratio",
            "win_rate",
            "n",
            "n_taken",
            "n_candidates",
            "ret_over_dd",
        )


class TestCtl4CandidateFound:
    """CTL-4 过线停：C2 双窗正 + C3 零翻转 + C4 打过随机天花板 ⇒ candidate。"""

    def _fake(self, params, *, start, end):
        # 胜区 = stop_pct≥6 ∧ time_stop 开（perturb_50 动不出该区域 ⇒ C3 恒零翻转）
        win = params["stop_pct"] >= 6 and params["time_stop_bars"] > 0
        return _rd(0.30, 0.95) if win else _rd(0.05, 0.05)

    def test_candidate_found(self):
        cfg = ec.CampaignConfig(batch_size=1, n_random=1, seed=5)
        state = ec.CampaignState()
        _baseline(state)
        winner = eg.normalize({"stop_pct": 12, "time_stop_bars": 40})
        loser = eg.normalize({"stop_pct": 4})
        rec = ec.ctl_step(
            cfg,
            state,
            [winner],
            [loser],
            [_both(0.30, 0.95)],
            [_rd(0.05, 0.05)],
            self._fake,
            random.Random(7),
        )
        assert state.status == "candidate_found"
        assert state.best_candidate["key"] == eg.genome_key(winner)
        assert "C5" in state.best_candidate["note"]
        assert rec["c3"]["pass"] and rec["c3"]["flips"] == 0
        assert rec["ctl_actions"][0]["type"] == "candidate_found"
        assert rec["random_ceiling"] == 0.05

    def test_near_miss_c4_below_random_ceiling(self):
        cfg = ec.CampaignConfig(batch_size=1, n_random=1, seed=5)
        state = ec.CampaignState()
        _baseline(state)
        winner = eg.normalize({"stop_pct": 12, "time_stop_bars": 40})
        hot_random = eg.normalize({"stop_pct": 4})
        rec = ec.ctl_step(
            cfg,
            state,
            [winner],
            [hot_random],
            [_both(0.30, 0.95)],
            [_rd(0.05, 0.99)],  # 随机天花板 0.99 > 候选 0.95
            self._fake,
            random.Random(7),
        )
        assert state.status == "running"  # 近失不停战役
        assert state.best_candidate is None
        assert state.consecutive_no_c2 == 0  # C2 过 ⇒ 证伪计数复位
        assert rec["ctl_actions"][0]["why"] == "c4_below_random_ceiling"

    def test_near_miss_c3_flipped(self):
        # 胜区收窄到 stop_pct==12：±50% 扰动会吸出该档 ⇒ C3 翻转
        def fake(params, *, start, end):
            win = params["stop_pct"] == 12 and params["time_stop_bars"] > 0
            return _rd(0.30, 0.95) if win else _rd(0.05, 0.05)

        cfg = ec.CampaignConfig(batch_size=1, n_random=1, seed=5, c3_draws=4)
        state = ec.CampaignState()
        _baseline(state)
        winner = eg.normalize({"stop_pct": 12, "time_stop_bars": 40})
        loser = eg.normalize({"stop_pct": 4})
        rec = None
        for seed in range(50):  # 找一个 4 抽内出现翻转移档的种子（确定性）
            st = ec.CampaignState()
            _baseline(st)
            r = ec.ctl_step(
                cfg,
                st,
                [winner],
                [loser],
                [_both(0.30, 0.95)],
                [_rd(0.05, 0.05)],
                fake,
                random.Random(seed),
            )
            if not r["c3"]["pass"]:
                rec, state = r, st
                break
        assert rec is not None, "50 个种子里应有一次 C3 翻转"
        assert rec["c3"]["flips"] >= 1
        assert rec["ctl_actions"][0]["why"] == "c3_flipped"
        assert state.status == "running"


class TestCtl2FamilyDeath:
    def test_family_closes_after_streak(self):
        cfg = ec.CampaignConfig(batch_size=1, n_random=0, family_death_streak=2)
        state = ec.CampaignState()
        _baseline(state)
        g_trail = eg.normalize({"stop_pct": 5, "trail_pct": 0.10})
        rng = random.Random(1)
        ec.ctl_step(cfg, state, [g_trail], [], [_both(0.05, 0.05)], [], _dead_eval, rng)
        assert state.family_dead_streak["trail"] == 1
        assert "trail" in state.open_families
        rec2 = ec.ctl_step(
            cfg, state, [g_trail], [], [_both(0.05, 0.05)], [], _dead_eval, rng
        )
        assert "trail" not in state.open_families
        assert state.family_deaths == ["trail"]
        assert any(a["type"] == "family_closed" for a in rec2["ctl_actions"])

    def test_winning_variant_resets_streak(self):
        cfg = ec.CampaignConfig(batch_size=2, n_random=0, family_death_streak=2)
        state = ec.CampaignState()
        _baseline(state)
        state.family_dead_streak["trail"] = 1
        g_trail = eg.normalize({"stop_pct": 5, "trail_pct": 0.10})
        # 一个变体挖掘窗 Δmargin>0（即便判定窗不正 ⇒ C2 不过）⇒ 连死复位
        rw = {"mining": _rd(0.20, 0.30), "judgment": _rd(0.05, 0.05)}
        ec.ctl_step(cfg, state, [g_trail], [], [rw], [], _dead_eval, random.Random(1))
        assert state.family_dead_streak["trail"] == 0
        assert "trail" in state.open_families

    def test_no_variant_no_evidence(self):
        cfg = ec.CampaignConfig(batch_size=1, n_random=0, family_death_streak=2)
        state = ec.CampaignState()
        _baseline(state)
        g_qsx = eg.normalize({"stop_pct": 5, "qsx_exit_consec": 2})
        ec.ctl_step(
            cfg,
            state,
            [g_qsx],
            [],
            [_both(0.05, 0.05)],
            [],
            _dead_eval,
            random.Random(1),
        )
        assert "trail" not in state.family_dead_streak  # 无变体 ⇒ 不计数


class TestC2Gate:
    def test_requires_dual_window_positive(self):
        cfg = ec.CampaignConfig(batch_size=2, n_random=0)
        state = ec.CampaignState()
        _baseline(state)
        g = eg.normalize({"stop_pct": 6})
        rw = {"mining": _rd(0.30, 0.50), "judgment": _rd(0.05, 0.05)}  # 判定窗负
        rec = ec.ctl_step(cfg, state, [g], [], [rw], [], _dead_eval, random.Random(1))
        assert rec["evolve"][0]["c2"] is False
        assert state.consecutive_no_c2 == 1

    def test_requires_n_taken(self):
        cfg = ec.CampaignConfig(batch_size=1, n_random=0, min_n_taken=100)
        state = ec.CampaignState()
        _baseline(state)
        g = eg.normalize({"stop_pct": 6})
        rec = ec.ctl_step(
            cfg,
            state,
            [g],
            [],
            [_both(0.30, 0.50, n_taken=80)],
            [],
            _dead_eval,
            random.Random(1),
        )
        assert rec["evolve"][0]["c2"] is False

    def test_missing_readings_not_c2(self):
        cfg = ec.CampaignConfig(batch_size=1, n_random=0)
        state = ec.CampaignState()
        _baseline(state)
        g = eg.normalize({"stop_pct": 6})
        rw = {"mining": None, "judgment": _rd(0.30, 0.50)}
        rec = ec.ctl_step(cfg, state, [g], [], [rw], [], _dead_eval, random.Random(1))
        assert rec["evolve"][0]["c2"] is False
        assert rec["evolve"][0]["d_margin_mining"] is None


class TestCtl1Survivors:
    def test_population_topk_by_mining_objective(self):
        cfg = ec.CampaignConfig(batch_size=3, n_random=0, n_survivors=2)
        state = ec.CampaignState()
        _baseline(state)
        gs = [eg.normalize({"stop_pct": x}) for x in (4, 5, 6)]
        rws = [_both(0.05, 0.05), _both(0.05, 0.09), _both(0.05, 0.07)]
        ec.ctl_step(cfg, state, gs, [], rws, [], _dead_eval, random.Random(1))
        assert [p["genome"]["stop_pct"] for p in state.population] == [5.0, 6.0]


# ---------------------------------------------------------------------------
# 端到端 fake 战役
# ---------------------------------------------------------------------------


def _fake_all_lose(params, *, start, end):
    if eg.genome_key(params) == eg.genome_key(eg.baseline_genome()):
        return _rd(0.10, 0.20)
    return _rd(0.05, 0.05)


def _fake_all_win_c2(params, *, start, end):
    if eg.genome_key(params) == eg.genome_key(eg.baseline_genome()):
        return _rd(0.10, 0.20)
    return _rd(0.30, 0.50)


class TestEndToEnd:
    def test_falsified_stop(self, tmp_path):
        cfg = ec.CampaignConfig(batch_size=6, n_random=2, seed=11)
        rep = ec.run_campaign(
            cfg, _fake_all_lose, tmp_path / "t" / "campaign_ledger.json", tag="t"
        )
        assert rep["status"] == "falsified"
        assert "结局②" in rep["verdict"]
        doc = json.loads((tmp_path / "t" / "campaign_ledger.json").read_text())
        assert doc["schema"] == ec.LEDGER_SCHEMA
        assert 2 <= len(doc["batches"]) <= 3
        actions = [a for b in doc["batches"] for a in b["ctl_actions"]]
        assert any(a["type"] == "family_closed" for a in actions)
        assert actions[-1]["type"] == "falsified"
        assert rep["notes"] == [ec._R11_NOTE]

    def test_budget_exhausted_with_near_miss(self, tmp_path):
        # 全赢 C2 但随机臂同为 0.50 ⇒ C4 不过（不严格大于）⇒ 近失连发到预算帽
        cfg = ec.CampaignConfig(batch_size=4, n_random=2, budget_cap=12, seed=3)
        rep = ec.run_campaign(
            cfg, _fake_all_win_c2, tmp_path / "t" / "campaign_ledger.json", tag="t"
        )
        assert rep["status"] == "budget_exhausted"
        assert "预算帽" in rep["verdict"]
        doc = json.loads((tmp_path / "t" / "campaign_ledger.json").read_text())
        assert len(doc["batches"]) == 2  # 6+6 = 12 帽
        near = [
            a
            for b in doc["batches"]
            for a in b["ctl_actions"]
            if a["type"] == "near_miss"
        ]
        assert near and all(a["why"] == "c4_below_random_ceiling" for a in near)
        assert rep["random_ceiling"] == 0.50
        assert rep["baseline"]["mining"]["margin"] == 0.10

    def test_candidate_found_end_to_end(self, tmp_path):
        # trail 关 ⇒ 胜（n_random=0 ⇒ C4 无臂不拦）；C3 扰动不动开关 ⇒ 恒零翻转
        def fake(params, *, start, end):
            if eg.genome_key(params) == eg.genome_key(eg.baseline_genome()):
                return _rd(0.10, 0.20)
            return _rd(0.05, 0.05) if params["trail_pct"] > 0 else _rd(0.30, 0.60)

        cfg = ec.CampaignConfig(batch_size=16, n_random=0, seed=23)
        rep = ec.run_campaign(
            cfg, fake, tmp_path / "t" / "campaign_ledger.json", tag="t"
        )
        assert rep["status"] == "candidate_found"
        assert "C5" in rep["verdict"]
        assert rep["best_candidate"]["genome"]["trail_pct"] == 0.0
        assert rep["n_batches"] == 1

    def test_resume_continues_from_ledger(self, tmp_path):
        ledger = tmp_path / "t" / "campaign_ledger.json"
        cfg = ec.CampaignConfig(batch_size=4, n_random=2, seed=17, max_batches=1)
        rep1 = ec.run_campaign(cfg, _fake_all_lose, ledger, tag="t")
        assert rep1["status"] == "running" and rep1["n_batches"] == 1
        # 宿主杀后重启：读台账续跑（此处把护栏改 2 批模拟「继续」）
        cfg_l, state, batches, tag = ec.load_ledger(ledger)
        assert state.batch_id == 1 and state.total_genomes == 6
        cfg2 = ec.CampaignConfig(**{**cfg_l.__dict__, "max_batches": 2})
        ec.save_ledger(ledger, tag, cfg2, state, batches)
        rep2 = ec.run_campaign(cfg, _fake_all_lose, ledger, resume=True, tag="t")
        assert rep2["n_batches"] == 2
        assert rep2["total_genomes"] == 12
        assert rep2["status"] == "running"

    def test_resume_terminal_report_only(self, tmp_path, capsys):
        ledger = tmp_path / "t" / "campaign_ledger.json"
        cfg = ec.CampaignConfig(batch_size=6, n_random=2, seed=11)
        ec.run_campaign(cfg, _fake_all_lose, ledger, tag="t")
        rep = ec.run_campaign(cfg, _fake_all_lose, ledger, resume=True, tag="t")
        assert rep["status"] == "falsified"  # 不续跑，直接再出报告
        assert "已有结局" in capsys.readouterr().err

    def test_max_batches_guard_is_not_a_verdict(self, tmp_path):
        cfg = ec.CampaignConfig(batch_size=4, n_random=1, seed=5, max_batches=1)
        rep = ec.run_campaign(
            cfg, _fake_all_win_c2, tmp_path / "t" / "campaign_ledger.json", tag="t"
        )
        assert rep["status"] == "running"
        assert "非结局" in rep["verdict"]


class TestCli:
    def _argv(self, tmp_path, *extra):
        return [
            "--tag",
            "t",
            "--out-dir",
            str(tmp_path),
            "--codes",
            "000001",
            "--batch-size",
            "2",
            "--n-random",
            "1",
            "--max-batches",
            "1",
            *extra,
        ]

    def test_main_smoke_with_injected_evaluator(self, tmp_path):
        rc = ec.main(self._argv(tmp_path), evaluator=_fake_all_lose)
        assert rc == 0
        assert (tmp_path / "t" / "campaign_ledger.json").exists()
        assert (tmp_path / "t" / "_exit_campaign__t.json").exists()

    def test_existing_ledger_requires_resume(self, tmp_path):
        ec.main(self._argv(tmp_path), evaluator=_fake_all_lose)
        with pytest.raises(SystemExit):
            ec.main(self._argv(tmp_path), evaluator=_fake_all_lose)

    def test_bad_batch_size_rejected(self, tmp_path):
        with pytest.raises(SystemExit):
            ec.main(self._argv(tmp_path, "--batch-size", "0"), evaluator=_fake_all_lose)

    def test_bad_budget_rejected(self, tmp_path):
        with pytest.raises(SystemExit):
            ec.main(self._argv(tmp_path, "--budget", "0"), evaluator=_fake_all_lose)
