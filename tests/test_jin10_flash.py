# -*- coding: utf-8 -*-
"""jin10_mcp（金十快讯）钉测：SSE 拆包 / structuredContent 抽取 / 翻页 / 归一化。

口径来源：2026-08-21 对 https://mcp.jin10.com/mcp 的冒烟实测——
initialize 响应头给 mcp-session-id；tools/call list_flash 返回
result.structuredContent.data.{items,next_cursor,has_more}（item 含
content/time/url，id/title 可能缺省）；样例文档分页键写作 next_offset，
两个键都认。
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from custos.datasource.news import rss_collector as rc

SRC = {
    "id": "jin10_flash",
    "name": "金十数据-快讯",
    "category": "cn_financial_media",
    "url": "https://mcp.jin10.com/mcp",
    "tier": "B",
    "type": "jin10_mcp",
}
FETCHED = "2026-08-21T08:50:00+08:00"

ENTRIES = [
    {
        "id": "20260821083000953800",
        "title": "",
        "content": "【金十图示】2026年08月21日（周五）早盘交易提示。",
        "time": "2026-08-21T08:30:00+08:00",
        "url": "https://flash.jin10.com/detail/20260821083000953800",
    },
    {
        # id/title 缺省：id 从 url 末段推导，title 取 content 前 50 字
        "content": "央行开展 500 亿元逆回购操作，中标利率持平。",
        "time": "2026-08-21T08:35:00+08:00",
        "url": "https://flash.jin10.com/detail/20260821083500953801",
    },
    {
        "id": "20260821084000953802",
        "content": "",  # 空 content 跳过
        "time": "2026-08-21T08:40:00+08:00",
        "url": "https://flash.jin10.com/detail/20260821084000953802",
    },
    {
        "id": "bad-time",
        "content": "时间字段损坏的条目保留但 published_at 为 None。",
        "time": "not-a-date",
        "url": "",
    },
]


class TestParseJin10Flash:
    def parse(self, entries=ENTRIES):
        return rc.parse_jin10_flash(entries, SRC, FETCHED)

    def test_mapping_and_skips(self):
        items = self.parse()
        assert len(items) == 3  # 空 content 条目被跳过
        first = items[0]
        expected_id = hashlib.sha256(b"jin10_flash|20260821083000953800").hexdigest()[
            :24
        ]
        assert first["item_id"] == expected_id
        assert first["title"] == "【金十图示】2026年08月21日（周五）早盘交易提示。"
        assert first["summary"] == first["title"]
        # +08:00 → UTC
        assert first["published_at"] == "2026-08-21T00:30:00+00:00"
        assert (
            first["source_url"] == "https://flash.jin10.com/detail/20260821083000953800"
        )
        assert first["feed_url"] == SRC["url"]
        assert first["source_id"] == "jin10_flash"
        assert first["source_tier"] == "B"
        assert first["quality"] == "candidate"
        assert first["confirmed"] is False
        assert first["transport_verified"] is True
        assert first["direction"] == "uncertain"
        assert first["fetched_at"] == FETCHED

    def test_id_and_title_fallbacks(self):
        second = self.parse()[1]
        expected_id = hashlib.sha256(
            b"jin10_flash|20260821083500953801"  # url 末段推导
        ).hexdigest()[:24]
        assert second["item_id"] == expected_id
        assert second["title"] == "央行开展 500 亿元逆回购操作，中标利率持平。"

    def test_bad_time_kept_with_none_published(self):
        third = self.parse()[2]
        assert third["published_at"] is None
        # url 也缺时按 id 构造 flash.jin10.com/detail/{id}
        assert third["source_url"] == "https://flash.jin10.com/detail/bad-time"

    def test_duplicate_group_id_matches_content_norm(self):
        first = self.parse()[0]
        norm = re.sub(r"\W+", "", first["summary"].lower())[:300]
        assert (
            first["duplicate_group_id"]
            == hashlib.sha256(norm.encode()).hexdigest()[:20]
        )


class TestSseDataMessages:
    def test_sse_multiline(self):
        raw = (
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
            'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"x":1}}\n\n'
        ).encode("utf-8")
        msgs = rc._sse_data_messages(raw)
        assert [m["id"] for m in msgs] == [1, 2]

    def test_plain_json_fallback(self):
        raw = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        assert rc._sse_data_messages(raw) == [
            {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        ]


def _tool_msg(items, *, has_more=False, cursor_key="next_cursor", cursor="c1"):
    data = {"items": items, "has_more": has_more}
    data[cursor_key] = cursor
    return {
        "jsonrpc": "2.0",
        "id": 100,
        "result": {"structuredContent": {"data": data}},
    }


class TestExtractData:
    def test_structured_content_preferred(self):
        msg = _tool_msg([{"content": "a"}])
        assert rc._jin10_extract_data(msg)["items"] == [{"content": "a"}]

    def test_content_text_fallback(self):
        inner = json.dumps({"data": {"items": [{"content": "b"}], "has_more": False}})
        msg = {
            "jsonrpc": "2.0",
            "id": 100,
            "result": {"content": [{"type": "text", "text": inner}]},
        }
        assert rc._jin10_extract_data(msg)["items"] == [{"content": "b"}]

    def test_jsonrpc_error_raises(self):
        with pytest.raises(ValueError, match="JSON-RPC error"):
            rc._jin10_extract_data(
                {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601}}
            )

    def test_is_error_raises(self):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "今日该工具调用次数已达上限"}],
            },
        }
        with pytest.raises(ValueError, match="调用次数已达上限"):
            rc._jin10_extract_data(msg)

    def test_empty_message_raises(self):
        with pytest.raises(ValueError, match="空响应"):
            rc._jin10_extract_data(None)


class TestFetchPagination:
    def _fake_rpc(self, pages, recorded):
        """pages: list of tools/call 返回消息；recorded 收集 (method, arguments, sid)。"""

        def fake(url, token, ctx, timeout, payload, session_id=None):
            method = payload["method"]
            if method == "initialize":
                recorded.append((method, None, session_id))
                return {"jsonrpc": "2.0", "id": 1, "result": {"ok": 1}}, "SID-1"
            if method == "notifications/initialized":
                recorded.append((method, None, session_id))
                return None, None
            args = payload["params"]["arguments"]
            recorded.append((method, args, session_id))
            return pages[len([r for r in recorded if r[0] == "tools/call"]) - 1], None

        return fake

    def test_paginates_until_no_more(self, monkeypatch):
        pages = [
            _tool_msg([{"content": "p1"}], has_more=True, cursor="c1"),
            _tool_msg([{"content": "p2"}], has_more=False, cursor="c2"),
        ]
        recorded = []
        monkeypatch.setattr(rc, "_jin10_rpc", self._fake_rpc(pages, recorded))
        entries = rc.fetch_jin10_flash("https://mcp.jin10.com/mcp", "tok", None, 5)
        assert [e["content"] for e in entries] == ["p1", "p2"]
        calls = [r for r in recorded if r[0] == "tools/call"]
        # 首页不带 cursor，次页带上一页的 next_cursor；握手后的请求都带 session id
        assert calls[0][1] == {} and calls[1][1] == {"cursor": "c1"}
        assert all(r[2] == "SID-1" for r in recorded if r[0] != "initialize")
        assert recorded[0][0] == "initialize"
        assert recorded[1][0] == "notifications/initialized"

    def test_next_offset_alias_accepted(self, monkeypatch):
        """样例文档分页键是 next_offset（实测是 next_cursor）——两个键都认。"""
        pages = [
            _tool_msg(
                [{"content": "p1"}],
                has_more=True,
                cursor_key="next_offset",
                cursor="o1",
            ),
            _tool_msg([{"content": "p2"}], has_more=False),
        ]
        recorded = []
        monkeypatch.setattr(rc, "_jin10_rpc", self._fake_rpc(pages, recorded))
        entries = rc.fetch_jin10_flash("https://mcp.jin10.com/mcp", "tok", None, 5)
        assert len(entries) == 2
        calls = [r for r in recorded if r[0] == "tools/call"]
        assert calls[1][1] == {"cursor": "o1"}

    def test_initialize_failure_raises(self, monkeypatch):
        monkeypatch.setattr(
            rc,
            "_jin10_rpc",
            lambda *a, **k: ({"jsonrpc": "2.0", "id": 1, "error": {}}, None),
        )
        with pytest.raises(ValueError, match="握手失败"):
            rc.fetch_jin10_flash("https://mcp.jin10.com/mcp", "tok", None, 5)
