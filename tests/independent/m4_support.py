"""M4 独立测试支撑模块：在 M3 假服务基础上补充 M4 专用工具。

设计约束：
- 复用 tests/independent/m3_support.py 的假 LLM / 假企微 / 假 RSS / Suite / 真实库快照；
  证据统一落到 reports/evidence/m4。
- 一切写入落在 %TEMP%/aihot-m3-tests/m4-*，绝不触碰真实 data/aihot.db、
  data/digests 与 config.yaml。
- 真实企业微信 webhook / 真实邮箱绝不使用；真实 LLM 不用（M4 全链路用假 LLM）。
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import yaml

# 证据根目录：默认 reports/evidence/m4；第 2 轮回归设置 AIHOT_M4_EVIDENCE=m4/regression2
EVIDENCE_ROOT = os.environ.get("AIHOT_M4_EVIDENCE", "m4")
os.environ["AIHOT_M3_EVIDENCE"] = EVIDENCE_ROOT

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as _m3_support  # noqa: E402
from m3_support import (  # noqa: E402
    PROJECT,
    PYTHON,
    TEMPROOT,
    FakeRSS,
    FakeWeCom,
    Suite,
    db_one,
    db_query,
    default_llm_generator,
    real_state_snapshot,
    write_config,
)

# 强制证据根目录：m3_support 可能在 import 顺序上已被先加载
EVIDENCE = PROJECT / "reports" / "evidence" / EVIDENCE_ROOT
_m3_support.EVIDENCE = EVIDENCE

REAL_CONFIG = PROJECT / "config.yaml"
RAW = EVIDENCE / "raw"


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", "replace")


# ----------------------------------------------------------------- 假 LLM（带 /models 探测）


class FakeLLMProbe:
    """OpenAI 兼容假服务：POST /v1/chat/completions + GET /v1/models（预检探测用）。

    modes: ok / http401 / http502 / empty
    """

    def __init__(self, generator: Callable[[dict[str, Any] | None], str] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.mode = "ok"
        self.generator = generator or default_llm_generator
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def request_count(self) -> int:
        return len([r for r in self.requests if r["method"] == "POST"])

    @property
    def models_probes(self) -> int:
        return len([r for r in self.requests if r["path"].endswith("/models")])

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> FakeLLMProbe:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, status: int, data: bytes, ctype: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                server.requests.append(
                    {"method": "GET", "path": self.path, "body": None, "at": time.time()}
                )
                if not self.path.endswith("/models"):
                    self._send(404, b"not found", "text/plain")
                    return
                if server.mode == "http401":
                    self._send(401, b'{"error":"invalid api key"}')
                    return
                if server.mode == "http502":
                    self._send(502, b"bad gateway", "text/plain")
                    return
                data = json.dumps(
                    {"object": "list", "data": [{"id": "fake-model", "object": "model"}]}
                ).encode()
                self._send(200, data)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    body = None
                server.requests.append(
                    {"method": "POST", "path": self.path, "body": body, "at": time.time()}
                )
                if server.mode == "http401":
                    self._send(401, b'{"error":"invalid api key"}')
                    return
                if server.mode == "http502":
                    self._send(502, b"bad gateway", "text/plain")
                    return
                content = server.generator(body)
                payload = {
                    "id": "fake",
                    "object": "chat.completion",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                }
                self._send(200, json.dumps(payload).encode("utf-8"))

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


# ------------------------------------------------------------------ 通用假 HTTP（适配器测试）


class FakeWeComProbe(FakeWeCom):
    """FakeWeCom 扩展：让 GET（预检探测）也遵循 mode，用于验证 check --quick 的企微分支。

    ok → 200 {"errcode":0}；errcode → 200 {"errcode":93000}；http502 → 502；drop → 断开。
    """

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        base = super()._handler()

        class Handler(base):  # type: ignore[misc, valid-type]
            def do_GET(self) -> None:  # noqa: N802
                mode = server.mode
                if mode == "drop":
                    self.close_connection = True
                    with contextlib.suppress(OSError):
                        self.connection.close()
                    return
                if mode == "http502":
                    data = b"bad gateway"
                    self.send_response(502)
                    self.send_header("Content-Type", "text/plain")
                elif mode == "errcode":
                    data = json.dumps({"errcode": 93000, "errmsg": "invalid webhook url"}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                elif mode == "errcode_other":
                    data = json.dumps(
                        {"errcode": 93017, "errmsg": "invalid json request"}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                elif mode == "nonjson":
                    data = b"<html>gateway ok</html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                elif mode == "json_array":
                    data = json.dumps([1, 2, 3]).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                else:
                    data = json.dumps({"errcode": 0, "errmsg": "ok"}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = self
        return Handler


class FakeHTTP:
    """通用路由假 HTTP 服务：按 path 返回预设响应，并记录 query/headers。"""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, bytes, str]] = {}
        self.requests: list[dict[str, Any]] = []
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def add(self, path: str, status: int, body: bytes | str, ctype: str = "text/html") -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.routes[path] = (status, data, ctype)

    def add_json(self, path: str, payload: Any) -> None:
        self.add(
            path, 200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json"
        )

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> FakeHTTP:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                parts = urlsplit(self.path)
                server.requests.append(
                    {
                        "path": parts.path,
                        "query": {k: v[0] for k, v in parse_qs(parts.query).items()},
                        "headers": dict(self.headers),
                        "at": time.time(),
                    }
                )
                route = server.routes.get(parts.path)
                if route is None:
                    data = b"not found"
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                else:
                    status, data, ctype = route
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


# --------------------------------------------------------------------------- 真实配置的脱敏副本


def real_raw() -> dict[str, Any]:
    """读取真实 config.yaml（仅用于构造测试配置；不写入证据）。"""
    return yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))


def sanitized_real_raw(*, api_key: str = "sk-m4-fake-000000000000") -> dict[str, Any]:
    """真实配置的脱敏副本：真实密钥全部替换为假值，邮件默认关闭。"""
    raw = copy.deepcopy(real_raw())
    llm = raw.setdefault("llm", {})
    llm["api_key"] = api_key
    llm.pop("api_key_env", None)
    push = raw.setdefault("push", {})
    wecom = push.setdefault("wecom", {})
    wecom["webhook"] = "http://127.0.0.1:1/fake-webhook"
    wecom.pop("webhook_env", None)
    email = push.setdefault("email", {})
    email["enabled"] = False
    email["password"] = "fake-auth-code"
    email.pop("password_env", None)
    return raw


def dump_config(path: Path, raw: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def make_m4_config(
    path: Path,
    *,
    llm_base_url: str,
    wecom_webhook: str = "",
    data_dir: Path,
    log_dir: Path,
    sources: list[dict[str, Any]] | None = None,
    wecom_enabled: bool = True,
    email_enabled: bool = False,
    proxy_enabled: bool = False,
    top_n: int = 12,
    window_hours: int = 24,
) -> Path:
    """M4 常用假配置（与 m3_support.write_config 同构，企微默认开启指向假服务）。"""
    return write_config(
        path,
        data_dir=data_dir,
        log_dir=log_dir,
        sources=sources,
        llm_base_url=llm_base_url,
        wecom_enabled=wecom_enabled,
        wecom_webhook=wecom_webhook,
        email_enabled=email_enabled,
        top_n=top_n,
        window_hours=window_hours,
    )


# --------------------------------------------------------------------------- bat 链路工具


def stage_bat(name: str, cfg_raw: dict[str, Any], *, copy_real_db: bool = False) -> Path:
    """在临时目录搭一套与真实根目录等价的 bat 运行环境（run_daily.bat 字节复制 + .venv 联接）。"""
    root = TEMPROOT / "m4-bat" / name
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT / "run_daily.bat", root / "run_daily.bat")
    junction = root / ".venv"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(PROJECT / ".venv")],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"创建 .venv 联接失败: {_decode(result.stdout)}{_decode(result.stderr)}")
    dump_config(root / "config.yaml", cfg_raw)
    if copy_real_db:
        (root / "data").mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / "data" / "aihot.db", root / "data" / "aihot.db")
    return root


def run_bat(
    root: Path,
    args: list[str] | None = None,
    *,
    timeout: float = 900.0,
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """执行 run_daily.bat（核心工作目录 = root），返回退出码与输出。"""
    argv = ["cmd", "/c", str(root / "run_daily.bat"), *(args or [])]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    start = time.perf_counter()
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, timeout=timeout, env=env)
        rc: int | str = proc.returncode
        out, err = _decode(proc.stdout), _decode(proc.stderr)
    except subprocess.TimeoutExpired as exc:
        rc = "timeout"
        out, err = _decode(exc.stdout), _decode(exc.stderr) + f"\n[harness] 超时 {timeout}s"
    return {
        "argv": " ".join(argv),
        "root": str(root),
        "rc": rc,
        "seconds": round(time.perf_counter() - start, 1),
        "stdout": out,
        "stderr": err,
        "log": (root / "logs" / "run_daily.log").read_text(encoding="utf-8", errors="replace")
        if (root / "logs" / "run_daily.log").exists()
        else "",
    }


def bat_sha256() -> str:
    from m3_support import file_sha256

    return file_sha256(PROJECT / "run_daily.bat")


# --------------------------------------------------------------------------- 实时抓取 / CLI


def run_python(args: list[str], *, timeout: float = 600.0) -> dict[str, Any]:
    """在项目根目录跑 `.venv\\Scripts\\python.exe args`（用于真实网络抓取）。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(PYTHON), *args], cwd=str(PROJECT), capture_output=True, timeout=timeout, env=env
        )
        rc: int | str = proc.returncode
        out, err = _decode(proc.stdout), _decode(proc.stderr)
    except subprocess.TimeoutExpired as exc:
        rc = "timeout"
        out, err = _decode(exc.stdout), _decode(exc.stderr) + f"\n[harness] 超时 {timeout}s"
    return {
        "command": f"python {' '.join(args)}",
        "rc": rc,
        "seconds": round(time.perf_counter() - start, 1),
        "stdout": out,
        "stderr": err,
    }


FETCH_LINE = re.compile(r"^\s*\[(OK |ERR)\]\s+(\S+)\s+(.*?)（(\d+) ms）\s*$", re.MULTILINE)


def parse_fetch_lines(text: str) -> list[dict[str, Any]]:
    rows = []
    for mark, source_id, detail, ms in FETCH_LINE.findall(text):
        rows.append(
            {"ok": mark.strip() == "OK", "source_id": source_id, "detail": detail, "ms": int(ms)}
        )
    return rows


def write_raw(name: str, text: str) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / name
    path.write_text(text, encoding="utf-8")
    return path


def evidence_json(name: str, data: Any) -> Path:
    return write_raw(name, json.dumps(data, ensure_ascii=False, indent=2))


__all__ = [
    "PROJECT",
    "PYTHON",
    "RAW",
    "TEMPROOT",
    "FakeHTTP",
    "FakeLLMProbe",
    "FakeRSS",
    "FakeWeCom",
    "Suite",
    "bat_sha256",
    "db_one",
    "db_query",
    "dump_config",
    "evidence_json",
    "make_m4_config",
    "parse_fetch_lines",
    "real_raw",
    "real_state_snapshot",
    "run_bat",
    "run_python",
    "sanitized_real_raw",
    "stage_bat",
    "write_config",
    "write_raw",
]
