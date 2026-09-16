# -*- coding: utf-8 -*-
"""b1_marks_v0_study 钉测：fake v0_scorer/bars_loader/index_loader 端到端
（无网络无通达信）。分位对拍手算、三态（hit/unavailable/no_universe）、
汇总计数、schema 钉住、空有效集拒跑、stdout 纪律表头。
"""

import json

import numpy as np
import pandas as pd
import pytest

from custos.research import b1_marks_v0_study as st

START, END = "2025-06-02", "2025-08-29"
CODES = ["600001", "600002", "600003", "600004", "600005"]
BUY_DATE = "2025-08-20"  # _bars 末根（2025-06-02+79 日）


def _bars(code: str, n: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2025-06-02", periods=n)
    close = 10.0 + (int(code[-1]) * 0.5) * np.arange(n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.full(n, 1e6),
            "amount": close * 1e6,
        }
    )


_BARS = {c: _bars(c) for c in CODES}


def _bars_loader(code, count, start, end):
    df = _BARS.get(code)
    if df is None:
        return None
    out = df[df["date"].astype(str).str[:10] <= (end or "9999")] if end else df
    return (
        out.tail(count).reset_index(drop=True) if count else out.reset_index(drop=True)
    )


def _index_loader():
    return _bars("600999")  # 指数帧（形态满足即可）


def _v0_factory(table: dict):
    """table[(code, date)] -> (score, level, contrib) 的 fake v0_scorer。"""

    def scorer(df, index_df, i, code):
        date = str(df["date"].iloc[i].date())
        v = table.get((code, date))
        if v is None:
            return None, "弱", {}
        return v, "强", {"j_low": 24.0, "macd": 7.0}

    return scorer


def _marks_json(tmp_path, marks):
    p = tmp_path / "marks.json"
    p.write_text(json.dumps(marks), encoding="utf-8")
    return str(p)


def _argv(tmp_path, mj, *extra):
    return [
        "--marks",
        mj,
        "--codes",
        ",".join(CODES),
        "--out-dir",
        str(tmp_path),
        "--tag",
        "t1",
    ] + list(extra)


def _run(tmp_path, mj, scorer):
    rc = st.main(
        _argv(tmp_path, mj),
        v0_scorer=scorer,
        bars_loader=_bars_loader,
        index_loader=_index_loader,
    )
    assert rc == 0
    return json.loads(
        (tmp_path / "t1" / "_b1_marks_v0__t1.json").read_text(encoding="utf-8")
    )


class TestEndToEnd:
    def test_schema_and_percentile_hand_compute(self, tmp_path):
        """分位对拍手算：案例股 v0=80，宇宙 {10,20,60,80,90} → ≤80 = 4/5=0.8。"""
        table = {("600001", BUY_DATE): 80.0}
        for u, v in zip(CODES[1:], (10.0, 20.0, 60.0, 90.0)):
            table[(u, BUY_DATE)] = v
        table[(CODES[0], BUY_DATE)] = 80.0  # 案例股自身也在宇宙（600001 ∈ CODES）
        # 宇宙 = {600001:80（案例）, 600002:10, 600003:20, 600004:60, 600005:90}
        # ≤80 = {80,10,20,60} = 4/5 = 0.8（90 不计；并列按 ≤ 计）
        mj = _marks_json(tmp_path, [{"code": "600001", "buy_date": BUY_DATE}])
        rep = _run(tmp_path, mj, _v0_factory(table))
        assert set(rep) == {
            "version",
            "tag",
            "source",
            "universe",
            "cases",
            "summary",
            "discipline_note",
        }
        assert "诊断指标非判据" in rep["discipline_note"]
        (c,) = rep["cases"]
        assert set(c) == {
            "code",
            "buy_date",
            "v0_score",
            "percentile",
            "status",
            "contrib",
        }
        assert c["status"] == "hit"
        assert c["v0_score"] == 80.0
        assert c["percentile"] == pytest.approx(0.8)
        assert c["contrib"] == {"j_low": 24.0, "macd": 7.0}  # 每条腿 as-of 明细
        s = rep["summary"]
        assert s["mean"] == pytest.approx(0.8)
        assert s["median"] == pytest.approx(0.8)
        assert s["ge_0_8"] == 1 and s["ge_0_9"] == 0
        assert s["n_hit"] == 1 and s["n_cases"] == 1

    def test_case_unavailable(self, tmp_path):
        """案例股当日不可评（scorer 返 None / 当日无 bar）→ unavailable 不硬算。"""
        table = {("600002", BUY_DATE): 50.0}  # 案例股 600001 不在表里（scorer 返 None）
        mj = _marks_json(tmp_path, [{"code": "600001", "buy_date": BUY_DATE}])
        rc = st.main(
            _argv(tmp_path, mj),
            v0_scorer=_v0_factory(table),
            bars_loader=_bars_loader,
            index_loader=_index_loader,
        )
        assert rc == 2  # 案例全灭 → 0 hit → 空结果护栏
        assert not (tmp_path / "t1" / "_b1_marks_v0__t1.json").exists()

    def test_no_universe_status(self, tmp_path):
        """案例可评但宇宙 0 有效 → no_universe（照实记录不静默跳过）。

        注意口径：宇宙含案例股自身时，宇宙至少含案例值 ⇒ no_universe 只在
        **案例股不在宇宙**且宇宙全灭时发生（本测试案例码 600099 ∉ CODES）。
        """
        # 案例股 600099 不在 CODES；宇宙码全部返 None（scorer 查表无值）
        table = {("600099", BUY_DATE): 55.0}

        def loader(code, count, start, end):
            if code == "600099":
                return _bars(code)
            return _bars_loader(code, count, start, end)

        mj = _marks_json(tmp_path, [{"code": "600099", "buy_date": BUY_DATE}])
        rc = st.main(
            _argv(tmp_path, mj),
            v0_scorer=_v0_factory(table),
            bars_loader=loader,
            index_loader=_index_loader,
        )
        assert rc == 2  # 宇宙 0 有效 → no_universe → 0 hit → 空结果护栏

    def test_multi_case_mixed_status(self, tmp_path):
        """多案例混合：hit / no_universe（宇宙空）共存时汇总只对 hit 计。"""
        table = {
            ("600001", BUY_DATE): 90.0,
            ("600002", BUY_DATE): 10.0,
            ("600003", BUY_DATE): 30.0,
            ("600004", BUY_DATE): 70.0,
            ("600005", BUY_DATE): 95.0,
        }
        d2 = "2025-08-19"  # 第二案例日（须在 bars 内）
        table2 = {("600099", d2): 50.0}  # 第二案例：案例股 600099 ∉ CODES 有值
        table.update(table2)
        mj = _marks_json(
            tmp_path,
            [
                {"code": "600001", "buy_date": BUY_DATE},
                {"code": "600099", "buy_date": d2},
            ],
        )

        def loader(code, count, start, end):
            if code == "600099":
                return _bars(code)
            df = _BARS.get(code)
            if df is None:
                return None
            out = df[df["date"].astype(str).str[:10] <= (end or "9999")]
            return out.reset_index(drop=True) if len(out) else None

        # 第二案例日 d2：宇宙码（∈CODES）全返 None → 宇宙 0 有效 → no_universe
        def scorer(df, index_df, i, code):
            date = str(df["date"].iloc[i].date())
            v = table.get((code, date))
            if v is None:
                return None, "弱", {}
            return v, "强", {}

        rc = st.main(
            _argv(tmp_path, mj),
            v0_scorer=scorer,
            bars_loader=loader,
            index_loader=_index_loader,
        )
        assert rc == 0
        rep = json.loads(
            (tmp_path / "t1" / "_b1_marks_v0__t1.json").read_text(encoding="utf-8")
        )
        c1, c2 = rep["cases"]
        # 案例1：宇宙 {90,10,30,70,95} ≤90 = 4/5
        assert c1["status"] == "hit" and c1["percentile"] == pytest.approx(0.8)
        assert c2["status"] == "no_universe" and c2["percentile"] is None
        assert rep["summary"]["n_hit"] == 1
        assert rep["summary"]["mean"] == pytest.approx(0.8)

    def test_stdout_discipline_header(self, tmp_path, capsys):
        table = {("600001", BUY_DATE): 80.0, ("600002", BUY_DATE): 50.0}
        mj = _marks_json(tmp_path, [{"code": "600001", "buy_date": BUY_DATE}])
        _run(tmp_path, mj, _v0_factory(table))
        out = capsys.readouterr().out
        assert "诊断指标非判据" in out
        assert "V0 技术分" in out
        assert "汇总：mean=" in out

    def test_empty_marks_rejected(self, tmp_path):
        mj = _marks_json(tmp_path, [])
        with pytest.raises(SystemExit) as exc:
            st.main(
                _argv(tmp_path, mj),
                v0_scorer=_v0_factory({}),
                bars_loader=_bars_loader,
                index_loader=_index_loader,
            )
        assert exc.value.code == 2
