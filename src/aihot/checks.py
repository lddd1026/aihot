"""诊断探测：供 `aihot check --net` 使用（代理 / 企业微信 / LLM 连通性）。"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from aihot.config import Config, SourceConfig
from aihot.net import HttpClient, resolve_proxy

TIMEOUT_SECONDS = 10.0
# qyapi 的 key 失效错误码（HTTP 状态码仍是 200，必须看 errcode）
WECOM_INVALID_KEY_CODES = frozenset({93000})
# httpx.InvalidURL 不继承 HTTPError；urlsplit 的非法端口抛 ValueError
_PROBE_ERRORS = (httpx.HTTPError, httpx.InvalidURL, ValueError, OSError)


@dataclass(frozen=True, slots=True)
class CheckResult:
    """单项探测结果。

    ``fatal`` 区分"致命"与"可降级"：
    - 致命（默认）：LLM / 企微 等核心依赖不可用 → 预检失败，今日跳过
    - 可降级：如**代理不可用** → 只影响部分墙外源，不阻断整体运行
    """

    name: str
    ok: bool
    detail: str
    duration_ms: int = 0
    fatal: bool = True

    @property
    def mark(self) -> str:
        if self.ok:
            return "[OK ]"
        return "[ERR]" if self.fatal else "[WARN]"


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def proxy_url(cfg: Config) -> str | None:
    """启用时返回代理地址，否则 None（仅用于诊断展示）。"""
    if cfg.proxy.enabled and cfg.proxy.url:
        return cfg.proxy.url
    return None


def probe_proxy(cfg: Config) -> CheckResult | None:
    """TCP 探测代理端口是否可连。

    注意：代理**不可用不是致命错误**（fatal=False）。
    采集层会自动降级为直连，只有依赖代理的墙外源会缺失；国内源与
    企微/邮件/LLM（均直连）不受影响。
    """
    if not cfg.proxy.enabled:
        return None
    start = time.perf_counter()
    try:
        parts = urlsplit(cfg.proxy.url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if not host:
            return CheckResult(
                "代理", False, "代理地址缺少主机名", _elapsed_ms(start), fatal=False
            )
        with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS):
            pass
    except _PROBE_ERRORS as exc:
        return CheckResult(
            "代理",
            False,
            f"连接失败: {exc}（将降级为直连，墙外源可能缺失）",
            _elapsed_ms(start),
            fatal=False,
        )
    return CheckResult("代理", True, f"{host}:{port} 可连接", _elapsed_ms(start))


def probe_wecom(cfg: Config) -> CheckResult | None:
    """探测企业微信 webhook：仅发 GET，不产生消息。

    注意：qyapi 对**失效的 key 也返回 HTTP 200**，必须解析 errcode 才能发现。
    - 93000 = invalid webhook url（key 失效）
    - 93017 等 = 请求体不合法（GET 的正常结果，说明 key 有效）
    """
    if not (cfg.push.wecom.enabled and cfg.push.wecom.webhook):
        return None
    start = time.perf_counter()
    try:
        # 强制直连：qyapi 是国内服务，走代理反而会在代理不可用时一并失联。
        # trust_env=False：同时忽略环境变量代理（HTTP(S)_PROXY / ALL_PROXY）
        response = httpx.get(
            cfg.push.wecom.webhook,
            timeout=TIMEOUT_SECONDS,
            trust_env=False,
        )
    except _PROBE_ERRORS as exc:
        return CheckResult("企业微信 webhook", False, f"请求失败: {exc}", _elapsed_ms(start))
    if response.status_code != 200:
        detail = f"HTTP {response.status_code}（非 200，可能被网关/代理拦截）"
        return CheckResult("企业微信 webhook", False, detail, _elapsed_ms(start))
    try:
        body = response.json()
    except ValueError:
        return CheckResult(
            "企业微信 webhook", True, "HTTP 200（响应非 JSON，仅确认可达）", _elapsed_ms(start)
        )
    if not isinstance(body, dict):
        return CheckResult(
            "企业微信 webhook", True, "HTTP 200（响应结构非对象，仅确认可达）", _elapsed_ms(start)
        )
    errcode = int(body.get("errcode") or 0)
    errmsg = str(body.get("errmsg") or "")[:60]
    if errcode == 0:
        return CheckResult("企业微信 webhook", True, "errcode=0（完全正常）", _elapsed_ms(start))
    if errcode in WECOM_INVALID_KEY_CODES:
        return CheckResult(
            "企业微信 webhook",
            False,
            f"errcode={errcode} {errmsg}（webhook key 已失效，请重新生成）",
            _elapsed_ms(start),
        )
    return CheckResult(
        "企业微信 webhook",
        True,
        f"HTTP 200 errcode={errcode}（key 有效；GET 请求体非法属预期）",
        _elapsed_ms(start),
    )


def probe_llm(cfg: Config) -> CheckResult | None:
    """探测 LLM 接口：有 key 时验证鉴权，无 key 时仅探测可达性。"""
    base_url = cfg.llm.base_url.rstrip("/")
    headers: dict[str, str] = {}
    if cfg.llm.api_key:
        headers["Authorization"] = f"Bearer {cfg.llm.api_key}"
    start = time.perf_counter()
    try:
        # 强制直连：DeepSeek 是国内服务，不应依赖代理；trust_env=False 忽略环境变量代理
        response = httpx.get(
            f"{base_url}/models",
            headers=headers,
            timeout=TIMEOUT_SECONDS,
            trust_env=False,
        )
    except _PROBE_ERRORS as exc:
        return CheckResult("LLM API", False, f"请求失败: {exc}", _elapsed_ms(start))
    status = response.status_code
    if cfg.llm.api_key:
        if status != 200:
            detail = f"鉴权失败 HTTP {status}: {response.text[:120]}"
            return CheckResult("LLM API", False, detail, _elapsed_ms(start))
        return CheckResult("LLM API", True, "HTTP 200（鉴权通过）", _elapsed_ms(start))
    if status in (200, 401, 403):
        return CheckResult(
            "LLM API", True, f"HTTP {status}（未配置 key，仅探测可达性）", _elapsed_ms(start)
        )
    return CheckResult("LLM API", False, f"HTTP {status}", _elapsed_ms(start))


def probe_sources(cfg: Config) -> list[CheckResult]:
    """逐个探测启用源的 URL 可达性（浅请求，不解析内容）。

    与采集层一致：代理不可用时降级为直连探测。
    """
    proxy, mode = resolve_proxy(cfg.proxy.enabled, cfg.proxy.url)
    try:
        client = HttpClient(proxy=proxy, timeout=TIMEOUT_SECONDS, retries=0)
    except ValueError as exc:
        return [CheckResult("信息源", False, f"HTTP 客户端初始化失败: {exc}")]
    if mode == "degraded":
        results: list[CheckResult] = [
            CheckResult("代理", False, "不可用，已降级为直连探测", fatal=False)
        ]
    else:
        results = []
    try:
        for source in cfg.enabled_sources:
            params = _probe_params(source)
            result = client.get(source.url, params=params)
            if result.ok:
                results.append(
                    CheckResult(
                        f"源 {source.id}",
                        True,
                        f"HTTP {result.status}（探测）",
                        result.duration_ms,
                    )
                )
            else:
                detail = result.error or f"HTTP {result.status}"
                results.append(CheckResult(f"源 {source.id}", False, detail, result.duration_ms))
    finally:
        client.close()
    return results


def _probe_params(source: SourceConfig) -> dict[str, str] | None:
    """部分源的裸地址会报错，探测时补上最小必要参数。"""
    if source.type == "arxiv":
        return {"search_query": "cat:cs.AI", "max_results": "1"}
    return None


def run_quick_checks(cfg: Config) -> list[CheckResult]:
    """快速预检：只验证关键依赖（代理 / 企业微信 / LLM），不做信息源探测。

    用于每日运行前的"预检"，几秒内判断今天能不能正常出日报。
    只有 ``fatal`` 项的失败才会阻断运行（代理失败会自动降级，不阻断）。
    """
    results: list[CheckResult] = []
    for probe in (probe_proxy, probe_wecom, probe_llm):
        result = probe(cfg)
        if result is not None:
            results.append(result)
    return results


def split_failures(results: list[CheckResult]) -> tuple[list[CheckResult], list[CheckResult]]:
    """把失败项拆成 (致命, 可降级) 两组。"""
    failed = [result for result in results if not result.ok]
    fatal = [result for result in failed if result.fatal]
    degraded = [result for result in failed if not result.fatal]
    return fatal, degraded


def run_net_checks(cfg: Config) -> list[CheckResult]:
    """执行全部网络探测（代理 / 企业微信 / LLM / 各信息源），返回结果列表。"""
    results: list[CheckResult] = []
    for probe in (probe_proxy, probe_wecom, probe_llm):
        result = probe(cfg)
        if result is not None:
            results.append(result)
    results.extend(probe_sources(cfg))
    return results
