# -*- coding: utf-8 -*-
"""审计【建议优化】批次回归测试：网络层 / 采集层 / 门控层 / 缓存。

全部为**行为断言**（不做源码文本匹配），不发真实网络请求。
字典断言按键比较，避免上游多透一个字段就算回归。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import requests

TESTS_DIR = Path(__file__).resolve().parent

from custos.core import net_retry  # noqa: E402
from custos.datasource.collect import online_quotes as oq  # noqa: E402
from custos.datasource.collect import collect_fund_flow as cff  # noqa: E402
from custos.datasource.collect import collect_holding_quotes as chq  # noqa: E402
from custos.core import runtime_guards as rg  # noqa: E402


# ---------------------------------------------------------------- helpers ----
class _Resp:
    """最小 requests.Response 替身：status_code / headers / raise_for_status。"""

    def __init__(self, status: int, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}
        self.url = "http://x/y"

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} for {self.url}")
            err.response = self
            raise err
        return None

    def json(self):
        return {}


class _Session:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        item = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture()
def sleeps(monkeypatch):
    """拦截 time.sleep，返回累计的休眠时长列表（测试不得真的睡）。"""
    rec: list[float] = []
    monkeypatch.setattr(net_retry.time, "sleep", lambda s: rec.append(s))
    return rec


# ============================================== 1. net_retry 4xx 不该重试 ====
class TestNetRetryStatusAware:
    def test_404_not_retried(self, sleeps):
        s = _Session(_Resp(404))
        with pytest.raises(requests.HTTPError):
            net_retry.fetch_with_retry("http://x", session=s, tries=3)
        assert s.calls == 1, "4xx 是确定性失败，重试纯浪费"
        assert sleeps == []

    def test_403_not_retried(self, sleeps):
        s = _Session(_Resp(403))
        with pytest.raises(requests.HTTPError):
            net_retry.fetch_with_retry("http://x", session=s, tries=3)
        assert s.calls == 1

    @pytest.mark.parametrize("status", [408, 429])
    def test_transient_4xx_still_retried(self, sleeps, status):
        s = _Session(_Resp(status))
        with pytest.raises(requests.HTTPError):
            net_retry.fetch_with_retry("http://x", session=s, tries=3)
        assert s.calls == 3

    def test_5xx_still_retried(self, sleeps):
        s = _Session(_Resp(503))
        with pytest.raises(requests.HTTPError):
            net_retry.fetch_with_retry("http://x", session=s, tries=3)
        assert s.calls == 3

    def test_connection_error_still_retried(self, sleeps):
        s = _Session(requests.ConnectionError("boom"))
        with pytest.raises(requests.ConnectionError):
            net_retry.fetch_with_retry("http://x", session=s, tries=3)
        assert s.calls == 3

    def test_success_after_transient(self, sleeps):
        s = _Session(_Resp(503), _Resp(200))
        resp = net_retry.fetch_with_retry("http://x", session=s, tries=3)
        assert resp.status_code == 200
        assert s.calls == 2

    def test_retry_after_header_honored(self, sleeps):
        s = _Session(_Resp(429, {"Retry-After": "7"}), _Resp(200))
        net_retry.fetch_with_retry("http://x", session=s, tries=3, jitter=0.0)
        assert sleeps == [7.0], (
            "服务端明确给了 Retry-After 就该照办，而不是自作主张退避"
        )

    def test_retry_after_capped(self, sleeps):
        s = _Session(_Resp(429, {"Retry-After": "9999"}), _Resp(200))
        net_retry.fetch_with_retry(
            "http://x", session=s, tries=3, jitter=0.0, max_sleep=30.0
        )
        assert sleeps and sleeps[0] <= 30.0

    def test_jitter_within_bounds(self, monkeypatch):
        rec: list[float] = []
        monkeypatch.setattr(net_retry.time, "sleep", lambda s: rec.append(s))
        s = _Session(_Resp(503), _Resp(503), _Resp(503), _Resp(503), _Resp(200))
        net_retry.fetch_with_retry("http://x", session=s, tries=5, backoff=2.0)
        assert len(rec) == 4
        for i, v in enumerate(rec):
            base = 2.0**i
            assert base <= v <= base * 1.5, f"attempt {i} 退避 {v} 越界"
        assert len(set(rec)) == len(rec), "加了 jitter 就不该每次都是同一个整数值"

    def test_jitter_zero_is_exact_backoff(self, sleeps):
        s = _Session(_Resp(503), _Resp(503), _Resp(200))
        net_retry.fetch_with_retry(
            "http://x", session=s, tries=3, backoff=2.0, jitter=0.0
        )
        assert sleeps == [1.0, 2.0], "jitter=0 必须退回精确指数退避（向后兼容）"

    def test_retry_call_backward_compatible(self, sleeps):
        calls = {"n": 0}

        def f():
            calls["n"] += 1
            raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            net_retry.retry_call(f, tries=3, backoff=2.0, jitter=0.0)
        assert calls["n"] == 3
        assert sleeps == [1.0, 2.0]


# ================================= 2/3. collect_incremental_market 解析 =====
def _cim():
    from custos.datasource.collect import collect_incremental_market as cim

    return cim


class TestYahooParsing:
    def _payload(self, **meta):
        base = {
            "regularMarketPrice": 13000.0,
            "previousClose": 13000.0,
            "regularMarketTime": 1_760_000_000,
        }
        base.update(meta)
        return {"chart": {"result": [{"meta": base}]}}

    def test_flat_change_pct_is_zero_not_none(self):
        out = _cim().parse_yahoo_payload(
            "XIN9.FGI", self._payload(regularMarketChangePercent=0.0)
        )
        assert out["change_pct"] == 0.0, (
            "平盘 0.0 被 `if chg` 当成缺数据 → 下游误判缺采集"
        )
        assert out["price"] == 13000.0

    def test_derived_change_pct_still_works(self):
        out = _cim().parse_yahoo_payload(
            "X", self._payload(regularMarketPrice=110.0, previousClose=100.0)
        )
        assert out["change_pct"] == pytest.approx(10.0)

    def test_null_result_raises_valueerror_not_typeerror(self):
        with pytest.raises(ValueError):
            _cim().parse_yahoo_payload("X", {"chart": {"result": None}})

    def test_empty_result_list_raises_valueerror(self):
        with pytest.raises(ValueError):
            _cim().parse_yahoo_payload("X", {"chart": {"result": []}})

    def test_error_payload_raises_valueerror(self):
        with pytest.raises(ValueError):
            _cim().parse_yahoo_payload(
                "X", {"chart": {"result": None, "error": {"code": "Not Found"}}}
            )


class _FakeReader:
    """mootdx Reader 替身：按 symbol 返回预置 DataFrame（或 None）。"""

    def __init__(self, frames):
        self._frames = frames

    def daily(self, symbol):
        return self._frames.get(symbol)


def _frame(rows):
    import pandas as pd

    return pd.DataFrame(rows)


class TestIncrementalUnavailableMarkers:
    def test_breadth_short_sample_writes_status_key(self):
        cim = _cim()
        reader = _FakeReader({"880005": _frame([{"close": 3000.0, "amount": 1.0}])})
        breadth = cim.derive_breadth(reader, None)
        assert "880005" in breadth, "样本不足时整键缺失 → 下游无法区分未采集与采集为空"
        assert breadth["880005"].get("status") == "unavailable"
        assert breadth["880005"].get("name")

    def test_breadth_missing_frame_writes_status_key(self):
        cim = _cim()
        breadth = cim.derive_breadth(_FakeReader({}), None)
        for code in ("880001", "880005", "880006", "880390", "880863"):
            assert code in breadth
            assert breadth[code].get("status") in {"unavailable", "error"}

    def test_breadth_reader_unavailable_marks_all(self):
        cim = _cim()
        breadth = cim.derive_breadth(None, "no tdx")
        assert breadth.get("status") == "unavailable"
        assert "no tdx" in json.dumps(breadth, ensure_ascii=False)

    def test_northbound_short_sample_writes_status(self):
        cim = _cim()
        reader = _FakeReader({"880863": _frame([{"close": 1.0}, {"close": 2.0}])})
        nb = cim.derive_northbound(reader, None)
        assert nb.get("status") == "unavailable"
        assert "latest_close" not in nb or nb.get("latest_close") is None

    def test_northbound_full_sample_ok(self):
        cim = _cim()
        rows = [{"close": float(i)} for i in range(1, 6)]
        nb = cim.derive_northbound(_FakeReader({"880863": _frame(rows)}), None)
        assert nb.get("status") == "ok"
        assert nb["latest_close"] == 5.0
        assert nb["trend"] == "up"


# ================================ 4. collect_fund_flow 失败 != 无数据 ======
class TestFundFlowSectorFailure:
    def _stock_payload(self):
        return {"data": {"diff": [{"f12": "600000", "f14": "浦发", "f62": 1.0}]}}

    def _sector_payload(self):
        return {"data": {"diff": [{"f12": "BK0001", "f14": "半导体", "f62": 2.0}]}}

    def test_sector_failure_marked_not_silently_empty(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(cff, "MARKET_DIR", tmp_path / "data" / "market")

        def fake_fetch(url):
            if "t:3+f:!50" in url:  # concept
                raise RuntimeError("boom")
            if "t:2+f:!50" in url:  # industry
                return self._sector_payload()
            return self._stock_payload()

        monkeypatch.setattr(cff, "fetch_json", fake_fetch)
        monkeypatch.setattr(cff.time, "sleep", lambda s: None)
        rc = cff.main(["--date", "2026-07-20"])
        assert rc == 0
        out = json.loads(
            (tmp_path / "data" / "market" / "2026-07-20_fund_flow_rank.json").read_text(
                encoding="utf-8"
            )
        )
        # 向后兼容：sector_rank 仍是 {类型: list}
        assert isinstance(out["sector_rank"]["concept"], list)
        assert out["sector_rank"]["concept"] == []
        status = out["sector_rank_status"]
        assert status["concept"]["status"] == "failed", "拉取失败被读成今天没有资金流入"
        assert "boom" in str(status["concept"].get("error"))
        assert status["industry"]["status"] == "ok"
        assert status["industry"]["count"] == 1
        assert out["status"] == "partial"

    def test_all_ok_status_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cff, "MARKET_DIR", tmp_path / "data" / "market")
        monkeypatch.setattr(
            cff,
            "fetch_json",
            lambda url: (
                self._sector_payload() if "m:90" in url else self._stock_payload()
            ),
        )
        monkeypatch.setattr(cff.time, "sleep", lambda s: None)
        assert cff.main(["--date", "2026-07-20"]) == 0
        out = json.loads(
            (tmp_path / "data" / "market" / "2026-07-20_fund_flow_rank.json").read_text(
                encoding="utf-8"
            )
        )
        assert out["status"] == "ok"
        assert {k: v["status"] for k, v in out["sector_rank_status"].items()} == {
            "industry": "ok",
            "concept": "ok",
        }


# ============================ 5. online_quotes 单行脏数据不丢整批 ==========
class TestOnlineQuotesRowTolerance:
    def _tencent(self, rows):
        return {"data": {"sh600000": {"day": rows}}}

    def test_tencent_partial_bad_row_keeps_good_rows(self, monkeypatch, capsys):
        rows = [
            ["2026-07-16", "10.0", "10.1", "10.2", "9.9", "1000"],
            ["2026-07-17", "bad", "x", "y", "z", "w"],
            ["2026-07-20", "10.3", "10.5", "10.6", "10.2", "3000"],
        ]
        resp = type("R", (), {"json": lambda self: self._p})()
        resp._p = self._tencent(rows)
        monkeypatch.setattr(oq, "fetch_with_retry", lambda *a, **k: resp)
        bars = oq.fetch_tencent_daily("600000", count=3)
        assert bars is not None and len(bars) == 2, "一行脏数据丢掉整批报价"
        assert [b["date"] for b in bars] == ["2026-07-16", "2026-07-20"]
        err = capsys.readouterr().err
        assert "1" in err and ("drop" in err.lower() or "丢弃" in err)

    def test_tencent_all_bad_rows_still_none(self, monkeypatch):
        resp = type("R", (), {"json": lambda self: self._p})()
        resp._p = self._tencent([["2026-07-20", "a", "b"]])
        monkeypatch.setattr(oq, "fetch_with_retry", lambda *a, **k: resp)
        assert oq.fetch_tencent_daily("600000") is None

    def test_sina_partial_bad_row_keeps_good_rows(self, monkeypatch, capsys):
        rows = [
            {
                "day": "2026-07-16",
                "open": "1",
                "high": "2",
                "low": "0.5",
                "close": "1.5",
                "volume": "10",
            },
            {"day": "2026-07-17"},
            {
                "day": "2026-07-20",
                "open": "2",
                "high": "3",
                "low": "1.5",
                "close": "2.5",
                "volume": "20",
            },
        ]
        resp = type("R", (), {"json": lambda self: self._p})()
        resp._p = rows
        monkeypatch.setattr(oq, "fetch_with_retry", lambda *a, **k: resp)
        bars = oq.fetch_sina_daily("600000", count=3)
        assert bars is not None and len(bars) == 2
        assert capsys.readouterr().err.strip() != ""

    def test_sina_all_bad_rows_still_none(self, monkeypatch):
        resp = type("R", (), {"json": lambda self: self._p})()
        resp._p = [{"day": "2026-07-20"}]
        monkeypatch.setattr(oq, "fetch_with_retry", lambda *a, **k: resp)
        assert oq.fetch_sina_daily("600000") is None


# ================================== 6. URL 代码白名单 + ut token 常量 ======
class TestUrlCodeWhitelist:
    @pytest.mark.parametrize("code", ["600000", "000001", "920819", "899050"])
    def test_valid_codes_build_url(self, code):
        url = chq.em_stock_url(code)
        assert f"secid=0.{code}" in url
        assert f"ut={chq.EM_QUOTE_UT}" in url

    @pytest.mark.parametrize(
        "code",
        [
            "",
            "60000",
            "6000001",
            "60000a",
            "600000&x=1",
            "../../etc/passwd",
            "0.1/y",
            None,
        ],
    )
    def test_invalid_codes_rejected(self, code):
        with pytest.raises(ValueError):
            chq.em_stock_url(code)

    def test_bj_quote_rejects_bad_code_without_network(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("代码未校验就发起了请求")

        monkeypatch.setattr(requests, "Session", boom)
        with pytest.raises(ValueError):
            chq._eastmoney_bj_quote("600000; rm -rf /", "x", "2026-07-20")

    def test_try_quote_swallows_validation_error(self, monkeypatch):
        monkeypatch.setattr(
            requests,
            "Session",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no net")),
        )
        assert (
            chq._try_quote(chq._eastmoney_bj_quote, "bad!", "x", "2026-07-20") is None
        )

    def test_fund_flow_urls_share_public_ut_constant(self):
        assert cff.EM_UT and cff.EM_UT.isalnum()
        assert f"ut={cff.EM_UT}" in cff.EM_URL
        for url in cff.SECTOR_URLS.values():
            assert f"ut={cff.EM_UT}" in url


# ======================= 7. runtime_guards 继承判定按 session 期望日 ======
DAY = "2026-07-20"
PREV = "2026-07-17"


def _full_section(name, as_of, quality="auto"):
    body = {
        "market_breadth": {"up_count": 3000, "down_count": 1000},
        "sentiment": {"limit_up_count": 50},
        "turnover": {"turnover_change_pct": 5.0},
        "amv_0": {"amv_change_pct": 1.0},
    }[name]
    return {**body, "quality": quality, "as_of": as_of}


@pytest.fixture()
def guard_data(tmp_path, monkeypatch):
    """把 runtime_guards.DATA 指到 tmp，用于 _latest_market_section 的继承扫描。"""
    monkeypatch.setattr(rg, "DATA", tmp_path)
    (tmp_path / "market").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_prior(root, day, sections, quality="confirmed"):
    payload = {name: _full_section(name, day, quality) for name in sections}
    (root / "market" / f"{day}_market_timing_input.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _chk(result, field):
    return next(x for x in result["checks"] if x["field"] == field)


class TestInheritedUsesExpectedDay:
    def test_preclose_inherited_from_expected_day_is_fresh(self, guard_data):
        """盘前 session 期望 T-1；继承自 T-1 的 section 不该被标 stale。"""
        _write_prior(
            guard_data, PREV, ["amv_0", "market_breadth", "sentiment", "turnover"]
        )
        r = rg.market_quality_gate({}, DAY, expected_day=PREV)
        for f in ("0AMV", "market_breadth", "sentiment", "turnover"):
            assert _chk(r, f)["quality"] != "stale", f
        assert r["status"] != "blocked"
        assert r["expected_day"] == PREV

    def test_preclose_inherited_from_older_day_still_stale(self, guard_data):
        _write_prior(
            guard_data,
            "2026-07-10",
            ["amv_0", "market_breadth", "sentiment", "turnover"],
        )
        r = rg.market_quality_gate({}, DAY, expected_day=PREV)
        for f in ("0AMV", "market_breadth", "sentiment", "turnover"):
            assert _chk(r, f)["quality"] == "stale", f

    def test_postclose_behavior_unchanged(self, guard_data):
        """exp 缺省=day（盘后）：继承自 T-1 仍必须是 stale。"""
        _write_prior(
            guard_data, PREV, ["amv_0", "market_breadth", "sentiment", "turnover"]
        )
        r = rg.market_quality_gate({}, DAY)
        for f in ("0AMV", "market_breadth", "sentiment", "turnover"):
            assert _chk(r, f)["quality"] == "stale", f
        assert r["status"] == "blocked"

    def test_current_file_section_unaffected_by_expected_day(self, guard_data):
        """当日文件里 as_of=T-1 的 section 在盘前 session 依旧算新鲜（as_of == exp）。"""
        market = {
            name: _full_section(name, PREV)
            for name in ("amv_0", "market_breadth", "sentiment", "turnover")
        }
        market["amv_0"]["quality"] = "confirmed"
        r = rg.market_quality_gate(market, DAY, expected_day=PREV)
        for f in ("0AMV", "market_breadth", "sentiment", "turnover"):
            assert _chk(r, f)["quality"] != "stale", f
        assert r["status"] == "pass"

    def test_inherited_flag_still_reports_calendar_truth(self, guard_data):
        """inherited 仍应如实标注「值不是来自当日文件」，只是不再据此判 stale。"""
        _write_prior(guard_data, PREV, ["market_breadth"])
        r = rg.market_quality_gate({}, DAY, expected_day=PREV)
        assert _chk(r, "market_breadth")["inherited"] is True
        assert PREV in r["inherited_sections"]["market_breadth"]["as_of"]

    @pytest.mark.parametrize("n_present", [1, 2, 3, 4])
    def test_no_new_blocked_when_any_core_fresh(self, guard_data, n_present):
        """放宽继承判定只能减少 stale，绝不能新增 blocked 场景。"""
        names = ["amv_0", "market_breadth", "sentiment", "turnover"]
        _write_prior(guard_data, PREV, names[:n_present])
        r = rg.market_quality_gate({}, DAY, expected_day=PREV)
        assert r["status"] != "blocked"

    def test_all_missing_still_blocked(self, guard_data):
        r = rg.market_quality_gate({}, DAY, expected_day=PREV)
        assert r["status"] == "blocked"

    @pytest.mark.parametrize("prior_day", [PREV, "2026-07-10", None])
    @pytest.mark.parametrize("in_file_as_of", [DAY, PREV, "2026-07-10", None])
    @pytest.mark.parametrize("exp", [DAY, PREV])
    def test_relaxation_never_adds_stale_or_blocked(
        self, guard_data, prior_day, in_file_as_of, exp
    ):
        """穷举：新判定标 stale 的场景必须是旧判定也标 stale 的子集。

        旧判定 = ``inherited or stale_as_of``（两个字段仍在 checks 里如实输出），
        新判定 = ``(inherited and source_day != exp) or stale_as_of``，是旧判定的
        **真子集** ⇒ 只可能把 stale 变回新鲜，绝不可能新增 stale；而 blocked 要求
        四个核心块全 bad，坏项集合只缩不涨 ⇒ 不新增 blocked。
        """
        names = ["amv_0", "market_breadth", "sentiment", "turnover"]
        if prior_day:
            _write_prior(guard_data, prior_day, names)
        market = {}
        if in_file_as_of:
            market = {n: _full_section(n, in_file_as_of) for n in names}
            market["amv_0"]["quality"] = "confirmed"
        r = rg.market_quality_gate(market, DAY, expected_day=exp)
        for chk in r["checks"]:
            if chk["field"] == "overseas":
                continue
            old_stale = bool(chk["inherited"]) or bool(chk["stale_as_of"])
            if chk["quality"] == "stale":
                assert old_stale, f"{chk['field']} 新增了旧判定没有的 stale: {chk}"
        if r["status"] == "blocked":
            # blocked 只允许出现在「旧判定也全 bad」的场景
            core = [c for c in r["checks"] if c["field"] != "overseas"]
            assert all(
                c["inherited"]
                or c["stale_as_of"]
                or c["quality"] in {"missing", "raw_only"}
                for c in core
            )


# ==================== 8/9. merge_incremental_market: amv as_of + 失败留痕 ===
def _mim():
    from custos.pipeline.market_timing import merge_incremental_market as mim

    return mim


def _market_dir(tmp_path):
    d = tmp_path / "data" / "market"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestMergeAmvAsOf:
    def test_amv_0_gets_as_of_from_amv_0day(self, tmp_path, monkeypatch):
        mim = _mim()
        monkeypatch.setattr(mim, "MARKET_DIR", tmp_path / "data" / "market")
        monkeypatch.setattr(mim, "QUALITY_DIR", tmp_path / "data" / "quality")
        md = _market_dir(tmp_path)
        (md / "2026-07-20_market_timing_input.json").write_text(
            json.dumps({"amv_0day": 5.1, "amv_0": {}}), encoding="utf-8"
        )
        assert mim.main(["--date", "2026-07-20"]) == 0
        mkt = json.loads(
            (md / "2026-07-20_market_timing_input.json").read_text(encoding="utf-8")
        )
        assert mkt["amv_0"]["quality"] == "confirmed"
        assert mkt["amv_0"]["as_of"] == "2026-07-20", (
            "权重 35 的块没有 as_of，无法做陈旧校验"
        )

    def test_amv_0_as_of_from_observation_ledger(self, tmp_path, monkeypatch):
        mim = _mim()
        monkeypatch.setattr(mim, "MARKET_DIR", tmp_path / "data" / "market")
        monkeypatch.setattr(mim, "QUALITY_DIR", tmp_path / "data" / "quality")
        md = _market_dir(tmp_path)
        (md / "2026-07-20_market_timing_input.json").write_text(
            json.dumps({"amv_0": {}}), encoding="utf-8"
        )
        (md / "0amv_observations.jsonl").write_text(
            json.dumps(
                {
                    "date": "2026-07-20",
                    "amv_change_pct": -3.0,
                    "as_of": "2026-07-20",
                    "quality": "confirmed",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert mim.main(["--date", "2026-07-20"]) == 0
        mkt = json.loads(
            (md / "2026-07-20_market_timing_input.json").read_text(encoding="utf-8")
        )
        assert mkt["amv_0"]["as_of"] == "2026-07-20"
        assert mkt["amv_0"]["effective_state"] == "空头"

    def test_gate_sees_amv_as_of_and_not_stale(self, tmp_path, monkeypatch):
        """端到端不变量：merge 写完后门控不得把当日 0AMV 判成 stale。"""
        mim = _mim()
        monkeypatch.setattr(mim, "MARKET_DIR", tmp_path / "data" / "market")
        monkeypatch.setattr(mim, "QUALITY_DIR", tmp_path / "data" / "quality")
        md = _market_dir(tmp_path)
        (md / "2026-07-20_market_timing_input.json").write_text(
            json.dumps({"amv_0day": 5.1, "amv_0": {}}), encoding="utf-8"
        )
        mim.main(["--date", "2026-07-20"])
        mkt = json.loads(
            (md / "2026-07-20_market_timing_input.json").read_text(encoding="utf-8")
        )
        r = rg.market_quality_gate(mkt, "2026-07-20")
        assert _chk(r, "0AMV")["quality"] == "confirmed"
        assert _chk(r, "0AMV")["as_of"] == "2026-07-20"
        assert r["amv_ok"] is True


class TestMergeFailureLeavesTrace:
    def test_merge_exception_writes_status_and_nonzero(
        self, tmp_path, monkeypatch, capsys
    ):
        mim = _mim()
        monkeypatch.setattr(mim, "MARKET_DIR", tmp_path / "data" / "market")
        monkeypatch.setattr(mim, "QUALITY_DIR", tmp_path / "data" / "quality")
        md = _market_dir(tmp_path)
        (md / "2026-07-20_incremental_market.json").write_text("{}", encoding="utf-8")
        (md / "2026-07-20_market_timing_input.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            mim,
            "merge_incremental",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaboom")),
        )
        rc = mim.main(["--date", "2026-07-20"])
        assert rc != 0, "异常只打 WARN + exit 0 → stage log 看不到任何失败"
        status = json.loads(
            (
                tmp_path
                / "data"
                / "quality"
                / "2026-07-20_merge_incremental_status.json"
            ).read_text(encoding="utf-8")
        )
        assert status["status"] == "failed"
        assert "kaboom" in status["error"]

    def test_success_writes_ok_status(self, tmp_path, monkeypatch):
        mim = _mim()
        monkeypatch.setattr(mim, "MARKET_DIR", tmp_path / "data" / "market")
        monkeypatch.setattr(mim, "QUALITY_DIR", tmp_path / "data" / "quality")
        md = _market_dir(tmp_path)
        (md / "2026-07-20_incremental_market.json").write_text(
            json.dumps(
                {
                    "breadth": {
                        "880005": {
                            "date": "2026-07-20",
                            "up_count": 3000,
                            "down_count": 1500,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (md / "2026-07-20_market_timing_input.json").write_text("{}", encoding="utf-8")
        assert mim.main(["--date", "2026-07-20"]) == 0
        status = json.loads(
            (
                tmp_path
                / "data"
                / "quality"
                / "2026-07-20_merge_incremental_status.json"
            ).read_text(encoding="utf-8")
        )
        assert status["status"] == "ok"

    def test_unavailable_incremental_section_not_merged(self, tmp_path, monkeypatch):
        """采集侧显式 unavailable 标记不得被并成一个空的 market_breadth 段。"""
        mim = _mim()
        inc = {
            "breadth": {
                "880005": {
                    "name": "涨跌家数",
                    "status": "unavailable",
                    "reason": "样本不足",
                }
            }
        }
        mkt, stale = mim.merge_incremental(inc, {}, "2026-07-20")
        assert "market_breadth" not in mkt


# ============================ 10. 硬编码总数推算跌家数 ====================
def _mtc():
    from custos.pipeline.market_timing import market_timing_collector as m

    return m


class TestBreadthRatioHonesty:
    def test_ratio_unavailable_without_real_total(self, monkeypatch):
        mtc = _mtc()
        monkeypatch.setattr(
            mtc,
            "_vipdoc_rows",
            lambda code, count=5: [
                {
                    "date": "2026-07-17",
                    "close": 2000.0,
                    "high": None,
                    "low": None,
                    "amount": 1.0,
                }
            ],
        )
        monkeypatch.setattr(
            mtc, "previous_confirmed_trading_day", lambda d: "2026-07-17"
        )
        monkeypatch.setattr(
            mtc, "resolve_total_stocks", lambda: (None, "no truth source")
        )
        breadth, _s, _t, q = mtc.derive_market_fields("2026-07-20")
        assert breadth["up_count"] == 2000
        assert breadth["down_count"] is None, "硬编码 5530 推算跌家数使涨跌比系统性偏低"
        assert breadth["up_down_ratio"] is None
        assert breadth["up_down_ratio_status"] == "unavailable"
        assert any("总数" in n or "跌家数" in n for n in q["notes"])

    def test_ratio_uses_real_total_when_available(self, monkeypatch):
        mtc = _mtc()
        monkeypatch.setattr(
            mtc,
            "_vipdoc_rows",
            lambda code, count=5: [
                {
                    "date": "2026-07-17",
                    "close": 2000.0,
                    "high": None,
                    "low": None,
                    "amount": 1.0,
                }
            ],
        )
        monkeypatch.setattr(
            mtc, "previous_confirmed_trading_day", lambda d: "2026-07-17"
        )
        monkeypatch.setattr(mtc, "resolve_total_stocks", lambda: (5000, "test_source"))
        breadth, _s, _t, _q = mtc.derive_market_fields("2026-07-20")
        assert breadth["down_count"] == 3000
        assert breadth["up_down_ratio"] == pytest.approx(2000 / 3000, abs=1e-4)
        assert breadth["up_down_ratio_status"] == "derived_from_total"
        assert breadth["total_stocks_source"] == "test_source"

    def test_scorer_treats_unavailable_ratio_as_neutral(self):
        from custos.pipeline.market_timing import market_timing_scorer as sc

        s, note = sc.score_breadth(
            {
                "market_breadth": {
                    "up_count": 2000,
                    "down_count": None,
                    "up_down_ratio_status": "unavailable",
                }
            }
        )
        assert s == 7.5, "不可用必须走中性，不能吃一个偏低的估算比值"

    def test_resolve_total_stocks_env_override(self, monkeypatch):
        from custos.datasource import breadth_basis as bb

        monkeypatch.setenv("A_SHARE_TOTAL_STOCKS", "5401")
        total, src = bb.resolve_total_stocks()
        assert total == 5401 and "env" in src

    def test_resolve_total_stocks_rejects_garbage(self, monkeypatch):
        from custos.datasource import breadth_basis as bb

        monkeypatch.setenv("A_SHARE_TOTAL_STOCKS", "abc")
        total, src = bb.resolve_total_stocks()
        assert total is None

    def test_resolve_total_stocks_none_by_default(self, monkeypatch, tmp_path):
        from custos.datasource import breadth_basis as bb

        monkeypatch.delenv("A_SHARE_TOTAL_STOCKS", raising=False)
        monkeypatch.setattr(bb, "UNIVERSE_FILE", tmp_path / "nope.json")
        total, src = bb.resolve_total_stocks()
        assert total is None and src

    def test_resolve_total_stocks_from_universe_file(self, monkeypatch, tmp_path):
        from custos.datasource import breadth_basis as bb

        monkeypatch.delenv("A_SHARE_TOTAL_STOCKS", raising=False)
        p = tmp_path / "a_share_universe.json"
        p.write_text(
            json.dumps({"total": 5388, "as_of": "2026-07-20"}), encoding="utf-8"
        )
        monkeypatch.setattr(bb, "UNIVERSE_FILE", p)
        total, src = bb.resolve_total_stocks()
        assert total == 5388 and "universe" in src


# =================================== 11. 缓存（可失效 / 可注入） ==========
class TestCalendarCache:
    def test_previous_trading_day_reads_calendar_once(self, monkeypatch):
        rg.clear_calendar_cache()
        reads = {"n": 0}
        real = rg.load_json

        def counting(path, default):
            reads["n"] += 1
            return real(path, default)

        monkeypatch.setattr(rg, "load_json", counting)
        rg.previous_confirmed_trading_day("2026-02-24")
        assert reads["n"] <= 2, f"单次门控读了 {reads['n']} 次日历文件"

    def test_cache_invalidated_when_file_changes(self, tmp_path, monkeypatch):
        rg.clear_calendar_cache()
        cfg = tmp_path / "cal.json"
        cfg.write_text(
            json.dumps(
                {"overrides": {"2026-07-20": {"is_trading_day": True, "reason": "v1"}}}
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(rg, "CALENDAR_CONFIG", cfg)
        monkeypatch.setattr(rg, "CALENDAR_CACHE", tmp_path / "missing.json")
        assert rg.trading_day_status("2026-07-20")["reason"] == "v1"
        cfg.write_text(
            json.dumps(
                {"overrides": {"2026-07-20": {"is_trading_day": False, "reason": "v2"}}}
            ),
            encoding="utf-8",
        )
        import os

        os.utime(cfg, (0, 0))  # 强制 mtime 变化，避免同秒写入被判未变
        assert rg.trading_day_status("2026-07-20")["reason"] == "v2"

    def test_clear_calendar_cache_is_public(self):
        rg.clear_calendar_cache()
        assert rg.trading_day_status("2026-07-16")["is_trading_day"] is True


class TestBatchHoldingTechnicalCache:
    def test_analysis_computed_in_process_and_cached(self, tmp_path, monkeypatch):
        from custos.pipeline.holdings import batch_holding_technical as bht

        bht.clear_analysis_cache()
        calls: list[str] = []

        def fake_analyze(code, name):
            calls.append(code)
            return {"available": True, "latest_date": "2026-07-20"}

        monkeypatch.setattr(bht, "analyze_code", fake_analyze)
        monkeypatch.setattr(bht, "HOLD", tmp_path)
        rows = bht.build_summary(
            [
                {"code": "600000", "name": "A"},
                {"code": "600000", "name": "A"},
                {"code": "000001", "name": "B"},
            ],
            "2026-07-20",
        )
        assert [r["code"] for r in rows] == ["600000", "600000", "000001"]
        assert all(r["technical_available"] for r in rows)
        assert calls == ["600000", "000001"], "同一代码重复持仓不该重复计算"

    def test_no_subprocess_fork_per_holding(self, tmp_path, monkeypatch):
        from custos.pipeline.holdings import batch_holding_technical as bht

        bht.clear_analysis_cache()
        monkeypatch.setattr(
            bht.subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("每持仓 fork 了一个进程")
            ),
        )
        monkeypatch.setattr(
            bht,
            "analyze_code",
            lambda code, name: {"available": True, "latest_date": "d"},
        )
        monkeypatch.setattr(bht, "HOLD", tmp_path)
        rows = bht.build_summary([{"code": "600000", "name": "A"}], "2026-07-20")
        assert rows[0]["technical_available"] is True

    def test_analysis_error_degrades_per_code(self, tmp_path, monkeypatch):
        from custos.pipeline.holdings import batch_holding_technical as bht

        bht.clear_analysis_cache()
        monkeypatch.setattr(
            bht,
            "analyze_code",
            lambda code, name: (_ for _ in ()).throw(RuntimeError("bad kline")),
        )
        monkeypatch.setattr(bht, "HOLD", tmp_path)
        rows = bht.build_summary([{"code": "600000", "name": "A"}], "2026-07-20")
        assert rows[0]["technical_available"] is False
        assert "bad kline" in str(rows[0]["technical_error"])
