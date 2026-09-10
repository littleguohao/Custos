# -*- coding: utf-8 -*-
"""llm_client 的结构化输出降级链 / 重试精细化 / 连续超时熔断钉测（无网络）。

全部 monkeypatch 模块级 ``_post``（或再下一层的 ``requests.post``）；
退避等待用实例属性 ``client.sleep`` 替换，不真等。
"""

from __future__ import annotations

import pytest

from custos.research.evolution import llm_client as lc
from custos.research.evolution.llm_client import LLMConfig, LLMError

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}
MSG = [{"role": "user", "content": "x"}]


def _config(**kw) -> LLMConfig:
    base = dict(base_url="https://x/v1", api_key="k", model="m")
    base.update(kw)
    return LLMConfig(**base)


def _client(**kw) -> lc.LLMClient:
    c = lc.LLMClient(_config(**kw))
    c.sleep = lambda s: None  # 不真等退避
    return c


def _ok(text: str = "ok") -> dict:
    return {"choices": [{"message": {"content": text}}]}


class TestLLMErrorAttrs:
    def test_positional_compat(self):
        e = LLMError("plain message")
        assert str(e) == "plain message"
        assert e.status_code is None and e.retry_after is None and not e.is_timeout

    def test_full_attrs(self):
        e = LLMError("limited", status_code=429, retry_after=2.5, is_timeout=False)
        assert e.status_code == 429 and e.retry_after == 2.5


class TestPostErrorMapping:
    def test_timeout_marked(self, monkeypatch):
        def fake_post(url, headers=None, json=None, timeout=None):
            raise lc.requests.Timeout("slow")

        monkeypatch.setattr(lc.requests, "post", fake_post)
        with pytest.raises(LLMError) as exc:
            lc._post(_config(), {})
        assert exc.value.is_timeout is True and exc.value.status_code is None

    def test_429_retry_after_parsed(self, monkeypatch):
        class Resp:
            status_code = 429
            text = "Rate limit reached. Please Retry After 2.5 seconds."

            def json(self):
                return {}

        monkeypatch.setattr(lc.requests, "post", lambda *a, **kw: Resp())
        with pytest.raises(LLMError) as exc:
            lc._post(_config(), {})
        assert exc.value.status_code == 429 and exc.value.retry_after == 2.5

    def test_503_without_hint(self, monkeypatch):
        class Resp:
            status_code = 503
            text = "Service Unavailable"

            def json(self):
                return {}

        monkeypatch.setattr(lc.requests, "post", lambda *a, **kw: Resp())
        with pytest.raises(LLMError) as exc:
            lc._post(_config(), {})
        assert exc.value.status_code == 503 and exc.value.retry_after is None

    def test_400_no_retry_after(self, monkeypatch):
        class Resp:
            status_code = 400
            text = "bad request; retry after 9"  # 非 429/503：提示不解析

            def json(self):
                return {}

        monkeypatch.setattr(lc.requests, "post", lambda *a, **kw: Resp())
        with pytest.raises(LLMError) as exc:
            lc._post(_config(), {})
        assert exc.value.status_code == 400 and exc.value.retry_after is None


class TestStructuredOutputChain:
    def test_schema_on_first_attempt(self, monkeypatch):
        seen = []
        monkeypatch.setattr(lc, "_post", lambda c, p: seen.append(p) or _ok())
        _client().chat(MSG, json_schema=SCHEMA)
        assert seen[0]["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "evolution_proposal", "schema": SCHEMA},
        }

    def test_400_degrades_step_by_step(self, monkeypatch):
        seen = []

        def fake_post(config, payload):
            seen.append(payload.get("response_format", "缺省"))
            if len(seen) < 3:
                raise LLMError("bad request", status_code=400)
            return _ok()

        monkeypatch.setattr(lc, "_post", fake_post)
        _client(max_retry=3).chat(MSG, json_schema=SCHEMA)
        kinds = [f["type"] if isinstance(f, dict) else f for f in seen]
        assert kinds == ["json_schema", "json_object", "缺省"]

    def test_non_400_does_not_degrade(self, monkeypatch):
        seen = []

        def fake_post(config, payload):
            seen.append(payload.get("response_format"))
            if len(seen) == 1:
                raise LLMError("server error", status_code=500)
            return _ok()

        monkeypatch.setattr(lc, "_post", fake_post)
        _client(max_retry=3).chat(MSG, json_schema=SCHEMA)
        assert seen[0] == seen[1] and seen[0]["type"] == "json_schema"

    def test_json_mode_chain_without_schema(self, monkeypatch):
        seen = []

        def fake_post(config, payload):
            seen.append(payload.get("response_format", "缺省"))
            if len(seen) == 1:
                raise LLMError("bad request", status_code=400)
            return _ok()

        monkeypatch.setattr(lc, "_post", fake_post)
        _client(max_retry=3).chat(MSG)  # json_mode=True 默认
        assert seen == [{"type": "json_object"}, "缺省"]


class TestRetryRefinement:
    def test_retry_after_drives_sleep(self, monkeypatch):
        sleeps = []
        calls = []

        def fake_post(config, payload):
            calls.append(1)
            if len(calls) == 1:
                raise LLMError("rate limited", status_code=429, retry_after=2.5)
            return _ok()

        monkeypatch.setattr(lc, "_post", fake_post)
        c = _client(max_retry=3)
        c.sleep = sleeps.append
        assert c.chat(MSG) == "ok"
        assert sleeps == [2.5]

    def test_retry_after_capped_at_60(self, monkeypatch):
        sleeps = []

        def fake_post(config, payload):
            raise LLMError("rate limited", status_code=429, retry_after=999.0)

        monkeypatch.setattr(lc, "_post", fake_post)
        c = _client(max_retry=2)
        c.sleep = sleeps.append
        with pytest.raises(LLMError, match="重试 2 次"):
            c.chat(MSG)
        assert sleeps == [60.0]

    def test_exponential_backoff_without_hint(self, monkeypatch):
        sleeps = []

        def fake_post(config, payload):
            raise LLMError("down", status_code=500)

        monkeypatch.setattr(lc, "_post", fake_post)
        c = _client(max_retry=3)
        c.sleep = sleeps.append
        with pytest.raises(LLMError, match="重试 3 次"):
            c.chat(MSG)
        assert sleeps == [2.0, 4.0]  # 2^1, 2^2（attempt 从 1 起）

    def test_timeout_circuit_breaker(self, monkeypatch):
        calls = []

        def fake_post(config, payload):
            calls.append(1)
            raise LLMError("timeout", is_timeout=True)

        monkeypatch.setattr(lc, "_post", fake_post)
        with pytest.raises(LLMError, match="熔断"):
            _client(max_retry=5).chat(MSG)
        # timeout_fail_limit 默认 3：max_retry=5 也不继续消耗
        assert len(calls) == 3

    def test_timeout_fail_limit_configurable(self, monkeypatch):
        calls = []

        def fake_post(config, payload):
            calls.append(1)
            raise LLMError("timeout", is_timeout=True)

        monkeypatch.setattr(lc, "_post", fake_post)
        with pytest.raises(LLMError, match="熔断"):
            _client(max_retry=9, timeout_fail_limit=2).chat(MSG)
        assert len(calls) == 2

    def test_non_consecutive_timeouts_no_breaker(self, monkeypatch):
        # 超时 → 500（重置计数）→ 超时 ×2：不构成"连续 3 次"，耗尽重试才报错
        seq = [
            LLMError("t1", is_timeout=True),
            LLMError("e", status_code=500),
            LLMError("t2", is_timeout=True),
            LLMError("t3", is_timeout=True),
        ]
        calls = []

        def fake_post(config, payload):
            calls.append(1)
            raise seq[len(calls) - 1]

        monkeypatch.setattr(lc, "_post", fake_post)
        with pytest.raises(LLMError, match="重试 4 次"):
            _client(max_retry=4).chat(MSG)
        assert len(calls) == 4


class TestConfigGuards:
    def test_repr_hides_api_key(self):
        cfg = _config(api_key="sk-secret-123")
        assert "sk-secret-123" not in repr(cfg)
        assert "api_key" not in repr(cfg)  # field(repr=False)：键名也不出现

    def test_non_https_base_url_rejected(self):
        with pytest.raises(ValueError, match="https"):
            _config(base_url="http://api.example.com/v1")
        with pytest.raises(ValueError, match="https"):
            _config(base_url="api.example.com/v1")  # 无 scheme 同样拒
        with pytest.raises(ValueError, match="https"):
            _config(base_url="u")

    def test_localhost_http_allowed(self):
        for url in (
            "http://localhost:8000/v1",
            "http://127.0.0.1:9000",
            "http://[::1]:8080/v1",
        ):
            assert _config(base_url=url).base_url == url

    def test_from_env_non_https_returns_none(self, monkeypatch):
        monkeypatch.setenv(lc.ENV_BASE_URL, "http://api.example.com/v1")
        monkeypatch.setenv(lc.ENV_API_KEY, "k")
        monkeypatch.setenv(lc.ENV_MODEL, "m")
        monkeypatch.delenv(lc.ENV_TIMEOUT, raising=False)
        assert lc.LLMConfig.from_env() is None  # 非 https 非回环 → fail-closed None
        monkeypatch.setenv(lc.ENV_BASE_URL, "http://127.0.0.1:8080/v1")
        cfg = lc.LLMConfig.from_env()
        assert cfg is not None and cfg.base_url == "http://127.0.0.1:8080/v1"


class TestRetryAfterHeader:
    def test_header_preferred_over_text(self, monkeypatch):
        class Resp:
            status_code = 429
            text = "rate limited; retry after 2"  # 文本提示与头不一致 → 头优先
            headers = {"Retry-After": "7"}

            def json(self):
                return {}

        monkeypatch.setattr(lc.requests, "post", lambda *a, **kw: Resp())
        with pytest.raises(LLMError) as exc:
            lc._post(_config(), {})
        assert exc.value.retry_after == 7.0

    def test_http_date_header_falls_back_to_text(self, monkeypatch):
        class Resp:
            status_code = 503
            text = "retry after 3"
            headers = {
                "Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"
            }  # 不支持 HTTP-date

            def json(self):
                return {}

        monkeypatch.setattr(lc.requests, "post", lambda *a, **kw: Resp())
        with pytest.raises(LLMError) as exc:
            lc._post(_config(), {})
        assert exc.value.retry_after == 3.0

    def test_header_seconds_drive_backoff(self, monkeypatch):
        sleeps = []
        calls = []

        def fake_post(config, payload):
            calls.append(1)
            if len(calls) == 1:
                raise LLMError("rate limited", status_code=429, retry_after=7.0)
            return _ok()

        monkeypatch.setattr(lc, "_post", fake_post)
        c = _client(max_retry=2)
        c.sleep = sleeps.append
        assert c.chat(MSG) == "ok"
        assert sleeps == [7.0]  # 头解析出的秒数进入退避（60s 封顶既有行为不变）
