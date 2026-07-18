# -*- coding: utf-8 -*-
"""Simple network fetch retry helper: exponential backoff, re-raise the last error."""
from __future__ import annotations

import time
from typing import Any, Callable

import requests


def fetch_with_retry(url: str, *, tries: int = 3, timeout: int = 15, backoff: float = 2.0,
                     session: requests.Session | None = None, **kwargs) -> requests.Response:
    """requests GET with exponential backoff; re-raises the last exception.

    Calls raise_for_status() so HTTP errors are retried as well.
    """
    for attempt in range(tries):
        try:
            getter = session.get if session is not None else requests.get
            resp = getter(url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception:
            if attempt >= tries - 1:
                raise
            time.sleep(backoff ** attempt)
    raise RuntimeError("unreachable")  # pragma: no cover


def retry_call(func: Callable[[], Any], *, tries: int = 3, backoff: float = 2.0) -> Any:
    """Retry a zero-arg callable (e.g. urllib.request.urlopen) with exponential backoff."""
    for attempt in range(tries):
        try:
            return func()
        except Exception:
            if attempt >= tries - 1:
                raise
            time.sleep(backoff ** attempt)
    raise RuntimeError("unreachable")  # pragma: no cover
