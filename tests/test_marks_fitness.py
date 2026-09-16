# -*- coding: utf-8 -*-
"""marks 分离适应度钉测（R36 Phase 2 监督模式）。

- marks_score：构造已知形态（买点放量 vs 缩量）对拍手算；n_hit/None 处理；
  K=250 在全案例恒 NaN 的反例钉（默认 rank_window=20 的存在理由）。
- loop 集成：ScriptedLLM 产同一表达式（IC 门同结果），marks 案例换向
  （买点大涨 vs 大跌）断言 decision 分流与 reasons/metrics["marks"]；
  --marks 关闭（marks_path=""）时行为逐位不变。
"""

import numpy as np
import pandas as pd
import pytest

from custos.research.evolution import loop as loop_mod
from custos.research.evolution.marks_fitness import (
    DEFAULT_RANK_WINDOW,
    marks_score,
)
from custos.research.evolution.trajectory import TrajectoryPool

from test_evolution_loop import ScriptedLLM, _cand, _cfg, make_bars  # noqa: E402


def _case(code: str, closes, vols, start="2025-06-02") -> "object":
    """构造 PerfectB1Case 同形对象（不引真类防循环：同名字段即可）。"""
    from custos.research.b1_perfect_dataset import PerfectB1Case

    n = len(closes)
    dates = pd.date_range(start, periods=n)
    df = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": np.asarray(closes) + 0.1,
            "low": np.asarray(closes) - 0.1,
            "close": closes,
            "volume": vols,
            "amount": np.asarray(vols) * 10.0,
        }
    )
    return PerfectB1Case(
        code=code,
        code_full=f"{code}.SZ",
        start=start,
        end=str(dates[-1].date()),
        buy_date=str(dates[-1].date()),
        bars=df,
        n_bars=n,
    )


def _write_case_csv(dir_path, code_full, closes, vols):
    """按 load_cases 的 CSV 形态落一个案例文件（末根日期=文件名末根）。"""
    n = len(closes)
    dates = pd.date_range("2025-06-02", periods=n)
    end = dates[-1].strftime("%Y%m%d")
    start = dates[0].strftime("%Y%m%d")
    df = pd.DataFrame(
        {
            "Date": [str(d.date()) for d in dates],
            "Code": code_full,
            "Amount": np.asarray(vols) * 10.0,
            "Close": closes,
            "ForwardFactor": 0.0,
            "High": np.asarray(closes) + 0.1,
            "Low": np.asarray(closes) - 0.1,
            "Volume": vols,
            "Open": closes,
        }
    )
    df.to_csv(dir_path / f"{code_full}-{start}-{end}.csv", index=False)


class TestMarksScore:
    def test_volume_surge_vs_shrink_hand_compute(self):
        """对拍手算：买点放量 → 高 rank；买点缩量 → 低 rank；contrast 同号。"""
        n = 80
        vol_low = [1e6] * n
        vol_low[-1] = 5e6  # 买点放量 5×
        vol_high = [1e6] * n
        vol_high[0] = 5e6  # 历史首日放量，买点平稳
        c1 = _case("600001", [10.0] * n, vol_low)
        c2 = _case("600002", [10.0] * n, vol_high)
        r = marks_score("volume/MA(volume,20)", [c1, c2], rank_window=20)
        assert r["n_hit"] == 2 and r["n_cases"] == 2
        by_code = {p["code"]: p for p in r["per_case"]}
        assert by_code["600001"]["rank"] == pytest.approx(0.975, abs=1e-9)
        assert by_code["600002"]["rank"] == pytest.approx(0.5, abs=1e-9)
        assert r["mean_rank"] == pytest.approx((0.975 + 0.5) / 2)
        assert r["min_rank"] == pytest.approx(0.5)
        assert r["contrast"] is not None and r["contrast"] > 0  # 放量案例抬高整体

    def test_buy_date_not_in_bars_is_no_value(self):
        c = _case("600001", [10.0] * 80, [1e6] * 80)
        c = c.__class__(  # buy_date 改成 bars 外的日期
            **{**c.__dict__, "buy_date": "2030-01-01"}
        )
        r = marks_score("close", [c], rank_window=20)
        assert r["n_hit"] == 0
        assert r["mean_rank"] is None and r["contrast"] is None
        assert r["per_case"][0]["status"] == "no_value"

    def test_k250_all_nan_counterexample(self):
        """反例钉：rank_window=250 在 ~70 根案例窗上 warmup 覆盖全窗 ⇒ 恒 NaN
        （默认 DEFAULT_RANK_WINDOW=20 的存在理由——窗口纪律写死在 docstring）。"""
        c = _case("600001", [10.0] * 70, [1e6] * 70)
        r = marks_score("volume/MA(volume,20)", [c], rank_window=250)
        assert r["n_hit"] == 0
        assert DEFAULT_RANK_WINDOW == 20

    def test_all_no_value_means_nulls_not_zero(self):
        """全部无值 → 汇总 None + n_hit=0（fail-closed 不许当高分）。"""
        c = _case("600001", [10.0] * 10, [1e6] * 10)  # 10 根 < warmup
        r = marks_score("volume/MA(volume,20)", [c], rank_window=20)
        assert r["n_hit"] == 0
        assert (
            r["mean_rank"] is None and r["min_rank"] is None and r["contrast"] is None
        )


class TestLoopMarksGate:
    def _run(self, tmp_path, *, spike: float, marks_path=""):
        """单候选跑循环：案例买点涨 spike（负=跌）；返回 pool 唯一轨迹。"""
        n = 80
        closes = [100.0] * n
        closes[-1] = closes[-2] * (1 + spike)
        vols = [1e6] * n
        if marks_path:
            _write_case_csv(tmp_path, "600001.SZ", closes, vols)
        llm = ScriptedLLM([_cand("ROC(CLOSE,5)", "动量5日")])
        pool = TrajectoryPool()
        loop_mod.run_loop(
            _cfg(marks_path=marks_path, marks_rank_window=20),
            make_bars(),
            llm,
            pool,
        )
        (t,) = pool.all()
        return t

    def test_marks_pass_records_metrics(self, tmp_path):
        """买点大涨案例：IC 门 + marks 门全过 ⇒ pass，mining_metrics 带 marks 块。"""
        t = self._run(tmp_path, spike=0.20, marks_path=str(tmp_path))
        assert t.decision == "pass"
        m = t.mining_metrics["marks"]
        assert m["n_hit"] == 1
        assert m["mean_rank"] >= 0.7
        assert m["contrast"] > 0
        assert m["per_case"][0]["status"] == "hit"

    def test_marks_fail_with_reasons(self, tmp_path):
        """买点大跌案例：IC 门过但 mean_rank < 0.7 ⇒ fail，reasons 含 marks 原因。"""
        t = self._run(tmp_path, spike=-0.20, marks_path=str(tmp_path))
        assert t.decision == "fail"
        m = t.mining_metrics["marks"]
        assert m["n_hit"] == 1 and m["mean_rank"] < 0.7
        assert "mean_rank" in t.feedback
        # IC 读数仍在（marks 门不覆盖 IC 门读数，rank_ic_mean 还在）
        assert isinstance(t.mining_metrics["rank_ic_mean"], float)

    def test_marks_off_bitwise_unchanged(self, tmp_path):
        """--marks 关闭（marks_path=""）：行为逐位不变（同输入同 decision/metrics）。"""
        closes = [100.0] * 80
        closes[-1] = closes[-2] * 0.8
        _write_case_csv(tmp_path, "600001.SZ", closes, [1e6] * 80)

        def run(mp):
            llm = ScriptedLLM([_cand("ROC(CLOSE,5)", "动量5日")])
            pool = TrajectoryPool()
            loop_mod.run_loop(
                _cfg(marks_path=mp, marks_rank_window=20), make_bars(), llm, pool
            )
            (t,) = pool.all()
            return t

        t_off = run("")
        t_default = run("")  # 与 marks_path 缺省一致（LoopConfig.marks_path 默认 ""）
        assert t_off.decision == t_default.decision == "pass"
        assert "marks" not in t_off.mining_metrics
        assert t_off.mining_metrics == t_default.mining_metrics
