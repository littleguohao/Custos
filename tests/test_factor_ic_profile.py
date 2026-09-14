# -*- coding: utf-8 -*-
"""factor_ic_profile 钉测：合成注入（无网络无通达信）。

- scorer_score_series：fake scorer（monkeypatch SCORERS 注入）逐日值对拍手算；
  None/异常/inf→NaN；预计算旁路被调用（spy）且结果与直算一致。
- 端到端（注入 loader，合成 8 股）：「score 与前向收益完全正相关」的构造
  → 主 horizon rank_ic_mean≈1.0；常数 scorer → n_days=0 照实记录；
  多 horizon 键齐全；schema 钉住；半窗字段在。
"""

import json
import math

import numpy as np
import pandas as pd
import pytest

from custos.research import backtest_factors as bt
from custos.research import factor_ic_profile as fip

CODES = [f"c{i}" for i in range(8)]  # 8 股 ≥ MIN_STOCKS(5)
START, END = "2024-01-01", "2024-03-31"


def _bars(slope: float, n: int = 60, start: str = "2023-11-01") -> pd.DataFrame:
    """线性价格路径：close = 10 + slope·t（斜率即每日收益，跨股可比）。"""
    dates = pd.date_range(start, periods=n, freq="B")
    close = 10.0 + slope * np.arange(n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.full(n, 1e6),
        }
    )


def _load_all(n: int = 60, start: str = "2023-11-01") -> dict[str, pd.DataFrame]:
    """8 股不同斜率：close 排序 = 斜率排序 = 前向收益排序（rank_ic≈1 的构造）。"""
    return {
        code: _bars(slope=0.01 * (k + 1), n=n, start=start)
        for k, code in enumerate(CODES)
    }


def _loader(bars):
    return lambda codes, count: {c: bars[c] for c in codes if c in bars}


# ---------------------------------------------------------------------------
# scorer_score_series
# ---------------------------------------------------------------------------


class TestScorerScoreSeries:
    def test_daily_values_match_hand_compute(self, monkeypatch):
        """逐日值 = 逐前缀切片调 scorer 的手算结果。"""
        monkeypatch.setitem(
            bt.SCORERS,
            "fake_close",
            lambda df, code: {"score": float(df["close"].iloc[-1])},
        )
        df = _bars(0.05, n=30)
        s = fip.scorer_score_series("fake_close", df, "c0")
        assert len(s) == 30
        expected = df["close"].to_numpy()
        np.testing.assert_allclose(s.to_numpy(), expected, rtol=1e-12)
        assert str(s.index[0].date()) == str(df["date"].iloc[0].date())

    def test_none_exception_inf_become_nan(self, monkeypatch):
        """None / 异常 / NaN / ±inf → NaN；绝不 raise。"""
        seq = {"i": 0}

        def flaky(df, code):
            seq["i"] += 1
            k = seq["i"]
            if k % 4 == 1:
                return None  # 不参与
            if k % 4 == 2:
                raise RuntimeError("boom")  # 异常
            if k % 4 == 3:
                return {"score": float("inf")}  # 非有限
            return {"score": 1.0}

        monkeypatch.setitem(bt.SCORERS, "flaky", flaky)
        s = fip.scorer_score_series("flaky", _bars(0.01, n=8), "c0")
        vals = s.to_numpy()
        assert np.isnan(vals[0]) and np.isnan(vals[1]) and np.isnan(vals[2])
        assert vals[3] == 1.0
        assert np.isnan(vals[4]) and np.isnan(vals[5]) and np.isnan(vals[6])

    def test_unknown_key_rejected(self):
        with pytest.raises(ValueError, match="未注册"):
            fip.scorer_score_series("no_such_scorer", _bars(0.01, n=5), "c0")

    def test_precompute_bypass_called_and_equal(self, monkeypatch):
        """预计算旁路：spy 断言每股只调一次；三参点查结果与两参直算逐位一致。"""
        calls = {"n": 0}

        def fake_scorer(df, code, pre=None):
            if pre is not None:  # 旁路：O(1) 点查
                return {"score": float(pre["close_series"][len(df) - 1])}
            return {"score": float(df["close"].iloc[-1])}  # 直算

        def fake_precompute(df):
            calls["n"] += 1
            return {"close_series": df["close"].to_numpy(dtype=float)}

        monkeypatch.setitem(bt.SCORERS, "fake_pre", fake_scorer)
        monkeypatch.setitem(bt._SCORER_PRECOMPUTE, fake_scorer, fake_precompute)
        df = _bars(0.03, n=25)
        s = fip.scorer_score_series("fake_pre", df, "c0")
        assert calls["n"] == 1, "预计算必须每股只调一次（v0.212 旁路口径）"
        # 与无旁路直算逐位一致
        monkeypatch.delitem(bt._SCORER_PRECOMPUTE, fake_scorer)
        s_direct = fip.scorer_score_series("fake_pre", df, "c0")
        np.testing.assert_array_equal(s.to_numpy(), s_direct.to_numpy())

    def test_precompute_exception_falls_back(self, monkeypatch):
        """旁路预计算抛异常 → 回退逐切片直算（引擎同语义），结果不受影响。"""
        monkeypatch.setitem(
            bt.SCORERS,
            "fake_fb",
            lambda df, code, pre=None: {"score": float(df["close"].iloc[-1])},
        )

        def bad_precompute(df):
            raise RuntimeError("precompute boom")

        monkeypatch.setitem(
            bt._SCORER_PRECOMPUTE, bt.SCORERS["fake_fb"], bad_precompute
        )
        df = _bars(0.02, n=10)
        s = fip.scorer_score_series("fake_fb", df, "c0")
        np.testing.assert_allclose(s.to_numpy(), df["close"].to_numpy(), rtol=1e-12)


# ---------------------------------------------------------------------------
# 端到端（注入 loader）
# ---------------------------------------------------------------------------


def _argv(tmp_path, *extra, codes=None):
    return [
        "--start",
        START,
        "--end",
        END,
        "--codes",
        codes or ",".join(CODES),
        "--out-dir",
        str(tmp_path),
        "--tag",
        "t1",
    ] + list(extra)


def _run(tmp_path, bars, *extra, codes=None):
    rc = fip.main(_argv(tmp_path, *extra, codes=codes), loader=_loader(bars))
    assert rc == 0
    out = tmp_path / "t1" / "_factor_ic_profile__t1.json"
    assert out.exists()
    return json.loads(out.read_text(encoding="utf-8"))


class TestEndToEnd:
    def test_schema_and_perfect_factor_expr(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bt, "SCORERS", {})  # 只看 expr 路（默认全部 19 键留空集）
        rep = _run(tmp_path, _load_all(), "--expr", "close")
        assert set(rep) == {
            "version",
            "tag",
            "window",
            "horizons",
            "primary_horizon",
            "universe",
            "factors",
            "ranking",
        }
        assert rep["window"] == {"start": START, "end": END}
        assert rep["horizons"] == [1, 5, 10, 20]
        assert rep["primary_horizon"] == 5
        assert rep["universe"]["n_codes"] == len(CODES)
        assert len(rep["factors"]) == 1
        f = rep["factors"][0]
        assert f["name"] == "close" and f["kind"] == "expr"
        assert set(f["per_horizon"]) == {"1", "5", "10", "20"}
        cell = f["per_horizon"]["5"]
        assert set(cell) == {
            "rank_ic_mean",
            "rank_icir",
            "ic_mean",
            "icir",
            "n_days",
            "half1",
            "half2",
        }
        # close 排序 = 斜率排序 = 前向收益排序 ⇒ 逐日 Spearman ≈ 1
        assert cell["rank_ic_mean"] == pytest.approx(1.0, abs=1e-3)
        # 逐日 IC 恒 1.0（零方差）⇒ ICIR 无定义，照实 nan（ic_eval._series_stats 口径：
        # std≈0 → icir=nan；零方差 IC 不可信与 judge_mining 的哲学一致）
        assert math.isnan(cell["rank_icir"])
        assert cell["n_days"] > 0
        assert cell["half1"] == pytest.approx(1.0, abs=1e-3)
        assert cell["half2"] == pytest.approx(1.0, abs=1e-3)
        # ranking：主 horizon 的 rank_icir 降序（单因子也在）
        assert rep["ranking"][0]["name"] == "close"
        assert rep["ranking"][0]["primary_rank_ic_mean"] == pytest.approx(1.0, abs=1e-3)

    def test_constant_factor_n_days_zero(self, tmp_path, monkeypatch):
        """常数因子：逐日零方差 ⇒ 全部跳过 ⇒ n_days=0 照实记录不崩。"""
        monkeypatch.setattr(bt, "SCORERS", {})
        rep = _run(tmp_path, _load_all(), "--expr", "1")
        f = rep["factors"][0]
        for h in ("1", "5", "10", "20"):
            assert f["per_horizon"][h]["n_days"] == 0
            assert math.isnan(f["per_horizon"][h]["rank_ic_mean"])
        # 常数因子 ICIR NaN → ranking 垫底语义（不抛错）
        assert rep["ranking"][0]["primary_rank_icir"] is None or math.isnan(
            rep["ranking"][0]["primary_rank_icir"] or float("nan")
        )

    def test_mixed_scorers_and_expr(self, tmp_path, monkeypatch):
        """--scorers 与 --expr 混用：kind 分列；fake scorer 与 expr close 同 IC。"""
        monkeypatch.setitem(
            bt.SCORERS,
            "fake_close_mix",
            lambda df, code: {"score": float(df["close"].iloc[-1])},
        )
        rep = _run(
            tmp_path,
            _load_all(),
            "--scorers",
            "fake_close_mix",
            "--expr",
            "close",
        )
        kinds = {f["name"]: f["kind"] for f in rep["factors"]}
        assert kinds == {"fake_close_mix": "scorer", "close": "expr"}
        ic_scorer = rep["factors"][0]["per_horizon"]["5"]["rank_ic_mean"]
        ic_expr = rep["factors"][1]["per_horizon"]["5"]["rank_ic_mean"]
        assert ic_scorer == pytest.approx(ic_expr, abs=1e-9)  # 同分值 ⇒ 同 IC

    def test_ranking_order_by_rank_icir(self, tmp_path, monkeypatch):
        """排序按主 horizon rank_icir 降序（有限 ICIR 因子居首；零方差 IC ⇒ NaN 垫底）。

        构造：c0 的 volume 按日奇偶跳变（偶日巨量 ⇒ expr `close+volume` 把 c0
        顶到截面第一，而它的前向收益斜率最小 ⇒ 该日 IC 掉档）——IC 逐日交替
        ⇒ std>0 ⇒ ICIR 有限；`close` 逐日 IC 恒 1.0（零方差 ⇒ ICIR NaN）垫底，
        这是「排序主键取 ICIR」的既定口径（零方差 IC 不可信）。
        """
        monkeypatch.setattr(bt, "SCORERS", {})
        bars = _load_all()
        n = len(bars["c0"])
        bars["c0"]["volume"] = [
            1e18 if i % 2 == 0 else 0.0 for i in range(n)
        ]  # 偶日巨量 ⇒ close+volume 奇偶交替失准
        rep = _run(
            tmp_path,
            bars,
            "--expr",
            "close+volume",
            "--expr",
            "close",
            "--expr",
            "1",
        )
        names = [r["name"] for r in rep["ranking"]]
        assert names[0] == "close+volume"  # 有限正 ICIR 居首
        assert rep["ranking"][0]["primary_rank_icir"] > 0
        # close（IC 恒 1，零方差）与常数因子 ICIR 均 NaN ⇒ 排在有限 ICIR 之后
        assert set(names[1:]) == {"close", "1"}

    def test_primary_horizon_must_be_in_horizons(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            fip.main(
                _argv(tmp_path, "--horizons", "1,5", "--primary-horizon", "20"),
                loader=_loader(_load_all()),
            )
        assert exc.value.code == 2

    def test_unknown_scorer_key_rejected(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            fip.main(
                _argv(tmp_path, "--scorers", "no_such"), loader=_loader(_load_all())
            )
        assert exc.value.code == 2

    def test_empty_universe_guard_no_dump(self, tmp_path):
        """空数据护栏：loader 返回空 → 非零退出不落盘。"""
        rc = fip.main(_argv(tmp_path), loader=lambda codes, count: {})
        assert rc == 2
        assert not (tmp_path / "t1" / "_factor_ic_profile__t1.json").exists()

    def test_empty_window_guard_no_dump(self, tmp_path):
        """窗口内无 K 线（窗口与数据错位）→ 非零退出不落盘。"""
        rc = fip.main(
            _argv(tmp_path, "--start", "2030-01-01", "--end", "2030-02-01"),
            loader=_loader(_load_all()),
        )
        assert rc == 2
        assert not (tmp_path / "t1" / "_factor_ic_profile__t1.json").exists()

    def test_start_end_required(self, tmp_path):
        """--start/--end 必填（argparse required）。"""
        with pytest.raises(SystemExit) as exc:
            fip.main(
                ["--codes", "c0,c1", "--out-dir", str(tmp_path)],
                loader=_loader(_load_all()),
            )
        assert exc.value.code == 2

    def test_horizon_tail_excluded(self, tmp_path, monkeypatch):
        """前向收益越界剔除：60 根窗口内 H=20 的有效日 < H=1 的有效日。"""
        monkeypatch.setattr(bt, "SCORERS", {})
        rep = _run(tmp_path, _load_all(n=40, start="2024-01-02"), "--expr", "close")
        f = rep["factors"][0]
        assert f["per_horizon"]["20"]["n_days"] < f["per_horizon"]["1"]["n_days"]
