"""`rss_collector` —— RSS/Atom 采集，**远端不可信输入的入口**。

覆盖率清点（2026-08-07）：51%、59 语句未覆盖，而未覆盖的正是几条安全不变量。

这个模块的特殊性：它是全项目唯一直接消费**第三方远端字节**的地方
（其余数据源都是通达信本地文件或已知 API）。所以测的重点不是「解析对不对」，
而是「**恶意或损坏的输入能造成多大伤害**」。
"""

from __future__ import annotations

import pathlib
import ssl

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from custos.datasource.news import rss_collector as rc  # noqa: E402

SRC = {
    "id": "s",
    "name": "源",
    "tier": "S",
    "category": "media",
    "url": "https://x.com/feed",
}


class TestSslPolicy:
    """⚠️ 关闭证书校验必须**显式承担风险**，不能靠一个布尔悄悄关掉。"""

    def test_default_verifies(self):
        ctx, verified = rc.build_ssl_context({"id": "s"})
        assert verified is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True

    def test_disabling_without_ack_is_refused(self):
        with pytest.raises(ValueError, match="ssl_insecure_ack"):
            rc.build_ssl_context({"id": "s", "ssl_verify": False})

    def test_disabling_with_ack_reports_unverified(self):
        """带 ack 时才允许，且**必须回报 transport_verified=False** ——
        下游据此拒绝把内容当既成事实。"""
        ctx, verified = rc.build_ssl_context(
            {"id": "s", "ssl_verify": False, "ssl_insecure_ack": True}
        )
        assert verified is False
        assert ctx.verify_mode == ssl.CERT_NONE and ctx.check_hostname is False


class TestTierQuality:
    """来源等级 → 证据质量。两个维度必须都成立才给 confirmed。"""

    @pytest.mark.parametrize(
        "tier,want",
        [
            ("S", "confirmed"),
            ("A", "confirmed"),
            ("B", "candidate"),
            ("C", "candidate"),
        ],
    )
    def test_tier_mapping(self, tier, want):
        assert rc._tier_quality(tier, True) == want

    @pytest.mark.parametrize("tier", ["S", "A", "B", "C"])
    def test_unverified_transport_never_confirmed(self, tier):
        """⚠️ 传输未经校验时**任何等级都不得 confirmed**。

        tier 说的是「这个机构说的话有多权威」，transport_verified 说的是
        「这段字节真的来自那个机构吗」。后者不成立时前者无意义。
        """
        assert rc._tier_quality(tier, False) == "candidate"


class TestEntityExpansion:
    """⚠️ 回归（2026-08-07 发现）：内部实体嵌套可放大内存。

    实测 `xml.etree.ElementTree` 两类实体攻击表现不同 ——
    外部实体（XXE）已被拒（`ParseError: undefined entity`），
    但**内部实体嵌套可行**：345 字节 4 层展开出 500 KB，再加两层 50 MB。
    `MAX_FEED_BYTES`（16 MB）只限输入大小，管不住展开后的内存。
    """

    NESTED = (
        '<?xml version="1.0"?>\n<!DOCTYPE r [\n'
        '<!ENTITY a "aaaaa">\n<!ENTITY b "&a;&a;&a;&a;&a;">\n]>\n'
        "<rss><channel><item><title>&b;</title></item></channel></rss>"
    )
    FLAT = (
        '<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY nbsp "&#160;">]>\n'
        "<rss><item><title>a&nbsp;b</title></item></rss>"
    )
    PLAIN = '<?xml version="1.0"?><rss><channel><item><title>t</title></item></channel></rss>'

    def test_nested_entity_refused(self):
        with pytest.raises(ValueError, match="billion-laughs"):
            rc.refuse_entity_expansion(self.NESTED)

    def test_flat_entity_allowed(self):
        """扁平声明是真实 feed 的合法用法且无放大能力 ——
        一律拒 `<!ENTITY` 会误杀正常源。"""
        rc.refuse_entity_expansion(self.FLAT)

    def test_no_doctype_allowed(self):
        rc.refuse_entity_expansion(self.PLAIN)

    def test_parse_feed_refuses_nested(self):
        """端到端：`parse_feed` 必须在 `ET.fromstring` **之前**拦下。"""
        with pytest.raises(ValueError, match="billion-laughs"):
            rc.parse_feed(self.NESTED.encode(), SRC, "2026-08-07T00:00:00+00:00")

    def test_external_entity_still_rejected_by_parser(self):
        """外部实体（读本地文件）本来就被解析器拒，这里钉住它别退化。"""
        xxe = (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
            "<rss><channel><item><title>&x;</title></item></channel></rss>"
        )
        with pytest.raises(Exception):
            rc.parse_feed(xxe.encode(), SRC, "2026-08-07T00:00:00+00:00")


class TestSizeLimit:
    def test_oversize_feed_refused(self):
        class R:
            def read(self, n):
                return b"x" * n

        with pytest.raises(ValueError, match="refused"):
            rc._read_limited(R())

    def test_normal_feed_passes(self):
        class R:
            def read(self, n):
                return b"ok"

        assert rc._read_limited(R()) == b"ok"


class TestParsing:
    FEED = (
        '<?xml version="1.0" encoding="utf-8"?><rss><channel>'
        "<item><title>标题 &amp; 摘要</title>"
        "<description>&lt;p&gt;正文  多空格&lt;/p&gt;</description>"
        "<link>https://x.com/a</link>"
        "<pubDate>Fri, 07 Aug 2026 08:00:00 +0800</pubDate></item>"
        "</channel></rss>"
    )

    def test_basic_item(self):
        items = rc.parse_feed(self.FEED.encode(), SRC, "2026-08-07T00:00:00+00:00")
        assert len(items) == 1
        it = items[0]
        assert it["title"] == "标题 & 摘要", "HTML 实体要还原"
        assert it["summary"] == "正文 多空格", "标签要剥掉、连续空白要压缩"
        assert it["published_at"].startswith("2026-08-07T00:00")

    def test_legacy_gb2312_decoded_as_gb18030(self):
        """⚠️ GB2312 声明按 **gb18030** 解 —— 后者是前者的超集。

        按字面 gb2312 解会在遇到扩展汉字时 replace 成乱码；
        而 ElementTree 本身也不接受这个声明，所以要显式解码并改写声明。
        """
        raw = (
            '<?xml version="1.0" encoding="gb2312"?><rss><channel><item>'
            "<title>浦发银行</title><link>https://x.com/a</link>"
            "</item></channel></rss>"
        ).encode("gb18030")
        items = rc.parse_feed(raw, SRC, "2026-08-07T00:00:00+00:00")
        assert items[0]["title"] == "浦发银行"

    def test_atom_link_href_fallback(self):
        """Atom 用 `<link href="...">` 而非文本节点 —— 取不到文本时回退读 href。"""
        feed = (
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><title>A</title><link href="https://x/1"/></entry></feed>'
        )
        items = rc.parse_feed(feed.encode(), SRC, "2026-08-07T00:00:00+00:00")
        assert items[0]["source_url"] == "https://x/1"

    def test_linkless_item_retained_with_empty_url(self):
        """无任何链接的条目**仍会保留**（不是跳过），`item_id` 退到标题哈希。

        代价：这种条目**人工无法核验**（报告里点不开）。下游 `rss_filter` 的
        URL 去重也会失效、只能靠标题近似去重。当前实现如此，这里如实钉住 ——
        若将来要求「证据必须可核验」，改动点就在这里。
        """
        feed = (
            '<?xml version="1.0"?><rss><channel><item><title>无链接标题</title>'
            "</item></channel></rss>"
        )
        items = rc.parse_feed(feed.encode(), SRC, "2026-08-07T00:00:00+00:00")
        assert len(items) == 1 and items[0]["source_url"] == ""
        assert items[0]["item_id"], "无链接也要有稳定 item_id（退到标题哈希）"

    def test_atom_entry_also_parsed(self):
        feed = (
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><title>A</title><link>https://x/1</link>"
            "<summary>s</summary></entry></feed>"
        )
        assert (
            len(
                rc.parse_feed(
                    feed.encode(), {**SRC, "tier": "A"}, "2026-08-07T00:00:00+00:00"
                )
            )
            == 1
        )

    def test_unverified_transport_marks_items_candidate(self):
        items = rc.parse_feed(
            self.FEED.encode(),
            SRC,
            "2026-08-07T00:00:00+00:00",
            transport_verified=False,
        )
        assert items[0]["quality"] == "candidate"
        assert items[0]["transport_verified"] is False


class TestHelpers:
    def test_clean_strips_tags_and_collapses_space(self):
        assert rc.clean("<b>a</b>   b\n c") == "a b c"
        assert rc.clean(None) == ""

    def test_iso_date_handles_rfc822_and_iso(self):
        assert rc.iso_date("Fri, 07 Aug 2026 08:00:00 +0800").startswith(
            "2026-08-07T00:00"
        )
        assert rc.iso_date("2026-08-07T00:00:00Z").startswith("2026-08-07T00:00")
        assert rc.iso_date("垃圾") is None and rc.iso_date(None) is None

    def test_redact_strips_query_string(self):
        """日志脱敏：异常信息里的 **query string** 去掉 —— feed URL 可能带 token/appkey。

        注册表里 6 个源有 1 个带 query string（wscn_lives）。
        """
        out = rc._redact("HTTPError: https://api.x.com/feed?token=abc123&k=v")
        assert "abc123" not in out and "<redacted>" in out

    def test_redact_scope_is_query_only(self):
        """⚠️ 如实记录**覆盖边界**：只脱敏 query string，**不**处理 URL 内嵌凭据
        （`https://user:pass@host/`）。

        现在注册表里 0 个源用内嵌凭据，所以没有补 —— 若将来加了需要 basic-auth
        的源，这条测试就是提醒：`_redact` 盖不住它，得先扩这个函数。
        """
        out = rc._redact("https://user:pw@example.com/feed")
        assert "pw@example.com" in out, "当前实现不脱敏 userinfo（已知边界）"


class TestTransientSslRetry:
    """v0.99：gov_cn/统计局源的 SSL CERTIFICATE_VERIFY_FAILED 实测是 CDN 边缘节点
    间歇返回坏证书链（直连 200、隔几分钟恢复）——RSS GET 改加长退避重试
    （tries=4 + jitter），瞬时失败不应把源判 failed。"""

    def _fake_resp(self):
        feed = (
            b'<?xml version="1.0"?><rss version="2.0"><channel>'
            b"<title>t</title><item><title>x</title>"
            b"<pubDate>Fri, 21 Aug 2026 08:00:00 +0800</pubDate></item>"
            b"</channel></rss>"
        )

        class R:
            status = 200
            headers = {"content-type": "application/rss+xml"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return feed

            def geturl(self):
                return "https://x.com/feed"

        return R()

    def test_transient_ssl_failure_retried_to_ok(self, monkeypatch, tmp_path):
        import json
        import urllib.error
        import ssl

        reg = tmp_path / "reg.json"
        reg.write_text(
            json.dumps({"sources": [dict(SRC, enabled=True)]}, ensure_ascii=False),
            encoding="utf-8",
        )
        monkeypatch.setattr(rc, "REG", reg)
        monkeypatch.setattr(rc, "DATA", tmp_path / "d")
        monkeypatch.setattr(rc, "LOG", tmp_path / "l")
        monkeypatch.setattr("custos.core.net_retry.time.sleep", lambda s: None)

        calls = {"n": 0}
        real_urlopen = rc.urllib.request.urlopen

        def flaky(req, timeout=None, context=None):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise urllib.error.URLError(
                    ssl.SSLCertVerificationError("certificate verify failed")
                )
            return self._fake_resp()

        monkeypatch.setattr(rc.urllib.request, "urlopen", flaky)
        seen_kwargs = {}
        real_retry = rc.retry_call

        def spy(func, **kw):
            seen_kwargs.update(kw)
            return real_retry(func, **kw)

        monkeypatch.setattr(rc, "retry_call", spy)
        monkeypatch.setattr(
            "sys.argv", ["rss_collector", "--date", "2026-08-21", "--timeout", "1"]
        )
        rc.main()
        log = json.loads(
            (tmp_path / "l" / "2026-08-21_collection_log.json").read_text("utf-8")
        )
        row = log["sources"][0]
        assert row["status"] == "ok" and row["items"] == 1
        assert calls["n"] == 3, "两次瞬时 SSL 失败后第三次成功"
        # 加长退避参数钉住：退化成默认 3 连快重试会重新撞坏节点
        assert seen_kwargs.get("tries") == 4 and seen_kwargs.get("jitter") == 0.5
