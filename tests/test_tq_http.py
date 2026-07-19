# -*- coding: utf-8 -*-
"""tq_http 单测：mock HTTP 层覆盖两种响应形态、ErrorId 非 0、连接失败等。"""
from __future__ import annotations

import json
import unittest
import urllib.error
from unittest import mock

import tq_http


def _body(obj: dict) -> bytes:
    return json.dumps(obj).encode("utf-8")


class CallTest(unittest.TestCase):
    def setUp(self) -> None:
        # 默认视为 TdxW 在运行，单测聚焦 HTTP/解析层
        patcher = mock.patch.object(tq_http, "is_tdxw_running", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_value_shape(self) -> None:
        """Value 形态：返回 result.Value。"""
        resp = {"id": 1, "result": {"ErrorId": "0", "Value": {"TPFlag": "0", "ZTPrice": "33.00"}}}
        with mock.patch.object(tq_http, "_post", return_value=_body(resp)):
            out = tq_http.call("get_more_info", {"stock_code": "600150.SH"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["value"], {"TPFlag": "0", "ZTPrice": "33.00"})
        self.assertIsNone(out["error"])

    def test_value_shape_array(self) -> None:
        """Value 为数组（get_match_stkinfo）时原样返回。"""
        resp = {"id": 1, "result": {"ErrorId": "0", "Value": [{"Code": "600000.SH", "Name": "浦发银行"}]}}
        with mock.patch.object(tq_http, "_post", return_value=_body(resp)):
            out = tq_http.call("get_match_stkinfo", {"key_word": "浦发"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["value"][0]["Code"], "600000.SH")

    def test_direct_shape(self) -> None:
        """直挂形态（snapshot）：value 为 result 去掉 ErrorId。"""
        resp = {"id": 1, "result": {"ErrorId": "0", "Now": "3764.15", "UpHome": "202", "DownHome": "2119"}}
        with mock.patch.object(tq_http, "_post", return_value=_body(resp)):
            out = tq_http.call("get_market_snapshot", {"stock_code": "999999.SH"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["value"], {"Now": "3764.15", "UpHome": "202", "DownHome": "2119"})

    def test_error_id_nonzero(self) -> None:
        resp = {"id": 1, "result": {"ErrorId": "1001", "Value": None}}
        with mock.patch.object(tq_http, "_post", return_value=_body(resp)):
            out = tq_http.call("get_more_info", {"stock_code": "BAD"})
        self.assertFalse(out["ok"])
        self.assertIsNone(out["value"])
        self.assertEqual(out["error"]["code"], "tq_error")

    def test_connection_failure(self) -> None:
        with mock.patch.object(tq_http, "_post", side_effect=urllib.error.URLError("refused")):
            out = tq_http.call("get_market_snapshot", {"stock_code": "999999.SH"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "connection_failed")

    def test_generic_request_failure(self) -> None:
        with mock.patch.object(tq_http, "_post", side_effect=TimeoutError("timed out")):
            out = tq_http.call("get_stock_info", {"stock_code": "600000.SH"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "timeout")

    def test_invalid_json(self) -> None:
        with mock.patch.object(tq_http, "_post", return_value=b"not-json"):
            out = tq_http.call("get_stock_info", {"stock_code": "600000.SH"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "invalid_response")

    def test_jsonrpc_error_field(self) -> None:
        with mock.patch.object(tq_http, "_post", return_value=_body({"id": 1, "error": {"code": -1}})):
            out = tq_http.call("get_stock_info", {"stock_code": "600000.SH"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "jsonrpc_error")

    def test_tdxw_not_running(self) -> None:
        with mock.patch.object(tq_http, "is_tdxw_running", return_value=False), \
             mock.patch.object(tq_http, "_post") as post:
            out = tq_http.call("get_stock_info", {"stock_code": "600000.SH"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "tdxw_not_running")
        post.assert_not_called()


class ConvenienceTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(tq_http, "is_tdxw_running", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _captured(self, resp_obj: dict):
        seen = {}

        def fake_post(payload, timeout):
            seen.update(payload)
            return _body(resp_obj)

        return seen, fake_post

    def test_snapshot_params(self) -> None:
        seen, fake = self._captured({"id": 1, "result": {"ErrorId": "0", "Now": "1.0"}})
        with mock.patch.object(tq_http, "_post", side_effect=fake):
            out = tq_http.snapshot("880006.SH")
        self.assertTrue(out["ok"])
        self.assertEqual(seen["method"], "get_market_snapshot")
        self.assertEqual(seen["params"], {"stock_code": "880006.SH"})

    def test_more_info_fields(self) -> None:
        seen, fake = self._captured({"id": 1, "result": {"ErrorId": "0", "Value": {"TPFlag": "0"}}})
        with mock.patch.object(tq_http, "_post", side_effect=fake):
            out = tq_http.more_info("600150.SH", fields=["TPFlag", "ZTPrice"])
        self.assertTrue(out["ok"])
        self.assertEqual(seen["method"], "get_more_info")
        self.assertEqual(seen["params"]["field_list"], ["TPFlag", "ZTPrice"])

    def test_stock_info_params(self) -> None:
        seen, fake = self._captured({"id": 1, "result": {"ErrorId": "0", "Value": {"Name": "浦发银行"}}})
        with mock.patch.object(tq_http, "_post", side_effect=fake):
            out = tq_http.stock_info("600000.SH")
        self.assertTrue(out["ok"])
        self.assertEqual(seen["method"], "get_stock_info")

    def test_ping_uses_match_stkinfo(self) -> None:
        seen, fake = self._captured({"id": 1, "result": {"ErrorId": "0", "Value": []}})
        with mock.patch.object(tq_http, "_post", side_effect=fake):
            out = tq_http.ping()
        self.assertTrue(out["ok"])
        self.assertEqual(seen["method"], "get_match_stkinfo")


@unittest.skipUnless(tq_http.ping()["ok"], "TQ-Local 服务不可达，跳过集成测试")
class IntegrationTest(unittest.TestCase):
    def test_snapshot_index(self) -> None:
        out = tq_http.snapshot("999999.SH")
        self.assertTrue(out["ok"], out["error"])
        self.assertIn("Now", out["value"])

    def test_stock_info(self) -> None:
        out = tq_http.stock_info("600000.SH")
        self.assertTrue(out["ok"], out["error"])
        self.assertIn("J_start", out["value"])


if __name__ == "__main__":
    unittest.main()
