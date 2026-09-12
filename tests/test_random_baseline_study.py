# -*- coding: utf-8 -*-
"""random_baseline_study 钉测：合成 bars + 注入 loader 端到端（无网络无通达信）。

钉住：产物 schema / pass_count 与 per_expression 一致 / --llm-pass-rate 进
summary / 空数据拒跑不落盘 / judge 路径只读挖掘窗（篡改窗后数据结果不变）。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from custos.research import random_baseline_study as rbs

N_DAYS = 300
DATES = pd.date_range("2024-01-01", periods=N_DAYS, freq="B")
MINING_START = str(DATES[0].date())
MINING_END = str(DATES[199].date())

SUMMARY_KEYS = {
    "version",
    "tag",
    "seed",
    "n",
    "window",
    "horizon",
    "universe",
    "per_expression",
    "pass_count",
    "pass_rate",
    "llm_pass_rate_ref",
}
ROW_KEYS = {
    "expression",
    "decision",
    "reasons",
    "rank_ic_mean",
    "rank_icir",
    "n_days",
    "half_mean_1",
    "half_mean_2",
}


def make_bars(n_stocks: int = 10) -> dict[str, pd.DataFrame]:
    """合成宇宙：每股日漂移按代码序递增 + 确定性噪声（模运算，无 RNG）。"""
    out = {}
    for i in range(n_stocks):
        r = 0.002 + 0.001 * i
        price, closes = 100.0, []
        for t in range(N_DAYS):
            eps = 0.005 * (((i * 3 + t * 7) % 4) - 1.5)
            price *= (1 + r) * (1 + eps)
            closes.append(price)
        out[f"S{i:03d}"] = pd.DataFrame(
            {
                "date": DATES,
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1000.0 + 10.0 * i] * N_DAYS,
                "amount": [0.0] * N_DAYS,
            }
        )
    return out


def _loader(bars):
    def load(codes, count):
        return {c: df.copy() for c, df in bars.items() if c in set(codes)}

    return load


def _argv(tmp_path, codes_file, *extra):
    return [
        "--mining-start",
        MINING_START,
        "--mining-end",
        MINING_END,
        "--codes-file",
        str(codes_file),
        "--tag",
        "rb1",
        "--out-dir",
        str(tmp_path / "out"),
        *extra,
    ]


def _setup(tmp_path):
    bars = make_bars()
    codes_file = tmp_path / "codes.txt"
    codes_file.write_text("\n".join(sorted(bars)), encoding="utf-8")
    return bars, codes_file


def _summary(tmp_path, tag="rb1"):
    return json.loads(
        (tmp_path / "out" / tag / f"_random_baseline__{tag}.json").read_text("utf-8")
    )


class TestEndToEnd:
    def test_schema_and_consistency(self, tmp_path, capsys):
        bars, codes_file = _setup(tmp_path)
        rc = rbs.main(_argv(tmp_path, codes_file, "--n", "6"), loader=_loader(bars))
        assert rc == 0
        s = _summary(tmp_path)
        assert SUMMARY_KEYS <= set(s)  # schema 键钉住
        assert s["n"] == 6 and len(s["per_expression"]) == 6
        assert s["window"] == {"start": MINING_START, "end": MINING_END}
        assert s["universe"]["n_codes"] == 10 and s["universe"]["digest"]
        assert s["universe"]["source"].startswith("codes_file")
        n_pass = 0
        for row in s["per_expression"]:
            assert ROW_KEYS <= set(row)
            assert row["decision"] in ("pass", "fail")
            n_pass += row["decision"] == "pass"
        assert s["pass_count"] == n_pass  # pass_count 与 per_expression 一致
        assert s["pass_rate"] == pytest.approx(n_pass / 6)
        assert s["llm_pass_rate_ref"] is None
        out = capsys.readouterr().out
        assert "[rand]" in out and "过门率" in out

    def test_llm_pass_rate_ref_recorded(self, tmp_path, capsys):
        bars, codes_file = _setup(tmp_path)
        rc = rbs.main(
            _argv(tmp_path, codes_file, "--n", "4", "--llm-pass-rate", "0.67"),
            loader=_loader(bars),
        )
        assert rc == 0
        assert _summary(tmp_path)["llm_pass_rate_ref"] == 0.67
        assert "参考线" in capsys.readouterr().out

    def test_deterministic_same_seed(self, tmp_path):
        bars, codes_file = _setup(tmp_path)
        rbs.main(_argv(tmp_path, codes_file, "--n", "6"), loader=_loader(bars))
        a = [r["expression"] for r in _summary(tmp_path)["per_expression"]]
        rbs.main(_argv(tmp_path, codes_file, "--n", "6"), loader=_loader(bars))
        b = [r["expression"] for r in _summary(tmp_path)["per_expression"]]
        assert a == b  # 同种子同表达式序列（裁决实验可复现）

    def test_empty_data_refuses_no_artifacts(self, tmp_path):
        _bars, codes_file = _setup(tmp_path)
        rc = rbs.main(_argv(tmp_path, codes_file), loader=lambda codes, count: {})
        assert rc == 2
        assert not (tmp_path / "out").exists()  # 空结果不落盘

    def test_isolation_beyond_mining_end(self, tmp_path):
        """judge 路径只读挖掘窗：篡改 mining_end 之后的数据，逐表达式结果不变。"""
        bars, codes_file = _setup(tmp_path)
        rbs.main(_argv(tmp_path, codes_file, "--n", "6"), loader=_loader(bars))
        before = _summary(tmp_path)["per_expression"]

        tampered = {c: df.copy() for c, df in bars.items()}
        for df in tampered.values():
            after = pd.to_datetime(df["date"]) > pd.Timestamp(MINING_END)
            assert after.any()
            df.loc[after, "close"] = df.loc[after, "close"] * 100.0
            df.loc[after, "volume"] = -1.0
        rbs.main(_argv(tmp_path, codes_file, "--n", "6"), loader=_loader(tampered))
        after_rows = _summary(tmp_path)["per_expression"]
        assert json.dumps(before, sort_keys=True, allow_nan=True) == json.dumps(
            after_rows, sort_keys=True, allow_nan=True
        )


class TestValidation:
    def test_bad_window_rejected(self, tmp_path):
        bars, codes_file = _setup(tmp_path)
        with pytest.raises(SystemExit):
            rbs.main(
                _argv(tmp_path, codes_file, "--mining-end", "2020-01-01"),
                loader=_loader(bars),
            )  # 倒挂 → ap.error

    def test_n_must_be_positive(self, tmp_path):
        bars, codes_file = _setup(tmp_path)
        with pytest.raises(SystemExit):
            rbs.main(_argv(tmp_path, codes_file, "--n", "0"), loader=_loader(bars))


def test_registry_entry():
    from custos.research import __main__ as rm

    assert "random_baseline_study" in rm.TOOLS
    assert rm.ALIASES["random_baseline"] == "random_baseline_study"
    assert (rm.HERE / "random_baseline_study.py").exists()
