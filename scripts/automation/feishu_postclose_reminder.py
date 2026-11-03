# -*- coding: utf-8 -*-
"""盘后补数单任务：15:00 缺项提醒一次，10 分钟轮询，收齐即停，17:00 截止。

每日只运行这一轮；开始时已齐则不提醒也不轮询。入站接收、台账登记、状态检查
均为确定性脚本，不调用模型，不保留常驻 10 分钟轮询。
"""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import time
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


def _run_for_day(day: str, ingest_fn=None) -> dict:
    if not _is_trading_day(day):
        return {
            "date": day,
            "trading_day": False,
            "sent": False,
            "reason": "非交易日，未发送",
        }
    ingest_artifact = None
    if ingest_fn is not None:
        ingest_artifact = ingest_fn({}).get("artifact", {})
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
    if ingest_artifact is not None:
        artifact["ingest"] = ingest_artifact
    if not missing:
        artifact["reason"] = "当日 0AMV 与交易记录均已确认，未发送"
        return artifact
    _send_text(_message(day, missing))
    labels = {"amv": "0AMV", "trades": "交易记录"}
    artifact["reason"] = (
        "缺少" + "、".join(labels[name] for name in missing) + "，已发送提醒"
    )
    return artifact


def _run_collection_day(
    day: str,
    ingest_fn,
    *,
    sleep_fn=None,
    max_polls: int = 12,
) -> dict:
    """15:00 缺项提醒一次，随后每 10 分钟接收；收齐即停，12 轮后到 17:00 截止。"""
    if not _is_trading_day(day):
        return {
            "date": day,
            "trading_day": False,
            "sent": False,
            "completed": False,
            "poll_count": 0,
            "stopped_reason": "non_trading_day",
            "reason": "非交易日，未提醒、未轮询",
        }
    status = _record_status(day)
    initial_missing = [name for name in ("amv", "trades") if not status[name]]
    if not initial_missing:
        return {
            "date": day,
            "trading_day": True,
            "sent": False,
            "completed": True,
            "poll_count": 0,
            "stopped_reason": "already_complete",
            "amv_confirmed": True,
            "trade_recorded": True,
            "initial_missing": [],
            "missing": [],
            "reason": "15:00 两项已齐，未提醒、未启动轮询",
        }

    _send_text(_message(day, initial_missing))
    sleep = sleep_fn or time.sleep
    last_ingest: dict = {}
    for poll_count in range(1, max_polls + 1):
        sleep(600)
        last_ingest = ingest_fn({}).get("artifact", {})
        status = _record_status(day)
        missing = [name for name in ("amv", "trades") if not status[name]]
        if not missing:
            return {
                "date": day,
                "trading_day": True,
                "sent": True,
                "completed": True,
                "poll_count": poll_count,
                "stopped_reason": "received_complete",
                "amv_confirmed": True,
                "trade_recorded": True,
                "initial_missing": initial_missing,
                "missing": [],
                "ingest": last_ingest,
                "reason": f"第 {poll_count} 次轮询后两项收齐，已停止",
            }

    labels = {"amv": "0AMV", "trades": "交易记录"}
    missing = [name for name in ("amv", "trades") if not status[name]]
    return {
        "date": day,
        "trading_day": True,
        "sent": True,
        "completed": False,
        "poll_count": max_polls,
        "stopped_reason": "deadline",
        "amv_confirmed": status["amv"],
        "trade_recorded": status["trades"],
        "initial_missing": initial_missing,
        "missing": missing,
        "ingest": last_ingest,
        "reason": "截至 17:00 仍未收齐：" + "、".join(labels[name] for name in missing),
    }


def run(ctx) -> dict:
    # Automation 按绝对路径加载本文件，不保证仓库根在 sys.path；同目录桥也按路径加载。
    import importlib.util

    bridge_path = Path(__file__).with_name("feishu_inbound_bridge.py")
    spec = importlib.util.spec_from_file_location("feishu_inbound_bridge", bridge_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载入站桥: {bridge_path}")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    day = datetime.now(CST).date().isoformat()
    return {"artifact": _run_collection_day(day, bridge.run)}
