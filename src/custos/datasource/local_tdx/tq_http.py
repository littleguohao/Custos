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

from custos.datasource.local_tdx.tq_sector import is_tdxw_running
import sys

TQ_HTTP_URL = "http://127.0.0.1:17709/"
DEFAULT_TIMEOUT = 15


# ⚠️ `download_file` 的危险 down_type —— **代码级拦截，不是文档约定**
#
# `TDX_LOCAL_INTERFACES.md` 有一节「TQ 服务可被打挂」的风险记录，`concept_tags.py` 顶部也写着
# 「只调用 down_type=4（实测安全）；禁止触碰 1/5/6（可打挂 TQ 服务）」。
# 但 `call()` 是泛型入口 —— 谁写一行 `call("download_file", {"down_type": 1})` 都不会被挡，
# 而后果是 TdxW 服务挂掉、整条选股链和持仓行情一起没了。
#
# 本仓库反复踩的坑正是「写进文档不等于内化」（同一个连接反模式跨两天犯了三次）。
# 所以这条约束做成拦截：非白名单的 down_type 直接返回结构化错误，不发请求。
# 确有需要探测时，显式传 `allow_unsafe_download=True`，让调用方为它签名。
SAFE_DOWN_TYPES = frozenset({4})  # 4 = miscinfo（概念/主题标签），实测安全

# TQ 要求 `stock_code` **带市场后缀**（`600000.SH`）。传裸 6 位会得到 `ErrorId=2
# stock_code error` —— 这是 2026-08-06 探针实测踩到的：探针传 `"600000"`，三个
# stock_code 类方法全挂，而 `get_match_stkinfo`/`download_file`（不吃 stock_code）正常。
# TDX_LOCAL_INTERFACES.md「stock_code 必须带市场后缀」本来就记着「`601696`(纯代码) → ErrorId=2 stock_code error」。
#
# 生产代码都记得先过 `normalize_code()`，但**接口自己不设防** ⇒ 谁忘了就得到一个
# 语义模糊的 ErrorId=2，得翻探测文档才知道原因。这里显式校验并直接说清要求。
# 不在这里自动补后缀：补错市场比报错更糟（600000.SZ 是另一只票或不存在）。
_CODE_SUFFIXES = ("SH", "SZ", "BJ")


def _bad_stock_code(code: Any) -> Optional[str]:
    """返回不合规的原因；合规返回 None。"""
    s = str(code).strip().upper()
    if "." not in s:
        return (
            f"stock_code={code!r} 缺市场后缀。TQ 要求 `600000.SH` 形态，"
            f"传裸 6 位会得到语义模糊的 ErrorId=2；请先过 "
            f"local_tdx_data.normalize_code()"
        )
    head, _, suf = s.partition(".")
    if not (head.isdigit() and len(head) == 6):
        return f"stock_code={code!r} 的代码段不是 6 位数字"
    if suf not in _CODE_SUFFIXES:
        return f"stock_code={code!r} 的后缀 {suf!r} 不在 {_CODE_SUFFIXES}"
    return None


def _err(code: str, detail: Any = "") -> dict:
    out = {"ok": False, "value": None, "error": {"code": code}}
    if detail:
        out["error"]["detail"] = str(detail)
    return out


def _post(payload: dict, timeout: int, endpoint: Optional[str] = None) -> bytes:
    """发送 JSON-RPC POST，返回原始响应体（网络/HTTP 错误向上抛，由 call 兜底）。

    ``endpoint`` 默认取 `TQ_HTTP_URL`，但**在调用时解析**而不是写成默认参数
    ——见 `governance/data/DATA_SOURCE_PRINCIPLE.md`「模块级常量 + 运行时替换 = 陷阱」。
    """
    req = urllib.request.Request(
        endpoint or TQ_HTTP_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def call(
    method: str,
    params: Optional[dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
    allow_unsafe_download: bool = False,
    endpoint: Optional[str] = None,
) -> dict:
    """调用 TQ-Local 接口，统一返回 {"ok", "value", "error"}，绝不 raise。

    ``allow_unsafe_download``：放行非白名单的 `download_file` down_type。
    默认拦截 —— 见 `SAFE_DOWN_TYPES` 上方注释（1/5/6 实测可打挂 TdxW 服务，
    而它一挂，选股链与持仓行情一起没了）。
    """
    if method == "download_file" and not allow_unsafe_download:
        dt = (params or {}).get("down_type")
        if dt not in SAFE_DOWN_TYPES:
            return _err(
                "unsafe_down_type",
                f"down_type={dt!r} 不在白名单 {sorted(SAFE_DOWN_TYPES)}；"
                f"1/5/6 实测可打挂 TdxW 服务。确需探测请显式传 "
                f"allow_unsafe_download=True",
            )
    if params and "stock_code" in params:
        why = _bad_stock_code(params["stock_code"])
        if why:
            return _err("bad_stock_code", why)
    if not is_tdxw_running():
        return _err("tdxw_not_running", "TdxW.exe 未运行，TQ-Local 服务不可用")
    payload = {"id": 1, "method": method, "params": params or {}}
    try:
        raw = _post(payload, timeout, endpoint)
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


def more_info(
    code: str, fields: Optional[list] = None, timeout: int = DEFAULT_TIMEOUT
) -> dict:
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
