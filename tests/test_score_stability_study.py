# -*- coding: utf-8 -*-
"""score_stability_study 钉测：R29 四候选打分 / R29-C1~C4 判据边界 / 灵敏度 / CLI 守卫。

合成 trades（factor_contrib/panel/ret/entry_date 手工构造），零真实数据依赖；
判据边界用手算案例钉住（R29-C1/C2 恰线 = ≥ 过线语义）。CLI 冒烟用
monkeypatch.chdir(tmp_path) 隔离产物路径（脚本落盘路径相对仓库根）。
"""

from __future__ import annotations

import json

import pytest

from custos.research import score_calibration_study as scs
from custos.research import score_stability_study as sss


def _trade(contrib=None, panel=None, ret=0.0, entry_date="2026-01-05"):
    t = {"ret": ret, "entry_date": entry_date}
    if contrib is not None:
        t["factor_contrib"] = contrib
    if panel is not None:
        t["panel"] = panel
    return t


def _score_fn(candidate):
    spec = sss.R29_CANDIDATES[candidate]
    return scs.make_candidate_score(spec["contrib_mult"], spec["panel_weights"])


def _strong_trades(n=200):
    """强信号样本：rsi_bull_div 命中组 75% 胜率/盈亏比 5，未中组 25%/1.0。

    两段时间 101/99（R29-C3 要求前后半窗都可评估且同正；中位切分点须落在
    前段日期上——同 score_calibration_study 钉测的构造）。
    """
    trades = []
    for i in range(n):
        hit = i % 2 == 0
        if hit:
            ret = 0.05 if i % 8 in (0, 2, 4) else -0.01  # 命中组 3 胜 1 亏
        else:
            ret = 0.02 if i % 8 == 1 else -0.02  # 未中组 1 胜 3 亏
        trades.append(
            _trade(
                panel={"rsi_bull_div": hit},
                ret=ret,
                entry_date="2026-01-05" if i < 101 else "2026-06-01",
            )
        )
    return trades


# ---------------------------------------------------------------------------
# ① 四候选打分：确定性 / 手算 / panel 三态 / 负腿 / contrib 归零
# ---------------------------------------------------------------------------


class TestCandidateScoring:
    def test_candidates_match_preregistration(self):
        """候选与预注册页「候选方案」表逐字一致（判据常量 likewise）。"""
        assert set(sss.R29_CANDIDATES) == {
            "W1_balanced",
            "W2_pre2019_tilt",
            "W3_no_deep",
            "W4_tilt_neg",
        }
        assert sss.R29_CANDIDATES["W1_balanced"]["panel_weights"] == {
            "rsi_deep_oversold": 25,
            "weekly_j_low": 25,
            "rsi_bull_div": 25,
            "macd_bottom_divergence": 25,
        }
        assert sss.R29_CANDIDATES["W2_pre2019_tilt"]["panel_weights"] == {
            "rsi_bull_div": 30,
            "macd_bottom_divergence": 30,
            "weekly_j_low": 20,
            "rsi_deep_oversold": 20,
        }
        assert sss.R29_CANDIDATES["W3_no_deep"]["panel_weights"] == {
            "rsi_bull_div": 40,
            "macd_bottom_divergence": 30,
            "weekly_j_low": 30,
        }
        assert sss.R29_CANDIDATES["W4_tilt_neg"]["panel_weights"] == {
            "rsi_bull_div": 30,
            "macd_bottom_divergence": 30,
            "weekly_j_low": 20,
            "rsi_deep_oversold": 20,
            **{k: -5 for k in sss.R29_NEG_LEGS},
        }
        assert sss.R29_NEG_LEGS == (
            "rsi_strong",
            "b1_ignition",
            "volume_contraction",
            "relative_strength_strong",
            "macd_top_divergence",
            "ignition",
        )
        assert sss.WIN_RATE_FLOOR == 0.40
        assert sss.PAYOFF_FLOOR == 2.4
        assert sss.STRONG_FRAC_MAX == 0.15
        for spec in sss.R29_CANDIDATES.values():
            assert spec["contrib_mult"] == {}  # 证据重构形态：现行腿全部归零

    def test_deterministic_same_trade_twice(self):
        """同一 trade 两次打分同分（四候选全查；纯函数 trade→score）。"""
        t = _trade(
            contrib={"j_low": 24, "bbi_above": 5},
            panel={
                "rsi_deep_oversold": True,
                "weekly_j_low": None,
                "rsi_bull_div": True,
            },
        )
        for name in sss.R29_CANDIDATES:
            fn = _score_fn(name)
            assert fn(t) == fn(dict(t))

    def test_panel_true_adds_none_skips(self):
        """panel 命中 True 加分；None（unavailable）按 0 不加分。"""
        w1 = _score_fn("W1_balanced")
        assert w1(_trade(panel={"weekly_j_low": True})) == 25
        assert w1(_trade(panel={"weekly_j_low": None})) == 0
        assert w1(_trade(panel={})) == 0  # 键缺失同 unavailable
        # 四腿全中 = 100（等权 25×4，封顶内）
        assert (
            w1(
                _trade(
                    panel={
                        "rsi_deep_oversold": True,
                        "weekly_j_low": True,
                        "rsi_bull_div": True,
                        "macd_bottom_divergence": True,
                    }
                )
            )
            == 100
        )

    def test_w2_w3_hand_computed(self):
        w2 = _score_fn("W2_pre2019_tilt")
        assert w2(_trade(panel={"rsi_bull_div": True, "rsi_deep_oversold": True})) == 50
        w3 = _score_fn("W3_no_deep")
        # W3 没有 rsi_deep_oversold 腿——命中也不计
        assert w3(_trade(panel={"rsi_bull_div": True, "rsi_deep_oversold": True})) == 40

    def test_w4_negative_leg_subtracts(self):
        """负腿命中减分；None 负腿不减；纯负腿命中 clamp 到 0。"""
        w4 = _score_fn("W4_tilt_neg")
        t = _trade(panel={"rsi_bull_div": True, "rsi_strong": True, "ignition": None})
        assert w4(t) == 25  # +30 −5，None 腿不计
        t_all_neg = _trade(panel={k: True for k in sss.R29_NEG_LEGS})
        assert w4(t_all_neg) == 0  # −30 clamp 0

    def test_contrib_ignored_in_rebuild_form(self):
        """证据重构形态：contrib 再高也不计（与 R24 P1 语义一致）。"""
        w1 = _score_fn("W1_balanced")
        base = _trade(panel={"weekly_j_low": True})
        rich = _trade(
            contrib={"j_low": 24, "bbi_above": 5, "leader_volume": 6},
            panel={"weekly_j_low": True},
        )
        assert w1(base) == w1(rich) == 25


# ---------------------------------------------------------------------------
# ② R29-C1/C2 边界（恰 0.40 / 2.4 过线语义 = ≥）+ None 安全
# ---------------------------------------------------------------------------


def _boundary_trades(win_ret=0.24, n_win=2):
    """25 笔：候选分前 5（basket 组 x=10）n_win 胜；对照组 y=20 ⇒ V0 篮子胜率 0.2。

    默认参数下篮子：2 胜（ret 0.24）3 亏（−0.10）⇒ 胜率恰 0.40、盈亏比恰 2.4。
    V0 篮子（V0 分 = x+y 求和 ⇒ 对照组 20 在前）= 对照组前 5：1 胜 4 亏 ⇒ 0.2。
    """
    trades = []
    for i in range(5):
        trades.append(
            _trade(
                contrib={"x": 10},
                ret=win_ret if i < n_win else -0.10,
                entry_date=f"2026-01-{i + 1:02d}",
            )
        )
    for i in range(20):
        trades.append(
            _trade(
                contrib={"y": 20},
                ret=0.05 if i == 0 else -0.05,
                entry_date=f"2026-02-{i + 1:02d}",
            )
        )
    return trades


def _x_score(t):
    return (t.get("factor_contrib") or {}).get("x", 0)


class TestCriteriaBoundary:
    def test_exactly_at_floors_passes(self):
        """恰线 = 过：胜率恰 0.40 且盈亏比恰 2.4 都判过（≥ 语义，手算钉住）。"""
        ev = sss.eval_r29_candidate(_boundary_trades(), "toy", _x_score)
        assert ev["basket"]["win_rate"] == pytest.approx(0.40)
        assert ev["basket"]["payoff_ratio"] == pytest.approx(2.4)
        assert ev["R29_C1"]["pass"] is True
        assert ev["R29_C2"]["pass"] is True

    def test_win_rate_below_floor_fails(self):
        """胜率 0.20 < 0.40 ⇒ R29-C1 不过。"""
        ev = sss.eval_r29_candidate(_boundary_trades(n_win=1), "toy", _x_score)
        assert ev["basket"]["win_rate"] == pytest.approx(0.20)
        assert ev["R29_C1"]["pass"] is False

    def test_payoff_below_floor_fails(self):
        """盈亏比 2.3 < 2.4 ⇒ R29-C2 不过（胜率条件仍过，两条判据独立）。"""
        ev = sss.eval_r29_candidate(_boundary_trades(win_ret=0.23), "toy", _x_score)
        assert ev["basket"]["payoff_ratio"] == pytest.approx(2.3)
        assert ev["R29_C1"]["pass"] is True
        assert ev["R29_C2"]["pass"] is False

    def test_c1_requires_beating_v0(self):
        """胜率 ≥0.40 但不 > V0 篮子胜率 ⇒ R29-C1 不过（两个子条件缺一不可）。"""
        trades = []
        for i in range(5):  # 候选与 V0 同序 ⇒ V0 篮子 = 同一组，胜率同样 0.4
            trades.append(
                _trade(
                    contrib={"x": 10},
                    ret=0.24 if i < 2 else -0.10,
                    entry_date=f"2026-01-{i + 1:02d}",
                )
            )
        for i in range(20):
            trades.append(
                _trade(ret=0.05 if i == 0 else -0.05, entry_date=f"2026-02-{i + 1:02d}")
            )
        ev = sss.eval_r29_candidate(trades, "toy", _x_score)
        assert ev["R29_C1"]["basket_win_rate"] == pytest.approx(0.40)
        assert ev["R29_C1"]["v0_basket_win_rate"] == pytest.approx(0.40)
        assert ev["R29_C1"]["pass"] is False  # 0.40 > 0.40 不成立

    def test_c2_none_safe_when_payoff_undefined(self):
        """篮子全赢（无亏单 ⇒ payoff 无定义）⇒ R29-C2 判 False 并如实标注。"""
        ev = sss.eval_r29_candidate(_boundary_trades(n_win=5), "toy", _x_score)
        assert ev["basket"]["payoff_ratio"] is None
        assert ev["R29_C2"]["pass"] is False
        assert ev["R29_C2"]["note"]  # 缺省注记非空

    def test_c1_none_safe_when_win_rate_missing(self):
        """胜率缺省 ⇒ R29-C1 判 False 并标注（不编数）。"""
        c1 = sss._r29_c1({"win_rate": None}, {"win_rate": 0.3})
        assert c1["pass"] is False
        assert c1["note"]
        c1b = sss._r29_c1({"win_rate": 0.5}, {})
        assert c1b["pass"] is False
        assert c1b["note"]


# ---------------------------------------------------------------------------
# ③ pass_all 合成逻辑（C1∧C2∧C3∧C4）
# ---------------------------------------------------------------------------


class TestPassAll:
    def test_strong_signal_all_pass(self):
        """强信号样本：W2 四判据全过 ⇒ pass_all（手算：篮子 75%/5.0，V0 50%）。"""
        ev = sss.eval_r29_candidate(
            _strong_trades(), "W2_pre2019_tilt", _score_fn("W2_pre2019_tilt")
        )
        assert ev["basket"]["win_rate"] == pytest.approx(0.75)
        assert ev["R29_C1"]["pass"] is True  # 0.75 ≥ 0.40 且 > V0 篮子 0.50
        assert ev["R29_C2"]["pass"] is True  # 盈亏比 5.0 ≥ 2.4
        assert ev["R29_C3"]["pass"] is True  # Spearman>0 且半窗同正
        assert ev["R29_C4"]["pass"] is True  # 得分 30/0 ⇒ 强档占比 0
        assert ev["pass_all"] is True
        # 参考列字段齐全（C3★ + Wilson 注记，不进判定）
        assert "C3_star" in ev and "C3_star_wilson_overlap" in ev

    def test_constant_score_fails_c3_and_pass_all(self):
        """零区分度（全员同分）⇒ Spearman 无定义 ⇒ R29-C3 不过 ⇒ pass_all 不过。"""
        ev = sss.eval_r29_candidate(_strong_trades(), "flat", lambda t: 50)
        assert ev["R29_C3"]["pass"] is False
        assert ev["pass_all"] is False


# ---------------------------------------------------------------------------
# ④ 灵敏度扫描：翻转检出 + 「基线不过不算翻转」
# ---------------------------------------------------------------------------


def _two_leg_trades(g1_win_rate_high=True):
    """40 笔三档（G2=A腿 先来 8 笔 / G1=B腿 8 笔 / G3=无腿 24 笔）。

    默认 G1 强（6 胜 2 亏，胜率 0.75/盈亏比 5.0）、G2 弱（3 胜 5 亏，0.375/1.0）、
    G3 中（6 胜 18 亏，0.25/3.0）——排序质量单调 ⇒ Spearman 正。
    日期按全局索引交错（i%20），保证前后半窗都含三档（否则半窗零方差 ⇒
    Spearman 无定义 ⇒ R29-C3 不过，同 calibration 钉测的坑）。
    """
    g1_rets = [0.05] * 6 + [-0.01] * 2 if g1_win_rate_high else [0.02] * 3 + [-0.02] * 5
    trades = []

    def _append(panel, rets):
        for r in rets:
            i = len(trades)
            trades.append(
                _trade(panel=panel, ret=r, entry_date=f"2026-01-{i % 20 + 1:02d}")
            )

    _append({"leg_a": True}, [0.02] * 3 + [-0.02] * 5)  # G2（A 腿）先来
    _append({"leg_b": True}, g1_rets)  # G1（B 腿）
    _append({}, [0.03] * 6 + [-0.01] * 18)  # G3（无腿）
    return trades


# 灵敏度玩具例的候选 spec（两腿，扰动后排序可变 ⇒ 翻转可构造）
_TOY_SPEC = {"contrib_mult": {}, "panel_weights": {"leg_a": 20, "leg_b": 25}}


class TestSensitivity:
    def test_flip_detected(self):
        """玩具例：基线全过（篮子=G1），leg_b×0.5=12.5 < leg_a ⇒ 篮子掉到 G2 翻转。

        leg_a×1.5=30 > leg_b=25 ⇒ 篮子同样掉到 G2 翻转；另两向（leg_a×0.5 /
        leg_b×1.5）排序不变不翻转 ⇒ 恰 2 次翻转。
        """
        s = sss.sensitivity_scan({"w1": _two_leg_trades()}, "toy", _TOY_SPEC)
        assert s["n_perturbations"] == 4  # 2 腿 × 2 向
        assert s["n_checks"] == 4  # × 1 窗
        assert s["base_pass_by_window"] == {"w1": True}
        assert s["n_flip"] == 2
        assert s["parameter_sensitive"] is True
        flipped = {(f["leg"], f["perturbed_to"]) for f in s["flips"]}
        assert flipped == {("leg_a", 30.0), ("leg_b", 12.5)}
        assert all(f["base_pass"] is True for f in s["flips"])

    def test_baseline_fail_window_not_a_flip(self):
        """基线本就不过的窗里扰动失败不算翻转（本来就不行，不是参数敏感）。"""
        s = sss.sensitivity_scan(
            {"w1": _two_leg_trades(g1_win_rate_high=False)}, "toy", _TOY_SPEC
        )
        assert s["base_pass_by_window"] == {"w1": False}  # 基线 G1 篮子胜率 0.375 不过
        assert s["n_flip"] == 0  # 扰动只把篮子在 G1/G2 间挪，两组都不过 ⇒ 无翻转
        assert s["parameter_sensitive"] is False

    def test_real_candidate_perturbation_counts(self):
        """W1 四腿 × 2 向 = 8 扰动 × 2 窗 = 16 检查；contrib_mult 空 ⇒ 无 contrib 扰动。"""
        windows = {"主窗": _strong_trades(), "跨窗": _strong_trades()}
        s = sss.sensitivity_scan(
            windows, "W1_balanced", sss.R29_CANDIDATES["W1_balanced"]
        )
        assert s["n_perturbations"] == 8
        assert s["n_checks"] == 16
        assert s["parameter_sensitive"] is False  # 强信号下 ±50% 不翻转

    def test_w4_negative_legs_perturbed_both_ways(self):
        """负腿同样 ±50%（−5 → −2.5/−7.5）：W4 共 10 腿 × 2 向 = 20 扰动。"""
        windows = {"w1": _strong_trades()}
        s = sss.sensitivity_scan(
            windows, "W4_tilt_neg", sss.R29_CANDIDATES["W4_tilt_neg"]
        )
        assert s["n_perturbations"] == 20
        neg_perturbs = [p for p in s["flips"] if p["leg"] in sss.R29_NEG_LEGS]
        assert neg_perturbs == []  # 样本里负腿零命中 ⇒ 不贡献翻转


# ---------------------------------------------------------------------------
# ⑤ Phase 2 守卫：pre2019 硬拒绝（反过拟合纪律代码化）
# ---------------------------------------------------------------------------


class TestPhase2Guard:
    def _write(self, path, trades):
        path.write_text(json.dumps({"trades": trades}), encoding="utf-8")

    def test_pre2019_rejected(self, tmp_path, capsys):
        f = tmp_path / "x_pre2019.json"
        self._write(f, [_trade(ret=0.1)])
        assert sss.main(["--phase2", "--from-trades", str(f)]) == 2
        assert "反过拟合" in capsys.readouterr().err

    def test_empty_trades_rejected(self, tmp_path):
        f = tmp_path / "x_n400.json"
        self._write(f, [])
        assert sss.main(["--phase2", "--from-trades", str(f)]) == 1

    def test_mutually_exclusive_modes(self):
        with pytest.raises(SystemExit):
            sss.main(["--phase2", "--phase3", "--from-trades", "x.json"])
        with pytest.raises(SystemExit):
            sss.main([])  # 两模式都不给 ⇒ parser.error


# ---------------------------------------------------------------------------
# ⑥ Phase 3 守卫：只接受 pre2019 单文件；⑧ 缺 Phase 2 名单报错
# ---------------------------------------------------------------------------


class TestPhase3Guard:
    def _write(self, path, trades):
        path.write_text(json.dumps({"trades": trades}), encoding="utf-8")

    def test_rejects_non_pre2019(self, tmp_path):
        f = tmp_path / "x_n400.json"
        self._write(f, [_trade(ret=0.1)])
        assert sss.main(["--phase3", "--from-trades", str(f)]) == 2

    def test_rejects_multiple_files(self, tmp_path):
        f1 = tmp_path / "a_pre2019.json"
        f2 = tmp_path / "b_pre2019.json"
        self._write(f1, [_trade(ret=0.1)])
        self._write(f2, [_trade(ret=0.1)])
        assert sss.main(["--phase3", "--from-trades", str(f1), str(f2)]) == 2

    def test_missing_phase2_finalists_errors(self, tmp_path, monkeypatch, capsys):
        """终审名单必须跑数前已定：找不到 Phase 2 落盘 ⇒ return 2。"""
        monkeypatch.chdir(tmp_path)  # 空目录 ⇒ 无 r29_phase2.json
        f = tmp_path / "x_pre2019.json"
        self._write(f, [_trade(ret=0.1)])
        assert sss.main(["--phase3", "--from-trades", str(f)]) == 2
        assert "Phase 2" in capsys.readouterr().err

    def test_empty_recommended_errors(self, tmp_path, monkeypatch, capsys):
        """Phase 2 推荐名单为空 ⇒ 无可终审方案，return 2（不进终审）。"""
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "artifacts/logs/score_stability_study/r29_phase2.json"
        out.parent.mkdir(parents=True)
        out.write_text(json.dumps({"recommended": []}), encoding="utf-8")
        f = tmp_path / "x_pre2019.json"
        self._write(f, [_trade(ret=0.1)])
        assert sss.main(["--phase3", "--from-trades", str(f)]) == 2
        assert "名单" in capsys.readouterr().err

    def test_finalist_guard_runs_before_loading_trades(
        self, tmp_path, monkeypatch, capsys
    ):
        """守卫先行：名单不可用时根本不读终审窗文件——pre2019 路径不存在也直接
        return 2（旧顺序会先 _load_trades 抛 FileNotFoundError）。"""
        monkeypatch.chdir(tmp_path)  # 空目录 ⇒ 无 r29_phase2.json
        ghost = tmp_path / "ghost_pre2019.json"  # 不存在的终审窗文件
        assert sss.main(["--phase3", "--from-trades", str(ghost)]) == 2
        assert "Phase 2" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Phase 3 终审判定（终审线 = R29-C1∧C2∧C3，C4 不进终审线）
# ---------------------------------------------------------------------------


class TestPhase3Report:
    def _fake_eval(self, c1=True, c2=True, c3=True, c4=False):
        return {
            "candidate": "toy",
            "n_trades": 1,
            "corr": {"spearman": 0.1},
            "half_window": {"consistent": True, "first_half": {"spearman": 0.1}},
            "basket": {"win_rate": 0.5, "payoff_ratio": 2.5},
            "basket_margin": 0.2,
            "universe_margin": 0.1,
            "C3_star": True,
            "C3_star_wilson_overlap": None,
            "R29_C1": {"pass": c1},
            "R29_C2": {"pass": c2},
            "R29_C3": {"pass": c3},
            "R29_C4": {"pass": c4, "strong_frac": 0.99},
            "pass_all": c1 and c2 and c3 and c4,
        }

    def test_terminal_line_excludes_c4(self, monkeypatch):
        """C4 不过不影响终审（终审线只 C1∧C2∧C3）；C2 失线 ⇒ 一票否决。"""
        monkeypatch.setattr(
            sss, "eval_r29_candidate", lambda trades, name, fn: self._fake_eval()
        )
        rep = sss.phase3_report([{"ret": 0.1}], ["W1_balanced", "W2_pre2019_tilt"])
        assert rep["verdict"] == "通过"
        assert rep["passed"] == ["W1_balanced", "W2_pre2019_tilt"]
        for c in rep["candidates"].values():
            assert c["terminal_pass"] is True  # C4=False 照样过终审
        # R29-C2 失线 ⇒ 证伪
        monkeypatch.setattr(
            sss,
            "eval_r29_candidate",
            lambda trades, name, fn: self._fake_eval(c2=False),
        )
        rep2 = sss.phase3_report([{"ret": 0.1}], ["W1_balanced"])
        assert rep2["verdict"] == "证伪"
        assert rep2["passed"] == []
        assert rep2["fallback"]  # 判负退路如实标注

    def test_unknown_finalist_raises(self):
        with pytest.raises(KeyError):
            sss.phase3_report([{"ret": 0.1}], ["NO_SUCH_CANDIDATE"])


# ---------------------------------------------------------------------------
# ⑦ CLI 冒烟：tmp_path 合成 trades → Phase 2 出 artifact；Phase 3 终审通过
# ---------------------------------------------------------------------------


class TestCliSmoke:
    def _write(self, path, trades):
        path.write_text(json.dumps({"trades": trades}), encoding="utf-8")

    def test_phase2_smoke_writes_artifact(self, tmp_path, monkeypatch):
        """主窗+跨窗两份合成 trades ⇒ artifact 落盘；窗口标签 n400→主窗 / cw→跨窗。

        强信号样本上 W1/W2/W4 两窗全过且参数不敏感 ⇒ 进推荐名单；
        W3 的 rsi_bull_div ×1.5=60 撞强档线 ⇒ C4 翻转 ⇒ 参数敏感被刷（机械生成）。
        """
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "score_s0_n400.rejudged.json"
        f2 = tmp_path / "score_s0_n1000_cw.rejudged.json"
        self._write(f1, _strong_trades())
        self._write(f2, _strong_trades())
        assert sss.main(["--phase2", "--from-trades", str(f1), str(f2)]) == 0
        out = tmp_path / "artifacts/logs/score_stability_study/r29_phase2.json"
        assert out.exists()
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["windows"] == ["主窗", "跨窗"]
        assert set(rep["candidates"]) == set(sss.R29_CANDIDATES)
        assert rep["candidates"]["W1_balanced"]["pass_all_windows"] is True
        assert (
            rep["candidates"]["W3_no_deep"]["sensitivity"]["parameter_sensitive"]
            is True
        )
        assert set(rep["recommended"]) == {
            "W1_balanced",
            "W2_pre2019_tilt",
            "W4_tilt_neg",
        }

    def test_phase3_smoke_after_phase2(self, tmp_path, monkeypatch, capsys):
        """先 Phase 2 落名单，再 Phase 3 终审（pre2019 同分布强样本 ⇒ 通过）。

        退出码恒 0（证伪也是结论）；三窗并排段在有 Phase 2 参照时打印。
        """
        monkeypatch.chdir(tmp_path)
        f1 = tmp_path / "score_s0_n400.rejudged.json"
        f2 = tmp_path / "score_s0_n1000_cw.rejudged.json"
        f3 = tmp_path / "score_s0_n1000_pre2019.rejudged.json"
        for f in (f1, f2, f3):
            self._write(f, _strong_trades())
        assert sss.main(["--phase2", "--from-trades", str(f1), str(f2)]) == 0
        capsys.readouterr()  # 清掉 phase2 输出
        assert sss.main(["--phase3", "--from-trades", str(f3)]) == 0
        out = tmp_path / "artifacts/logs/score_stability_study/r29_phase3_pre2019.json"
        assert out.exists()
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["verdict"] == "通过"
        assert set(rep["passed"]) == {"W1_balanced", "W2_pre2019_tilt", "W4_tilt_neg"}
        assert rep["finalists"] == rep["passed"]  # 终审名单 = Phase 2 落盘推荐
        for c in rep["candidates"].values():
            assert c["terminal_pass"] is True
        assert "三窗并排" in capsys.readouterr().out
