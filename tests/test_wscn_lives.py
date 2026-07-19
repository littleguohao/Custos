# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import re
import unittest
from datetime import datetime, timezone

from news.rss_collector import parse_wscn_lives

SRC = {
    "id": "wscn_lives",
    "name": "华尔街见闻-A股快讯",
    "category": "cn_financial_media",
    "url": "https://api.wallstreetcn.com/apiv1/content/lives?channel=a-stock-channel&limit=100",
    "tier": "B",
    "type": "wscn_lives",
}
FETCHED = "2026-07-19T10:30:00+08:00"

FIXTURE = {
    "code": 20000,
    "data": {
        "items": [
            {
                "id": 123456,
                "content": "<p>【A股快讯】沪指午后涨逾1%,半导体板块领涨。</p>",
                "display_time": 1784272800,
                "uri": "https://wallstreetcn.com/livenews/123456",
                "channels": ["a-stock-channel"],
            },
            {
                "id": 123457,
                "content": "",
                "display_time": 1784272860,
                "uri": "https://wallstreetcn.com/livenews/123457",
            },
            {
                "id": 123458,
                "content": "<b>央行开展500亿元逆回购操作。</b>",
            },
        ]
    },
}


class ParseWscnLivesTests(unittest.TestCase):
    def parse(self, payload=FIXTURE):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return parse_wscn_lives(raw, SRC, FETCHED)

    def test_mapping_and_skips(self):
        items = self.parse()
        self.assertEqual(len(items), 2)  # 空 content 条目被跳过
        first = items[0]
        expected_id = hashlib.sha256(b"wscn_lives|123456").hexdigest()[:24]
        self.assertEqual(first["item_id"], expected_id)
        self.assertEqual(first["title"], "【A股快讯】沪指午后涨逾1%,半导体板块领涨。")
        self.assertEqual(first["summary"], "【A股快讯】沪指午后涨逾1%,半导体板块领涨。")
        self.assertEqual(
            first["published_at"],
            datetime.fromtimestamp(1784272800, timezone.utc).isoformat(),
        )
        self.assertEqual(first["source_url"], "https://wallstreetcn.com/livenews/123456")
        self.assertEqual(first["feed_url"], SRC["url"])
        self.assertEqual(first["source_id"], "wscn_lives")
        self.assertEqual(first["source_tier"], "B")
        self.assertEqual(first["quality"], "candidate")
        self.assertFalse(first["confirmed"])
        self.assertEqual(first["direction"], "uncertain")
        self.assertEqual(first["fetched_at"], FETCHED)
        # 缺 display_time 的条目保留但 published_at 为 None(filter 会排除)
        self.assertIsNone(items[1]["published_at"])
        self.assertEqual(items[1]["title"], "央行开展500亿元逆回购操作。")
        # 实测 API 无 uri 字段:按 id 构造 wallstreetcn.com/livenews/{id}
        self.assertEqual(items[1]["source_url"], "https://wallstreetcn.com/livenews/123458")

    def test_duplicate_group_id_matches_content_norm(self):
        first = self.parse()[0]
        norm = re.sub(r"\W+", "", first["summary"].lower())[:300]
        self.assertEqual(first["duplicate_group_id"], hashlib.sha256(norm.encode()).hexdigest()[:20])

    def test_bad_code_raises(self):
        with self.assertRaises(ValueError):
            self.parse({"code": 40001, "message": "bad channel"})

    def test_malformed_payload_raises(self):
        with self.assertRaises(ValueError):
            self.parse({"code": 20000, "data": {}})
        with self.assertRaises(ValueError):
            parse_wscn_lives(b'["not a dict"]', SRC, FETCHED)


if __name__ == "__main__":
    unittest.main()
