"""runner 编排单元测试（不触网、不调用 LLM）。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from aihot import runner
from aihot.config import Config, load_config
from aihot.models import Digest, NewsItem, RawItem
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
push:
  wecom:
    enabled: false
  email:
    enabled: false
sources:
  - id: s1
    type: rss
    url: https://example.com/feed
    enabled: true
    weight: 1.0
"""


def load_cfg(tmp_path: Path) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG_TEMPLATE.format(
            data_dir=(tmp_path / "data").as_posix(),
            log_dir=(tmp_path / "logs").as_posix(),
        ),
        encoding="utf-8",
    )
    return load_config(path)


def make_news(
    index: int,
    *,
    category: str = "模型发布",
    importance: int = 80,
    published: datetime | None = None,
    source_id: str = "s1",
) -> NewsItem:
    return NewsItem(
        source_id=source_id,
        title=f"Title {index}",
        url=f"https://example.com/{index}",
        published_at=published or datetime.now(UTC) - timedelta(hours=1),
        cluster_id=index,
        category=category,
        importance=importance,
        cn_title=f"中文标题 {index}",
        cn_summary=f"摘要 {index}",
        extra={"mention_count": 1, "source_ids": [source_id]},
    )


def to_raw(item: NewsItem) -> RawItem:
    return RawItem(
        source_id=item.source_id,
        title=item.title,
        url=item.url,
        published_at=item.published_at,
        raw_summary=item.raw_summary,
        lang=item.lang,
        extra=item.extra,
    )


def store_enriched(cfg: Config, items: list[NewsItem]) -> None:
    """把已加工条目直接写进库（跳过采集与 LLM）。"""
    with Store(cfg.app.data_dir / "aihot.db") as store:
        raw_items = [to_raw(item) for item in items]
        store.save_items(raw_items)
        rows = [
            (
                raw.source_id,
                raw.url_hash,
                item.cluster_id,
                item.category,
                item.importance,
                item.cn_title,
                item.cn_summary,
                "{}",
            )
            for raw, item in zip(raw_items, items, strict=True)
        ]
        store.mark_enriched(rows)


def test_fetch_dry_run_does_not_create_db(tmp_path: Path, monkeypatch) -> None:
    """P2 回归：dry-run 不应在磁盘上创建空库文件。"""
    cfg = load_cfg(tmp_path)
    monkeypatch.setattr(
        runner, "collect_all", lambda adapters, since: CollectResult(items=[], stats=[])
    )
    outcome = runner.fetch_stage(cfg, dry_run=True)
    assert outcome.saved == 0
    assert outcome.db_total == 0
    assert not (cfg.app.data_dir / "aihot.db").exists()


def test_build_digest_from_db_excludes_noise(tmp_path: Path) -> None:
    cfg = load_cfg(tmp_path)
    store_enriched(
        cfg,
        [
            make_news(1),
            make_news(2, category="其他", importance=10),  # 噪音：应被排除
            make_news(3, category="过滤", importance=0),  # 已过滤：不应出现
        ],
    )
    digest = runner.build_digest_from_db(cfg, digest_date=date(2026, 9, 24), top_n=5)
    assert [item.cn_title for item in digest.top_items] == ["中文标题 1"]


def test_build_digest_from_db_respects_top_n_and_briefs(tmp_path: Path) -> None:
    cfg = load_cfg(tmp_path)
    store_enriched(cfg, [make_news(index) for index in range(1, 8)])
    digest = runner.build_digest_from_db(cfg, digest_date=date(2026, 9, 24), top_n=5)
    assert len(digest.top_items) == 5
    assert len(digest.briefs) == 2


def test_render_outputs_without_image(tmp_path: Path) -> None:
    cfg = load_cfg(tmp_path)
    store_enriched(cfg, [make_news(1)])
    digest = Digest(
        digest_date=date(2026, 9, 24),
        summary="综述",
        top_items=[make_news(1)],
        briefs=[],
        stats={"scanned": 10, "clusters": 3, "selected": 1},
    )
    rendered = runner.render_outputs(cfg, digest, with_image=False)
    assert rendered.image_paths == []
    assert rendered.wecom_parts
    assert Path(rendered.files["markdown"]).exists()
    assert Path(rendered.files["html"]).exists()
    # 日报已存档，可被 preview/resend 复用
    loaded = runner.load_digest(cfg, date(2026, 9, 24))
    assert loaded.summary == "综述"
    assert len(loaded.top_items) == 1
