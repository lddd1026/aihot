"""M2 独立测试支撑模块：假 LLM 服务（OpenAI 兼容）+ 临时配置/种子库 + 证据留存。

设计约束：
- 断言只基于 CLI 输出与 SQLite 文件观察；产品代码仅在「构造固定夹具」时使用
  （计算 url_hash / 复制真实库），不参与任何判定。
- 一切写入落在 `%TEMP%\\aihot-m2-tests` 与 `reports/evidence/m2`，绝不触碰真实 data/aihot.db。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

PROJECT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
EVIDENCE_ROOT = os.environ.get("AIHOT_M2_EVIDENCE", "m2")
EVIDENCE = PROJECT / "reports" / "evidence" / EVIDENCE_ROOT
TEMPROOT = Path(os.environ.get("TEMP", str(Path.home()))) / "aihot-m2-tests"
REAL_DB = PROJECT / "data" / "aihot.db"
REAL_CONFIG = PROJECT / "config.yaml"

# 仅为夹具哈希计算导入 utils（断言不依赖产品代码）
sys.path.insert(0, str(PROJECT / "src"))
from aihot.utils import normalize_url  # noqa: E402


def url_hash(url: str) -> str:
    """与产品一致的 URL 哈希（用于夹具入库）。"""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()


def workspace(name: str) -> Path:
    path = TEMPROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- 假 LLM 服务


@dataclass
class Reply:
    """一次 LLM 响应。content=None 且 raw_body=None 时返回结构异常体。"""

    status: int = 200
    content: str | None = '{"items": []}'
    usage: dict[str, int] | None = None
    delay: float = 0.0
    close: bool = False
    raw_body: bytes | None = None

    def body(self) -> bytes:
        if self.raw_body is not None:
            return self.raw_body
        usage = self.usage or {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        payload: dict[str, Any] = {"id": "fake", "object": "chat.completion", "usage": usage}
        if self.content is not None:
            payload["choices"] = [
                {"index": 0, "message": {"role": "assistant", "content": self.content}}
            ]
        else:
            payload["choices"] = []
        return json.dumps(payload).encode("utf-8")


class FakeLLM:
    """OpenAI 兼容的本地假服务：按队列返回预设响应，统计请求次数与请求体。"""

    def __init__(self, default: Reply | None = None) -> None:
        self.replies: list[Reply] = []
        self.default = default or Reply()
        self.requests: list[dict[str, Any]] = []
        self.generator: Callable[[dict[str, Any] | None], str] | None = None
        self.generator_usage: dict[str, int] | None = None
        self.generator_delay = 0.0
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._build_handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self.base = f"http://127.0.0.1:{self.port}"
        self.base_url = f"{self.base}/v1"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    # ---- 使用侧 API ----
    def enqueue(self, reply: Reply) -> None:
        self.replies.append(reply)

    def enqueue_content(self, content: str, **kwargs: Any) -> None:
        self.replies.append(Reply(content=content, **kwargs))

    def set_default(self, content: str, **kwargs: Any) -> None:
        self.default = Reply(content=content, **kwargs)

    def set_generator(
        self,
        fn: Callable[[dict[str, Any] | None], str],
        *,
        usage: dict[str, int] | None = None,
        delay: float = 0.0,
    ) -> None:
        """按请求体动态生成响应（用于批次数不确定的整批运行）。"""
        self.generator = fn
        self.generator_usage = usage
        self.generator_delay = delay

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

    # ---- HTTP 侧 ----
    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
                parsed = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    parsed_body = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    parsed_body = None
                server.requests.append(
                    {"path": parsed.path, "body": parsed_body, "at": time.time()}
                )
                if server.generator is not None:
                    reply = Reply(
                        content=server.generator(parsed_body),
                        usage=server.generator_usage,
                        delay=server.generator_delay,
                    )
                else:
                    reply = server.replies.pop(0) if server.replies else server.default
                if reply.delay:
                    time.sleep(reply.delay)
                if reply.close:
                    self.connection.close()
                    return
                body = reply.body()
                self.send_response(reply.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


# --------------------------------------------------------------------------- 夹具构造


def ago(**kwargs: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kwargs)


def seed_items(db_path: Path, items: list[dict[str, Any]]) -> int:
    """把条目写入（临时）库的 items 表，字段与产品 schema 对齐。"""
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
            (item.get("published_at") or now),
            item.get("raw_summary", ""),
            item.get("lang", ""),
            json.dumps(item.get("extra", {}), ensure_ascii=False),
            item.get("fetched_at", now),
            now,
        )
        for item in items
    ]
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO items (url_hash, source_id, title, url, published_at,"
            " raw_summary, lang, extra, fetched_at, first_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    count = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()[0]
    conn.close()
    return int(count)


def reset_enrichment(db_path: Path) -> int:
    """把库内条目全部重置为「待加工」（用于真实库副本的可重复测试）。"""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "UPDATE items SET enriched_at = NULL, cluster_id = NULL, category = NULL,"
            " importance = NULL, cn_title = NULL, cn_summary = NULL"
        )
        conn.execute("DELETE FROM clusters")
    count = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()[0]
    conn.close()
    return int(count)


def copy_real_db(destination: Path) -> Path:
    """用 SQLite backup API 复制真实库（含 WAL 中未落盘数据），只读源库。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{REAL_DB.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return destination


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


DEFAULT_LLM: dict[str, Any] = {
    "provider": "fake",
    "base_url": "http://127.0.0.1:1/v1",  # 默认指向关闭端口：任何真实调用都会失败（安全兜底）
    "model": "fake-model",
    "api_key": "sk-fake-000000000000",
    "batch_size": 5,
    "daily_cost_limit_cny": 1.0,
}

DEFAULT_SOURCES: list[dict[str, Any]] = [
    {
        "id": "hackernews",
        "name": "Hacker News",
        "type": "hackernews",
        "url": "https://hn.algolia.com/api/v1/search_by_date",
        "enabled": True,
        "weight": 0.8,
    },
    {
        "id": "openai_news",
        "name": "OpenAI News",
        "type": "rss",
        "url": "https://openai.com/news/rss.xml",
        "enabled": True,
        "weight": 1.2,
    },
    {
        "id": "qbitai",
        "name": "量子位",
        "type": "rss",
        "url": "https://www.qbitai.com/feed",
        "enabled": True,
        "weight": 1.0,
    },
]


def default_pipeline(
    *,
    gate: list[str] | None = None,
    similarity: float = 88.0,
    hours: int = 72,
    refine: bool | None = False,
    max_items: int = 200,
) -> dict[str, Any]:
    """默认 pipeline 配置段（测试用；价格与门控可覆盖）。

    refine=None 表示不写 `dedup.llm_refine` 键（用于验证产品默认值）。
    """
    dedup: dict[str, Any] = {
        "title_similarity": similarity,
        "max_hours_apart": hours,
    }
    if refine is not None:
        dedup["llm_refine"] = refine
    return {
        "prefilter": {
            "gate_sources": ["hackernews"] if gate is None else gate,
            "blacklist_keywords": [],
            "extra_ai_keywords": [],
        },
        "dedup": dedup,
        "enrich": {
            "max_items": max_items,
            "price_input_per_million": 2.0,
            "price_output_per_million": 8.0,
        },
    }


def write_config(
    path: Path,
    *,
    sources: list[dict[str, Any]] | None = None,
    pipeline: dict[str, Any] | None = None,
    llm: dict[str, Any] | None = None,
    data_dir: Path | None = None,
    log_dir: Path | None = None,
    window_hours: int = 24,
    app_extra: dict[str, Any] | None = None,
) -> Path:
    """写出最小可用配置（data/log 落在临时目录；不写真实密钥）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    base = path.parent
    app: dict[str, Any] = {
        "timezone": "Asia/Shanghai",
        "window_hours": window_hours,
        "top_n": 12,
        "log_level": "INFO",
        "data_dir": str(data_dir or (base / "data")),
        "log_dir": str(log_dir or (base / "logs")),
    }
    if app_extra:
        app.update(app_extra)
    llm_section = dict(DEFAULT_LLM)
    llm_section.update(llm or {})
    raw: dict[str, Any] = {
        "app": app,
        "llm": llm_section,
        "proxy": {"enabled": False, "url": ""},
        "push": {"wecom": {"enabled": False}, "email": {"enabled": False, "to_addrs": []}},
        "sources": DEFAULT_SOURCES if sources is None else sources,
    }
    if pipeline is not None:
        raw["pipeline"] = pipeline
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def run_cli(
    args: list[str],
    *,
    timeout: float = 600.0,
    env_extra: dict[str, str] | None = None,
) -> CliResult:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)
    command = f'python -m aihot {" ".join(args)}'
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


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", "replace")


def has_traceback(text: str) -> bool:
    return "Traceback (most recent call last)" in text


def parse_stats(stdout: str) -> dict[str, Any]:
    """解析 enrich 输出的统计行：批次 X（失败 Y）· token A+B · 成本约 ¥C。"""
    stats: dict[str, Any] = {}
    batch = re.search(r"批次 (\d+)（失败 (\d+)）", stdout)
    if batch:
        stats["batches"] = int(batch.group(1))
        stats["failed_batches"] = int(batch.group(2))
    tokens = re.search(r"token (\d+)\+(\d+)", stdout)
    if tokens:
        stats["prompt_tokens"] = int(tokens.group(1))
        stats["completion_tokens"] = int(tokens.group(2))
    cost = re.search(r"成本约 ¥([\d.]+)", stdout)
    if cost:
        stats["cost_cny"] = float(cost.group(1))
    return stats


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


def item_rows(db_path: Path, *, order: str = "id") -> list[dict[str, Any]]:
    return db_query(db_path, f"SELECT * FROM items ORDER BY {order}")


def real_db_snapshot() -> dict[str, Any]:
    """真实库只读快照（用于证明测试未改动它）。"""
    if not REAL_DB.exists():
        return {"exists": False}
    stat = REAL_DB.stat()
    return {
        "exists": True,
        "bytes": stat.st_size,
        "items": db_one(REAL_DB, "SELECT COUNT(*) FROM items"),
        "enriched": db_one(
            REAL_DB, "SELECT COUNT(*) FROM items WHERE enriched_at IS NOT NULL"
        ),
        "runs": db_one(REAL_DB, "SELECT COUNT(*) FROM runs"),
        "clusters": db_one(REAL_DB, "SELECT COUNT(*) FROM clusters"),
        "max_id": db_one(REAL_DB, "SELECT COALESCE(MAX(id),0) FROM items"),
    }


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
