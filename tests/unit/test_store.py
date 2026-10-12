"""store 模块单元测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from aihot.models import Cluster, RawItem, RunRecord
from aihot.store import Store


def make_item(url: str, source_id: str = "s1", title: str = "标题") -> RawItem:
    return RawItem(
        source_id=source_id,
        title=title,
        url=url,
        published_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
        raw_summary="摘要",
        lang="zh",
        extra={"points": 10},
    )


def test_save_and_count(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        assert store.save_items([make_item("https://a.com/1")]) == 1
        assert store.total_items() == 1
        assert store.is_known("s1", make_item("https://a.com/1").url_hash)


def test_save_is_idempotent(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        items = [make_item("https://a.com/1")]
        assert store.save_items(items) == 1
        assert store.save_items(items) == 0
        assert store.total_items() == 1


def test_filter_new_removes_known_and_batch_duplicates(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        store.save_items([make_item("https://a.com/1")])
        candidates = [
            make_item("https://a.com/1"),  # 库内已有
            make_item("https://a.com/2"),
            make_item("https://a.com/2"),  # 批内重复
            make_item("https://a.com/3", source_id="s2"),  # 同 URL 不同源 → 保留
        ]
        new_items = store.filter_new(candidates)
        assert [item.url for item in new_items] == ["https://a.com/2", "https://a.com/3"]


def test_same_url_different_sources_are_distinct(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        store.save_items([make_item("https://a.com/1", source_id="s1")])
        assert store.save_items([make_item("https://a.com/1", source_id="s2")]) == 1
        assert store.total_items() == 2


def test_tracking_params_do_not_create_duplicates(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        store.save_items([make_item("https://a.com/1")])
        assert store.save_items([make_item("https://a.com/1?utm_source=rss")]) == 0


def test_stats_and_recent_items(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        store.save_items(
            [make_item("https://a.com/1"), make_item("https://a.com/2", source_id="s2")]
        )
        assert store.stats_by_source() == {"s1": 1, "s2": 1}
        recent = store.recent_items(limit=5)
        assert len(recent) == 2
        assert {"source_id", "title", "url", "published_at"} <= set(recent[0])


def test_log_run_and_read_back(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        now = datetime.now(UTC)
        store.log_run(
            RunRecord(
                run_id="r1",
                started_at=now,
                finished_at=now,
                status="ok",
                stage_stats={"fetched": 3},
            )
        )
        runs = store.recent_runs()
        assert runs[0]["run_id"] == "r1"
        assert runs[0]["status"] == "ok"


def test_schema_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    with Store(path) as store:
        store.save_items([make_item("https://a.com/1")])
    with Store(path) as store:  # 重新打开不应报错，也不应丢数据
        assert store.total_items() == 1


def test_all_tables_exist(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {row["name"] for row in rows}
        assert {"items", "clusters", "digests", "runs"} <= names


def test_naive_datetime_treated_as_utc(tmp_path: Path) -> None:
    item = make_item("https://a.com/naive")
    item.published_at = datetime(2026, 1, 1, 0, 0, 0)  # naive：应按 UTC 解释
    with Store(tmp_path / "db.sqlite") as store:
        store.save_items([item])
        row = store.recent_items(1)[0]
    assert row["published_at"].startswith("2026-01-01T00:00:00")


# ---------- M2：加工阶段读写 ----------


def test_unenriched_items_and_mark_enriched(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        items = [make_item("https://a.com/1"), make_item("https://a.com/2", source_id="s2")]
        store.save_items(items)
        pending = store.unenriched_items()
        assert len(pending) == 2
        assert store.enriched_count() == 0

        rows = [
            (
                item.source_id,
                item.url_hash,
                1,
                "模型发布",
                88,
                "中文标题",
                "中文摘要",
                json.dumps({"mention_count": 3}, ensure_ascii=False),
            )
            for item in pending
        ]
        assert store.mark_enriched(rows) == 2
        assert store.enriched_count() == 2
        assert store.unenriched_items() == []

        row = store.recent_items(1)[0]
        assert row["title"]  # 原始字段不受影响
        detail = store._conn.execute(  # noqa: SLF001
            "SELECT category, importance, cn_title, cn_summary, cluster_id, extra "
            "FROM items LIMIT 1"
        ).fetchone()
        assert detail["category"] == "模型发布"
        assert detail["importance"] == 88
        assert detail["cn_title"] == "中文标题"
        assert detail["cluster_id"] == 1
        assert json.loads(detail["extra"])["mention_count"] == 3


def test_unenriched_items_respects_limit(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        store.save_items([make_item(f"https://a.com/{index}") for index in range(5)])
        assert len(store.unenriched_items(limit=3)) == 3


def test_mark_filtered_drains_queue(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        items = [make_item("https://a.com/1"), make_item("https://a.com/2")]
        store.save_items(items)
        assert store.mark_filtered([(item.source_id, item.url_hash) for item in items]) == 2
        assert store.unenriched_items() == []
        assert store.enriched_count() == 2
        category = store._conn.execute("SELECT category FROM items LIMIT 1").fetchone()["category"]  # noqa: SLF001
        assert category == "过滤"


def test_save_clusters_and_next_id(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        assert store.next_cluster_id() == 1
        clusters = [
            Cluster(
                cluster_id=1,
                canonical_title="标题",
                item_hashes=["h1", "h2"],
                source_ids=["a", "b"],
                mention_count=2,
                heat_score=0.0,
                first_seen=datetime(2026, 9, 24, 8, 0, tzinfo=UTC),
            )
        ]
        assert store.save_clusters(clusters) == 1
        assert store.next_cluster_id() == 2
        row = store._conn.execute("SELECT * FROM clusters WHERE cluster_id = 1").fetchone()  # noqa: SLF001
        assert json.loads(row["item_hashes"]) == ["h1", "h2"]
        assert json.loads(row["source_ids"]) == ["a", "b"]
        assert row["mention_count"] == 2

        # 覆盖写入（同日重跑）
        clusters[0].mention_count = 5
        assert store.save_clusters(clusters) == 1
        row = store._conn.execute(  # noqa: SLF001
            "SELECT mention_count FROM clusters WHERE cluster_id = 1"
        ).fetchone()
        assert row["mention_count"] == 5


def test_row_to_raw_item_handles_bad_extra(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        store.save_items([make_item("https://a.com/1")])
        store._conn.execute("UPDATE items SET extra = '不是JSON'")  # noqa: SLF001
        store._conn.commit()
        pending = store.unenriched_items()
        assert len(pending) == 1
        assert pending[0].extra == {}
