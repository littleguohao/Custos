# -*- coding: utf-8 -*-
"""Deterministic RSS/Atom collector with strict JSON and source-quality metadata."""

from __future__ import annotations
import argparse, hashlib, html, json, os, pathlib, re, ssl, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from custos.datasource.news.source_name_overrides import fix_source_name


from custos.core.paths import LOGS, NEWS_DIR, RSS_SOURCE_REGISTRY_FILE, cn_now  # noqa: E402
from custos.core.contracts import require  # noqa: E402
from custos.core.net_retry import retry_call  # noqa: E402

REG = RSS_SOURCE_REGISTRY_FILE
DATA = NEWS_DIR / "rss"
LOG = LOGS / "rss"

# 单个 feed 的字节上限。国家统计局的 feed 实测约 4.5MB,所以上限不能定得太小;
# 但 r.read() 完全不设限意味着任何被劫持/故障的源都能把内存打爆,故设 16MB 硬顶。
MAX_FEED_BYTES = 16 * 1024 * 1024
# 只扫文件头：实体声明必须在 DOCTYPE 内部子集里，不可能出现在正文深处。
MAX_DTD_SCAN_BYTES = 64 * 1024
# source_id 会直接拼进落盘文件名,必须白名单,否则 registry 里一个 "../../x" 就能写到库外。
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


JIN10_TOKEN_FALLBACK = (
    pathlib.Path.home() / ".openclaw-tdxclaw" / "secrets" / "jin10_mcp_token"
)


def _jin10_token() -> str:
    """金十 Bearer token 解析：① 环境变量 `JIN10_MCP_TOKEN`（v0.95 主通道）；
    ② 兜底文件 `JIN10_TOKEN_FALLBACK`（v0.113）。

    为什么加兜底：OpenClaw 网关（TdxClaw.exe）的环境继承自其启动者，Windows 上
    `setx` 后新进程未必拿到新变量（启动者 env 快照是旧的）——实测网关有
    TDX_MCP_URL 却没有后设的 JIN10_MCP_TOKEN，cron 任务反复「token 未设置」。
    兜底文件仍在**仓库外**（用户 home 下），token 不入库的安全性质不变。
    异常信息不 echo token 本体。
    """
    tok = os.environ.get(JIN10_TOKEN_ENV, "").strip()
    if tok:
        return tok
    try:
        if JIN10_TOKEN_FALLBACK.exists():
            return JIN10_TOKEN_FALLBACK.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def _redact(text: str) -> str:
    """去掉异常信息里的 query string——feed URL 可能带 token/appkey。"""
    return re.sub(r'\?[^\s\'"]*', "?<redacted>", str(text))


def build_ssl_context(src: dict) -> tuple[ssl.SSLContext, bool]:
    """按源配置构造 SSL 上下文,返回 (ctx, transport_verified)。

    关闭校验必须**同时**显式写 ssl_insecure_ack:tier S 的政府源一旦关掉校验,
    中间人就能伪造"国务院发文"进入正式复盘并被标成 source_confirmed。三个政府源
    历史上带着 ssl_verify=false,而实测证书链完全正常——这属于无必要的历史遗留,
    已从 registry 移除。保留这条通道只为应对真实的证书故障,且必须留痕。
    """
    ctx = ssl.create_default_context()
    if src.get("ssl_verify", True) is False:
        if not src.get("ssl_insecure_ack"):
            raise ValueError(
                f"{src['id']}: ssl_verify=false 需同时设 ssl_insecure_ack=true 以显式承担中间人风险"
            )
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx, False
    return ctx, True


def _read_limited(resp) -> bytes:
    raw = resp.read(MAX_FEED_BYTES + 1)
    if len(raw) > MAX_FEED_BYTES:
        raise ValueError(f"feed exceeds {MAX_FEED_BYTES} bytes, refused")
    return raw


def text(node, names):
    for child in node.iter():
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return ""


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def iso_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        return d.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            return (
                datetime.fromisoformat(s.replace("Z", "+00:00"))
                .astimezone(timezone.utc)
                .isoformat()
            )
        except Exception:
            return None


def _tier_quality(tier, transport_verified=True):
    """来源等级 → 证据质量。传输未经校验时**不得**给 confirmed。

    tier 表达的是"这个机构说的话有多权威",transport_verified 表达的是"这段字节
    真的来自那个机构吗"。后者不成立时前者无意义,故降级为 candidate 并由
    transport_verified 字段留痕,下游可据此拒绝把它当既成事实。
    """
    if not transport_verified:
        return "candidate"
    return "candidate" if tier in {"B", "C"} else "confirmed"


def refuse_entity_expansion(decoded: str) -> None:
    """拒收**嵌套实体声明**的 XML —— 这是「billion laughs」放大攻击的特征。

    ⚠️ 实测（2026-08-07）：`xml.etree.ElementTree` 对两类实体攻击的表现**不同**：

        外部实体（XXE, `<!ENTITY x SYSTEM "file:///etc/passwd">`）→ 已被拒 ✅
          ParseError: undefined entity —— 不必额外防。
        内部实体嵌套（`<!ENTITY b "&a;&a;...">`）→ **可行** ⚠️
          345 字节的 payload 4 层展开出 500 KB；再加两层就是 50 MB。
          `MAX_FEED_BYTES`（16 MB）只限**输入**大小，管不住展开后的内存。

    为什么这条路径值得防：这些 feed 是**远端不可信输入**，而
    `build_ssl_context` 按设计允许个别源 `ssl_verify=false`（需显式 ack 承担风险）。
    那类源上的中间人可以直接投递放大 payload，把 08:50 采集 OOM 掉。

    为什么**不**引 `defusedxml`：只为一条已知特征加一个依赖不划算。
    为什么只拒**嵌套**而不是所有 `<!ENTITY`：扁平声明（`<!ENTITY nbsp "&#160;">`）
    是真实 feed 的合法用法且无放大能力，一律拒会误杀正常源。
    放大的必要条件是「一个实体的值里引用了另一个被声明的实体」，只拦这个。
    """
    head = decoded[:MAX_DTD_SCAN_BYTES]
    if "<!ENTITY" not in head:
        return
    declared = dict(re.findall(r'<!ENTITY\s+(\S+)\s+["\']([^"\']*)["\']', head))
    for name, value in declared.items():
        for ref in re.findall(r"&(\w+);", value):
            if ref in declared:
                raise ValueError(
                    f"refused nested XML entity declaration: &{name}; references &{ref}; "
                    "(billion-laughs signature)"
                )


def parse_feed(raw, src, fetched, transport_verified=True):
    # ElementTree rejects some valid legacy multibyte declarations (for
    # example GB2312). Decode explicitly and normalize the XML declaration.
    declaration = raw[:200].decode("ascii", errors="ignore")
    match = re.search(r'encoding=["\']([^"\']+)', declaration, re.I)
    encoding = (match.group(1) if match else "utf-8").lower()
    if encoding in {"gb2312", "gbk", "gb_2312-80"}:
        encoding = "gb18030"
    decoded = raw.decode(encoding, errors="replace")
    decoded = re.sub(
        r'(<\?xml[^>]*encoding=)["\'][^"\']+["\']',
        r'\1"utf-8"',
        decoded,
        count=1,
        flags=re.I,
    )
    refuse_entity_expansion(decoded)
    root = ET.fromstring(decoded)
    nodes = []
    for e in root.iter():
        if e.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}:
            nodes.append(e)
    items = []
    for e in nodes:
        title = clean(text(e, {"title"}))
        summary = clean(text(e, {"description", "summary", "content"}))
        link = text(e, {"link"})
        if not link:
            for c in e.iter():
                if c.tag.rsplit("}", 1)[-1].lower() == "link" and c.attrib.get("href"):
                    link = c.attrib["href"]
                    break
        published = text(e, {"pubdate", "published", "updated", "date"})
        guid = text(e, {"guid", "id"})
        norm = re.sub(r"\W+", "", title.lower())[:300]
        item_id = hashlib.sha256(
            (src["id"] + "|" + (guid or link or norm)).encode()
        ).hexdigest()[:24]
        dup = hashlib.sha256(norm.encode()).hexdigest()[:20] if norm else item_id
        corrected_name = fix_source_name(src["id"], src["name"])
        items.append(
            {
                "item_id": item_id,
                "published_at": iso_date(published),
                "fetched_at": fetched,
                "source_id": src["id"],
                "source_name": corrected_name,
                "source_tier": src["tier"],
                "category": src["category"],
                "title": title,
                "summary": summary[:2000],
                "source_url": link,
                "feed_url": src["url"],
                "affected_entities": [],
                "affected_sectors": [],
                "direction": "uncertain",
                "impact_horizon": "unknown",
                "fact": title,
                "inference": "",
                "validation_condition": [],
                "quality": _tier_quality(src["tier"], transport_verified),
                "confirmed": src["tier"] in {"S", "A"} and transport_verified,
                "transport_verified": transport_verified,
                "duplicate_group_id": dup,
            }
        )
    return items


# ---------------------------------------------------------------------------
# 金十数据快讯（jin10_mcp）：MCP = JSON-RPC 2.0 over HTTP POST，响应是 SSE
# （data: 行包 JSON-RPC 消息）。握手 initialize（响应头给 mcp-session-id，
# 后续请求必须带）→ notifications/initialized → tools/call list_flash，
# 分页 data.next_cursor / has_more（2026-08-21 冒烟实测；样例文档里分页键
# 写作 next_offset，两个键都认）。Bearer token 走环境变量 JIN10_MCP_TOKEN，
# 不写进 registry（那是入库的治理文件）。
# ---------------------------------------------------------------------------
JIN10_TOKEN_ENV = "JIN10_MCP_TOKEN"
JIN10_PROTOCOL_VERSION = "2025-11-25"
# 每页 20 条，5 页 ≈ 100 条 —— 对齐 wscn limit=100 的盘前覆盖量级；
# 限流 1500 次/工具/天，08:50 每天一轮（1 握手 + ≤5 页）可忽略。
JIN10_MAX_PAGES = 5


def _sse_data_messages(raw: bytes) -> list[dict]:
    """拆 SSE 响应里的 JSON-RPC 消息（data: 行）；非 SSE 则按整体 JSON 兜底。"""
    msgs = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                msgs.append(json.loads(payload))
    if not msgs:
        msgs.append(json.loads(raw.decode("utf-8", errors="replace")))
    return msgs


def _jin10_rpc(url, token, ctx, timeout, payload, session_id=None):
    """单次 JSON-RPC POST → (最后一条消息 dict 或 None, mcp-session-id 或 None)。

    token 只进 Authorization 头，不进 URL ⇒ 异常信息（_redact 处理 query string）
    不含凭据。通知（notifications/*）响应 202 无 body，返回消息为 None。
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 TdxClawRSS/1.0",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    with retry_call(
        lambda: urllib.request.urlopen(req, timeout=timeout, context=ctx)
    ) as r:
        raw = _read_limited(r)
        sid = r.headers.get("mcp-session-id")
    msgs = _sse_data_messages(raw) if raw.strip() else []
    return (msgs[-1] if msgs else None), sid


def _jin10_extract_data(msg) -> dict:
    """tools/call 消息 → data 块。优先 result.structuredContent.data（机器解析
    主来源），result.content[0].text（内嵌 JSON 字符串）只做兜底；JSON-RPC
    error 与 isError=true 都按失败抛出。"""
    if not isinstance(msg, dict):
        raise ValueError("jin10 空响应（tools/call 无消息）")
    if msg.get("error"):
        raise ValueError(f"jin10 JSON-RPC error: {msg['error']!r}")
    result = msg.get("result") or {}
    if result.get("isError"):
        texts = [
            c.get("text", "")
            for c in result.get("content") or []
            if isinstance(c, dict)
        ]
        raise ValueError(f"jin10 工具业务错误(isError): {';'.join(texts)[:200]}")
    sc = result.get("structuredContent")
    if isinstance(sc, dict) and isinstance(sc.get("data"), dict):
        return sc["data"]
    for c in result.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
            data = json.loads(c["text"]).get("data")
            if isinstance(data, dict):
                return data
    raise ValueError("jin10 响应缺 structuredContent.data 且 content 兜底失败")


def fetch_jin10_flash(url, token, ctx, timeout, max_pages=JIN10_MAX_PAGES):
    """握手 + 翻页抓 list_flash，返回原始条目 list（content/time/url[/id/title]）。"""
    init, sid = _jin10_rpc(
        url,
        token,
        ctx,
        timeout,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": JIN10_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "custos-rss", "version": "1.0"},
            },
        },
    )
    if not isinstance(init, dict) or "result" not in init:
        raise ValueError("jin10 initialize 握手失败")
    _jin10_rpc(
        url,
        token,
        ctx,
        timeout,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id=sid,
    )
    entries: list[dict] = []
    cursor = None
    for page in range(max_pages):
        args = {"cursor": cursor} if cursor else {}
        msg, _ = _jin10_rpc(
            url,
            token,
            ctx,
            timeout,
            {
                "jsonrpc": "2.0",
                "id": 100 + page,
                "method": "tools/call",
                "params": {"name": "list_flash", "arguments": args},
            },
            session_id=sid,
        )
        data = _jin10_extract_data(msg)
        entries.extend(e for e in data.get("items") or [] if isinstance(e, dict))
        cursor = data.get("next_cursor") or data.get("next_offset")
        if not data.get("has_more") or not cursor:
            break
    return entries


def parse_jin10_flash(entries, src, fetched, transport_verified=True):
    """金十快讯条目 → rss_evidence 归一化（字段映射对齐 parse_wscn_lives）。

    实测（2026-08-21 冒烟）：content/time/url 必有；id/title 可能缺省——
    id 缺时从 url 末段推导（flash.jin10.com/detail/{id}），title 空取
    content 前 50 字（与 wscn 一致）。
    """
    items = []
    for e in entries:
        content = clean(e.get("content"))
        if not content:
            continue
        url = str(e.get("url") or "")
        eid = str(e.get("id") or "") or url.rstrip("/").rsplit("/", 1)[-1]
        try:
            published = (
                datetime.fromisoformat(str(e.get("time")).replace("Z", "+00:00"))
                .astimezone(timezone.utc)
                .isoformat()
            )
        except (TypeError, ValueError):
            published = None
        norm = re.sub(r"\W+", "", content.lower())[:300]
        item_id = hashlib.sha256(
            (src["id"] + "|" + (eid or norm)).encode()
        ).hexdigest()[:24]
        dup = hashlib.sha256(norm.encode()).hexdigest()[:20] if norm else item_id
        corrected_name = fix_source_name(src["id"], src["name"])
        title = clean(e.get("title")) or content[:50]
        items.append(
            {
                "item_id": item_id,
                "published_at": published,
                "fetched_at": fetched,
                "source_id": src["id"],
                "source_name": corrected_name,
                "source_tier": src["tier"],
                "category": src["category"],
                "title": title,
                "summary": content[:2000],
                "source_url": url
                or (f"https://flash.jin10.com/detail/{eid}" if eid else ""),
                "feed_url": src["url"],
                "affected_entities": [],
                "affected_sectors": [],
                "direction": "uncertain",
                "impact_horizon": "unknown",
                "fact": title,
                "inference": "",
                "validation_condition": [],
                "quality": _tier_quality(src["tier"], transport_verified),
                "confirmed": src["tier"] in {"S", "A"} and transport_verified,
                "transport_verified": transport_verified,
                "duplicate_group_id": dup,
            }
        )
    return items


def parse_wscn_lives(raw, src, fetched, transport_verified=True):
    # WallstreetCN lives JSON API: {"code":20000,"data":{"items":[{id,content,display_time,uri,...}]}}
    data = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(data, dict) or data.get("code") != 20000:
        raise ValueError(
            "wscn_lives bad response code: "
            + repr(data.get("code") if isinstance(data, dict) else type(data).__name__)
        )
    entries = data.get("data", {}).get("items")
    if not isinstance(entries, list):
        raise ValueError("wscn_lives malformed payload: data.items missing")
    items = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        content = clean(e.get("content"))
        if not content:
            continue
        try:
            published = datetime.fromtimestamp(
                int(e.get("display_time")), timezone.utc
            ).isoformat()
        except (TypeError, ValueError, OverflowError):
            published = None
        norm = re.sub(r"\W+", "", content.lower())[:300]
        item_id = hashlib.sha256(
            (src["id"] + "|" + str(e.get("id"))).encode()
        ).hexdigest()[:24]
        dup = hashlib.sha256(norm.encode()).hexdigest()[:20] if norm else item_id
        corrected_name = fix_source_name(src["id"], src["name"])
        title = content[:50]
        items.append(
            {
                "item_id": item_id,
                "published_at": published,
                "fetched_at": fetched,
                "source_id": src["id"],
                "source_name": corrected_name,
                "source_tier": src["tier"],
                "category": src["category"],
                "title": title,
                "summary": content[:2000],
                "source_url": e.get("uri")
                or (
                    f"https://wallstreetcn.com/livenews/{e.get('id')}"
                    if e.get("id") is not None
                    else ""
                ),
                "feed_url": src["url"],
                "affected_entities": [],
                "affected_sectors": [],
                "direction": "uncertain",
                "impact_horizon": "unknown",
                "fact": title,
                "inference": "",
                "validation_condition": [],
                "quality": _tier_quality(src["tier"], transport_verified),
                "confirmed": src["tier"] in {"S", "A"} and transport_verified,
                "transport_verified": transport_verified,
                "duplicate_group_id": dup,
            }
        )
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--limit-per-feed", type=int, default=100)
    a = ap.parse_args()
    cfg = json.loads(REG.read_text(encoding="utf-8-sig"))
    fetched = cn_now().isoformat(timespec="seconds")
    day = DATA / "raw" / a.date
    day.mkdir(parents=True, exist_ok=True)
    normalized = []
    log = []
    for src in cfg["sources"]:
        if not src.get("enabled") or not src.get("url"):
            continue
        row = {"source_id": src["id"], "url": src["url"], "fetched_at": fetched}
        try:
            if not SAFE_ID.match(str(src.get("id", ""))):
                raise ValueError(
                    f"unsafe source id {src.get('id')!r}: 会拼进落盘文件名"
                )
            ctx, verified = build_ssl_context(src)
            row["transport_verified"] = verified
            if src.get("type") == "jin10_mcp":
                # 金十 MCP（JSON-RPC POST + SSE），与上方 RSS/JSON GET 不同路；
                # token 走环境变量（v0.113 起兜底用户 secrets 文件），缺席时
                # 该源标 failed 留痕、不炸链路（与其他源一致）。
                token = _jin10_token()
                if not token:
                    raise ValueError(
                        f"{JIN10_TOKEN_ENV} 未设置（Bearer token 不入库，"
                        "走环境变量或 ~/.openclaw-tdxclaw/secrets/jin10_mcp_token）"
                    )
                entries = fetch_jin10_flash(src["url"], token, ctx, a.timeout)
                raw = json.dumps(entries, ensure_ascii=False).encode("utf-8")
                row.update(
                    http_status=200,
                    final_url=src["url"],
                    content_type="application/json",
                    bytes=len(raw),
                )
                (day / f"{src['id']}.json").write_bytes(raw)
                items = parse_jin10_flash(entries, src, fetched, verified)[
                    : a.limit_per_feed
                ]
                normalized.extend(items)
                row.update(status="ok", items=len(items))
                log.append(row)
                continue
            req = urllib.request.Request(
                src["url"],
                headers={
                    "User-Agent": "Mozilla/5.0 TdxClawRSS/1.0",
                    "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml",
                },
            )
            with retry_call(
                lambda: urllib.request.urlopen(req, timeout=a.timeout, context=ctx),
                # 2026-08-21（v0.99）：gov_cn/统计局源的 SSL CERTIFICATE_VERIFY_FAILED
                # 实测是 CDN 边缘节点间歇返回坏证书链（同一 URL 直连 200，隔几分钟
                # 恢复）——默认 tries=3/backoff=2s/jitter=0 的三连重试在几秒内打满，
                # 全撞同一坏节点 ⇒ 加长退避 + jitter 拉开重试时点，给路由换节点的
                # 机会。代价仅是失败源多等 ~15s（best-effort stage，可接受）。
                tries=4,
                backoff=2.0,
                jitter=0.5,
            ) as r:
                raw = _read_limited(r)
                row.update(
                    http_status=r.status,
                    final_url=r.geturl(),
                    content_type=r.headers.get("content-type", ""),
                    bytes=len(raw),
                )
            if src.get("type") == "wscn_lives":
                (day / f"{src['id']}.json").write_bytes(raw)
                items = parse_wscn_lives(raw, src, fetched, verified)[
                    : a.limit_per_feed
                ]
            else:
                (day / f"{src['id']}.xml").write_bytes(raw)
                items = parse_feed(raw, src, fetched, verified)[: a.limit_per_feed]
            normalized.extend(items)
            row.update(status="ok", items=len(items))
        except Exception as e:
            row.update(status="failed", error=_redact(repr(e)), items=0)
        log.append(row)
    # exact item IDs and normalized-title duplicate groups are deterministic.
    seen = set()
    unique = []
    for x in sorted(
        normalized,
        key=lambda z: (z.get("published_at") or "", z["item_id"]),
        reverse=True,
    ):
        if x["item_id"] in seen:
            continue
        seen.add(x["item_id"])
        unique.append(x)
    require("rss_evidence", unique)
    out = DATA / "normalized" / f"{a.date}_rss_evidence.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.mkdir(parents=True, exist_ok=True)
    lp = LOG / f"{a.date}_collection_log.json"
    lp.write_text(
        json.dumps(
            {
                "date": a.date,
                "fetched_at": fetched,
                "sources": log,
                "item_count": len(unique),
                "output": str(out),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(out),
                "log": str(lp),
                "items": len(unique),
                "sources_ok": sum(x["status"] == "ok" for x in log),
                "sources_failed": sum(x["status"] != "ok" for x in log),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
