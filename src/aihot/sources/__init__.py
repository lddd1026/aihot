"""信息源适配器集合。"""

from aihot.sources.arxiv import ArxivAdapter
from aihot.sources.base import SourceAdapter, SourceError
from aihot.sources.github import GithubAdapter
from aihot.sources.hackernews import HackerNewsAdapter
from aihot.sources.hf_papers import HfPapersAdapter
from aihot.sources.html import HtmlAdapter
from aihot.sources.registry import SourceStat, build_adapters, collect_all
from aihot.sources.rss import RssAdapter

__all__ = [
    "ArxivAdapter",
    "GithubAdapter",
    "HackerNewsAdapter",
    "HfPapersAdapter",
    "HtmlAdapter",
    "RssAdapter",
    "SourceAdapter",
    "SourceError",
    "SourceStat",
    "build_adapters",
    "collect_all",
]
