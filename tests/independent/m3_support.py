"""M3 独立测试支撑模块：假 LLM / 假 RSS / 假企微服务（HTTP/HTTPS/CONNECT 代理）/ 假 SMTP。

设计约束（与 M2 一致）：
- 断言只基于 CLI 输出、HTTP 服务收到的请求体、SMTP 会话内容与文件观察；
  产品代码只在「构造夹具」时导入（models / compose），不参与判定。
- 一切写入落在 `%TEMP%\\aihot-m3-tests` 与 `reports/evidence/m3`，绝不触碰真实 data/aihot.db、
  data/digests 与真实 config.yaml。
- 真实企业微信 webhook / 真实邮箱地址绝不使用：所有 webhook 指向本地假服务，
  SMTP 指向本地假服务器。
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email import message_from_bytes, policy
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

PROJECT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
EVIDENCE_ROOT = os.environ.get("AIHOT_M3_EVIDENCE", "m3")
EVIDENCE = PROJECT / "reports" / "evidence" / EVIDENCE_ROOT
TEMPROOT = Path(os.environ.get("TEMP", str(Path.home()))) / "aihot-m3-tests"
CERTS = TEMPROOT / "certs"
CA_PEM = CERTS / "ca.pem"
SERVER_PEM = CERTS / "server.pem"
SERVER_KEY = CERTS / "server-key.pem"
REAL_DB = PROJECT / "data" / "aihot.db"
REAL_DIGESTS = PROJECT / "data" / "digests"
REAL_CONFIG = PROJECT / "config.yaml"
REAL_LOGS = PROJECT / "logs"

sys.path.insert(0, str(PROJECT / "src"))
from aihot.models import Digest, NewsItem  # noqa: E402  （仅构造夹具）


def workspace(name: str) -> Path:
    path = TEMPROOT / "work" / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", "replace")


def has_traceback(text: str) -> bool:
    return "Traceback (most recent call last)" in text


# --------------------------------------------------------------------------- 假 LLM 服务


@dataclass
class LlmReply:
    status: int = 200
    content: str | None = '{"items": []}'
    usage: dict[str, int] | None = None
    delay: float = 0.0
    raw_body: bytes | None = None

    def body(self) -> bytes:
        if self.raw_body is not None:
            return self.raw_body
        usage = self.usage or {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        payload: dict[str, Any] = {"id": "fake", "object": "chat.completion", "usage": usage}
        payload["choices"] = (
            [{"index": 0, "message": {"role": "assistant", "content": self.content}}]
            if self.content is not None
            else []
        )
        return json.dumps(payload).encode("utf-8")


class FakeLLM:
    """OpenAI 兼容假服务：记录每次请求体，可按请求动态生成响应。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.generator: Callable[[dict[str, Any] | None], str] | None = None
        self.generator_usage: dict[str, int] | None = None
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def request_count(self) -> int:
        return len(self.requests)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> FakeLLM:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    body = None
                server.requests.append({"path": self.path, "body": body, "at": time.time()})
                content = server.generator(body) if server.generator else "{}"
                reply = LlmReply(content=content, usage=server.generator_usage)
                data = reply.body()
                self.send_response(reply.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


def default_llm_generator(body: dict[str, Any] | None) -> str:
    """默认假 LLM：综述请求返回纯文本，条目加工请求返回完整 JSON。"""
    messages = (body or {}).get("messages") or []
    system = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    user = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
    if "主编" in system or "今日综述" in user:
        return (
            "今日 AI 领域以模型发布与开源工具为主：多家厂商更新了推理模型与端侧方案，"
            "开源社区发布了新的推理框架；研究侧出现了关于长上下文与多模态对齐的新进展，"
            "产业侧则集中在算力供给与合规落地上。"
        )
    indexes = [int(m) for m in re.findall(r"^\[(\d+)\]", user, flags=re.MULTILINE)]
    if not indexes:
        indexes = list(range(1, 6))
    items = [
        {
            "index": index,
            "cn_title": f"假 LLM 生成标题 {index}：AI 模型与工具更新",
            "cn_summary": f"这是第 {index} 条假摘要，用于独立验证渲染与推送链路，不涉及真实内容。",
            "category": "模型发布" if index % 3 else "开源工具",
            "importance": max(20, 95 - index * 3),
            "is_ai": True,
        }
        for index in indexes
    ]
    return json.dumps({"items": items}, ensure_ascii=False)


# --------------------------------------------------------------------------- 假 RSS 源


def rss_feed(entries: list[dict[str, str]]) -> bytes:
    """生成 RSS 2.0 feed（pubDate 为 RFC822）。"""
    items = []
    for entry in entries:
        items.append(
            "<item>"
            f"<title>{entry['title']}</title>"
            f"<link>{entry['link']}</link>"
            f"<description>{entry.get('description', '')}</description>"
            f"<pubDate>{entry['pubDate']}</pubDate>"
            "</item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        "<title>aihot fake feed</title><link>http://127.0.0.1/</link>"
        "<description>independent test feed</description>" + "".join(items) + "</channel></rss>"
    )
    return xml.encode("utf-8")


def rfc822(offset_minutes: int = 60) -> str:
    moment = datetime.now(UTC) - timedelta(minutes=offset_minutes)
    return moment.strftime("%a, %d %b %Y %H:%M:%S +0000")


class FakeRSS:
    """本地假 RSS 源（供 run 的采集阶段使用）。"""

    def __init__(self, entries: list[dict[str, str]]) -> None:
        self.entries = entries
        self.hits = 0
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}/feed.xml"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> FakeRSS:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                server.hits += 1
                if self.path.startswith("/feed.xml"):
                    data = rss_feed(server.entries)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
                else:
                    data = b"not found"
                    self.send_response(404)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


# --------------------------------------------------------------------------- 假企微 webhook


class _TLSHTTPServer(ThreadingHTTPServer):
    """直接以 HTTPS 提供服务（模块级测试用）。"""

    ssl_context: ssl.SSLContext

    def get_request(self) -> tuple[socket.socket, Any]:
        sock, addr = super().get_request()
        return self.ssl_context.wrap_socket(sock, server_side=True), addr


class FakeWeCom:
    """假企业微信 webhook 服务。

    - 默认 HTTP 直连（模块级测试用 webhook 可任意填写）
    - tls=True 时直接以 HTTPS 提供服务
    - proxy=True 时作为 HTTP CONNECT 代理 + TLS MITM（CLI 级 e2e：webhook 必须是官方域名前缀）

    mode: ok / errcode / http502 / nonjson / drop
    """

    def __init__(
        self,
        mode: str = "ok",
        *,
        tls: bool = False,
        proxy: bool = False,
        cert: Path = SERVER_PEM,
        key: Path = SERVER_KEY,
    ) -> None:
        self.mode = mode
        self.proxy = proxy
        self.requests: list[dict[str, Any]] = []
        self._ssl_context: ssl.SSLContext | None = None
        if tls or proxy:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
            self._ssl_context = ctx
        server_cls = _TLSHTTPServer if tls else ThreadingHTTPServer
        self._httpd = server_cls(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        if tls:
            self._httpd.ssl_context = self._ssl_context  # type: ignore[attr-defined]
        self.port = int(self._httpd.server_address[1])
        self.url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake-key-123456"
        self.local_url = f"http://127.0.0.1:{self.port}/cgi-bin/webhook/send?key=fake-key-123456"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def image_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["payload"] and r["payload"].get("msgtype") == "image"]

    @property
    def markdown_requests(self) -> list[dict[str, Any]]:
        return [
            r for r in self.requests if r["payload"] and r["payload"].get("msgtype") == "markdown"
        ]

    @property
    def text_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["payload"] and r["payload"].get("msgtype") == "text"]

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> FakeWeCom:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _record(self, path: str, payload: dict[str, Any] | None, raw_len: int, tls: bool) -> None:
        self.requests.append(
            {"path": path, "payload": payload, "raw_len": raw_len, "tls": tls, "at": time.time()}
        )

    def _respond(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any] | None) -> None:
        mode = self.mode
        if mode == "drop":
            handler.close_connection = True
            with contextlib.suppress(OSError):
                handler.connection.close()
            return
        if mode == "fail_image" and payload and payload.get("msgtype") == "image":
            data = json.dumps({"errcode": 93000, "errmsg": "image rejected"}).encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
        elif mode == "http502":
            data = b"bad gateway"
            handler.send_response(502)
            handler.send_header("Content-Type", "text/plain")
        elif mode == "nonjson":
            data = b"<html>not json</html>"
            handler.send_response(200)
            handler.send_header("Content-Type", "text/html")
        elif mode == "errcode":
            data = json.dumps({"errcode": 93000, "errmsg": "invalid webhook key"}).encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
        else:
            data = json.dumps({"errcode": 0, "errmsg": "ok"}).encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _read_body(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def do_CONNECT(self) -> None:  # noqa: N802
                self.send_response(200, "Connection established")
                self.end_headers()
                assert server._ssl_context is not None
                try:
                    tls = server._ssl_context.wrap_socket(self.connection, server_side=True)
                except (ssl.SSLError, OSError):
                    self.close_connection = True
                    return
                self.connection = tls
                self.rfile = tls.makefile("rb", -1)
                self.wfile = tls.makefile("wb", 0)
                self.close_connection = False
                while not self.close_connection:
                    self.handle_one_request()

            def do_POST(self) -> None:  # noqa: N802
                raw = self._read_body()
                if self.path.startswith("/__mode"):
                    with contextlib.suppress(
                        json.JSONDecodeError, UnicodeDecodeError, AttributeError
                    ):
                        server.mode = str(json.loads(raw.decode("utf-8")).get("mode"))
                    data = b'{"ok": true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = None
                tls = isinstance(self.connection, ssl.SSLSocket)
                server._record(self.path, payload, len(raw), tls)
                server._respond(self, payload)

            def do_GET(self) -> None:  # noqa: N802
                data = b"aihot fake wecom"
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


# --------------------------------------------------------------------------- 假 SMTP 服务


class FakeSMTP:
    """最小可用假 SMTP 服务（EHLO / STARTTLS / AUTH LOGIN|PLAIN / MAIL / RCPT / DATA / QUIT）。

    - use_ssl=True 时监听端口直接 TLS（模拟 465）
    - 否则明文 + STARTTLS（模拟 587 路径，产品会调用 starttls()）
    - reject_auth=True 时所有认证失败（535）
    """

    def __init__(
        self,
        *,
        use_ssl: bool = False,
        reject_auth: bool = False,
        username: str = "sender@example.com",
        password: str = "fake-auth-code",
    ) -> None:
        self.use_ssl = use_ssl
        self.reject_auth = reject_auth
        self.username = username
        self.password = password
        self.messages: list[dict[str, Any]] = []
        self.auth_attempts: list[dict[str, Any]] = []
        self._stop = False
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(certfile=str(SERVER_PEM), keyfile=str(SERVER_KEY))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self._sock.settimeout(0.5)
        self.port = int(self._sock.getsockname()[1])
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self) -> None:
        self._stop = True
        with contextlib.suppress(OSError):
            self._sock.close()

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        state: dict[str, Any] = {
            "tls": self.use_ssl,
            "auth": False,
            "auth_user": "",
            "mail_from": "",
            "rcpt": [],
        }
        try:
            if self.use_ssl:
                conn = self._ctx.wrap_socket(conn, server_side=True)
            self._send(conn, b"220 aihot-test ESMTP ready")
            while True:
                line = self._readline(conn)
                if line is None:
                    break
                text = line.decode("utf-8", "replace").strip()
                upper = text.upper()
                if upper.startswith(("EHLO", "HELO")):
                    features = ["250-aihot-test", "250-8BITMIME", "250-SIZE 10485760"]
                    if not state["tls"]:
                        features.append("250-STARTTLS")
                    features.append("250 AUTH LOGIN PLAIN")
                    self._send(conn, "\r\n".join(features).encode())
                elif upper == "STARTTLS":
                    self._send(conn, b"220 2.0.0 Ready to start TLS")
                    try:
                        conn = self._ctx.wrap_socket(conn, server_side=True)
                    except (ssl.SSLError, OSError):
                        break
                    state["tls"] = True
                elif upper.startswith("AUTH LOGIN"):
                    self._auth_login(conn, state)
                elif upper.startswith("AUTH PLAIN"):
                    self._auth_plain(conn, state, text)
                elif upper.startswith("MAIL FROM"):
                    state["mail_from"] = text
                    self._send(conn, b"250 2.1.0 OK")
                elif upper.startswith("RCPT TO"):
                    state["rcpt"].append(text)
                    self._send(conn, b"250 2.1.5 OK")
                elif upper == "DATA":
                    self._send(conn, b"354 End data with <CR><LF>.<CR><LF>")
                    data = self._read_data(conn)
                    self.messages.append(
                        {
                            "mail_from": state["mail_from"],
                            "rcpt": list(state["rcpt"]),
                            "data": data,
                            "tls": state["tls"],
                            "auth_user": state["auth_user"],
                        }
                    )
                    state["rcpt"] = []
                    self._send(conn, b"250 2.0.0 OK: queued")
                elif upper == "RSET":
                    state["rcpt"] = []
                    self._send(conn, b"250 2.0.0 OK")
                elif upper == "NOOP":
                    self._send(conn, b"250 2.0.0 OK")
                elif upper == "QUIT":
                    self._send(conn, b"221 2.0.0 Bye")
                    break
                else:
                    self._send(conn, b"502 5.5.2 Command not implemented")
        except (OSError, ssl.SSLError):
            pass
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def _auth_login(self, conn: socket.socket, state: dict[str, Any]) -> None:
        self._send(conn, b"334 VXNlcm5hbWU6")
        user_line = self._readline(conn)
        self._send(conn, b"334 UGFzc3dvcmQ6")
        pass_line = self._readline(conn)
        user = base64.b64decode(user_line or b"").decode("utf-8", "replace") if user_line else ""
        password = (
            base64.b64decode(pass_line or b"").decode("utf-8", "replace") if pass_line else ""
        )
        self._finish_auth(conn, state, user, password)

    def _auth_plain(self, conn: socket.socket, state: dict[str, Any], text: str) -> None:
        blob = text.split(" ", 2)[2] if len(text.split(" ", 2)) > 2 else ""
        user = password = ""
        try:
            parts = base64.b64decode(blob).split(b"\x00")
            if len(parts) >= 3:
                user = parts[1].decode("utf-8", "replace")
                password = parts[2].decode("utf-8", "replace")
        except (ValueError, UnicodeDecodeError):
            pass
        self._finish_auth(conn, state, user, password)

    def _finish_auth(
        self, conn: socket.socket, state: dict[str, Any], user: str, password: str
    ) -> None:
        self.auth_attempts.append(
            {"user": user, "password_len": len(password), "tls": state["tls"]}
        )
        if self.reject_auth or user != self.username or password != self.password:
            self._send(conn, b"535 5.7.8 Authentication failed")
            return
        state["auth"] = True
        state["auth_user"] = user
        self._send(conn, b"235 2.7.0 Authentication successful")

    @staticmethod
    def _send(conn: socket.socket, data: bytes) -> None:
        conn.sendall(data + b"\r\n")

    @staticmethod
    def _readline(conn: socket.socket) -> bytes | None:
        buffer = bytearray()
        while True:
            try:
                chunk = conn.recv(1)
            except (TimeoutError, OSError):
                return None
            if not chunk:
                return None
            if chunk == b"\n":
                return bytes(buffer).rstrip(b"\r")
            buffer += chunk

    @classmethod
    def _read_data(cls, conn: socket.socket) -> bytes:
        buffer = bytearray()
        while True:
            try:
                chunk = conn.recv(1)
            except (TimeoutError, OSError):
                break
            if not chunk:
                break
            buffer += chunk
            if buffer.endswith(b"\r\n.\r\n"):
                return bytes(buffer[:-5])
        return bytes(buffer)


def parse_email(raw: bytes) -> Message:
    """解析原始邮件（policy=default：自动解码 RFC2047 主题/文件名）。"""
    return message_from_bytes(raw, policy=policy.default)


# --------------------------------------------------------------------------- 配置 / CLI


@dataclass
class CliResult:
    rc: int
    stdout: str
    stderr: str
    seconds: float
    command: str

    @property
    def text(self) -> str:
        return (
            f"$ {self.command}\nexit={self.rc} ({self.seconds:.1f}s)\n"
            f"--- stdout ---\n{self.stdout}\n--- stderr ---\n{self.stderr}"
        )

    @property
    def combined(self) -> str:
        return self.stdout + "\n" + self.stderr


def run_cli(
    args: list[str],
    *,
    timeout: float = 900.0,
    env_extra: dict[str, str] | None = None,
) -> CliResult:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)
    command = f"python -m aihot {' '.join(args)}"
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(PYTHON), "-m", "aihot", *args],
            cwd=str(PROJECT),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return CliResult(
            rc=-999,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr) + f"\n[harness] 超时 {timeout}s",
            seconds=time.perf_counter() - start,
            command=command,
        )
    return CliResult(
        rc=proc.returncode,
        stdout=_decode(proc.stdout),
        stderr=_decode(proc.stderr),
        seconds=time.perf_counter() - start,
        command=command,
    )


DEFAULT_SOURCES: list[dict[str, Any]] = [
    {
        "id": "qbitai",
        "name": "量子位",
        "type": "rss",
        "url": "https://www.qbitai.com/feed",
        "enabled": True,
        "weight": 1.0,
    },
]


def write_config(
    path: Path,
    *,
    data_dir: Path,
    log_dir: Path,
    sources: list[dict[str, Any]] | None = None,
    llm_base_url: str = "http://127.0.0.1:1/v1",
    llm_api_key: str = "sk-fake-000000000000",
    wecom_enabled: bool = False,
    wecom_webhook: str = "",
    email_enabled: bool = False,
    email_overrides: dict[str, Any] | None = None,
    top_n: int = 12,
    window_hours: int = 24,
    pipeline: dict[str, Any] | None = None,
) -> Path:
    """写出测试配置（data/log 在临时目录；webhook/邮箱均为假值）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    email: dict[str, Any] = {
        "enabled": email_enabled,
        "smtp_host": "127.0.0.1",
        "smtp_port": 1025,
        "use_ssl": False,
        "username": "sender@example.com",
        "password": "fake-auth-code",
        "from_addr": "sender@example.com",
        "to_addrs": ["receiver@example.com"],
        "attach_image": False,
    }
    if email_overrides:
        email.update(email_overrides)
    raw: dict[str, Any] = {
        "app": {
            "timezone": "Asia/Shanghai",
            "window_hours": window_hours,
            "top_n": top_n,
            "log_level": "INFO",
            "data_dir": str(data_dir),
            "log_dir": str(log_dir),
        },
        "llm": {
            "provider": "fake",
            "base_url": llm_base_url,
            "model": "fake-model",
            "api_key": llm_api_key,
            "batch_size": 5,
            "daily_cost_limit_cny": 1.0,
        },
        "proxy": {"enabled": False, "url": ""},
        "push": {
            "wecom": {"enabled": wecom_enabled, "webhook": wecom_webhook},
            "email": email,
        },
        "sources": sources if sources is not None else DEFAULT_SOURCES,
    }
    if pipeline is not None:
        raw["pipeline"] = pipeline
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- 夹具


def make_item(
    index: int,
    *,
    title: str | None = None,
    summary: str = "",
    category: str = "模型发布",
    importance: int = 80,
    url: str | None = None,
    mentions: int = 1,
    source_ids: list[str] | None = None,
    links: list[dict[str, str]] | None = None,
    source_id: str = "qbitai",
) -> NewsItem:
    resolved_url = url or f"https://example.com/news/{index}"
    extra: dict[str, Any] = {}
    if mentions > 1:
        extra["mention_count"] = mentions
    if source_ids:
        extra["source_ids"] = source_ids
    if links:
        extra["links"] = links
    return NewsItem(
        source_id=source_id,
        title=title or f"Fake English Title {index}",
        url=resolved_url,
        published_at=datetime.now(UTC) - timedelta(hours=index % 20 + 1),
        raw_summary="raw summary",
        lang="en",
        extra=extra,
        cluster_id=index,
        category=category,
        importance=importance,
        cn_title=title or f"假中文标题 {index}：某公司发布新模型",
        cn_summary=summary,
    )


def make_digest(
    *,
    top_count: int = 12,
    brief_count: int = 12,
    summary: str = "今日综述：这是用于独立测试的综述文本，包含中文与 English mixed 内容。",
    digest_date: date = date(2026, 9, 24),
    stats: dict[str, Any] | None = None,
    top_items: list[NewsItem] | None = None,
    briefs: list[NewsItem] | None = None,
) -> Digest:
    resolved_top = top_items or [
        make_item(
            i,
            summary=f"第 {i} 条摘要：用于渲染测试的中文摘要文本。",
            mentions=1 + (i % 3),
            source_ids=["qbitai", "hackernews"] if i % 3 == 0 else None,
            links=[
                {
                    "source_id": "qbitai",
                    "title": f"中文标题 {i}",
                    "url": f"https://example.com/a/{i}",
                },
                {
                    "source_id": "hackernews",
                    "title": f"EN {i}",
                    "url": f"https://example.com/b/{i}",
                },
            ]
            if i % 3 == 0
            else None,
        )
        for i in range(1, top_count + 1)
    ]
    resolved_briefs = briefs or [
        make_item(
            100 + i,
            title=f"简讯标题 {i}：值得一看的行业动态",
            category="行业动态",
            importance=40,
        )
        for i in range(1, brief_count + 1)
    ]
    return Digest(
        digest_date=digest_date,
        summary=summary,
        top_items=resolved_top,
        briefs=resolved_briefs,
        stats=stats
        or {
            "scanned": 300,
            "kept": 120,
            "clusters": 90,
            "selected": len(resolved_top),
            "briefs": len(resolved_briefs),
            "sources": 6,
            "cost_cny": 0.35,
        },
    )


# --------------------------------------------------------------------------- 文件观察


def render_image_if_available(digest: Digest, out_dir: Path) -> Path | None:
    """调用产品渲染器出图；失败（RenderError）返回 None（供假服务测试使用）。"""
    from aihot.presentation.render_image import RenderError, render_images

    try:
        paths = render_images(digest, out_dir)
    except RenderError:
        return None
    return paths[0] if paths else None


def jpeg_info(path: Path) -> dict[str, Any]:
    """解析 JPEG SOI/SOF 头，返回宽度/高度/体积（不依赖第三方库）。"""
    data = path.read_bytes()
    info: dict[str, Any] = {
        "bytes": len(data),
        "soi": data[:2] == b"\xff\xd8",
        "eoi": data[-2:] == b"\xff\xd9",
        "width": None,
        "height": None,
        "progressive": False,
    }
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            info["height"] = int.from_bytes(data[index + 5 : index + 7], "big")
            info["width"] = int.from_bytes(data[index + 7 : index + 9], "big")
            info["progressive"] = marker == 0xC2
            break
        index += 2 + length
    return info


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def real_state_snapshot() -> dict[str, Any]:
    """真实库 + 真实日报存档目录快照（证明测试未改动）。"""
    snapshot: dict[str, Any] = {"db": {}, "digests": {}}
    if REAL_DB.exists():
        stat = REAL_DB.stat()
        snapshot["db"] = {
            "bytes": stat.st_size,
            "mtime": stat.st_mtime,
            "items": db_one(REAL_DB, "SELECT COUNT(*) FROM items"),
            "enriched": db_one(REAL_DB, "SELECT COUNT(*) FROM items WHERE enriched_at IS NOT NULL"),
            "runs": db_one(REAL_DB, "SELECT COUNT(*) FROM runs"),
            "clusters": db_one(REAL_DB, "SELECT COUNT(*) FROM clusters"),
            "digests": db_one(REAL_DB, "SELECT COUNT(*) FROM digests"),
        }
    if REAL_DIGESTS.exists():
        snapshot["digests"] = {
            path.name: {"bytes": path.stat().st_size, "mtime": path.stat().st_mtime}
            for path in sorted(REAL_DIGESTS.iterdir())
            if path.is_file()
        }
    return snapshot


# --------------------------------------------------------------------------- 数据库观察


def db_query(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def db_one(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> Any:
    rows = db_query(db_path, sql, params)
    if not rows:
        return None
    return next(iter(rows[0].values()))


def seed_enriched_items(db_path: Path, items: list[dict[str, Any]]) -> int:
    """向临时库写入「已加工」条目（供 preview / resend / dry-run 使用）。"""
    from aihot.utils import url_hash  # 夹具哈希与产品一致

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, url_hash TEXT NOT NULL,
            source_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
            published_at TEXT, raw_summary TEXT NOT NULL DEFAULT '', lang TEXT NOT NULL DEFAULT '',
            extra TEXT NOT NULL DEFAULT '{}', fetched_at TEXT NOT NULL, first_seen_at TEXT NOT NULL,
            cluster_id INTEGER, category TEXT, importance INTEGER, cn_title TEXT, cn_summary TEXT,
            enriched_at TEXT, UNIQUE (source_id, url_hash));
        CREATE TABLE IF NOT EXISTS clusters (
            cluster_id INTEGER PRIMARY KEY, canonical_title TEXT NOT NULL,
            item_hashes TEXT NOT NULL DEFAULT '[]', source_ids TEXT NOT NULL DEFAULT '[]',
            mention_count INTEGER NOT NULL DEFAULT 1, heat_score REAL NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, stage_stats TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS digests (
            digest_date TEXT PRIMARY KEY, summary TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL DEFAULT '{}', image_path TEXT NOT NULL DEFAULT '',
            md_path TEXT NOT NULL DEFAULT '', html_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL);
        """
    )
    now = datetime.now(UTC).isoformat()
    rows = [
        (
            url_hash(item["url"]),
            item["source_id"],
            item["title"],
            item["url"],
            item.get("published_at") or now,
            item.get("raw_summary", ""),
            item.get("lang", ""),
            json.dumps(item.get("extra", {}), ensure_ascii=False),
            now,
            now,
            item.get("cluster_id"),
            item.get("category", "模型发布"),
            item.get("importance", 80),
            item.get("cn_title", item["title"]),
            item.get("cn_summary", ""),
            now,
        )
        for item in items
    ]
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO items (url_hash, source_id, title, url, published_at,"
            " raw_summary, lang, extra, fetched_at, first_seen_at, cluster_id, category,"
            " importance, cn_title, cn_summary, enriched_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    count = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()[0]
    conn.close()
    return int(count)


# --------------------------------------------------------------------------- 证据 / 断言


@dataclass
class Suite:
    name: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def check(self, item: str, ok: bool, detail: str, *, evidence: str | None = None) -> bool:
        record = {"item": item, "ok": bool(ok), "detail": detail}
        self.checks.append(record)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {item}: {detail}")
        if evidence is not None:
            self.evidence(f"{item}.txt", evidence)
        return bool(ok)

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  [NOTE] {text}")

    def evidence(self, rel_name: str, text: str) -> Path:
        safe = rel_name
        for char in '<>:"/\\|?*':
            safe = safe.replace(char, "_")
        path = EVIDENCE / self.name / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def evidence_json(self, rel_name: str, data: Any) -> Path:
        return self.evidence(rel_name, json.dumps(data, ensure_ascii=False, indent=2))

    def finish(self) -> int:
        passed = sum(1 for check in self.checks if check["ok"])
        failed = len(self.checks) - passed
        summary = {
            "suite": self.name,
            "passed": passed,
            "failed": failed,
            "checks": self.checks,
            "notes": self.notes,
        }
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        path = EVIDENCE / f"{self.name}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n== {self.name}: {passed} passed / {failed} failed == summary={path}")
        return 0 if failed == 0 else 1
