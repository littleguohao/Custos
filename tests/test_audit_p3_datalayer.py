# -*- coding: utf-8 -*-
"""审计第四批（数据采集层 C3~C7）回归测试。

统一不变量：**缺数据/坏数据不得表现为好数据**。每个用例都断言"异常被传导给下游"，
而不是断言源码长什么样。全部用 monkeypatch / 临时目录，不发真实网络请求。

  C3 get_snapshot 缺字段回落 0.0  → 无有效价返回空快照
  C4 东财分页把限流空响应当翻完了  → FetchIncomplete + 主动限速
  C5 指南针 0AMV 选错链却报 ok    → latest_amv 透出 identification/quality
  C6 概念标签缓存不校验 mtime      → 过期文件不得盖今日日期
  C7 空 DataFrame 三义             → TDX_ROOT 配错必须炸，缺文件带 missing_reason
"""
from __future__ import annotations

import datetime as dt
import json
import os
import struct
import time
from pathlib import Path

import pandas as pd
import pytest

import compass_amv
import concept_tags
import fetch_market_cap as mc
import fetch_pit_financials as pit
import local_tdx_data as ltd


# ===================== C3 get_snapshot 缺字段回落 0.0 =====================

class _FakeQuotesClient:
    """mootdx Quotes 替身：quotes() 直接返回预置 DataFrame。"""

    def __init__(self, rows):
        self.rows = rows

    def quotes(self, symbol=None):
        return pd.DataFrame(self.rows)


@pytest.fixture()
def fake_quotes(monkeypatch):
    """注入假 quotes 客户端。

    ⚠️ 同时开 `TDX_ONLINE_QUOTES` —— 在线行情自 2026-08-06 起默认标记为不可用
    （`_online_quotes_enabled`，实测 bars/index 约 10~13s 返回空）。
    本组测试测的是 `get_snapshot` 的**字段解析契约**（0 价→空、有效价→返回），
    不是在线开关；不开这个环境变量的话，`get_snapshot` 会在触到假客户端之前就短路，
    于是「0 价返回空」这类断言会**因为错误的原因通过**。
    """
    monkeypatch.setenv("TDX_ONLINE_QUOTES", "1")

    def _install(rows):
        monkeypatch.setattr(ltd, "_get_client", lambda: _FakeQuotesClient(rows))
    return _install


class TestSnapshotZeroPrice:
    """C3: 0 价当真值 → 涨跌幅 -100%（误触风控卖出）或 last_close=0 除零。"""

    def test_missing_price_field_returns_empty(self, fake_quotes):
        fake_quotes([{"code": "600000", "last_close": 10.0}])   # 服务端漏 price 字段
        assert ltd.get_snapshot("600000") == {}

    def test_nan_price_returns_empty(self, fake_quotes):
        fake_quotes([{"code": "600000", "price": float("nan"), "last_close": 10.0}])
        assert ltd.get_snapshot("600000") == {}

    def test_zero_price_returns_empty(self, fake_quotes):
        """停牌/未开盘常见 0 价：必须表现为"没有报价"，不能表现为"价格是 0"。"""
        fake_quotes([{"code": "600000", "price": 0, "last_close": 10.0}])
        assert ltd.get_snapshot("600000") == {}

    def test_valid_price_still_returned(self, fake_quotes):
        fake_quotes([{"code": "600000", "price": 10.5, "last_close": 10.0,
                      "open": 10.1, "high": 10.6, "low": 10.0}])
        snap = ltd.get_snapshot("600000")
        assert snap["price"] == 10.5 and snap["last_close"] == 10.0

    def test_invalid_last_close_is_none_not_zero(self, fake_quotes):
        """last_close 缺失时给 None：给 0.0 会让下游 price/last_close-1 直接除零。"""
        fake_quotes([{"code": "600000", "price": 10.5, "last_close": 0}])
        snap = ltd.get_snapshot("600000")
        assert snap["price"] == 10.5
        assert snap["last_close"] is None

    def test_batch_drops_invalid_keeps_valid(self, fake_quotes):
        fake_quotes([
            {"code": "600000", "price": 10.5, "last_close": 10.0},
            {"code": "000001", "price": 0.0, "last_close": 12.0},        # 停牌
            {"code": "300001", "price": float("nan"), "last_close": 8.0},
        ])
        got = ltd.get_snapshots(["600000", "000001", "300001"])
        assert set(got) == {"600000"}
        assert got["600000"]["price"] == 10.5

    def test_empty_response_still_empty(self, fake_quotes):
        fake_quotes([])
        assert ltd.get_snapshot("600000") == {}
        assert ltd.get_snapshots(["600000"]) == {}


# ===================== C4 东财分页残缺 =====================

class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class _FakePagedSession:
    """按页号返回预置 payload 的 requests.Session 替身。"""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls: list[int] = []

    def get(self, url, params=None, headers=None, timeout=None, proxies=None):
        page = params["pageNumber"]
        self.calls.append(page)
        return _FakeResp(self.pages[page])


def _page(data, pages=1, count=None):
    return {"success": True, "code": 0,
            "result": {"data": data, "pages": pages,
                       "count": count if count is not None else len(data)}}


_EMPTY_OK = {"success": True, "code": 0, "message": "ok", "result": None}


def _rows(n, start=0):
    return [{"SECURITY_CODE": f"{600000 + start + i:06d}", "TOTAL_SHARES": 1e10,
             "SECURITY_NAME_ABBR": "测试股", "CLOSE_PRICE": 10.0,
             "TOTAL_MARKET_CAP": 1e11, "SECURITY_TYPE": "A股",
             "NOTICE_DATE": "2024-04-29 00:00:00"} for i in range(n)]


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """限速 sleep 不应让测试变慢，但要能观察到它被调用。"""
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    return slept


class TestPagedFetchIncomplete:
    """C4: `if not data: break` 把限流空响应当"翻完了"，残缺样本按 [OK] 落盘。"""

    @pytest.mark.parametrize("mod,fn", [(pit, "fetch_period"), (mc, "fetch_trade_date")])
    def test_empty_page_midway_raises(self, mod, fn):
        """声明 3 页，第 2 页空 → 限流，绝不能当成翻完了。"""
        s = _FakePagedSession({1: _page(_rows(2), pages=3, count=6),
                               2: _page([], pages=3, count=6)})
        with pytest.raises(mod.FetchIncomplete):
            getattr(mod, fn)("2024-06-28", page_size=2, session=s)

    @pytest.mark.parametrize("mod,fn", [(pit, "fetch_period"), (mc, "fetch_trade_date")])
    def test_row_count_short_of_declared_raises(self, mod, fn):
        """pages 说翻完了，但 count 声明 6 行只拿到 2 行 → 残缺。"""
        s = _FakePagedSession({1: _page(_rows(2), pages=1, count=6)})
        with pytest.raises(mod.FetchIncomplete):
            getattr(mod, fn)("2024-06-28", page_size=2, session=s)

    @pytest.mark.parametrize("mod,fn", [(pit, "fetch_period"), (mc, "fetch_trade_date")])
    def test_missing_result_section_raises(self, mod, fn):
        """限流常见形态：200 + 无 result 段（且不自称成功）。"""
        s = _FakePagedSession({1: {"success": False, "code": 429, "message": "too many"}})
        with pytest.raises(mod.FetchIncomplete):
            getattr(mod, fn)("2024-06-28", session=s)

    @pytest.mark.parametrize("mod,fn", [(pit, "fetch_period"), (mc, "fetch_trade_date")])
    def test_max_pages_exhausted_raises(self, mod, fn):
        """声明 9 页但 max_pages=2 → 只拿到前两页，必须报残缺而不是静默截断。"""
        s = _FakePagedSession({1: _page(_rows(2), pages=9, count=18),
                               2: _page(_rows(2, 2), pages=9, count=18)})
        with pytest.raises(mod.FetchIncomplete):
            getattr(mod, fn)("2024-06-28", page_size=2, max_pages=2, session=s)

    @pytest.mark.parametrize("mod,fn", [(pit, "fetch_period"), (mc, "fetch_trade_date")])
    def test_complete_multipage_ok(self, mod, fn):
        s = _FakePagedSession({1: _page(_rows(2), pages=2, count=4),
                               2: _page(_rows(2, 2), pages=2, count=4)})
        rows = getattr(mod, fn)("2024-06-28", page_size=2, session=s)
        assert len(rows) == 4 and s.calls == [1, 2]

    @pytest.mark.parametrize("mod,fn", [(pit, "fetch_period"), (mc, "fetch_trade_date")])
    def test_genuinely_empty_is_not_an_error(self, mod, fn):
        """非交易日/未披露报告期：接口自称成功且 result 为 null → 空列表，不抛。"""
        s = _FakePagedSession({1: _EMPTY_OK})
        assert getattr(mod, fn)("2024-06-29", session=s) == []

    @pytest.mark.parametrize("mod,fn", [(pit, "fetch_period"), (mc, "fetch_trade_date")])
    def test_sleeps_between_pages(self, mod, fn, _no_real_sleep):
        """主动限速：翻页之间必须 sleep，否则连打几十页会被静默限流。"""
        s = _FakePagedSession({1: _page(_rows(2), pages=2, count=4),
                               2: _page(_rows(2, 2), pages=2, count=4)})
        getattr(mod, fn)("2024-06-28", page_size=2, session=s)
        assert _no_real_sleep and all(x > 0 for x in _no_real_sleep)


class TestMarketCapIncompleteNotPersisted:
    """C4 加重版：残缺样本进 diff_events 会把未返回的票当"股本未变"污染台账。"""

    def test_incomplete_day_is_not_sampled_or_written(self, tmp_path, capsys, monkeypatch):
        ledger = tmp_path / "sc.jsonl"
        ledger.write_text(json.dumps({"code": "600000", "observed_on": "2024-06-28",
                                      "prev_sample": None, "total_shares": 1e10,
                                      "kind": "first_seen"}) + "\n", encoding="utf-8")
        samples = tmp_path / "samples.json"
        samples.write_text(json.dumps({"sampled": ["2024-06-28"], "empty": []}),
                           encoding="utf-8")

        def boom(d, session=None, **_):
            raise mc.FetchIncomplete(f"{d} 只拿到 500/5300 行")

        monkeypatch.setattr(mc, "fetch_trade_date", boom)
        rc = mc.main(["--dates", "2024-07-28", "--out", str(ledger),
                      "--samples-out", str(samples)])
        assert rc == 0                                   # best-effort，不炸管线
        assert len(mc.load_events(ledger)) == 1          # 台账未被污染
        saved = json.loads(samples.read_text(encoding="utf-8"))
        assert saved["sampled"] == ["2024-06-28"]        # 未记为已采样 → 可重跑
        assert "2024-07-28" not in saved["empty"]        # 也不能记成"已知空日期"
        assert "残缺" in capsys.readouterr().err


class TestPitIncompleteNotPersisted:
    def test_incomplete_period_not_written(self, tmp_path, capsys, monkeypatch):
        out = tmp_path / "pit.jsonl"

        def boom(p, session=None, **_):
            raise pit.FetchIncomplete(f"{p} 只拿到 500/5544 行")

        monkeypatch.setattr(pit, "fetch_period", boom)
        rc = pit.main(["--periods", "2024-03-31", "--out", str(out)])
        assert rc == 0
        assert not out.exists()                          # 残缺期不落盘
        assert "残缺" in capsys.readouterr().err


# ===================== C5 指南针 0AMV 选错链却报 ok =====================

_HEADER = b"\x00" * 16
_GAP = b"\x00" * 28


def _amv_record(d: dt.date, c: float) -> bytes:
    return struct.pack("<I6f", d.year * 10000 + d.month * 100 + d.day,
                       c - 0.5, c + 1.0, c - 1.0, c, 1e11, 2e12)


def _amv_series(start: dt.date, closes: list) -> bytes:
    return b"".join(_amv_record(start + dt.timedelta(days=i), c)
                    for i, c in enumerate(closes))


@pytest.fixture()
def vdat_two_chains(tmp_path):
    """两条链：WRONG（更新更长）与 TRUE（靠真值可辨认）。"""
    wrong_start, true_start = dt.date(2026, 5, 10), dt.date(2026, 6, 1)
    wrong_closes = [500.0] * 60
    true_closes = [100.0 + (i % 5) * 2 for i in range(30)]
    path = tmp_path / compass_amv.DAY_VDAT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_HEADER + _GAP.join([_amv_series(wrong_start, wrong_closes),
                                          _amv_series(true_start, true_closes)]))
    truth = tmp_path / "truth.jsonl"
    lines = []
    prev = None
    for i, c in enumerate(true_closes):
        pct = None if prev is None else round((c / prev - 1) * 100, 2)
        if i >= 20:
            lines.append(json.dumps({"date": (true_start + dt.timedelta(days=i)).isoformat(),
                                     "amv_change_pct": pct, "quality": "confirmed"}))
        prev = c
    truth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"root": str(tmp_path), "truth": str(truth),
            "no_truth": str(tmp_path / "nope.jsonl"),
            "wrong_start": wrong_start, "true_start": true_start}


class TestLatestAmvQuality:
    """C5: 选错链 → sync 写 confirmed → regime 切换 → 授予加仓权。用别的指数驱动仓位。"""

    def test_fallback_marked_unverified(self, vdat_two_chains):
        out = compass_amv.latest_amv(root=vdat_two_chains["root"],
                                     truth_path=vdat_two_chains["no_truth"])
        assert out["ok"] is True
        assert out["quality"] == "unverified"
        assert "fallback" in out["identification"]

    def test_truth_matched_marked_verified(self, vdat_two_chains):
        out = compass_amv.latest_amv(root=vdat_two_chains["root"],
                                     truth_path=vdat_two_chains["truth"])
        assert out["quality"] == "verified"
        assert out["identification"].startswith("truth_match")

    def test_parse_exposes_quality_too(self, vdat_two_chains):
        parsed = compass_amv.parse_amv_daily(since="1990-01-01",
                                             root=vdat_two_chains["root"],
                                             truth_path=vdat_two_chains["no_truth"])
        assert parsed["quality"] == "unverified"

    def test_series_start_exposed_for_attribution(self, vdat_two_chains):
        """选错链时至少能从 series_start 看出选了哪条（归因用）。"""
        out = compass_amv.latest_amv(root=vdat_two_chains["root"],
                                     truth_path=vdat_two_chains["no_truth"])
        assert out["series_start"] == vdat_two_chains["wrong_start"].isoformat()

    def test_error_path_has_no_false_quality(self, tmp_path):
        out = compass_amv.latest_amv(root=str(tmp_path / "nope"))
        assert out["ok"] is False and out.get("quality") in (None, "unavailable")


# ===================== C6 概念标签缓存不校验 mtime =====================

@pytest.fixture()
def concept_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "PYPlugins" / "data"
    data_dir.mkdir(parents=True)
    src = data_dir / "miscinfo.json"
    src.write_text(json.dumps(
        [{"code": "600150", "xq": "船舶制造,军工", "id": "10001"}], ensure_ascii=False),
        encoding="utf-8")
    out = tmp_path / "stock_concept_tags.json"
    monkeypatch.setattr(concept_tags, "TDX_DATA_DIR", data_dir)
    monkeypatch.setattr(concept_tags, "OUT_PATH", out)
    return {"src": src, "out": out}


def _set_mtime(path: Path, days_ago: float) -> None:
    ts = time.time() - days_ago * 86400
    os.utime(path, (ts, ts))


def _ok_call(*_a, **_k):
    return {"ok": True, "value": None}


class TestConceptTagsStaleCache:
    """C6: TQ 返回 ok 但文件没更新（异步落盘/上周残留）→ 过期标签被盖今日日期。"""

    def test_stale_file_flagged_and_not_stamped_today(self, concept_env):
        _set_mtime(concept_env["src"], days_ago=7)
        today = concept_tags.cn_now().date().isoformat()
        r = concept_tags.refresh(today, call_fn=_ok_call)
        assert r["status"] == "stale"
        assert "stale" in r["degraded_reason"]
        payload = json.loads(concept_env["out"].read_text(encoding="utf-8"))
        assert payload["date"] != today          # 绝不给过期标签盖今日日期
        assert payload.get("stale") is True
        assert payload.get("requested_date") == today

    def test_rewritten_file_is_ok(self, concept_env):
        _set_mtime(concept_env["src"], days_ago=7)
        today = concept_tags.cn_now().date().isoformat()

        def rewriting_call(*_a, **_k):
            concept_env["src"].write_text(json.dumps(
                [{"code": "600150", "xq": "船舶制造,军工", "id": "10001"}],
                ensure_ascii=False), encoding="utf-8")
            return {"ok": True, "value": None}

        r = concept_tags.refresh(today, call_fn=rewriting_call)
        assert r["status"] == "ok" and r["stock_count"] == 1
        payload = json.loads(concept_env["out"].read_text(encoding="utf-8"))
        assert payload["date"] == today and not payload.get("stale")

    def test_same_day_file_without_rewrite_is_ok(self, concept_env):
        """当日已下过、TQ 不重复写盘：mtime 未变但就是当天的，算新鲜。"""
        _set_mtime(concept_env["src"], days_ago=0)
        today = concept_tags.cn_now().date().isoformat()
        r = concept_tags.refresh(today, call_fn=_ok_call)
        assert r["status"] == "ok"

    def test_missing_file_still_unavailable(self, concept_env):
        concept_env["src"].unlink()
        r = concept_tags.refresh("2026-07-21", call_fn=_ok_call)
        assert r["status"] == "unavailable" and "miscinfo_missing" in r["degraded_reason"]

    def test_cli_returns_nonzero_when_degraded(self, concept_env, monkeypatch, capsys):
        _set_mtime(concept_env["src"], days_ago=7)
        today = concept_tags.cn_now().date().isoformat()
        monkeypatch.setattr(concept_tags, "refresh",
                            lambda d, **_: {"date": d, "status": "stale",
                                            "degraded_reason": "miscinfo_stale:x"})
        monkeypatch.setattr("sys.argv", ["concept_tags.py", "--date", today])
        assert concept_tags.main() == 1


# ===================== C7 空 DataFrame 三义 =====================

class TestTdxRootValidation:
    """C7: TDX_ROOT 在 Linux 上默认 E:\\new_tdx64，配错时表现为"全市场都没数据"。"""

    def test_missing_root_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path / "no_such_tdx")
        monkeypatch.setattr(ltd, "_tdx_root_verified", set())
        with pytest.raises(ltd.LocalTdxError) as e:
            ltd.read_vipdoc_daily("600000")
        assert "TDX_ROOT" in str(e.value)

    def test_root_without_vipdoc_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(ltd, "_tdx_root_verified", set())
        with pytest.raises(ltd.LocalTdxError) as e:
            ltd.read_vipdoc_daily("600000")
        assert "vipdoc" in str(e.value)

    def test_valid_root_passes_check(self, tmp_path, monkeypatch):
        (tmp_path / "vipdoc" / "sh" / "lday").mkdir(parents=True)
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(ltd, "_tdx_root_verified", set())
        ltd._assert_tdx_root()                       # 不抛即通过

    def test_universe_enumeration_refuses_broken_default_root(self, tmp_path, monkeypatch):
        """全市场 universe 为空 vs TDX_ROOT 配错，必须能区分（前者返回空，后者抛）。"""
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path / "no_such_tdx")
        monkeypatch.setattr(ltd, "_tdx_root_verified", set())
        with pytest.raises(ltd.LocalTdxError):
            ltd.list_local_vipdoc_codes()
        # 显式传 root 的调用方（回测/测试）自己负责，仍返回空列表
        assert ltd.list_local_vipdoc_codes(tdx_root=tmp_path) == []


class TestEmptyFrameReason:
    """C7: 空 DataFrame 同时表达"文件不存在/解析失败/确实无数据"。"""

    @pytest.fixture()
    def bj_root(self, tmp_path, monkeypatch):
        (tmp_path / "vipdoc" / "bj" / "lday").mkdir(parents=True)
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(ltd, "_tdx_root_verified", set())
        return tmp_path

    def test_missing_day_file_carries_reason(self, bj_root):
        df = ltd.read_vipdoc_daily("920001")
        assert df.empty
        assert df.attrs.get("missing_reason", "").startswith("file_not_found")

    def test_strict_mode_raises_on_missing_file(self, bj_root):
        with pytest.raises(ltd.LocalTdxError):
            ltd.read_vipdoc_daily("920001", strict=True)

    def test_reader_empty_carries_reason(self, tmp_path, monkeypatch):
        (tmp_path / "vipdoc" / "sh" / "lday").mkdir(parents=True)
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(ltd, "_tdx_root_verified", set())

        class _EmptyReader:
            def daily(self, symbol=None):
                return pd.DataFrame()

        monkeypatch.setattr(ltd, "_get_reader", lambda: _EmptyReader())
        df = ltd.read_vipdoc_daily("600000")
        assert df.empty and df.attrs.get("missing_reason") == "reader_empty:600000"
        with pytest.raises(ltd.LocalTdxError):
            ltd.read_vipdoc_daily("600000", strict=True)

    def test_real_data_has_no_reason(self, tmp_path, monkeypatch):
        (tmp_path / "vipdoc" / "sh" / "lday").mkdir(parents=True)
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path)
        monkeypatch.setattr(ltd, "_tdx_root_verified", set())

        class _Reader:
            def daily(self, symbol=None):
                idx = pd.date_range("2026-07-01", periods=3, freq="D")
                return pd.DataFrame({"open": [1.0] * 3, "high": [1.0] * 3,
                                     "low": [1.0] * 3, "close": [1.0] * 3,
                                     "amount": [1.0] * 3, "volume": [1.0] * 3}, index=idx)

        monkeypatch.setattr(ltd, "_get_reader", lambda: _Reader())
        df = ltd.read_vipdoc_daily("600000")
        assert len(df) == 3 and "missing_reason" not in df.attrs


class TestC7DoesNotBreakOnlineFallback:
    """C7 的护栏不能把在线回退一并掐死：报告链在无通达信的机器上仍要能跑。"""

    def test_get_ohlcv_table_falls_back_online_when_root_broken(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ltd, "TDX_ROOT", tmp_path / "no_such_tdx")
        monkeypatch.setattr(ltd, "_tdx_root_verified", set())
        online = pd.DataFrame({
            "date": pd.date_range("2026-07-01", periods=5, freq="D"),
            "open": [1.0] * 5, "high": [1.0] * 5, "low": [1.0] * 5,
            "close": [1.0] * 5, "volume": [1.0] * 5})
        monkeypatch.setattr(ltd, "get_online_bars", lambda c, offset=0: online)
        df = ltd.get_ohlcv_table("600000", count=5, adjust="none")
        assert len(df) == 5

    def test_module_annotations_resolve(self):
        """缺 Optional 导入时 get_type_hints 会 NameError（工具链/文档生成会炸）。"""
        import typing
        typing.get_type_hints(ltd.list_local_vipdoc_codes)
        typing.get_type_hints(ltd._assert_tdx_root)
        typing.get_type_hints(ltd._clean_price)
