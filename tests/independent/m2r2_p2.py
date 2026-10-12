"""M2 回归（第 2 轮）· P2 复验：GPU/GPUs、use_builtin_blacklist、runs 记录、time_budget。

4a GPU/GPUs/TPU/TPUs 过门槛（控制组仍淘汰）
4b use_builtin_blacklist 缺省/true/false 三种语义
4c enrich 写入 runs（stage=enrich，成本字段可读且与假 usage 一致；--no-llm 也留痕）
4d time_budget_seconds 校验（rc=3/0）与生效（超时停止后续批次 + time_budget_hit）
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    FakeLLM,
    Reply,
    Suite,
    db_query,
    default_pipeline,
    real_db_snapshot,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "r2-p2"


def ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def seed(path: Path, rows: list[tuple[str, str]], *, prefix: str) -> None:
    seed_items(path, [
        {
            "source_id": source, "title": title, "url": f"https://example.com/{prefix}/{index}",
            "published_at": ts(1 + index), "raw_summary": "", "lang": "zh",
        }
        for index, (source, title) in enumerate(rows)
    ])


def filtered_urls(db_path: Path) -> set[str]:
    return {
        row["url"]
        for row in db_query(db_path, "SELECT url FROM items WHERE category = '过滤'")
    }


def enrich_runs(db_path: Path) -> list[dict]:
    out = []
    rows = db_query(db_path, "SELECT run_id, status, stage_stats FROM runs ORDER BY started_at")
    for row in rows:
        stats = json.loads(row["stage_stats"] or "{}")
        if stats.get("stage") == "enrich":
            out.append({"run_id": row["run_id"], "status": row["status"], **stats})
    return out


def case_4a(suite: Suite) -> None:
    base = workspace(f"{WS}-4a")
    db_path = base / "data" / "aihot.db"
    rows = [
        ("hackernews", "Nvidia unveils Rubin GPU"),
        ("hackernews", "Nvidia unveils Rubin GPUs"),
        ("hackernews", "Google starts deploying TPU v8 for training"),
        ("hackernews", "Google starts deploying TPUs v8 for training"),
        ("hackernews", "Rust 1.99 released with faster builds"),
    ]
    seed(db_path, rows, prefix="r2p2a")
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(gate=["hackernews"], refine=False),
        data_dir=base / "data", log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-p2"},
    )
    result = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "20", "--top", "1"])
    dropped = filtered_urls(db_path)
    kept = {
        row["url"]
        for row in db_query(db_path, "SELECT url FROM items WHERE category != '过滤'")
    }
    suite.evidence("4a-gpu-gate.txt", result.text + "\n" + json.dumps(
        {"dropped": sorted(dropped), "kept": sorted(kept)}, ensure_ascii=False, indent=2))
    for index, url_suffix in enumerate(["0", "1", "2", "3"]):
        title = rows[index][1]
        suite.check(
            f"4a-过门槛：{title}",
            f"https://example.com/r2p2a/{url_suffix}" in kept,
            f"保留={'https://example.com/r2p2a/' + url_suffix in kept}",
        )
    suite.check(
        "4a-控制组仍被淘汰（无 AI 关键词）",
        "https://example.com/r2p2a/4" in dropped,
        f"Rust 1.99 淘汰={'https://example.com/r2p2a/4' in dropped}",
    )


def case_4b(suite: Suite) -> None:
    """use_builtin_blacklist 语义：缺省=true（内置+自定义）；false=仅自定义。"""
    rows = [
        ("qbitai", "招聘｜大模型算法工程师（北京）"),      # 内置词表命中
        ("qbitai", "辟谣：网传某大模型将暂停服务"),          # 自定义词命中
        ("qbitai", "OpenAI 发布 GPT-6 Sol 模型说明"),      # 正常条目
    ]
    results: dict[str, dict] = {}
    for slug, pipeline_extra in [
        ("default", None),                                    # 不写 use_builtin_blacklist
        ("true", {"use_builtin_blacklist": True}),
        ("false", {"use_builtin_blacklist": False}),
        ("false-empty", {"use_builtin_blacklist": False, "blacklist_keywords": []}),
    ]:
        base = workspace(f"{WS}-4b-{slug}")
        db_path = base / "data" / "aihot.db"
        seed(db_path, rows, prefix=f"r2p2b{slug}")
        prefilter = {
            "gate_sources": [],
            "blacklist_keywords": ["辟谣"] if slug != "false-empty" else [],
            "extra_ai_keywords": [],
        }
        if pipeline_extra is not None:
            prefilter.update(pipeline_extra)
        cfg = write_config(
            base / "config.yaml",
            pipeline={
                "prefilter": prefilter,
                "dedup": {"title_similarity": 88, "max_hours_apart": 72, "llm_refine": False},
                "enrich": {"max_items": 200, "price_input_per_million": 2.0,
                           "price_output_per_million": 8.0},
            },
            data_dir=base / "data", log_dir=base / "logs",
            llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-p2"},
        )
        result = run_cli(
            ["enrich", "--no-llm", "--config", str(cfg), "--limit", "20", "--top", "1"]
        )
        dropped = {url.rsplit("/", 1)[-1] for url in filtered_urls(db_path)}
        results[slug] = {"dropped_indexes": sorted(dropped), "rc": result.rc}
        suite.evidence(f"4b-{slug}.txt", result.text)
    suite.evidence_json("4b-blacklist-matrix.json", results)

    suite.check(
        "4b-缺省：内置+自定义都生效",
        results["default"]["dropped_indexes"] == ["0", "1"] and results["default"]["rc"] == 0,
        f"淘汰={results['default']['dropped_indexes']}（期望 ['0','1']：招聘+辟谣）",
    )
    suite.check(
        "4b-true：内置+自定义都生效",
        results["true"]["dropped_indexes"] == ["0", "1"],
        f"淘汰={results['true']['dropped_indexes']}",
    )
    suite.check(
        "4b-false：仅自定义词生效（招聘不再淘汰）",
        results["false"]["dropped_indexes"] == ["1"],
        f"淘汰={results['false']['dropped_indexes']}（期望 ['1']：仅辟谣）",
    )
    suite.check(
        "4b-false+空自定义：黑名单整体停用",
        results["false-empty"]["dropped_indexes"] == [],
        f"淘汰={results['false-empty']['dropped_indexes']}（期望 []）",
    )


def case_4c(suite: Suite) -> None:
    """runs 表：stage=enrich + 成本字段可读（LLM 与 --no-llm 两种）。"""
    base = workspace(f"{WS}-4c")
    db_path = base / "data" / "aihot.db"
    seed(db_path, [
        ("qbitai", "OpenAI ships GPT-6 Sol"),
        ("openai_news", "Anthropic releases Claude Opus 6"),
        ("techcrunch_ai", "Meta open-sources Llama-9"),
    ], prefix="r2p2c")
    usage = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}
    expected_cost = 1000 / 1e6 * 2.0 + 500 / 1e6 * 8.0  # 0.006
    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=False),
            data_dir=base / "data", log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-p2", "batch_size": 15,
                 "daily_cost_limit_cny": 100.0},
        )
        fake.enqueue(Reply(content=json.dumps({"items": [
            {"index": index, "cn_title": f"标题 {index}", "cn_summary": "摘要",
             "category": "模型发布", "importance": 60, "is_ai": True} for index in (1, 2, 3)
        ]}), usage=usage))
        llm_run = run_cli(["enrich", "--config", str(cfg), "--limit", "20", "--top", "1"])
    llm_records = enrich_runs(db_path)

    # --no-llm 再次留痕（新增 2 条待加工）
    seed(db_path, [
        ("qbitai", "DeepSeek publishes V5 report"),
        ("openai_news", "Mistral launches Le Chat Enterprise"),
    ], prefix="r2p2c2")
    cfg2 = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(refine=False),
        data_dir=base / "data", log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-p2"},
    )
    no_llm_run = run_cli(
        ["enrich", "--no-llm", "--config", str(cfg2), "--limit", "20", "--top", "1"]
    )
    all_records = enrich_runs(db_path)

    suite.evidence("4c-runs.txt", llm_run.text + "\n\n" + no_llm_run.text + "\n"
                   + json.dumps(all_records, ensure_ascii=False, indent=2))
    record = llm_records[0] if llm_records else {}
    required = {
        "pending", "prefilter_kept", "clusters", "enriched", "filtered",
        "batches", "succeeded", "failed_batches", "prompt_tokens", "completion_tokens",
        "cost_cny", "cost_limit_hit", "time_budget_hit", "refine_calls", "refine_rejected",
    }
    suite.check(
        "4c-runs 含 stage=enrich 记录",
        bool(llm_records) and record.get("stage") == "enrich",
        f"enrich 记录数={len(llm_records)}，stage={record.get('stage')}，"
        f"status={record.get('status')}",
    )
    suite.check(
        "4c-成本字段可读且与假 usage 一致",
        abs(float(record.get("cost_cny") or 0) - expected_cost) < 1e-6
        and record.get("prompt_tokens") == 1000
        and record.get("completion_tokens") == 500
        and record.get("batches") == 1
        and record.get("succeeded") == 3,
        f"cost_cny={record.get('cost_cny')}（期望 {expected_cost}），"
        f"token={record.get('prompt_tokens')}+{record.get('completion_tokens')}，"
        f"batches={record.get('batches')} succeeded={record.get('succeeded')}",
    )
    suite.check(
        "4c-统计字段齐备",
        required <= set(record),
        f"缺失字段={sorted(required - set(record)) or '无'}；"
        f"no_llm 字段={record.get('no_llm', '<缺省=视为 false>')}",
    )
    no_llm_record = all_records[-1] if all_records else {}
    suite.check(
        "4c---no-llm 也写 runs（no_llm=true 且字段齐全）",
        no_llm_record.get("no_llm") is True
        and no_llm_record.get("enriched") == 2
        and no_llm_record.get("batches") == 0
        and float(no_llm_record.get("cost_cny") or 0) == 0.0
        and {"batches", "cost_cny", "prompt_tokens", "time_budget_hit"}
        <= set(no_llm_record),
        f"no_llm={no_llm_record.get('no_llm')} enriched={no_llm_record.get('enriched')} "
        f"cost={no_llm_record.get('cost_cny')} batches={no_llm_record.get('batches')}",
    )


def case_4d(suite: Suite) -> None:
    """time_budget_seconds：配置校验 + 生效（超时停止后续批次）。"""
    base = workspace(f"{WS}-4d")
    db_path = base / "data" / "aihot.db"
    seed(db_path, [
        ("qbitai", "OpenAI ships GPT-6 Sol"),
        ("openai_news", "Anthropic releases Claude Opus 6"),
        ("techcrunch_ai", "Meta open-sources Llama-9"),
    ], prefix="r2p2d")
    bad_values = [59, 7201, "abc", 60.5]
    bad_results = []
    for index, value in enumerate(bad_values):
        cfg = write_config(
            base / f"bad-{index}.yaml",
            pipeline={**default_pipeline(refine=False),
                      "enrich": {"max_items": 200, "price_input_per_million": 2.0,
                                 "price_output_per_million": 8.0,
                                 "time_budget_seconds": value}},
            data_dir=base / "data", log_dir=base / "logs",
            llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-p2"},
        )
        result = run_cli(["enrich", "--no-llm", "--dry-run", "--config", str(cfg)], timeout=60)
        bad_results.append({"value": value, "rc": result.rc})
    good_results = []
    for index, value in enumerate([60, 7200]):
        cfg = write_config(
            base / f"good-{index}.yaml",
            pipeline={**default_pipeline(refine=False),
                      "enrich": {"max_items": 200, "price_input_per_million": 2.0,
                                 "price_output_per_million": 8.0,
                                 "time_budget_seconds": value}},
            data_dir=base / "data", log_dir=base / "logs",
            llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-p2"},
        )
        result = run_cli(["enrich", "--no-llm", "--dry-run", "--config", str(cfg)], timeout=60)
        good_results.append({"value": value, "rc": result.rc})
    suite.evidence_json("4d-time-budget-validation.json", {
        "bad": bad_results, "good": good_results,
    })
    suite.check(
        "4d-越界 time_budget_seconds → rc=3",
        all(row["rc"] == 3 for row in bad_results),
        f"坏值 rc={[(row['value'], row['rc']) for row in bad_results]}",
    )
    suite.check(
        "4d-边界值 60/7200 合法",
        all(row["rc"] == 0 for row in good_results),
        f"边界 rc={[(row['value'], row['rc']) for row in good_results]}",
    )

    # 生效：batch_size=1、每次 31s 延迟 → 第 3 批前超过 60s 预算
    base2 = workspace(f"{WS}-4d-run")
    db_path2 = base2 / "data" / "aihot.db"
    seed(db_path2, [
        ("qbitai", "OpenAI ships GPT-6 Sol"),
        ("openai_news", "Anthropic releases Claude Opus 6"),
        ("techcrunch_ai", "Meta open-sources Llama-9"),
    ], prefix="r2p2d2")
    with FakeLLM() as fake:
        fake.set_generator(
            lambda body: json.dumps({"items": [
                {"index": 1, "cn_title": "标题", "cn_summary": "摘要",
                 "category": "模型发布", "importance": 60, "is_ai": True}
            ]}),
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            delay=31.0,
        )
        cfg = write_config(
            base2 / "config.yaml",
            pipeline={**default_pipeline(refine=False),
                      "enrich": {"max_items": 200, "price_input_per_million": 2.0,
                                 "price_output_per_million": 8.0,
                                 "time_budget_seconds": 60}},
            data_dir=base2 / "data", log_dir=base2 / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-p2", "batch_size": 1,
                 "daily_cost_limit_cny": 100.0},
        )
        run = run_cli(["enrich", "--config", str(cfg), "--limit", "20", "--top", "1"], timeout=300)
    records = enrich_runs(db_path2)
    record = records[0] if records else {}
    fallbacks = db_query(
        db_path2, "SELECT COUNT(*) AS n FROM items WHERE cn_title = title"
    )[0]["n"]
    suite.evidence("4d-time-budget-run.txt", run.text + "\n"
                   + json.dumps(records, ensure_ascii=False, indent=2))
    suite.check(
        "4d-超时停止后续批次（time_budget_hit=true）",
        record.get("time_budget_hit") is True and record.get("batches") == 2,
        f"time_budget_hit={record.get('time_budget_hit')}，batches={record.get('batches')}"
        f"（3 批中执行 2 批，耗时 {run.seconds:.0f}s）",
    )
    enriched_count = db_query(
        db_path2, "SELECT COUNT(*) AS n FROM items WHERE enriched_at IS NOT NULL"
    )[0]["n"]
    suite.check(
        "4d-不丢数据（3 条全部落库，1 条降级原文）",
        enriched_count == 3 and fallbacks == 1 and run.rc == 0,
        f"enriched={enriched_count}，降级条目={fallbacks}（期望 1），rc={run.rc}",
    )
    suite.check(
        "4d-日志记录停止原因",
        "已达加工时间上限" in _log_text(base2),
        f"日志含「已达加工时间上限」={'已达加工时间上限' in _log_text(base2)}",
    )


def _log_text(base: Path) -> str:
    logs = list((base / "logs").glob("*.log"))
    return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in logs)


def main() -> int:
    suite = Suite("p2")
    print("== 4a GPU/GPUs/TPU/TPUs 门槛 ==")
    case_4a(suite)
    print("\n== 4b use_builtin_blacklist 语义 ==")
    case_4b(suite)
    print("\n== 4c runs 表（stage=enrich + 成本） ==")
    case_4c(suite)
    print("\n== 4d time_budget_seconds（含 60s 生效用例，约 1 分钟） ==")
    case_4d(suite)
    suite.check(
        "4z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
