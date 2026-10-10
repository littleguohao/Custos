# -*- coding: utf-8 -*-
"""各研究终端的零假设校准（强制两连测，owner 方法论 review #4，v0.323；
#1 返修 v0.330）。

每个接入 criteria_kit 的终端必须带：**纯噪声 confirmed_pass ≤ ~10%**
（5 种子实测须 0/5）+ **植入真 edge ⇒ confirmed_pass 可识别**——
新终端不过这两项不许合入（R40 C4 的 7/8 假阳性、R41 池子永远填不满，
都是临时写纯噪声脚本才发现的；现在变成合入门）。判定只看报告
``criteria.C4.state``（夹具见 tests/helpers_null.py）。

v0.330 返修（owner review #1）：
- 零假设改**逐笔 iid 方差**噪声（helpers_null 模块 docstring 有退化版
  教训——交替 ± 下 max-of-230 与 max-of-5 同数，选择效应不出现）；
- **池 ≥20**（原 n_random=4/min_pool=3 的 q95≈最大值，测不出 q95 校准）；
- bear 随机臂抽样改**逐次偏移** ``_rand_seq``（原 _rand_flat 对同一
  (n,w) 恒返回同一批入场 ⇒ 25 条臂全同，池退化——生产路径每臂 rng
  重抽本就有臂间方差，夹具必须复刻这一点）；
- **回归哨兵** ``TestSentinelMaxOf5``：把 R40 随机臂换回 v0.307 前的
  max-of-5 构造（monkeypatch run_arm），断言校准测试**必须失败**——
  能抓住已知 bug 才证明这道合入门有效。
"""

from __future__ import annotations

import pytest

import helpers_null as hn
import test_factor_exit_study as fxt
from custos.research import bear_regime_study as brs
from custos.research import criteria_kit as kit
from custos.research import factor_exit_study as fes
from custos.research import plan_rules_replay as prr
from custos.research import score_tier_position_study as stp
from test_bear_regime_study import (
    _args as _bear_args,
    _mk_per_code as _bear_per_code,
)
from test_plan_rules_replay import (
    _args as _prr_args,
    _mk_per_code as _prr_per_code,
)
from test_score_tier_position_study import (
    _args as _stp_args,
    _mk_per_code as _stp_per_code,
)

_WINDOWS = (("2022-01-01", "2024-07-31"), ("2024-08-01", "2026-09-04"))

#: 校准池规模（v0.330：≥20——小池 q95≈max 测不出校准）
N_RANDOM, MIN_POOL = 25, 20


def _rand_seq(seed: int):
    """逐次偏移的随机入场（确定性但臂间有方差——生产 random_entries
    每臂 rng 重抽的夹具等价物；_rand_flat 恒同批 ⇒ 池退化，已废于此）。"""
    calls = {"n": 0}

    def _draw(n: int, w: str) -> list[dict]:
        calls["n"] += 1
        off = calls["n"] * 131 + seed * 977
        return [
            {
                "code": f"{(i + off) % 7:06d}",
                "date": f"2024-{((i + off) % 12) + 1:02d}-{((i + off) % 28) + 1:02d}",
                "i": (0 if w == "mining" else 10000) + i + off,
                "score": 50.0,
                "sig": {"i": i + off, "date": "2024-01-02"},
                "gate": "__random__",
            }
            for i in range(n)
        ]

    return _draw


# ---------------------------------------------------------------------------
# bear_regime_study（R40 终端）
# ---------------------------------------------------------------------------


def _bear_run(replay, **kw):
    def run(seed):
        # j_low_adx25=60（owner 2026-10-10：4 信号的 margin 易冲极值，
        # 每条臂 max 都落这个门 ⇒ 池饱和 q95 恒同，分辨力偏弱）
        spec = _bear_per_code({"j_low": 120, "j_low_adx25": 60})  # 自带双窗键
        kw.setdefault("n_random", N_RANDOM)
        kw.setdefault("c4_min_pool", MIN_POOL)
        return brs.run_study(
            _bear_args(seed=seed, **kw),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=replay,
            random_entry_fn=_rand_seq(seed),
        )

    return run


class TestBearRegimeCalibration:
    def test_noise(self):
        hn.assert_noise_calibration("bear_regime", _bear_run(hn.null_replay))

    def test_edge_detected(self):
        hn.assert_edge_detected(
            "bear_regime",
            _bear_run(
                lambda sub, p: hn.edge_replay(
                    sub, p, is_edge=lambda r, _p: r["gate"] == "j_low"
                )
            ),
        )


class TestSentinelMaxOf5:
    """回归哨兵（owner review #1）：R40 随机臂换回 v0.307 前的 max-of-5
    构造 ⇒ 零假设校准**必须失败**（top 独享 230 格选择效应、臂只有 5 格，
    标尺系统性偏低——v0.306 的 7/8 假阳性就是这个 bug）。"""

    @staticmethod
    def _run_arm_max_of_5(
        gate_counts, exits, draw_entries, replay_mining, top_n, judgment_reader=None
    ):
        """v0.307 前的退化臂构造（仅哨兵复刻，勿用于生产）。"""
        n = sum(gate_counts.values())
        entries = draw_entries(n, "mining")
        ref_rd = None
        for e in exits:
            if e["name"] == "pct5_trail08":
                ref_rd = fes.combine_readings(
                    replay_mining(entries, e["params"]), top_n, ref="self"
                )
                break
        best = None
        for e in exits:
            rd = fes.combine_readings(
                replay_mining(entries, e["params"]), top_n, ref=ref_rd
            )
            if rd and rd["objective"] is not None and rd["margin"] is not None:
                if best is None or rd["margin"] > best["readings"]["margin"]:
                    best = {
                        "n_signals": n,
                        "gate_template": "__max5__",
                        "exit": e["name"],
                        "params": e["params"],
                        "readings": rd,
                    }
        out = {"mining": best}
        if best is not None and judgment_reader is not None:
            out["judgment"] = judgment_reader(best["n_signals"], best["params"])
        return out

    def test_calibration_catches_max_of_5_bug(self, monkeypatch):
        # 哨兵自备 12 门 ×30 信号 spec：top max-of-60 vs 臂 max-of-5 的
        # 预算不对称足够 decisive（v0.306 生产事故=46 门 max-of-230 vs
        # max-of-5；校准主用例的 2 门/大样本口径下差距会缩到抓不住——
        # owner：主用例防饱和、哨兵保分辨力，两者 fixture 分开）
        monkeypatch.setattr(brs, "run_arm", self._run_arm_max_of_5)
        gates12 = {
            g: 30
            for g in (
                "b2,bottom_surge,bottom_surge_j13,bottom_surge_strict,"
                "bottom_surge_strict_j13,breakout_pullback_b1,j_low,j_low_adx25,"
                "j_low_adx60,j_low_dif_pos,j_low_qsx_gt_dks,j_low_qsx_weekly"
            ).split(",")
        }

        def _run(seed):
            spec = _bear_per_code(gates12)
            return brs.run_study(
                _bear_args(seed=seed, n_random=15, c4_min_pool=10),
                warm_fn=lambda s, e: spec[(s, e)],
                replay_fn=hn.null_replay,
                random_entry_fn=_rand_seq(seed),
            )

        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration(
                "bear_regime[max-of-5 哨兵]", _run, seeds=(0, 1, 2)
            )


# ---------------------------------------------------------------------------
# plan_rules_replay（R41 终端）
# ---------------------------------------------------------------------------


def _prr_run(replay, **kw):
    def run(seed):
        per_code = _prr_per_code()
        spec = {w: per_code for w in _WINDOWS}
        kw.setdefault("n_random", N_RANDOM)
        kw.setdefault("c4_min_pool", MIN_POOL)
        return prr.run_study(
            _prr_args(seed=seed, **kw),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=replay,
        )

    return run


class TestPlanRulesCalibration:
    def test_noise(self):
        hn.assert_noise_calibration("plan_rules", _prr_run(hn.null_replay))

    def test_edge_detected(self):
        hn.assert_edge_detected(
            "plan_rules",
            _prr_run(
                lambda sub, p: hn.edge_replay(
                    sub,
                    p,
                    is_edge=lambda r, _p: (
                        r["sig"].get("stop_override") == r.get("plan_stop")
                    ),
                )
            ),
        )


# ---------------------------------------------------------------------------
# factor_exit_study（R39 终端）
# ---------------------------------------------------------------------------


class TestFactorExitCalibration:
    def test_noise(self, monkeypatch):
        def run(seed):
            return fxt._run(
                monkeypatch,
                fxt._three_level_spec(180),
                hn.null_replay,
                n_random=N_RANDOM,
                c4_min_pool=MIN_POOL,
                seed=seed,
            )

        hn.assert_noise_calibration("factor_exit", run)

    def test_edge_detected(self, monkeypatch):
        def run(seed):
            # _scripted_replay = 真条件映射剧本（条件 margin ≫ uniform-best）
            return fxt._run(
                monkeypatch,
                fxt._three_level_spec(180),
                fxt._scripted_replay,
                n_random=N_RANDOM,
                c4_min_pool=MIN_POOL,
                seed=seed,
            )

        hn.assert_edge_detected("factor_exit", run)


# ---------------------------------------------------------------------------
# score_tier_position_study（R42 终端）
# ---------------------------------------------------------------------------


def _stp_run(replay, **kw):
    def run(seed):
        per_code = _stp_per_code()  # 180 信号/窗，三档各 60 笔（档阈值 70/40）
        spec = {w: per_code for w in _WINDOWS}
        kw.setdefault("n_random", N_RANDOM)
        kw.setdefault("c4_min_pool", MIN_POOL)
        return stp.run_study(
            _stp_args(seed=seed, **kw),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=replay,
        )

    return run


class TestScoreTierCalibration:
    def test_noise(self):
        hn.assert_noise_calibration("score_tier_position", _stp_run(hn.null_replay))

    def test_edge_detected(self):
        hn.assert_edge_detected(
            "score_tier_position",
            _stp_run(
                lambda sub, p: hn.edge_replay(
                    sub,
                    p,
                    # 高档（score≥70）系统性大胜 ⇒ 加权 expR 倾斜增量
                    is_edge=lambda r, _p: r["score"] >= 70.0,
                )
            ),
        )


# ---------------------------------------------------------------------------
# score_tier_position 成簇校准（v0.335，owner review——C4 打乱粒度按簇不按笔）
# ---------------------------------------------------------------------------


def _clustered_per_code(n_codes=10, clusters_per_code=6, cluster_size=20):
    """成簇夹具：每码 6 簇 × 20 信号（簇内 i 间隔 1 ≤5 同簇，簇间 i 跳 1000
    强制分簇）；簇内分数同档（簇 cl ⇒ 档 (cl+码序) mod 3 按码旋转，档分数带
    [10,19.5] / [45,54.5] / [80,89.5] 互不相交 ⇒ 分档=整簇归属，与整簇抽签
    的臂**可交换**——同源零假设）；真实数据的「同票连续信号同档同结局」
    结构在这里复刻（owner 零假设模拟：簇大小 20 + 逐笔打乱 ⇒ 假过线 38%，
    本夹具实测复现 15/40——见 _cluster_noise_replay）。

    60 簇/窗（20 簇/档）。夹具定稿记录（v0.335 返修）：9 变体（band 旋转
    ×噪声 salt）扫描取 rot=1/salt="v2"——主测 5 种子 0/5、40 种子实测
    confirmed_pass 2/40≈名义 5%、逐笔哨兵 3/5 decisive；同结构的
    rot=0/salt="" 版 40 种子 4/40（10%）且主测 seed4 冲线——夹具差异
    只是噪声实现，机制（簇打乱 vs 逐笔）不变。"""
    per_code = {}
    k = 0
    for c in range(n_codes):
        code = f"{c:06d}"
        signals = []
        scores = {}
        for cl in range(clusters_per_code):
            band_base = (10.0, 45.0, 80.0)[(cl + c) % 3]  # 带间留缺口 ⇒ 切点落带隙
            for j in range(cluster_size):
                day = (
                    f"{2023 + (k // 336)}-{((k // 28) % 12) + 1:02d}-{(k % 28) + 1:02d}"
                )
                signals.append({"i": 1000 * cl + j, "date": day, "score": 0.0})
                scores[day] = band_base + j * 0.5
                k += 1
        for s in signals:
            s["score"] = scores[s["date"]]
        per_code[code] = {"signals": signals, "scores": scores}
    return per_code


def _cluster_noise_replay(seed: int):
    """成簇零假设重放：结局按 (code, 簇id, params, seed, salt) 哈希抽
    40% +0.06 / 60% −0.035——**同簇 20 笔同 ret**（簇相关）、与分数无关。
    逐笔打乱的臂把簇拆散 ⇒ q95 系统性偏低（本夹具实测：逐笔 15/40=37.5%
    ≈ owner 模拟的 38%；整簇 2/40≈名义 5%——见 _clustered_per_code
    docstring 的定稿记录）；整簇打乱才是对等零假设。salt="v2" 是夹具
    实现细节（定稿记录见夹具 docstring）。"""

    def _replay(subset, params):
        clusters = kit.cluster_ids(subset)
        out = []
        for r, cid in zip(subset, clusters):
            key = f"{r['code']}|{cid}|{sorted(params.items())}|{seed}|v2"
            ret = hn.NULL_WIN if hn._draw(key, hn.NULL_P) else hn.NULL_LOSE
            out.append(hn.mk_trade(r["code"], r["date"], ret, r.get("score", 50.0)))
        return out

    return _replay


def _stp_clustered_run(**kw):
    def run(seed):
        per_code = _clustered_per_code()  # 1200 信号/窗 = 60 簇（20 簇/档）
        spec = {w: per_code for w in _WINDOWS}
        kw.setdefault("n_random", N_RANDOM)
        kw.setdefault("c4_min_pool", MIN_POOL)
        return stp.run_study(
            _stp_args(seed=seed, **kw),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=_cluster_noise_replay(seed),
        )

    return run


class TestScoreTierClusteredCalibration:
    """成簇夹具 + 簇级噪声下的零假设校准（owner 指定两连测的成簇版）。"""

    def test_clustered_noise(self):
        hn.assert_noise_calibration("score_tier_position[成簇]", _stp_clustered_run())


class TestSentinelPerTradeShuffle:
    """逐笔打乱哨兵（照 TestSentinelMaxOf5 模式）：monkeypatch 把
    ``stp.arm_tier_draw`` 换回逐笔 shuffle（v0.335 前的 bug 形态）⇒ 成簇
    零假设校准**必须失败**（能抓住已知 bug 才证明成簇校准有效——owner
    实测该形态下假过线 15/40≈38%，名义 ≈5%）。"""

    def test_calibration_catches_per_trade_bug(self, monkeypatch):
        def _per_trade_draw(labels, clusters, rng):
            perm = list(labels)  # v0.335 前的逐笔打乱（忽略 clusters）
            rng.shuffle(perm)
            return perm

        monkeypatch.setattr(stp, "arm_tier_draw", _per_trade_draw)
        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration(
                "score_tier_position[逐笔哨兵]", _stp_clustered_run()
            )
