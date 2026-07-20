# -*- coding: utf-8 -*-
"""飞书报告投递：聊天执行摘要 + 完整报告 md 文件附件。

用法:
    uv run python 07_tools/feishu_report_publisher.py --report <md路径> \
        --title "盘前日报" --date 2026-07-20 [--dry-run]

凭据优先级: 环境变量 FEISHU_APP_ID/FEISHU_APP_SECRET; 否则读 OPENCLAW_CONFIG
指向的 openclaw.json 的 channels.feishu.accounts.default.appId/appSecret。
接收人 open_id 可用 FEISHU_TO_OPEN_ID 覆盖。

stdout 是机器可消费协议: 成功打印一行 JSON {"sent": true, "file_key": ..., "summary_len": N};
dry-run 打印摘要后打印 {"sent": false, "dry_run": true, ...}; 失败打 stderr [WARN] 并 exit 1。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import requests  # noqa: E402

from net_retry import retry_call  # noqa: E402
from paths import BASE  # noqa: E402

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FILES_URL = "https://open.feishu.cn/open-apis/im/v1/files"
MESSAGES_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"

DEFAULT_OPENCLAW_CONFIG = r"C:\Users\gh\.openclaw-tdxclaw\openclaw.json"
DEFAULT_TO_OPEN_ID = "ou_54ca3ea3b343e4d868b66b7084ed3be1"

HTTP_TIMEOUT = 15
TRUNCATE_MARKER = "\n...(完整报告见附件)"

_CODE_RE = re.compile(r"(?<![\d.:])\d{6}(?![\d.:])")
_PRIORITY_RE = re.compile(r"\bP[012]\b")


class FeishuError(RuntimeError):
    """飞书 API 返回 code != 0 或凭据缺失。"""


# ---------------------------------------------------------------- credentials

def load_credentials(env=None) -> dict:
    """读取 app_id/app_secret, 环境变量优先, 其次 openclaw.json。"""
    env = os.environ if env is None else env
    app_id = (env.get("FEISHU_APP_ID") or "").strip()
    app_secret = (env.get("FEISHU_APP_SECRET") or "").strip()
    if app_id and app_secret:
        return {"app_id": app_id, "app_secret": app_secret, "source": "env"}
    cfg_path = Path(env.get("OPENCLAW_CONFIG") or DEFAULT_OPENCLAW_CONFIG)
    if not cfg_path.exists():
        raise FeishuError(f"缺少飞书凭据: 未设置 FEISHU_APP_ID/FEISHU_APP_SECRET 且找不到 {cfg_path}")
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        acct = cfg["channels"]["feishu"]["accounts"]["default"]
        app_id, app_secret = str(acct["appId"]), str(acct["appSecret"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FeishuError(f"openclaw.json 飞书凭据解析失败: {exc}") from exc
    if not app_id or not app_secret:
        raise FeishuError(f"openclaw.json 飞书凭据为空: {cfg_path}")
    return {"app_id": app_id, "app_secret": app_secret, "source": str(cfg_path)}


def resolve_to_open_id(env=None) -> str:
    env = os.environ if env is None else env
    return (env.get("FEISHU_TO_OPEN_ID") or "").strip() or DEFAULT_TO_OPEN_ID


# ------------------------------------------------------------------- summary

def _is_heading(line: str) -> bool:
    return line.lstrip().startswith("#")


def _section_body(lines: list[str], start: int) -> list[str]:
    """取 start 行之后到下一个标题前的正文: 跳过空行、表格行、引用行。"""
    body = []
    for line in lines[start:]:
        s = line.strip()
        if _is_heading(line):
            break
        if not s or s.startswith("|") or s.startswith(">"):
            continue
        body.append(line.rstrip())
    return body


def _core_section(lines: list[str]) -> list[str]:
    """优先"核心结论"节, 否则首个有正文的 ## 节。"""
    h2 = [i for i, line in enumerate(lines) if line.lstrip().startswith("## ")]
    for i in h2:
        if "核心结论" in lines[i]:
            body = _section_body(lines, i + 1)
            if body:
                return body
    for i in h2:
        body = _section_body(lines, i + 1)
        if body:
            return body
    return []


def build_exec_summary(md_text: str, title: str, date: str, limit: int = 800) -> str:
    """确定性提取执行摘要: 标题行 + 核心结论节 + 逐股行动行 + 权限行。

    只提取报告中已存在的内容, 不编造; 超过 limit 截断并标注附件提示。
    """
    lines = md_text.splitlines()
    report_title = ""
    for line in lines:
        if line.lstrip().startswith("# "):
            report_title = line.lstrip()[2:].strip()
            break

    actions = []
    for line in lines:
        s = line.strip()
        if s.startswith("- ") and (_CODE_RE.search(s) or _PRIORITY_RE.search(s)):
            actions.append(s)
            if len(actions) >= 6:
                break

    core = _core_section(lines)
    used = {l.strip() for l in core} | set(actions)
    permissions = []
    for key in ("禁止", "权限"):
        for line in lines:
            if len(permissions) >= 2:
                break
            s = line.strip()
            if s.startswith("|") or s.startswith("#") or s in used:
                continue
            if key in s and s not in permissions:
                permissions.append(s)

    parts = [f"【{title}】{date}"]
    if report_title:
        parts.append(report_title)
    if core:
        parts.append("核心结论：")
        parts.extend(core)
    if actions:
        parts.append("逐股行动：")
        parts.extend(actions)
    if permissions:
        parts.append("权限：")
        parts.extend(permissions)

    text = "\n".join(parts)
    if len(text) > limit:
        text = text[: max(0, limit - len(TRUNCATE_MARKER))].rstrip() + TRUNCATE_MARKER
    return text


# ---------------------------------------------------------------------- http

def _post(url: str, *, headers=None, json_body=None, data=None, files=None) -> dict:
    """POST JSON 接口, 指数退避重试, 返回解析后的 dict。"""
    def _do() -> dict:
        resp = requests.post(url, headers=headers, json=json_body, data=data,
                             files=files, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    return retry_call(_do, tries=2, backoff=1.5)


def _check_code(body: dict, step: str) -> None:
    if body.get("code") != 0:
        raise FeishuError(f"{step} 失败: code={body.get('code')} msg={body.get('msg')}")


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    body = _post(TOKEN_URL, json_body={"app_id": app_id, "app_secret": app_secret})
    _check_code(body, "获取 tenant_access_token")
    token = body.get("tenant_access_token")
    if not token:
        raise FeishuError("获取 tenant_access_token 失败: 响应缺少 token")
    return token


def upload_report_file(token: str, report_path: Path, file_name: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    with open(report_path, "rb") as fh:
        body = _post(FILES_URL, headers=headers,
                     data={"file_type": "stream", "file_name": file_name},
                     files={"file": (file_name, fh, "text/markdown")})
    _check_code(body, "上传报告文件")
    file_key = (body.get("data") or {}).get("file_key")
    if not file_key:
        raise FeishuError("上传报告文件失败: 响应缺少 file_key")
    return file_key


def send_message(token: str, to_open_id: str, msg_type: str, content: dict) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    body = _post(MESSAGES_URL, headers=headers, json_body={
        "receive_id": to_open_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    })
    _check_code(body, f"发送 {msg_type} 消息")


# ----------------------------------------------------------------------- cli

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="飞书报告投递: 执行摘要 + md 文件附件")
    parser.add_argument("--report", required=True, help="报告 md 路径")
    parser.add_argument("--title", required=True, help="报告标题, 如 盘前日报")
    parser.add_argument("--date", required=True, help="报告日期, 如 2026-07-20")
    parser.add_argument("--limit", type=int, default=800, help="摘要字数上限")
    parser.add_argument("--dry-run", action="store_true", help="只生成摘要打印, 不发送")
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = BASE / report_path
    if not report_path.exists():
        print(f"[WARN] 报告文件不存在: {report_path}", file=sys.stderr)
        return 1

    md_text = report_path.read_text(encoding="utf-8")
    summary = build_exec_summary(md_text, args.title, args.date, limit=args.limit)

    if args.dry_run:
        print(summary)
        print(json.dumps({"sent": False, "dry_run": True, "summary_len": len(summary)},
                         ensure_ascii=False))
        return 0

    try:
        creds = load_credentials()
        to_open_id = resolve_to_open_id()
        token = get_tenant_access_token(creds["app_id"], creds["app_secret"])
        file_key = upload_report_file(token, report_path, f"{args.date}_{args.title}.md")
        send_message(token, to_open_id, "file", {"file_key": file_key})
        send_message(token, to_open_id, "text", {"text": summary})
    except Exception as exc:
        print(f"[WARN] feishu_report_publisher 失败: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"sent": True, "file_key": file_key, "summary_len": len(summary)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
