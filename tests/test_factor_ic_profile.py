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


# ---------------------------------------------------------------------------
# --marks 打点（R36 Phase 1：正例买点在全宇宙当日分值序列上的分位）
# ---------------------------------------------------------------------------


def _marks_json(tmp_path, marks):
    import json as _json

    p = tmp_path / "marks.json"
    p.write_text(_json.dumps(marks), encoding="utf-8")
    return str(p)


def _write_b1_case_csv(dir_, code_full, dates):
    """合成 B1_DATA 形态 CSV：BOM + 大写列名 + {code}-{start}-{end}.csv 文件名口径。

    日期轴直接取宇宙 bars 的 date 列 ⇒ 买点=末根=文件名 end（load_cases 装载
    护栏），且打点必落在 frame 索引上（status=hit 可断言）。
    """
    lines = ["Date,Code,Amount,Close,ForwardFactor,High,Low,Volume,Open"]
    for i, d in enumerate(dates):
        c = 10.0 + 0.1 * i
        lines.append(
            f"{d.date()},{code_full},{c * 1000:.2f},{c},0.0,{c * 1.01:.4f},"
            f"{c * 0.99:.4f},1000,{c * 0.998:.4f}"
        )
    start = pd.Timestamp(dates.iloc[0]).strftime("%Y%m%d")
    end = pd.Timestamp(dates.iloc[-1]).strftime("%Y%m%d")
    p = dir_ / f"{code_full}-{start}-{end}.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return p


class TestMarkPercentile:
    def test_percentile_hand_compute(self):
        """分位对拍手算：当日全宇宙有效分值 ≤ 案例股分值的比例，并列按 ≤ 计。"""
        import numpy as np

        vals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        assert fip._mark_percentile(vals, 3.0) == pytest.approx(0.6)  # 1,2,3 ≤ 3
        assert fip._mark_percentile(vals, 5.0) == pytest.approx(1.0)
        assert fip._mark_percentile(vals, 0.5) == pytest.approx(0.0)

    def test_nan_and_inf_excluded_both_sides(self):
        """NaN/±inf 双侧剔除（案例侧与宇宙侧都不进分母分子）。"""
        import numpy as np

        vals = np.array([1.0, 2.0, np.nan, 4.0, np.inf, -np.inf])
        # 有效宇宙 = {1,2,4}（NaN/±inf 剔除）；案例值 2.0 → 2/3
        assert fip._mark_percentile(vals, 2.0) == pytest.approx(2 / 3)

    def test_tie_counts_in_le(self):
        """并列处理口径写死：并列全算在 ≤ 侧（与 TS_RANK 平局各让一半不同）。"""
        import numpy as np

        vals = np.array([1.0, 2.0, 2.0, 2.0, 5.0])
        assert fip._mark_percentile(vals, 2.0) == pytest.approx(0.8)  # 4/5 含全部并列

    def test_empty_valid_returns_nan(self):
        import numpy as np

        assert np.isnan(fip._mark_percentile(np.array([np.nan, np.inf]), 1.0))


class TestMarksEndToEnd:
    def _setup_bars(self):
        bars = _load_all()
        # 两个 fake 案例股：600001 斜率最大（close 恒截面第一），600002 与 c2 同斜率（并列）
        bars["600001"] = _bars(0.09)
        bars["600002"] = _bars(0.02)
        return bars

    def _argv_marks(self, tmp_path, bars, mj, *extra):
        return _argv(
            tmp_path,
            "--expr",
            "close",
            "--codes",
            ",".join(bars.keys()),
            "--marks",
            mj,
            *extra,
        )

    def test_schema_and_percentiles(self, tmp_path, monkeypatch):
        """端到端：分位对拍（首=1.00/并列=按 ≤ 计/o=不在宇宙）；schema 钉住。"""
        monkeypatch.setattr(bt, "SCORERS", {})
        bars = self._setup_bars()
        buy_date = str(bars["600001"]["date"].iloc[-1].date())
        mj = _marks_json(
            tmp_path,
            [
                {"code": "600001", "buy_date": buy_date},
                {"code": "600002", "buy_date": buy_date},
                {"code": "600099", "buy_date": buy_date},
            ],
        )
        rc = fip.main(self._argv_marks(tmp_path, bars, mj), loader=_loader(bars))
        assert rc == 0
        rep = json.loads(
            (tmp_path / "t1" / "_factor_ic_profile__t1.json").read_text(
                encoding="utf-8"
            )
        )
        assert set(rep["marks"]) == {"source", "universe_note", "per_factor"}
        assert "八段" in rep["marks"]["universe_note"]  # 映射口径必须在报告里
        blk = rep["marks"]["per_factor"]["close"]
        assert set(blk) == {"points", "mean", "median", "ge_0_8", "ge_0_9"}
        p0, p1, p2 = blk["points"]
        assert set(p0) == {"code", "buy_date", "value", "percentile", "status"}
        # 600001 斜率最大 ⇒ 分位 1.0（hit）
        assert p0["status"] == "hit" and p0["percentile"] == pytest.approx(1.0)
        # 600002 与 c2 同斜率并列 ⇒ ≤ 计：c1,c2,600002 = 3/10 = 0.3
        assert p1["status"] == "hit" and p1["percentile"] == pytest.approx(0.3)
        # 600099 不在宇宙 ⇒ out_of_universe
        assert p2["status"] == "out_of_universe" and p2["percentile"] is None
        assert blk["ge_0_8"] == 1 and blk["ge_0_9"] == 1
        assert blk["mean"] == pytest.approx((1.0 + 0.3) / 2)
        assert blk["median"] == pytest.approx((1.0 + 0.3) / 2)

    def test_case_scorer_none_becomes_unavailable(self, tmp_path, monkeypatch):
        """案例股当日 scorer 返 None/NaN → unavailable（不硬算）。"""
        # fake scorer：600001 恒 None
        monkeypatch.setitem(
            bt.SCORERS,
            "fake_none_for_case",
            lambda df, code: None if code == "600001" else {"score": 1.0},
        )
        bars = self._setup_bars()
        buy_date = str(bars["600001"]["date"].iloc[-1].date())
        mj = _marks_json(tmp_path, [{"code": "600001", "buy_date": buy_date}])
        rc = fip.main(
            _argv(
                tmp_path,
                "--scorers",
                "fake_none_for_case",
                "--codes",
                ",".join(bars.keys()),
                "--marks",
                mj,
            ),
            loader=_loader(bars),
        )
        assert rc == 0
        rep = json.loads(
            (tmp_path / "t1" / "_factor_ic_profile__t1.json").read_text(
                encoding="utf-8"
            )
        )
        p = rep["marks"]["per_factor"]["fake_none_for_case"]["points"][0]
        assert p["status"] == "unavailable" and p["percentile"] is None
        assert rep["marks"]["per_factor"]["fake_none_for_case"]["ge_0_8"] == 0

    def test_stdout_table_has_discipline_header(self, tmp_path, monkeypatch, capsys):
        """stdout 表渲染含纪律表头（诊断指标非判据）与状态字符。"""
        monkeypatch.setattr(bt, "SCORERS", {})
        bars = self._setup_bars()
        buy_date = str(bars["600001"]["date"].iloc[-1].date())
        mj = _marks_json(
            tmp_path,
            [
                {"code": "600001", "buy_date": buy_date},
                {"code": "600099", "buy_date": buy_date},
            ],
        )
        fip.main(self._argv_marks(tmp_path, bars, mj), loader=_loader(bars))
        out = capsys.readouterr().out
        assert "诊断指标非判据" in out
        assert "R36" in out
        assert "out_of_universe" in out or " o " in out

    def test_marks_dir_loads_b1_dataset(self, tmp_path, monkeypatch):
        """--marks 指向目录 → 走 b1_perfect_dataset.load_cases 加载全部正例。

        合成目录（hermetic）：真实 B1_DATA 只在 dev 机，CI/其他环境无此路径。
        """
        monkeypatch.setattr(bt, "SCORERS", {})
        bars = self._setup_bars()
        marks_dir = tmp_path / "b1_data_fake"
        marks_dir.mkdir()
        for full in ("600001.SH", "600002.SH"):
            _write_b1_case_csv(marks_dir, full, bars[full[:6]]["date"])
        rc = fip.main(
            _argv(
                tmp_path,
                "--expr",
                "close",
                "--codes",
                ",".join(bars.keys()),
                "--marks",
                str(marks_dir),
            ),
            loader=_loader(bars),
        )
        assert rc == 0
        rep = json.loads(
            (tmp_path / "t1" / "_factor_ic_profile__t1.json").read_text(
                encoding="utf-8"
            )
        )
        assert rep["marks"]["source"].startswith("b1_data_dir(")
        pts = rep["marks"]["per_factor"]["close"]["points"]
        assert [p["code"] for p in pts] == ["600001", "600002"]  # 目录分支全量装载
        assert all(p["status"] == "hit" for p in pts)  # 日期轴对齐 ⇒ 全 hit

    def test_empty_marks_dir_rejected(self, tmp_path, monkeypatch):
        """空 marks 目录/路径不存在 → fail-closed（ap.error，exit 2）。"""
        monkeypatch.setattr(bt, "SCORERS", {})
        empty = tmp_path / "empty_marks"
        empty.mkdir()
        with pytest.raises(SystemExit) as exc:
            fip.main(
                _argv(tmp_path, "--expr", "close", "--marks", str(empty)),
                loader=_loader(_load_all()),
            )
        assert exc.value.code == 2
        with pytest.raises(SystemExit) as exc2:
            fip.main(
                _argv(tmp_path, "--expr", "close", "--marks", str(tmp_path / "nope")),
                loader=_loader(_load_all()),
            )
        assert exc2.value.code == 2

    def test_marks_coexists_with_scorers_and_expr(self, tmp_path):
        """--marks 与 --scorers/--expr 共存（互不冲突，IC 主表照出）。"""
        bars = self._setup_bars()
        buy_date = str(bars["600001"]["date"].iloc[-1].date())
        mj = _marks_json(tmp_path, [{"code": "600001", "buy_date": buy_date}])
        rc = fip.main(
            _argv(
                tmp_path,
                "--scorers",
                "kdj_j",
                "--expr",
                "close",
                "--codes",
                ",".join(bars.keys()),
                "--marks",
                mj,
            ),
            loader=_loader(bars),
        )
        assert rc == 0
        rep = json.loads(
            (tmp_path / "t1" / "_factor_ic_profile__t1.json").read_text(
                encoding="utf-8"
            )
        )
        assert "kdj_j" in rep["marks"]["per_factor"]
        assert "close" in rep["marks"]["per_factor"]
        assert rep["ranking"]  # 主 ranking 表不变（照出）


class TestMarksEnvelope:
    """权威清单信封形态（{version,note,marks}，R36_perfect_b1_marks.json）与
    裸 list 同效——v0.244 库内 JSON 入库后生产机直传该文件（此前两工具内联
    解析器只认裸 list，生产机 2026-09-16 实测拒跑）。"""

    def test_envelope_accepted(self, tmp_path):
        import argparse

        env = tmp_path / "marks_env.json"
        env.write_text(
            json.dumps(
                {
                    "version": 1,
                    "note": "x",
                    "marks": [
                        {"code": "600000", "buy_date": "2025-07-10"},
                        {"code": "600001", "buy_date": "2025-08-01"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ns = argparse.Namespace(marks=str(env))
        marks, source = fip._load_marks(ns, fip._build_parser())
        assert [m["code"] for m in marks] == ["600000", "600001"]
        assert source.startswith("marks_json(")

    def test_authoritative_repo_json_loads(self):
        """库内权威清单本体过解析（10 点，code/buy_date 形态）。"""
        import argparse
        from pathlib import Path

        auth = (
            Path(__file__).resolve().parents[1]
            / "governance"
            / "research"
            / "R36_perfect_b1_marks.json"
        )
        ns = argparse.Namespace(marks=str(auth))
        marks, _ = fip._load_marks(ns, fip._build_parser())
        assert len(marks) == 10
        assert all(isinstance(m["code"], str) and m["buy_date"] for m in marks)
