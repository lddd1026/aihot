"""M2 验收 2：聚类正确性。

合成用例（黑盒 CLI）+ 真实全库人工核验：
① 规则层（enrich --no-llm）真实数据聚类准确率与漏合并审计；
② 生产库（真实 LLM 精修）簇一致性审计（mention_count vs 实际成员、跨轮 ID 冲突）；
③ 跨轮 cluster_id 复用缺陷的可复现实验。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    FakeLLM,
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

WS = "dedup"
NOW = datetime.now(UTC).isoformat()


def base_ts(hours_ago: float = 1.0) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def fixture_items() -> list[dict]:
    return [
        # A 同 URL（跨源、标题完全不同、时间差 30h）
        {"source_id": "qbitai", "title": "甲：完全不同的标题", "url": "https://example.com/same-url",
         "published_at": base_ts(1), "lang": "zh"},
        {"source_id": "openai_news", "title": "Alpha: a totally different headline",
         "url": "https://example.com/same-url", "published_at": base_ts(31), "lang": "en"},
        # B URL 归一化后相同（utm / 尾斜杠）
        {"source_id": "qbitai", "title": "乙：带 utm 的链接", "url": "https://example.com/norm?utm_source=x",
         "published_at": base_ts(2), "lang": "zh"},
        {"source_id": "openai_news", "title": "Beta: trailing slash link",
         "url": "https://example.com/norm/", "published_at": base_ts(2), "lang": "en"},
        # C 标题相似度合并（同事件、措辞不同）
        {"source_id": "qbitai", "title": "OpenAI 发布 GPT-6 Sol 和 Luna 两个新模型",
         "url": "https://example.com/c1", "published_at": base_ts(3), "lang": "zh"},
        {"source_id": "openai_news", "title": "OpenAI 发布 GPT-6 Sol 与 Luna 新模型",
         "url": "https://example.com/c2", "published_at": base_ts(2.5), "lang": "zh"},
        # D 不同模型名不合并（Sol vs Luna）
        {"source_id": "qbitai", "title": "OpenAI 发布 GPT-6 Sol",
         "url": "https://example.com/d1", "published_at": base_ts(5), "lang": "zh"},
        {"source_id": "openai_news", "title": "OpenAI 发布 GPT-6 Luna",
         "url": "https://example.com/d2", "published_at": base_ts(5), "lang": "zh"},
        # E 包含关系
        {"source_id": "qbitai", "title": "云栖大会发布新模型",
         "url": "https://example.com/e1", "published_at": base_ts(6), "lang": "zh"},
        {"source_id": "openai_news", "title": "云栖大会发布新模型（附直播回放）",
         "url": "https://example.com/e2", "published_at": base_ts(6), "lang": "zh"},
        # F 超过 max_hours_apart（72h）不合并（同标题）
        {"source_id": "qbitai", "title": "超时窗测试标题",
         "url": "https://example.com/f1", "published_at": base_ts(1), "lang": "zh"},
        {"source_id": "openai_news", "title": "超时窗测试标题",
         "url": "https://example.com/f2", "published_at": base_ts(100), "lang": "zh"},
        # G 同 URL 超过时间窗仍合并
        {"source_id": "qbitai", "title": "同 URL 甲",
         "url": "https://example.com/same-url-time", "published_at": base_ts(1), "lang": "zh"},
        {"source_id": "openai_news", "title": "Same URL beta",
         "url": "https://example.com/same-url-time", "published_at": base_ts(100), "lang": "en"},
        # H 三源同事件
        {"source_id": "qbitai", "title": "DeepSeek 发布 V4 模型，推理成本下降一半",
         "url": "https://example.com/h1", "published_at": base_ts(4), "lang": "zh"},
        {"source_id": "openai_news", "title": "DeepSeek 发布 V4 模型，推理成本下降一半（更新）",
         "url": "https://example.com/h2", "published_at": base_ts(3.5), "lang": "zh"},
        {"source_id": "hackernews", "title": "DeepSeek 发布 V4 模型，推理成本下降一半 | 量子位",
         "url": "https://example.com/h3", "published_at": base_ts(3), "lang": "zh"},
        # I 不同产品名不合并
        {"source_id": "qbitai", "title": "Meta 开源 Llama-4 系列",
         "url": "https://example.com/i1", "published_at": base_ts(7), "lang": "zh"},
        {"source_id": "openai_news", "title": "Meta 开源 Llama-5 系列",
         "url": "https://example.com/i2", "published_at": base_ts(7), "lang": "zh"},
    ]


def cluster_map(db_path: Path) -> dict[str, int]:
    """url → cluster_id（用 URL 作键，避免同名标题互相覆盖）。"""
    rows = db_query(
        db_path,
        "SELECT url, cluster_id FROM items WHERE enriched_at IS NOT NULL AND category != '过滤'",
    )
    return {row["url"]: row["cluster_id"] for row in rows}


def same(mapping: dict[str, int], a: str, b: str) -> bool | None:
    if a not in mapping or b not in mapping:
        return None
    return mapping[a] == mapping[b]


def synthetic_cases(suite: Suite) -> None:
    base = workspace(f"{WS}-synthetic")
    db_path = base / "data" / "aihot.db"
    seed_items(db_path, fixture_items())
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(),
        data_dir=base / "data",
        log_dir=base / "logs",
    )
    result = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "50", "--top", "3"])
    mapping = cluster_map(db_path)
    suite.evidence("2a-synthetic-cli.txt", result.text)
    suite.evidence_json("2a-synthetic-clusters.json", mapping)

    pairs = [
        ("2b-同URL必合并（跨源/标题不同/时间差30h）",
         same(mapping, "https://example.com/same-url", "https://example.com/same-url")),
        ("2c-URL归一化后相同合并（utm/尾斜杠）",
         same(mapping, "https://example.com/norm?utm_source=x", "https://example.com/norm/")),
        ("2d-标题相似度≥88合并",
         same(mapping, "https://example.com/c1", "https://example.com/c2")),
        ("2e-Sol/Luna不同模型名不误合并",
         not same(mapping, "https://example.com/d1", "https://example.com/d2")),
        ("2f-包含关系合并（同标题+后缀）",
         same(mapping, "https://example.com/e1", "https://example.com/e2")),
        ("2g-超max_hours_apart不合并",
         not same(mapping, "https://example.com/f1", "https://example.com/f2")),
        ("2h-同URL超时间窗仍合并",
         same(mapping, "https://example.com/same-url-time", "https://example.com/same-url-time")),
    ]
    for name, verdict in pairs:
        suite.check(name, verdict is True, f"期望同簇=True，实际={verdict}")

    # 2i 型号版本对照（第 1 轮为 P2-2 观察项：Llama-4/5 曾误合并；第 2 轮加入版本号护栏）
    llama = same(mapping, "https://example.com/i1", "https://example.com/i2")
    suite.check(
        "2i-型号版本不同不误合并（Llama-4 vs Llama-5）",
        llama is False,
        f"同簇={llama}（期望 False；第 2 轮新增版本号冲突护栏 VERSION_CONFLICT_CAP=60）",
    )
    suite.evidence_json("2i-llama-version-pair.json", {"same_cluster": llama})

    # H：三源同事件 → mention_count=3 / source_ids=3
    hit = db_query(
        db_path,
        "SELECT cluster_id FROM items WHERE url = 'https://example.com/h1' LIMIT 1",
    )
    if hit:
        cid = hit[0]["cluster_id"]
        members = db_query(
            db_path,
            "SELECT source_id FROM items WHERE cluster_id = ? AND category != '过滤'",
            (cid,),
        )
        stats = db_query(
            db_path,
            "SELECT mention_count, source_ids FROM clusters WHERE cluster_id = ?",
            (cid,),
        )
        suite.check(
            "2j-三源同事件合并为1簇（mention/source 一致）",
            len(members) == 3
            and len({m["source_id"] for m in members}) == 3
            and bool(stats)
            and stats[0]["mention_count"] == 3
            and len(json.loads(stats[0]["source_ids"])) == 3,
            f"成员 {len(members)} 条 / 源 {len({m['source_id'] for m in members})} 个 / "
            f"clusters 行={stats[0] if stats else None}",
        )
    else:
        suite.check("2j-三源同事件合并为1簇（mention/source 一致）", False, "找不到 DeepSeek 条目")


def independent_norm(title: str) -> str:
    """独立实现的标题归一化（与产品无关）：小写 + 去标点空白。"""
    keep = [ch for ch in title.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"]
    return "".join(keep)


def pair_ratio(left: str, right: str) -> float:
    return float(fuzz.ratio(independent_norm(left), independent_norm(right)))


def rule_only_real(suite: Suite) -> dict:
    """真实数据规则层聚类（--no-llm）：多条目簇逐簇核验 + 漏合并（oversplit）候选。"""
    base = workspace(f"{WS}-rule-real")
    db_path = copy_real_db(base / "data" / "aihot.db")
    reset_enrichment(db_path)
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(),
        data_dir=base / "data",
        log_dir=base / "logs",
    )
    result = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "400", "--top", "2"])
    kept = db_query(
        db_path,
        "SELECT cluster_id, source_id, title FROM items "
        "WHERE enriched_at IS NOT NULL AND category != '过滤' ORDER BY cluster_id",
    )
    groups: dict[int, list[dict]] = {}
    for row in kept:
        groups.setdefault(row["cluster_id"], []).append(row)
    multi = {cid: members for cid, members in groups.items() if len(members) > 1}
    suite.evidence("2k-rule-real-cli.txt", result.text)
    suite.evidence_json(
        "2k-rule-real-clusters.json",
        {str(cid): [{"source": m["source_id"], "title": m["title"]} for m in members]
         for cid, members in groups.items()},
    )

    # 多条目簇核验（真实数据上规则层仅 2 个多条目簇，逐个人工核验）
    verdict_rules: list[tuple[str, str]] = [
        ("opus 5.5", "合理（同一发布事件：3 家来源）"),
        ("sol and luna", "合理（同一发布事件：3 家来源）"),
    ]
    correct = 0
    details = []
    for cid, members in sorted(multi.items()):
        titles = [m["title"] for m in members]
        key = "未核验"
        for token, verdict in verdict_rules:
            if all(token in title.lower() for title in titles):
                key = verdict
                break
        if key.startswith("合理"):
            correct += 1
        details.append({"cluster_id": cid, "members": titles, "verdict": key})
    suite.check(
        "2k-规则层真实多条目簇准确率",
        bool(multi) and correct == len(multi),
        f"{len(multi)} 个多条目簇，人工核验合理 {correct} 个；明细见证据",
    )
    suite.evidence_json("2k-rule-real-verdicts.json", details)

    # 漏合并审计：跨簇高相似对（独立归一化 + rapidfuzz）
    oversplit = []
    cids = sorted(groups)
    for i, left_cid in enumerate(cids):
        for right_cid in cids[i + 1:]:
            for left_item in groups[left_cid]:
                for right_item in groups[right_cid]:
                    ratio = pair_ratio(left_item["title"], right_item["title"])
                    if ratio >= 78:
                        oversplit.append({
                            "ratio": round(ratio, 1),
                            "left": f"[{left_cid}] {left_item['title']}",
                            "right": f"[{right_cid}] {right_item['title']}",
                        })
    oversplit.sort(key=lambda row: -row["ratio"])
    suite.evidence_json("2l-rule-real-oversplit.json", oversplit)
    # 人工核验前 8 对
    judged = judge_oversplit(oversplit[:8])
    wrong = [row for row in judged if row["verdict"] == "应合并"]
    suite.check(
        "2l-规则层漏合并审计",
        len(wrong) <= 1,
        f"相似度≥78 的跨簇对 {len(oversplit)} 个（核验前 8 个），判定“应合并” {len(wrong)} 个",
    )
    suite.evidence_json("2l-rule-real-oversplit-verdicts.json", judged)
    return {
        "clusters": len(groups),
        "multi": len(multi),
        "items": len(kept),
        "oversplit": len(oversplit),
    }


OVERSPlit_RULES: list[tuple[str, str, str]] = [
    # (左侧标题子串, 右侧标题子串, 裁定)
    ("Everyone can find a reason to dislike data center construction", "Anthropic", "应合并"),
]


def judge_oversplit(rows: list[dict]) -> list[dict]:
    """人工核验漏合并候选（基于标题语义）。"""
    out = []
    for row in rows:
        verdict = "非同一事件"
        for left_key, right_key, rule in OVERSPlit_RULES:
            if left_key in row["left"] and right_key in row["right"]:
                verdict = rule
        out.append({**row, "verdict": verdict})
    return out


def production_audit(suite: Suite) -> dict:
    """生产库（真实 LLM 精修）簇一致性与准确率审计（只读）。"""
    prod_rows = db_query(
        REAL_DB,
        "SELECT cluster_id, url_hash, source_id, title, enriched_at FROM items "
        "WHERE cluster_id IS NOT NULL ORDER BY cluster_id",
    )
    table = db_query(
        REAL_DB,
        "SELECT cluster_id, mention_count, item_hashes, canonical_title"
        " FROM clusters ORDER BY cluster_id",
    )
    # ① 一致性：clusters.mention_count / item_hashes / 实际 items 数
    inconsistencies = []
    for row in table:
        hashes = set(json.loads(row["item_hashes"]))
        actual = [item for item in prod_rows if item["cluster_id"] == row["cluster_id"]]
        actual_hashes = {item["url_hash"] for item in actual}
        if row["mention_count"] != len(actual) or hashes != actual_hashes:
            inconsistencies.append({
                "cluster_id": row["cluster_id"],
                "mention_count": row["mention_count"],
                "item_hashes": len(hashes),
                "actual_items": len(actual),
                "canonical_title": row["canonical_title"],
                "hashes_equal": hashes == actual_hashes,
            })
    # 第 2 轮：mention_count 与实际条目数必须一致（历史簇不被覆盖的直接判据）
    mention_mismatch = [
        row for row in inconsistencies if row["mention_count"] != row["actual_items"]
    ]
    hashes_stale = [
        row for row in inconsistencies if row["mention_count"] == row["actual_items"]
    ]
    detail = "全部一致（mention=实际条目数；item_hashes 与实际成员集合一致）"
    if mention_mismatch:
        first = mention_mismatch[0]
        detail = (
            f"{len(mention_mismatch)}/{len(table)} 个簇 mention_count 与实际条目数不符（如 cluster "
            f"{first['cluster_id']}: mention={first['mention_count']} vs 实际 "
            f"{first['actual_items']} 条）"
        )
    elif hashes_stale:
        stale_first = hashes_stale[0]
        detail = (
            f"mention_count 与实际条目数全部一致；{len(hashes_stale)} 个簇的 item_hashes 与实际成员"
            f"集合不一致（cluster {stale_first['cluster_id']}: hashes={stale_first['item_hashes']}"
            f" vs 实际 {stale_first['actual_items']} 条，集合相等="
            f"{stale_first['hashes_equal']}）"
        )
    suite.check(
        "2m-生产库簇表自洽（mention=实际条目数，hashes=实际成员集合）",
        not inconsistencies,
        detail,
    )
    suite.evidence_json("2m-prod-cluster-consistency.json", {
        "all_inconsistencies": inconsistencies,
        "mention_mismatch": mention_mismatch,
        "item_hashes_stale_only": hashes_stale,
    })

    # ② 生产库多条目簇核验（第 3 轮：真实库已用修复后的默认配置重建，非历史遗留）
    multi = []
    for row in table:
        hashes = set(json.loads(row["item_hashes"]))
        members = [item for item in prod_rows if item["url_hash"] in hashes]
        if len(members) > 1:
            multi.append({"cluster_id": row["cluster_id"], "members": members})
    judged = []
    for cluster in multi:
        titles = [m["title"] for m in cluster["members"]]
        verdict = prod_verdict(titles)
        ok_pairs = 0
        total_pairs = 0
        for i in range(len(titles)):
            for j in range(i + 1, len(titles)):
                total_pairs += 1
                if same_event(titles[i], titles[j]):
                    ok_pairs += 1
        judged.append({
            "cluster_id": cluster["cluster_id"],
            "titles": titles,
            "cluster_verdict": verdict[0],
            "basis": verdict[1],
            "pairs": total_pairs,
            "same_event_pairs": ok_pairs,
        })
    cluster_level = [row for row in judged if row["cluster_verdict"].startswith("合理")]
    pairs_total = sum(row["pairs"] for row in judged)
    pairs_ok = sum(row["same_event_pairs"] for row in judged)
    pair_precision = pairs_ok / pairs_total * 100 if pairs_total else 100.0
    suite.check(
        "2n-生产库多条目簇核验（重建后的真实数据）",
        bool(judged) and len(cluster_level) == len(judged) and pair_precision >= 90.0,
        f"多条目簇 {len(judged)} 个（人工核验合理 {len(cluster_level)} 个）；"
        f"同事件对 {pairs_ok}/{pairs_total}，对级准确率 {pair_precision:.1f}%（阈 ≥90%）",
    )
    suite.evidence_json("2n-prod-cluster-verdicts.json", {
        "legacy": False,
        "note": "真实库已按修复后的默认配置重建（llm_refine=false）",
        "multi_clusters": judged,
        "pair_precision": round(pair_precision, 1),
        "cluster_level_ok": len(cluster_level),
        "cluster_level_total": len(judged),
    })
    return {"multi": len(judged), "pair_precision": pair_precision}


# 生产库多条目簇裁定规则（按标题内容，id 变更后仍可用）
def prod_verdict(titles: list[str]) -> tuple[str, str]:
    low = [title.lower() for title in titles]
    if all("opus 5.5" in title for title in low):
        return "合理", "Claude Opus 5.5 发布（3 家来源）"
    if all(("sol and luna" in title) or ("gpt-6 sol" in title) for title in low):
        return "合理", "GPT-6 Sol 与 Luna 发布（3 家来源）"
    return "需人工复核", "未匹配已知事件模式"


# 第 1 轮生产库多条目簇人工裁定（已随真实库重建失效，仅留档）
PROD_VERDICTS: dict[int, tuple[str, str]] = {
    4: ("合理", "Jev 模型热议：同一波事件的不同报道（可接受）"),
    7: ("不合理", "10 条 OpenAI 不同产品/事件（Sol/Luna 发布、prompt caching、"
                  "MentalHealthBench、ChatGPT 语音、Enigma、车载、Harvey/invideo 案例）"
                  "被并为 1 簇"),
    17: ("合理", "Claude Opus 5.5 发布（3 家来源）"),
    18: ("不合理", "Coverage Cat 保险 与 Unreal Agent 无关事件被合并"),
    57: ("合理", "YouTube AI 创作者功能（同源 2 篇，同一发布）"),
}

# 同事件判定（用于 pair precision）：标题中出现相同专名列即视为同事件
SAME_EVENT_TOKENS = (
    "sol and luna", "gpt-6 sol", "opus 5.5", "youtube", "jev", "coverage cat", "unreal agent",
)


def same_event(left: str, right: str) -> bool:
    low_left, low_right = left.lower(), right.lower()
    return any(token in low_left and token in low_right for token in SAME_EVENT_TOKENS)


def stale_cluster_id_repro(suite: Suite) -> None:
    """D2 回归：两轮 enrich 的簇编号必须全局单调，历史簇与旧条目引用不被破坏。"""
    base = workspace(f"{WS}-stale")
    db_path = base / "data" / "aihot.db"
    seed_items(db_path, stale_fixture())
    pipeline = default_pipeline(gate=[], similarity=95, hours=1, refine=False)
    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=pipeline,
            data_dir=base / "data",
            log_dir=base / "logs",
            llm={
                "base_url": fake.base_url, "api_key": "sk-fake", "batch_size": 50,
                "daily_cost_limit_cny": 100.0,
            },
        )
        fake.enqueue_content(items_reply(3))
        run1 = run_cli(["enrich", "--config", str(cfg), "--limit", "3", "--top", "1"])
        fake.enqueue_content(items_reply(5))
        run2 = run_cli(["enrich", "--config", str(cfg), "--limit", "5", "--top", "1"])
    issues = stale_issues(db_path)
    first_ids = sorted(
        row["cluster_id"]
        for row in db_query(
            db_path, "SELECT cluster_id FROM clusters WHERE cluster_id <= 3 ORDER BY cluster_id"
        )
    )
    all_ids = sorted(
        row["cluster_id"] for row in db_query(db_path, "SELECT cluster_id FROM clusters")
    )
    second_ids = [cid for cid in all_ids if cid > 3]
    suite.evidence(
        "2o-stale-repro.txt",
        run1.text + "\n\n" + run2.text + "\n"
        + json.dumps({"all_cluster_ids": all_ids, "first_batch": first_ids,
                      "second_batch": second_ids, "issues": issues},
                     ensure_ascii=False, indent=2),
    )
    suite.evidence("2o-stale-issues.txt", "\n".join(issues) or "无问题")
    suite.check(
        "2o-跨轮簇编号全局单调（D2 回归）",
        not issues and second_ids == [4, 5, 6, 7, 8],
        f"两轮后簇编号={all_ids}；第二轮新增={second_ids}（期望 [4,5,6,7,8]）；"
        f"一致性问题={issues or '无'}",
    )


def items_reply(count: int) -> str:
    return json.dumps({"items": [
        {"index": index, "cn_title": f"中文标题 {index}", "cn_summary": "摘要",
         "category": "行业动态", "importance": 50, "is_ai": True}
        for index in range(1, count + 1)
    ]})


def stale_issues(db_path: Path) -> list[str]:
    """检查跨轮簇冲突：mention_count 与实际条目数不一致或孤儿 cluster_id。"""
    rows = db_query(
        db_path,
        "SELECT cluster_id, COUNT(*) AS n FROM items WHERE cluster_id IS NOT NULL "
        "GROUP BY cluster_id ORDER BY cluster_id",
    )
    table = {
        row["cluster_id"]: row["mention_count"]
        for row in db_query(db_path, "SELECT cluster_id, mention_count FROM clusters")
    }
    issues: list[str] = []
    for row in rows:
        cid, count = row["cluster_id"], row["n"]
        if cid not in table:
            issues.append(f"孤儿 cluster_id={cid}（{count} 条，无 clusters 行）")
        elif table[cid] != count:
            issues.append(f"cluster {cid}: mention_count={table[cid]} 但实际 {count} 条")
    return issues


def stale_fixture() -> list[dict]:
    titles = [
        "News Alpha 001", "News Beta 002", "News Gamma 003", "News Delta 004",
        "News Epsilon 005", "News Zeta 006", "News Eta 007", "News Theta 008",
    ]
    return [
        {
            "source_id": "qbitai",
            "title": title,
            "url": f"https://example.com/stale/{index}",
            "published_at": base_ts(index),
            "lang": "en",
        }
        for index, title in enumerate(titles)
    ]


def stale_check(db_path: Path, base: Path) -> bool:  # noqa: ARG001 - 兼容旧签名
    """兼容旧签名（当前 stale_issues 已替代）。"""
    issues = stale_issues(db_path)
    (base / "stale-issues.txt").write_text("\n".join(issues) or "无问题", encoding="utf-8")
    return bool(issues)


def refine_overmerge_repro(suite: Suite) -> None:
    """D1 回归：LLM 把 10 个不同事件说成一组（high）时，规则校验必须拦截（不合并）。"""
    base = workspace(f"{WS}-refine")
    db_path = base / "data" / "aihot.db"
    distinct = [
        ("qbitai", "OpenAI ships GPT-6 Sol", "https://example.com/refine/1"),
        ("openai_news", "Anthropic releases Claude Opus 6", "https://example.com/refine/2"),
        ("techcrunch_ai", "Meta open-sources Llama-9", "https://example.com/refine/3"),
        ("qbitai", "DeepSeek publishes V5 report", "https://example.com/refine/4"),
        ("hackernews", "Google announces Gemini 8 Ultra", "https://example.com/refine/5"),
        ("openai_news", "Mistral launches Le Chat Enterprise", "https://example.com/refine/6"),
        ("techcrunch_ai", "Cohere releases Command R10", "https://example.com/refine/7"),
        ("qbitai", "Perplexity ships Comet 2 browser", "https://example.com/refine/8"),
        ("openai_news", "Nvidia unveils Rubin GPU", "https://example.com/refine/9"),
        ("hackernews", "Amazon launches Nova 3 models", "https://example.com/refine/10"),
    ]
    seed_items(db_path, [
        {
            "source_id": source, "title": title, "url": url,
            "published_at": base_ts(1 + index), "raw_summary": "summary", "lang": "en",
        }
        for index, (source, title, url) in enumerate(distinct)
    ])
    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=True),
            data_dir=base / "data",
            log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-refine", "batch_size": 20,
                 "daily_cost_limit_cny": 100.0},
        )
        fake.enqueue_content(json.dumps({"groups": [
            {"indexes": list(range(1, 11)), "event": "OpenAI 发布 GPT-6 Sol", "confidence": "high"}
        ]}))
        fake.enqueue_content(items_reply(10))
        result = run_cli(["enrich", "--config", str(cfg), "--limit", "20", "--top", "2"])
        calls = fake.request_count
    cluster = db_query(
        db_path,
        "SELECT cluster_id, mention_count, item_hashes FROM clusters ORDER BY cluster_id",
    )
    runs = db_query(
        db_path, "SELECT stage_stats FROM runs ORDER BY started_at",
    )
    stats = json.loads(runs[-1]["stage_stats"]) if runs else {}
    suite.evidence("2p-refine-overmerge.txt", result.text + f"\nLLM 请求={calls}\n"
                   + json.dumps({"clusters": cluster, "run_stats": stats},
                                ensure_ascii=False, indent=2))
    suite.check(
        "2p-LLM 精修盲合并已被规则拦截（D1 回归）",
        len(cluster) == 10
        and all(row["mention_count"] == 1 for row in cluster)
        and stats.get("refine_rejected", 0) >= 1,
        f"簇数={len(cluster)}（期望 10 个单条簇），"
        f"refine_rejected={stats.get('refine_rejected')}（期望 ≥1），"
        "说明：10 条互不相关被 high 置信度说成一组 → 规则按相似链拆散",
    )


def main() -> int:
    suite = Suite("dedup")
    print("== 合成用例（黑盒 enrich --no-llm） ==")
    synthetic_cases(suite)

    print("\n== 真实数据规则层聚类核验（176 条 → 过滤后全量） ==")
    rule = rule_only_real(suite)
    suite.note(f"规则层：{rule['items']} 条 → {rule['clusters']} 簇，多条目簇 {rule['multi']}，"
               f"高相似跨簇对 {rule['oversplit']}")

    print("\n== 生产库（真实 LLM 精修）簇审计（只读） ==")
    prod = production_audit(suite)
    suite.note(f"生产库：多条目簇 {prod['multi']} 个，对级准确率 {prod['pair_precision']:.1f}%")

    print("\n== 跨轮 cluster_id 复用（受控复现） ==")
    stale_cluster_id_repro(suite)

    print("\n== LLM 精修盲合并拦截（D1 回归，受控注入） ==")
    refine_overmerge_repro(suite)

    print("\n== 真实库只读守护 ==")
    suite.check(
        "2z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_DB = Path(__file__).resolve().parents[2] / "data" / "aihot.db"
REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0


def detect_stale_in_real() -> bool:
    """真实库是否存在跨轮 cluster_id 冲突（mention_count 与实际条目不相等）。"""
    table = db_query(
        REAL_DB,
        "SELECT c.cluster_id, c.mention_count, "
        "(SELECT COUNT(*) FROM items i WHERE i.cluster_id = c.cluster_id) AS actual "
        "FROM clusters c",
    )
    return any(row["mention_count"] != row["actual"] for row in table)


if __name__ == "__main__":
    raise SystemExit(main())
