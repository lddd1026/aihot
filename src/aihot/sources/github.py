"""GitHub 热门项目：官方 Search API 取"近期创建 + star 最多"的项目。

为什么用 Search API 而不是 Trending 页面：官方接口稳定、有结构化字段
（stars / forks / language / topics），不需要跟页面改版和反爬对抗。

额外信号：`extra["upvotes"]` 写入 star 数，参与 rank 的社区热度计算。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from aihot.models import RawItem
from aihot.sources.base import SourceAdapter, SourceError
from aihot.utils import clean_html, now_utc

DEFAULT_DAYS = 7
DEFAULT_LIMIT = 15
DEFAULT_MIN_STARS = 100
MAX_LIMIT = 50
TOPIC_LIMIT = 6
SUMMARY_LIMIT = 400
GITHUB_ACCEPT = "application/vnd.github+json"


class GithubAdapter(SourceAdapter):
    """按「最近 N 天创建 + star 数」抓取增长最快的项目。"""

    type = "github"

    def fetch(self, since: datetime) -> list[RawItem]:  # noqa: ARG002 - 时间范围由查询参数限定
        """抓取最近 N 天创建、star 最多的项目。"""
        days = max(1, int(self.source.extra.get("days") or DEFAULT_DAYS))
        limit = min(MAX_LIMIT, max(1, int(self.source.extra.get("limit") or DEFAULT_LIMIT)))
        min_stars = max(0, int(self.source.extra.get("min_stars") or DEFAULT_MIN_STARS))
        language = str(self.source.extra.get("language") or "").strip()
        token = str(self.source.extra.get("token") or "").strip()

        created_after = (now_utc() - timedelta(days=days)).date().isoformat()
        query = f"created:>{created_after} stars:>{min_stars}"
        if language:
            query += f" language:{language}"
        headers = {"Accept": GITHUB_ACCEPT}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload, result = self.http.get_json(
            self.source.url,
            params={"q": query, "sort": "stars", "order": "desc", "per_page": limit},
            headers=headers,
        )
        if payload is None:
            raise SourceError(f"请求失败: {result.error or result.status}")
        if not isinstance(payload, dict) or "items" not in payload:
            raise SourceError(f"响应缺少 items 字段: {str(payload)[:120]}")

        items: list[RawItem] = []
        for repo in payload.get("items") or []:
            if not isinstance(repo, dict):
                continue
            full_name = str(repo.get("full_name") or "").strip()
            html_url = str(repo.get("html_url") or "").strip()
            if not full_name or not html_url:
                continue
            stars = _as_int(repo.get("stargazers_count"))
            forks = _as_int(repo.get("forks_count"))
            language_name = str(repo.get("language") or "").strip()
            topics = [str(topic) for topic in (repo.get("topics") or [])][:TOPIC_LIMIT]
            description = clean_html(str(repo.get("description") or ""))

            summary_parts = [description] if description else []
            meta_bits = [f"语言 {language_name}" if language_name else "", f"star {stars}"]
            if forks:
                meta_bits.append(f"fork {forks}")
            if topics:
                meta_bits.append("topic: " + ", ".join(topics))
            summary_parts.append("｜".join(bit for bit in meta_bits if bit))

            item = self.build_item(
                title=full_name,
                url=html_url,
                # 用"发现时间"作为发布时间：热门项目可能创建于几天前，
                # 但它的新闻价值是"今天正在快速增长"，这样才能进入当日窗口
                published=now_utc(),
                summary=" ｜ ".join(summary_parts)[:SUMMARY_LIMIT],
                extra={
                    "kind": "repo",
                    "stars": stars,
                    "upvotes": stars,  # 参与社区热度
                    "forks": forks,
                    "language": language_name,
                    "topics": topics,
                    "repo_created_at": str(repo.get("created_at") or ""),
                    "pushed_at": str(repo.get("pushed_at") or ""),
                },
            )
            if item is not None:
                items.append(item)
        return items


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
