"""M1 独立测试支撑模块（本地 HTTP 服务 / 临时配置 / CLI 执行 / 证据留存）。

设计约束：
- 不导入被测产品代码（黑盒优先），只通过 CLI 与 SQLite 文件观察行为。
- 一切写入落在系统临时目录或 reports/evidence/m1，绝不触碰真实 data/aihot.db。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import yaml

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

PROJECT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
EVIDENCE_ROOT = os.environ.get("AIHOT_M1_EVIDENCE", "m1")
EVIDENCE = PROJECT / "reports" / "evidence" / EVIDENCE_ROOT
ROUND1 = PROJECT / "reports" / "evidence" / "m1"
TEMPROOT = Path(os.environ.get("TEMP", str(Path.home()))) / "aihot-m1-tests"

RouteFn = Callable[[dict[str, list[str]]], "Payload"]


# --------------------------------------------------------------------------- HTTP 服务


@dataclass
class Payload:
    status: int = 200
    body: bytes | None = b""
    content_type: str = "application/rss+xml; charset=utf-8"


class LocalServer:
    """本地线程 HTTP 服务：可注册任意路由，用于模拟好源/坏源/超大源/慢源。

    body 为 None 表示"不发响应直接断开连接"，用于模拟协议错误。
    """

    def __init__(self) -> None:
        self.routes: dict[str, RouteFn] = {}
        self.hits: dict[str, int] = {}
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._build_handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str) -> str:
        return f"{self.base}{path}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
                parsed = urlsplit(self.path)
                server.hits[parsed.path] = server.hits.get(parsed.path, 0) + 1
                route = server.routes.get(parsed.path)
                if route is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    payload = route(parse_qs(parsed.query))
                except Exception:  # noqa: BLE001 - 测试服务兜底
                    self.send_response(500)
                    self.end_headers()
                    return
                if payload.body is None:
                    self.connection.close()
                    return
                self.send_response(payload.status)
                self.send_header("Content-Type", payload.content_type)
                self.send_header("Content-Length", str(len(payload.body)))
                self.end_headers()
                self.wfile.write(payload.body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


# --------------------------------------------------------------------------- RSS 构造


def rss_xml(entries: list[dict[str, Any]], *, title: str = "test feed") -> bytes:
    """构造 RSS 2.0 文本；entry 支持 title/link/date/summary/raw_date。"""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        f"<title>{title}</title><link>http://example.com/</link>",
    ]
    for entry in entries:
        parts.append("<item>")
        if entry.get("title") is not None:
            parts.append(f"<title>{entry['title']}</title>")
        if entry.get("link") is not None:
            parts.append(f"<link>{entry['link']}</link>")
        if entry.get("raw_date") is not None:
            parts.append(f"<pubDate>{entry['raw_date']}</pubDate>")
        elif entry.get("date") is not None:
            value = entry["date"]
            stamp = value if isinstance(value, str) else format_datetime(value)
            parts.append(f"<pubDate>{stamp}</pubDate>")
        if entry.get("summary") is not None:
            parts.append(f"<description><![CDATA[{entry['summary']}]]></description>")
        parts.append("</item>")
    parts.append("</channel></rss>")
    return "\n".join(parts).encode("utf-8")


def ago(**kwargs: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kwargs)


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


def write_config(
    workspace: Path,
    sources: list[dict[str, Any]],
    *,
    window_hours: int = 24,
    filename: str = "config.yaml",
    data_dir: Path | None = None,
    log_dir: Path | None = None,
    app_extra: dict[str, Any] | None = None,
    llm_base_url: str = "https://api.deepseek.com",
) -> Path:
    """写出最小可用配置，data/log 均落在临时目录。"""
    workspace.mkdir(parents=True, exist_ok=True)
    app: dict[str, Any] = {
        "timezone": "Asia/Shanghai",
        "window_hours": window_hours,
        "top_n": 12,
        "log_level": "INFO",
        "data_dir": str(data_dir or (workspace / "data")),
        "log_dir": str(log_dir or (workspace / "logs")),
    }
    if app_extra:
        app.update(app_extra)
    raw = {
        "app": app,
        "llm": {
            "provider": "deepseek",
            "base_url": llm_base_url,
            "model": "deepseek-chat",
            "batch_size": 15,
            "daily_cost_limit_cny": 1.0,
        },
        "proxy": {"enabled": False, "url": ""},
        "push": {"wecom": {"enabled": False}, "email": {"enabled": False, "to_addrs": []}},
        "sources": sources,
    }
    path = workspace / filename
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def source(
    source_id: str,
    url: str,
    *,
    name: str | None = None,
    type_: str = "rss",
    enabled: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": source_id,
        "name": name or source_id,
        "type": type_,
        "url": url,
        "enabled": enabled,
        "weight": 1.0,
    }
    if extra is not None:
        item["extra"] = extra
    return item


def run_cli(args: list[str], *, timeout: float = 240.0, cwd: Path | None = None) -> CliResult:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    command = f'python -m aihot {" ".join(args)}'
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(PYTHON), "-m", "aihot", *args],
            cwd=str(cwd or PROJECT),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        seconds = time.perf_counter() - start
        return CliResult(
            rc=-999,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr) + f"\n[harness] 超时 {timeout}s",
            seconds=seconds,
            command=command,
        )
    return CliResult(
        rc=proc.returncode,
        stdout=_decode(proc.stdout),
        stderr=_decode(proc.stderr),
        seconds=time.perf_counter() - start,
        command=command,
    )


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", "replace")


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


def db_counts(db_path: Path) -> dict[str, int]:
    """items 总数 / 唯一键数 / runs 数；库不存在时全为 0。"""
    path = Path(db_path)
    if not path.exists():
        return {"exists": 0, "items": 0, "unique_keys": 0, "runs": 0}
    rows = db_query(
        path,
        "SELECT COUNT(*) AS items, COUNT(DISTINCT source_id || '|' || url_hash) AS unique_keys "
        "FROM items",
    )
    runs = db_query(path, "SELECT COUNT(*) AS n FROM runs")
    return {
        "exists": 1,
        "items": rows[0]["items"],
        "unique_keys": rows[0]["unique_keys"],
        "runs": runs[0]["n"],
    }


def table_names(db_path: Path) -> list[str]:
    rows = db_query(db_path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [row["name"] for row in rows]


def titles(db_path: Path) -> list[str]:
    return [row["title"] for row in db_query(db_path, "SELECT title FROM items ORDER BY id")]


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
