"""M5 观察期复盘的修复回归（2026-10-12）。

背景：代理（Clash）未开时，预检把"代理不可用"当致命错误 → 整日跳过；
且告警本身也走代理 → 连失败通知都发不出（静默失败 10 天）。

本文件覆盖四组修复：
1. 代理不可用 → 降级为直连（net.resolve_proxy / runner.fetch_stage）
2. 企微 / LLM / 邮件强制直连，预检分级（checks.CheckResult.fatal）
3. 告警降级链：企微 → 邮件 → 本地告警文件（runner.send_alert）
4. 跳过的运行写入 runs 表（runner.record_skip / reporting 展示）
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from aihot import checks, runner
from aihot import net as net_module
from aihot.checks import CheckResult, run_quick_checks, split_failures
from aihot.config import Config, load_config
from aihot.delivery import NotifyError
from aihot.models import RunRecord
from aihot.net import proxy_healthy, proxy_reachable, resolve_proxy
from aihot.sources.registry import CollectResult
from aihot.store import Store

CONFIG_TEMPLATE = """
app:
  timezone: Asia/Shanghai
  data_dir: "{data_dir}"
  log_dir: "{log_dir}"
  top_n: 5
llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key: sk-test-1234567890
proxy:
  enabled: {proxy_enabled}
  url: "{proxy_url}"
push:
  wecom:
    enabled: {wecom_enabled}
    webhook: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key"
  email:
    enabled: {email_enabled}
    smtp_host: smtp.qq.com
    smtp_port: 465
    use_ssl: true
    username: test@qq.com
    password: authcode-0123456789
    from_addr: test@qq.com
    to_addrs: ["test@qq.com"]
sources:
  - id: s1
    type: rss
    url: https://example.com/feed
    enabled: true
    weight: 1.0
"""


def load_cfg(
    tmp_path: Path,
    *,
    proxy_enabled: bool = False,
    proxy_url: str = "http://127.0.0.1:7897",
    wecom_enabled: bool = False,
    email_enabled: bool = False,
) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG_TEMPLATE.format(
            data_dir=(tmp_path / "data").as_posix(),
            log_dir=(tmp_path / "logs").as_posix(),
            proxy_enabled=str(proxy_enabled).lower(),
            proxy_url=proxy_url,
            wecom_enabled=str(wecom_enabled).lower(),
            email_enabled=str(email_enabled).lower(),
        ),
        encoding="utf-8",
    )
    return load_config(path)


def open_port() -> tuple[socket.socket, int]:
    """占用一个本机端口，模拟"代理在跑"（返回的 socket 需由调用方持有）。"""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)  # 预留 backlog，测试会连多次
    return server, server.getsockname()[1]


# ---------- 1. 代理可用性判定与降级 ----------


def test_resolve_proxy_disabled_or_empty() -> None:
    assert resolve_proxy(False, "http://127.0.0.1:7897") == (None, "direct")
    assert resolve_proxy(True, "") == (None, "direct")


def test_resolve_proxy_unreachable_degrades() -> None:
    proxy, mode = resolve_proxy(True, "http://127.0.0.1:1", timeout=0.5)
    assert proxy is None
    assert mode == "degraded"


def test_resolve_proxy_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """TCP 可连 + 健康检查通过 → 使用代理（健康检查单独测，见下一组用例）。"""
    server, port = open_port()
    monkeypatch.setattr(net_module, "proxy_healthy", lambda *a, **k: True)
    try:
        assert proxy_reachable(f"http://127.0.0.1:{port}", timeout=0.5) is True
        proxy, mode = resolve_proxy(True, f"http://127.0.0.1:{port}", timeout=0.5)
    finally:
        server.close()
    assert proxy == f"http://127.0.0.1:{port}"
    assert mode == "proxy"


def test_resolve_proxy_degrades_when_node_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    """M5R2：Clash 端口开着但节点坏 → 必须降级直连，而不是让所有源陪葬。"""
    monkeypatch.setattr(net_module, "proxy_reachable", lambda *a, **k: True)
    monkeypatch.setattr(net_module, "proxy_healthy", lambda *a, **k: False)
    assert resolve_proxy(True, "http://127.0.0.1:7897") == (None, "degraded")


def test_resolve_proxy_uses_proxy_when_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net_module, "proxy_reachable", lambda *a, **k: True)
    monkeypatch.setattr(net_module, "proxy_healthy", lambda *a, **k: True)
    assert resolve_proxy(True, "http://127.0.0.1:7897") == (
        "http://127.0.0.1:7897",
        "proxy",
    )


def _patch_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    statuses: dict[str, int] | None = None,
    default_status: int = 204,
    error: Exception | None = None,
):
    """把 net 模块里的 httpx.Client 换成假客户端（避免真实外呼），并记录参数。

    statuses: 按 URL 子串匹配返回不同状态码，例如 {"gstatic": 502}。
    """
    captured: dict = {}
    rules = statuses or {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return False

        def get(self, url: str) -> httpx.Response:
            if error is not None:
                raise error
            status = default_status
            for key, value in rules.items():
                if key in url:
                    status = value
            return httpx.Response(status, request=httpx.Request("GET", url))

    monkeypatch.setattr(net_module.httpx, "Client", FakeClient)
    return captured


def test_proxy_healthy_ok_on_204(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_httpx_client(monkeypatch, default_status=204)
    assert proxy_healthy("http://127.0.0.1:7897") is True
    assert captured.get("proxy") == "http://127.0.0.1:7897"
    assert captured.get("trust_env") is False


def test_proxy_healthy_second_probe_rescues(monkeypatch: pytest.MonkeyPatch) -> None:
    """P2-3：第一个探测点失败（如 gstatic 被污染），第二个通过也算健康。"""
    _patch_httpx_client(
        monkeypatch, statuses={"gstatic": 502, "api.github.com": 200}, default_status=502
    )
    assert proxy_healthy("http://127.0.0.1:7897") is True


def test_proxy_healthy_false_when_all_probes_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx_client(monkeypatch, default_status=502)
    assert proxy_healthy("http://127.0.0.1:7897") is False


def test_proxy_healthy_false_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx_client(monkeypatch, error=httpx.ConnectError("boom"))
    assert proxy_healthy("http://127.0.0.1:7897") is False


def test_probe_proxy_reports_broken_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """端口通但节点坏 → 可降级 + 文案说明原因。"""
    server, port = open_port()
    cfg = load_cfg(tmp_path, proxy_enabled=True, proxy_url=f"http://127.0.0.1:{port}")
    monkeypatch.setattr(checks, "proxy_healthy", lambda *a, **k: False)
    try:
        result = checks.probe_proxy(cfg)
    finally:
        server.close()
    assert result is not None
    assert result.ok is False
    assert result.fatal is False
    assert "节点异常" in result.detail


def test_fetch_stage_degrades_to_direct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """核心回归：代理不可用时采集降级为直连（短超时），不再整日跳过。"""
    cfg = load_cfg(tmp_path, proxy_enabled=True, proxy_url="http://127.0.0.1:1")
    monkeypatch.setattr(runner, "resolve_proxy", lambda *args, **kwargs: (None, "degraded"))
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(runner, "HttpClient", FakeClient)
    monkeypatch.setattr(runner, "build_adapters", lambda cfg, http, only=None: ([object()], []))
    monkeypatch.setattr(
        runner, "collect_all", lambda adapters, since: CollectResult(items=[], stats=[])
    )

    outcome = runner.fetch_stage(cfg, dry_run=True)

    assert outcome.proxy_mode == "degraded"
    assert captured["proxy"] is None
    assert captured["timeout"] == runner.DEGRADED_TIMEOUT
    assert captured["retries"] == runner.DEGRADED_RETRIES
    assert captured.get("closed") is True


def test_run_all_notifies_on_degrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """降级不阻断出报，但必须让你知情（发一条提示）。"""
    cfg = load_cfg(tmp_path)
    notices: list[str] = []
    monkeypatch.setattr(
        runner,
        "send_alert",
        lambda cfg, title, details: notices.append(title) or runner.AlertResult(delivered=True),
    )
    monkeypatch.setattr(
        runner, "fetch_stage", lambda *a, **k: runner.FetchOutcome(stats=[], proxy_mode="degraded")
    )
    monkeypatch.setattr(
        runner, "enrich_stage", lambda *a, **k: runner.EnrichOutcome(pending=0)
    )

    runner.run_all(cfg)

    assert any("降级" in title for title in notices)


# ---------- 2. 预检分级 + 直连通道 ----------


def test_nonfatal_failure_is_warning() -> None:
    assert CheckResult("代理", False, "boom", 5, fatal=False).mark == "[WARN]"
    assert CheckResult("LLM API", False, "boom").mark == "[ERR]"
    assert CheckResult("代理", True, "ok").mark == "[OK ]"


def test_split_failures_groups() -> None:
    fatal, degraded = split_failures(
        [
            CheckResult("a", False, "x"),
            CheckResult("b", False, "y", fatal=False),
            CheckResult("c", True, "z"),
        ]
    )
    assert [result.name for result in fatal] == ["a"]
    assert [result.name for result in degraded] == ["b"]


def test_probe_proxy_down_is_not_fatal(tmp_path: Path) -> None:
    cfg = load_cfg(tmp_path, proxy_enabled=True, proxy_url="http://127.0.0.1:1")
    result = checks.probe_proxy(cfg)
    assert result is not None
    assert result.ok is False
    assert result.fatal is False
    assert "降级" in result.detail


def test_quick_check_survives_proxy_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """核心回归：Clash 没开时，企微/LLM 直连仍通过 → 预检不再拦截。"""
    cfg = load_cfg(tmp_path, proxy_enabled=True, proxy_url="http://127.0.0.1:1")
    seen_proxy: list[object] = []

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        seen_proxy.append(kwargs.get("proxy"))
        if "qyapi" in url:
            return httpx.Response(
                200,
                json={"errcode": 93017, "errmsg": "invalid json request"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(checks.httpx, "get", fake_get)

    results = run_quick_checks(cfg)
    fatal, degraded = split_failures(results)

    assert not fatal, "代理不可用不应再是致命失败"
    assert [result.name for result in degraded] == ["代理"]
    assert seen_proxy and all(proxy is None for proxy in seen_proxy), "企微/LLM 必须直连"


# ---------- 3. 告警降级链 ----------


def test_alert_falls_back_to_email(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_cfg(tmp_path, wecom_enabled=True, email_enabled=True)
    sent: list[str] = []

    class BoomWeCom:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def send_text(self, content: str) -> None:
            raise NotifyError("模拟企微不可用")

        def close(self) -> None:
            pass

    class FakeEmail:
        def __init__(self, config: object) -> None:
            pass

        def send(self, subject: str, html: str, *, images: object = None) -> None:
            sent.append(subject)

    monkeypatch.setattr(runner, "WeComNotifier", BoomWeCom)
    monkeypatch.setattr(runner, "EmailNotifier", FakeEmail)

    result = runner.send_alert(cfg, "测试告警", ["细节一行"])

    assert result.delivered is True
    assert "邮件" in result.channels
    assert sent and "测试告警" in sent[0]
    assert any("企业微信" in error for error in result.errors)


def test_alert_last_resort_writes_local_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = load_cfg(tmp_path, wecom_enabled=True, email_enabled=True)

    class BoomWeCom:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def send_text(self, content: str) -> None:
            raise NotifyError("企微挂了")

        def close(self) -> None:
            pass

    class BoomEmail:
        def __init__(self, config: object) -> None:
            pass

        def send(self, subject: str, html: str, *, images: object = None) -> None:
            raise NotifyError("邮件也挂了")

    monkeypatch.setattr(runner, "WeComNotifier", BoomWeCom)
    monkeypatch.setattr(runner, "EmailNotifier", BoomEmail)

    result = runner.send_alert(cfg, "全部通道失败", ["原因：模拟"])

    assert result.delivered is False
    alert_file = cfg.app.log_dir / runner.ALERT_FILE_NAME
    assert alert_file.exists()
    content = alert_file.read_text(encoding="utf-8")
    assert "全部通道失败" in content
    assert any("本地文件" in channel for channel in result.channels)


def test_alert_wecom_direct_has_no_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """企微告警必须直连（历史事故：走代理导致告警也发不出）。"""
    cfg = load_cfg(tmp_path, proxy_enabled=True, wecom_enabled=True)
    captured: dict = {}

    class RecordWeCom:
        def __init__(self, webhook: str, **kwargs: object) -> None:
            captured["webhook"] = webhook
            captured.update(kwargs)

        def send_text(self, content: str) -> None:
            captured["text"] = content

        def close(self) -> None:
            pass

    monkeypatch.setattr(runner, "WeComNotifier", RecordWeCom)

    result = runner.send_alert(cfg, "直连校验", ["x"])

    assert result.delivered is True
    assert captured.get("proxy") is None, "告警不应把代理传给企微客户端"


# ---------- 4. 跳过记录与报告 ----------


def test_record_skip_writes_run_row(tmp_path: Path) -> None:
    cfg = load_cfg(tmp_path)
    runner.record_skip(cfg, "preflight failed: LLM unreachable")

    with Store(cfg.app.data_dir / "aihot.db") as store:
        rows = store.runs_since(datetime.now(UTC) - timedelta(hours=1))

    assert rows, "跳过必须留下运行记录"
    assert rows[-1]["status"] == "skipped"
    stats = json.loads(rows[-1]["stage_stats"])
    assert stats["stage"] == "skip"
    assert "LLM unreachable" in stats["reason"]


def test_skip_run_record_shape(tmp_path: Path) -> None:
    """自定义 RunRecord 里的 skipped 状态能落库（reporting 依赖该约定）。"""
    cfg = load_cfg(tmp_path)
    with Store(cfg.app.data_dir / "aihot.db") as store:
        store.log_run(
            RunRecord(
                run_id="skip-shape-1",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                status="skipped",
                stage_stats={"stage": "skip", "reason": "manual"},
            )
        )
        rows = store.runs_since(datetime.now(UTC) - timedelta(hours=1))
    assert any(row["status"] == "skipped" for row in rows)


# ---------- 5. P1：忽略环境变量代理（trust_env=False） ----------


def test_http_client_disables_env_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1 回归：HTTP 客户端必须 trust_env=False，只认显式配置的代理。"""
    from aihot import net as net_module

    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            pass

    monkeypatch.setattr(net_module.httpx, "Client", FakeClient)
    client = net_module.HttpClient()
    client.close()
    assert captured.get("trust_env") is False


def test_wecom_notifier_disables_env_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1 回归：企微推送客户端必须 trust_env=False。"""
    from aihot.delivery import wecom as wecom_module

    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            pass

    monkeypatch.setattr(wecom_module.httpx, "Client", FakeClient)
    notifier = wecom_module.WeComNotifier("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x")
    notifier.close()
    assert captured.get("trust_env") is False


def test_probes_disable_env_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1 回归：企微 / LLM 探测都必须 trust_env=False。"""
    cfg = load_cfg(tmp_path, wecom_enabled=True)
    seen: list[dict] = []

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        seen.append(kwargs)
        if "qyapi" in url:
            return httpx.Response(
                200,
                json={"errcode": 93017, "errmsg": "invalid json request"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(checks.httpx, "get", fake_get)

    checks.probe_wecom(cfg)
    checks.probe_llm(cfg)

    assert seen, "探测必须发生"
    assert all(item.get("trust_env") is False for item in seen)
