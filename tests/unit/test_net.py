"""net 模块单元测试（用 httpx.MockTransport 模拟网络，不产生真实请求）。"""

from __future__ import annotations

import httpx
import pytest

from aihot.net import HttpClient


def make_client(handler, *, retries: int = 2) -> HttpClient:
    return HttpClient(transport=httpx.MockTransport(handler), retries=retries, backoff=0.0)


def test_get_ok() -> None:
    client = make_client(lambda request: httpx.Response(200, text="hello"))
    result = client.get("https://example.com/feed")
    assert result.ok
    assert result.status == 200
    assert result.text == "hello"
    client.close()


def test_4xx_does_not_retry() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404, text="nope")

    client = make_client(handler)
    result = client.get("https://example.com/x")
    assert not result.ok
    assert result.status == 404
    assert len(calls) == 1
    client.close()


def test_5xx_retries_then_succeeds() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, text="ok")

    client = make_client(handler)
    result = client.get("https://example.com/x")
    assert result.ok
    assert len(calls) == 3
    client.close()


def test_network_error_retries_and_reports() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("boom")

    client = make_client(handler)
    result = client.get("https://example.com/x")
    assert not result.ok
    assert "ConnectError" in result.error
    assert len(calls) == 3  # 首次 + 2 次重试
    client.close()


def test_params_are_passed() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, text="{}")

    client = make_client(handler)
    client.get("https://example.com/api", params={"tags": "story", "n": 1})
    assert seen["tags"] == "story"
    assert seen["n"] == "1"
    client.close()


def test_get_json_parses() -> None:
    client = make_client(lambda request: httpx.Response(200, text='{"hits": []}'))
    payload, result = client.get_json("https://example.com/api")
    assert result.ok
    assert payload == {"hits": []}
    client.close()


def test_get_json_invalid_json_is_error() -> None:
    client = make_client(lambda request: httpx.Response(200, text="not json"))
    payload, result = client.get_json("https://example.com/api")
    assert payload is None
    assert not result.ok
    assert "JSON 解析失败" in result.error
    client.close()


def test_invalid_proxy_raises_value_error() -> None:
    with pytest.raises(ValueError, match="HTTP 客户端初始化失败"):
        HttpClient(proxy="http://127.0.0.1:abc")


def test_invalid_proxy_ipv6_raises_value_error() -> None:
    with pytest.raises(ValueError, match="HTTP 客户端初始化失败"):
        HttpClient(proxy="http://[::1")
