# -*- coding: utf-8 -*-
"""Simple network fetch retry helper: exponential backoff, re-raise the last error."""
from __future__ import annotations

import random
import time
from typing import Any, Callable

import requests

# 可重试的 4xx。其余 4xx 是**确定性**失败(URL 写错、被封、参数非法):再请求 3 次
# 结果一模一样,只是把管线拖慢 backoff 之和,并给对端多刷两条 403 日志。
# 408 Request Timeout / 429 Too Many Requests 是明确的"稍后再来"语义,必须重试。
RETRYABLE_4XX = frozenset({408, 429})
DEFAULT_JITTER = 0.5      # 退避区间 [base, base*(1+jitter)]
DEFAULT_MAX_SLEEP = 60.0  # Retry-After 可能给出极大值,必须封顶


def _status_of(exc: BaseException) -> int | None:
    """从异常里取 HTTP 状态码。

    两套形态都要认:requests.HTTPError 挂 ``.response.status_code``;
    urllib.error.HTTPError 自身带 ``.code``(retry_call 的调用方用的是 urlopen)。
    """
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if isinstance(status, int):
        return status
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _is_retryable(exc: BaseException) -> bool:
    """HTTP 状态码可否重试。非 HTTPError(连接/超时/DNS)一律按可重试,保持原行为。"""
    status = _status_of(exc)
    if status is None:
        return True
    if 400 <= status < 500:
        return status in RETRYABLE_4XX
    return True


def _retry_after_seconds(exc: BaseException) -> float | None:
    """解析 Retry-After 头(仅支持秒数形态;HTTP-date 形态不猜,回落自有退避)。"""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None) or {}
    try:
        raw = headers.get("Retry-After")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        secs = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return secs if secs >= 0 else None


def _sleep_seconds(attempt: int, backoff: float, jitter: float,
                   exc: BaseException | None, max_sleep: float) -> float:
    """退避时长:优先服务端 Retry-After,否则指数退避 + jitter。

    jitter 的作用:一个时点会并发拉多个数据源(东财/Yahoo/腾讯/新浪),它们同时失败时
    没有 jitter 会在**同一毫秒**一起重试,继续互相撞车(并且更容易触发对端限流)。
    jitter=0 退回精确指数退避,保持既有调用方/测试的可预期行为。
    """
    hinted = _retry_after_seconds(exc) if exc is not None else None
    if hinted is not None:
        return min(hinted, max_sleep)
    base = backoff ** attempt
    if jitter:
        base *= 1.0 + random.random() * jitter
    return min(base, max_sleep)


def fetch_with_retry(url: str, *, tries: int = 3, timeout: int = 15, backoff: float = 2.0,
                     session: requests.Session | None = None,
                     jitter: float = DEFAULT_JITTER,
                     max_sleep: float = DEFAULT_MAX_SLEEP,
                     **kwargs) -> requests.Response:
    """requests GET with exponential backoff; re-raises the last exception.

    Calls raise_for_status(), so HTTP errors are retried as well — **except**
    deterministic 4xx (anything but 408/429), which are re-raised immediately.
    A ``Retry-After`` response header wins over the computed backoff, capped by
    ``max_sleep``. ``jitter`` (0 disables) spreads concurrent sources apart.
    """
    for attempt in range(tries):
        try:
            getter = session.get if session is not None else requests.get
            resp = getter(url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if attempt >= tries - 1 or not _is_retryable(exc):
                raise
            time.sleep(_sleep_seconds(attempt, backoff, jitter, exc, max_sleep))
    raise RuntimeError("unreachable")  # pragma: no cover


def retry_call(func: Callable[[], Any], *, tries: int = 3, backoff: float = 2.0,
               jitter: float = 0.0, max_sleep: float = DEFAULT_MAX_SLEEP) -> Any:
    """Retry a zero-arg callable (e.g. urllib.request.urlopen) with exponential backoff.

    ``jitter`` 默认 0(与历史行为一致);带 response 的 4xx 异常同样不重试。
    """
    for attempt in range(tries):
        try:
            return func()
        except Exception as exc:
            if attempt >= tries - 1 or not _is_retryable(exc):
                raise
            time.sleep(_sleep_seconds(attempt, backoff, jitter, exc, max_sleep))
    raise RuntimeError("unreachable")  # pragma: no cover
