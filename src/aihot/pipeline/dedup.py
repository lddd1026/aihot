"""事件聚类：URL 相同自动同簇 + 标题相似度并查集。

只负责「同一语言、标题高度相似」的合并；跨语言事件合并交给 enrich 的 LLM 精修环节。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from rapidfuzz import fuzz

from aihot.models import Cluster, RawItem
from aihot.utils import text_hash

# 标题噪声：标点、空白
_PUNCT_RE = re.compile(r"[\s\-—–_|｜·、:：,，。.!！?？\"'“”‘’()（）\[\]【】<>《》/\\]+")
# 媒体后缀（如 "标题 - 量子位" / "标题 | TechCrunch"）：
# 必须"连字符前有空白"或"竖线分隔"，避免把 GPT-6、Llama-3 里的连字符当后缀
_SUFFIX_RE = re.compile(r"(?:\s+[-—–]\s*|\s*[|｜]\s*)[^-—–|｜]{1,24}$")
_SPACE_RE = re.compile(r"\s+")

DEFAULT_THRESHOLD = 88.0
DEFAULT_MAX_HOURS_APART = 72
# 包含关系（一个标题是另一个的缩短版）的最低长度与加分
CONTAINMENT_MIN_CHARS = 6
CONTAINMENT_SCORE = 92.0
# 版本号冲突时的相似度上限（必须低于合并阈值）
VERSION_CONFLICT_CAP = 60.0
# LLM 精修结果的规则校验参数
REFINE_SIM_FLOOR = 55.0
REFINE_MAX_GROUP_SIZE = 4


@dataclass(slots=True)
class DedupStats:
    """聚类统计。"""

    input_items: int = 0
    clusters: int = 0
    merged_pairs: int = 0
    max_cluster_size: int = 0
    multi_source_clusters: int = 0


@dataclass(slots=True)
class ClusterResult:
    clusters: list[Cluster] = field(default_factory=list)
    stats: DedupStats = field(default_factory=DedupStats)
    members: dict[int, list[RawItem]] = field(default_factory=dict)


def normalize_title(title: str) -> str:
    """标题归一化：小写、去标点空白、去尾部媒体后缀。"""
    text = (title or "").strip().lower()
    text = _SUFFIX_RE.sub("", text)
    return _PUNCT_RE.sub("", text)


def token_form(title: str) -> str:
    """保留空格的归一化形式（供英文词序无关的比对使用）。"""
    text = (title or "").strip().lower()
    text = _SUFFIX_RE.sub("", text)
    text = re.sub(r"[_|｜·、:：,，。.!！?？\"'“”‘’()（）\[\]【】<>《》/\\]+", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def title_similarity(left: str, right: str) -> float:
    """0~100 的标题相似度。

    组合三种信号，取最大值（宁可少合并，也不要把不同事件错并）：
    ① 字符级编辑相似度——中文主力，且对产品名差异（Sol vs Luna）敏感
    ② 词排序相似度——英文标题换词序仍能匹配
    ③ 包含关系加成——"标题" vs "标题（附回放）"

    另有一条硬护栏：两者都带版本号且版本号完全不同（Llama-4 vs Llama-5）时封顶，
    避免"同一产品不同代次"被误判为同一事件。
    """
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    score = fuzz.ratio(a, b)
    token_a, token_b = token_form(left), token_form(right)
    if " " in token_a or " " in token_b:
        score = max(score, fuzz.token_sort_ratio(token_a, token_b))
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= CONTAINMENT_MIN_CHARS and shorter in longer:
        score = max(score, CONTAINMENT_SCORE)
    if _versions_conflict(a, b):
        score = min(score, VERSION_CONFLICT_CAP)
    return float(score)


_VERSION_RE = re.compile(r"\d+(?:\.\d+)+|\d+")


def _versions_conflict(left: str, right: str) -> bool:
    """两侧都出现版本号且完全不相交时为 True（如 llama4 vs llama5）。"""
    left_versions = set(_VERSION_RE.findall(left))
    right_versions = set(_VERSION_RE.findall(right))
    return bool(left_versions and right_versions and not (left_versions & right_versions))


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, index: int) -> int:
        while self._parent[index] != index:
            self._parent[index] = self._parent[self._parent[index]]
            index = self._parent[index]
        return index

    def union(self, left: int, right: int) -> bool:
        root_left, root_right = self.find(left), self.find(right)
        if root_left == root_right:
            return False
        self._parent[root_right] = root_left
        return True


def cluster_items(
    items: list[RawItem],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_hours_apart: int = DEFAULT_MAX_HOURS_APART,
) -> ClusterResult:
    """把同一事件的条目聚成一簇。

    规则：① URL 完全相同 → 同簇；② 标题相似度 ≥ threshold 且发布时间相近 → 同簇。
    """
    result = ClusterResult()
    result.stats.input_items = len(items)
    if not items:
        return result

    union = _UnionFind(len(items))
    merged_pairs = 0

    # ① URL 相同
    by_hash: dict[str, int] = {}
    for index, item in enumerate(items):
        previous = by_hash.get(item.url_hash)
        if previous is not None and union.union(previous, index):
            merged_pairs += 1
        else:
            by_hash.setdefault(item.url_hash, index)

    # ② 标题相似度（索引按发布时间排序，便于时间窗剪枝）
    order = sorted(range(len(items)), key=lambda i: items[i].published_at)
    normalized = [normalize_title(item.title) for item in items]
    max_delta = timedelta(hours=max_hours_apart)
    for position, left in enumerate(order):
        for right in order[position + 1 :]:
            right_item = items[right]
            if right_item.published_at - items[left].published_at > max_delta:
                break
            if union.find(left) == union.find(right):
                continue
            if not normalized[left] or not normalized[right]:
                continue
            score = title_similarity(items[left].title, right_item.title)
            if score >= threshold and union.union(left, right):
                merged_pairs += 1

    groups: dict[int, list[int]] = {}
    for index in range(len(items)):
        groups.setdefault(union.find(index), []).append(index)

    clusters: list[Cluster] = []
    ordered_groups = sorted(groups.values(), key=lambda group: group[0])
    for cluster_id, member_indexes in enumerate(ordered_groups, start=1):
        members = [items[index] for index in member_indexes]
        members.sort(key=lambda item: item.published_at)
        source_ids = sorted({item.source_id for item in members})
        clusters.append(
            Cluster(
                cluster_id=cluster_id,
                canonical_title=members[0].title,
                item_hashes=[item.url_hash for item in members],
                source_ids=source_ids,
                mention_count=len(members),
                heat_score=0.0,
                first_seen=min(item.published_at for item in members),
            )
        )
        result.members[cluster_id] = members

    result.clusters = clusters
    result.stats.clusters = len(clusters)
    result.stats.merged_pairs = merged_pairs
    result.stats.max_cluster_size = max((len(m) for m in result.members.values()), default=0)
    result.stats.multi_source_clusters = sum(1 for c in clusters if len(c.source_ids) > 1)
    return result


def representativeness(item: RawItem, source_weight: float = 1.0) -> tuple:
    """代表条目打分（越大越优先）：源权重 > 有摘要 > 热度 > 更早发布。"""
    heat = float(item.extra.get("points") or 0) + float(item.extra.get("num_comments") or 0)
    return (source_weight, 1 if item.raw_summary else 0, heat, -item.published_at.timestamp())


def pick_representative(
    members: list[RawItem], weights: dict[str, float]
) -> RawItem:
    """从簇内选出代表条目（用于 LLM 加工与展示）。"""
    return max(members, key=lambda item: representativeness(item, weights.get(item.source_id, 1.0)))


def cluster_signature(item: RawItem) -> str:
    """条目级签名（用于簇内去重与测试）。"""
    return text_hash(normalize_title(item.title))


def validate_merged_groups(
    groups: list[list[int]],
    clusters: list[Cluster],
    *,
    sim_floor: float = REFINE_SIM_FLOOR,
    max_size: int = REFINE_MAX_GROUP_SIZE,
) -> tuple[list[list[int]], list[str]]:
    """用规则校验 LLM 提出的分组，返回 (接受的分组, 拒绝原因)。

    校验两条硬约束：
    ① 组内必须存在相似链（相邻成员标题相似度 ≥ sim_floor），否则拆成连通分量
    ② 单组不得超过 max_size（一个事件一天内被 4 家以上重复报道的概率极低）
    """
    titles = {cluster.cluster_id: cluster.canonical_title for cluster in clusters}
    accepted: list[list[int]] = []
    rejections: list[str] = []
    for group in groups:
        if len(group) <= 1:
            accepted.append(list(group))
            continue
        components = _connected_components(group, titles, sim_floor)
        if len(components) > 1:
            rejections.append(
                f"组内缺少相似链（{len(group)} 条 → 拆为 {len(components)} 组）: "
                f"{titles.get(group[0], '')[:40]}"
            )
            accepted.extend(components)
            continue
        if len(group) > max_size:
            rejections.append(
                f"分组过大（{len(group)} 条 > {max_size}）: {titles.get(group[0], '')[:40]}"
            )
            accepted.extend([[cluster_id] for cluster_id in group])
            continue
        accepted.append(list(group))
    return accepted, rejections


def _connected_components(
    group: list[int], titles: dict[int, str], sim_floor: float
) -> list[list[int]]:
    """按「标题相似度 ≥ sim_floor」把分组拆成连通分量。"""
    union = _UnionFind(len(group))
    for left in range(len(group)):
        for right in range(left + 1, len(group)):
            if union.find(left) == union.find(right):
                continue
            score = title_similarity(
                titles.get(group[left], ""), titles.get(group[right], "")
            )
            if score >= sim_floor:
                union.union(left, right)
    buckets: dict[int, list[int]] = {}
    for index, cluster_id in enumerate(group):
        buckets.setdefault(union.find(index), []).append(cluster_id)
    return [buckets[root] for root in sorted(buckets)]


def merge_clusters_by_groups(    clusters: list[Cluster],
    members: dict[int, list[RawItem]],
    groups: list[list[int]],
) -> ClusterResult:
    """按 LLM 给出的分组把规则簇合并成更大簇（跨语言事件合并）。

    groups: 每个元素是一组「应视为同一事件」的旧 cluster_id。
    """
    cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
    merged_clusters: list[Cluster] = []
    merged_members: dict[int, list[RawItem]] = {}

    sorted_groups = sorted(
        (group for group in groups if any(cid in cluster_by_id for cid in group)),
        key=lambda group: min(
            cluster_by_id[cid].first_seen for cid in group if cid in cluster_by_id
        ),
    )
    for new_id, group in enumerate(sorted_groups, start=1):
        group_members: list[RawItem] = []
        for cluster_id in group:
            group_members.extend(members.get(cluster_id, []))
        if not group_members:
            continue
        group_members.sort(key=lambda item: item.published_at)
        source_ids = sorted({item.source_id for item in group_members})
        merged_clusters.append(
            Cluster(
                cluster_id=new_id,
                canonical_title=cluster_by_id[group[0]].canonical_title,
                item_hashes=[item.url_hash for item in group_members],
                source_ids=source_ids,
                mention_count=len(group_members),
                heat_score=0.0,
                first_seen=min(item.published_at for item in group_members),
            )
        )
        merged_members[new_id] = group_members

    return ClusterResult(
        clusters=merged_clusters,
        stats=DedupStats(
            input_items=sum(len(group) for group in merged_members.values()),
            clusters=len(merged_clusters),
            merged_pairs=0,
            max_cluster_size=max((len(m) for m in merged_members.values()), default=0),
            multi_source_clusters=sum(1 for c in merged_clusters if len(c.source_ids) > 1),
        ),
        members=merged_members,
    )
