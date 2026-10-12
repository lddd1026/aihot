"""M2 验收 7：CLI 行为（免费 --no-llm --dry-run / 参数校验 rc=2 / pipeline 配置校验 rc=3）。"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    FakeLLM,
    Suite,
    db_one,
    default_pipeline,
    has_traceback,
    real_db_snapshot,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "cli"
BASE = workspace(WS)


def ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def fixture_items() -> list[dict]:
    titles = [
        "OpenAI ships GPT-6 Sol", "Anthropic releases Claude Opus 6",
        "Meta open-sources Llama-9", "DeepSeek publishes V5 report",
        "Google announces Gemini 8 Ultra", "Mistral launches Le Chat Enterprise",
        "Cohere releases Command R10", "Perplexity ships Comet 2",
        "Hugging Face launches datasets v9", "arXiv benchmarks multimodal agents",
    ]
    return [
        {
            "source_id": "qbitai",
            "title": f"{title}（第 {index} 条）",
            "url": f"https://example.com/cli/{index}",
            "published_at": ts(index * 0.5),
            "lang": "en",
        }
        for index, title in enumerate(titles, 1)
    ]


def main() -> int:
    suite = Suite("cli")

    # ---------- 7a：enrich --no-llm --dry-run 免费可用 ----------
    base = workspace(f"{WS}-free")
    db_path = base / "data" / "aihot.db"
    seed_items(db_path, fixture_items())
    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(),
            data_dir=base / "data",
            log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-cli",
                 "daily_cost_limit_cny": 100.0},
        )
        result = run_cli(["enrich", "--no-llm", "--dry-run", "--config", str(cfg), "--top", "5"])
        calls = fake.request_count
    suite.evidence("7a-no-llm-dry-run.txt", result.text)
    suite.check(
        "7a-免费可用（rc=0、零 LLM 调用、未写库）",
        result.rc == 0
        and calls == 0
        and "（dry-run，未写库）" in result.stdout
        and db_one(db_path, "SELECT COUNT(*) FROM items WHERE enriched_at IS NOT NULL") == 0,
        f"rc={result.rc}，LLM 请求={calls}，enriched_at 非空=0",
    )
    suite.check(
        "7a-输出含预筛与聚类统计",
        "预筛:" in result.stdout and "聚类:" in result.stdout and "Top 5:" in result.stdout,
        "stdout 含预筛/聚类/Top 段落",
    )

    # 真实配置的 --no-llm --dry-run（不写库、不调用真实 LLM）
    seed_items(base / "data2" / "aihot.db", fixture_items())
    real_cfg = write_config(
        base / "real-like.yaml",
        pipeline=default_pipeline(),
        data_dir=base / "data2",
        log_dir=base / "logs2",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-cli"},
    )
    real_run = run_cli(["enrich", "--no-llm", "--dry-run", "--config", str(real_cfg), "--top", "3"])
    suite.evidence("7a-no-llm-real-config.txt", real_run.text)
    suite.check(
        "7a-no-llm 指向不可达 LLM 也能跑（零调用）",
        real_run.rc == 0 and "预筛:" in real_run.stdout and "Top 3:" in real_run.stdout,
        f"rc={real_run.rc}（--no-llm 不触碰 LLM 端点，指向 127.0.0.1:1 仍成功）",
    )

    # ---------- 7b：--limit/--top 参数校验（rc=2） ----------
    bad_args = [
        ["--limit", "-1"], ["--top", "-1"], ["--limit", "abc"], ["--top", "abc"],
        ["--limit", "1.5"], ["--top", "1e9"],
    ]
    arg_results = []
    for extra in bad_args:
        res = run_cli(["enrich", "--no-llm", "--dry-run", "--config", str(cfg), *extra], timeout=60)
        arg_results.append({"args": extra, "rc": res.rc, "stderr_tail": res.stderr.strip()[-160:]})
    suite.evidence_json("7b-bad-args.json", {
        "results": arg_results,
    })
    for row in arg_results:
        suite.check(
            f"7b-非法参数 {' '.join(row['args'])} → rc=2",
            row["rc"] == 2 and not has_traceback(row["stderr_tail"]),
            f"rc={row['rc']}（期望 2）：{row['stderr_tail'][-90:]}",
        )
    res_zero = run_cli(
        ["enrich", "--no-llm", "--dry-run", "--config", str(cfg), "--limit", "0", "--top", "0"],
        timeout=60,
    )
    suite.check(
        "7b-边界值 --limit 0/--top 0 合法",
        res_zero.rc == 0 and "Top 0:" not in res_zero.stdout,
        f"rc={res_zero.rc}（limit 0=用配置上限，top 0=不打印）",
    )

    # ---------- 7c：pipeline 配置校验（rc=3） ----------
    bad_pipeline = [
        ("gate_sources 非列表", {"prefilter": {"gate_sources": "hackernews"}}),
        ("title_similarity=-1", {"dedup": {"title_similarity": -1}}),
        ("title_similarity=101", {"dedup": {"title_similarity": 101}}),
        ("title_similarity=abc", {"dedup": {"title_similarity": "abc"}}),
        ("max_hours_apart=0", {"dedup": {"max_hours_apart": 0}}),
        ("max_hours_apart=721", {"dedup": {"max_hours_apart": 721}}),
        ("llm_refine='false' 字符串", {"dedup": {"llm_refine": "false"}}),
        ("enrich.max_items=0", {"enrich": {"max_items": 0}}),
        ("enrich.max_items=2001", {"enrich": {"max_items": 2001}}),
        ("price_input=-1", {"enrich": {"price_input_per_million": -1}}),
        ("price_output=10001", {"enrich": {"price_output_per_million": 10001}}),
        ("interest_bonus 非映射", {"rank": {"interest_bonus": ["模型发布"]}}),
        ("interest_bonus 值非数字", {"rank": {"interest_bonus": {"模型发布": "高"}}}),
    ]
    bad_results = []
    for index, (name, patch) in enumerate(bad_pipeline):
        path = BASE / f"bad-pipeline-{index}.yaml"
        pipe = {
            "prefilter": {"gate_sources": ["hackernews"], "blacklist_keywords": [],
                          "extra_ai_keywords": []},
            "dedup": {"title_similarity": 88, "max_hours_apart": 72, "llm_refine": True},
            "enrich": {"max_items": 200, "price_input_per_million": 2.0,
                       "price_output_per_million": 8.0},
        }
        for key, value in patch.items():
            if isinstance(value, dict):
                pipe.setdefault(key, {}).update(value)
            else:
                pipe[key] = value
        cfg_bad = write_config(
            path, pipeline=pipe, data_dir=BASE / "data", log_dir=BASE / "logs",
            llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-bad"},
        )
        res = run_cli(["enrich", "--no-llm", "--dry-run", "--config", str(cfg_bad)], timeout=60)
        bad_results.append({
            "case": name, "rc": res.rc, "stdout_tail": res.stdout.strip()[-200:],
            "traceback": has_traceback(res.combined),
        })
    suite.evidence_json("7c-bad-pipeline.json", bad_results)
    for row in bad_results:
        suite.check(
            f"7c-{row['case']} → rc=3",
            row["rc"] == 3 and not row["traceback"],
            f"rc={row['rc']}（期望 3）",
        )

    # 合理边界值应通过
    good_results = []
    for index, patch in enumerate([
        {"dedup": {"title_similarity": 0, "max_hours_apart": 1}},
        {"dedup": {"title_similarity": 100, "max_hours_apart": 720}},
        {"enrich": {"max_items": 1, "price_input_per_million": 0,
                    "price_output_per_million": 10000}},
        {"rank": {"interest_bonus": {}}},
    ]):
        path = BASE / f"good-pipeline-{index}.yaml"
        pipe = {
            "prefilter": {"gate_sources": [], "blacklist_keywords": [], "extra_ai_keywords": []},
            "dedup": {"title_similarity": 88, "max_hours_apart": 72, "llm_refine": True},
            "enrich": {"max_items": 200, "price_input_per_million": 2.0,
                       "price_output_per_million": 8.0},
        }
        for key, value in patch.items():
            pipe.setdefault(key, {}).update(value)
        cfg_ok = write_config(
            path, pipeline=pipe, data_dir=BASE / "data", log_dir=BASE / "logs",
            llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-good"},
        )
        res = run_cli(["enrich", "--no-llm", "--dry-run", "--config", str(cfg_ok)], timeout=60)
        good_results.append({"patch": json.dumps(patch, ensure_ascii=False), "rc": res.rc})
    suite.evidence_json("7c-good-pipeline.json", good_results)
    suite.check(
        "7c-合法边界值通过（rc=0）",
        all(row["rc"] == 0 for row in good_results),
        f"4 组边界配置 rc={[row['rc'] for row in good_results]}",
    )

    # ---------- 7d：默认 pipeline（无配置段）仍可用 ----------
    base2 = workspace(f"{WS}-default-pipeline")
    path = base2 / "config.yaml"
    write_config(path, pipeline=None, data_dir=base2 / "data", log_dir=base2 / "logs",
                 llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-default"})
    seed_items(base2 / "data" / "aihot.db", fixture_items())
    res = run_cli(["enrich", "--no-llm", "--dry-run", "--config", str(path), "--top", "3"])
    suite.check(
        "7d-缺失 pipeline 段可用默认值",
        res.rc == 0 and "预筛:" in res.stdout,
        f"rc={res.rc}",
    )

    suite.check(
        "7z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
