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

覆盖面（v0.405）：R40 / R41 / R42 / R43 挖掘 + 判定段读数 / R44 扫描 + 判定段 / R39 + 判定段 /
R45 / R46，以及 R40/R45 判定半窗的攒批判定线。**R36 ``--frozen-read`` 豁免**：其零假设池是
r36_p3_c4review 既有 50 臂（v0.269 采样器标尺，当时已校准），冻结读数只把同一批臂搬到判定段
重读，不新造零假设构造；合成夹具下臂读数由 FakeRunner 给定，校准无信息量。
"""

from __future__ import annotations

import pytest

import helpers_null as hn
import test_factor_exit_study as fxt
from custos.research import bear_regime_study as brs
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
        gate_counts,
        exits,
        draw_entries,
        replay_mining,
        top_n,
        judgment_reader=None,
        judgment_counts=None,
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
# plan_rules_replay --scan-widths（R44 宽度扫描，v0.379）
# ---------------------------------------------------------------------------


def _r44_run(edge: bool, **kw):
    kw.setdefault("scan_random", N_RANDOM)
    kw.setdefault("scan_c4_min_pool", MIN_POOL)

    def run(seed):
        from test_plan_rules_replay import _scan_run

        return _scan_run(seed, edge, **kw)

    return run


class TestPlanWidthScanCalibration:
    """R44 零假设两连测：纯噪声（收益与止损宽度无关）confirmed_pass 5 种子 0/5
    + 植入「x=10 固定宽度真更优」可识别（C4 confirmed_pass）。"""

    def test_noise(self):
        hn.assert_noise_calibration("plan_width_scan", _r44_run(False))

    def test_edge_detected(self):
        hn.assert_edge_detected("plan_width_scan", _r44_run(True))


def _r44_mono_run(**kw):
    """宽度单调零假设：胜率随止损宽度线性上升（系统性效应，没有哪一档特殊）——
    固定宽度候选 x* 选到最宽档不算 edge，必须打不过「同范围随机固定宽度 max-of-8」。"""
    import hashlib

    from test_plan_rules_replay import _scan_args, _scan_fixture, _trade_of

    kw.setdefault("scan_random", N_RANDOM)
    kw.setdefault("scan_c4_min_pool", MIN_POOL)

    def run(seed):
        def rp(subset, params):
            out = []
            for r in subset:
                so = r["sig"].get("stop_override")
                key = f"{r['code']}|{r['date']}|{so}|{seed}|mono"
                h = (
                    int.from_bytes(hashlib.md5(key.encode()).digest()[:4], "little")
                    / 2**32
                )
                x = (
                    (1.0 - so / r["anchor_low"]) * 100.0
                    if so is not None and "anchor_low" in r
                    else 10.0
                )
                out.append(_trade_of(r, 0.06 if h < 0.21 + 0.02 * x else -0.035))
            return out

        spec = _scan_fixture()
        return prr.run_width_scan(
            _scan_args(seed=seed, **kw), warm_fn=lambda s, e: spec[(s, e)], replay_fn=rp
        )

    return run


class TestPlanWidthMonotoneNull:
    """R44 宽度单调零假设（v0.408 审计 2 P0-3）：固定宽度随机臂下 confirmed_pass ≤10%
    （12 种子实测：固定宽度臂 0/12，逐信号臂 12/12）。"""

    def test_monotone_null(self):
        hn.assert_noise_calibration("plan_width_scan[宽度单调]", _r44_mono_run())


class TestSentinelScanPerSignalWidth:
    """回归哨兵（替代 v0.379 的 max-of-1 哨兵——固定宽度臂口径下 max-of-1 只 2/20，不再
    decisive）：随机臂换回逐信号独立抽宽度（审计 2 P0-3 旧 bug）⇒ 宽度单调零假设下校准
    **必须失败**（宽度系统效应在臂内被平均掉，候选最宽档恒过线）。"""

    def test_calibration_catches_per_signal_width(self, monkeypatch):
        monkeypatch.setattr(prr, "C4_WIDTH_PER_SIGNAL", True)
        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration(
                "plan_width_scan[逐信号宽度哨兵]", _r44_mono_run()
            )


# ---------------------------------------------------------------------------
# regime_style_study（R43 终端，v0.368）
# ---------------------------------------------------------------------------


def _r43_run(edge: bool, **inj):
    def run(seed):
        from test_regime_style_study import _calib_run

        return _calib_run(seed, edge, **inj)

    return run


class TestRegimeStyleCalibration:
    """R43 零假设两连测：纯噪声（收益与特征无关）5 种子 confirmed_pass 0/5；
    植入 regime 专属价差（空头段 age 高龄组系统性占优——静态序不漂移）可识别。"""

    def test_noise(self):
        hn.assert_noise_calibration("regime_style", _r43_run(False))

    def test_edge_detected(self):
        hn.assert_edge_detected("regime_style", _r43_run(True))


class TestSentinelAlignedPseudo:
    """回归哨兵：伪段构造换成「与真实同向段对齐」的错误实现（零假设 = 真实段
    本身 ⇒ 族 max|t| 池恒 ≥ 候选 |t|，q95 永远压线）⇒ 植入用例必须**不再**
    confirmed_pass。能抓住这个 bug 才证明 C4 的「随机放置」语义有效。"""

    @staticmethod
    def _aligned_draw(real_segs, win_days, *, n_draws, seed, max_retry=100):
        # 错误实现（仅哨兵复刻，勿用于生产）：伪段 = 真实段本身
        aligned = [dict(s) for s in real_segs]
        return {"draws": [aligned for _ in range(n_draws)], "n_dropped": 0}

    def test_aligned_pseudo_breaks_edge_detection(self, monkeypatch):
        from custos.research import regime_style_study as rs

        monkeypatch.setattr(rs, "draw_pseudo_placements", self._aligned_draw)
        with pytest.raises(AssertionError, match="未被识别"):
            hn.assert_edge_detected("regime_style[对齐哨兵]", _r43_run(True), seed=0)


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


def _clustered_per_code():
    """R42 成簇夹具（构造件 = ``hn.clustered_layout`` 共享单源，v0.336 抽
    出——与 R39 成簇用例同骨架）：簇内分数同档（档分数带 [10,19.5] /
    [45,54.5] / [80,89.5] 互不相交 ⇒ 分档=整簇归属，与整簇抽签的臂
    **可交换**）；1200 信号/窗 = 60 簇（20 簇/档）；真实数据的「同票
    连续信号同档同结局」结构在这里复刻（owner 零假设模拟：簇大小 20 +
    逐笔打乱 ⇒ 假过线 38%，本夹具实测复现 15/40；定稿记录见
    ``hn.clustered_layout`` docstring）。"""
    per_code: dict[str, dict] = {}
    for code, day, i, band in hn.clustered_layout():
        pack = per_code.setdefault(code, {"signals": [], "scores": {}})
        score = (10.0, 45.0, 80.0)[band] + (i % 1000) * 0.5
        pack["signals"].append({"i": i, "date": day, "score": score})
        pack["scores"][day] = score
    return per_code


def _stp_clustered_run(**kw):
    def run(seed):
        per_code = _clustered_per_code()  # 1200 信号/窗 = 60 簇（20 簇/档）
        spec = {w: per_code for w in _WINDOWS}
        kw.setdefault("n_random", N_RANDOM)
        kw.setdefault("c4_min_pool", MIN_POOL)
        return stp.run_study(
            _stp_args(seed=seed, **kw),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=hn.cluster_noise_replay(seed),
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


# ---------------------------------------------------------------------------
# factor_exit_study 成簇校准（v0.336，owner review——R39 与 R42 同源同覆盖）
# ---------------------------------------------------------------------------


def _fes_clustered_spec():
    """R39 成簇夹具：``hn.clustered_layout(n_codes=5)`` 共享骨架 + 簇内
    因子同水平（factor=band ⇒ 分桶=整簇归属，与整簇抽签臂可交换）；经
    ``fxt._per_code`` 转 per_code（df=fmap 替身，adx14_series 照旧
    monkeypatch）。600 信号/窗 = 30 簇。

    尺寸定稿（v0.336）：C4 臂=同 80 格枚举，臂成本随信号数线性——
    10 码版单次 run ~20s（5 种子主测+哨兵 ~4.5min 太重）；6 码变体扫描
    （n_codes×rot 六格实测）里 5 码各 rot 全部主测 0/5 + 哨兵 decisive
    （≥2/5 假过线），取 5 码 rot=1（与 R42 定稿同 rot）。"""

    def _mk(offset):
        spec: dict[str, list] = {}
        for code, day, i, band in hn.clustered_layout(n_codes=5):
            spec.setdefault(code, []).append((day, offset + i, 50.0, band))
        return spec

    return {
        _WINDOWS[0]: fxt._per_code(_mk(0)),
        _WINDOWS[1]: fxt._per_code(_mk(1_000_000)),
    }


def _fes_clustered_run(monkeypatch, **kw):
    def run(seed):
        kw.setdefault("n_random", N_RANDOM)
        kw.setdefault("c4_min_pool", MIN_POOL)
        return fxt._run(
            monkeypatch,
            _fes_clustered_spec(),
            hn.cluster_noise_replay(seed),
            seed=seed,
            **kw,
        )

    return run


class TestFactorExitClusteredCalibration:
    """R39 成簇零假设校准：纯噪声（簇结局相关）⇒ confirmed_pass 5 种子
    0/5——逐信号打乱会把簇拆散、q95 偏低（与 R42 同修，v0.335/v0.336）。"""

    def test_clustered_noise(self, monkeypatch):
        hn.assert_noise_calibration(
            "factor_exit[成簇]", _fes_clustered_run(monkeypatch)
        )


class TestSentinelFesPerSignalShuffle:
    """R39 逐信号打乱哨兵（照 TestSentinelMaxOf5 / R42 逐笔哨兵模式）：
    monkeypatch 把 ``fes.arm_bucket_draw`` 换回逐信号 shuffle（v0.335 前
    的 bug 形态）⇒ 成簇零假设校准**必须失败**。"""

    def test_calibration_catches_per_signal_bug(self, monkeypatch):
        def _per_signal_draw(buckets, clusters, rng):
            perm = list(buckets)  # v0.335 前的逐信号 shuffle（忽略 clusters）
            rng.shuffle(perm)
            return perm

        monkeypatch.setattr(fes, "arm_bucket_draw", _per_signal_draw)
        with pytest.raises(AssertionError, match="零假设校准失败"):
            # 哨兵用小池（15/10，bear 哨兵同例——主用例守 25/20 契约）
            hn.assert_noise_calibration(
                "factor_exit[逐信号哨兵]",
                _fes_clustered_run(monkeypatch, n_random=15, c4_min_pool=10),
            )


# ---------------------------------------------------------------------------
# R45（bear_regime_study --r45，v0.397）：j_low 单门 × 16 格 × 30% 敞口
# ---------------------------------------------------------------------------


def _r45_run(replay, **kw):
    return _bear_run(replay, r45=True, gates="j_low", **kw)


class TestR45Calibration:
    """R45 零假设两连测：纯噪声 5 种子 confirmed_pass ≤10%；植入「T2（trail8）真更优」可识别。"""

    def test_noise(self):
        hn.assert_noise_calibration("r45", _r45_run(hn.null_replay))

    def test_edge_detected(self):
        hn.assert_edge_detected(
            "r45",
            _r45_run(
                lambda sub, p: hn.edge_replay(
                    sub,
                    p,
                    is_edge=lambda r, pp: (
                        r["gate"] == "j_low" and bool(pp.get("trail_pct"))
                    ),
                )
            ),
        )


class TestSentinelR45MaxOf1:
    """回归哨兵：R45 随机臂只看 1 格（丢掉与 16 格网格对等的 max-of-16 预算）⇒
    零假设校准**必须失败**——证明 C4 的「同预算选格」是有效约束。"""

    def test_calibration_catches_max_of_1(self, monkeypatch):
        orig = brs.run_arm

        def max_of_1(gate_counts, exits, *a, **k):
            # 只留 1 个候选格 + 参照格（参照档必须在，否则退回绝对门、混淆哨兵）
            keep = [exits[0]] + [e for e in exits if e["name"] == brs.R45_REF]
            return orig(gate_counts, keep, *a, **k)

        monkeypatch.setattr(brs, "run_arm", max_of_1)
        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration("r45[max-of-1 哨兵]", _r45_run(hn.null_replay))


# ---------------------------------------------------------------------------
# 判定段攒批判定线（v0.389~0.395，owner 选 B）：终端级两连测
# ---------------------------------------------------------------------------


def _open_cal_batch(m: int = 4) -> str:
    from custos.research import judgment_batch as jb

    bid = f"cal{m}"
    if bid not in jb.status():
        jb.open_batch(
            bid,
            [{"id": f"c{k}", "config_hash": f"t:{k}", "unit": "R0"} for k in range(m)],
        )
    return bid


def _judgment_hits(run, seeds=hn.NULL_SEEDS):
    return [bool(run(s)["criteria"]["C2"].get("pool_ok_judgment")) for s in seeds]


class TestBearJudgmentLineCalibration:
    """R40/R45 判定半窗（带 --batch-id ⇒ 判定线 = batch_threshold(冻结臂判定窗重读池, m)）：
    纯噪声下「判定窗 > 判定线」比例 ≤10%；植入 edge 可识别。"""

    def test_noise(self):
        bid = _open_cal_batch()
        hits = _judgment_hits(_bear_run(hn.null_replay, batch_id=bid))
        assert sum(hits) / len(hits) <= hn.NOISE_CAP, hits

    def test_edge_detected(self):
        bid = _open_cal_batch()
        run = _bear_run(
            lambda sub, p: hn.edge_replay(
                sub, p, is_edge=lambda r, _p: r["gate"] == "j_low"
            ),
            batch_id=bid,
        )
        rep = run(0)
        assert rep["pools"]["batch_m"] == 4
        assert rep["criteria"]["C2"]["pool_ok_judgment"] is True


def _prr_anchor_run(edge: bool, monkeypatch, bid: str):
    monkeypatch.setattr(prr, "SCAN_N_RANDOM", N_RANDOM)

    def is_edge(r, _p):
        so = r["sig"].get("stop_override")
        return (
            so is not None
            and "anchor_low" in r
            and abs(1.0 - so / r["anchor_low"] - 0.10) < 1e-9
        )

    replay = (
        (lambda sub, p: hn.edge_replay(sub, p, is_edge=is_edge))
        if edge
        else hn.null_replay
    )

    def run(seed):
        spec = {w: _prr_per_code() for w in _WINDOWS}
        return prr.run_study(
            _prr_args(
                seed=seed,
                stop_anchor="signal_low",
                stop_buffer_pct=10.0,
                batch_id=bid,
                c4_min_pool=MIN_POOL,
            ),
            warm_fn=lambda s, e: spec[(s, e)],
            replay_fn=replay,
        )

    return run


class TestPrrAnchorJudgmentLineCalibration:
    """R44 判定段（锚点模式 criteria.judgment_batch）：纯噪声 candidate 率 ≤10%；植入 x=10 真更优可识别。"""

    def test_noise(self, monkeypatch):
        run = _prr_anchor_run(False, monkeypatch, _open_cal_batch())
        hits = [
            run(s)["criteria"]["judgment_batch"]["verdict"] == "candidate"
            for s in hn.NULL_SEEDS
        ]
        assert sum(hits) / len(hits) <= hn.NOISE_CAP, hits

    def test_edge_detected(self, monkeypatch):
        blk = _prr_anchor_run(True, monkeypatch, _open_cal_batch())(0)["criteria"][
            "judgment_batch"
        ]
        assert blk["line"]["m"] == 4 and blk["pool_size"] >= MIN_POOL
        assert blk["verdict"] == "candidate", blk["verdict"]


class TestFesJudgmentLineCalibration:
    """R39 判定段（criteria.judgment_batch：随机分桶臂冻结映射判定段整簇重赋桶重读）：
    纯噪声 candidate 率 ≤10%；条件映射真剧本可识别。"""

    @staticmethod
    def _run(monkeypatch, replay, seed, bid):
        return fxt._run(
            monkeypatch,
            fxt._three_level_spec(180),
            replay,
            n_random=N_RANDOM,
            c4_min_pool=MIN_POOL,
            seed=seed,
            batch_id=bid,
        )

    def test_noise(self, monkeypatch):
        bid = _open_cal_batch()
        hits = [
            self._run(monkeypatch, hn.null_replay, s, bid)["criteria"][
                "judgment_batch"
            ]["verdict"]
            == "candidate"
            for s in hn.NULL_SEEDS
        ]
        assert sum(hits) / len(hits) <= hn.NOISE_CAP, hits

    def test_edge_detected(self, monkeypatch):
        blk = self._run(monkeypatch, fxt._scripted_replay, 0, _open_cal_batch())[
            "criteria"
        ]["judgment_batch"]
        assert blk["line"]["m"] == 4
        assert blk["verdict"] == "candidate", (blk["verdict"], blk["line"])


def _r43j_fixture(seed: int, edge: bool):
    """判定段 2017~2021 内 9 空头 + 9 多头段；edge=空头段高龄组 +0.35%/日（同 R43 挖掘校准）。"""
    import numpy as np
    import pandas as pd
    from test_regime_style_study import _blocks_days, _mk_df, _regime_of

    blocks = [("中性", 5)] + [("空头", 10), ("中性", 5), ("做多", 10), ("中性", 5)] * 9
    days, states = _blocks_days(blocks, "2017-03-01")
    regime = _regime_of(days, states)
    full = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2014-01-02", "2019-12-31")]
    bear = {d for d, s in regime.items() if s == "空头"}
    rng = np.random.RandomState(seed)
    frames = {}
    for k in range(40):
        c = f"{['600', '000', '300', '920'][k % 4]}{100 + k:03d}"
        dk = full[10 * k :]
        r = rng.normal(0.0002, 0.008, len(dk))
        if edge:
            bump = 0.0035 if k < 20 else -0.0035
            r = np.array([ri + (bump if d in bear else 0.0) for d, ri in zip(dk, r)])
        frames[c] = _mk_df(dk, (5.0 + k) * np.exp(np.cumsum(r)))
    return regime, frames


def _r43j_run(edge: bool, bid: str):
    from custos.research import regime_style_study as rs

    def run(seed):
        regime, frames = _r43j_fixture(seed, edge)
        args = rs._build_parser().parse_args(
            [
                "--judgment-read",
                "bear:age:+",
                "--min-segs",
                "8",
                "--min-stocks",
                "20",
                "--min-group",
                "4",
                "--n-random",
                str(N_RANDOM),
                "--c4-min-pool",
                str(MIN_POOL),
                "--c4-seed",
                str(20261014 + seed),
                "--batch-id",
                bid,
            ]
        )
        return rs.run_judgment_read(
            args,
            rs.parse_judgment_specs(args.judgment_read),
            bars_loader=lambda codes, count, start, end: frames,
            regime=regime,
            codes=sorted(frames),
            sector_bars={},
            sector_types={},
            shares_fn=lambda c: None,
            xdxr_fn=lambda c: None,
            pit_records=[],
        )

    return run


class TestR43JudgmentReadCalibration:
    """R43 --judgment-read：纯噪声 candidate 率 ≤10%；植入空头段 age 价差可识别。"""

    def test_noise(self):
        run = _r43j_run(False, _open_cal_batch())
        hits = [
            run(s)["candidates"][0]["verdict"] == "candidate" for s in hn.NULL_SEEDS
        ]
        assert sum(hits) / len(hits) <= hn.NOISE_CAP, hits

    def test_edge_detected(self):
        c = _r43j_run(True, _open_cal_batch())(0)["candidates"][0]
        assert c["n_segs"] >= 8 and c["line"]["m"] == 4
        assert c["verdict"] == "candidate", (c["verdict"], c["signed_t"], c["line"])


# ---------------------------------------------------------------------------
# R46（family_lattice_study，v0.404）：729 格 → 校准用 9 格缩小网格（前两家族 3×3）
# ---------------------------------------------------------------------------


@pytest.fixture
def r46_small(monkeypatch):
    from custos.research import family_lattice_study as fl

    grid = [m for m in fl.GRID if all(x == 1 for x in m[2:])]
    monkeypatch.setattr(fl, "GRID", grid)
    monkeypatch.setattr(fl, "ONOFF", [m for m in grid if all(x in (0, 1) for x in m)])
    monkeypatch.setattr(fl, "family_subscores", lambda cand, legs: cand["S"])
    monkeypatch.setattr(
        fl,
        "neighbors",
        lambda m: [x for x in grid if sum(abs(a - b) for a, b in zip(x, m)) == 1],
    )
    return fl


def _r46_clustered_world(seed, n_codes=30, n_days=50):
    """成簇零假设：同票子分与收益各自跨日持续（簇），但两者互相独立。"""
    import numpy as np
    import pandas as pd

    rng = np.random.RandomState(seed)
    out = {}
    for seg, y0 in (("A", 2010), ("B", 2023), ("J", 2018)):
        items = []
        days = pd.bdate_range(f"{y0}-01-04", periods=n_days)
        sc0 = {k: rng.uniform(0, 12, 6) for k in range(n_codes)}
        mu = {k: rng.normal(0, 0.03) for k in range(n_codes)}
        for di, d in enumerate(days):
            for k in range(n_codes):
                ret = float(mu[k] + rng.normal(0, 0.02))
                ds = d.strftime("%Y-%m-%d")
                items.append(
                    {
                        "code": f"{k:06d}",
                        "i": di,
                        "S": sc0[k] + rng.normal(0, 0.3, 6),
                        "trade": {
                            "code": f"{k:06d}",
                            "entry_date": ds,
                            "exit_date": (d + pd.Timedelta(days=3)).strftime(
                                "%Y-%m-%d"
                            ),
                            "ret": ret,
                            "reason": "stop",
                            "risk_frac": 0.05,
                            "r_multiple": ret / 0.05,
                            "holding": 3,
                        },
                    }
                )
        out[seg] = items
    return out


def _r46_run(fl, tmp_path, world_fn):
    import json

    import test_family_lattice as tfl

    def run(seed):
        out = tmp_path / f"r46_{seed}.json"
        args = [
            "--out",
            str(out),
            "--n-random",
            str(N_RANDOM),
            "--c4-min-pool",
            str(MIN_POOL),
            "--tie-seeds",
            "3",
            "--top-n",
            "5",
            "--seed",
            str(seed),
        ]
        assert fl.main(args, collector=tfl._collector(world_fn(seed))) == 0
        return json.loads(out.read_text(encoding="utf-8"))

    return run


class TestR46Calibration:
    """R46 零假设两连测：纯噪声 0/5；植入「F1 家族有信息」可识别；成簇零假设（簇级置换）0/5。"""

    def test_noise(self, r46_small, tmp_path):
        import test_family_lattice as tfl

        hn.assert_noise_calibration(
            "r46",
            _r46_run(r46_small, tmp_path, lambda s: tfl._world(500 + s, edge=0.0)),
        )

    def test_edge_detected(self, r46_small, tmp_path):
        import test_family_lattice as tfl

        hn.assert_edge_detected(
            "r46",
            _r46_run(r46_small, tmp_path, lambda s: tfl._world(500 + s, edge=3.0)),
        )

    def test_clustered_noise(self, r46_small, tmp_path):
        hn.assert_noise_calibration(
            "r46[成簇]",
            _r46_run(r46_small, tmp_path, lambda s: _r46_clustered_world(900 + s)),
        )


class TestSentinelR46IidPermutation:
    """回归哨兵：零假设换成**逐候选**置换（拆散簇）⇒ 成簇零假设下校准必须失败
    （20 种子实测：簇级 1/20，逐候选 10/20）。"""

    def test_calibration_catches_iid_permutation(
        self, r46_small, tmp_path, monkeypatch
    ):
        def iid(items, S, rng):
            idx = list(range(len(S)))
            rng.shuffle(idx)
            return S[idx]

        monkeypatch.setattr(r46_small, "permute_clusters", iid)
        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration(
                "r46[逐候选置换哨兵]",
                _r46_run(r46_small, tmp_path, lambda s: _r46_clustered_world(900 + s)),
            )


# ---------------------------------------------------------------------------
# swing_recall_study（R50，v0.456，TODO #119）：16 对象 × 精度 lift 的簇级置换
# ---------------------------------------------------------------------------


def _r50_run(edge: bool, clustered: bool = True, **kw):
    def run(seed):
        from test_swing_recall_study import _unit_c4, _world_run

        return _unit_c4(_world_run(seed, edge=edge, clustered=clustered, **kw))

    return run


#: R50 校准口径（max-of-16 的 q95 尾部分辨力需要放大池——照 R48 的 20 种子 × 50 臂）
R50_SEEDS = tuple(range(20))
R50_KW = {"min_n": 50, "n_random": 50, "c4_min_pool": 40}


class TestSwingRecallCalibration:
    """R50 零假设两连测 + 成簇用例：信号位置与波段窗口无关（纯块标签、簇结局相关）
    ⇒ 20 种子 lead_robust 0/20；逐 bar iid 世界 0/20；植入「b1 热概率 0.18→0.85」
    真 edge ⇒ b1_l1 lead_robust 可识别。

    实测定稿（v0.456，合成世界逐种子实测）：成簇零假设 0/20、iid 世界 0/20、
    edge 种子 0/1/2 全过、逐笔置换哨兵 6/20（30% > 10% 帽 ⇒ 决定性）。
    夹具设计纪律（簇必须同长且冷热不合并）见 test_swing_recall_study._world docstring。
    """

    def test_noise(self):
        hn.assert_noise_calibration(
            "swing_recall[成簇]", _r50_run(False, **R50_KW), seeds=R50_SEEDS
        )

    def test_iid_world_noise(self):
        hn.assert_noise_calibration(
            "swing_recall[逐bar世界]",
            _r50_run(False, clustered=False, **R50_KW),
            seeds=R50_SEEDS,
        )

    def test_edge_detected(self):
        hn.assert_edge_detected("swing_recall", _r50_run(True, **R50_KW), seed=0)


class TestSentinelSwingIidPermutation:
    """回归哨兵（照 R48 逐笔哨兵模式）：monkeypatch 把 ``sr.cluster_lengths`` 拆成全 1
    （随机局部低点逐笔抽、不保簇形状，v0.458）⇒ 成簇零假设校准**必须失败**（事件位置的「∈起涨窗口」标签成簇
    相关；拆散后逐对象精度 concentrate、q95 系统性偏低——实测 6/20=30% 假过线，
    超过 10% 帽）。"""

    def test_calibration_catches_iid(self, monkeypatch):
        import numpy as np

        from custos.research import swing_recall_study as sr

        # v0.458：零假设改为「保簇形状的随机局部低点入场」——哨兵把簇形状拆成全 1（逐笔抽样）
        monkeypatch.setattr(sr, "cluster_lengths", lambda rows: [(1, r) for r in rows])
        with pytest.raises(AssertionError, match="零假设校准失败"):
            hn.assert_noise_calibration(
                "swing_recall[逐笔置换哨兵]", _r50_run(False, **R50_KW), seeds=R50_SEEDS
            )
