"""M2 验收 1：预筛正确性。

覆盖：AI 关键词门槛仅对 gate_sources 生效 / 垃圾过滤 / 批内去重 /
空列表禁用门槛 / 真实数据误杀率。
黑盒为主：fixture 种子库 + `aihot enrich --no-llm` + SQLite 观察；
批内去重与门控列表语义用模块级调用复算（CLI 路径因库内唯一键不可达，注明）。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    PROJECT,
    REAL_CONFIG,
    Suite,
    copy_real_db,
    db_query,
    real_db_snapshot,
    reset_enrichment,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "prefilter"

# 独立语义分类（依据验收标准人工推导，不参考产品输出）
EXPECTED_DROP: dict[str, str] = {
    "https://example.com/hn-rust": "与AI无关（hackernews 是门控源且无 AI 关键词）",
    "https://example.com/qbit-hire": "垃圾（招聘）",
    "https://example.com/oa-webinar": "垃圾（webinar/register now）",
    "https://example.com/qbit-course": "垃圾（训练营/报名/限时优惠）",
    "https://example.com/hn-ad": "垃圾（sponsored/promo code）",
}
EXPECTED_KEEP: dict[str, str] = {
    "https://example.com/hn-gpt6": "门控源命中 AI 关键词",
    "https://example.com/qbit-os": "非门控源不检查 AI 关键词",
    "https://example.com/oa-luna": "非门控源",
}
JUNK_URLS = {url for url, why in EXPECTED_DROP.items() if "垃圾" in why}

# 人工核验用的关键词（拉丁词用词边界，避免 "said/email" 里的 "ai" 误判）
_AI_RE = re.compile(
    r"\b(ai|gpt|llm|agent|robot|robotics|neural|gpu|chip|chips|openai|anthropic|gemini|claude|"
    r"llama|qwen|deepseek|mistral|transformer|inference|training|benchmark|dataset)\b",
    re.IGNORECASE,
)
_AI_ZH = (
    "大模型", "人工智能", "模型", "机器人", "算力", "芯片", "推理", "机器学习", "智能体", "微调",
)
_JUNK_RE = re.compile(
    r"招聘|诚聘|内推|简历投递|hiring|join our team|报名|训练营|直播课|公开课|webinar|"
    r"限时|优惠|折扣|免费领取|giveaway|sponsored|促销|代金券|领取优惠券",
    re.IGNORECASE,
)


def fixture_items() -> list[dict]:
    """覆盖门控/垃圾/正常三类；标题故意不含 AI 关键词或有意含之。"""
    return [
        {
            "source_id": "hackernews",
            "title": "Rust 1.99 lands with faster incremental builds",
            "url": "https://example.com/hn-rust",
            "raw_summary": "The compiler team shipped a performance-focused release.",
            "lang": "en",
        },
        {
            "source_id": "hackernews",
            "title": "OpenAI ships GPT-6 Sol to all ChatGPT users",
            "url": "https://example.com/hn-gpt6",
            "raw_summary": "Rollout starts today.",
            "lang": "en",
        },
        {
            "source_id": "qbitai",
            "title": "国产操作系统发布新版本，性能提升 20%",
            "url": "https://example.com/qbit-os",
            "raw_summary": "该版本聚焦桌面体验。",
            "lang": "zh",
        },
        {
            "source_id": "qbitai",
            "title": "招聘｜大模型算法工程师（北京，35-60K）",
            "url": "https://example.com/qbit-hire",
            "raw_summary": "简历投递至 hr@example.com",
            "lang": "zh",
        },
        {
            "source_id": "openai_news",
            "title": "Webinar: register now for the GPT-6 developer day",
            "url": "https://example.com/oa-webinar",
            "raw_summary": "Early bird seats are limited.",
            "lang": "en",
        },
        {
            "source_id": "qbitai",
            "title": "限时优惠：AI 训练营报名开启，名额有限",
            "url": "https://example.com/qbit-course",
            "raw_summary": "在线课程 8 折。",
            "lang": "zh",
        },
        {
            "source_id": "openai_news",
            "title": "Introducing GPT-6 Luna, our most efficient model yet",
            "url": "https://example.com/oa-luna",
            "raw_summary": "Available in the API today.",
            "lang": "en",
        },
        {
            "source_id": "hackernews",
            "title": "Sponsored: rent GPU clusters with promo code SAVE20",
            "url": "https://example.com/hn-ad",
            "raw_summary": "",
            "lang": "en",
        },
    ]


def now_utc() -> datetime:
    return datetime.now(UTC)


def brief(text: str, limit: int = 170) -> str:
    return " ".join(text.split())[:limit]


def cli_flow(suite: Suite, tag: str, *, gate_sources: list[str]) -> tuple[list[dict], str]:
    """跑一次 enrich --no-llm，返回库内条目行与原始输出。"""
    base = workspace(f"{WS}-{tag}")
    db_path = base / "data" / "aihot.db"
    seed_items(db_path, fixture_items())
    pipeline = {
        "prefilter": {
            "gate_sources": gate_sources,
            "blacklist_keywords": [],
            "extra_ai_keywords": [],
        },
        "dedup": {"title_similarity": 88, "max_hours_apart": 72, "llm_refine": False},
        "enrich": {
            "max_items": 200,
            "price_input_per_million": 2.0,
            "price_output_per_million": 8.0,
        },
    }
    cfg = write_config(
        base / "config.yaml", pipeline=pipeline, data_dir=base / "data", log_dir=base / "logs"
    )
    result = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "50", "--top", "8"])
    rows = db_query(
        db_path,
        "SELECT source_id, url, title, category, cn_title, enriched_at FROM items ORDER BY id",
    )
    suite.evidence(
        f"cli-{tag}.txt",
        result.text + "\n\n--- items ---\n" + json.dumps(rows, ensure_ascii=False, indent=2),
    )
    return rows, result.text


def mod_config(tag: str, gate: list[str]) -> Path:
    base = workspace(f"{WS}-{tag}")
    return write_config(
        base / "config.yaml",
        pipeline={
            "prefilter": {
                "gate_sources": gate,
                "blacklist_keywords": [],
                "extra_ai_keywords": [],
            },
            "dedup": {"title_similarity": 88, "max_hours_apart": 72, "llm_refine": False},
        },
        data_dir=base / "data",
        log_dir=base / "logs",
    )


def module_level_checks(suite: Suite) -> None:
    from aihot.config import load_config
    from aihot.models import RawItem
    from aihot.pipeline.prefilter import prefilter_items

    cfg = load_config(mod_config("mod", ["hackernews"]))
    dup = RawItem(
        source_id="qbitai",
        title="AI 大模型发布",
        url="https://example.com/dup",
        published_at=now_utc(),
    )
    result = prefilter_items([dup, dup], cfg)
    suite.check(
        "1e-批内重复去重",
        result.stats.dropped_duplicate == 1 and result.stats.kept == 1,
        f"去重 {result.stats.dropped_duplicate}，保留 {result.stats.kept}"
        "（说明：CLI 路径因 items 唯一键不可达，此项为模块级行为）",
    )
    off_topic = RawItem(
        source_id="hackernews",
        title="Rust 1.99 released",
        url="https://example.com/x1",
        published_at=now_utc(),
    )
    gated = prefilter_items([off_topic], cfg)
    suite.check(
        "1f-门控源无关键词被淘汰（模块级）",
        gated.stats.dropped_offtopic == 1 and gated.stats.kept == 0,
        f"dropped_offtopic={gated.stats.dropped_offtopic}",
    )
    empty_cfg = load_config(mod_config("mod-empty", []))
    ungated = prefilter_items([off_topic], empty_cfg)
    suite.check(
        "1g-空gate_sources放行（模块级）",
        ungated.stats.dropped_offtopic == 0 and ungated.stats.kept == 1,
        f"gate_sources=() → dropped_offtopic={ungated.stats.dropped_offtopic}"
        f"，kept={ungated.stats.kept}",
    )


# 人工核验的「非明显」个案裁定（来自 2026-09-24 真实库 176 条全量复核；按标题前缀匹配）
ADJUDICATION: list[tuple[str, str, str]] = [
    ("TechCrunch Founder Summit", "合理", "TechCrunch 活动议程/招商营销"),
    ("Five AI safety sessions every founder", "合理", "TechCrunch Disrupt 活动议程营销"),
    ("TechCrunch Disrupt 2026: Aaron Edsinger", "合理", "TechCrunch Disrupt 展台活动宣传"),
    ("Exhibit tables added", "合理", "活动招商（last chance）"),
    ("4 days to save up to $200", "合理", "活动促销（save up to / days left）"),
    ("3 days left to save up to $200", "合理", "活动促销（save up to / days left）"),
    ("I asked Meta’s Muse", "误杀", "Meta Muse 属 AI 产品讨论，关键词表未覆盖 Muse"),
    ("Meta’s Muse has a serious 0-day", "误杀", "同上"),
    ("LensVLM", "误杀", "VLM 论文；关键词表未覆盖 VLM"),
    ("Comma's hands-off driving tech", "误杀",
     "自动驾驶（comma.ai）；英文词表只覆盖 autonomous driving"),
    ("Show HN: Npunlock", "误杀（边缘）", "NPU（AI 加速器）工具；词表未覆盖 NPU"),
    ("Tokens too cheap to meter", "误杀（边缘）", "疑似 LLM token 经济学评论"),
    ("The Price of Intelligence Is Falling Rapidly", "误杀（边缘）", "疑似 AI/推理经济学评论"),
]
BORDERLINE_NOT_COUNTED = {
    "Woman Arrested, Dragged Away After Speaking About Flock at City Council Meeting":
        "Flock 为 AI 车牌识别厂商，但文章主题是警务争议",
    "No Sloptober": "疑似 AI 生成内容（slop）文化评论，证据不足",
    "US criticises Australia's proposed algorithm opt-out laws as 'censorship'":
        "主题为社交媒体算法监管，非 AI",
}


def judge_drop(reason: str, title: str) -> tuple[str, str]:
    """人工核验：返回 (裁定, 依据)。裁定表优先，其次规则判定。"""
    for prefix, verdict, basis in ADJUDICATION:
        if title.startswith(prefix):
            return verdict, basis
    if title in BORDERLINE_NOT_COUNTED:
        return "合理", BORDERLINE_NOT_COUNTED[title]
    if reason == "垃圾内容":
        return ("合理", "命中垃圾词表") if _JUNK_RE.search(title) else ("误杀", "垃圾词表误命中")
    if reason == "与AI无关":
        if _JUNK_RE.search(title):
            return "合理", "命中垃圾词表"
        if _AI_RE.search(title) or any(h in title for h in _AI_ZH):
            return "误杀", "标题含 AI 相关信号但未命中词表"
        return "合理", "与 AI 无关"
    return "存疑", "未知原因"


def real_review(suite: Suite) -> dict:
    """真实库全量：预筛淘汰项逐条人工核验 + CLI 交叉验证。"""
    from aihot.config import load_config
    from aihot.models import RawItem
    from aihot.pipeline.prefilter import prefilter_items
    from aihot.utils import parse_datetime

    cfg = load_config(REAL_CONFIG)
    rows = db_query(
        PROJECT / "data" / "aihot.db",
        "SELECT source_id, title, url, raw_summary, published_at, lang, extra"
        " FROM items ORDER BY id",
    )
    items = [
        RawItem(
            source_id=row["source_id"],
            title=row["title"],
            url=row["url"],
            published_at=parse_datetime(row["published_at"]) or now_utc(),
            raw_summary=row["raw_summary"] or "",
            lang=row["lang"] or "",
            extra=json.loads(row["extra"] or "{}"),
        )
        for row in rows
    ]
    result = prefilter_items(items, cfg)
    dropped: list[dict] = []
    for row, item in zip(rows, items, strict=True):
        single = prefilter_items([item], cfg)
        if single.stats.kept:
            continue
        reason = (
            "垃圾内容"
            if single.stats.dropped_blacklist
            else ("与AI无关" if single.stats.dropped_offtopic else "其他")
        )
        dropped.append(
            {
                "source_id": item.source_id,
                "title": item.title,
                "published_at": row["published_at"],
                "reason": reason,
                "verdict": judge_drop(reason, item.title)[0],
                "basis": judge_drop(reason, item.title)[1],
            }
        )
    correct = sum(1 for entry in dropped if entry["verdict"].startswith("合理"))
    wrong = [entry for entry in dropped if not entry["verdict"].startswith("合理")]
    total = len(dropped)
    miskill = (total - correct) / total * 100 if total else 0.0
    suite.check(
        "1h-真实数据预筛误杀率",
        miskill <= 10.0,
        f"库内 {len(items)} 条 → 淘汰 {total} 条（垃圾 {result.stats.dropped_blacklist}"
        f" · 与AI无关 {result.stats.dropped_offtopic}"
        f" · 批内重复 {result.stats.dropped_duplicate}），"
        f"人工核验误杀 {len(wrong)} 条，误杀率 {miskill:.1f}%"
        f"（占全库 {len(wrong) / len(items) * 100:.1f}%；阈值为 ≤10%，超出升级 P1）",
    )
    suite.evidence_json(
        "1h-real-prefilter-review.json",
        {
            "total_items": len(items),
            "stats": {
                "total": result.stats.total,
                "kept": result.stats.kept,
                "blacklist": result.stats.dropped_blacklist,
                "offtopic": result.stats.dropped_offtopic,
                "duplicate": result.stats.dropped_duplicate,
            },
            "dropped": dropped,
            "wrong_drops": wrong,
            "borderline_not_counted": BORDERLINE_NOT_COUNTED,
            "samples": result.stats.dropped_samples,
        },
    )

    # CLI 交叉验证：真实库副本跑 enrich --no-llm，统计 category='过滤'
    copy_base = workspace(f"{WS}-real-copy")
    db_path = copy_real_db(copy_base / "data" / "aihot.db")
    reset_enrichment(db_path)
    cfg_path = write_config(
        copy_base / "config.yaml",
        sources=None,
        pipeline={
            "prefilter": {
                "gate_sources": ["hackernews"],
                "blacklist_keywords": [],
                "extra_ai_keywords": [],
            },
            "dedup": {"title_similarity": 88, "max_hours_apart": 72, "llm_refine": False},
            "enrich": {
                "max_items": 200,
                "price_input_per_million": 2.0,
                "price_output_per_million": 8.0,
            },
        },
        data_dir=copy_base / "data",
        log_dir=copy_base / "logs",
    )
    cli = run_cli(
        ["enrich", "--no-llm", "--config", str(cfg_path), "--limit", "400", "--top", "3"]
    )
    cli_filtered = db_query(
        db_path, "SELECT COUNT(*) AS n FROM items WHERE category='过滤'"
    )[0]["n"]
    cli_enriched = db_query(
        db_path, "SELECT COUNT(*) AS n FROM items WHERE enriched_at IS NOT NULL"
    )[0]["n"]
    suite.check(
        "1i-CLI路径淘汰数与独立复算一致",
        cli_filtered == total and cli_enriched == len(items),
        f"CLI 标记过滤 {cli_filtered} 条（独立复算淘汰 {total} 条）；"
        f"enriched_at 非空 {cli_enriched}/{len(items)}",
    )
    suite.evidence("1i-real-cli.txt", cli.text)
    return {"dropped": total, "miskill": miskill, "items": len(items)}


def main() -> int:
    suite = Suite("prefilter")
    print("== case A/B：门控与垃圾过滤（enrich --no-llm，黑盒） ==")
    rows, output = cli_flow(suite, "gate-hn", gate_sources=["hackernews"])
    suite.check(
        "1a-命令成功且打印预筛行",
        "预筛:" in output and "exit=0" in output,
        brief(output),
    )
    dropped = {row["url"] for row in rows if row["category"] == "过滤"}
    kept = {row["url"] for row in rows if row["category"] != "过滤"}
    suite.check(
        "1b-门控仅对gate_sources生效",
        "https://example.com/hn-rust" in dropped
        and "https://example.com/qbit-os" in kept
        and "https://example.com/hn-gpt6" in kept,
        f"gate=[hackernews]：hn-rust 淘汰={('https://example.com/hn-rust' in dropped)}，"
        f"qbit-os(非门控) 保留={('https://example.com/qbit-os' in kept)}，"
        f"hn-gpt6(命中关键词) 保留={('https://example.com/hn-gpt6' in kept)}",
    )
    suite.check(
        "1c-垃圾过滤覆盖所有源",
        dropped >= JUNK_URLS,
        f"招聘/课程/webinar/促销 4 条全部淘汰={dropped >= JUNK_URLS}",
    )
    suite.check(
        "1d-淘汰集合与独立判定一致",
        dropped == set(EXPECTED_DROP) and kept == set(EXPECTED_KEEP),
        f"实际淘汰={sorted(u.rsplit('/', 1)[-1] for u in dropped)}；"
        f"期望淘汰={sorted(u.rsplit('/', 1)[-1] for u in EXPECTED_DROP)}",
    )

    print("== case C：gate_sources: [] 禁用门槛（黑盒） ==")
    rows2, output2 = cli_flow(suite, "gate-empty", gate_sources=[])
    dropped2 = {row["url"] for row in rows2 if row["category"] == "过滤"}
    kept2 = {row["url"] for row in rows2 if row["category"] != "过滤"}
    suite.check(
        "1j-空列表禁用AI门槛",
        "https://example.com/hn-rust" in kept2,
        f"gate=[]：hn-rust（无 AI 关键词）保留={('https://example.com/hn-rust' in kept2)}",
    )
    suite.check(
        "1k-空门槛不影响垃圾过滤",
        dropped2 == JUNK_URLS and "https://example.com/qbit-os" in kept2,
        f"gate=[] 淘汰集合={sorted(u.rsplit('/', 1)[-1] for u in dropped2)}",
    )

    print("== case D：批内去重与门控语义（模块级复算） ==")
    module_level_checks(suite)

    print("== case E：真实数据抽样核验（全量淘汰项人工复核） ==")
    real_review(suite)

    snapshot = real_db_snapshot()
    suite.check(
        "1z-真实库只读守护",
        snapshot["items"] == REAL_ITEMS_BASELINE,
        f"items={snapshot['items']}（测试开始时 {REAL_ITEMS_BASELINE}）",
    )
    suite.evidence_json("1z-real-db-snapshot.json", snapshot)
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0


if __name__ == "__main__":
    raise SystemExit(main())
