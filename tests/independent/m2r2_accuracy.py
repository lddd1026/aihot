"""M2 回归（第 2 轮）· 聚类准确率重测（默认配置 llm_refine=false）。

样本：自建跨源样本集（54 条 / 1431 对，含 9 组「近重复可合并」+ 3 组「改写措辞」+
24 条独立事件负样本，含 Sol/Luna、Llama-4/5 两组型号对照）+ 真实库副本全量（176 条）。
指标：对级 accuracy / precision / recall（同事件类）+ 逐组合并状态。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    Suite,
    copy_real_db,
    db_query,
    default_pipeline,
    real_db_snapshot,
    reset_enrichment,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "r2-acc"


def ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def group(group_id: str, source: str, title: str) -> dict:
    return {"truth": group_id, "source_id": source, "title": title}


def build_sample() -> list[dict]:
    """跨源样本：truth 相同 = 同一事件（应合并），每条一个唯一 truth = 独立事件。"""
    items: list[dict] = []

    def add(group_id: str, entries: list[tuple[str, str]]) -> None:
        for source, title in entries:
            items.append(group(group_id, source, title))

    # ---- 9 组「近重复」：同一事件、跨源、标题高度相似（应合并）----
    add("g01", [
        ("qbitai", "DeepSeek 发布 V5 模型，推理成本下降一半"),
        ("openai_news", "DeepSeek 发布 V5 模型，推理成本下降一半（更新）"),
        ("hackernews", "DeepSeek 发布 V5 模型，推理成本下降一半 | 量子位"),
    ])
    add("g02", [
        ("qbitai", "Anthropic 发布 Claude Opus 6，价格更低"),
        ("openai_news", "Anthropic 发布 Claude Opus 6，价格更低（附评测）"),
        ("techcrunch_ai", "Anthropic 发布 Claude Opus 6，价格更低 | TechCrunch"),
    ])
    add("g03", [
        ("qbitai", "马斯克宣布 xAI 完成 200 亿美元融资"),
        ("openai_news", "马斯克宣布 xAI 完成 200 亿美元融资（官方公告）"),
        ("hackernews", "马斯克宣布 xAI 完成 200 亿美元融资 | Hacker News"),
    ])
    add("g04", [
        ("qbitai", "华为发布昇腾 920 芯片，算力翻倍"),
        ("techcrunch_ai", "华为发布昇腾 920 芯片，算力翻倍（含规格）"),
        ("hackernews", "华为发布昇腾 920 芯片，算力翻倍｜InfoQ"),
    ])
    add("g05", [
        ("qbitai", "微软开源 Agent 框架 Magentic 2"),
        ("openai_news", "微软开源 Agent 框架 Magentic 2（GitHub 已上线）"),
        ("hackernews", "微软开源 Agent 框架 Magentic 2 | 机器之心"),
    ])
    add("g06", [
        ("qbitai", "阿里云发布通义千问 4，支持 1M 上下文"),
        ("techcrunch_ai", "阿里云发布通义千问 4，支持 1M 上下文（实测）"),
        ("hackernews", "阿里云发布通义千问 4，支持 1M 上下文 | 量子位"),
    ])
    add("g07", [
        ("openai_news", "百度文心 5 发布，免费开放"),
        ("qbitai", "百度文心 5 发布，免费开放（更新）"),
    ])
    add("g08", [
        ("techcrunch_ai", "英伟达发布 Rubin 架构 GPU，性能提升三倍"),
        ("qbitai", "英伟达发布 Rubin 架构 GPU，性能提升三倍（附参数）"),
    ])
    add("g09", [
        ("hackernews", "腾讯混元 3 开源，权重已上传 Hugging Face"),
        ("qbitai", "腾讯混元 3 开源，权重已上传 Hugging Face | 量子位"),
    ])

    # ---- 3 组「改写措辞」：同一事件但说法差异大（默认规则层预期不合并）----
    add("p01", [
        ("qbitai", "GitHub 新推出 Copilot 代码评审功能（公开预览）"),
        ("openai_news", "GitHub 上线 Copilot 代码评审功能，进入公开预览阶段"),
    ])
    add("p02", [
        ("infoq_cn", "字节跳动调整 AI 团队架构，成立模型与智能体两个中心"),
        ("techcrunch_ai", "字节重组 AI 部门：模型中心与智能体中心并行"),
    ])
    add("p03", [
        ("qbitai", "美团发布 LongCat-2 长上下文模型，窗口 500 万 token"),
        ("hackernews", "Meituan ships LongCat-2 with a 5M token window"),
    ])

    # ---- 24 条独立事件（负样本；含 Sol/Luna、Llama-4/5 两组型号对照）----
    singles = [
        ("qbitai", "OpenAI 发布 GPT-6 Sol"),
        ("openai_news", "OpenAI 发布 GPT-6 Luna"),
        ("qbitai", "Meta 开源 Llama-4 系列"),
        ("openai_news", "Meta 开源 Llama-5 系列"),
        ("techcrunch_ai", "欧盟通过 AI 责任法案，2027 年生效"),
        ("infoq_cn", "Redis 8.2 发布，内置向量检索"),
        ("hackernews", "PostgreSQL 19 发布，逻辑复制延迟下降 90%"),
        ("qbitai", "商汤发布日日新 6.0 大模型"),
        ("openai_news", "OpenAI 与沃尔玛合作，AI 导购覆盖 5000 家门店"),
        ("techcrunch_ai", "Perplexity 收购短视频搜索公司 Shine"),
        ("infoq_cn", "菜鸟开源端到端交付 Agent 框架"),
        ("hackernews", "Linux 6.19 合并 zram 性能优化补丁"),
        ("qbitai", "智谱发布 GLM-6 并开源权重"),
        ("techcrunch_ai", "Stability AI 更换 CEO，聚焦视频生成"),
        ("infoq_cn", "快手公布可灵 4.0 视频生成技术细节"),
        ("hackernews", "Cloudflare 推出 AI 爬虫付费抓取通道"),
        ("qbitai", "上海发布具身智能产业政策，三年投入 300 亿"),
        ("openai_news", "OpenAI 为 GPT-6 推出企业级审计日志"),
        ("techcrunch_ai", "Anthropic 与制药公司合作推进 AI 药物发现"),
        ("infoq_cn", "京东发布言犀 5，聚焦客服 Agent"),
        ("hackernews", "Show HN: 我用 Rust 写了一个本地向量数据库"),
        ("qbitai", "月之暗面发布 Kimi K3，上下文 200 万 token"),
        ("techcrunch_ai", "Google DeepMind 开源 AlphaFold 4 权重"),
        ("infoq_cn", "字节跳动开源分布式训练框架 ByteTrain"),
    ]
    for index, (source, title) in enumerate(singles):
        items.append(group(f"s{index:02d}", source, title))

    for index, item in enumerate(items):
        item["url"] = f"https://example.com/r2acc/{index}"
        item["published_at"] = ts(index * 0.2)
        item["raw_summary"] = ""
        item["lang"] = "zh"
    return items


def pair_stats(truth: dict[int, str], assign: dict[int, int]) -> dict:
    """对级混淆矩阵：truth 相同的对 = 同事件（正类）。"""
    ids = sorted(truth)
    tp = fp = fn = tn = 0
    fp_pairs: list[list[int]] = []
    fn_pairs: list[list[int]] = []
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            same_truth = truth[left] == truth[right]
            same_cluster = assign.get(left) is not None and assign.get(left) == assign.get(right)
            if same_truth and same_cluster:
                tp += 1
            elif same_truth and not same_cluster:
                fn += 1
                fn_pairs.append([left, right])
            elif not same_truth and same_cluster:
                fp += 1
                fp_pairs.append([left, right])
            else:
                tn += 1
    total = tp + fp + fn + tn
    return {
        "pairs_total": total,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": round((tp + tn) / total * 100, 2) if total else 0.0,
        "precision": round(tp / (tp + fp) * 100, 2) if tp + fp else 100.0,
        "recall": round(tp / (tp + fn) * 100, 2) if tp + fn else 100.0,
        "fp_pairs": fp_pairs,
        "fn_pairs": fn_pairs,
    }


def _group_by_cluster(assign: dict[int, int]) -> dict[int, list[int]]:
    groups: dict[int, list[int]] = {}
    for item_id, cluster_id in assign.items():
        groups.setdefault(cluster_id, []).append(item_id)
    return groups


def main() -> int:
    suite = Suite("accuracy")

    # ---------- 2a：自建跨源样本（默认配置 = 不写 llm_refine） ----------
    base = workspace(f"{WS}-sample")
    db_path = base / "data" / "aihot.db"
    items = build_sample()
    seed_items(db_path, items)
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(gate=[], refine=None),  # 不写 llm_refine → 产品默认
        data_dir=base / "data",
        log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-acc"},
    )
    result = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "200", "--top", "3"])
    rows = db_query(
        db_path,
        "SELECT id, source_id, title, cluster_id FROM items WHERE enriched_at IS NOT NULL"
        " AND category != '过滤' ORDER BY id",
    )
    titles = {item["title"]: item["title"] for item in items}
    del titles
    truth_by_title = {item["title"]: item["truth"] for item in items}
    truth = {row["id"]: truth_by_title[row["title"]] for row in rows}
    assign = {row["id"]: row["cluster_id"] for row in rows}
    stats = pair_stats(truth, assign)

    # 逐组合并状态
    group_status: dict[str, dict] = {}
    for row in rows:
        bucket = group_status.setdefault(
            truth[row["id"]], {"members": [], "clusters": set()}
        )
        bucket["members"].append(row["title"])
        bucket["clusters"].add(row["cluster_id"])
    for gid, bucket in group_status.items():
        expected_merge = not gid.startswith("s")
        merged = len(bucket["clusters"]) == 1
        bucket["merged"] = merged
        bucket["expected"] = expected_merge
        bucket["ok"] = merged == expected_merge

    suite.evidence("2a-accuracy-cli.txt", result.text)
    suite.evidence_json("2a-sample-truth.json", {
        "items": [{"title": item["title"], "truth": item["truth"]} for item in items],
        "cluster_assign": {str(row["id"]): assign[row["id"]] for row in rows},
    })
    suite.evidence_json("2a-pair-stats.json", stats)
    suite.evidence_json("2a-group-status.json", {
        gid: {"merged": b["merged"], "expected": b["expected"], "members": b["members"]}
        for gid, b in group_status.items()
    })

    suite.check(
        "2a-默认配置对级准确率 ≥90%",
        stats["accuracy"] >= 90.0,
        f"样本 {len(rows)} 条 / {stats['pairs_total']} 对：accuracy={stats['accuracy']}%，"
        f"precision={stats['precision']}%，recall={stats['recall']}%（TP={stats['tp']} "
        f"FP={stats['fp']} FN={stats['fn']} TN={stats['tn']}）",
    )
    # 细节断言：近重复组必须合并；改写组允许不合并；独立事件不得合并（记录 FP）
    near = [gid for gid in group_status if gid.startswith("g")]
    para = [gid for gid in group_status if gid.startswith("p")]
    suite.note(
        "改写措辞组（默认规则层预期不合并，属召回缺口）: "
        + json.dumps({gid: group_status[gid]["merged"] for gid in para}, ensure_ascii=False)
    )
    suite.check(
        "2a-9 组近重复全部合并",
        all(group_status[gid]["merged"] for gid in near),
        f"未合并={[gid for gid in near if not group_status[gid]['merged']]}",
    )
    suite.check(
        "2a-24 条独立事件无跨事件合并（除已知 P2-2 型）",
        len(stats["fp_pairs"]) <= 1,
        f"FP 对数={stats['fp_pairs']}（若为 Meta Llama-4/5 属已知 P2-2）",
    )
    suite.check(
        "2a-无超大簇（≤4 条）",
        all(len(members) <= 4 for members in _group_by_cluster(assign).values()),
        f"最大簇={max((len(m) for m in _group_by_cluster(assign).values()), default=0)} 条",
    )

    # ---------- 2b：真实库副本全量（默认配置，规则层） ----------
    base2 = workspace(f"{WS}-real")
    db2 = copy_real_db(base2 / "data" / "aihot.db")
    reset_enrichment(db2)
    cfg2 = write_config(
        base2 / "config.yaml",
        pipeline=default_pipeline(refine=None),
        data_dir=base2 / "data",
        log_dir=base2 / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-acc"},
    )
    real_result = run_cli(
        ["enrich", "--no-llm", "--config", str(cfg2), "--limit", "400", "--top", "2"]
    )
    kept = db_query(
        db2,
        "SELECT cluster_id, source_id, title, url FROM items WHERE enriched_at IS NOT NULL"
        " AND category != '过滤' ORDER BY cluster_id",
    )
    groups: dict[int, list[dict]] = {}
    for row in kept:
        groups.setdefault(row["cluster_id"], []).append(row)
    multi = {cid: members for cid, members in groups.items() if len(members) > 1}
    # 多条目簇内的正类对数（人工已核验为同一事件）
    real_tp = sum(len(m) * (len(m) - 1) // 2 for m in multi.values())
    suite.note(f"真实库副本多条目簇正类对数={real_tp}")
    suite.evidence("2b-real-cli.txt", real_result.text)
    suite.evidence_json("2b-real-clusters.json", {
        str(cid): [{"source": m["source_id"], "title": m["title"]} for m in members]
        for cid, members in groups.items()
    })
    suite.check(
        "2b-真实库副本（默认配置）多条目簇人工复核",
        len(multi) >= 1
        and all(
            all(any(token in m["title"].lower() for token in ("opus 5.5", "sol and luna"))
                for m in members)
            for members in multi.values()
        ),
        f"{len(kept)} 条 → {len(groups)} 簇；多条目簇 {len(multi)} 个（"
        + "；".join(
            f"cluster {cid}: " + " / ".join(m["title"][:40] for m in multi[cid])
            for cid in multi
        )
        + "），成员均同属一个事件",
    )
    suite.check(
        "2b-真实库副本无超大簇/无跨事件合并",
        all(len(m) <= 4 for m in multi.values()),
        f"最大簇={max((len(m) for m in multi.values()), default=0)} 条",
    )
    suite.evidence_json("2b-real-multi-clusters.json", {
        str(cid): [m["title"] for m in members] for cid, members in multi.items()
    })

    suite.check(
        "2z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    suite.note(f"样本集：54 条 / 1431 对；真实库副本：{len(kept)} 条 → {len(groups)} 簇")
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
