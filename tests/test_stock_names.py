# -*- coding: utf-8 -*-
"""stock_names 回归测试：ST 判定的数据基础。

名称是硬排除 ST 的唯一依据（enrich 用 `"ST" in name.upper()`），仓库里没有别的 ST
判据（tq_sector 的板块分类不含风险警示板）。所以这张表的**可用性与新鲜度**直接决定
ST 股会不会进候选池。

原实现的隐蔽失效：只有 mootdx 一个在线源且它 2026-07 起持续失败 ⇒ 系统长期靠一份手动
生成、永不更新、读取时不校验时效的缓存在跑 ⇒ 缓存非空就报 st_filter=ok，而旧缓存里
新被 ST 的票名字还是正常的，照样通过硬排除。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from custos.datasource.local_tdx import stock_names as sn
from custos.core.paths import cn_today

# conftest 有 autouse fixture 把网络层函数替换成"发请求即断言失败"的哨兵。本文件要测
# 这些函数本身，所以在模块导入期（fixture 生效之前）先抓住真实实现。
_REAL_FETCH_NAMES = sn.fetch_names_for
_REAL_CLIST = sn.fetch_all_from_clist
_REAL_RESOLVE = (
    sn.resolve_names_for
)  # conftest 连这个也 stub 了(它是 formula_screen 的入口)


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """按 host 决定行为：可指定哪些 host 抛异常，用于验证多域名轮询。"""

    def __init__(self, rows_by_batch=None, dead_hosts=(), total=None):
        self.rows_by_batch = list(rows_by_batch or [])
        self.dead_hosts = set(dead_hosts)
        self.total = total
        self.calls = []

    def get(self, url, params=None, timeout=None, proxies=None):
        host = url.split("//", 1)[1].split("/", 1)[0]
        self.calls.append((host, dict(params or {})))
        if host in self.dead_hosts:
            raise ConnectionError("Remote end closed connection without response")
        rows = self.rows_by_batch.pop(0) if self.rows_by_batch else []
        data = {"diff": rows}
        if self.total is not None:
            data["total"] = self.total
        return FakeResp({"data": data})


def _row(code, name):
    return {"f12": code, "f14": name}


@pytest.fixture(autouse=True)
def _clean_host_cache():
    sn.reset_host_cache()
    yield
    sn.reset_host_cache()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """跳过限速/退避的真实等待。

    fetch_names_for 每批之间 sleep 0.6s，_em_get 每次重试退避 1.2s×n。多域名轮询用例
    会真的等 3~4 秒，整个文件因此要跑 8.5s——那是白等，不是在验证任何行为。
    """
    monkeypatch.setattr(sn.time, "sleep", lambda *_a, **_k: None)


class TestSecidMapping:
    """secid 前缀必须复用 code_utils.market_of —— 手写"9 开头即沪市"会误判北交所。"""

    @pytest.mark.parametrize(
        "code,expect",
        [
            ("600000", "1.600000"),  # 沪主板
            ("688111", "1.688111"),  # 科创板
            ("900001", "1.900001"),  # 沪 B
            ("000001", "0.000001"),  # 深主板
            ("300750", "0.300750"),  # 创业板
            ("920819", "0.920819"),  # 北交所新代码段 —— 首字符也是 9，最易误判
            ("830799", "0.830799"),  # 北交所旧代码段
            ("430047", "0.430047"),  # 北交所旧代码段
        ],
    )
    def test_secid(self, code, expect):
        assert sn._secid(code) == expect

    def test_bj_920_not_treated_as_shanghai(self):
        """留证：这是实测踩到的 bug —— 1.920819 在东财查不到，表现为该票 ST 状态未知。"""
        assert sn._secid("920819").startswith("0."), "920xxx 是北交所，不是沪 B"

    def test_accepts_suffixed_code(self):
        assert sn._secid("600000.SH") == "1.600000"


class TestFetchNamesFor:
    """ulist 批量：只查传入的代码，按 batch 分批。"""

    def test_single_batch(self):
        s = FakeSession([[_row("600000", "浦发银行"), _row("000005", "ST星源")]])
        got = _REAL_FETCH_NAMES(["600000", "000005"], session=s)
        assert got == {"600000": "浦发银行", "000005": "ST星源"}
        assert len(s.calls) == 1

    def test_splits_into_batches(self):
        codes = [f"60{i:04d}" for i in range(450)]
        s = FakeSession([[_row(c, f"股{c}")] for c in codes[:3]] + [[], []])
        _REAL_FETCH_NAMES(codes, session=s, batch=200)
        assert len(s.calls) == 3, "450 只 / batch 200 → 3 批"

    def test_only_requested_codes_queried(self):
        s = FakeSession([[_row("600000", "浦发银行")]])
        _REAL_FETCH_NAMES(["600000"], session=s)
        assert s.calls[0][1]["secids"] == "1.600000"

    def test_dedupes_and_normalizes(self):
        s = FakeSession([[_row("600000", "浦发银行")]])
        _REAL_FETCH_NAMES(["600000", "600000.SH", "6000"], session=s)
        secids = s.calls[0][1]["secids"].split(",")
        assert sorted(secids) == ["0.006000", "1.600000"]  # 6000→006000 属深市

    def test_empty_request_no_call(self):
        s = FakeSession()
        assert _REAL_FETCH_NAMES([], session=s) == {}
        assert s.calls == []

    def test_missing_codes_simply_absent(self):
        """东财没有的标的不会出现在结果里——调用方据此算覆盖率，不得当成"名称为空"。"""
        s = FakeSession([[_row("600000", "浦发银行")]])
        got = _REAL_FETCH_NAMES(["600000", "999999"], session=s)
        assert "999999" not in got

    def test_no_data_section_raises(self):
        class S(FakeSession):
            def get(self, url, params=None, timeout=None, proxies=None):
                return FakeResp({"data": None})

        with pytest.raises(sn.NameFetchIncomplete):
            _REAL_FETCH_NAMES(["600000"], session=S())


class TestHostRotation:
    """单域名故障不该让整个数据源失效（实测 push2 全挂而 push2delay 可用）。"""

    def test_falls_over_to_next_host(self):
        s = FakeSession(
            [[_row("600000", "浦发银行")]], dead_hosts={"push2delay.eastmoney.com"}
        )
        got = _REAL_FETCH_NAMES(["600000"], session=s)
        assert got == {"600000": "浦发银行"}
        assert s.calls[0][0] == "push2delay.eastmoney.com"  # 先试默认首选
        assert s.calls[-1][0] != "push2delay.eastmoney.com"  # 失败后换域名

    def test_all_hosts_dead_raises(self):
        s = FakeSession(dead_hosts=set(sn.EM_HOSTS))
        with pytest.raises(sn.NameFetchIncomplete, match="全部域名不可用"):
            _REAL_FETCH_NAMES(["600000"], session=s)

    def test_working_host_is_remembered(self):
        s = FakeSession(
            [[_row("600000", "浦发银行")], [_row("000001", "平安银行")]],
            dead_hosts={"push2delay.eastmoney.com"},
        )
        _REAL_FETCH_NAMES(["600000"], session=s)
        first_ok = sn._working_host
        assert first_ok and first_ok != "push2delay.eastmoney.com"
        s.calls.clear()
        _REAL_FETCH_NAMES(["000001"], session=s)
        assert s.calls[0][0] == first_ok, "应直接用上次成功的域名，不再重试已知不通的"


class TestCache:
    """缓存必须带 generated_at，且读取时判时效。"""

    def test_save_then_load_roundtrip(self, tmp_path):
        p = tmp_path / "names.json"
        sn.save_cache({"600000": "浦发银行"}, "eastmoney_ulist", p)
        names, meta = sn.load_cache(p)
        assert names == {"600000": "浦发银行"}
        assert meta["available"] is True and meta["stale"] is False
        assert meta["source"] == "eastmoney_ulist" and meta["age_days"] == 0

    def test_save_refuses_empty(self, tmp_path):
        with pytest.raises(ValueError, match="empty name map"):
            sn.save_cache({}, "x", tmp_path / "n.json")

    def test_save_is_atomic(self, tmp_path):
        p = tmp_path / "names.json"
        sn.save_cache({"600000": "浦发银行"}, "x", p)
        assert not list(tmp_path.glob("*.tmp"))

    def test_stale_when_old(self, tmp_path):
        p = tmp_path / "names.json"
        old = (cn_today() - timedelta(days=sn.NAME_MAP_MAX_AGE_DAYS + 1)).isoformat()
        p.write_text(
            json.dumps({"generated_at": old, "names": {"600000": "浦发银行"}}),
            encoding="utf-8",
        )
        _, meta = sn.load_cache(p)
        assert meta["stale"] is True and meta["age_days"] > sn.NAME_MAP_MAX_AGE_DAYS

    def test_boundary_not_stale(self, tmp_path):
        p = tmp_path / "names.json"
        at = (cn_today() - timedelta(days=sn.NAME_MAP_MAX_AGE_DAYS)).isoformat()
        p.write_text(
            json.dumps({"generated_at": at, "names": {"600000": "浦发银行"}}),
            encoding="utf-8",
        )
        _, meta = sn.load_cache(p)
        assert meta["stale"] is False, "恰好等于上限不算陈旧"

    def test_legacy_flat_format_is_stale(self, tmp_path):
        """旧扁平格式无 generated_at ⇒ 时效未知 ⇒ 不假定新鲜。"""
        p = tmp_path / "names.json"
        p.write_text(json.dumps({"600000": "浦发银行"}), encoding="utf-8")
        names, meta = sn.load_cache(p)
        assert names == {"600000": "浦发银行"}
        assert meta["stale"] is True and meta["age_days"] is None
        assert meta["source"] == "legacy_cache"

    def test_missing_file(self, tmp_path):
        names, meta = sn.load_cache(tmp_path / "nope.json")
        assert names == {} and meta["available"] is False
        assert meta["reason"].startswith("cache_unreadable")

    def test_malformed_file(self, tmp_path):
        p = tmp_path / "names.json"
        p.write_text("not json", encoding="utf-8")
        names, meta = sn.load_cache(p)
        assert names == {} and meta["stale"] is True

    def test_empty_names_treated_unavailable(self, tmp_path):
        p = tmp_path / "names.json"
        p.write_text(
            json.dumps({"generated_at": cn_today().isoformat(), "names": {}}),
            encoding="utf-8",
        )
        names, meta = sn.load_cache(p)
        assert names == {} and meta["available"] is False


class TestResolveNamesFor:
    """st_filter 四态必须如实反映 ST 判定可信度——调用方据此决定能否声称已排除 ST。"""

    def _patch_sources(
        self, monkeypatch, *, em=None, tq=None, cache=None, cache_meta=None, tdx=None
    ):
        # TDX 协议是 2026-08-04 起的主路径；默认给空表 = 模拟 TDX 不可用，
        # 这样既有用例（验证 HTTP/TQ/缓存回退链）的语义保持不变。
        monkeypatch.setattr(sn, "fetch_from_tdx_protocol", lambda **kw: tdx or {})
        monkeypatch.setattr(
            sn,
            "fetch_names_for",
            (lambda codes, **kw: em)
            if em is not None
            else (
                lambda codes, **kw: (_ for _ in ()).throw(
                    sn.NameFetchIncomplete("down")
                )
            ),
        )
        monkeypatch.setattr(sn, "fetch_from_tq", lambda codes=None, **kw: tq or {})
        monkeypatch.setattr(
            sn, "load_cache", lambda path=None: (cache or {}, cache_meta or {})
        )

    def test_ok_when_all_resolved(self, monkeypatch):
        self._patch_sources(monkeypatch, em={"600000": "浦发银行", "000005": "ST星源"})
        names, diag = _REAL_RESOLVE(["600000", "000005"])
        assert diag["st_filter"] == "ok" and diag["missing_count"] == 0
        assert names["000005"] == "ST星源"

    def test_partial_when_some_missing(self, monkeypatch, capsys):
        self._patch_sources(monkeypatch, em={"600000": "浦发银行"})
        _, diag = _REAL_RESOLVE(["600000", "999999"])
        assert diag["st_filter"] == "partial"
        assert diag["missing_codes"] == ["999999"]
        assert "ST 状态未知" in capsys.readouterr().err

    def test_unavailable_when_nothing_resolved(self, monkeypatch, capsys):
        self._patch_sources(monkeypatch)
        names, diag = _REAL_RESOLVE(["600000"])
        assert names == {} and diag["st_filter"] == "unavailable"
        assert "ST 硬排除失效" in capsys.readouterr().err

    def test_falls_back_to_tq(self, monkeypatch):
        self._patch_sources(monkeypatch, tq={"600000": "浦发银行"})
        names, diag = _REAL_RESOLVE(["600000"])
        assert names == {"600000": "浦发银行"}
        assert "tq_local" in diag["name_map_source"]

    def test_stale_when_only_stale_cache(self, monkeypatch, capsys):
        self._patch_sources(
            monkeypatch,
            cache={"600000": "浦发银行"},
            cache_meta={
                "available": True,
                "stale": True,
                "age_days": 99,
                "generated_at": "2026-04-01",
            },
        )
        names, diag = _REAL_RESOLVE(["600000"])
        assert names == {"600000": "浦发银行"}
        assert diag["st_filter"] == "stale" and diag["name_map_age_days"] == 99
        assert "新被 ST 的票可能不在表内" in capsys.readouterr().err

    def test_fresh_cache_is_ok(self, monkeypatch):
        self._patch_sources(
            monkeypatch,
            cache={"600000": "浦发银行"},
            cache_meta={"available": True, "stale": False, "age_days": 2},
        )
        _, diag = _REAL_RESOLVE(["600000"])
        assert diag["st_filter"] == "ok"

    def test_empty_request_is_ok(self, monkeypatch):
        self._patch_sources(monkeypatch)
        names, diag = _REAL_RESOLVE([])
        assert names == {} and diag["st_filter"] == "ok"

    def test_sources_are_combined(self, monkeypatch):
        """东财缺的由 TQ 补，TQ 也缺的由缓存补。"""
        self._patch_sources(
            monkeypatch,
            em={"600000": "浦发银行"},
            tq={"000005": "ST星源"},
            cache={"300750": "宁德时代"},
            cache_meta={"available": True, "stale": False, "age_days": 1},
        )
        names, diag = _REAL_RESOLVE(["600000", "000005", "300750"])
        assert len(names) == 3 and diag["st_filter"] == "ok"
        assert "eastmoney_ulist" in diag["name_map_source"]


class TestClistBestEffort:
    """clist 全市场受限流，取到多少算多少；一条都没取到才抛。落盘路径另有覆盖率门槛。"""

    def test_partial_pages_kept(self):
        s = FakeSession(
            [[_row("600000", "浦发银行")], [_row("000001", "平安银行")]], total=5888
        )
        got = _REAL_CLIST(session=s)
        assert len(got) == 2, "被限流断连后保留已取部分"

    def test_nothing_fetched_raises(self):
        s = FakeSession([[]], total=5888)
        with pytest.raises(sn.NameFetchIncomplete, match="一条未取到"):
            _REAL_CLIST(session=s)

    def test_below_coverage_threshold_raises(self):
        """要落盘当全量表的调用方必须设 min_coverage：残缺表落盘比不更新更危险
        （覆盖完整缓存 + generated_at 刷新 → 30 天时效计时被重置）。"""
        s = FakeSession(
            [[_row("600000", "浦发银行")], [_row("000001", "平安银行")]], total=5888
        )
        with pytest.raises(sn.NameFetchIncomplete, match="覆盖率不足"):
            _REAL_CLIST(session=s, min_coverage=sn.CLIST_MIN_COVERAGE)

    def test_at_coverage_threshold_kept(self):
        """恰好达到门槛（800/1000）不抛——边界口径是 len < total*coverage 才拒绝。"""
        pages = [
            [_row(f"60{p:02d}{i:02d}", f"股{p}{i}") for i in range(100)]
            for p in range(8)
        ]
        s = FakeSession(pages, total=1000)
        got = _REAL_CLIST(session=s, min_coverage=sn.CLIST_MIN_COVERAGE)
        assert len(got) == 800

    def test_unknown_total_not_rejected(self):
        """接口没报告 total 时无法判覆盖率，保持尽力而为（不抛）。"""
        s = FakeSession([[_row("600000", "浦发银行")], []], total=None)
        got = _REAL_CLIST(session=s, min_coverage=sn.CLIST_MIN_COVERAGE)
        assert len(got) == 1


class TestTdxProtocolIsPrimary:
    """owner 原则：尽量用本地 TDX 接口，HTTP 不稳定（2026-08-04）。

    背景：TDX 名称源曾被判定「2026-07 起持续失败（'>' NoneType）」并改走东财 HTTP，
    真实原因是 local_tdx_data._get_client() 永不重连——连接一断 stock_count() 返回
    None，mootdx 内部 `if counts > 0` 就抛 TypeError。接口本身完好。
    """

    def _patch(self, monkeypatch, *, tdx=None, em=None, tq=None, cache=None):
        monkeypatch.setattr(sn, "fetch_from_tdx_protocol", lambda **kw: tdx or {})
        monkeypatch.setattr(sn, "fetch_names_for", lambda codes, **kw: em or {})
        monkeypatch.setattr(sn, "fetch_from_tq", lambda codes=None, **kw: tq or {})
        monkeypatch.setattr(
            sn,
            "load_cache",
            lambda path=None: (cache or {}, {"available": bool(cache), "stale": False}),
        )

    def test_tdx_wins_when_available(self, monkeypatch):
        """TDX 有数据时不该再走 HTTP。"""
        called = []
        monkeypatch.setattr(
            sn, "fetch_from_tdx_protocol", lambda **kw: {"600000": "浦发银行"}
        )
        monkeypatch.setattr(
            sn, "fetch_names_for", lambda codes, **kw: called.append(codes) or {}
        )
        monkeypatch.setattr(sn, "fetch_from_tq", lambda codes=None, **kw: {})
        monkeypatch.setattr(sn, "load_cache", lambda path=None: ({}, {}))
        names, diag = _REAL_RESOLVE(["600000"])
        assert names == {"600000": "浦发银行"}
        assert diag["name_map_source"] == "tdx_protocol"
        assert called == [], "TDX 已覆盖全部候选，不该再发 HTTP 请求"

    def test_http_only_fills_what_tdx_lacks(self, monkeypatch):
        """北交所（TDX 服务器不提供）才由东财补——HTTP 请求里只该有缺的那些票。"""
        asked = []

        def _em(codes, **kw):
            asked.extend(codes)
            return {"920819": "颖泰生物"}

        monkeypatch.setattr(
            sn,
            "fetch_from_tdx_protocol",
            lambda **kw: {"600000": "浦发银行", "300750": "宁德时代"},
        )
        monkeypatch.setattr(sn, "fetch_names_for", _em)
        monkeypatch.setattr(sn, "fetch_from_tq", lambda codes=None, **kw: {})
        monkeypatch.setattr(sn, "load_cache", lambda path=None: ({}, {}))
        names, diag = _REAL_RESOLVE(["600000", "300750", "920819"])
        assert len(names) == 3 and diag["st_filter"] == "ok"
        assert asked == ["920819"], f"只该问 TDX 缺的票，实际问了 {asked}"
        assert "tdx_protocol" in diag["name_map_source"]
        assert "eastmoney_ulist" in diag["name_map_source"]

    def test_st_recognized_from_tdx(self, monkeypatch):
        """ST 判定靠名称，TDX 源必须能支撑它。"""
        self._patch(monkeypatch, tdx={"000005": "*ST美丽", "600000": "浦发银行"})
        names, diag = _REAL_RESOLVE(["000005", "600000"])
        assert "ST" in names["000005"].upper()
        assert diag["st_filter"] == "ok"

    def test_degrades_to_http_when_tdx_down(self, monkeypatch):
        """TDX 挂了要能回退 HTTP，不能因为改了优先级就失去冗余。"""

        def _boom(**kw):
            raise RuntimeError("tdx down")

        monkeypatch.setattr(sn, "fetch_from_tdx_protocol", _boom)
        monkeypatch.setattr(
            sn, "fetch_names_for", lambda codes, **kw: {"600000": "浦发银行"}
        )
        monkeypatch.setattr(sn, "fetch_from_tq", lambda codes=None, **kw: {})
        monkeypatch.setattr(sn, "load_cache", lambda path=None: ({}, {}))
        names, diag = _REAL_RESOLVE(["600000"])
        assert names == {"600000": "浦发银行"}
        assert diag["st_filter"] == "ok"

    def test_full_map_prefers_tdx(self, monkeypatch):
        monkeypatch.setattr(
            sn, "fetch_from_tdx_protocol", lambda **kw: {"600000": "浦发银行"}
        )
        m, src = sn.fetch_name_map()
        assert src == "tdx_protocol" and m


class TestClistCoverageGuard:
    """fetch_name_map / resolve_name_map：clist 残缺表不得落盘覆盖完整缓存。"""

    def _patch_sources(self, monkeypatch, clist_exc):
        monkeypatch.setattr(sn, "fetch_from_tq", lambda *a, **kw: {})

        def _clist(*a, **kw):
            raise clist_exc

        monkeypatch.setattr(sn, "fetch_all_from_clist", _clist)
        monkeypatch.setattr(sn, "fetch_from_mootdx", lambda: {})

    def test_fetch_name_map_passes_coverage_threshold(self, monkeypatch):
        monkeypatch.setattr(sn, "fetch_from_tdx_protocol", lambda **kw: {})
        """auto 路径调 clist 必须带 min_coverage（否则残缺表会被当成功结果落盘）。"""
        seen = {}
        monkeypatch.setattr(sn, "fetch_from_tq", lambda *a, **kw: {})
        monkeypatch.setattr(
            sn,
            "fetch_all_from_clist",
            lambda *a, **kw: seen.update(kw) or {"600000": "浦发银行"},
        )
        monkeypatch.setattr(sn, "fetch_from_mootdx", lambda: {})
        m, source = sn.fetch_name_map()
        assert source == "eastmoney_clist" and m
        assert seen.get("min_coverage") == sn.CLIST_MIN_COVERAGE

    def test_partial_clist_falls_back_to_cache_without_overwrite(self, monkeypatch):
        monkeypatch.setattr(sn, "fetch_from_tdx_protocol", lambda **kw: {})
        """TdxW 关 + clist 覆盖率不足 → 回退旧缓存，且**绝不**调 save_cache。"""
        self._patch_sources(
            monkeypatch,
            sn.NameFetchIncomplete(
                "clist 覆盖率不足: 1000/5888 (<80%)，拒绝当全量表落盘"
            ),
        )
        monkeypatch.setattr(
            sn,
            "load_cache",
            lambda *a, **kw: (
                {"600000": "浦发银行"},
                {
                    "available": True,
                    "stale": False,
                    "age_days": 2,
                    "generated_at": "2026-08-01",
                    "source": "tq_local",
                },
            ),
        )
        monkeypatch.setattr(
            sn,
            "save_cache",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("残缺/未更新时不允许落盘覆盖缓存")
            ),
        )
        names, diag = sn.resolve_name_map()
        assert names == {"600000": "浦发银行"}
        assert diag["st_filter"] == "ok"
        assert diag["name_map_source"] == "tq_local"


from custos.datasource.local_tdx import local_tdx_data  # noqa: E402


class TestGetStockListAShareFilter:
    """`get_stock_list()` 默认只返回 A 股个股（2026-08-06 加）。

    ⚠️ 原实现返回**全部** 51567 项（实测），含 `999999` 等指数、ETF、债券，
    而沪深 A 股个股只有约 5300 只。而 `backtest_factors:2285`——不传
    `--universe-local` 时的 universe 源——**没有任何下游过滤**，直接
    `sample_codes(base, N, seed)` ⇒ 抽样时约 89% 的概率抽到非个股。

    案底：`1d0d7de` 的提交信息就是「universe 改用本地 vipdoc 枚举，
    **修复回测 16.7% 覆盖率**」，`eab500a` 又专门修文档里漏传 `--universe-local` 的命令。
    """

    def _fake_client(self, codes):
        import pandas as pd

        class _C:
            def stocks(self, market):
                # 只在 market==1(SH) 返回，避免同一批被计两次
                if market != 1:
                    return pd.DataFrame()
                return pd.DataFrame({"code": codes})

        return _C()

    def test_filters_indices_etf_bonds(self, monkeypatch, capsys):
        codes = [
            "600000",
            "601398",
            "688001",  # 沪 A
            "000001",
            "002415",
            "300750",
            "301001",  # 深 A
            "999999",
            "999998",  # 指数
            "510300",
            "159915",  # ETF
            "113050",
            "128036",  # 可转债
            "880005",
        ]  # 板块指数
        monkeypatch.setattr(
            local_tdx_data,
            "_get_client",
            lambda force_new=False: self._fake_client(codes),
        )
        got = local_tdx_data.get_stock_list()
        assert got == [
            "600000",
            "601398",
            "688001",
            "000001",
            "002415",
            "300750",
            "301001",
        ], got
        assert "滤掉 7 项" in capsys.readouterr().err

    def test_raw_list_available_on_demand(self, monkeypatch):
        """需要原始全表时仍可取——过滤是默认值，不是能力删除。"""
        codes = ["600000", "999999", "510300"]
        monkeypatch.setattr(
            local_tdx_data,
            "_get_client",
            lambda force_new=False: self._fake_client(codes),
        )
        assert local_tdx_data.get_stock_list(ashare_only=False) == codes

    def test_prefix_rule_matches_vipdoc_rule(self):
        """与 `_is_ashare_stock_file` 的沪深两段规则必须一致，否则两个 universe
        源口径不同，而它们会被拿来互相对照。"""
        for p in (
            "600",
            "601",
            "603",
            "605",
            "688",
            "000",
            "001",
            "002",
            "003",
            "300",
            "301",
        ):
            code6 = p + "000"[: 6 - len(p)] + "0" * (6 - len(p) - 3)
            code6 = (p + "000000")[:6]
            mkt = "sh" if p.startswith(("60", "68")) else "sz"
            assert local_tdx_data._is_ashare_prefix(code6) is True, code6
            assert local_tdx_data._is_ashare_stock_file(mkt, code6) is True, code6
        for bad in ("999999", "510300", "159915", "113050", "880005"):
            assert local_tdx_data._is_ashare_prefix(bad) is False, bad


class TestNameMapOverwriteGuard:
    """⚠️ **落盘覆盖门槛**（2026-08-07 新增）：残缺表不得覆盖完整缓存。

    实测的事故形态：TQ 只成功取到 3 只（TdxW 忙 / 连接抖动），那 3 只照样落盘
    覆盖了 5000 只的完整缓存、`generated_at` 刷新成当天（30 天时效计时重置），
    而 `st_filter` 报 **ok** ⇒ **ST 硬排除静默失效**。

    原先门槛只加在 `fetch_all_from_clist` 上，可它在源顺序里**排最后**；
    排在前面的 `fetch_from_tdx_protocol`（沪深两市，一市失败就剩一半）与
    `fetch_from_tq`（逐只 RPC，部分失败照样返回）都没有门槛。
    ⇒ 门槛移到**落盘边界**（`resolve_name_map` 与 `main()` 两条落盘路径的
    `save_cache` 之前——生产每日刷新走的是 main()，只挂前者等于没挂）。
    """

    @staticmethod
    def _write_cache(tmp_path, n, days_ago=0):
        p = tmp_path / "stock_name_map.json"
        names = {str(600000 + i): f"票{i}" for i in range(n)}
        gen = (cn_today() - timedelta(days=days_ago)).isoformat()
        p.write_text(
            json.dumps(
                {
                    "generated_at": gen,
                    "source": "eastmoney",
                    "count": n,
                    "names": names,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return p

    def _run(self, tmp_path, monkeypatch, cache_n, new_n, days_ago=0):
        p = self._write_cache(tmp_path, cache_n, days_ago)
        new = {str(600000 + i): f"新{i}" for i in range(new_n)}
        monkeypatch.setattr(sn, "CACHE", p)
        monkeypatch.setattr(
            sn, "fetch_name_map", lambda session=None: (new, "tq_local")
        )
        names, diag = sn.resolve_name_map()
        return names, diag, json.loads(p.read_text(encoding="utf-8"))

    def test_tiny_table_refused(self, tmp_path, monkeypatch):
        """3 只不得覆盖 5000 只 —— 缓存必须原封不动。"""
        names, diag, after = self._run(tmp_path, monkeypatch, 5000, 3)
        assert after["count"] == 5000
        assert len(names) == 5000, "应回退到缓存而不是用那 3 只"
        assert "拒绝覆盖" in diag.get("name_map_rejected_reason", "")
        assert diag.get("name_map_rejected_size") == 3

    def test_at_threshold_accepted(self, tmp_path, monkeypatch):
        """恰好 80%（4000/5000）放行 —— 退市会让 universe 自然缩小，
        门槛不能把正常波动也拦住。"""
        _n, diag, after = self._run(tmp_path, monkeypatch, 5000, 4000)
        assert after["count"] == 4000 and "name_map_rejected_reason" not in diag

    def test_just_below_threshold_refused(self, tmp_path, monkeypatch):
        _n, _d, after = self._run(tmp_path, monkeypatch, 5000, 3999)
        assert after["count"] == 5000

    def test_larger_table_accepted(self, tmp_path, monkeypatch):
        _n, _d, after = self._run(tmp_path, monkeypatch, 4000, 5000)
        assert after["count"] == 5000

    def test_first_build_accepted(self, tmp_path, monkeypatch):
        """首次构建（无缓存）一律放行 —— 否则永远建不起来。"""
        monkeypatch.setattr(sn, "CACHE", tmp_path / "stock_name_map.json")
        monkeypatch.setattr(
            sn, "fetch_name_map", lambda session=None: ({"600000": "浦发"}, "tq_local")
        )
        names, diag = sn.resolve_name_map()
        assert len(names) == 1 and diag["st_filter"] == "ok"

    def test_stale_but_complete_preferred_over_fresh_partial(
        self, tmp_path, monkeypatch
    ):
        """⚠️ 既有缓存**陈旧**时仍拒绝残缺表。

        一份陈旧但完整的表比一份新鲜但残缺的表更适合做 ST 排除 ——
        ST 名称变动不频繁，而残缺表会让大部分票**根本查不到名字**。
        陈旧本身会被 `load_cache` 判出来、`st_filter="stale"`，信息不会丢。
        """
        names, diag, after = self._run(tmp_path, monkeypatch, 5000, 3, days_ago=99)
        assert after["count"] == 5000 and len(names) == 5000
        assert diag["st_filter"] == "stale", "陈旧要如实报出"

    def test_main_refuses_tiny_table(self, tmp_path, monkeypatch, capsys):
        """🔴 直接打 main()：生产每日刷新的实际路径，门槛必须同样挂在它的落盘前。

        review 发现门槛一度只挂在 `resolve_name_map` 里，而 main() 直接 `save_cache`
        ——「3 只残缺表覆盖 5000 只完整缓存」的事故形态在这条路径上原样可复现。
        拒绝时必须非零退出并打清原因，缓存原封不动。
        """
        p = self._write_cache(tmp_path, 5000)
        tiny = {str(600000 + i): f"新{i}" for i in range(3)}
        monkeypatch.setattr(sn, "CACHE", p)
        monkeypatch.setattr(
            sn, "fetch_name_map", lambda session=None: (tiny, "tq_local")
        )
        rc = sn.main(["--out", str(p)])
        assert rc != 0, "残缺表必须拒绝落盘且非零退出"
        after = json.loads(p.read_text(encoding="utf-8"))
        assert after["count"] == 5000, "缓存必须原封不动"
        out = capsys.readouterr().out
        assert "overwrite_refused" in out and "拒绝覆盖" in out

    def test_main_accepts_complete_table(self, tmp_path, monkeypatch):
        """对照组：新表不小于门槛时 main() 正常落盘（门槛不误伤正常刷新）。"""
        p = self._write_cache(tmp_path, 5000)
        full = {str(600000 + i): f"新{i}" for i in range(5000)}
        monkeypatch.setattr(sn, "CACHE", p)
        monkeypatch.setattr(
            sn, "fetch_name_map", lambda session=None: (full, "tdx_protocol")
        )
        rc = sn.main(["--out", str(p)])
        assert rc == 0
        assert json.loads(p.read_text(encoding="utf-8"))["count"] == 5000
        assert json.loads(p.read_text(encoding="utf-8"))["source"] == "tdx_protocol"
