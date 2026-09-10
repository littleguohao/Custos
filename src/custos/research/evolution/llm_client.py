# -*- coding: utf-8 -*-
"""LLM 客户端：OpenAI 兼容 HTTP 协议层（研究侧因子进化引擎专用）。

- 依赖只有 requests（项目既有依赖），不引任何 SDK；协议细节拆成模块级小函数
  （``_post`` / ``_extract_content`` / ``_extract_tokens``），单测可 monkeypatch，
  全程无网络也能测。
- **模块 import 时不读环境变量、不发任何请求**：环境读取只发生在
  ``LLMConfig.from_env()``，网络只发生在 ``LLMClient.chat()``。
- fail-closed：``from_env`` 任一必需变量缺失/非法返回 None（不 raise），由上层
  （CLI）决定降级还是报错；``chat`` 重试耗尽后 raise ``LLMError``。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

ENV_BASE_URL = "CUSTOS_LLM_BASE_URL"
ENV_API_KEY = "CUSTOS_LLM_API_KEY"
ENV_MODEL = "CUSTOS_LLM_MODEL"
ENV_TIMEOUT = "CUSTOS_LLM_TIMEOUT"

_DEFAULT_TIMEOUT = 60

# base_url 协议纪律：API key 走 Authorization 头，明文 http 等于把密钥交给
# 中间人 —— 只许 https；唯一例外是本机回环（测试常 spin 本地 http server）。
_LOCAL_HTTP_HOSTS = ("localhost", "127.0.0.1", "::1")


def _base_url_allowed(url: str) -> bool:
    """https 一律允许；http 仅允许本机回环；其余 scheme/形态一律拒。"""
    if url.startswith("https://"):
        return True
    if not url.startswith("http://"):
        return False
    host = (urlparse(url).hostname or "").lower()
    return host in _LOCAL_HTTP_HOSTS


class LLMError(RuntimeError):
    """LLM 调用最终失败（重试耗尽 / 响应形状不符），上层据此 fail-closed。

    附加属性（重试策略用；位置参数 message 用法兼容旧调用方）：
    ``status_code`` HTTP 状态码（≥400 时记录）；``retry_after`` 429/503 响应的
    Retry-After 头或文本里解析出的等待秒数；``is_timeout`` 请求超时标记
    （连续超时熔断用）。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        is_timeout: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.is_timeout = is_timeout


class ChatLLM(Protocol):
    """operators / loop 依赖的最小 LLM 接口（LLMClient 与测试替身都满足）。"""

    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = True,
        json_schema: dict | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class LLMConfig:
    """LLM 连接配置（immutable；from_env 之外也可手工构造注入测试）。

    api_key 不进 repr（防日志 / pytest 失败回显 / 报错文本泄钥）；base_url
    必须 https（本机回环 http 除外），违规在构造期 ValueError（fail-closed）。
    """

    base_url: str  # 如 https://api.openai.com/v1（尾部斜杠在 from_env 去掉）
    api_key: str = field(repr=False)
    model: str
    timeout: int = _DEFAULT_TIMEOUT
    max_retry: int = 3
    # 连续超时熔断阈值：连续 is_timeout 失败这么多次立即 raise（不消耗剩余重试）
    timeout_fail_limit: int = 3

    def __post_init__(self) -> None:
        if not _base_url_allowed(self.base_url):
            raise ValueError(
                f"base_url 必须 https（或本机回环 http: localhost/127.0.0.1/::1）: "
                f"{self.base_url!r}"
            )

    @classmethod
    def from_env(cls) -> "LLMConfig | None":
        """读 CUSTOS_LLM_* 环境变量；任一必需项缺失/非法 → None（不 raise）。

        必需：CUSTOS_LLM_BASE_URL / CUSTOS_LLM_API_KEY / CUSTOS_LLM_MODEL；
        可选：CUSTOS_LLM_TIMEOUT（秒，非正整数视为未配置 → None，fail-closed）。
        base_url 非 https 且非本机回环 → 同样 None（构造期校验的前移，不 raise）。
        """
        base_url = os.environ.get(ENV_BASE_URL, "").strip()
        api_key = os.environ.get(ENV_API_KEY, "").strip()
        model = os.environ.get(ENV_MODEL, "").strip()
        if not (base_url and api_key and model):
            return None
        if not _base_url_allowed(base_url):
            return None
        timeout = _DEFAULT_TIMEOUT
        raw_timeout = os.environ.get(ENV_TIMEOUT, "").strip()
        if raw_timeout:
            try:
                timeout = int(raw_timeout)
            except ValueError:
                return None
            if timeout <= 0:
                return None
        return cls(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            timeout=timeout,
        )


# 429/503 响应文本里的限流等待提示（大小写不敏感，允许小数秒）。
_RETRY_AFTER_RE = re.compile(r"retry after (\d+(?:\.\d+)?)", re.IGNORECASE)


def _parse_retry_after(
    status_code: int, text: str, headers: Any = None
) -> float | None:
    """限流等待秒数；只认 429/503，解析不到返回 None。

    优先标准的 ``Retry-After`` 响应头（只认整数/小数秒形态；HTTP-date 形态
    不支持，落回文本提示），其次响应文本里的「retry after N」提示。返回值的
    封顶在 ``_backoff_seconds``（60s）统一做。
    """
    if status_code not in (429, 503):
        return None
    raw = (headers or {}).get("Retry-After")
    if raw is not None:
        try:
            secs = float(str(raw).strip())
        except ValueError:
            secs = None
        if secs is not None and secs >= 0:
            return secs
    m = _RETRY_AFTER_RE.search(text)
    return float(m.group(1)) if m else None


def _post(config: LLMConfig, payload: dict[str, Any]) -> dict[str, Any]:
    """一次 OpenAI 兼容 POST {base_url}/chat/completions；返回响应 JSON dict。

    超时 → LLMError(is_timeout=True)；HTTP ≥400 → status_code 入错（429/503
    附带 retry_after）；其余网络异常 / 响应非 JSON 一律 LLMError。
    """
    url = f"{config.base_url}/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=config.timeout,
        )
    except requests.Timeout as exc:
        raise LLMError(f"LLM 请求超时: {exc}", is_timeout=True) from exc
    except requests.RequestException as exc:
        raise LLMError(f"LLM 请求网络失败: {exc}") from exc
    if resp.status_code >= 400:
        raise LLMError(
            f"LLM HTTP {resp.status_code}: {resp.text[:200]}",
            status_code=resp.status_code,
            retry_after=_parse_retry_after(
                resp.status_code, resp.text, getattr(resp, "headers", None)
            ),
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise LLMError(f"LLM 响应不是 JSON: {resp.text[:200]}") from exc
    if not isinstance(data, dict):
        raise LLMError("LLM 响应 JSON 顶层不是对象")
    return data


def _extract_content(data: dict[str, Any]) -> str:
    """OpenAI 兼容响应 → content 文本；形状不符 / 空文本 → LLMError。"""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("LLM 响应缺 choices[0].message.content") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM 响应 content 为空或非字符串")
    return content


def _extract_tokens(data: dict[str, Any]) -> int:
    """usage.total_tokens；缺失/非数值 → 0（不计）。"""
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return 0
    v = usage.get("total_tokens")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    return int(v)


def _response_format_chain(
    json_mode: bool, json_schema: dict | None
) -> list[dict | None]:
    """response_format 降级链：json_schema → json_object → 不带（按需截短）。

    上游 RD-Agent 用 litellm 的模型数据库做能力探测；我们不引 litellm，
    这条链就是探测的替代 —— 400 类失败才沿链降级，其余错误原地重试。
    """
    if json_schema is not None:
        return [
            {
                "type": "json_schema",
                "json_schema": {"name": "evolution_proposal", "schema": json_schema},
            },
            {"type": "json_object"},
            None,
        ]
    if json_mode:
        return [{"type": "json_object"}, None]
    return [None]


def _backoff_seconds(attempt: int, last: LLMError | None) -> float:
    """retry_after 优先（封顶 60s），否则指数退避 2^attempt（封顶 30s）。"""
    if last is not None and last.retry_after is not None:
        return min(last.retry_after, 60.0)
    return min(2.0**attempt, 30.0)


class LLMClient:
    """OpenAI 兼容 chat 客户端：降级链重试 + 超时熔断 + token 计数。"""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.total_tokens = 0
        # 退避等待做成实例属性：测试替换掉即可避免真等（不 monkeypatch 全局 time）。
        self.sleep = time.sleep

    def _payload(
        self, messages: list[dict], response_format: dict | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.config.model, "messages": messages}
        if response_format is not None:
            payload["response_format"] = response_format
        return payload

    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = True,
        json_schema: dict | None = None,
    ) -> str:
        """发送一轮对话，返回 content 文本；重试耗尽 raise LLMError。

        - 降级链：给了 json_schema → 首轮 json_schema 格式，HTTP 400 类失败后
          降级 json_object，再失败降级不带 response_format；400 之外的错误
          **不触发降级**，只原地重试。
        - 退避：上一次错误带 retry_after 按它睡（封顶 60s），否则 2^attempt
          封顶 30s。
        - 熔断：连续 is_timeout 失败达 ``config.timeout_fail_limit`` 次立即
          raise，不再消耗剩余重试。
        """
        attempts = max(self.config.max_retry, 1)
        chain = _response_format_chain(json_mode, json_schema)
        fmt_idx = 0
        last: LLMError | None = None
        consec_timeouts = 0
        for attempt in range(attempts):
            if attempt:
                self.sleep(_backoff_seconds(attempt, last))
            try:
                data = _post(self.config, self._payload(messages, chain[fmt_idx]))
                self.total_tokens += _extract_tokens(data)
                return _extract_content(data)
            except LLMError as exc:
                last = exc
                if exc.is_timeout:
                    consec_timeouts += 1
                    if consec_timeouts >= self.config.timeout_fail_limit:
                        raise LLMError(
                            f"连续 {consec_timeouts} 次请求超时，熔断不再重试: {exc}"
                        ) from exc
                else:
                    consec_timeouts = 0
                if exc.status_code is not None and 400 <= exc.status_code < 500:
                    fmt_idx = min(fmt_idx + 1, len(chain) - 1)
        raise LLMError(f"LLM 调用重试 {attempts} 次仍失败: {last}") from last
