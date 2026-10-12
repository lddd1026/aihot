"""信息源注册表与并发调度（单源失败隔离）。"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

from aihot.config import Config
from aihot.models import RawItem
from aihot.net import HttpClient
from aihot.sources.arxiv import ArxivAdapter
from aihot.sources.base import SourceAdapter, SourceError
from aihot.sources.github import GithubAdapter
from aihot.sources.hackernews import HackerNewsAdapter
from aihot.sources.hf_papers import HfPapersAdapter
from aihot.sources.html import HtmlAdapter
from aihot.sources.rss import RssAdapter

logger = logging.getLogger("aihot.sources")

ADAPTER_TYPES: dict[str, type[SourceAdapter]] = {
    RssAdapter.type: RssAdapter,
    HackerNewsAdapter.type: HackerNewsAdapter,
    ArxivAdapter.type: ArxivAdapter,
    HtmlAdapter.type: HtmlAdapter,
    HfPapersAdapter.type: HfPapersAdapter,
    GithubAdapter.type: GithubAdapter,
}


@dataclass(slots=True)
class SourceStat:
    """单个源的采集统计。"""

    source_id: str
    name: str = ""
    ok: bool = True
    count: int = 0
    error: str = ""
    duration_ms: int = 0


@dataclass(slots=True)
class CollectResult:
    """一次采集的汇总结果。"""

    items: list[RawItem] = field(default_factory=list)
    stats: list[SourceStat] = field(default_factory=list)

    @property
    def failed(self) -> list[SourceStat]:
        return [stat for stat in self.stats if not stat.ok]

    @property
    def ok_count(self) -> int:
        return sum(1 for stat in self.stats if stat.ok)


def build_adapters(
    cfg: Config,
    http: HttpClient,
    *,
    only: set[str] | None = None,
) -> tuple[list[SourceAdapter], list[str]]:
    """按配置构建启用的适配器，返回 (适配器列表, 跳过的源说明)。"""
    adapters: list[SourceAdapter] = []
    skipped: list[str] = []
    for source in cfg.sources:
        if not source.enabled:
            continue
        if only and source.id not in only:
            continue
        adapter_class = ADAPTER_TYPES.get(source.type)
        if adapter_class is None:
            message = f"{source.id}（类型 {source.type} 尚未实现，计划在 M4 适配）"
            skipped.append(message)
            logger.warning("跳过信息源: %s", message)
            continue
        adapters.append(adapter_class(source, http))
    return adapters, skipped


def collect_all(
    adapters: Sequence[SourceAdapter],
    since: datetime,
    *,
    max_workers: int = 5,
) -> CollectResult:
    """并发采集全部源；单源失败只记录，不中断整体。"""
    result = CollectResult()
    if not adapters:
        return result
    workers = max(1, min(max_workers, len(adapters)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, adapter, since): adapter for adapter in adapters}
        for future in as_completed(futures):
            stat, items = future.result()
            result.stats.append(stat)
            result.items.extend(items)
    result.stats.sort(key=lambda stat: stat.source_id)
    return result


def _fetch_one(adapter: SourceAdapter, since: datetime) -> tuple[SourceStat, list[RawItem]]:
    start = time.perf_counter()
    try:
        items = adapter.fetch(since)
    except Exception as exc:  # 单源失败必须隔离，不能拖垮整体
        duration_ms = int((time.perf_counter() - start) * 1000)
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("信息源失败: %s（%s）", adapter.id, error)
        return SourceStat(
            source_id=adapter.id,
            name=adapter.name,
            ok=False,
            error=error,
            duration_ms=duration_ms,
        ), []
    duration_ms = int((time.perf_counter() - start) * 1000)
    logger.info("信息源完成: %s，%d 条（%d ms）", adapter.id, len(items), duration_ms)
    return SourceStat(
        source_id=adapter.id,
        name=adapter.name,
        ok=True,
        count=len(items),
        duration_ms=duration_ms,
    ), items


__all__ = [
    "ADAPTER_TYPES",
    "CollectResult",
    "SourceError",
    "SourceStat",
    "build_adapters",
    "collect_all",
]
