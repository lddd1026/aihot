"""M6 独立验收支撑模块（m6r = 第 6 轮 / 新源 + 代理健康检查）。

设计约束（黑盒验收）：
- 只用 localhost 假服务：假 Trending 页 / 假代理节点 / 假 RSS / 假 LLM；
- 不访问公网（唯一例外：可选对 https://github.com/trending 做只读 GET）；
- 不修改 config.yaml / data / logs 等真实资产；证据写到 reports/evidence/m6r/<suite>/。

复用既有 tests/independent/m3_support.py、m4_support.py、m5r_support.py（旧文件尽量不改）。
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

# 证据根目录：reports/evidence/m6r（可用 AIHOT_M6R_EVIDENCE 覆盖）
os.environ["AIHOT_M4_EVIDENCE"] = os.environ.get("AIHOT_M6R_EVIDENCE", "m6r")
os.environ["AIHOT_M3_EVIDENCE"] = os.environ["AIHOT_M4_EVIDENCE"]

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m4_support as m4  # noqa: E402
import m5r_support as m5  # noqa: E402

PROJECT = m3.PROJECT
PYTHON = m3.PYTHON
TEMPROOT = m3.TEMPROOT
EVIDENCE = m3.EVIDENCE
RAW = m4.RAW
BAD_PORT = m5.BAD_PORT
DEAD_FEED = f"http://127.0.0.1:{BAD_PORT}/feed.xml"

# 节点健康探测端点（与 src/aihot/net.py 的 PROXY_HEALTH_URL 同形）
HEALTH_PROBE = "http://127.0.0.1:9/generate_204"


def write_json(name: str, data: Any) -> Path:
    return m5.write_json(name, data)


def clean_proxy_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    return m5.clean_proxy_env(extra)


# --------------------------------------------------------------------------- Trending 页面夹具

# 1 号行：完整字段（含 <em> 标签与 HTML 实体，验证 description 清洗）
ROW_FULL = """
  <article class="Box-row">
    <div class="float-sm-right d-inline-block">
      <span class="float-sm-right">25,599 stars today</span>
    </div>
    <h2 class="h3 lh-condensed">
      <a href="/acme-org/model-lab">acme-org / model-lab</a>
    </h2>
    <p class="col-9 color-fg-muted my-1 pr-4">
      A <em>tiny</em> inference engine &amp; toolkit for LLMs
    </p>
    <div class="f6 color-fg-muted mt-2">
      <span class="d-inline-block ml-0 mr-3" itemprop="programmingLanguage">Python</span>
      <a class="Link--muted d-inline-block mr-3" href="/acme-org/model-lab/stargazers">124,887</a>
      <a class="Link--muted d-inline-block mr-3" href="/acme-org/model-lab/forks">8,123</a>
    </div>
  </article>
"""

# 2 号行：字段缺失（无描述 / 无语言 / 无 fork；今日 star 一位数）
ROW_PARTIAL = """
  <article class="Box-row">
    <h2 class="h3 lh-condensed"><a href="/solo-dev/blobber">solo-dev / blobber</a></h2>
    <div class="f6 color-fg-muted mt-2">
      <a href="/solo-dev/blobber/stargazers">5</a>
      <span class="float-sm-right">7 stars today</span>
    </div>
  </article>
"""

# 3 号行：坏 href（绝对 URL，非站内路径）→ 必须被跳过而不是产出脏数据
ROW_ABSOLUTE_HREF = """
  <article class="Box-row">
    <h2 class="h3 lh-condensed">
      <a href="https://github.com/absolute/elsewhere">absolute / elsewhere</a>
    </h2>
    <p class="col-9">绝对 URL，非 /owner/repo</p>
    <span class="float-sm-right">99 stars today</span>
  </article>
"""

# 3b 号行：sponsors 链接（探针：验证是否会被误当仓库，见 suite.note）
ROW_SPONSOR_HREF = """
  <article class="Box-row">
    <h2 class="h3 lh-condensed"><a href="/sponsors/acme-org">Sponsor @acme-org</a></h2>
    <p class="col-9">赞助页，不是仓库</p>
    <span class="float-sm-right">99 stars today</span>
  </article>
"""

# 4 号行：多级 href（/owner/repo/tree/...）→ 不匹配仓库正则，跳过
ROW_DEEP_HREF = """
  <article class="Box-row">
    <h2 class="h3 lh-condensed">
      <a href="/acme-org/model-lab/tree/main">acme-org / model-lab</a>
    </h2>
    <p>子目录链接</p>
  </article>
"""

# 5 号行：正常但排在第 4 个有效仓库之后（用于 limit 截断）
ROW_TAIL = """
  <article class="Box-row">
    <h2 class="h3 lh-condensed"><a href="/tail-org/agent-desk">tail-org / agent-desk</a></h2>
    <p>Agent workspace</p>
    <div class="f6 color-fg-muted">
      <span itemprop="programmingLanguage">TypeScript</span>
      <a href="/tail-org/agent-desk/stargazers">3,001</a>
      <a href="/tail-org/agent-desk/forks">210</a>
      <span class="float-sm-right">1,234 stars today</span>
    </div>
  </article>
"""

# 6 号行：用于 max/limit 截断验证（第 5 个有效仓库）
ROW_EXTRA = """
  <article class="Box-row">
    <h2 class="h3 lh-condensed"><a href="/extra-org/rag-notes">extra-org / rag-notes</a></h2>
    <p>RAG notes</p>
    <span class="float-sm-right">42 stars today</span>
  </article>
"""

DEFAULT_ROWS = [
    ROW_FULL,
    ROW_PARTIAL,
    ROW_ABSOLUTE_HREF,
    ROW_DEEP_HREF,
    ROW_TAIL,
    ROW_EXTRA,
]


def trending_html(rows: list[str] | None = None, *, wrap: bool = True) -> str:
    """拼出 GitHub Trending 页面骨架（结构参考 article.Box-row 实测）。"""
    body = "\n".join(rows if rows is not None else DEFAULT_ROWS)
    if not wrap:
        return body
    return (
        '<!DOCTYPE html>\n<html lang="en">\n'
        "<head><title>Trending repositories on GitHub today</title></head>\n"
        "<body>\n<div class=\"application-main\">\n"
        "<div class=\"Box\">\n  <div class=\"Box-header\"><h1>Trending</h1></div>\n"
        f"  <div class=\"Box-body\">\n{body}\n  </div>\n</div>\n"
        "</div>\n</body>\n</html>\n"
    )


def expected_rows() -> list[dict[str, Any]]:
    """夹具里预期被解析出的条目（按页面顺序）。"""
    return [
        {
            "title": "acme-org/model-lab",
            "url": "https://github.com/acme-org/model-lab",
            "language": "Python",
            "stars": 124887,
            "stars_today": 25599,
            "forks": 8123,
            "description": "A tiny inference engine & toolkit for LLMs",
        },
        {
            "title": "solo-dev/blobber",
            "url": "https://github.com/solo-dev/blobber",
            "language": "",
            "stars": 5,
            "stars_today": 7,
            "forks": 0,
            "description": "",
        },
        {
            "title": "tail-org/agent-desk",
            "url": "https://github.com/tail-org/agent-desk",
            "language": "TypeScript",
            "stars": 3001,
            "stars_today": 1234,
            "forks": 210,
            "description": "Agent workspace",
        },
        {
            "title": "extra-org/rag-notes",
            "url": "https://github.com/extra-org/rag-notes",
            "language": "",
            "stars": 0,
            "stars_today": 42,
            "forks": 0,
            "description": "RAG notes",
        },
    ]


class _QuietServer(ThreadingHTTPServer):
    """本地假服务：连接被客户端中止时不向 stderr 打 traceback（测试噪音）。"""

    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: ARG002
        return


class FakeTrendingSite:
    """假 GitHub Trending 页：记录请求（含 query），可切换状态码 / 页面内容。"""

    def __init__(self, html: str | None = None, *, status: int = 200) -> None:
        self.html = trending_html() if html is None else html
        self.status = status
        self.requests: list[dict[str, Any]] = []
        self._httpd = _QuietServer(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/trending"

    @property
    def query_of_requests(self) -> list[str]:
        return [str(row["path"]) for row in self.requests]

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> FakeTrendingSite:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # 夹具：不保持长连接，避免测试抖动

            def do_GET(self) -> None:  # noqa: N802
                server.requests.append({"method": "GET", "path": self.path})
                if server.status == 200:
                    payload = server.html.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                else:
                    payload = b"denied by fake server"
                    ctype = "text/plain"
                self.send_response(server.status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


# --------------------------------------------------------------------------- 假代理 / 假节点


class FakeNodeProxy:
    """假 HTTP 代理（假"节点"）：可配置对 GET/CONNECT 的响应。

    mode:
    - ``healthy``：任何请求 200（health 路径给 204）→ 健康检查通过
    - ``http403`` / ``http500``：端口通但一律拒绝 → 模拟"Clash 开着但节点坏"
    - ``drop``：接受 TCP 后直接断开 → 模拟节点超时/重置
    """

    def __init__(
        self, mode: str = "healthy", *, body: bytes = b"ok", health_status: int = 204
    ) -> None:
        self.mode = mode
        self.body = body
        self.health_status = health_status
        self.requests: list[dict[str, Any]] = []
        self.tcp_connections = 0
        self._httpd = _QuietServer(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def absolute_uri_gets(self) -> list[str]:
        return [row["path"] for row in self.requests if row["path"].startswith("http")]

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> FakeNodeProxy:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                server.tcp_connections += 1
                super().setup()

            def _reply(self, status: int, data: bytes, ctype: str = "text/plain") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                server.requests.append({"method": "GET", "path": self.path})
                if server.mode == "drop":
                    self.close_connection = True
                    return
                if server.mode == "http403":
                    self._reply(403, b"forbidden by node")
                    return
                if server.mode == "http500":
                    self._reply(500, b"upstream failure")
                    return
                if self.path.endswith("/generate_204") or self.path.endswith("generate_204"):
                    self._reply(server.health_status, b"")
                    return
                self._reply(200, server.body, "application/rss+xml; charset=utf-8")

            def do_CONNECT(self) -> None:  # noqa: N802
                server.requests.append({"method": "CONNECT", "path": self.path})
                if server.mode == "drop":
                    self.close_connection = True
                    return
                if server.mode in {"http403", "http500"}:
                    self._reply(403 if server.mode == "http403" else 500, b"no tunnel")
                    return
                # healthy：不做 MITM，直接给 502，阻止 TLS 隧道（避免任何外部连接）
                self._reply(502, b"no tunnel in fake node")

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


# --------------------------------------------------------------------------- 配置夹具


def trending_source_payload(url: str, *, limit: int = 12, period: str = "daily") -> dict[str, Any]:
    return {
        "id": "github_trending_page",
        "name": "GitHub 当日热榜",
        "type": "github_trending",
        "url": url,
        "enabled": True,
        "weight": 0.9,
        "extra": {"period": period, "limit": limit},
    }


def rss_source_payload(url: str, *, source_id: str = "fake_feed") -> dict[str, Any]:
    return {
        "id": source_id,
        "name": "假 RSS",
        "type": "rss",
        "url": url,
        "enabled": True,
        "weight": 1.0,
    }


def raw_config(
    ws: Path,
    *,
    sources: list[dict[str, Any]],
    proxy_url: str = "",
    proxy_enabled: bool = False,
    llm_base_url: str = "http://127.0.0.1:1/v1",
) -> dict[str, Any]:
    raw = m4.sanitized_real_raw()
    raw["app"]["data_dir"] = str(ws / "data")
    raw["app"]["log_dir"] = str(ws / "logs")
    raw["llm"]["base_url"] = llm_base_url
    raw["llm"].pop("api_key_env", None)
    raw["proxy"] = {"enabled": proxy_enabled, "url": proxy_url if proxy_enabled else ""}
    raw["push"]["wecom"] = {"enabled": False, "webhook": ""}
    email_cfg = raw["push"].setdefault("email", {})
    email_cfg["enabled"] = False
    email_cfg.pop("password_env", None)
    raw["sources"] = sources
    return raw


def staged_config(ws: Path, raw: dict[str, Any], name: str = "config.yaml") -> Path:
    return m4.dump_config(ws / name, raw)


def dump_ok(title: str, payload: Any) -> str:
    return title + "\n" + json.dumps(payload, ensure_ascii=False, indent=2)


__all__ = [
    "BAD_PORT",
    "DEAD_FEED",
    "DEFAULT_ROWS",
    "EVIDENCE",
    "HEALTH_PROBE",
    "PROJECT",
    "PYTHON",
    "RAW",
    "TEMPROOT",
    "FakeNodeProxy",
    "FakeTrendingSite",
    "clean_proxy_env",
    "dump_ok",
    "expected_rows",
    "m3",
    "m4",
    "m5",
    "raw_config",
    "rss_source_payload",
    "staged_config",
    "trending_html",
    "trending_source_payload",
    "write_json",
]
