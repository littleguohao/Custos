# -*- coding: utf-8 -*-
"""fetch_market_cap 测试:股本变动事件表 + as-of 查询 + 市值计算。

核心不变量:
  1. 只在**观测到股本变化**时写事件(未变不写),否则日频 1100 万行存不下;
  2. `shares_as_of` 只取 `observed_on <= day` ⇒ 采样间隔内的变动只会**延后**生效,
     方向是 stale 而非 look-ahead;
  3. 市值必须用**查询日**收盘价 × 股本,不能直接用事件里采样日的 market_cap;
  4. 早于 MV_START(2018-01-02)一律无数据,窗口护栏据此剔除。
"""
from __future__ import annotations

import json

import fetch_market_cap as mc


def _row(code="600000", shares=1e10, close=10.0, name="测试股", free=None):
    mv = None if shares is None else shares * close
    return {"SECURITY_CODE": code, "SECURITY_NAME_ABBR": name,
            "TRADE_DATE": "2024-06-28 00:00:00", "CLOSE_PRICE": close,
            "TOTAL_SHARES": shares,
            "FREE_SHARES_A": free if free is not None else (
                None if shares is None else shares * 0.8),
            "TOTAL_MARKET_CAP": mv,
            "NOTLIMITED_MARKETCAP_A": None if mv is None else mv * 0.8}


class TestDiffEvents:
    def test_first_seen_recorded(self):
        evs = mc.diff_events({}, [_row()], "2024-06-28", None)
        assert len(evs) == 1
        assert evs[0]["kind"] == "first_seen" and evs[0]["prev_shares"] is None
        assert evs[0]["total_shares"] == 1e10

    def test_unchanged_shares_not_written(self):
        """核心:未变化不写,否则日频全市场会写出 1100 万行。"""
        evs = mc.diff_events({"600000": 1e10}, [_row(shares=1e10)], "2024-07-28", "2024-06-28")
        assert evs == []

    def test_change_recorded_with_interval(self):
        """事件必须同时带 prev_sample 与 observed_on,界定真实变动的时间区间。"""
        evs = mc.diff_events({"600000": 1e10}, [_row(shares=1.2e10)],
                             "2024-07-28", "2024-06-28")
        assert len(evs) == 1
        e = evs[0]
        assert e["kind"] == "change" and e["prev_shares"] == 1e10
        assert e["total_shares"] == 1.2e10
        assert e["prev_sample"] == "2024-06-28" and e["observed_on"] == "2024-07-28"

    def test_tiny_float_noise_not_treated_as_change(self):
        evs = mc.diff_events({"600000": 1e10}, [_row(shares=1e10 + 1e-9)],
                             "2024-07-28", "2024-06-28")
        assert evs == []

    def test_invalid_rows_skipped(self):
        rows = [_row(code=""), _row(code="000001", shares=None), _row(code="300001", shares=0),
                _row(code="600519", shares=1.2e9)]
        evs = mc.diff_events({}, rows, "2024-06-28", None)
        assert [e["code"] for e in evs] == ["600519"]

    def test_multiple_codes_independent(self):
        prev = {"600000": 1e10, "000001": 2e10}
        rows = [_row(code="600000", shares=1e10), _row(code="000001", shares=2.5e10)]
        evs = mc.diff_events(prev, rows, "2024-07-28", "2024-06-28")
        assert [e["code"] for e in evs] == ["000001"]


class TestSharesAsOf:
    def _events(self):
        return [
            {"code": "600000", "observed_on": "2018-01-31", "prev_sample": None,
             "total_shares": 1e10, "kind": "first_seen"},
            {"code": "600000", "observed_on": "2020-06-28", "prev_sample": "2020-05-28",
             "total_shares": 1.5e10, "kind": "change"},
            {"code": "000001", "observed_on": "2019-03-28", "prev_sample": "2019-02-28",
             "total_shares": 2e10, "kind": "first_seen"},
        ]

    def test_picks_latest_observed_before_day(self):
        got = mc.shares_as_of(self._events(), "2021-01-01", code="600000")
        assert got["600000"]["total_shares"] == 1.5e10

    def test_change_not_visible_before_observation(self):
        """核心方向性:采样间隔内变动只会延后生效 ⇒ 返回旧股本(stale),绝不提前(look-ahead)。"""
        got = mc.shares_as_of(self._events(), "2020-06-01", code="600000")
        assert got["600000"]["total_shares"] == 1e10          # 变动 6-28 才观测到
        assert got["600000"]["observed_on"] == "2018-01-31"

    def test_observation_day_inclusive(self):
        got = mc.shares_as_of(self._events(), "2020-06-28", code="600000")
        assert got["600000"]["total_shares"] == 1.5e10

    def test_nothing_before_first_observation(self):
        assert mc.shares_as_of(self._events(), "2017-12-01") == {}

    def test_multiple_codes_returned(self):
        got = mc.shares_as_of(self._events(), "2019-06-01")
        assert set(got) == {"600000", "000001"}


class TestMarketCap:
    def _events(self):
        return [{"code": "600000", "observed_on": "2024-06-28", "prev_sample": "2024-05-28",
                 "total_shares": 2.9352e10, "close": 8.23,
                 "market_cap": 2.9352e10 * 8.23, "kind": "change"}]

    def test_uses_query_day_close_not_sample_day(self):
        """必须用查询日收盘价:股本可跨日沿用,价格不行。"""
        got = mc.market_cap(self._events(), "2024-07-15", {"600000": 9.00})
        assert got["600000"]["close"] == 9.00
        assert abs(got["600000"]["market_cap"] - 2.9352e10 * 9.00) < 1
        # 不等于事件里采样日的市值
        assert abs(got["600000"]["market_cap"] - self._events()[0]["market_cap"]) > 1e9

    def test_carries_resolution_metadata(self):
        got = mc.market_cap(self._events(), "2024-07-15", {"600000": 9.0})
        assert got["600000"]["shares_observed_on"] == "2024-06-28"
        assert got["600000"]["shares_prev_sample"] == "2024-05-28"

    def test_missing_close_skipped(self):
        assert mc.market_cap(self._events(), "2024-07-15", {}) == {}

    def test_real_world_consistency(self):
        """实测锚点:浦发 293.52 亿股 × 8.23 = 2416 亿(接口自洽性已核验)。"""
        got = mc.market_cap(self._events(), "2024-06-28", {"600000": 8.23})
        assert abs(got["600000"]["market_cap"] / 1e8 - 2416) < 2


class TestMvStartGuard:
    def test_before_mv_start(self):
        assert mc.before_mv_start("2017-12-29") is True
        assert mc.before_mv_start("2018-01-02") is False
        assert mc.before_mv_start("2015-06-30") is True

    def test_sample_dates_clamped_to_mv_start(self):
        """--since 2015 也不该产出 2018 之前的采样日(那些日期数据源为空)。"""
        got = mc.sample_dates(2015, freq="month", until="2018-06-30")
        assert got and all(d >= mc.MV_START for d in got)
        assert got[0].startswith("2018-01")

    def test_first_month_sample_aligned_to_mv_start(self):
        """月采样首点必须对齐 MV_START:否则 2018-01-02~27 护栏放行但 shares 全空。"""
        got = mc.sample_dates(2018, freq="month", until="2018-03-01")
        assert got == ["2018-01-02", "2018-01-28", "2018-02-28"]
        assert got[0] == mc.MV_START

    def test_first_sample_alignment_leaves_later_years_untouched(self):
        """对齐只作用于被 MV_START 截断的起点;正常年份首点仍是 1 月 28 日。"""
        got = mc.sample_dates(2019, freq="month", until="2019-03-01")
        assert got == ["2019-01-28", "2019-02-28"]

    def test_sample_dates_month_uses_28th(self):
        got = mc.sample_dates(2024, freq="month", until="2024-04-01")
        assert got == ["2024-01-28", "2024-02-28", "2024-03-28"]

    def test_sample_dates_week_are_mondays(self):
        import datetime as dt
        got = mc.sample_dates(2024, freq="week", until="2024-02-01")
        assert all(dt.date.fromisoformat(d).weekday() == 0 for d in got)

    def test_sample_dates_day_skips_weekends(self):
        import datetime as dt
        got = mc.sample_dates(2024, freq="day", until="2024-01-15")
        assert all(dt.date.fromisoformat(d).weekday() < 5 for d in got)


class TestLedgerIO:
    def test_merge_dedups_on_code_and_observed_on(self, tmp_path):
        p = tmp_path / "sc.jsonl"
        e = {"code": "600000", "observed_on": "2024-06-28", "total_shares": 1e10,
             "kind": "first_seen"}
        assert mc.merge_write([e], p)["added"] == 1
        assert mc.merge_write([dict(e)], p)["added"] == 0
        assert mc.merge_write([{**e, "observed_on": "2024-07-28",
                                "total_shares": 1.2e10}], p)["added"] == 1

    def test_round_trip_and_atomic(self, tmp_path):
        p = tmp_path / "sc.jsonl"
        mc.merge_write([{"code": "600000", "observed_on": "2024-06-28",
                         "total_shares": 1e10}], p)
        assert len(mc.load_events(p)) == 1 and not list(tmp_path.glob("*.tmp"))

    def test_corrupt_lines_skipped(self, tmp_path):
        p = tmp_path / "sc.jsonl"
        p.write_text('{"code":"600000","observed_on":"2024-06-28","total_shares":1}\n'
                     "{bad\n\n", encoding="utf-8")
        assert len(mc.load_events(p)) == 1

    def test_missing_ledger_empty(self, tmp_path):
        assert mc.load_events(tmp_path / "nope.jsonl") == []


class TestVerify:
    def test_flags_events_before_mv_start(self):
        rep = mc.verify([{"code": "600000", "observed_on": "2017-06-30",
                          "total_shares": 1e10, "kind": "first_seen"}], [])
        assert rep["ok"] is False and rep["n_before_mv_start"] == 1
        assert "早于 MV_START" in rep["text"]

    def test_ok_when_all_within_range(self):
        rep = mc.verify([{"code": "600000", "observed_on": "2018-01-31",
                          "total_shares": 1e10, "kind": "first_seen"},
                         {"code": "600000", "observed_on": "2020-06-28",
                          "total_shares": 1.5e10, "kind": "change"}],
                        ["2018-01-31", "2020-06-28"])
        assert rep["ok"] is True and rep["n_changes"] == 1 and rep["n_first_seen"] == 1
        assert "stale 而非 look-ahead" in rep["text"]


class TestCli:
    def test_as_of_rejects_dates_before_mv_start(self, tmp_path, capsys):
        p = tmp_path / "sc.jsonl"
        p.write_text(json.dumps({"code": "600000", "observed_on": "2018-01-31",
                                 "total_shares": 1e10}) + "\n", encoding="utf-8")
        rc = mc.main(["--as-of", "2015-06-30", "--out", str(p)])
        assert rc == 2 and "早于市值数据起点" in capsys.readouterr().err

    def test_as_of_prints_shares(self, tmp_path, capsys):
        p = tmp_path / "sc.jsonl"
        p.write_text(json.dumps({"code": "600000", "name": "浦发银行",
                                 "observed_on": "2024-06-28", "prev_sample": "2024-05-28",
                                 "total_shares": 2.9352e10}, ensure_ascii=False) + "\n",
                     encoding="utf-8")
        rc = mc.main(["--as-of", "2024-07-15", "--out", str(p)])
        out = capsys.readouterr().out
        assert rc == 0 and "293.52亿股" in out

    def test_verify_requires_ledger(self, tmp_path, capsys):
        rc = mc.main(["--verify", "--out", str(tmp_path / "nope.jsonl")])
        assert rc == 2 and "台账为空" in capsys.readouterr().err

    def test_rejects_dates_before_mv_start_on_fetch(self, capsys):
        try:
            mc.main(["--dates", "2015-06-30"])
        except SystemExit as exc:
            assert exc.code == 2
        assert "2018-01-02" in capsys.readouterr().err


class TestSamplingRun:
    """采样主循环:乱序补采拒绝、已知空日期不重打(monkeypatch 掉网络层)。"""

    def _setup_ledger(self, tmp_path):
        ledger = tmp_path / "sc.jsonl"
        ledger.write_text(json.dumps({"code": "600000", "observed_on": "2024-06-28",
                                      "prev_sample": None, "total_shares": 1e10,
                                      "kind": "first_seen"}) + "\n", encoding="utf-8")
        samples = tmp_path / "samples.json"
        samples.write_text(json.dumps({"sampled": ["2024-06-28"], "empty": []}),
                           encoding="utf-8")
        return ledger, samples

    def _fake_fetch(self, counter, rows):
        def fake(d, session=None, **_):
            counter.append(d)
            return rows
        return fake

    def test_out_of_order_backfill_rejected(self, tmp_path, capsys, monkeypatch):
        """早于已采样末日的补采必须拒绝:prev 会是台账最终股本,diff 基准与元数据都会错。"""
        ledger, samples = self._setup_ledger(tmp_path)
        calls = []
        monkeypatch.setattr(mc, "fetch_trade_date", self._fake_fetch(calls, [_row(shares=1.2e10)]))
        rc = mc.main(["--dates", "2024-05-28", "--out", str(ledger),
                      "--samples-out", str(samples)])
        err = capsys.readouterr().err
        assert rc == 0 and calls == []                       # 根本没发请求
        assert "拒绝乱序补采" in err and "重放" in err
        assert len(mc.load_events(ledger)) == 1              # 台账未被污染
        assert json.loads(samples.read_text(encoding="utf-8"))["sampled"] == ["2024-06-28"]

    def test_in_order_new_sample_still_works(self, tmp_path, capsys, monkeypatch):
        """时间序前进的补采不受影响:prev 用上一采样日的股本。"""
        ledger, samples = self._setup_ledger(tmp_path)
        calls = []
        monkeypatch.setattr(mc, "fetch_trade_date", self._fake_fetch(calls, [_row(shares=1.2e10)]))
        rc = mc.main(["--dates", "2024-07-28", "--out", str(ledger),
                      "--samples-out", str(samples)])
        assert rc == 0 and calls == ["2024-07-28"]
        evs = mc.load_events(ledger)
        assert len(evs) == 2
        new = [e for e in evs if e["observed_on"] == "2024-07-28"][0]
        assert new["prev_shares"] == 1e10 and new["prev_sample"] == "2024-06-28"
        assert new["kind"] == "change"

    def test_empty_date_recorded_and_not_refetched(self, tmp_path, capsys, monkeypatch):
        """非交易日空转:无数据日期记入 empty,重跑不再重复请求。"""
        ledger = tmp_path / "sc.jsonl"
        samples = tmp_path / "samples.json"
        calls = []
        monkeypatch.setattr(mc, "fetch_trade_date", self._fake_fetch(calls, []))
        argv = ["--dates", "2024-06-29", "--out", str(ledger),
                "--samples-out", str(samples)]
        assert mc.main(argv) == 0
        assert calls == ["2024-06-29"]
        saved = json.loads(samples.read_text(encoding="utf-8"))
        assert saved["empty"] == ["2024-06-29"] and saved["sampled"] == []
        assert "已知空日期" in capsys.readouterr().out
        # 重跑:已知空日期直接跳过,不再发请求
        assert mc.main(argv) == 0 and calls == ["2024-06-29"]


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _FakeSession:
    """模拟东财 F10 股本接口:2 页历史 + 分页终止。"""

    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, timeout=15, headers=None):
        self.calls += 1
        page = params["pageNumber"]
        data = {1: [{"END_DATE": "2015-03-31 00:00:00", "TOTAL_SHARES": 1e8,
                     "CHANGE_REASON": "转增股上市", "SECURITY_NAME_ABBR": "测试股"},
                    {"END_DATE": "2016-11-20 00:00:00", "TOTAL_SHARES": 1.5e8,
                     "CHANGE_REASON": "增发A股上市", "SECURITY_NAME_ABBR": "测试股"}],
                2: [{"END_DATE": "2018-06-30 00:00:00", "TOTAL_SHARES": 2e8,
                     "CHANGE_REASON": "转增股上市", "SECURITY_NAME_ABBR": "测试股"}]}[page]
        return _FakeResp({"result": {"pages": 2, "data": data}})


def test_fetch_equity_history_paginates_and_parses():
    rows = mc.fetch_equity_history("600000", session=_FakeSession())
    assert len(rows) == 3 and rows[1]["observed_on"] == "2016-11-20"
    assert rows[1]["kind"] == "增发A股上市"          # 定增必须在内(送转因子表缺它)


def test_backfill_only_before_mv_start_and_merges(tmp_path):
    out = tmp_path / "ledger.jsonl"
    res = mc.backfill_equity_history(["600000"], before=mc.MV_START, out_path=out,
                                     progress=0, session=_FakeSession())
    # 2018-06-30 事件在 MV_START 之后 → 不回填;2015/2016 两条进来
    assert res["added"] == 2
    events = mc.load_events(out)
    assert [e["observed_on"] for e in events] == ["2015-03-31", "2016-11-20"]
    sh = mc.shares_as_of(events, "2016-05-01", code="600000")["600000"]
    assert sh["total_shares"] == 1e8                 # as-of 取 2015 事件(stale 安全侧)
    sh2 = mc.shares_as_of(events, "2017-01-01", code="600000")["600000"]
    assert sh2["total_shares"] == 1.5e8              # 定增后股本
