"""选股练习台账（TODO #101）：台账写入纪律 + T+1 开盘入场 + B1 现行出场 + 统计口径。"""

import json

import pandas as pd
import pytest

from custos.research import pick_practice as pp


def _df(closes, opens=None, start="2026-09-01"):
    dates = pd.bdate_range(start, periods=len(closes))
    opens = opens or closes
    return pd.DataFrame(
        {
            "date": [d.strftime("%Y-%m-%d") for d in dates],
            "open": opens,
            "high": [max(o, c) * 1.001 for o, c in zip(opens, closes)],
            "low": [min(o, c) * 0.999 for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1e6] * len(closes),
        }
    )


class TestLedger:
    def test_append_idempotent_and_validated(self, tmp_path):
        p = tmp_path / "pp.jsonl"
        assert pp.append_picks("2026-10-15", ["600000", "000001"], path=p) == [
            "600000",
            "000001",
        ]
        assert pp.append_picks("2026-10-15", ["600000", "300750"], path=p) == ["300750"]
        assert len(pp.load_picks(p)) == 3
        with pytest.raises(ValueError):
            pp.append_picks("2026-10-15", ["60000"], path=p)
        with pytest.raises(ValueError):
            pp.append_picks("20261015", ["600000"], path=p)

    def test_daily_snapshot_before_first_write(self, tmp_path):
        p = tmp_path / "pp.jsonl"
        pp.append_picks("2026-10-14", ["600000"], path=p)
        pp.append_picks("2026-10-15", ["600001"], path=p)
        baks = list(tmp_path.glob("pp.jsonl.bak_*"))
        assert len(baks) == 1 and "600000" in baks[0].read_text(encoding="utf-8")

    def test_default_path_is_redirected_in_tests(self, tmp_path):
        # conftest 改道：缺省路径落在 tmp，不碰真实 data/trades
        assert "pick_practice.jsonl" in str(pp.PICK_PRACTICE_LEDGER)
        assert str(pp.PICK_PRACTICE_LEDGER).startswith(str(tmp_path))


class TestSimulate:
    def test_pending_without_next_bar(self):
        df = _df([10.0] * 40)
        assert pp.simulate_pick(df, df["date"].iloc[-1])["status"] == "pending"

    def test_entry_at_next_open_and_stop_on_close(self):
        closes = [10.0] * 31 + [10.2, 8.8] + [8.8] * 5
        opens = [10.0] * 31 + [10.0, 9.5] + [8.8] * 5
        df = _df(closes, opens)
        day = df["date"].iloc[30]  # 选股日 T
        out = pp.simulate_pick(df, day)
        assert out["entry_date"] == df["date"].iloc[31]
        assert out["entry_price"] == 10.0 and out["stop"] == pytest.approx(9.0)
        assert out["status"] == "closed" and out["reason"].startswith("stop")
        # 收盘触发、收盘成交：8.8/开盘 10.0 − 1 − 25bps（不是相对 T+1 收盘 10.2）
        assert out["ret"] == pytest.approx(8.8 / 10.0 - 1 - 0.0025)

    def test_open_position_not_counted(self):
        df = _df([10.0] * 31 + [10.1, 10.2, 10.25])
        out = pp.simulate_pick(df, df["date"].iloc[30])
        assert out["status"] == "open" and "ret" not in out

    def test_entry_day_crash_exits_next_open(self):
        closes = [10.0] * 31 + [8.5, 8.6] + [8.6] * 3
        opens = [10.0] * 31 + [10.0, 8.4] + [8.6] * 3
        df = _df(closes, opens)
        out = pp.simulate_pick(df, df["date"].iloc[30])
        assert out["status"] == "closed" and out["holding"] == 1
        assert out["ret"] == pytest.approx(8.4 / 10.0 - 1 - 0.0025)


class TestReport:
    def test_stats_hand_checked(self, monkeypatch):
        fake = {
            "A": {"status": "closed", "ret": 0.10, "holding": 5},
            "B": {"status": "closed", "ret": -0.05, "holding": 3},
            "C": {"status": "closed", "ret": 0.06, "holding": 4},
            "D": {"status": "open", "mark_ret": 0.02},
            "E": {"status": "pending"},
        }
        monkeypatch.setattr(pp, "simulate_pick", lambda df, day: dict(fake[df]))
        picks = [{"date": "2026-10-15", "code": c} for c in "ABCDE"]
        rep = pp.build_report(picks, loader=lambda c: c)
        o = rep["overall"]
        assert o["n"] == 3 and o["win_rate"] == pytest.approx(2 / 3)
        assert o["payoff_ratio"] == pytest.approx(0.08 / 0.05)
        assert o["avg_ret"] == pytest.approx(0.11 / 3) and o["avg_holding"] == 4
        assert [r["code"] for r in rep["open"]] == ["D"]
        assert [r["code"] for r in rep["pending"]] == ["E"]
        assert set(rep["by_month"]) == {"2026-10"}

    def test_cli_add_then_report(self, tmp_path, capsys):
        assert pp.main(["add", "600000", "--date", "2026-09-30", "--note", "测试"]) == 0
        df = _df([10.0] * 31 + [10.2, 8.8] + [8.8] * 5)
        day = df["date"].iloc[30]
        assert pp.main(["add", "000001", "--date", day]) == 0
        out = tmp_path / "rep.json"
        rc = pp.main(["report", "--out", str(out)], loader=lambda c: df)
        assert rc == 0
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["overall"]["n"] >= 1
        assert "选股练习" in capsys.readouterr().out

    def test_report_empty_ledger_exits_2(self):
        assert pp.main(["report"]) == 2


def test_pick_before_data_window_is_no_data():
    df = _df([10.0] * 60, start="2025-01-02")
    assert pp.simulate_pick(df, "2020-06-01")["status"] == "no_data"
