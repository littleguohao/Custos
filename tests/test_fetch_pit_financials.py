# -*- coding: utf-8 -*-
"""fetch_pit_financials 测试:PIT 可见日判定是回测正确性的地基,必须钉死。

核心不变量:
  1. 可见日 = 公告日的**次一日**(公告多在盘后/晚间发布,当日不算可见);
  2. 公告日不晚于报告期的行必须丢弃(现实不可能,一条错的可见日就是 look-ahead);
  3. 非 A 股(新三板/B股/CDR)默认剔除 —— 实测某报告期 11514 行里新三板占 5890;
  4. 同比字段(YSTZ/SJLTZ)不得入库 —— 次年同期报告发布时会被重算,不是 PIT 值。
"""
from __future__ import annotations

import json

import fetch_pit_financials as fp


def _row(code="600000", report="2024-03-31", notice="2024-04-29", stype="A股", **kw):
    r = {"SECURITY_CODE": code, "SECURITY_NAME_ABBR": "测试股", "SECURITY_TYPE": stype,
         "REPORTDATE": f"{report} 00:00:00", "NOTICE_DATE": f"{notice} 00:00:00",
         "EITIME": f"{notice} 20:16:21", "UPDATE_DATE": "2026-07-31 00:00:00",
         "TRADE_MARKET": "上交所主板", "PUBLISHNAME": "银行",
         "BASIC_EPS": 0.5, "TOTAL_OPERATE_INCOME": 1.0e9, "PARENT_NETPROFIT": 1.0e8,
         "WEIGHTAVG_ROE": 3.2, "BPS": 15.0, "MGJYXJJE": 1.1, "XSMLL": 40.0,
         "YSTZ": 12.3, "SJLTZ": 45.6}
    r.update(kw)
    return r


class TestNormalize:
    def test_a_share_kept_with_lag(self):
        recs, st = fp.normalize([_row()], "2024-03-31")
        assert st["kept"] == 1
        r = recs[0]
        assert r["notice_date"] == "2024-04-29" and r["lag_days"] == 29
        assert r["eps"] == 0.5 and r["net_profit"] == 1.0e8

    def test_non_ashare_dropped_by_default(self):
        """新三板不过滤就会灌进宇宙(实测 2025-12-31 有 5890 只)。"""
        rows = [_row(code="830001", stype="三板股"), _row(code="200001", stype="B股"),
                _row(code="600000", stype="A股")]
        recs, st = fp.normalize(rows, "2024-03-31")
        assert [r["code"] for r in recs] == ["600000"] and st["dropped_type"] == 2

    def test_non_ashare_kept_when_asked(self):
        rows = [_row(code="830001", stype="三板股"), _row(code="600000")]
        recs, _ = fp.normalize(rows, "2024-03-31", a_share_only=False)
        assert len(recs) == 2

    def test_notice_not_after_report_date_dropped(self):
        """公告日 = 报告期或更早 = 不可能,必须丢弃而不是当成 0 天滞后。"""
        rows = [_row(notice="2024-03-31"), _row(code="600001", notice="2024-03-01")]
        recs, st = fp.normalize(rows, "2024-03-31")
        assert recs == [] and st["dropped_bad_lag"] == 2

    def test_missing_notice_dropped(self):
        rows = [_row(NOTICE_DATE=None), _row(code="", notice="2024-04-29")]
        recs, st = fp.normalize(rows, "2024-03-31")
        assert recs == [] and st["dropped_no_notice"] == 2

    def test_yoy_fields_never_stored(self):
        """同比字段会被次年同期报告重算(实测重述比例 100% vs 次年未到时 3.2%),不得入库。"""
        recs, _ = fp.normalize([_row()], "2024-03-31")
        keys = set(recs[0])
        for banned in fp._REFUSED_FIELDS:
            assert banned not in keys
        assert not any(k in keys for k in ("revenue_yoy", "net_profit_yoy"))

    def test_none_values_preserved_as_none(self):
        recs, _ = fp.normalize([_row(BASIC_EPS=None)], "2024-03-31")
        assert recs[0]["eps"] is None and recs[0]["bps"] == 15.0


class TestAsOf:
    def _recs(self):
        return [
            {"code": "600000", "name": "A", "report_date": "2024-03-31",
             "notice_date": "2024-04-29", "lag_days": 29, "eps": 0.5},
            {"code": "600000", "name": "A", "report_date": "2024-06-30",
             "notice_date": "2024-08-28", "lag_days": 59, "eps": 1.1},
            {"code": "000001", "name": "B", "report_date": "2024-03-31",
             "notice_date": "2024-04-20", "lag_days": 20, "eps": 0.3},
        ]

    def test_announcement_day_not_visible_by_default(self):
        """核心不变量:公告当日不可见(实测 EITIME 多为公告日前一晚 20 点,属盘后披露)。"""
        assert fp.as_of(self._recs(), "2024-04-29", code="600000") == {}
        got = fp.as_of(self._recs(), "2024-04-30", code="600000")
        assert got["600000"]["report_date"] == "2024-03-31"

    def test_same_day_visible_when_opted_in(self):
        got = fp.as_of(self._recs(), "2024-04-29", code="600000", visible_next_day=False)
        assert got["600000"]["notice_date"] == "2024-04-29"

    def test_picks_latest_visible_period_not_latest_absolute(self):
        """2024-08-01 时中报(8/28 公告)尚不可见,只能拿到一季报 —— 这正是 PIT 的意义。"""
        got = fp.as_of(self._recs(), "2024-08-01", code="600000")
        assert got["600000"]["report_date"] == "2024-03-31" and got["600000"]["eps"] == 0.5
        later = fp.as_of(self._recs(), "2024-08-29", code="600000")
        assert later["600000"]["report_date"] == "2024-06-30" and later["600000"]["eps"] == 1.1

    def test_multiple_codes_independent_visibility(self):
        """000001 于 4/20 公告、600000 于 4/29,4/25 时只有前者可见。"""
        got = fp.as_of(self._recs(), "2024-04-25")
        assert set(got) == {"000001"}

    def test_restatement_version_picked_by_notice_date(self):
        """同报告期多版本:取已可见的最新公告版本,重现"当时看到的是哪一版"。"""
        recs = self._recs() + [{"code": "600000", "name": "A", "report_date": "2024-03-31",
                                "notice_date": "2024-06-15", "lag_days": 76, "eps": 0.42}]
        early = fp.as_of(recs, "2024-05-06", code="600000")
        assert early["600000"]["eps"] == 0.5            # 更正版还没出
        after = fp.as_of(recs, "2024-06-16", code="600000")
        assert after["600000"]["eps"] == 0.42           # 更正版已可见

    def test_nothing_visible_before_any_announcement(self):
        assert fp.as_of(self._recs(), "2024-04-01") == {}


class TestLedgerIO:
    def test_merge_dedups_on_triple_key_and_keeps_versions(self, tmp_path):
        p = tmp_path / "pit.jsonl"
        base = {"code": "600000", "report_date": "2024-03-31", "notice_date": "2024-04-29",
                "eps": 0.5}
        r1 = fp.merge_write([base], p)
        assert r1["added"] == 1
        # 同三元组重复写 → 不新增
        r2 = fp.merge_write([dict(base)], p)
        assert r2["added"] == 0 and r2["after"] == 1
        # 同报告期新公告日 = 更正版 → 必须新增(不能覆盖原版,否则 as-of 无法回溯)
        r3 = fp.merge_write([{**base, "notice_date": "2024-06-15", "eps": 0.42}], p)
        assert r3["added"] == 1 and r3["after"] == 2

    def test_round_trip_load(self, tmp_path):
        p = tmp_path / "pit.jsonl"
        fp.merge_write([{"code": "600000", "report_date": "2024-03-31",
                         "notice_date": "2024-04-29", "eps": 0.5}], p)
        got = fp.load_ledger(p)
        assert len(got) == 1 and got[0]["eps"] == 0.5

    def test_corrupt_lines_skipped_not_raised(self, tmp_path):
        p = tmp_path / "pit.jsonl"
        p.write_text('{"code":"600000","report_date":"2024-03-31","notice_date":"2024-04-29"}\n'
                     "{not json\n\n", encoding="utf-8")
        assert len(fp.load_ledger(p)) == 1

    def test_missing_ledger_returns_empty(self, tmp_path):
        assert fp.load_ledger(tmp_path / "nope.jsonl") == []

    def test_write_is_atomic_no_tmp_left(self, tmp_path):
        p = tmp_path / "pit.jsonl"
        fp.merge_write([{"code": "600000", "report_date": "2024-03-31",
                         "notice_date": "2024-04-29"}], p)
        assert p.exists() and not list(tmp_path.glob("*.tmp"))


class TestQuarterEnds:
    def test_quarter_ends_shape(self):
        got = fp.quarter_ends(2023, until="2024-06-30")
        assert got[:4] == ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"]
        assert got[-1] == "2024-06-30" and "2024-09-30" not in got

    def test_future_periods_excluded(self):
        got = fp.quarter_ends(2024, until="2024-01-15")
        assert got == []


class TestPriorYearPeriod:
    def test_same_quarter_prior_year(self):
        assert fp.prior_year_period("2024-03-31") == "2023-03-31"
        assert fp.prior_year_period("2024-12-31") == "2023-12-31"
        assert fp.prior_year_period("2024-06-30") == "2023-06-30"


class TestAsOfPeriod:
    """同比必须取**指定报告期的当时可见版本**,不能拿今天的最终版(那是数值维度 look-ahead)。"""

    def _recs(self):
        return [
            {"code": "600000", "report_date": "2023-03-31", "notice_date": "2023-04-28",
             "revenue": 100.0, "net_profit": 10.0},
            # 2023 一季报的更正版,2023-08 才公告
            {"code": "600000", "report_date": "2023-03-31", "notice_date": "2023-08-10",
             "revenue": 95.0, "net_profit": 8.0},
            {"code": "600000", "report_date": "2024-03-31", "notice_date": "2024-04-29",
             "revenue": 120.0, "net_profit": 15.0},
        ]

    def test_picks_specified_period_not_latest(self):
        got = fp.as_of_period(self._recs(), "2024-06-01", "2023-03-31", code="600000")
        assert got["600000"]["report_date"] == "2023-03-31"

    def test_picks_version_visible_at_query_day(self):
        """2023-05 查时只看得到原版(revenue=100),更正版 8 月才出。"""
        early = fp.as_of_period(self._recs(), "2023-05-01", "2023-03-31", code="600000")
        assert early["600000"]["revenue"] == 100.0
        later = fp.as_of_period(self._recs(), "2023-09-01", "2023-03-31", code="600000")
        assert later["600000"]["revenue"] == 95.0

    def test_returns_empty_when_period_not_yet_visible(self):
        assert fp.as_of_period(self._recs(), "2024-04-01", "2024-03-31") == {}

    def test_unknown_period_returns_empty(self):
        assert fp.as_of_period(self._recs(), "2024-06-01", "2022-03-31") == {}


class TestPitFeatures:
    def _recs(self):
        return [
            {"code": "600000", "report_date": "2023-03-31", "notice_date": "2023-04-28",
             "revenue": 100.0, "net_profit": 10.0, "roe_waa": 2.0, "gross_margin": 30.0,
             "eps": 0.5, "eps_deduct": 0.45, "ocf_ps": 1.0},
            {"code": "600000", "report_date": "2024-03-31", "notice_date": "2024-04-29",
             "revenue": 120.0, "net_profit": 15.0, "roe_waa": 2.6, "gross_margin": 32.0,
             "eps": 0.6, "eps_deduct": 0.54, "ocf_ps": 1.2},
        ]

    def test_features_computed_from_visible_period(self):
        f = fp.pit_features(self._recs(), "2024-05-06", "600000")
        assert f["f_roe"] == 2.6 and f["f_gross_margin"] == 32.0
        assert f["f_ocf_ps"] == 1.2
        assert abs(f["f_deduct_ratio"] - 0.9) < 1e-6
        assert abs(f["f_rev_yoy"] - 0.2) < 1e-6            # 120/100-1
        assert abs(f["f_np_yoy"] - 0.5) < 1e-6             # 15/10-1
        assert f["f_pit_lag_days"] == 7.0                  # 04-29 → 05-06

    def test_uses_older_period_before_new_one_visible(self):
        """2024-04-01 时一季报还没公告,应回落到 2023 一季报 —— 这正是 PIT。"""
        f = fp.pit_features(self._recs(), "2024-04-01", "600000")
        assert f["f_roe"] == 2.0 and "f_rev_yoy" not in f   # 2022 同期不在台账
        assert f["f_pit_lag_days"] > 300

    def test_empty_when_nothing_visible(self):
        assert fp.pit_features(self._recs(), "2023-01-01", "600000") == {}

    def test_yoy_skipped_when_prior_missing(self):
        recs = [r for r in self._recs() if r["report_date"] == "2024-03-31"]
        f = fp.pit_features(recs, "2024-05-06", "600000")
        assert "f_rev_yoy" not in f and "f_np_yoy" not in f
        assert f["f_roe"] == 2.6                            # 其余特征仍出

    def test_negative_base_yoy_suppressed(self):
        """上年同期亏损(分母<=0)时同比无经济含义,必须给 None 而不是算出个数。"""
        recs = self._recs()
        recs[0]["net_profit"] = -5.0
        f = fp.pit_features(recs, "2024-05-06", "600000")
        assert "f_np_yoy" not in f and "f_rev_yoy" in f

    def test_zero_eps_does_not_divide(self):
        recs = self._recs()
        recs[1]["eps"] = 0.0
        f = fp.pit_features(recs, "2024-05-06", "600000")
        assert "f_deduct_ratio" not in f

    def test_yoy_uses_prior_version_visible_then(self):
        """核心:上年同期若有更正版,取的必须是**查询日当时**可见的那一版。"""
        recs = self._recs() + [
            {"code": "600000", "report_date": "2023-03-31", "notice_date": "2024-06-15",
             "revenue": 80.0, "net_profit": 10.0}]
        early = fp.pit_features(recs, "2024-05-06", "600000")
        assert abs(early["f_rev_yoy"] - 0.2) < 1e-6         # 120/100-1,更正版还没出
        later = fp.pit_features(recs, "2024-06-16", "600000")
        assert abs(later["f_rev_yoy"] - 0.5) < 1e-6         # 120/80-1,更正版已可见


class TestBuildPitFeatureFn:
    def test_callback_signature_matches_extra_feature_fn(self):
        """必须是 (code, as_of_day) -> dict,才能直接挂 launch_point_study 的钩子。"""
        recs = [{"code": "600000", "report_date": "2024-03-31", "notice_date": "2024-04-29",
                 "roe_waa": 2.6}]
        fn = fp.build_pit_feature_fn(recs)
        assert fn("600000", "2024-05-06") == {"f_roe": 2.6, "f_pit_lag_days": 7.0}

    def test_unknown_code_returns_empty(self):
        fn = fp.build_pit_feature_fn([{"code": "600000", "report_date": "2024-03-31",
                                       "notice_date": "2024-04-29", "roe_waa": 2.6}])
        assert fn("000001", "2024-05-06") == {}

    def test_accepts_datetime_like_day(self):
        fn = fp.build_pit_feature_fn([{"code": "600000", "report_date": "2024-03-31",
                                       "notice_date": "2024-04-29", "roe_waa": 2.6}])
        assert fn("600000", "2024-05-06 00:00:00")["f_roe"] == 2.6


class TestVerifyLedger:
    """缺期不会让 as_of 报错,只会静默返回上一期 ⇒ 必须靠自检抓出来。"""

    def _recs(self, periods, n=3):
        out = []
        for p in periods:
            for i in range(n):
                out.append({"code": f"60000{i}", "report_date": p,
                            "notice_date": p[:4] + "-12-31", "eps": 0.1})
        return out

    def test_complete_ledger_ok(self):
        rep = fp.verify_ledger(self._recs(["2024-03-31", "2024-06-30", "2024-09-30",
                                           "2024-12-31"]), since_year=2024)
        assert rep["ok"] is True and rep["missing"] == []
        assert "无缺口" in rep["text"]

    def test_missing_period_detected(self):
        """漏 2024-06-30 ⇒ as_of 在 2024-09 前会一直返回一季报,必须报出来。"""
        rep = fp.verify_ledger(self._recs(["2024-03-31", "2024-09-30", "2024-12-31"]),
                               since_year=2024)
        assert rep["ok"] is False and rep["missing"] == ["2024-06-30"]
        assert "缺 1 期" in rep["text"] and "静默返回上一期" in rep["text"]

    def test_thin_period_detected(self):
        """某期行数远低于邻期 = 分页中断/限流导致样本残缺。"""
        recs = self._recs(["2024-03-31", "2024-09-30", "2024-12-31"], n=100)
        recs += [{"code": "600001", "report_date": "2024-06-30",
                  "notice_date": "2024-08-28", "eps": 0.1}]
        rep = fp.verify_ledger(recs, since_year=2024)
        assert rep["ok"] is False
        assert [t["period"] for t in rep["thin_periods"]] == ["2024-06-30"]
        assert "行数异常偏少" in rep["text"]

    def test_expected_range_inferred_from_ledger(self):
        rep = fp.verify_ledger(self._recs(["2023-03-31", "2023-06-30", "2023-09-30",
                                           "2023-12-31"]))
        assert rep["n_periods_expect"] == 4 and rep["ok"] is True

    def test_empty_ledger_reported(self):
        rep = fp.verify_ledger([])
        assert rep["ok"] is False and "台账为空" in rep["error"]

    def test_cli_verify_exit_1_on_hole(self, tmp_path, capsys):
        p = tmp_path / "pit.jsonl"
        rows = [{"code": "600000", "report_date": rd, "notice_date": "2024-12-31"}
                for rd in ("2024-03-31", "2024-12-31")]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        rc = fp.main(["--verify", "--out", str(p), "--verify-since", "2024"])
        assert rc == 1
        err = capsys.readouterr()
        assert "2024-06-30" in err.out and "补拉命令" in err.err

    def test_cli_verify_exit_0_when_clean(self, tmp_path, capsys):
        p = tmp_path / "pit.jsonl"
        rows = [{"code": "600000", "report_date": rd, "notice_date": "2024-12-31"}
                for rd in ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31")]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        assert fp.main(["--verify", "--out", str(p), "--verify-since", "2024"]) == 0


class TestCli:
    def test_as_of_requires_ledger(self, tmp_path, capsys):
        rc = fp.main(["--as-of", "2024-05-06", "--out", str(tmp_path / "nope.jsonl")])
        assert rc == 2 and "台账为空" in capsys.readouterr().err

    def test_as_of_prints_visible_rows(self, tmp_path, capsys):
        p = tmp_path / "pit.jsonl"
        p.write_text(json.dumps({"code": "600000", "name": "测试股",
                                 "report_date": "2024-03-31", "notice_date": "2024-04-29",
                                 "lag_days": 29, "eps": 0.5, "roe_waa": 3.2},
                                ensure_ascii=False) + "\n", encoding="utf-8")
        rc = fp.main(["--as-of", "2024-05-06", "--out", str(p)])
        out = capsys.readouterr().out
        assert rc == 0 and "600000" in out and "公告次日起可见" in out

    def test_requires_periods_or_as_of(self, capsys):
        try:
            fp.main([])
        except SystemExit as exc:
            assert exc.code == 2
        assert "--periods" in capsys.readouterr().err
