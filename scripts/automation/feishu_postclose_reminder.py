# -*- coding: utf-8 -*-
"""盘后补数检查提醒：交易日缺什么提醒什么，记录齐全则不发送。

调度时点为交易日 15:30 / 16:00 / 16:30。脚本只读当天台账并发送飞书文本，
不生成文件、不调用模型；飞书入站桥是否启用与本脚本无关。
"""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(r"C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team")
OPENCLAW_CFG = Path(
    os.environ.get("OPENCLAW_CONFIG") or r"C:\Users\gh\.openclaw-tdxclaw\openclaw.json"
)
BRIDGE_CRED = Path(
    r"E:\KimiData\daimon-share\daimon\agents\main\blueprint\automations"
    r"\automation_364c4499-8534-4fbf-838b-04b8cb2583d4\assets\feishu_cred.json"
)
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGES_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
CST = timezone(timedelta(hours=8))
HTTP_TIMEOUT = 15


def _credentials() -> dict:
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    to_open_id = os.environ.get("FEISHU_TO_OPEN_ID", "").strip()
    if not (app_id and app_secret) and BRIDGE_CRED.exists():
        cred = json.loads(BRIDGE_CRED.read_text(encoding="utf-8"))
        app_id, app_secret = cred["app_id"], cred["app_secret"]
        to_open_id = to_open_id or cred.get("to_open_id", "")
    if not (app_id and app_secret) and OPENCLAW_CFG.exists():
        cfg = json.loads(OPENCLAW_CFG.read_text(encoding="utf-8"))
        acct = cfg["channels"]["feishu"]["accounts"]["default"]
        app_id, app_secret = str(acct["appId"]), str(acct["appSecret"])
        for key in ("reportToOpenId", "toOpenId", "defaultToOpenId"):
            if acct.get(key):
                to_open_id = str(acct[key])
                break
    if not (app_id and app_secret and to_open_id):
        raise RuntimeError("飞书凭据或接收人 open_id 缺失")
    return {"app_id": app_id, "app_secret": app_secret, "to_open_id": to_open_id}


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
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("code") != 0:
        raise RuntimeError(f"飞书接口失败: {body.get('code')} {body.get('msg')}")
    return body


def _is_trading_day(day: str) -> bool:
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "src/custos/datasource/trading_calendar.py",
            "--check-date",
            day,
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"交易日历检查失败: {result.stderr.strip()[:300]}")
    output = result.stdout.strip()
    start, end = output.find("{"), output.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(f"交易日历输出无法解析: {output[:300]}")
    data = json.loads(output[start : end + 1])
    if "is_trading_day" not in data:
        raise RuntimeError("交易日历输出缺少 is_trading_day")
    return data["is_trading_day"] is True


def _has_confirmed_amv(day: str, repo: Path) -> bool:
    """当天 confirmed、未 superseded 且数值有限的 0AMV 才算已记录。"""
    path = repo / "data" / "market" / "0amv_observations.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            obs = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(obs, dict) or obs.get("date") != day:
            continue
        value = obs.get("amv_change_pct")
        if (
            obs.get("quality") == "confirmed"
            and obs.get("superseded") is not True
            and str(obs.get("as_of") or day)[:10] == day
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return True
    return False


def _has_trade_record(day: str, repo: Path) -> bool:
    """当天任一成交行或明确的 no_trades 确认都算交易侧已记录。"""
    confirmations = repo / "data" / "trades" / "position_confirmations.json"
    try:
        records = json.loads(confirmations.read_text(encoding="utf-8"))
        record = records.get(day) if isinstance(records, dict) else None
        if isinstance(record, dict) and record.get("no_trades") is True:
            return True
    except (OSError, TypeError, ValueError):
        pass

    ledger = repo / "data" / "trades" / "master_trade_ledger.csv"
    try:
        with ledger.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("成交日期") or "").strip()[:10] == day:
                    return True
    except (OSError, csv.Error, UnicodeError):
        return False
    return False


def _record_status(day: str, repo: Path = REPO) -> dict[str, bool]:
    return {
        "amv": _has_confirmed_amv(day, repo),
        "trades": _has_trade_record(day, repo),
    }


def _message(day: str, missing: list[str]) -> str:
    if not missing:
        raise ValueError("记录齐全时不应生成提醒消息")
    if missing == ["amv"]:
        return (
            f"【盘后补数提醒】{day}\n"
            "当日交易记录已确认，当前仅缺 0AMV 收盘值。\n"
            "请回「0AMV 数值」，或直接回数字。"
        )
    if missing == ["trades"]:
        return (
            f"【盘后补数提醒】{day}\n"
            "当日 0AMV 已确认，当前仅缺交易记录。\n"
            "无交易请回「无交易」；有交易请把成交表文件（csv/xlsx）直接发到本会话。\n"
            "成交表列口径：成交日期/成交时间/代码/名称/交易类别/成交数量/成交价格/"
            "成交金额/发生金额/费用/备注。"
        )
    return (
        f"【盘后补数提醒】{day}\n"
        "请补充或确认当日交易记录与 0AMV 收盘值：\n"
        "1. 无交易：回「无交易」。\n"
        "2. 0AMV：回「0AMV 数值」，或直接回数字。\n"
        "3. 有交易：把成交表文件（csv/xlsx）直接发到本会话。\n"
        "成交表列口径：成交日期/成交时间/代码/名称/交易类别/成交数量/成交价格/"
        "成交金额/发生金额/费用/备注。\n"
        "也可以合并回复，例如「无交易 0AMV -1.18%」。"
    )


def _send_text(text: str) -> None:
    cred = _credentials()
    token_body = _http(
        "POST",
        TOKEN_URL,
        payload={"app_id": cred["app_id"], "app_secret": cred["app_secret"]},
    )
    token = token_body.get("tenant_access_token")
    if not token:
        raise RuntimeError("飞书 token 响应缺少 tenant_access_token")
    _http(
        "POST",
        MESSAGES_URL,
        token=token,
        params={"receive_id_type": "open_id"},
        payload={
            "receive_id": cred["to_open_id"],
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
    )


def _run_for_day(day: str) -> dict:
    if not _is_trading_day(day):
        return {
            "date": day,
            "trading_day": False,
            "sent": False,
            "reason": "非交易日，未发送",
        }
    status = _record_status(day)
    missing = [name for name in ("amv", "trades") if not status[name]]
    artifact = {
        "date": day,
        "trading_day": True,
        "sent": bool(missing),
        "amv_confirmed": status["amv"],
        "trade_recorded": status["trades"],
        "missing": missing,
    }
    if not missing:
        artifact["reason"] = "当日 0AMV 与交易记录均已确认，未发送"
        return artifact
    _send_text(_message(day, missing))
    labels = {"amv": "0AMV", "trades": "交易记录"}
    artifact["reason"] = (
        "缺少" + "、".join(labels[name] for name in missing) + "，已发送提醒"
    )
    return artifact


def run(ctx) -> dict:
    day = datetime.now(CST).date().isoformat()
    return {"artifact": _run_for_day(day)}
