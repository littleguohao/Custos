# -*- coding: utf-8 -*-
"""`b1_fingerprint_study`（R18）——优秀 B1 指纹证据层回测脚本。

⚠️ 样本边界是这个研究单元的第一公民：正例精选、无负例 ⇒ 输出必须带 caveat，
脚本丢了这个声明等于把「召回率」读成「胜率」。合成 CSV 端到端 + 手算核对。
"""

from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest

from custos.research import b1_fingerprint_study as bf  # noqa: E402


def _synth_hit_df():
    """点火 → 回调缩量（J 落低位）→ 修复 的合成序列：b1_ignition 应命中。

    构造（实测调过）：45 根缓涨 → t=45 点火（+5%、3×量、收阳 open<close）
    → 10 根回调缩量（vol 400）→ 5 根修复。命中在回调后段（J<13 + 回调缩量 +
    点火在 10 日窗内）。
    """
    closes = [10 + i * 0.045 for i in range(45)]
    vols = [1000.0] * 45
    closes.append(11.98 * 1.05)
    vols.append(3000.0)  # t=45 点火
    for i in range(10):
        closes.append(12.58 - (i + 1) * 0.108)
        vols.append(400.0)
    for _ in range(5):
        closes.append(closes[-1] * 1.01)
        vols.append(800.0)
    opens = list(closes)
    opens[45] = 12.0  # 点火日必须收阳（open<close），否则 is_bull 不成立
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=n, freq="B"),
            "open": opens,
            "high": [max(o, c) * 1.005 for o, c in zip(opens, closes)],
            "low": [min(o, c) * 0.995 for o, c in zip(opens, closes)],
            "close": closes,
            "volume": vols,
            "amount": [0.0] * n,
        }
    )


def _write_csv(path: pathlib.Path, df: pd.DataFrame, code: str) -> None:
    """按 B1_DATA 真实形态写：首行带 BOM、大写列名、date 为字符串。"""
    out = df.rename(columns={c: c.capitalize() for c in df.columns}).copy()
    out["Code"] = code
    out["ForwardFactor"] = 0.0
    out["Amount"] = 0.0
    out["Date"] = df["date"].dt.strftime("%Y-%m-%d")
    out = out[
        [
            "Date",
            "Code",
            "Amount",
            "Close",
            "ForwardFactor",
            "High",
            "Low",
            "Volume",
            "Open",
        ]
    ]
    path.write_text(out.to_csv(index=False), encoding="utf-8-sig")


class TestLoadB1Csv:
    def test_bom_and_case_and_sort(self, tmp_path):
        df = _synth_hit_df()
        # 故意乱序 + BOM + 大写列名
        _write_csv(tmp_path / "600000.SH-x.csv", df.iloc[::-1], "600000.SH")
        got = bf.load_b1_csv(tmp_path / "600000.SH-x.csv")
        assert list(got.columns[:4]) == ["date", "code", "amount", "close"]
        assert str(got["date"].iloc[0]) < str(got["date"].iloc[-1]), "必须按日期升序"
        assert pd.api.types.is_datetime64_any_dtype(got["date"]), (
            "date 必须是 datetime（resample 周线聚合在字符串上直接 TypeError）"
        )


class TestScanCode:
    def test_composite_hit_and_forward_hand_calc(self):
        df = _synth_hit_df()
        r = bf.scan_code(df, "600000")
        assert r["n_hits"] >= 1, "合成序列应命中（点火+回调缩量+J 低同窗）"
        first = r["first_hit"]
        # 手算 fwd5：命中日收盘 vs 5 个交易日后收盘
        idx = int(df.index[df["date"].dt.strftime("%Y-%m-%d") == first["date"]][0])
        want = round(
            (float(df["close"].iloc[idx + 5]) / float(df["close"].iloc[idx]) - 1) * 100,
            2,
        )
        assert first["fwd"][5] == want
        # 驱动腿如实记录：点火 + 回调缩量 + J 低
        assert first["ignition"] and first["pullback_shrink"] and first["j_low"]

    def test_flat_series_never_hits(self):
        n = 60
        df = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=n, freq="B"),
                "open": [10.0] * n,
                "high": [10.05] * n,
                "low": [9.95] * n,
                "close": [10.0] * n,
                "volume": [1000.0] * n,
                "amount": [0.0] * n,
            }
        )
        r = bf.scan_code(df, "600000")
        assert r["n_hits"] == 0 and r["first_hit"] is None


class TestSummarize:
    def test_recall_and_pooled_fwd_hand_calc(self):
        hit = {"date": "2025-03-13", "fwd": {5: 4.0, 10: None, 20: None}}
        per = [
            {
                "code": "a",
                "bars": 60,
                "scanned_days": 40,
                "n_hits": 1,
                "first_hit": hit,
                "hit_days": [hit],
                "leg_hit_days": {},
                "fwd": {},
            },
            {
                "code": "b",
                "bars": 60,
                "scanned_days": 40,
                "n_hits": 0,
                "first_hit": None,
                "hit_days": [],
                "leg_hit_days": {},
                "fwd": {},
            },
        ]
        s = bf.summarize(per)
        assert s["n_codes"] == 2 and s["recall_codes"] == 1
        assert s["recall_rate"] == 50.0
        assert s["pooled_fwd"]["fwd5"] == {
            "n": 1,
            "mean": 4.0,
            "median": 4.0,
            "win_rate": 100.0,
        }
        assert "caveat" in s and "无负例" in s["caveat"], "样本边界声明不得丢"


class TestMainEndToEnd:
    def test_two_csvs(self, tmp_path, capsys):
        _write_csv(tmp_path / "600000.SH-a.csv", _synth_hit_df(), "600000.SH")
        flat = _synth_hit_df()
        flat["close"] = 10.0
        flat["open"] = 10.0
        flat["high"] = 10.05
        flat["low"] = 9.95
        _write_csv(tmp_path / "000001.SZ-b.csv", flat, "000001.SZ")
        out = tmp_path / "summary.json"
        rc = bf.main(["--b1-data-dir", str(tmp_path), "--out", str(out)])
        assert rc == 0
        d = json.loads(out.read_text(encoding="utf-8"))
        assert d["summary"]["n_codes"] == 2
        assert "无负例" in capsys.readouterr().out, "边界声明必须随输出"

    def test_empty_dir_fails_loudly(self, tmp_path):
        assert bf.main(["--b1-data-dir", str(tmp_path)]) == 2
