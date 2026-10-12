"""统一 HTTP 客户端：代理、超时、重试退避。"""

from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 1.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 30.0

logger = logging.getLogger("aihot.net")

PROXY_PROBE_TIMEOUT = 2.0


def proxy_reachable(url: str, *, timeout: float = PROXY_PROBE_TIMEOUT) -> bool:
    """TCP 探测代理端口是否可连（快速失败，绝不抛异常）。"""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def resolve_proxy(
    enabled: bool, url: str, *, timeout: float = PROXY_PROBE_TIMEOUT
) -> tuple[str | None, str]:
    """决定本次采集使用的代理，返回 (代理地址或 None, 模式)。

    模式取值：
    - ``direct``   未启用代理（或未配置地址）
    - ``proxy``    代理可用
    - ``degraded`` 代理已配置但当前不可用 → **降级为直连**，不阻断整体运行
    """
    if not (enabled and url):
        return None, "direct"
    if proxy_reachable(url, timeout=timeout):
        return url, "proxy"
    return None, "degraded"


@dataclass(frozen=True, slots=True)
class FetchResult:
    """一次 HTTP 请求的结果（失败不抛异常，便于单源隔离）。"""

    url: str
    status: int = 0
    text: str = ""
    error: str = ""
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.error and 200 <= self.status < 300


class HttpClient:
    """带重试的同步 HTTP 客户端。

    - 5xx 与网络错误会重试（指数退避），4xx 直接返回
    - 失败返回 FetchResult(error=...)，由调用方决定是否升级为 SourceError
    """

    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.retries = max(0, int(retries))
        self.backoff = max(0.0, float(backoff))
        try:
            self._client = httpx.Client(
                proxy=proxy,
                timeout=timeout,
                headers={"User-Agent": user_agent},
                follow_redirects=True,
                transport=transport,
                # 只认显式配置的代理：忽略 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 等环境变量，
                # 避免"配置里写了直连、实际仍被环境代理接管"的隐性故障（M5 修复轮 P1）
                trust_env=False,
            )
        except (httpx.InvalidURL, ValueError, ImportError) as exc:
            raise ValueError(f"HTTP 客户端初始化失败（请检查代理地址）: {exc}") from exc

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchResult:
        """GET 请求（自动重试 5xx 与 429）。"""
        last_error = ""
        start = time.perf_counter()
        for attempt in range(self.retries + 1):
            response: httpx.Response | None = None
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRY_STATUSES:
                    return FetchResult(
                        url=str(response.url),
                        status=response.status_code,
                        text=response.text,
                        duration_ms=_elapsed_ms(start),
                    )
                last_error = f"HTTP {response.status_code}"
            if attempt < self.retries:
                delay = self._retry_delay(attempt, response)
                logger.debug("请求失败将重试: %s（%s，%.1fs 后）", url, last_error, delay)
                time.sleep(delay)
        logger.warning("请求最终失败: %s（%s）", url, last_error)
        return FetchResult(url=url, error=last_error, duration_ms=_elapsed_ms(start))

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        """退避时长：429 优先遵循 Retry-After，其余按次数线性退避。"""
        base = self.backoff * (attempt + 1)
        if response is not None and response.status_code == 429:
            header = (response.headers.get("retry-after") or "").strip()
            if header.isdigit():
                return min(float(header), MAX_RETRY_AFTER)
            return base * 3
        return base

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[Any | None, FetchResult]:
        """GET 并解析 JSON；解析失败时返回 (None, 带 error 的结果)。"""
        result = self.get(url, params=params, headers=headers)
        if not result.ok:
            return None, result
        try:
            return json.loads(result.text), result
        except json.JSONDecodeError as exc:
            return None, replace(result, error=f"JSON 解析失败: {exc}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
