"""M5 可用性修复 · 独立验收支撑模块（m5r = M5 fix review）。

设计约束（黑盒验收）：
- 只用 localhost 假服务：假 RSS / 假 LLM / 假企微（CONNECT+TLS MITM）/ 假 SMTP / 假 HTTP 代理；
- 不访问公网、不发真实企微消息、不发真实邮件、不调真实 LLM；
- 不修改 config.yaml / data / logs 等真实资产；证据写到 reports/evidence/m5r/<suite>/。

复用既有 tests/independent/m3_support.py、m4_support.py（旧文件不改动）。
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, date, datetime
from datetime import time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from zoneinfo import ZoneInfo

# 证据根目录：reports/evidence/m5r（可用 AIHOT_M5R_EVIDENCE 覆盖）
os.environ["AIHOT_M4_EVIDENCE"] = os.environ.get("AIHOT_M5R_EVIDENCE", "m5r")
os.environ["AIHOT_M3_EVIDENCE"] = os.environ["AIHOT_M4_EVIDENCE"]

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m4_support as m4  # noqa: E402

PROJECT = m3.PROJECT
PYTHON = m3.PYTHON
TEMPROOT = m3.TEMPROOT
EVIDENCE = m3.EVIDENCE
RAW = m4.RAW
BAD_PORT = 1  # 本机坏端口：连接必然被拒绝
CN_TZ = ZoneInfo("Asia/Shanghai")

_PROXY_ENV_KEYS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")


def clean_proxy_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """从当前进程环境中移除所有 http(s)_proxy 变量，再返回 extra 覆盖。

    注意：Windows 的 os.environ 大小写不敏感，写 "https_proxy" 会覆盖 "HTTPS_PROXY"
    （曾导致假服务被绕过、直连真实域名），因此这里只做删除，不做空串占位。
    """
    for key in list(os.environ):
        if key.lower() in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
    return dict(extra or {})


def write_json(name: str, data: Any) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- 证书


def _openssl_cmd() -> list[str]:
    """优先用 Git 自带 openssl（不依赖 Anaconda 的 OPENSSL_CONF 环境）。"""
    candidates = [
        Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
        Path(r"C:\Program Files (x86)\Git\usr\bin\openssl.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate)]
    return ["openssl"]


def ensure_certs() -> dict[str, Path]:
    """生成 m3_support 约定的自签 CA + 服务器证书（仅用于 localhost 假服务）。"""
    certs = m3.CERTS
    certs.mkdir(parents=True, exist_ok=True)
    if m3.CA_PEM.exists() and m3.SERVER_PEM.exists() and m3.SERVER_KEY.exists():
        return {"ca": m3.CA_PEM, "server": m3.SERVER_PEM, "key": m3.SERVER_KEY}
    ca_key = certs / "ca-key.pem"
    csr = certs / "server.csr"
    san = certs / "san.cnf"
    san.write_text(
        "subjectAltName=DNS:qyapi.weixin.qq.com,DNS:localhost,IP:127.0.0.1\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )

    def run(*args: str) -> None:
        subprocess.run(cmd + list(args), check=True, capture_output=True, env=openssl_env)

    cmd = _openssl_cmd()
    openssl_env = dict(os.environ)
    openssl_env["OPENSSL_CONF"] = str(certs / "openssl.cnf")
    (certs / "openssl.cnf").write_text(
        "[ req ]\n"
        "distinguished_name = req_dn\n"
        "prompt = no\n"
        "[ req_dn ]\n"
        "CN = aihot-m5r\n",
        encoding="utf-8",
    )

    run(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(ca_key), "-out", str(m3.CA_PEM), "-days", "30",
        "-subj", "/CN=aihot-m5r-test-ca",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
    )
    run(
        "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(m3.SERVER_KEY), "-out", str(csr),
        "-subj", "/CN=qyapi.weixin.qq.com",
    )
    run(
        "x509", "-req", "-in", str(csr), "-CA", str(m3.CA_PEM), "-CAkey", str(ca_key),
        "-CAcreateserial", "-out", str(m3.SERVER_PEM), "-days", "30", "-extfile", str(san),
    )
    return {"ca": m3.CA_PEM, "server": m3.SERVER_PEM, "key": m3.SERVER_KEY}


# --------------------------------------------------------------------------- 假服务


class RecordedWeComProbe(m3.FakeWeCom):
    """FakeWeCom 扩展：GET（预检探测）也记录，并返回 errcode=0 的 JSON。"""

    # 类型标注：do_GET_calls: list[dict[str, Any]]
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.do_GET_calls = []
        self.connect_calls = 0

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(m3.FakeWeCom._handler(self)):  # type: ignore[misc, valid-type]
            def do_CONNECT(self) -> None:  # noqa: N802
                server.connect_calls += 1
                super().do_CONNECT()

            def do_GET(self) -> None:  # noqa: N802
                server.do_GET_calls.append({"path": self.path})
                data = json.dumps({"errcode": 0, "errmsg": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    @property
    def url_proxy(self) -> str:
        """作为 HTTPS 代理（CONNECT）使用的地址。"""
        return f"http://127.0.0.1:{self.port}"


class FakeHttpProxy:
    """记录型假 HTTP 代理：对任何 GET 返回预设正文（默认 RSS），并记录绝对 URI。"""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[dict[str, Any]] = []
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def proxied_get_paths(self) -> list[str]:
        """绝对 URI 形式的请求（说明确实走了代理）。"""
        return [row["path"] for row in self.requests if row["path"].startswith("http://")]

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> FakeHttpProxy:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                server.requests.append(
                    {"method": "GET", "path": self.path, "headers": dict(self.headers)}
                )
                data = server.body
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_CONNECT(self) -> None:  # noqa: N802
                server.requests.append({"method": "CONNECT", "path": self.path, "headers": {}})
                data = b"no tunnel in this fake"
                self.send_response(502, "Bad Gateway")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


# --------------------------------------------------------------------------- 配置 / DB 夹具


def build_raw(
    ws: Path,
    *,
    proxy_url: str,
    llm_base_url: str,
    wecom_webhook: str = "",
    wecom_enabled: bool = False,
    email: dict[str, Any] | None = None,
    source_url: str = "http://127.0.0.1:1/feed.xml",
    source_id: str = "fake_feed",
    proxy_enabled: bool = True,
    data_dir: Path | None = None,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """以真实配置为模板的脱敏副本（密钥全假、数据/日志指向临时目录）。"""
    raw = m4.sanitized_real_raw()
    raw["app"]["data_dir"] = str(data_dir or (ws / "data"))
    raw["app"]["log_dir"] = str(log_dir or (ws / "logs"))
    raw["llm"]["base_url"] = llm_base_url
    raw["llm"].pop("api_key_env", None)
    raw["proxy"] = {"enabled": proxy_enabled, "url": proxy_url if proxy_enabled else ""}
    raw["push"]["wecom"] = {"enabled": wecom_enabled, "webhook": wecom_webhook}
    email_cfg = raw["push"].setdefault("email", {})
    email_cfg["enabled"] = bool(email)
    if email:
        email_cfg.pop("password_env", None)
        email_cfg.update(email)
    raw["sources"] = [
        {
            "id": source_id,
            "name": "Fake",
            "type": "rss",
            "url": source_url,
            "enabled": True,
            "weight": 1.0,
        }
    ]
    return raw


def iso_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def local_moment(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, dtime(hour=hour, minute=minute, second=second), tzinfo=CN_TZ)


def insert_run(
    db_path: Path,
    *,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    stage_stats: dict[str, Any],
    error: str = "",
) -> None:
    """直接向 runs 表插入一条记录（用于构造报告统计夹具）。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL, stage_stats TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs"
                " (run_id, started_at, finished_at, status, stage_stats, error)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    iso_utc(started_at),
                    iso_utc(finished_at),
                    status,
                    json.dumps(stage_stats, ensure_ascii=False),
                    error,
                ),
            )
    finally:
        conn.close()


def fake_email_cfg(smtp_port: int, *, enabled: bool = True) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "smtp_host": "127.0.0.1",
        "smtp_port": smtp_port,
        "use_ssl": False,
        "username": "sender@example.com",
        "password": "fake-auth-code",
        "from_addr": "sender@example.com",
        "to_addrs": ["receiver@example.com"],
        "attach_image": False,
    }


def local_wecom_config(cfg: Any, webhook: str) -> Any:
    """把已加载配置的企微通道替换为「启用 + 指向本地假服务」的副本。

    用于在「企微强制直连、忽略环境代理」的新语义下做 localhost-only 验证：
    配置校验要求 webhook 以 https://qyapi.weixin.qq.com/ 开头，无法直接指向
    本地假服务，因此这里在**已通过校验的 Config 对象**上做定向替换（仅夹具用途）。
    """
    wecom = dataclasses.replace(cfg.push.wecom, enabled=True, webhook=webhook)
    return dataclasses.replace(cfg, push=dataclasses.replace(cfg.push, wecom=wecom))


def email_text(raw: bytes) -> str:
    """把原始邮件解码成可断言的文本（主题 + 所有 MIME 部分的解码正文）。"""
    message = m3.parse_email(raw)
    chunks = [str(message.get("Subject") or "")]
    for part in message.walk():
        payload = part.get_payload(decode=True)
        if payload:
            charset = part.get_content_charset() or "utf-8"
            chunks.append(payload.decode(charset, "replace"))
    return "\n".join(chunks)


def rss_entries(count: int = 8, tag: str = "m5r") -> list[dict[str, str]]:
    return [
        {
            "title": f"M5 独立验收假新闻 {index}：模型与工具更新（{tag}）",
            "link": f"https://example.com/{tag}/{index}",
            "description": "AI model and tool update",
            "pubDate": m3.rfc822(30 + index),
        }
        for index in range(1, count + 1)
    ]


__all__ = [
    "BAD_PORT",
    "CN_TZ",
    "EVIDENCE",
    "PROJECT",
    "PYTHON",
    "RAW",
    "TEMPROOT",
    "FakeHttpProxy",
    "RecordedWeComProbe",
    "build_raw",
    "clean_proxy_env",
    "email_text",
    "ensure_certs",
    "fake_email_cfg",
    "insert_run",
    "iso_utc",
    "local_moment",
    "local_wecom_config",
    "m3",
    "m4",
    "rss_entries",
    "write_json",
]
