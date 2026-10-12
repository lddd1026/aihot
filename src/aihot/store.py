"""SQLite 存储：条目落库、幂等去重、运行记录。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from aihot.models import (
    Cluster,
    Digest,
    NewsItem,
    RawItem,
    RunRecord,
    news_item_from_dict,
    news_item_to_dict,
)
from aihot.utils import parse_datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash      TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    published_at  TEXT,
    raw_summary   TEXT NOT NULL DEFAULT '',
    lang          TEXT NOT NULL DEFAULT '',
    extra         TEXT NOT NULL DEFAULT '{}',
    fetched_at    TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    cluster_id    INTEGER,
    category      TEXT,
    importance    INTEGER,
    cn_title      TEXT,
    cn_summary    TEXT,
    enriched_at   TEXT,
    UNIQUE (source_id, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_items_url_hash ON items (url_hash);
CREATE INDEX IF NOT EXISTS idx_items_published ON items (published_at);
CREATE INDEX IF NOT EXISTS idx_items_source ON items (source_id);

CREATE TABLE IF NOT EXISTS clusters (
    cluster_id      INTEGER PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    item_hashes     TEXT NOT NULL DEFAULT '[]',
    source_ids      TEXT NOT NULL DEFAULT '[]',
    mention_count   INTEGER NOT NULL DEFAULT 1,
    heat_score      REAL NOT NULL DEFAULT 0,
    first_seen      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digests (
    digest_date TEXT PRIMARY KEY,
    summary     TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL DEFAULT '{}',
    image_path  TEXT NOT NULL DEFAULT '',
    md_path     TEXT NOT NULL DEFAULT '',
    html_path   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    stage_stats TEXT NOT NULL DEFAULT '{}',
    error       TEXT NOT NULL DEFAULT ''
);
"""

_CHUNK_SIZE = 400


def _iso(value: datetime | None) -> str | None:
    """序列化为 UTC ISO8601；naive 时间按 UTC 解释（避免本机时区偏移）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _chunks(values: list[str], size: int = _CHUNK_SIZE) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


class Store:
    """SQLite 封装。所有写入都是幂等的（INSERT OR IGNORE）。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self.init_schema()

    # ---------- 生命周期 ----------

    def init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------- 写入 ----------

    def save_items(self, items: Sequence[RawItem]) -> int:
        """批量写入，返回实际新增条数（重复条目自动忽略）。"""
        if not items:
            return 0
        now = _iso(datetime.now(UTC))
        rows = [
            (
                item.url_hash,
                item.source_id,
                item.title,
                item.url,
                _iso(item.published_at),
                item.raw_summary,
                item.lang,
                json.dumps(item.extra, ensure_ascii=False),
                _iso(item.fetched_at),
                now,
            )
            for item in items
        ]
        before = self._conn.total_changes
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO items
                    (url_hash, source_id, title, url, published_at,
                     raw_summary, lang, extra, fetched_at, first_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return self._conn.total_changes - before

    def log_run(self, record: RunRecord) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO runs
                    (run_id, started_at, finished_at, status, stage_stats, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    _iso(record.started_at),
                    _iso(record.finished_at),
                    record.status,
                    json.dumps(record.stage_stats, ensure_ascii=False),
                    record.error,
                ),
            )

    # ---------- 查询 ----------

    def is_known(self, source_id: str, url_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM items WHERE source_id = ? AND url_hash = ? LIMIT 1",
            (source_id, url_hash),
        ).fetchone()
        return row is not None

    def filter_new(self, items: Sequence[RawItem]) -> list[RawItem]:
        """过滤掉库内已存在的条目，并对同一批内的重复去重（保持原顺序）。"""
        if not items:
            return []
        seen: set[tuple[str, str]] = set()
        unique: list[RawItem] = []
        for item in items:
            key = (item.source_id, item.url_hash)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        known = self._known_keys([item.url_hash for item in unique])
        return [item for item in unique if (item.source_id, item.url_hash) not in known]

    def _known_keys(self, url_hashes: Sequence[str]) -> set[tuple[str, str]]:
        unique_hashes = list(dict.fromkeys(url_hashes))
        known: set[tuple[str, str]] = set()
        for chunk in _chunks(unique_hashes):
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT source_id, url_hash FROM items WHERE url_hash IN ({placeholders})",
                chunk,
            ).fetchall()
            known.update((row["source_id"], row["url_hash"]) for row in rows)
        return known

    def total_items(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
        return int(row["n"]) if row else 0

    def stats_by_source(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT source_id, COUNT(*) AS n FROM items GROUP BY source_id ORDER BY source_id"
        ).fetchall()
        return {row["source_id"]: int(row["n"]) for row in rows}

    def recent_items(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT source_id, title, url, published_at FROM items "
            "ORDER BY published_at DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (max(1, limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    # ---------- 加工阶段（M2） ----------

    def unenriched_items(self, limit: int | None = None) -> list[RawItem]:
        """取尚未加工的条目（发布时间倒序）。"""
        sql = "SELECT * FROM items WHERE enriched_at IS NULL ORDER BY published_at DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (max(1, int(limit)),)
        rows = self._conn.execute(sql, params).fetchall()
        items: list[RawItem] = []
        for row in rows:
            item = _row_to_raw_item(row)
            if item is not None:
                items.append(item)
        return items

    def mark_enriched(self, rows: Sequence[tuple[Any, ...]]) -> int:
        """回写加工结果。

        rows 元素顺序：
        (source_id, url_hash, cluster_id, category, importance, cn_title, cn_summary, extra_json)
        extra_json 用于持久化跨源信息（mention_count / source_ids / links），
        这样"从库里重建日报"与"用内存结果"完全等价。
        """
        if not rows:
            return 0
        now = _iso(datetime.now(UTC))
        payload = [
            (
                cluster_id,
                category,
                importance,
                cn_title,
                cn_summary,
                extra_json,
                now,
                source_id,
                url_hash,
            )
            for (
                source_id,
                url_hash,
                cluster_id,
                category,
                importance,
                cn_title,
                cn_summary,
                extra_json,
            ) in rows
        ]
        before = self._conn.total_changes
        with self._conn:
            self._conn.executemany(
                """
                UPDATE items
                   SET cluster_id = ?, category = ?, importance = ?,
                       cn_title = ?, cn_summary = ?, extra = ?, enriched_at = ?
                 WHERE source_id = ? AND url_hash = ?
                """,
                payload,
            )
        return self._conn.total_changes - before

    def recent_enriched_items(self, since: datetime, limit: int = 500) -> list[NewsItem]:
        """取窗口内已加工的条目（用于组装日报，不依赖本轮内存结果）。"""
        rows = self._conn.execute(
            """
            SELECT * FROM items
             WHERE enriched_at IS NOT NULL
               AND category <> '过滤'
               AND published_at IS NOT NULL
               AND published_at >= ?
             ORDER BY published_at DESC
             LIMIT ?
            """,
            (_iso(since), max(1, int(limit))),
        ).fetchall()
        items: list[NewsItem] = []
        for row in rows:
            item = _row_to_news_item(row)
            if item is not None:
                items.append(item)
        return items

    def mark_filtered(self, rows: Sequence[tuple[str, str]]) -> int:
        """把预筛淘汰的条目标记为已处理，避免每天重复进入队列。

        rows: (source_id, url_hash)。记录 category='过滤' 以便日后审计。
        """
        if not rows:
            return 0
        now = _iso(datetime.now(UTC))
        payload = [(now, source_id, url_hash) for source_id, url_hash in rows]
        before = self._conn.total_changes
        with self._conn:
            self._conn.executemany(
                """
                UPDATE items
                   SET category = '过滤', importance = 0, cn_title = '', cn_summary = '',
                       enriched_at = ?
                 WHERE source_id = ? AND url_hash = ?
                """,
                payload,
            )
        return self._conn.total_changes - before

    def save_clusters(self, clusters: Sequence[Cluster]) -> int:
        """写入/覆盖事件簇。"""
        if not clusters:
            return 0
        payload = [
            (
                cluster.cluster_id,
                cluster.canonical_title,
                json.dumps(cluster.item_hashes, ensure_ascii=False),
                json.dumps(cluster.source_ids, ensure_ascii=False),
                cluster.mention_count,
                cluster.heat_score,
                _iso(cluster.first_seen),
            )
            for cluster in clusters
        ]
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO clusters
                    (cluster_id, canonical_title, item_hashes, source_ids,
                     mention_count, heat_score, first_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        return len(clusters)

    def next_cluster_id(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(cluster_id), 0) + 1 AS next_id FROM clusters"
        ).fetchone()
        return int(row["next_id"]) if row else 1

    def enriched_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM items WHERE enriched_at IS NOT NULL"
        ).fetchone()
        return int(row["n"]) if row else 0

    # ---------- 日报存档（M3） ----------

    def runs_since(self, since: datetime) -> list[dict[str, Any]]:
        """取某时间之后的运行记录（SLO 报告用）。"""
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE started_at >= ? ORDER BY started_at",
            (_iso(since),),
        ).fetchall()
        return [dict(row) for row in rows]

    def digest_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM digests").fetchone()
        return int(row["n"]) if row else 0

    def save_digest(self, digest: Digest) -> None:
        """保存/覆盖一期日报（payload 存完整条目，便于预览与重发）。"""
        payload = {
            "summary": digest.summary,
            "stats": digest.stats,
            "top_items": [news_item_to_dict(item) for item in digest.top_items],
            "briefs": [news_item_to_dict(item) for item in digest.briefs],
            "files": digest.files,
        }
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO digests
                    (digest_date, summary, payload, image_path, md_path, html_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest.digest_date.isoformat(),
                    digest.summary,
                    json.dumps(payload, ensure_ascii=False),
                    digest.files.get("image", ""),
                    digest.files.get("markdown", ""),
                    digest.files.get("html", ""),
                    _iso(datetime.now(UTC)),
                ),
            )

    def load_digest(self, digest_date: date) -> Digest | None:
        """按日期读取日报存档。"""
        row = self._conn.execute(
            "SELECT * FROM digests WHERE digest_date = ?", (digest_date.isoformat(),)
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        files = dict(payload.get("files") or {})
        if row["image_path"]:
            files.setdefault("image", row["image_path"])
        if row["md_path"]:
            files.setdefault("markdown", row["md_path"])
        return Digest(
            digest_date=digest_date,
            summary=str(payload.get("summary") or row["summary"] or ""),
            top_items=[
                news_item_from_dict(item) for item in (payload.get("top_items") or [])
            ],
            briefs=[news_item_from_dict(item) for item in (payload.get("briefs") or [])],
            stats=dict(payload.get("stats") or {}),
            files=files,
        )

    def digest_dates(self, limit: int = 30) -> list[str]:
        rows = self._conn.execute(
            "SELECT digest_date FROM digests ORDER BY digest_date DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [row["digest_date"] for row in rows]


def _row_to_raw_item(row: sqlite3.Row) -> RawItem | None:
    """数据库行 → RawItem（时间缺失或无法解析时返回 None）。"""
    published = parse_datetime(row["published_at"])
    if published is None:
        return None
    return RawItem(
        source_id=row["source_id"],
        title=row["title"],
        url=row["url"],
        published_at=published,
        raw_summary=row["raw_summary"] or "",
        lang=row["lang"] or "",
        extra=_row_extra(row),
    )


def _row_extra(row: sqlite3.Row) -> dict[str, Any]:
    try:
        extra = json.loads(row["extra"] or "{}")
    except json.JSONDecodeError:
        extra = {}
    return extra if isinstance(extra, dict) else {}


def _row_to_news_item(row: sqlite3.Row) -> NewsItem | None:
    """数据库行 → 已加工的 NewsItem。"""
    raw = _row_to_raw_item(row)
    if raw is None:
        return None
    return NewsItem(
        source_id=raw.source_id,
        title=raw.title,
        url=raw.url,
        published_at=raw.published_at,
        raw_summary=raw.raw_summary,
        lang=raw.lang,
        extra=raw.extra,
        cluster_id=row["cluster_id"],
        category=row["category"] or "其他",
        importance=int(row["importance"] or 0),
        cn_title=row["cn_title"] or "",
        cn_summary=row["cn_summary"] or "",
    )
