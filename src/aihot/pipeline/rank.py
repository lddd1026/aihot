"""热度排序：综合 LLM 重要性、跨源提及、社区热度与源权重。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aihot.models import NewsItem

W_IMPORTANCE = 0.55
W_CROSS_SOURCE = 0.20
W_COMMUNITY = 0.15
W_SOURCE = 0.10

COMMUNITY_CAP = 1500.0  # 社区热度归一参考上限（HN 头条约 1500 分）
CROSS_SOURCE_FULL = 3  # 被 4 家以上报道即拿满跨源分
SOURCE_WEIGHT_CAP = 1.2  # 官方源权重上限


@dataclass(slots=True)
class RankedItem:
    """排序结果（含分数拆解，便于调优与解释）。"""

    item: NewsItem
    score: float
    breakdown: dict[str, float]


def community_signal(extra: dict) -> float:
    """社区热度 0~1：取 HN 点数/评论数（与未来的 reddit upvotes）的最大值做 log 归一。"""
    points = float(extra.get("points") or 0)
    comments = float(extra.get("num_comments") or 0)
    upvotes = float(extra.get("upvotes") or 0)
    raw = max(points, comments * 2, upvotes)
    if raw <= 0:
        return 0.0
    return min(1.0, math.log10(1 + raw) / math.log10(1 + COMMUNITY_CAP))


def score_item(
    item: NewsItem,
    *,
    source_weight: float = 1.0,
    interest_bonus: dict[str, float] | None = None,
) -> tuple[float, dict[str, float]]:
    """返回 (总分, 分项拆解)。总分约 0~110（含兴趣加成）。"""
    importance = max(0, min(100, int(item.importance))) / 100.0
    mention = max(1, int(item.extra.get("mention_count") or 1))
    cross = min(1.0, (mention - 1) / CROSS_SOURCE_FULL)
    community = community_signal(item.extra)
    weight = min(1.0, max(0.0, float(source_weight)) / SOURCE_WEIGHT_CAP)

    base = 100.0 * (
        W_IMPORTANCE * importance
        + W_CROSS_SOURCE * cross
        + W_COMMUNITY * community
        + W_SOURCE * weight
    )
    bonus = float((interest_bonus or {}).get(item.category, 0.0))
    breakdown = {
        "importance": round(W_IMPORTANCE * importance * 100, 2),
        "cross_source": round(W_CROSS_SOURCE * cross * 100, 2),
        "community": round(W_COMMUNITY * community * 100, 2),
        "source_weight": round(W_SOURCE * weight * 100, 2),
        "interest_bonus": round(bonus, 2),
    }
    return round(base + bonus, 2), breakdown


def rank_items(
    items: list[NewsItem],
    *,
    weights: dict[str, float] | None = None,
    interest_bonus: dict[str, float] | None = None,
) -> list[RankedItem]:
    """按综合分降序排序；同分时更新的优先。"""
    source_weights = weights or {}
    ranked = [
        RankedItem(
            item=item,
            score=score,
            breakdown=breakdown,
        )
        for item in items
        for score, breakdown in [
            score_item(
                item,
                source_weight=source_weights.get(item.source_id, 1.0),
                interest_bonus=interest_bonus,
            )
        ]
    ]
    ranked.sort(key=lambda entry: (entry.score, entry.item.published_at.timestamp()), reverse=True)
    return ranked


def select_top(ranked: list[RankedItem], count: int) -> list[RankedItem]:
    """取前 count 条（count <= 0 时返回空列表）。"""
    if count <= 0:
        return []
    return ranked[:count]
