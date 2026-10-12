"""LLM 加工：事件跨语言精修 + 条目加工 + 日报综述。

约束：
- 这是全项目**唯一**调用大模型的地方
- 任何 LLM 异常都必须降级（不崩溃、不写垃圾），并计入统计
- 已加工条目由 store 层过滤，避免重复计费
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aihot.config import Config
from aihot.models import Cluster, NewsItem, RawItem
from aihot.pipeline import prompts
from aihot.pipeline.dedup import (
    ClusterResult,
    merge_clusters_by_groups,
    pick_representative,
    validate_merged_groups,
)

logger = logging.getLogger("aihot.enrich")

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_FALLBACK_IMPORTANCE = 30
_REFINE_MAX_CLUSTERS = 150


@dataclass(slots=True)
class EnrichStats:
    """LLM 调用统计（成本核算与告警依据）。"""

    refine_calls: int = 0
    refine_failed: int = 0
    refine_rejected: int = 0
    batches: int = 0
    succeeded: int = 0
    failed_batches: int = 0
    fallback_items: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    cost_limit_hit: bool = False
    time_budget_hit: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "refine_calls": self.refine_calls,
            "refine_failed": self.refine_failed,
            "refine_rejected": self.refine_rejected,
            "batches": self.batches,
            "succeeded": self.succeeded,
            "failed_batches": self.failed_batches,
            "fallback_items": self.fallback_items,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_cny": round(self.cost_cny, 4),
            "cost_limit_hit": self.cost_limit_hit,
            "time_budget_hit": self.time_budget_hit,
        }


def _strip_fence(content: str) -> str:
    return _JSON_FENCE_RE.sub("", (content or "").strip()).strip()


def parse_json_object(content: str) -> dict[str, Any] | None:
    """宽容解析：去 Markdown 围栏、截取首个 { 到最后一个 }。失败返回 None。"""
    text = _strip_fence(content)
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class Enricher:
    """DeepSeek（openai 兼容接口）封装。"""

    def __init__(
        self,
        cfg: Config,
        *,
        client: Any | None = None,
        chat: Callable[[str, str], tuple[str | None, str]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.stats = EnrichStats()
        self._batch_size = max(1, cfg.llm.batch_size)
        self._price_in = cfg.pipeline.enrich.price_input_per_million
        self._price_out = cfg.pipeline.enrich.price_output_per_million
        self._cost_limit = cfg.llm.daily_cost_limit_cny
        self._time_budget = max(60, cfg.pipeline.enrich.time_budget_seconds)
        self._chat_override = chat
        self._client = client if chat is None else None

    # ---------- LLM 调用底座 ----------

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
            self.stats.errors.append(f"openai SDK 不可用: {exc}")
            return None
        self._client = OpenAI(
            api_key=self.cfg.llm.api_key,
            base_url=self.cfg.llm.base_url,
            timeout=60.0,
            max_retries=2,
        )
        return self._client

    def _chat(
        self, system: str, user: str, *, json_mode: bool = True
    ) -> tuple[str | None, str]:
        """返回 (内容, 错误描述)。任何异常都被降级为错误字符串。

        json_mode=False 用于"要纯文本"的场景（如日报综述），
        否则模型可能把 JSON 包装也写进正文。
        """
        if self._chat_override is not None:
            return self._chat_override(system, user)
        client = self._ensure_client()
        if client is None:
            return None, "LLM 客户端不可用"
        request: dict[str, Any] = {
            "model": self.cfg.llm.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 4000,
        }
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        try:
            response = client.chat.completions.create(**request)
        except Exception as exc:  # LLM 失败必须降级，不能拖垮整条流水线
            return None, f"{type(exc).__name__}: {exc}"
        self._record_usage(getattr(response, "usage", None))
        try:
            content = response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError) as exc:
            return None, f"响应结构异常: {exc}"
        return content, ""

    def _record_usage(self, usage: Any) -> None:
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        self.stats.prompt_tokens += prompt_tokens
        self.stats.completion_tokens += completion_tokens
        self.stats.cost_cny = (
            self.stats.prompt_tokens / 1_000_000 * self._price_in
            + self.stats.completion_tokens / 1_000_000 * self._price_out
        )

    def _over_budget(self) -> bool:
        if self.stats.cost_cny > self._cost_limit:
            self.stats.cost_limit_hit = True
            return True
        return False

    # ---------- ① 事件跨语言精修 ----------

    def refine_clusters(self, result: ClusterResult) -> ClusterResult:
        """用一个 LLM 调用把规则簇按"同一事件"合并（含跨语言）。失败则原样返回。"""
        clusters = result.clusters
        if len(clusters) < 2:
            return result
        if len(clusters) > _REFINE_MAX_CLUSTERS:
            logger.warning("簇数量 %d 超过精修上限，跳过 LLM 精修", len(clusters))
            return result
        listing = "\n".join(
            f"{cluster.cluster_id}. [{', '.join(cluster.source_ids)}] {cluster.canonical_title}"
            for cluster in clusters
        )
        content, error = self._chat(
            prompts.CLUSTER_REFINE_SYSTEM,
            prompts.CLUSTER_REFINE_USER.format(count=len(clusters), listing=listing),
        )
        self.stats.refine_calls += 1
        if content is None:
            self.stats.refine_failed += 1
            self.stats.errors.append(f"聚类精修失败: {error}")
            logger.warning("聚类精修失败（保留规则聚类）: %s", error)
            return result
        payload = parse_json_object(content)
        valid_ids = {cluster.cluster_id for cluster in clusters}
        raw_groups = _parse_refine_groups(payload, valid_ids)
        if raw_groups is None:
            self.stats.refine_failed += 1
            self.stats.errors.append("聚类精修返回结构非法")
            logger.warning("聚类精修返回结构非法（保留规则聚类）")
            return result
        groups, rejections = validate_merged_groups(raw_groups, clusters)
        for rejection in rejections:
            self.stats.refine_rejected += 1
            self.stats.errors.append(f"聚类精修被规则拒绝: {rejection}")
            logger.warning("聚类精修被规则拒绝: %s", rejection)
        if all(len(group) == 1 for group in groups):
            return result
        merged = merge_clusters_by_groups(clusters, result.members, groups)
        logger.info(
            "聚类精修完成: %d → %d 簇（跨源簇 %d）",
            len(clusters),
            len(merged.clusters),
            merged.stats.multi_source_clusters,
        )
        return merged

    # ---------- ② 逐条（逐簇）加工 ----------

    def enrich_clusters(
        self, result: ClusterResult, weights: dict[str, float]
    ) -> list[NewsItem]:
        """对每个簇的代表条目调用 LLM，产出加工后的 NewsItem（保持输入顺序）。"""
        clusters = result.clusters
        if not clusters:
            return []
        deadline = time.monotonic() + self._time_budget
        enriched: dict[int, NewsItem] = {}
        for start in range(0, len(clusters), self._batch_size):
            if self._over_budget():
                logger.warning("已达每日成本上限 %.2f 元，停止后续加工", self._cost_limit)
                break
            if time.monotonic() > deadline:
                self.stats.time_budget_hit = True
                logger.warning("已达加工时间上限 %d 秒，停止后续批次", self._time_budget)
                break
            batch = clusters[start : start + self._batch_size]
            self._enrich_batch(batch, result.members, weights, enriched)

        items: list[NewsItem] = []
        for cluster in clusters:
            item = enriched.get(cluster.cluster_id)
            if item is None:
                item = fallback_item(cluster, result.members, weights)
                self.stats.fallback_items += 1
            items.append(item)
        return items

    def _enrich_batch(
        self,
        batch: list[Cluster],
        members: dict[int, list[RawItem]],
        weights: dict[str, float],
        out: dict[int, NewsItem],
    ) -> None:
        representatives = {
            cluster.cluster_id: pick_representative(
                members.get(cluster.cluster_id, []), weights
            )
            for cluster in batch
        }
        listing = "\n".join(
            _format_entry(index, cluster, representatives[cluster.cluster_id])
            for index, cluster in enumerate(batch, start=1)
        )
        content, error = self._chat(
            prompts.ENRICH_SYSTEM,
            prompts.ENRICH_USER.format(count=len(batch), listing=listing),
        )
        self.stats.batches += 1
        payload = parse_json_object(content) if content is not None else None
        entries = _valid_items(payload, len(batch)) if payload is not None else None
        if entries is None:
            self.stats.failed_batches += 1
            reason = error or "返回结构非法"
            self.stats.errors.append(f"加工失败: {reason}")
            logger.warning("批次加工失败（降级为原文）: %s", reason)
            for cluster in batch:
                out[cluster.cluster_id] = fallback_item(cluster, members, weights)
                self.stats.fallback_items += 1
            return
        self.stats.succeeded += len(batch)
        for index, cluster in enumerate(batch, start=1):
            entry = entries[index - 1]
            out[cluster.cluster_id] = _build_news_item(
                cluster, representatives[cluster.cluster_id], members, entry
            )

    # ---------- ③ 日报综述 ----------

    def summarize(self, items: list[NewsItem]) -> str:
        """生成 150~250 字的今日综述；失败返回空串（渲染层会跳过该段）。"""
        if not items:
            return ""
        listing = "\n".join(
            f"{index}. [{item.category}] {item.cn_title or item.title}"
            for index, item in enumerate(items, start=1)
        )
        content, error = self._chat(
            prompts.DIGEST_SUMMARY_SYSTEM,
            prompts.DIGEST_SUMMARY_USER.format(listing=listing),
            json_mode=False,
        )
        if content is None:
            self.stats.errors.append(f"综述生成失败: {error}")
            logger.warning("综述生成失败: %s", error)
            return ""
        return clean_summary(content)


# ---------- 纯函数（便于单测） ----------


def _format_entry(index: int, cluster: Cluster, representative: RawItem) -> str:
    summary = (representative.raw_summary or "").strip()[: prompts.SUMMARY_MAX_CHARS]
    sources = ",".join(cluster.source_ids)
    return (
        f"[{index}] 来源：{sources} | 标题：{representative.title}"
        f" | 摘要：{summary or '（无）'}"
    )


def clean_summary(content: str) -> str:
    """综述文本清洗。

    兜底场景：模型偶发仍返回 JSON 包装（历史数据里出现过
    `{"type": "json_object", "content": "..."}`），此时抽出正文字段。
    """
    text = _strip_fence(content).strip()
    if text.startswith("{"):
        payload = parse_json_object(text)
        if payload:
            for key in ("content", "summary", "综述", "text", "result", "摘要"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:600]
            longest = ""
            for value in payload.values():
                if isinstance(value, str) and len(value) > len(longest):
                    longest = value
            if longest:
                text = longest
    return text.strip()[:600]


def _parse_refine_groups(
    payload: dict[str, Any] | None, valid_ids: set[int]
) -> list[list[int]] | None:
    """解析 LLM 的分组结果。

    - 非 high 置信度的分组一律不合并（只保留单元素组）
    - 未提及的编号自动补成单元素组，保证覆盖完整性
    """
    if not payload or not isinstance(payload.get("groups"), list):
        return None
    groups: list[list[int]] = []
    seen: set[int] = set()
    for entry in payload["groups"]:
        if not isinstance(entry, dict):
            return None
        indexes = entry.get("indexes")
        if not isinstance(indexes, list) or not indexes:
            return None
        group: list[int] = []
        for value in indexes:
            if not isinstance(value, int) or value not in valid_ids or value in seen:
                return None
            seen.add(value)
            group.append(value)
        confidence = str(entry.get("confidence") or "").strip().lower()
        if confidence == "high":
            groups.append(group)
        else:
            groups.extend([[value] for value in group])
    for missing in sorted(valid_ids - seen):
        groups.append([missing])
    return groups


def _valid_items(
    payload: dict[str, Any] | None, expected: int
) -> list[dict[str, Any]] | None:
    if not payload or not isinstance(payload.get("items"), list):
        return None
    entries = payload["items"]
    if len(entries) != expected:
        return None
    indexed: list[dict[str, Any] | None] = [None] * expected
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        index = entry.get("index")
        if not isinstance(index, int) or not 1 <= index <= expected:
            return None
        if indexed[index - 1] is not None:
            return None
        indexed[index - 1] = entry
    if any(entry is None for entry in indexed):
        return None
    return [entry for entry in indexed if entry is not None]


def _coerce_category(value: object) -> str:
    category = str(value or "").strip()
    return category if category in prompts.CATEGORIES else "其他"


def _coerce_importance(value: object) -> int:
    try:
        importance = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _FALLBACK_IMPORTANCE
    return max(0, min(100, importance))


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _build_news_item(
    cluster: Cluster,
    representative: RawItem,
    members: dict[int, list[RawItem]],
    entry: dict[str, Any],
) -> NewsItem:
    cn_title = str(entry.get("cn_title") or "").strip()[:120] or representative.title
    cn_summary = str(entry.get("cn_summary") or "").strip()[:200]
    category = _coerce_category(entry.get("category"))
    importance = _coerce_importance(entry.get("importance"))
    if not _coerce_bool(entry.get("is_ai", True)):
        category = "其他"
        importance = min(importance, 15)
    return _news_item_from(
        cluster,
        representative,
        members,
        cn_title=cn_title,
        cn_summary=cn_summary,
        category=category,
        importance=importance,
    )


def fallback_item(
    cluster: Cluster, members: dict[int, list[RawItem]], weights: dict[str, float]
) -> NewsItem:
    """LLM 不可用时的降级条目：保留原文标题与摘要片段，重要度中性偏低。"""
    group = members.get(cluster.cluster_id, [])
    representative = (
        pick_representative(group, weights) if group else None
    )
    if representative is None:  # 理论上不会发生
        representative = RawItem(
            source_id="unknown",
            title=cluster.canonical_title,
            url="",
            published_at=cluster.first_seen,
        )
    return _news_item_from(
        cluster,
        representative,
        members,
        cn_title=representative.title,
        cn_summary=(representative.raw_summary or "").strip()[:160],
        category="其他",
        importance=_FALLBACK_IMPORTANCE,
    )


def _news_item_from(
    cluster: Cluster,
    representative: RawItem,
    members: dict[int, list[RawItem]],
    *,
    cn_title: str,
    cn_summary: str,
    category: str,
    importance: int,
) -> NewsItem:
    links = [
        {"source_id": item.source_id, "title": item.title, "url": item.url}
        for item in members.get(cluster.cluster_id, [])
    ]
    extra = dict(representative.extra)
    extra["mention_count"] = cluster.mention_count
    extra["source_ids"] = list(cluster.source_ids)
    extra["links"] = links
    return NewsItem(
        source_id=representative.source_id,
        title=representative.title,
        url=representative.url,
        published_at=representative.published_at,
        raw_summary=representative.raw_summary,
        lang=representative.lang,
        extra=extra,
        cluster_id=cluster.cluster_id,
        category=category,
        importance=importance,
        cn_title=cn_title,
        cn_summary=cn_summary,
    )
