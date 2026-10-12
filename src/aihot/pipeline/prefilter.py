"""预筛：AI 相关性门槛 + 垃圾过滤 + 批内去重。

设计要点：
- 只对「通用科技源」（配置里的 gate_sources）做 AI 关键词门槛，AI 专属源直接放行
- 黑名单过滤招聘/课程/促销/活动报名类内容
- 所有规则可被配置覆盖，不硬编码在流程里
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from aihot.config import Config
from aihot.models import RawItem

# ---------- 内置黑名单（垃圾内容） ----------

DEFAULT_BLACKLIST_PATTERNS: tuple[str, ...] = (
    r"招聘|诚聘|内推|简历投递|加入我们|虚位以待",
    r"\bhiring\b|\bwe(?:'| a)?re hiring\b|\bjoin our team\b|\bjob opening\b|\bcareers? at\b",
    r"报名|训练营|直播课|公开课|在线课程|讲座|征稿|投稿|招募",
    r"\bwebinar\b|\bregister (?:now|today|before)\b|\bsave up to\b|\bearly bird\b",
    r"限时|优惠|折扣|免费领取|福利放送|coupon|promo code|giveaway",
    r"赞助|广告位|sponsored|advertisement",
    r"门票|agenda revealed|last chance|days left|tickets? (?:are )?(?:on sale|available)",
    r"活动预告|直播预告|报名开启|邀您参加|敬请期待.*直播",
)

# ---------- 内置 AI 关键词（通用源必须命中其一） ----------

DEFAULT_AI_KEYWORDS_ZH: tuple[str, ...] = (
    "人工智能",
    "大模型",
    "大语言模型",
    "语言模型",
    "多模态",
    "生成式",
    "智能体",
    "机器学习",
    "深度学习",
    "神经网络",
    "预训练",
    "微调",
    "推理",
    "算力",
    "芯片",
    "显卡",
    "具身智能",
    "机器人",
    "自动驾驶",
    "智能驾驶",
    "语音识别",
    "计算机视觉",
    "文生图",
    "文生视频",
    "数据集",
    "提示词",
    "对话模型",
    "开源模型",
    "模型训练",
    "模型发布",
    # 常见公司与产品名（避免误杀"智谱开源了 X"这类没有 AI 字样的标题）
    "智谱",
    "月之暗面",
    "百川智能",
    "零一万物",
    "阶跃星辰",
    "科大讯飞",
    "商汤",
    "通义",
    "文心",
    "混元",
    "豆包",
    "悟道",
    "盘古",
    "星火",
    "混元大模型",
)

DEFAULT_AI_KEYWORDS_EN: tuple[str, ...] = (
    r"\bAI\b",
    r"\bAGI\b",
    r"\bLLM\b",
    r"\bGPT\b",
    r"\bClaude\b",
    r"\bGemini\b",
    r"\bLlama\b",
    r"\bQwen\b",
    r"\bDeepSeek\b",
    r"\bMistral\b",
    r"\bmodel(?:s)?\b",
    r"\bagent(?:s|ic)?\b",
    r"\bmachine learning\b",
    r"\bdeep learning\b",
    r"\bneural\b",
    r"\btransformer(?:s)?\b",
    r"\binference\b",
    r"\btraining\b",
    r"\bfine-?tun",
    r"\bmultimodal\b",
    r"\bgenerative\b",
    r"\bGPUs?\b",
    r"\bTPUs?\b",
    r"\bNPUs?\b",
    r"\bVLM\b",
    r"\bMLOps\b",
    r"\bchips?\b",
    r"\brobotics?\b",
    r"\bautonomous driving\b",
    r"\bRAG\b",
    r"\bprompt(?:s|ing)?\b",
    r"\bembedding(?:s)?\b",
    r"\bbenchmark(?:s)?\b",
    # 常见厂商与产品名
    r"\bOpenAI\b",
    r"\bAnthropic\b",
    r"\bDeepMind\b",
    r"\bMistral\b",
    r"\bCohere\b",
    r"\bPerplexity\b",
    r"\bGrok\b",
    r"\bCopilot\b",
    r"\bCodex\b",
    r"\bCursor\b",
    r"\bJev\b",
    r"\barxiv\b",
    r"\bdatasets?\b",
    r"\bself-driving\b",
    r"\bautonomous\b",
    r"\bvoice assistant\b",
)


@dataclass(slots=True)
class PrefilterStats:
    """预筛统计（用于运行报告与调优）。"""

    total: int = 0
    kept: int = 0
    dropped_blacklist: int = 0
    dropped_offtopic: int = 0
    dropped_duplicate: int = 0
    dropped_samples: dict[str, list[str]] = field(default_factory=dict)

    def sample(self, reason: str, title: str, limit: int = 5) -> None:
        bucket = self.dropped_samples.setdefault(reason, [])
        if len(bucket) < limit:
            bucket.append(title)


@dataclass(slots=True)
class PrefilterResult:
    items: list[RawItem]
    stats: PrefilterStats


def compile_patterns(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """把配置/内置的模式编译为正则（忽略大小写）。"""
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue  # 坏配置不应拖垮流程
    return compiled


def prefilter_items(
    items: list[RawItem],
    cfg: Config,
) -> PrefilterResult:
    """过滤垃圾内容、与 AI 无关（仅门槛源）以及批内重复的条目。

    注意：时间窗口在采集阶段已经应用，这里不重复过滤（否则隔日加工会误杀）。
    """
    settings = cfg.pipeline.prefilter
    if settings.use_builtin_blacklist:
        blacklist_patterns = DEFAULT_BLACKLIST_PATTERNS + tuple(settings.blacklist_keywords)
    else:
        blacklist_patterns = tuple(settings.blacklist_keywords)
    blacklist = compile_patterns(blacklist_patterns)
    ai_patterns = compile_patterns(
        DEFAULT_AI_KEYWORDS_EN + tuple(settings.extra_ai_keywords)
    )
    ai_chinese = DEFAULT_AI_KEYWORDS_ZH
    gate_sources = set(settings.gate_sources)

    stats = PrefilterStats(total=len(items))
    kept: list[RawItem] = []
    seen_hashes: set[tuple[str, str]] = set()

    for item in items:
        key = (item.source_id, item.url_hash)
        if key in seen_hashes:
            stats.dropped_duplicate += 1
            stats.sample("批内重复", item.title)
            continue
        seen_hashes.add(key)
        text = f"{item.title} {item.raw_summary}"
        if _matches(blacklist, text):
            stats.dropped_blacklist += 1
            stats.sample("垃圾内容", item.title)
            continue
        if item.source_id in gate_sources and not _is_ai_related(
            text, ai_patterns, ai_chinese
        ):
            stats.dropped_offtopic += 1
            stats.sample("与 AI 无关", item.title)
            continue
        kept.append(item)

    stats.kept = len(kept)
    return PrefilterResult(items=kept, stats=stats)


def _matches(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _is_ai_related(
    text: str,
    english_patterns: tuple[re.Pattern[str], ...],
    chinese_keywords: tuple[str, ...],
) -> bool:
    if any(keyword in text for keyword in chinese_keywords):
        return True
    return any(pattern.search(text) for pattern in english_patterns)
