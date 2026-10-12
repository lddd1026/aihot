"""M7 独立验收支撑模块（内容范围调整为开发者向）。

设计约束（黑盒验收）：
- 复用 m3_support（假 RSS / 假 LLM / Suite）、m4_support（脱敏真实配置）、
  m5r_support、m6r_support（staged 配置）——旧文件不改动；
- 只用 localhost 假服务；不访问公网、不真实推送/发邮件/调 LLM；
- 不修改 config.yaml / 真实 data / logs；证据写到 reports/evidence/m7r/<suite>/。

样本表：把「必须剔除的硬件/商业标题」「必须保留的工程标题」「规则层按设计放行
（交给 LLM 语义层）的擦边标题」集中定义，供各套件共用与增补。
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# 证据根目录必须在导入 m3/m4/m6r 支撑模块之前逐级设置（各支撑模块在 import 时读取）
os.environ["AIHOT_M7R_EVIDENCE"] = os.environ.get("AIHOT_M7R_EVIDENCE", "m7r")
os.environ["AIHOT_M6R_EVIDENCE"] = os.environ["AIHOT_M7R_EVIDENCE"]
os.environ["AIHOT_M5R_EVIDENCE"] = os.environ["AIHOT_M7R_EVIDENCE"]
os.environ["AIHOT_M4_EVIDENCE"] = os.environ["AIHOT_M7R_EVIDENCE"]
os.environ["AIHOT_M3_EVIDENCE"] = os.environ["AIHOT_M7R_EVIDENCE"]

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m4_support as m4  # noqa: E402
import m5r_support as m5  # noqa: E402
import m6r_support as m6  # noqa: E402

PROJECT = m3.PROJECT
PYTHON = m3.PYTHON
TEMPROOT = m3.TEMPROOT
EVIDENCE = m3.EVIDENCE
RAW = EVIDENCE / "raw"

sys.path.insert(0, str(PROJECT / "src"))

from aihot.config import Config, load_config  # noqa: E402
from aihot.models import NewsItem, RawItem  # noqa: E402
from aihot.pipeline.prefilter import (  # noqa: E402
    DEFAULT_EXCLUDE_PATTERNS,
    PrefilterResult,
    prefilter_items,
)

# --------------------------------------------------------------------------- 样本表

# 明确硬件/商业标题：规则层必须剔除（措辞全部命中内置高精度模式）
TOPIC_DROP_SAMPLES: list[dict[str, str]] = [
    {"kind": "hardware", "title": "台积电 2nm 晶圆产能被 AI 公司预订一空"},
    {"kind": "hardware", "title": "液冷散热方案成为新一代数据中心标配"},
    {"kind": "hardware", "title": "Nvidia data center revenue jumps 40% on AI demand"},
    {"kind": "hardware", "title": "AI server racks sold out as chip makers ramp up output"},
    {"kind": "hardware", "title": "固态硬盘价格走高，AI 训练集群采购承压"},
    {"kind": "hardware", "title": "显卡涨价潮：新一代旗舰价格创新高"},
    {"kind": "business", "title": "英伟达洽谈收购一家 AI 芯片初创公司"},
    {"kind": "business", "title": "AI startup raises $50M to build autonomous agents"},
    {"kind": "business", "title": "OpenAI acquires robotics startup in an all-stock deal"},
    {"kind": "business", "title": "某大模型公司完成 B 轮融资，估值 120 亿元"},
    {"kind": "business", "title": "Meta 财报：AI 部门营收不及预期，宣布裁员"},
    {"kind": "business", "title": "Anthropic's valuation doubles in a new funding round"},
    {"kind": "business", "title": "国内 AI 芯片公司启动 IPO 招股"},
]

# 工程内容：出现 GPU/显存/型号名也必须保留（误杀护栏）
TOPIC_KEEP_SAMPLES: list[dict[str, str]] = [
    {"kind": "engineering", "title": "AirLLM：单卡 4GB GPU 跑 70B 大模型"},
    {"kind": "engineering", "title": "单张 RTX 3090 上 Qwen 27B 达 140 tok/s"},
    {"kind": "engineering", "title": "vLLM 推理优化实践：PagedAttention 吞吐提升 3 倍"},
    {"kind": "engineering", "title": "可在消费级显卡运行的小模型：4GB 显存跑 7B"},
    {"kind": "engineering", "title": "在 8 张 A100 上训练 13B 模型的工程笔记"},
    {"kind": "engineering", "title": "用 llama.cpp 量化部署：MacBook 统一内存跑 70B"},
    {"kind": "engineering", "title": "How we cut inference cost by 80% with KV cache reuse"},
    {"kind": "engineering", "title": "从 0 搭建 RAG 评测流水线：踩坑与经验"},
    {"kind": "engineering", "title": "NVIDIA CUDA 显存优化：让大模型推理快 2 倍"},
    {"kind": "engineering", "title": "DeepSeek 蒸馏小模型实战：显存不够也能微调"},
    {"kind": "engineering", "title": "消费级 GPU 的 TPOT 对比：3090 vs 4090 vs A6000"},
    {"kind": "engineering", "title": "用 4 张 4090 复现 70B 推理的踩坑记录"},
]

# 规则层按设计放行的擦边标题：语义层（LLM→分类→compose 排除）负责兜底
RULE_ESCAPE_SAMPLES: list[dict[str, str]] = [
    {"kind": "hardware-escape", "title": "RTX 5090 涨价，AI 公司抢购高端显卡"},
    {
        "kind": "business-escape",
        "title": "Mistral AI partners with EuroCloud in multi-year cloud deal",
    },
    {"kind": "business-escape", "title": "AWS 与 Anthropic 深化合作，共建 AI 基础设施"},
]


def make_raw_item(
    index: int,
    title: str,
    *,
    source_id: str = "fake_feed",
    summary: str = "",
    minutes_ago: int = 30,
) -> RawItem:
    """构造预筛输入条目（source_id 默认不在 gate_sources，避免 AI 门槛干扰主题判定）。"""
    return RawItem(
        source_id=source_id,
        title=title,
        url=f"https://example.com/m7r/{index}",
        published_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        raw_summary=summary,
        lang="zh" if any("\u4e00" <= ch <= "\u9fff" for ch in title) else "en",
    )


def base_pipeline_raw() -> dict[str, Any]:
    """真实配置里的 pipeline 段（含本轮新键），用于构造测试配置。"""
    raw = m4.sanitized_real_raw()
    pipeline = raw.get("pipeline")
    return copy.deepcopy(pipeline) if isinstance(pipeline, dict) else {}


def write_and_load(ws: Path, *, pipeline: dict[str, Any] | None) -> tuple[Path, Config]:
    """写一份临时配置并加载（数据/日志指向 ws）。"""
    cfg_path = m3.write_config(
        ws / "config.yaml",
        data_dir=ws / "data",
        log_dir=ws / "logs",
        pipeline=pipeline,
    )
    return cfg_path, load_config(cfg_path)


def run_prefilter_titles(
    titles: list[tuple[str, str]],
    *,
    pipeline: dict[str, Any] | None = None,
    ws_name: str = "m7r-prefilter",
    source_id: str = "fake_feed",
) -> tuple[PrefilterResult, list[RawItem]]:
    """跑一次预筛：titles 为 (title, summary) 列表，返回结果与原始条目。"""
    ws = m3.workspace(ws_name)
    _, cfg = write_and_load(ws, pipeline=pipeline)
    items = [
        make_raw_item(index, title, source_id=source_id, summary=summary)
        for index, (title, summary) in enumerate(titles, start=1)
    ]
    return prefilter_items(items, cfg), items


def make_news(
    index: int,
    *,
    title: str,
    category: str,
    importance: int,
    cn_title: str | None = None,
    kind: str = "",
    source_id: str = "qbitai",
) -> NewsItem:
    """构造 NewsItem（排序/组装层夹具）。"""
    extra: dict[str, Any] = {"mention_count": 1, "source_ids": [source_id]}
    if kind:
        extra["kind"] = kind
    return NewsItem(
        source_id=source_id,
        title=title,
        url=f"https://example.com/m7r/news/{index}",
        published_at=datetime.now(UTC) - timedelta(hours=index % 12 + 1),
        raw_summary="",
        lang="zh",
        extra=extra,
        cluster_id=index,
        category=category,
        importance=importance,
        cn_title=cn_title or title,
        cn_summary=f"第 {index} 条夹具摘要。",
    )


__all__ = [
    "EVIDENCE",
    "PROJECT",
    "PYTHON",
    "RAW",
    "RULE_ESCAPE_SAMPLES",
    "TEMPROOT",
    "TOPIC_DROP_SAMPLES",
    "TOPIC_KEEP_SAMPLES",
    "DEFAULT_EXCLUDE_PATTERNS",
    "base_pipeline_raw",
    "m3",
    "m4",
    "m5",
    "m6",
    "make_news",
    "make_raw_item",
    "run_prefilter_titles",
    "write_and_load",
]
