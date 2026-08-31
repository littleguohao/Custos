# -*- coding: utf-8 -*-
"""tq_http 单测：mock HTTP 层覆盖两种响应形态、ErrorId 非 0、连接失败等。"""

from __future__ import annotations

import json
import unittest
import urllib.error
from unittest import mock

import pytest

from custos.datasource.local_tdx import tq_http


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
        resp = {
            "id": 1,
            "result": {"ErrorId": "0", "Value": {"TPFlag": "0", "ZTPrice": "33.00"}},
        }
        with mock.patch.object(tq_http, "_post", return_value=_body(resp)):
            out = tq_http.call("get_more_info", {"stock_code": "600150.SH"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["value"], {"TPFlag": "0", "ZTPrice": "33.00"})
        self.assertIsNone(out["error"])

    def test_value_shape_array(self) -> None:
        """Value 为数组（get_match_stkinfo）时原样返回。"""
        resp = {
            "id": 1,
            "result": {
                "ErrorId": "0",
                "Value": [{"Code": "600000.SH", "Name": "浦发银行"}],
            },
        }
        with mock.patch.object(tq_http, "_post", return_value=_body(resp)):
            out = tq_http.call("get_match_stkinfo", {"key_word": "浦发"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["value"][0]["Code"], "600000.SH")

    def test_direct_shape(self) -> None:
        """直挂形态（snapshot）：value 为 result 去掉 ErrorId。"""
        resp = {
            "id": 1,
            "result": {
                "ErrorId": "0",
                "Now": "3764.15",
                "UpHome": "202",
                "DownHome": "2119",
            },
        }
        with mock.patch.object(tq_http, "_post", return_value=_body(resp)):
            out = tq_http.call("get_market_snapshot", {"stock_code": "999999.SH"})
        self.assertTrue(out["ok"])
        self.assertEqual(
            out["value"], {"Now": "3764.15", "UpHome": "202", "DownHome": "2119"}
        )

    def test_error_id_nonzero(self) -> None:
        resp = {"id": 1, "result": {"ErrorId": "1001", "Value": None}}
        with mock.patch.object(tq_http, "_post", return_value=_body(resp)):
            out = tq_http.call("get_more_info", {"stock_code": "600000.SH"})
        self.assertFalse(out["ok"])
        self.assertIsNone(out["value"])
        self.assertEqual(out["error"]["code"], "tq_error")

    def test_connection_failure(self) -> None:
        with mock.patch.object(
            tq_http, "_post", side_effect=urllib.error.URLError("refused")
        ):
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
        with mock.patch.object(
            tq_http, "_post", return_value=_body({"id": 1, "error": {"code": -1}})
        ):
            out = tq_http.call("get_stock_info", {"stock_code": "600000.SH"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "jsonrpc_error")

    def test_tdxw_not_running(self) -> None:
        with (
            mock.patch.object(tq_http, "is_tdxw_running", return_value=False),
            mock.patch.object(tq_http, "_post") as post,
        ):
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

        def fake_post(payload, timeout, endpoint=None):
            seen.update(payload)
            seen["_endpoint"] = endpoint
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
        seen, fake = self._captured(
            {"id": 1, "result": {"ErrorId": "0", "Value": {"TPFlag": "0"}}}
        )
        with mock.patch.object(tq_http, "_post", side_effect=fake):
            out = tq_http.more_info("600150.SH", fields=["TPFlag", "ZTPrice"])
        self.assertTrue(out["ok"])
        self.assertEqual(seen["method"], "get_more_info")
        self.assertEqual(seen["params"]["field_list"], ["TPFlag", "ZTPrice"])

    def test_stock_info_params(self) -> None:
        seen, fake = self._captured(
            {"id": 1, "result": {"ErrorId": "0", "Value": {"Name": "浦发银行"}}}
        )
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


class TestUnsafeDownTypeGuard:
    """`download_file` 的危险 down_type 必须被**代码**挡住，不能只写在注释里。

    背景：`TDX_LOCAL_INTERFACES.md` 有一节「TQ 服务可被打挂」的风险记录（原
    `concept_tags.py` 顶部也写着「只调用 down_type=4；禁止触碰 1/5/6」，该模块已随
    miscinfo 数据源在 v0.157 整删）。但 `call()` 是泛型入口——
    任何人写一行 `call("download_file", {"down_type": 1})` 都不会被挡，而 TdxW 一挂，
    选股链与持仓行情一起没了。

    本仓库反复踩的坑正是「写进文档不等于内化」（同一个连接反模式跨两天犯了三次），
    所以这条约束做成拦截 + 测试。
    """

    def test_safe_down_type_passes_guard(self, monkeypatch):
        """down_type=4 必须放行（白名单内唯一实测安全的类型）。"""
        seen = {}
        monkeypatch.setattr(tq_http, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(
            tq_http,
            "_post",
            lambda payload, timeout, endpoint=None: (
                seen.update(payload),
                b'{"result":{"ErrorId":"0","Value":1}}',
            )[1],
        )
        r = tq_http.call("download_file", {"down_type": 4})
        assert r["ok"] is True
        assert seen["params"]["down_type"] == 4

    @pytest.mark.parametrize("dt", [1, 5, 6, 0, "4", None])
    def test_unsafe_down_type_blocked_without_request(self, dt, monkeypatch):
        """非白名单一律拦截，且**不得发出请求**——探测本身就会打挂服务。

        `"4"`（字符串）也要挡：白名单是整数集合，类型不符说明调用方没按约定传参。
        """
        monkeypatch.setattr(tq_http, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(
            tq_http,
            "_post",
            lambda *a, **k: pytest.fail("拦截失败：危险 down_type 竟然发出了请求"),
        )
        r = tq_http.call("download_file", {"down_type": dt})
        assert r["ok"] is False
        assert r["error"]["code"] == "unsafe_down_type"
        assert "白名单" in r["error"]["detail"]

    def test_guard_runs_before_process_check(self, monkeypatch):
        """拦截要在 is_tdxw_running 之前——TdxW 没开时也该报真正的原因。"""
        monkeypatch.setattr(tq_http, "is_tdxw_running", lambda: False)
        r = tq_http.call("download_file", {"down_type": 1})
        assert r["error"]["code"] == "unsafe_down_type", (
            "应先报 unsafe_down_type，而不是被 tdxw_not_running 掩盖"
        )

    def test_explicit_override_allowed(self, monkeypatch):
        """确需探测时可显式签名放行——让调用方为它负责，而不是无法探测。"""
        monkeypatch.setattr(tq_http, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(
            tq_http, "_post", lambda *a, **k: b'{"result":{"ErrorId":"0","Value":1}}'
        )
        r = tq_http.call("download_file", {"down_type": 1}, allow_unsafe_download=True)
        assert r["ok"] is True

    def test_other_methods_unaffected(self, monkeypatch):
        """拦截只针对 download_file，别的方法不受影响。"""
        monkeypatch.setattr(tq_http, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(
            tq_http,
            "_post",
            lambda *a, **k: b'{"result":{"ErrorId":"0","Value":{"a":1}}}',
        )
        assert tq_http.call("get_stock_info", {"stock_code": "600000.SH"})["ok"] is True


class TestStockCodeFormatGuard:
    """`stock_code` 必须带市场后缀 —— 传裸 6 位会得到语义模糊的 `ErrorId=2`。

    2026-08-06 探针实测踩到：探针传 `"600000"`，三个 stock_code 类方法全挂
    （ErrorId=2），而 `get_match_stkinfo`/`download_file`（不吃 stock_code）正常。
    TDX_LOCAL_INTERFACES.md「stock_code 必须带市场后缀」本来就记着这件事，但**接口自己不设防**，
    谁忘了归一就得翻探测文档才知道原因。

    ⚠️ 刻意**不自动补后缀**：补错市场比报错更糟（`600000.SZ` 是另一只票或不存在）。
    """

    @pytest.mark.parametrize(
        "bad", ["600000", "60000.SH", "600000.XX", "6000000.SH", "abcdef.SH"]
    )
    def test_bad_code_blocked_without_request(self, bad, monkeypatch):
        monkeypatch.setattr(tq_http, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(
            tq_http,
            "_post",
            lambda *a, **k: pytest.fail("校验失败：不合规的 stock_code 竟然发出了请求"),
        )
        r = tq_http.call("get_stock_info", {"stock_code": bad})
        assert r["ok"] is False
        assert r["error"]["code"] == "bad_stock_code"

    @pytest.mark.parametrize(
        "good", ["600000.SH", "000001.SZ", "920808.BJ", "600000.sh"]
    )
    def test_good_code_passes(self, good, monkeypatch):
        monkeypatch.setattr(tq_http, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(
            tq_http,
            "_post",
            lambda *a, **k: b'{"result":{"ErrorId":"0","Value":{"Name":"x"}}}',
        )
        assert tq_http.call("get_stock_info", {"stock_code": good})["ok"] is True

    def test_error_message_says_how_to_fix(self, monkeypatch):
        """报错要给出修法，而不是只说「不合规」。"""
        monkeypatch.setattr(tq_http, "is_tdxw_running", lambda: True)
        r = tq_http.call("get_stock_info", {"stock_code": "600000"})
        assert "normalize_code" in r["error"]["detail"]
        assert "ErrorId=2" in r["error"]["detail"]

    def test_methods_without_stock_code_unaffected(self, monkeypatch):
        """get_match_stkinfo / download_file 不吃 stock_code，不该被校验挡。"""
        monkeypatch.setattr(tq_http, "is_tdxw_running", lambda: True)
        monkeypatch.setattr(
            tq_http, "_post", lambda *a, **k: b'{"result":{"ErrorId":"0","Value":[1]}}'
        )
        assert tq_http.call("get_match_stkinfo", {"key_word": "平安"})["ok"] is True


class TestCustomEndpoint(unittest.TestCase):
    """`call(endpoint=...)` 必须透传到 `_post`。

    为什么需要：`trading_calendar` 有 `--endpoint` 参数，收敛到 `tq_http` 时若
    endpoint 传不进去，它就会静默打到默认端口 —— 换端口调试时**看起来在工作**
    却连的是另一个服务。
    """

    def setUp(self) -> None:
        patcher = mock.patch.object(tq_http, "is_tdxw_running", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_endpoint_passed_through(self) -> None:
        seen = {}

        def fake_post(payload, timeout, endpoint=None):
            seen["endpoint"] = endpoint
            return _body({"id": 1, "result": {"ErrorId": "0", "Value": []}})

        with mock.patch.object(tq_http, "_post", side_effect=fake_post):
            tq_http.call(
                "get_trading_dates",
                {"market": "SH"},
                endpoint="http://127.0.0.1:19999/",
            )
        self.assertEqual(seen["endpoint"], "http://127.0.0.1:19999/")

    def test_default_endpoint_resolved_at_call_time(self) -> None:
        """默认端点在**调用时**解析，不是写成默认参数。

        见 DATA_SOURCE_PRINCIPLE「模块级常量 + 运行时替换 = 陷阱」变体②：
        写成 `endpoint=TQ_HTTP_URL` 的话，改 `TQ_HTTP_URL` 就对已定义的函数无效。
        """
        with (
            mock.patch.object(tq_http, "TQ_HTTP_URL", "http://example/"),
            mock.patch.object(tq_http.urllib.request, "urlopen") as uo,
        ):
            uo.side_effect = urllib.error.URLError("stop here")
            tq_http.call("get_trading_dates", {})
        req = uo.call_args[0][0]
        self.assertEqual(req.full_url, "http://example/")
