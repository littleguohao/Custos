# -*- coding: utf-8 -*-
"""TQ-Local HTTP JSON-RPC 薄封装（TdxW.exe 本地服务 http://127.0.0.1:17709/）。

设计要点：

- 仅使用标准库 urllib 发 POST（net_retry.fetch_with_retry 只支持 GET，且本机
  调用无需重试），不引入新依赖。
- ``call()`` 统一返回 ``{"ok": bool, "value": ..., "error": ...}``，任何失败
  （TdxW 未运行、连接失败、HTTP 错误、ErrorId != "0"、响应不可解析）都结构化
  返回，绝不 raise 到调用方。
- 兼容两种响应形态：多数接口 ``result.ErrorId + result.Value``（取 Value）；
  get_market_snapshot 字段直挂 result（去掉 ErrorId 后取 result 本体）。
- 复用 tq_sector.is_tdxw_running 做进程级快速预检，不重复造轮子。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from tq_sector import is_tdxw_running

TQ_HTTP_URL = "http://127.0.0.1:17709/"
DEFAULT_TIMEOUT = 15


def _err(code: str, detail: Any = "") -> dict:
    out = {"ok": False, "value": None, "error": {"code": code}}
    if detail:
        out["error"]["detail"] = str(detail)
    return out


def _post(payload: dict, timeout: int) -> bytes:
    """发送 JSON-RPC POST，返回原始响应体（网络/HTTP 错误向上抛，由 call 兜底）。"""
    req = urllib.request.Request(
        TQ_HTTP_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def call(method: str, params: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """调用 TQ-Local 接口，统一返回 {"ok", "value", "error"}，绝不 raise。"""
    if not is_tdxw_running():
        return _err("tdxw_not_running", "TdxW.exe 未运行，TQ-Local 服务不可用")
    payload = {"id": 1, "method": method, "params": params or {}}
    try:
        raw = _post(payload, timeout)
    except urllib.error.URLError as exc:
        return _err("connection_failed", exc.reason if hasattr(exc, "reason") else exc)
    except TimeoutError as exc:
        return _err("timeout", exc)
    except Exception as exc:  # noqa: BLE001 —— 绝不 raise 到调用方
        return _err("request_failed", exc)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return _err("invalid_response", exc)
    if not isinstance(body, dict):
        return _err("invalid_response", f"unexpected body type: {type(body).__name__}")
    if body.get("error"):
        return _err("jsonrpc_error", body["error"])
    result = body.get("result")
    if not isinstance(result, dict):
        return _err("invalid_response", "missing result object")
    error_id = str(result.get("ErrorId", "0"))
    if error_id != "0":
        return _err("tq_error", f"ErrorId={error_id} method={method}")
    # 两种响应形态：Value 形态取 Value；字段直挂 result 形态去掉 ErrorId 取本体
    if "Value" in result:
        value = result["Value"]
    else:
        value = {k: v for k, v in result.items() if k != "ErrorId"}
    return {"ok": True, "value": value, "error": None}


def snapshot(code: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """实时行情快照（get_market_snapshot，字段直挂 result 形态）。"""
    return call("get_market_snapshot", {"stock_code": code}, timeout=timeout)


def more_info(code: str, fields: Optional[list] = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """更多证券信息（get_more_info，Value 形态；传 field_list 实际仍返回全字段）。"""
    params = {"stock_code": code, "field_list": list(fields) if fields else []}
    return call("get_more_info", params, timeout=timeout)


def stock_info(code: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """证券基础信息（get_stock_info，Value 形态）。"""
    return call("get_stock_info", {"stock_code": code}, timeout=timeout)


def ping(timeout: int = 10) -> dict:
    """连通性检查：用 get_match_stkinfo 探测服务是否可用。"""
    return call("get_match_stkinfo", {"key_word": "平安"}, timeout=timeout)


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(ping(), ensure_ascii=False))
