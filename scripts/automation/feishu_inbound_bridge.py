# -*- coding: utf-8 -*-
"""飞书入站桥（纯确定性，零 LLM）：轮询用户与机器人的单聊，登记补数并回执。

每轮（Automation 调用 run(ctx)）：
1. 拉取单聊最近消息，筛出上次游标之后的**用户**消息，append 落 inbox.jsonl；
2. 严格格式确定性登记：
   - 「无交易」类确认 → data/trades/position_confirmations.json[当日]；
   - 0AMV 数值（文本含 amv 后取数字，或整条消息就是数字）→ 官方唯一写入口
     amv_state.append_observation（uv run 子进程，cwd=仓库根）；
   两者同日已登记则跳过（幂等）。
3. 文件消息：下载落 inbox_files/；是成交表（含必需列）→ 规范化后走唯一入口
   incremental_ledger.py 入库（幂等可重导）；缺列/非表格 → 只留痕待人工处理。
4. 飞书回执：登记结果 / 未识别提示。自由文本成交描述**不自动写台账**。
凭据：环境变量 FEISHU_APP_ID/FEISHU_APP_SECRET 优先，否则 openclaw.json。
网络与飞书 API 只用 stdlib urllib；表格解析用托管运行时自带的 pandas。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 代码迁入仓库后，状态、游标、原始消息和本地凭据仍沿用原桥目录，避免重复回放。
BASE_DIR = Path(
    r"E:\KimiData\daimon-share\daimon\agents\main\blueprint\automations"
    r"\automation_364c4499-8534-4fbf-838b-04b8cb2583d4\assets"
)
STATE_PATH = BASE_DIR / "bridge_state.json"
INBOX_PATH = BASE_DIR / "inbox.jsonl"
CRED_PATH = (
    BASE_DIR / "feishu_cred.json"
)  # 本地凭据（2026-10-24：openclaw 通道下线后改从这里读）

REPO = Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team")
CONFIRM_PATH = REPO / "data" / "trades" / "position_confirmations.json"
OPENCLAW_CFG = Path(
    os.environ.get("OPENCLAW_CONFIG") or r"C:\Users\gh\.openclaw-tdxclaw\openclaw.json"
)
DEFAULT_CHAT_ID = (
    "oc_3806ae837d66587a23d9061dbfb5a59c"  # 与 owner 的单聊（2026-10-23 联调实测）
)

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGES_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

CST = timezone(timedelta(hours=8))
HTTP_TIMEOUT = 15

NO_TRADE_RE = re.compile(r"(无|没|未|没有)\s*(交易|成交|操作|买卖)|无交易动作")
AMV_VALUE_RE = re.compile(
    r"0?\s*amv\s*[:：=]?\s*([+-]?\d+(?:\.\d+)?)\s*%?", re.IGNORECASE
)
NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")
PURE_NUMBER_RE = re.compile(r"^\s*[+-]?\d+(?:\.\d+)?\s*%?\s*$")


def _http(
    method: str,
    url: str,
    *,
    token: str = "",
    payload: dict | None = None,
    params: dict | None = None,
) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _credentials() -> dict:
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    to_open_id = os.environ.get("FEISHU_TO_OPEN_ID", "").strip()
    if not (app_id and app_secret) and CRED_PATH.exists():
        # 本地凭据文件（openclaw 通道下线后的主来源）
        cred = json.loads(CRED_PATH.read_text(encoding="utf-8"))
        app_id, app_secret = cred["app_id"], cred["app_secret"]
        to_open_id = to_open_id or cred.get("to_open_id", "")
    if not (app_id and app_secret):
        cfg = json.loads(OPENCLAW_CFG.read_text(encoding="utf-8"))
        acct = cfg["channels"]["feishu"]["accounts"]["default"]
        app_id, app_secret = str(acct["appId"]), str(acct["appSecret"])
        for k in ("reportToOpenId", "toOpenId", "defaultToOpenId"):
            if acct.get(k):
                to_open_id = str(acct[k])
                break
    if not (app_id and app_secret and to_open_id):
        raise RuntimeError("飞书凭据或接收人 open_id 缺失")
    return {"app_id": app_id, "app_secret": app_secret, "to_open_id": to_open_id}


def _token(cred: dict) -> str:
    resp = _http(
        "POST",
        TOKEN_URL,
        payload={"app_id": cred["app_id"], "app_secret": cred["app_secret"]},
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"tenant_access_token 获取失败: {resp.get('msg')}")
    return resp["tenant_access_token"]


def _send_text(cred: dict, token: str, text: str) -> bool:
    resp = _http(
        "POST",
        f"{MESSAGES_URL}?receive_id_type=open_id",
        token=token,
        payload={
            "receive_id": cred["to_open_id"],
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
    )
    return resp.get("code") == 0


def _download_file(token: str, message_id: str, file_key: str, dest: Path) -> None:
    if not file_key:
        raise RuntimeError("文件消息缺 file_key")
    url = f"{MESSAGES_URL}/{message_id}/resources/{file_key}?type=file"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.write_bytes(resp.read())


TEMPLATE_COLS = [
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
]
REQUIRED_COLS = ["成交日期", "代码", "交易类别", "成交数量", "成交价格"]


def _maybe_import_trades(path: Path, day: str) -> str:
    """只把上传当天的成交送入唯一入口 incremental_ledger（幂等可重导）。

    owner 发送的是完整账户历史表；历史行与既有台账可能因拆单、费用或手工
    修正而不同，不能重新参与增量指纹比对，否则会把旧成交误判为新增并触发
    超卖保护，连带拦下当天真实成交。非表格/缺列 → 只留痕不入库。
    """
    import pandas as pd  # 托管运行时自带（requirements.lock 含 pandas/openpyxl）

    suffix = path.suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xls"):
        return f"收到文件 {path.name}（非表格，已保存待人工处理）"
    try:
        if suffix == ".csv":
            try:
                df = pd.read_csv(path, encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(path, encoding="gbk")
        else:
            # 多工作表文件（如券商账户导出）：选第一个含成交必需列的表
            xl = pd.ExcelFile(path)
            df = None
            for sheet in xl.sheet_names:
                cand = xl.parse(sheet, dtype=str)
                if all(c in cand.columns for c in REQUIRED_COLS):
                    df = cand
                    break
            if df is None:
                return (
                    f"收到文件 {path.name}，各工作表都缺成交列 {REQUIRED_COLS}，"
                    "未入库（已保存；请核对模板列口径，或在 Kimi 会话发我处理）"
                )
    except Exception as exc:
        return f"收到文件 {path.name}，表格解析失败（已保存）：{exc}"
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        return (
            f"收到文件 {path.name}，缺成交列 {missing}，未入库"
            "（已保存；请核对模板列口径，或在 Kimi 会话发我处理）"
        )
    trade_dates = pd.to_datetime(df["成交日期"], errors="coerce").dt.strftime(
        "%Y-%m-%d"
    )
    df = df.loc[trade_dates.eq(day)].copy()
    if df.empty:
        return f"成交表 {path.name} 已保存，但未找到 {day} 的成交记录，未入库"
    for c in TEMPLATE_COLS:
        if c not in df.columns:
            df[c] = ""
    # 代码规范化：券商导出常被读成浮点（938.0）→ 去 .0、补前导零到 6 位
    df["代码"] = (
        df["代码"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    )
    norm = path.with_name(path.stem + "_normalized.csv")
    df[TEMPLATE_COLS].to_csv(norm, index=False, encoding="utf-8")
    r = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "src/custos/core/trades/incremental_ledger.py",
            "--input",
            str(norm),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return f"成交表 {path.name} 入库失败（文件已保存）：{err[-200:]}"
    tail = (r.stdout or "").strip().splitlines()
    return f"成交表 {path.name} 已入库（{day} 当日 {len(df)} 行）：{tail[-1][:120] if tail else 'ok'}"


def _handle_file(token: str, day: str, m: dict) -> str:
    fname = m.get("file_name") or f"{m['message_id']}.bin"
    dest_dir = BASE_DIR / "inbox_files"
    dest_dir.mkdir(exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", fname)
    dest = dest_dir / f"{day}_{safe}"
    _download_file(token, m["message_id"], m.get("file_key", ""), dest)
    return _maybe_import_trades(dest, day)


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    # 首轮：游标置为当前（不回放历史消息），chat_id 用联调实测值
    now_ms = str(int(datetime.now(CST).timestamp() * 1000))
    return {"chat_id": DEFAULT_CHAT_ID, "last_seen_ms": now_ms, "seen_ids": []}


def _save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _fetch_new_user_messages(token: str, state: dict) -> list[dict]:
    resp = _http(
        "GET",
        MESSAGES_URL,
        token=token,
        params={
            "container_id_type": "chat",
            "container_id": state["chat_id"],
            "page_size": 20,
            "sort_type": "ByCreateTimeDesc",
        },
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"拉取消息失败: {resp.get('code')} {resp.get('msg')}")
    last = int(state["last_seen_ms"])
    seen = set(state.get("seen_ids") or [])
    out = []
    for m in resp["data"].get("items", []):
        if m.get("sender", {}).get("sender_type") != "user":
            continue
        ts = int(m.get("create_time", "0"))
        mid = m.get("message_id", "")
        if ts <= last or mid in seen:
            continue
        text = ""
        if m.get("msg_type") == "text":
            try:
                text = json.loads(m.get("body", {}).get("content", "{}")).get(
                    "text", ""
                )
            except ValueError:
                text = ""
        item = {
            "message_id": mid,
            "ts": ts,
            "msg_type": m.get("msg_type"),
            "text": text.strip(),
        }
        if m.get("msg_type") == "file":
            try:
                body = json.loads(m.get("body", {}).get("content", "{}"))
                item["file_key"] = body.get("file_key", "")
                item["file_name"] = body.get("file_name", "")
            except ValueError:
                pass
        out.append(item)
    return sorted(out, key=lambda x: x["ts"])


def _register_no_trade(day: str, note: str) -> str:
    data = (
        json.loads(CONFIRM_PATH.read_text(encoding="utf-8"))
        if CONFIRM_PATH.exists()
        else {}
    )
    cur = data.get(day)
    if isinstance(cur, dict) and cur.get("no_trades"):
        return "无交易确认已存在，跳过"
    if CONFIRM_PATH.exists():  # 改写前留快照（append-only 纪律的备份习惯）
        CONFIRM_PATH.with_name(CONFIRM_PATH.name + ".bak_feishu_bridge").write_bytes(
            CONFIRM_PATH.read_bytes()
        )
    data[day] = {
        "confirmed_at": datetime.now(CST).isoformat(timespec="seconds"),
        "no_trades": True,
        "note": note,
    }
    tmp = CONFIRM_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIRM_PATH)
    return f"{day} 无交易确认已登记"


def _register_amv(day: str, value: float) -> str:
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "from custos.pipeline.market_timing.amv_state import append_observation;"
        f"append_observation({day!r}, {{'amv_change_pct': {value!r}, 'as_of': {day!r},"
        " 'quality': 'confirmed', 'source': 'feishu_bridge'})"
    )
    r = subprocess.run(
        ["uv", "run", "--no-sync", "python", "-c", code],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if r.returncode != 0:
        raise RuntimeError(f"0AMV 登记失败: {r.stderr.strip()[:300]}")
    return f"{day} 0AMV={value}% 已登记"


def _classify(text: str) -> tuple[str, float | None]:
    """→ ('no_trade'|'amv'|'both'|'unknown', amv值)。"""
    no_trade = bool(NO_TRADE_RE.search(text))
    amv: float | None = None
    m = AMV_VALUE_RE.search(text)
    if m:
        amv = float(m.group(1))
    elif PURE_NUMBER_RE.match(text):  # 整条就一个数字：补数窗口里按 0AMV 理解
        amv = float(NUMBER_RE.search(text).group(0))  # type: ignore[union-attr]
    kind = (
        "both"
        if (no_trade and amv is not None)
        else ("no_trade" if no_trade else ("amv" if amv is not None else "unknown"))
    )
    return kind, amv


def run(ctx) -> dict:
    state = _load_state()
    cred = _credentials()
    token = _token(cred)
    day = datetime.now(CST).date().isoformat()
    actions: list[str] = []
    notes: list[str] = []

    new_msgs = _fetch_new_user_messages(token, state)
    for m in new_msgs:
        with INBOX_PATH.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"date": day, **m}, ensure_ascii=False) + "\n")
        state["last_seen_ms"] = str(max(int(state["last_seen_ms"]), m["ts"]))
        state.setdefault("seen_ids", []).append(m["message_id"])
        if m["msg_type"] == "file":
            actions.append(_handle_file(token, day, m))
            continue
        if m["msg_type"] != "text" or not m["text"]:
            notes.append(f"非文本消息已留痕（type={m['msg_type']}）")
            continue
        kind, amv = _classify(m["text"])
        if kind in ("no_trade", "both"):
            actions.append(_register_no_trade(day, f"飞书确认（{m['text'][:60]}）"))
        if kind in ("amv", "both") and amv is not None:
            actions.append(_register_amv(day, amv))
        if kind == "unknown":
            notes.append(f"未识别（已留痕待核对）：{m['text'][:60]}")
    state["seen_ids"] = state.get("seen_ids", [])[-200:]  # 幂等集合封顶
    _save_state(state)

    receipt_sent = False
    if new_msgs:
        lines = [f"【补数桥】收到 {len(new_msgs)} 条回复（{day}）"]
        lines += [f"✅ {a}" for a in actions]
        lines += [f"📌 {n}" for n in notes]
        if notes:
            lines.append(
                "未识别内容不会自动写台账，请核对格式；"
                "支持：「无交易」/「0AMV 数值」/ 直接回数字 / 发成交表文件（csv/xlsx）"
            )
        receipt_sent = _send_text(cred, token, "\n".join(lines))

    return {
        "artifact": {
            "new_messages": len(new_msgs),
            "actions": actions,
            "receipt_sent": receipt_sent,
            "notes": "；".join(notes),
        }
    }
