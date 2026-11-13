# -*- coding: utf-8 -*-
"""投资账本 MCP 当日成交适配器：复用本地 OAuth，输出标准成交 CSV。

只通过 Python 调用固定 MCP 工具，不经过模型。OAuth 配置与账户 ID 保存在
``data/trades/tzzb_oauth.local.json``（gitignored），也可用 ``TZZB_OAUTH_FILE`` /
``TZZB_FUND_KEY`` 覆盖。访问令牌过期或收到 401 时用 refresh_token 自动续期。

投资账本只暴露 PC 已同步数据；导入前要求账户 ``lastSyncTime`` 为目标日 15:00
或之后，防止漏掉尾盘成交。接口没有成交时间，标准 CSV 的该字段保持空值；
现有 incremental_ledger 的多重集合指纹负责同形同量多笔及重复刷新去重。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from custos.core.code_utils import clean_code
from custos.core.paths import TRADES_DIR

MCP_URL = "https://tzzb.10jqka.com.cn/mcp"
TOKEN_URL = "https://tzzb.10jqka.com.cn/oauth2/token"
OAUTH_FILE = Path(
    os.environ.get("TZZB_OAUTH_FILE") or TRADES_DIR / "tzzb_oauth.local.json"
)
FIELDS = (
    "成交日期",
    "成交时间",
    "代码",
    "名称",
    "交易类别",
    "成交数量",
    "成交价格",
    "成交金额",
    "发生金额",
    "费用",
    "备注",
)


class TzzbError(RuntimeError):
    """授权、MCP 响应或成交字段验收失败。"""


def _load_oauth(path: Path = OAUTH_FILE) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TzzbError(f"投资账本 OAuth 配置不可读: {path}: {exc}") from exc
    if not isinstance(data, dict) or not data.get("client_id"):
        raise TzzbError(f"投资账本 OAuth 配置缺少 client_id: {path}")
    return data


def _save_oauth(data: dict[str, Any], path: Path = OAUTH_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _token_expired(data: dict[str, Any]) -> bool:
    try:
        expires = float(data.get("expires_in") or 0)
        received = float(data.get("received_at") or 0)
    except (TypeError, ValueError):
        return False
    return bool(expires and received and time.time() >= received + expires - 45)


def _refresh_oauth(path: Path = OAUTH_FILE, *, force: bool = False) -> dict[str, Any]:
    data = _load_oauth(path)
    if not force and not _token_expired(data) and data.get("access_token"):
        return data
    refresh = str(data.get("refresh_token") or "")
    if not refresh:
        raise TzzbError("投资账本访问令牌已过期且没有 refresh_token，需重新授权")
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": data["client_id"],
            "refresh_token": refresh,
            "resource": data.get("resource") or "https://tzzb.10jqka.com.cn/",
        }
    ).encode("ascii")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            fresh = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise TzzbError(f"投资账本令牌刷新失败: {exc}") from exc
    if not isinstance(fresh, dict) or not fresh.get("access_token"):
        raise TzzbError("投资账本令牌刷新响应缺少 access_token")
    data.update(fresh)
    data["refresh_token"] = fresh.get("refresh_token") or refresh
    data["received_at"] = int(time.time())
    _save_oauth(data, path)
    return data


def _json_payload(body: bytes, content_type: str = "") -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    if (
        "text/event-stream" in content_type
        or text.startswith("event:")
        or text.startswith("data:")
    ):
        events = []
        for line in text.splitlines():
            if line.startswith("data:"):
                events.append(line[5:].strip())
        if not events:
            raise TzzbError("MCP SSE 响应没有 data 内容")
        text = events[-1]
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise TzzbError(f"MCP 返回非 JSON: {text[-300:]!r}") from exc
    if not isinstance(payload, dict):
        raise TzzbError("MCP JSON-RPC 响应必须是对象")
    return payload


def _post_mcp(
    method: str, params: dict[str, Any], token: str, session: str = ""
) -> tuple[dict[str, Any], dict[str, str]]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if method != "notifications/initialized":
        payload["id"] = 1
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **({"Mcp-Session-Id": session} if session else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read()
            headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
            return _json_payload(body, resp.headers.get("Content-Type", "")), headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[-300:]
        if exc.code == 401:
            raise
        raise TzzbError(f"MCP HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise TzzbError(f"MCP 网络请求失败: {exc}") from exc


def _mcp_call_once(
    tool: str, arguments: dict[str, Any], path: Path = OAUTH_FILE
) -> Any:
    oauth = _refresh_oauth(path)
    token = str(oauth.get("access_token") or "")
    if not token:
        raise TzzbError("投资账本 OAuth 配置缺少 access_token")
    init, headers = _post_mcp(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "custos-tzzb", "version": "1.0"},
        },
        token,
    )
    if init.get("error"):
        raise TzzbError(f"MCP 初始化失败: {init['error']}")
    session = headers.get("mcp-session-id", "")
    if session:
        _post_mcp("notifications/initialized", {}, token, session)
    response, _ = _post_mcp(
        "tools/call", {"name": tool, "arguments": arguments}, token, session
    )
    if response.get("error"):
        raise TzzbError(f"MCP {tool} 失败: {response['error']}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise TzzbError(f"MCP {tool} 缺少 result")
    if result.get("isError"):
        raise TzzbError(f"MCP {tool} 返回错误: {result.get('content')}")
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            text = str(part.get("text") or "")
            try:
                return json.loads(text)
            except ValueError:
                return text
    raise TzzbError(f"MCP {tool} 没有结构化内容或文本内容")


def mcp_call(tool: str, arguments: dict[str, Any], *, path: Path = OAUTH_FILE) -> Any:
    """调用固定投资账本工具；401 时刷新令牌并只重试一次。"""
    try:
        return _mcp_call_once(tool, arguments, path)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
    _refresh_oauth(path, force=True)
    return _mcp_call_once(tool, arguments, path)


def resolve_account(*, path: Path = OAUTH_FILE) -> tuple[str, str]:
    """返回 (fund_key, lastSyncTime)；单账户可自动选择，多账户必须显式配置。"""
    oauth = _load_oauth(path)
    accounts = mcp_call("promptAccountSelection", {"scope": "stock"}, path=path)
    if not isinstance(accounts, dict):
        raise TzzbError("投资账本账户列表结构异常")
    stock_accounts = accounts.get("fund_key") or []
    configured = str(os.environ.get("TZZB_FUND_KEY") or oauth.get("fund_key") or "")
    if not configured:
        if len(stock_accounts) != 1:
            raise TzzbError("投资账本有多个或没有股票账户，请设置 TZZB_FUND_KEY")
        configured = str(stock_accounts[0].get("id") or "")
        # mcp_call 可能刚刷新过令牌；保存账户时必须重读，不能用旧快照覆盖新令牌。
        latest = _load_oauth(path)
        latest["fund_key"] = configured
        _save_oauth(latest, path)
    matched = next(
        (a for a in stock_accounts if str(a.get("id") or "") == configured), None
    )
    if matched is None:
        raise TzzbError("配置的 TZZB_FUND_KEY 不在当前投资账本账户列表中")
    return configured, str(matched.get("lastSyncTime") or "")


def validate_sync_time(last_sync: str, day: str) -> None:
    """PC 同步时间必须覆盖目标日收盘，否则拒绝作为完整当日成交。"""
    try:
        synced = datetime.strptime(last_sync, "%Y-%m-%d %H:%M:%S")
        target = datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        raise TzzbError(f"投资账本 lastSyncTime 无效: {last_sync!r}") from exc
    if (
        synced.date() != target.date()
        or synced.time() < datetime.strptime("15:00:00", "%H:%M:%S").time()
    ):
        raise TzzbError(
            f"投资账本 PC 同步时间为 {last_sync or '缺失'}，未覆盖 {day} 15:00 收盘；"
            "请先在投资账本 PC 同步后再回复有交易"
        )


def _number(row: dict[str, Any], key: str, *, positive: bool = False) -> float:
    value = row.get(key)
    if value is None or isinstance(value, bool):
        raise TzzbError(f"投资账本成交字段 {key} 非数值: {value!r}")
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise TzzbError(f"投资账本成交字段 {key} 非数值: {value!r}") from exc
    if not math.isfinite(number) or (positive and number <= 0) or number < 0:
        raise TzzbError(f"投资账本成交字段 {key} 超出有效范围: {value!r}")
    return number


def normalize_trades(payload: dict[str, Any], day: str) -> list[dict[str, Any]]:
    """MCP 流水 → incremental_ledger 标准字段；非买卖流水不进入成交表。"""
    entries = payload.get("list")
    if not isinstance(entries, list):
        raise TzzbError("投资账本 queryMoneyHistory 缺少 list")
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise TzzbError("投资账本成交记录必须是对象")
        if entry.get("entryDate") != day:
            raise TzzbError(f"投资账本返回了其他日期成交: {entry.get('entryDate')}")
        side = str(entry.get("opName") or "")
        if side not in {"买入", "卖出"}:
            continue
        code = clean_code(entry.get("stockCode"))
        if not code:
            raise TzzbError("投资账本成交缺少股票代码")
        qty = _number(entry, "entryCount", positive=True)
        price = _number(entry, "entryPrice", positive=True)
        amount = _number(entry, "entryMoney", positive=True)
        fee = _number(entry, "feeTotal")
        if abs(qty * price - amount) > max(0.02, amount * 1e-6):
            raise TzzbError(
                f"投资账本数量×价格与成交金额不符: {code} {qty} {price} {amount}"
            )
        rows.append(
            {
                "成交日期": day,
                "成交时间": "",
                "代码": code,
                "名称": str(entry.get("stockName") or ""),
                "交易类别": side,
                "成交数量": qty,
                "成交价格": price,
                "成交金额": amount,
                "发生金额": -amount if side == "买入" else amount,
                "费用": fee,
                "备注": "投资账本MCP（接口无成交时间字段）",
            }
        )
    return rows


def fetch_trades(
    day: str, *, path: Path = OAUTH_FILE, call_fn=None, account_fn=None
) -> tuple[list[dict[str, Any]], str]:
    account = account_fn or resolve_account
    call = call_fn or mcp_call
    fund_key, last_sync = account(path=path)
    validate_sync_time(last_sync, day)
    compact = day.replace("-", "")
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = call(
            "queryMoneyHistory",
            {
                "page": str(page),
                "count": "100",
                "startDate": compact,
                "endDate": compact,
                "fundKey": fund_key,
                "rzrqFundKey": "",
                "manualId": "",
            },
            path=path,
        )
        if not isinstance(payload, dict):
            raise TzzbError("投资账本成交分页响应不是对象")
        rows.extend(normalize_trades(payload, day))
        try:
            max_page = int(payload.get("maxPage") or 1)
        except (TypeError, ValueError) as exc:
            raise TzzbError("投资账本 maxPage 无效") from exc
        if page >= max_page:
            break
        page += 1
        if page > 100:
            raise TzzbError("投资账本成交分页超过 100 页，停止读取")
    return rows, last_sync


def write_trade_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        raise TzzbError("投资账本当日没有买卖成交，拒绝写空导入文件")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    datetime.strptime(args.date, "%Y-%m-%d")
    rows, last_sync = fetch_trades(args.date)
    out = Path(args.out)
    write_trade_csv(rows, out)
    print(
        json.dumps(
            {
                "date": args.date,
                "source": "tzzb_mcp",
                "last_sync": last_sync,
                "row_count": len(rows),
                "buy_count": sum(r["交易类别"] == "买入" for r in rows),
                "sell_count": sum(r["交易类别"] == "卖出" for r in rows),
                "output": str(out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
