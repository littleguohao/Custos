# -*- coding: utf-8 -*-
"""score_combo_search_study 钉测：R30 网格生成/gcd 去重/筛选线边界/F5 翻转/CLI 守卫/快慢路径对拍。

合成 trades（factor_contrib/panel/ret/entry_date 手工构造），零真实数据依赖；
判据边界用手算案例钉住（F1/F2 恰线 0.45/2.6 = ≥ 过线语义）。CLI 冒烟用
monkeypatch.chdir(tmp_path) 隔离产物路径（脚本落盘路径相对仓库根），并把
POS_LEVELS 换成小档集加速（generate_grid 运行时读模块全局，语义不变）。
"""

from __future__ import annotations

import itertools
import json
import random
from collections import Counter
from functools import reduce
from math import gcd

import pytest

from custos.research import score_calibration_study as scs
from custos.research import score_combo_search_study as scbs


def _trade(contrib=None, panel=None, ret=0.0, entry_date="2026-01-05"):
    t = {"ret": ret, "entry_date": entry_date}
    if contrib is not None:
        t["factor_contrib"] = contrib
    if panel is not None:
        t["panel"] = panel
    return t


def _strong_trades(n=200):
    """强信号样本：rsi_bull_div 命中组 75% 胜率/盈亏比 5，未中组 25%/1.0。

    两段时间 101/99（F3 要求前后半窗都可评估且同正；中位切分点须落在
    前段日期上——同 score_stability_study 钉测的构造）。
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


def _random_trades(n=600, seed=7):
    """随机样本：11 条搜索腿三态（True/False/None）+ 随机 contrib/ret，两簇日期。"""
    rng = random.Random(seed)
    trades = []
    for i in range(n):
        panel = {}
        for k in scbs.POS_LEGS + list(scbs.NEG_LEGS):
            r = rng.random()
            panel[k] = True if r < 0.35 else (False if r < 0.75 else None)
        trades.append(
            _trade(
                contrib={"j_low": 24, "bbi_above": rng.choice([0, 5])},
                panel=panel,
                ret=round(rng.gauss(0.005, 0.04), 4),
                entry_date=f"2025-{i % 12 + 1:02d}-{i % 28 + 1:02d}",
            )
        )
    return trades


# ---------------------------------------------------------------------------
# ① 网格生成：计数 / gcd 排序等价去重正确性 / 负腿块不参与去重 / 全零排除
# ---------------------------------------------------------------------------


class TestGrid:
    def test_preregistration_constants(self):
        """搜索空间常量与预注册页逐字一致。"""
        assert scbs.POS_LEGS == [
            "rsi_deep_oversold",
            "weekly_j_low",
            "rsi_bull_div",
            "macd_bottom_divergence",
            "leader_volume",
        ]
        assert scbs.POS_LEVELS == (0, 10, 20, 30, 40)
        assert scbs.NEG_LEGS == (
            "rsi_strong",
            "b1_ignition",
            "volume_contraction",
            "relative_strength_strong",
            "macd_top_divergence",
            "ignition",
        )
        assert scbs.NEG_BLOCK_WEIGHT == -5
        assert scbs.SEARCH_WIN_RATE_FLOOR == 0.45
        assert scbs.SEARCH_PAYOFF_FLOOR == 2.6
        assert scbs.BASE_WIN_RATE_FLOOR == 0.40
        assert scbs.BASE_PAYOFF_FLOOR == 2.4
        assert scbs.TOP_SURVIVORS == 3

    def test_grid_counts(self):
        """原始 3124×2=6248；gcd 去重后 2851 类 ×2 = 5702（独立重算交叉验证）。"""
        grid = scbs.generate_grid()
        assert (len(scbs.POS_LEVELS) ** len(scbs.POS_LEGS) - 1) * 2 == 6248
        expect = set()
        for levels in itertools.product((0, 1, 2, 3, 4), repeat=5):
            if not any(levels):
                continue
            g = reduce(gcd, [x for x in levels if x])
            expect.add(tuple(x // g for x in levels))
        assert len(expect) == 2851  # 排序等价类数（数学独立重算）
        assert {tuple(c["canonical"]) for c in grid} == expect
        assert len(grid) == 5702

    def test_scaled_duplicates_evaluated_once(self):
        """10/10/20/0/0 与 20/20/40/0/0 同类 ⇒ 只评一次，代表权重 = 原始向量 ×10。"""
        grid = scbs.generate_grid()
        cls = [c for c in grid if c["canonical"] == [1, 1, 2, 0, 0]]
        assert len(cls) == 2  # 负腿块 关/开 各一
        for c in cls:
            assert {k: c["panel_weights"][k] for k in scbs.POS_LEGS} == {
                "rsi_deep_oversold": 10,
                "weekly_j_low": 10,
                "rsi_bull_div": 20,
                "macd_bottom_divergence": 0,
                "leader_volume": 0,
            }
        # 缩放后的非原始向量不作为 canonical 出现
        assert not any(c["canonical"] == [2, 2, 4, 0, 0] for c in grid)

    def test_all_zero_excluded(self):
        """全零（无正腿）预注册排除。"""
        assert all(any(c["canonical"]) for c in scbs.generate_grid())

    def test_neg_block_not_deduped(self):
        """负腿块不参与去重：每个等价类恰 2 个（关/开）；开 = 六负腿各 −5。"""
        grid = scbs.generate_grid()
        cnt = Counter(tuple(c["canonical"]) for c in grid)
        assert set(cnt.values()) == {2}
        on = [c for c in grid if c["neg_block"]]
        off = [c for c in grid if not c["neg_block"]]
        assert len(on) == len(off) == 2851
        for c in on[:50]:
            assert all(c["panel_weights"][k] == -5 for k in scbs.NEG_LEGS)
        for c in off[:50]:
            assert all(c["panel_weights"][k] == 0 for k in scbs.NEG_LEGS)


# ---------------------------------------------------------------------------
# ② 名字确定性（落盘可溯源）
# ---------------------------------------------------------------------------


class TestNaming:
    def test_deterministic_and_unique(self):
        g1 = [c["name"] for c in scbs.generate_grid()]
        g2 = [c["name"] for c in scbs.generate_grid()]
        assert g1 == g2  # 两次生成逐位一致
        assert len(set(g1)) == len(g1)  # 无重名

    def test_known_names(self):
        """单腿类 canonical (0,0,1,0,0) ⇒ bd10 / bd10_nb（_nb = 负腿块开）。"""
        by_name = {c["name"]: c for c in scbs.generate_grid()}
        assert by_name["bd10"]["panel_weights"]["rsi_bull_div"] == 10
        assert by_name["bd10"]["neg_block"] is False
        assert by_name["bd10_nb"]["neg_block"] is True
        assert by_name["bd10_nb"]["panel_weights"]["ignition"] == -5
        assert by_name["rd10_wj10_bd20"]["canonical"] == [1, 1, 2, 0, 0]


# ---------------------------------------------------------------------------
# ③ F1/F2 恰线边界（0.45/2.6 = ≥ 过）+ None 安全 + 强信号全过
# ---------------------------------------------------------------------------


def _boundary_trades(win_ret=0.26, n_win=9):
    """100 笔：候选分前 20（basket 组 x=10）n_win 胜；对照组 y=20 ⇒ V0 篮子胜率 0.2。

    默认参数下篮子：9 胜（ret 0.26）11 亏（−0.10）⇒ 胜率恰 0.45、盈亏比恰 2.6。
    V0 篮子（V0 分 = x+y 求和 ⇒ 对照组 20 在前）= 对照组前 20：4 胜 16 亏 ⇒ 0.2。
    """
    trades = []
    for i in range(20):
        trades.append(
            _trade(
                contrib={"x": 10},
                ret=win_ret if i < n_win else -0.10,
                entry_date=f"2026-01-{i + 1:02d}",
            )
        )
    for i in range(80):
        trades.append(
            _trade(
                contrib={"y": 20},
                ret=0.05 if i < 4 else -0.05,
                entry_date=f"2026-02-{i % 28 + 1:02d}",
            )
        )
    return trades


def _x_score(t):
    return (t.get("factor_contrib") or {}).get("x", 0)


class TestFilterBoundary:
    def test_exactly_at_search_floors_passes(self):
        """恰线 = 过：胜率恰 0.45 且盈亏比恰 2.6 都判过（≥ 语义，手算钉住）。"""
        ev = scbs.eval_combo(_boundary_trades(), "toy", _x_score)
        assert ev["basket"]["win_rate"] == pytest.approx(0.45)
        assert ev["basket"]["payoff_ratio"] == pytest.approx(2.6)
        assert ev["F1"]["pass"] is True
        assert ev["F2"]["pass"] is True
        assert ev["F1"]["floor"] == 0.45
        assert ev["F2"]["floor"] == 2.6

    def test_win_rate_below_floor_fails(self):
        """胜率 0.40 < 0.45 ⇒ F1 不过（恰在 R29 基线上也不够——显著性税）。"""
        ev = scbs.eval_combo(_boundary_trades(n_win=8), "toy", _x_score)
        assert ev["basket"]["win_rate"] == pytest.approx(0.40)
        assert ev["F1"]["pass"] is False

    def test_payoff_below_floor_fails(self):
        """盈亏比 2.5 < 2.6 ⇒ F2 不过（胜率条件仍过，两条判据独立）。"""
        ev = scbs.eval_combo(_boundary_trades(win_ret=0.25), "toy", _x_score)
        assert ev["basket"]["payoff_ratio"] == pytest.approx(2.5)
        assert ev["F1"]["pass"] is True
        assert ev["F2"]["pass"] is False

    def test_f1_requires_beating_v0(self):
        """胜率 ≥0.45 但不 > V0 篮子胜率 ⇒ F1 不过（两个子条件缺一不可）。"""
        trades = _boundary_trades()
        for t in trades:  # 候选与 V0 同序 ⇒ V0 篮子 = 同一组，胜率同样 0.45
            t["factor_contrib"] = {"x": (t["factor_contrib"] or {}).get("x", 0)}
        ev = scbs.eval_combo(trades, "toy", _x_score)
        assert ev["F1"]["basket_win_rate"] == pytest.approx(0.45)
        assert ev["F1"]["v0_basket_win_rate"] == pytest.approx(0.45)
        assert ev["F1"]["pass"] is False  # 0.45 > 0.45 不成立

    def test_f2_none_safe_when_payoff_undefined(self):
        """篮子全赢（无亏单 ⇒ payoff 无定义）⇒ F2 判 False 并如实标注。"""
        ev = scbs.eval_combo(_boundary_trades(n_win=20), "toy", _x_score)
        assert ev["basket"]["payoff_ratio"] is None
        assert ev["F2"]["pass"] is False
        assert ev["F2"]["note"]

    def test_f1_none_safe(self):
        """胜率缺省 ⇒ F1 判 False 并标注（不编数）。"""
        f1 = scbs._r30_f1({"win_rate": None}, {"win_rate": 0.3}, 0.45)
        assert f1["pass"] is False
        assert f1["note"]
        f1b = scbs._r30_f1({"win_rate": 0.5}, {}, 0.45)
        assert f1b["pass"] is False
        assert f1b["note"]

    def test_strong_signal_all_pass_at_search_floors(self):
        """强信号样本：bd 单腿组合 F1~F4 全过 ⇒ pass_all（手算：篮子 75%/5.0，V0 50%）。"""
        fn = scs.make_candidate_score({}, {"rsi_bull_div": 20})
        ev = scbs.eval_combo(_strong_trades(), "bd20", fn)
        assert ev["basket"]["win_rate"] == pytest.approx(0.75)
        assert ev["F1"]["pass"] is True  # 0.75 ≥ 0.45 且 > V0 篮子 0.50
        assert ev["F2"]["pass"] is True  # 盈亏比 5.0 ≥ 2.6
        assert ev["F3"]["pass"] is True  # Spearman>0 且半窗同正
        assert ev["F4"]["pass"] is True  # 得分 20/0 ⇒ 强档占比 0
        assert ev["pass_all"] is True
        # 参考列字段齐全（C3★ + Wilson 注记，不进判定）
        assert "C3_star" in ev and "C3_star_wilson_overlap" in ev

    def test_baseline_floors_as_parameters(self):
        """胜率/盈亏比线作参数传入：0.40 在搜索线不过、在 R29 基线过。"""
        trades = _boundary_trades(n_win=8)  # 篮子胜率恰 0.40
        assert scbs.eval_combo(trades, "toy", _x_score)["F1"]["pass"] is False
        ev_base = scbs.eval_combo(
            trades, "toy", _x_score, scbs.BASE_WIN_RATE_FLOOR, scbs.BASE_PAYOFF_FLOOR
        )
        assert ev_base["F1"]["pass"] is True  # 0.40 ≥ 0.40 且 > V0 0.20


# ---------------------------------------------------------------------------
# ④ F5 灵敏度：翻转检出 + 「基线不过不算翻转」+ 零权重腿不扰动
# ---------------------------------------------------------------------------


def _two_leg_trades(g1_win_rate_high=True):
    """40 笔三档（G2=weekly_j_low 先来 8 笔 / G1=rsi_bull_div 8 笔 / G3=无腿 24 笔）。

    ⚠️ 腿名必须用搜索空间 11 条真腿——fast path 的命中矩阵只有这 11 列，
    编造的腿名在快路径里不计分（真腿名两条路径才同口径）。
    默认 G1 强（6 胜 2 亏，胜率 0.75/盈亏比 5.0）、G2 弱（3 胜 5 亏，0.375/1.0）、
    G3 中（6 胜 18 亏，0.25/3.0）——排序质量单调 ⇒ Spearman 正。
    日期按全局索引交错（i%20），保证前后半窗都含三档（同 stability 钉测的构造）。
    """
    g1_rets = [0.05] * 6 + [-0.01] * 2 if g1_win_rate_high else [0.02] * 3 + [-0.02] * 5
    trades = []

    def _append(panel, rets):
        for r in rets:
            i = len(trades)
            trades.append(
                _trade(panel=panel, ret=r, entry_date=f"2026-01-{i % 20 + 1:02d}")
            )

    _append({"weekly_j_low": True}, [0.02] * 3 + [-0.02] * 5)  # G2 先来
    _append({"rsi_bull_div": True}, g1_rets)  # G1
    _append({}, [0.03] * 6 + [-0.01] * 18)  # G3（无腿）
    return trades


# 灵敏度玩具例的组合权重（两腿，扰动后排序可变 ⇒ 翻转可构造）
_TOY_WEIGHTS = {"weekly_j_low": 20, "rsi_bull_div": 25}


class TestSensitivity:
    def test_flip_detected(self):
        """玩具例：基线全过（篮子=G1），rsi_bull_div×0.5=12.5 < weekly_j_low ⇒ 翻 G2。

        weekly_j_low×1.5=30 > rsi_bull_div=25 ⇒ 篮子同样掉到 G2 翻转；另两向
        （weekly_j_low×0.5 / rsi_bull_div×1.5）排序不变不翻转 ⇒ 恰 2 次翻转。
        判定对象 = R29 基线 pass_all。
        """
        windows = {"w1": scbs.prepare_window(_two_leg_trades())}
        s = scbs.combo_sensitivity(windows, "toy", _TOY_WEIGHTS)
        assert s["n_perturbations"] == 4  # 2 腿 × 2 向
        assert s["n_checks"] == 4  # × 1 窗
        assert s["base_pass_by_window"] == {"w1": True}
        assert s["n_flip"] == 2
        assert s["parameter_sensitive"] is True
        flipped = {(f["leg"], f["perturbed_to"]) for f in s["flips"]}
        assert flipped == {("weekly_j_low", 30.0), ("rsi_bull_div", 12.5)}
        assert all(f["base_pass"] is True for f in s["flips"])

    def test_baseline_fail_window_not_a_flip(self):
        """基线本就不过的窗里扰动失败不算翻转（本来就不行，不是参数敏感）。"""
        windows = {"w1": scbs.prepare_window(_two_leg_trades(g1_win_rate_high=False))}
        s = scbs.combo_sensitivity(windows, "toy", _TOY_WEIGHTS)
        assert s["base_pass_by_window"] == {"w1": False}  # 基线 G1 篮子胜率 0.375 不过
        assert s["n_flip"] == 0  # 扰动只把篮子在 G1/G2 间挪，两组都不过 ⇒ 无翻转
        assert s["parameter_sensitive"] is False

    def test_zero_weight_legs_not_perturbed(self):
        """零权重腿不扰动：完整 dict 里 9 条零腿不产生扰动项。"""
        windows = {"w1": scbs.prepare_window(_strong_trades())}
        pw = {**{k: 0 for k in scbs.ALL_LEGS}, "rsi_bull_div": 20}
        s = scbs.combo_sensitivity(windows, "bd20", pw)
        assert s["n_perturbations"] == 2  # 只有 bd 一条非零腿 × 2 向
        assert s["parameter_sensitive"] is False  # 强信号下 ±50% 不翻转

    def test_negative_legs_perturbed_both_ways(self):
        """负腿同样 ±50%（−5 → −2.5/−7.5，round 2 位）。"""
        windows = {"w1": scbs.prepare_window(_strong_trades())}
        pw = {"rsi_bull_div": 20, "rsi_strong": -5}
        s = scbs.combo_sensitivity(windows, "toy", pw)
        assert s["n_perturbations"] == 4  # 2 腿 × 2 向
        neg_vals = {f["perturbed_to"] for f in s["flips"] if f["leg"] == "rsi_strong"}
        assert neg_vals == set()  # 样本里 rsi_strong 零命中 ⇒ 不贡献翻转
        # 扰动取值钉住（−2.5/−7.5）
        pwds = {
            (leg, round(w * 0.5, 2), round(w * 1.5, 2))
            for leg, w in pw.items()
            if w != 0
        }
        assert ("rsi_strong", -2.5, -7.5) in pwds


# ---------------------------------------------------------------------------
# ⑤ --search 守卫：pre2019 硬拒绝（反过拟合纪律代码化）
# ---------------------------------------------------------------------------


class TestSearchGuard:
    def _write(self, path, trades):
        path.write_text(json.dumps({"trades": trades}), encoding="utf-8")

    def test_pre2019_rejected(self, tmp_path, capsys):
        f = tmp_path / "x_pre2019.json"
        self._write(f, [_trade(ret=0.1)])
        assert scbs.main(["--search", "--from-trades", str(f)]) == 2
        assert "反过拟合" in capsys.readouterr().err

    def test_requires_exactly_two_files(self, tmp_path, capsys):
        """预注册调参双窗：--search 需要且仅需要两份输入，否则 return 2（不读文件）。"""
        f1 = tmp_path / "x_n400.json"
        f2 = tmp_path / "x_n1000_cw.json"
        f3 = tmp_path / "y_n999.json"
        for f in (f1, f2, f3):
            self._write(f, [_trade(ret=0.1)])
        assert scbs.main(["--search", "--from-trades", str(f1)]) == 2
        assert "需要且仅需要" in capsys.readouterr().err
        assert scbs.main(["--search", "--from-trades", str(f1), str(f2), str(f3)]) == 2
        assert "需要且仅需要" in capsys.readouterr().err

    def test_empty_trades_rejected(self, tmp_path):
        f1 = tmp_path / "x_n400.json"
        f2 = tmp_path / "x_n1000_cw.json"
        self._write(f1, [])
        self._write(f2, [_trade(ret=0.1)])
        assert scbs.main(["--search", "--from-trades", str(f1), str(f2)]) == 1

    def test_mutually_exclusive_modes(self):
        with pytest.raises(SystemExit):
            scbs.main(["--search", "--final", "--from-trades", "x.json"])
        with pytest.raises(SystemExit):
            scbs.main([])  # 两模式都不给 ⇒ parser.error


# ---------------------------------------------------------------------------
# ⑥ --final 守卫：只接受 pre2019 单文件；终审名单机械读取（缺文件/空名单/未知组合）
# ---------------------------------------------------------------------------


class TestFinalGuard:
    def _write(self, path, trades):
        path.write_text(json.dumps({"trades": trades}), encoding="utf-8")

    def test_rejects_non_pre2019(self, tmp_path):
        f = tmp_path / "x_n400.json"
        self._write(f, [_trade(ret=0.1)])
        assert scbs.main(["--final", "--from-trades", str(f)]) == 2

    def test_rejects_multiple_files(self, tmp_path):
        f1 = tmp_path / "a_pre2019.json"
        f2 = tmp_path / "b_pre2019.json"
        self._write(f1, [_trade(ret=0.1)])
        self._write(f2, [_trade(ret=0.1)])
        assert scbs.main(["--final", "--from-trades", str(f1), str(f2)]) == 2

    def test_missing_search_file_errors(self, tmp_path, monkeypatch, capsys):
        """终审名单必须跑数前已定：找不到 r30_search.json ⇒ return 2。"""
        monkeypatch.chdir(tmp_path)  # 空目录 ⇒ 无 r30_search.json
        f = tmp_path / "x_pre2019.json"
        self._write(f, [_trade(ret=0.1)])
        assert scbs.main(["--final", "--from-trades", str(f)]) == 2
        assert "--search" in capsys.readouterr().err

    def test_empty_survivors_errors(self, tmp_path, monkeypatch, capsys):
        """survivors 为空 ⇒ 无可终审组合，return 2（不进终审）。"""
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "artifacts/logs/score_combo_search_study/r30_search.json"
        out.parent.mkdir(parents=True)
        out.write_text(json.dumps({"survivors": [], "passed": []}), encoding="utf-8")
        f = tmp_path / "x_pre2019.json"
        self._write(f, [_trade(ret=0.1)])
        assert scbs.main(["--final", "--from-trades", str(f)]) == 2
        assert "名单" in capsys.readouterr().err

    def test_unknown_survivor_errors(self, tmp_path, monkeypatch, capsys):
        """survivors 里有 passed 清单查不到的组合 ⇒ 名单不可用，return 2。"""
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "artifacts/logs/score_combo_search_study/r30_search.json"
        out.parent.mkdir(parents=True)
        out.write_text(
            json.dumps({"survivors": ["ghost_combo"], "passed": []}), encoding="utf-8"
        )
        f = tmp_path / "x_pre2019.json"
        self._write(f, [_trade(ret=0.1)])
        assert scbs.main(["--final", "--from-trades", str(f)]) == 2
        assert "未知组合" in capsys.readouterr().err

    def test_finalist_guard_runs_before_loading_trades(
        self, tmp_path, monkeypatch, capsys
    ):
        """守卫先行：名单不可用时根本不读终审窗文件——pre2019 路径不存在也直接
        return 2（旧顺序会先 _load_trades 抛 FileNotFoundError）。"""
        monkeypatch.chdir(tmp_path)  # 空目录 ⇒ 无 r30_search.json
        ghost = tmp_path / "ghost_pre2019.json"  # 不存在的终审窗文件
        assert scbs.main(["--final", "--from-trades", str(ghost)]) == 2
        assert "--search" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 终审判定（终审线 = R29 基线 F1∧F2∧F3，F4 只作参考列）
# ---------------------------------------------------------------------------


class TestFinalReport:
    def _fake_eval(self, f1=True, f2=True, f3=True, f4=False):
        return {
            "combo": "toy",
            "n_trades": 1,
            "corr": {"spearman": 0.1},
            "half_window": {"consistent": True, "first_half": {"spearman": 0.1}},
            "basket": {"win_rate": 0.5, "payoff_ratio": 2.5},
            "band_stats": {">=60": {"n": 99}},
            "basket_margin": 0.2,
            "universe_margin": 0.1,
            "C3_star": True,
            "C3_star_wilson_overlap": None,
            "F1": {"pass": f1},
            "F2": {"pass": f2},
            "F3": {"pass": f3},
            "F4": {"pass": f4, "strong_frac": 0.99},
            "pass_all": f1 and f2 and f3 and f4,
        }

    def test_terminal_line_excludes_f4(self, monkeypatch):
        """F4 不过不影响终审（终审线只 F1∧F2∧F3）；F2 失线 ⇒ 一票否决。"""
        monkeypatch.setattr(
            scbs, "eval_combo", lambda trades, name, fn, wr, pf: self._fake_eval()
        )
        specs = {"a": {"panel_weights": {}}, "b": {"panel_weights": {}}}
        rep = scbs.final_report([{"ret": 0.1}], ["a", "b"], specs)
        assert rep["verdict"] == "通过"
        assert rep["passed"] == ["a", "b"]
        for c in rep["candidates"].values():
            assert c["terminal_pass"] is True  # F4=False 照样过终审
        # F2 失线 ⇒ 证伪
        monkeypatch.setattr(
            scbs,
            "eval_combo",
            lambda trades, name, fn, wr, pf: self._fake_eval(f2=False),
        )
        rep2 = scbs.final_report([{"ret": 0.1}], ["a"], specs)
        assert rep2["verdict"] == "证伪"
        assert rep2["passed"] == []
        assert rep2["fallback"]  # 判负退路如实标注

    def test_eval_called_with_baseline_floors(self, monkeypatch):
        """终审评估必须用 R29 基线（0.40/2.4），不是搜索线（0.45/2.6）。"""
        seen = []

        def _spy(trades, name, fn, wr_floor, payoff_floor):
            seen.append((wr_floor, payoff_floor))
            return self._fake_eval()

        monkeypatch.setattr(scbs, "eval_combo", _spy)
        scbs.final_report([{"ret": 0.1}], ["a"], {"a": {"panel_weights": {}}})
        assert seen == [(scbs.BASE_WIN_RATE_FLOOR, scbs.BASE_PAYOFF_FLOOR)]

    def test_unknown_finalist_raises(self):
        with pytest.raises(KeyError):
            scbs.final_report([{"ret": 0.1}], ["NO_SUCH_COMBO"], {})


# ---------------------------------------------------------------------------
# ⑦ CLI 冒烟：tmp_path + 小档集（monkeypatch POS_LEVELS）跑通 search→final 链路
# ---------------------------------------------------------------------------


class TestCliSmoke:
    def _write(self, path, trades):
        path.write_text(json.dumps({"trades": trades}), encoding="utf-8")

    def test_search_then_final(self, tmp_path, monkeypatch, capsys):
        """小网格（POS_LEVELS=(0,10) ⇒ 31 类 ×2=62）双窗 search 落盘 → final 终审通过。

        强信号样本：凡含 bd 非零腿的组合打分只由 bd 决定（其余腿零命中）⇒ 两窗
        F1~F4 全过、±50% 不翻转 ⇒ survivors 非空；pre2019 同分布 ⇒ 终审通过。
        退出码恒 0（证伪也是结论）；三窗并排段在有 search 参照时打印。
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(scbs, "POS_LEVELS", (0, 10))
        f1 = tmp_path / "score_s0_n400.rejudged.json"
        f2 = tmp_path / "score_s0_n1000_cw.rejudged.json"
        f3 = tmp_path / "score_s0_n1000_pre2019.rejudged.json"
        for f in (f1, f2, f3):
            self._write(f, _strong_trades())
        assert scbs.main(["--search", "--from-trades", str(f1), str(f2)]) == 0
        out = tmp_path / "artifacts/logs/score_combo_search_study/r30_search.json"
        assert out.exists()
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["windows"] == ["主窗", "跨窗"]
        assert rep["n_combos_raw"] == 62
        assert rep["n_combos_evaluated"] == 62  # {0,1} 向量 gcd 归一后仍互异
        assert rep["n_pass_f1_f4"] > 0
        assert len(rep["survivors"]) == min(3, rep["n_survivor_pool"])
        assert set(rep["survivors"]) <= {c["name"] for c in rep["passed"]}
        assert (
            "bd10" in rep["survivors"]
        )  # 网格序最前的过线组合（margin 全相同 ⇒ 稳定序）
        search_out = capsys.readouterr().out
        assert "搜索规模" in search_out and "survivors" in search_out

        assert scbs.main(["--final", "--from-trades", str(f3)]) == 0
        fout = (
            tmp_path / "artifacts/logs/score_combo_search_study/r30_final_pre2019.json"
        )
        assert fout.exists()
        frep = json.loads(fout.read_text(encoding="utf-8"))
        assert frep["verdict"] == "通过"
        assert frep["finalists"] == rep["survivors"]  # 终审名单 = 搜索落盘 survivors
        assert set(frep["passed"]) == set(rep["survivors"])
        for c in frep["candidates"].values():
            assert c["terminal_pass"] is True
        assert "三窗并排" in capsys.readouterr().out

    def test_search_records_ranking_key(self, tmp_path, monkeypatch):
        """排名键写死：margin_min = 两窗（篮子margin−全样本margin）较小值，降序。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(scbs, "POS_LEVELS", (0, 10))
        f1 = tmp_path / "score_s0_n400.rejudged.json"
        f2 = tmp_path / "score_s0_n1000_cw.rejudged.json"
        for f in (f1, f2):
            self._write(f, _strong_trades())
        assert scbs.main(["--search", "--from-trades", str(f1), str(f2)]) == 0
        rep = json.loads(
            (
                tmp_path / "artifacts/logs/score_combo_search_study/r30_search.json"
            ).read_text(encoding="utf-8")
        )
        keys = [c["margin_min_across_windows"] for c in rep["passed"]]
        assert all(k is not None for k in keys)
        # survivors 的键 = pool 里最大的 top 3
        by_name = {c["name"]: c for c in rep["passed"]}
        top_keys = sorted(keys, reverse=True)[: len(rep["survivors"])]
        assert sorted(
            by_name[n]["margin_min_across_windows"] for n in rep["survivors"]
        ) == sorted(top_keys)


# ---------------------------------------------------------------------------
# ⑧ fast path 对拍：与 svs.evaluate_variant 慢路径逐位等价（50 随机组合 + 半值权重）
# ---------------------------------------------------------------------------


class TestFastPathParity:
    def test_fast_matches_slow_50_random_combos(self):
        """同一批合成 trades，两条路径的产出逐位一致（搜索线 + R29 基线两套 floor）。"""
        trades = _random_trades()
        win = scbs.prepare_window(trades)
        grid = scbs.generate_grid()
        sample = random.Random(0).sample(grid, 50)
        for combo in sample:
            pw = combo["panel_weights"]
            fn = scs.make_candidate_score({}, pw)
            for floors in ((0.45, 2.6), (0.40, 2.4)):
                slow = scbs.eval_combo(trades, combo["name"], fn, *floors)
                fast = scbs.eval_combo_fast(win, combo["name"], pw, *floors)
                assert slow == fast, (combo["name"], floors)

    def test_fast_matches_slow_fractional_weights(self):
        """±50% 扰动后的半值权重（−2.5/5.0/7.5 等）两条路径同样逐位一致（rint=round）。"""
        trades = _random_trades()
        win = scbs.prepare_window(trades)
        nb = [c for c in scbs.generate_grid() if c["neg_block"]][:5]
        for combo in nb:
            pw = {
                k: (round(w * 0.5, 2) if w else 0)
                for k, w in combo["panel_weights"].items()
            }
            fn = scs.make_candidate_score({}, pw)
            slow = scbs.eval_combo(trades, combo["name"], fn)
            fast = scbs.eval_combo_fast(win, combo["name"], pw)
            assert slow == fast, combo["name"]

    def test_fast_matches_slow_with_nan_inf_rets(self):
        """rets 混入 NaN/±inf：corr/半窗与判定列（F1~F4/pass_all）两路径同口径。

        慢路径（pandas）成对剔除 NaN、±inf 保留（pearson 得 None、spearman 把 ±inf
        排在两端）——fast path 镜像同一语义（见 scbs._fast_corr）。不做全 dict 对拍：
        NaN 下篮子/分档统计两路径同为 nan 但 nan != nan，且 ret 降序选取的
        winner/bottom 分布（C2 参考列，不进判定）慢路径是 sorted 未定序。
        """
        trades = _random_trades()
        for i, v in (
            (5, float("nan")),
            (137, float("inf")),
            (250, float("-inf")),
            (411, float("nan")),
        ):
            trades[i]["ret"] = v
        win = scbs.prepare_window(trades)
        sample = random.Random(0).sample(scbs.generate_grid(), 50)
        for combo in sample:
            pw = combo["panel_weights"]
            fn = scs.make_candidate_score({}, pw)
            slow = scbs.eval_combo(trades, combo["name"], fn)
            fast = scbs.eval_combo_fast(win, combo["name"], pw)
            assert slow["corr"] == fast["corr"], combo["name"]
            assert slow["half_window"] == fast["half_window"], combo["name"]
            for k in ("F1", "F2", "F3", "F4"):
                assert slow[k]["pass"] == fast[k]["pass"], (combo["name"], k)
            assert slow["pass_all"] == fast["pass_all"], combo["name"]
