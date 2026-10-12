"""M7 P3 修复复验（第 2 轮）：M7-1 口径 / M7-2 可观测性 / M7-3 文档 / M7-4 证据 + 回归。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m7r_p3_fixes.py
证据: reports/evidence/m7r/round2/（脚本固定该目录，避免与第 1 轮证据混淆）
约束: 只连 localhost；--no-llm 跑 enrich（不起真实/假 LLM）；不改真实 data/config。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# 第 2 轮证据根目录必须在导入任何支撑模块之前固定
os.environ["AIHOT_M7R_EVIDENCE"] = "m7r/round2"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m7r_support as m7  # noqa: E402

from aihot.config import DEFAULT_INTEREST_BONUS  # noqa: E402

PROJECT = m3.PROJECT
PYTHON = m3.PYTHON
REGRESSION = m7.EVIDENCE / "regression"
PYTEST_PASSED = re.compile(r"(\d+) passed")
PYTEST_FAILED = re.compile(r"(\d+) (?:failed|error)")

# 6 条混合条目：3 技术（保留）+ 2 主题不符（硬件/商业）+ 1 垃圾（招聘）
FIXTURES: list[tuple[str, str]] = [
    ("AirLLM 更新：单卡 4GB GPU 跑 70B 大模型", "tech"),
    ("vLLM 推理优化实践：吞吐提升 3 倍", "tech"),
    ("从 0 搭建 RAG 评测流水线：踩坑与经验", "tech"),
    ("台积电 2nm 晶圆产能被 AI 公司预订一空", "topic"),
    ("AI startup raises $50M to reinvent search", "topic"),
    ("AI 大厂招聘算法工程师，简历投递内推", "blacklist"),
]


def run_cmd(args: list[str], *, timeout: float) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            args, cwd=str(PROJECT), capture_output=True, timeout=timeout, env=env
        )
        rc: int | str = proc.returncode
        out = proc.stdout.decode("utf-8", "replace") + "\n--- stderr ---\n" + proc.stderr.decode(
            "utf-8", "replace"
        )
    except subprocess.TimeoutExpired as exc:
        rc = "timeout"
        out = (exc.stdout or b"").decode("utf-8", "replace") + "\n[harness] 超时"
    return {
        "cmd": " ".join(args),
        "rc": rc,
        "seconds": round(time.perf_counter() - start, 1),
        "output": out,
    }


def section(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    if begin < 0:
        return ""
    finish = text.find(end, begin + len(start))
    return text[begin : finish if finish > 0 else len(text)]


def verify_m7_2(suite: m3.Suite) -> None:
    """M7-2：预筛摘要行显示「主题不符 N」+ runs.stage_stats 带 dropped_* 细分。"""
    env = m7.m5.clean_proxy_env({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    entries = [
        {
            "title": title,
            "link": f"https://example.com/m7r/round2/{index}",
            "description": "m7r round2 fixture",
            "pubDate": m3.rfc822(15 + index),
        }
        for index, (title, _) in enumerate(FIXTURES, start=1)
    ]
    with m3.FakeRSS(entries) as rss:
        ws = m3.workspace("m7r-round2-p3")
        cfg_path = m3.write_config(
            ws / "config.yaml",
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                {
                    "id": "fake_feed",
                    "name": "假 RSS",
                    "type": "rss",
                    "url": rss.url,
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
        )
        fetched = m3.run_cli(["fetch", "--config", str(cfg_path)], env_extra=env, timeout=300)
        suite.evidence("m7-2/1-fetch.txt", fetched.text)
        suite.check(
            "P3-2a fetch 落库 6 条混合条目 rc=0",
            fetched.rc == 0 and "新增 6 条" in fetched.stdout,
            f"rc={fetched.rc}",
        )

        dry = m3.run_cli(
            ["enrich", "--dry-run", "--no-llm", "--config", str(cfg_path)],
            env_extra=env,
            timeout=300,
        )
        suite.evidence("m7-2/2-enrich-dry-run.txt", dry.text)

        def _is_prefilter_line(line: str) -> bool:
            return "预筛" not in line and "输入" in line

        prefilter_line = next(
            (line.strip() for line in dry.stdout.splitlines() if _is_prefilter_line(line)),
            "",
        )
        suite.check(
            "P3-2a enrich 预筛摘要行并列显示「主题不符 N」（垃圾/与AI无关/主题不符/批内重复）",
            dry.rc == 0
            and "输入 6 → 保留 3" in dry.stdout
            and "垃圾 1" in prefilter_line
            and "与 AI 无关 0" in prefilter_line
            and "主题不符 2" in prefilter_line
            and "批内重复 0" in prefilter_line,
            prefilter_line,
        )
        suite.check(
            "P3-2b 样本行仍打印 [主题不符（硬件/商业）] 具体标题",
            "主题不符（硬件/商业）" in dry.stdout and "台积电" in dry.stdout,
            next((line.strip() for line in dry.stdout.splitlines() if "主题不符" in line), ""),
        )

        enriched = m3.run_cli(
            ["enrich", "--no-llm", "--config", str(cfg_path)], env_extra=env, timeout=300
        )
        suite.evidence("m7-2/3-enrich-persist.txt", enriched.text)
        suite.check(
            "P3-2c 非 dry-run enrich rc=0 并写回",
            enriched.rc == 0 and "已写回" in enriched.stdout,
            f"rc={enriched.rc}",
        )

        rows = m3.db_query(
            ws / "data" / "aihot.db",
            "SELECT stage_stats FROM runs WHERE stage_stats LIKE '%\"stage\": \"enrich\"%'"
            " ORDER BY started_at DESC LIMIT 1",
        )
        stats: dict[str, Any] = {}
        if rows:
            try:
                stats = json.loads(rows[0]["stage_stats"])
            except json.JSONDecodeError:
                stats = {}
        suite.evidence_json("m7-2/4-enrich-stage-stats.json", stats)
        expected = {
            "dropped_blacklist": 1,
            "dropped_offtopic": 0,
            "dropped_topic": 2,
            "dropped_duplicate": 0,
            "prefilter_kept": 3,
        }
        suite.check(
            "P3-2d runs 表 enrich 记录 stage_stats 带 dropped_* 细分且数值正确",
            bool(stats)
            and all(stats.get(key) == value for key, value in expected.items())
            and stats.get("stage") == "enrich",
            f"stage_stats={ {k: stats.get(k) for k in expected} }",
        )


def verify_docs(suite: m3.Suite) -> None:
    """M7-1 口径统一 / M7-3 整体替换说明 / M7-4 复核证据落盘。"""
    readme = (PROJECT / "README.md").read_text(encoding="utf-8")
    design = (PROJECT / "DESIGN.md").read_text(encoding="utf-8")
    plan = (PROJECT / "PLAN.md").read_text(encoding="utf-8")
    readme_block = section(readme, "## 内容范围（开发者向）", "\n## ")
    design_block = section(design, "### 3.8", "\n---")
    plan_row = next((line for line in plan.splitlines() if "| v1.5 |" in line), "")
    suite.evidence("m7-1/readme-block.txt", readme_block)
    suite.evidence("m7-1/design-3.8.txt", design_block)
    suite.evidence("m7-1/plan-v1.5-row.txt", plan_row)

    label = "三道过滤 + 一层排序加权"
    suite.check(
        "P3-1 README / DESIGN §3.8 / PLAN v1.5 口径统一为「三道过滤 + 一层排序加权」",
        label in readme_block and label in design_block and label in plan_row,
        f"README={label in readme_block} DESIGN={label in design_block} PLAN={label in plan_row}",
    )
    suite.check(
        "P3-1b 三处不再出现旧口径「三层过滤」",
        all("三层过滤" not in block for block in (readme_block, design_block, plan_row)),
        "旧口径残留="
        + str(
            [
                name
                for name, block in (
                    ("README", readme_block),
                    ("DESIGN", design_block),
                    ("PLAN", plan_row),
                )
                if "三层过滤" in block
            ]
        ),
    )

    example_text = (PROJECT / "config.example.yaml").read_text(encoding="utf-8")
    bonus_idx = example_text.find("interest_bonus")
    bonus_comment = example_text[bonus_idx : bonus_idx + 400] if bonus_idx >= 0 else ""
    suite.evidence("m7-3/config-example-bonus-comment.txt", bonus_comment)
    suite.check(
        "P3-3a config.example.yaml 注释注明「整体替换、需列全分类」",
        "整体替换" in bonus_comment and "列全" in bonus_comment,
        next((line.strip() for line in bonus_comment.splitlines() if "整体替换" in line), ""),
    )
    suite.check(
        "P3-3b README / DESIGN 同步注明整体替换语义",
        "整体替换" in readme_block and "整体替换" in design_block,
        f"README={'整体替换' in readme_block} DESIGN={'整体替换' in design_block}",
    )
    partial_ws = m3.workspace("m7r-round2-partial-bonus")
    partial_pipeline = m7.base_pipeline_raw()
    partial_pipeline.setdefault("rank", {})["interest_bonus"] = {"模型发布": 20}
    _, partial_cfg = m7.write_and_load(partial_ws, pipeline=partial_pipeline)
    behavior = partial_cfg.pipeline.rank.interest_bonus
    suite.evidence_json("m7-3/partial-bonus-loaded.json", behavior)
    suite.check(
        "P3-3c 行为与文档一致：部分配置为整体替换（未列分类不保留默认加成）",
        behavior == {"模型发布": 20.0},
        f"loaded={behavior}（默认全集={sorted(DEFAULT_INTEREST_BONUS)}）",
    )

    evidence_path = PROJECT / "docs" / "evidence" / "content-policy" / "2026-10-12-recheck.txt"
    evidence_text = evidence_path.read_text(encoding="utf-8") if evidence_path.exists() else ""
    suite.evidence("m7-4/recheck.txt", evidence_text)
    suite.check(
        "P3-4a 真实数据复核证据落盘且内容与记述一致（78 条 / 主题不符 1 / 工程内容在报）",
        bool(evidence_text)
        and "78" in evidence_text
        and "主题不符 1" in evidence_text
        and "AirLLM" in evidence_text
        and "RTX 3090" in evidence_text,
        f"证据文件={evidence_path.name if evidence_text else '缺失'}（{len(evidence_text)} 字节）",
    )
    suite.check(
        "P3-4b PLAN v1.5 引用该证据路径",
        "docs/evidence/content-policy/2026-10-12-recheck.txt" in plan_row,
        "引用命中=" + str("docs/evidence/content-policy/2026-10-12-recheck.txt" in plan_row),
    )


def verify_regression(suite: m3.Suite) -> None:
    """回归：pytest 408 / ruff 零告警。"""
    pytest_result = run_cmd([str(PYTHON), "-m", "pytest"], timeout=1800)
    suite.evidence("regression/pytest.txt", pytest_result["output"])
    passed = PYTEST_PASSED.search(pytest_result["output"])
    failed = PYTEST_FAILED.search(pytest_result["output"])
    suite.check(
        "P3-回归1 pytest 全绿且用例数为 408",
        pytest_result["rc"] == 0
        and failed is None
        and passed is not None
        and int(passed.group(1)) == 408,
        f"rc={pytest_result['rc']}；passed={passed.group(1) if passed else '?'}"
        f"；failed={failed.group(1) if failed else '0'}；{pytest_result['seconds']}s",
    )
    ruff_result = run_cmd([str(PYTHON), "-m", "ruff", "check", "."], timeout=600)
    suite.evidence("regression/ruff.txt", ruff_result["output"])
    suite.check(
        "P3-回归2 ruff check . 零告警",
        ruff_result["rc"] == 0 and "All checks passed!" in ruff_result["output"],
        f"rc={ruff_result['rc']}；{ruff_result['output'].strip().splitlines()[0][:60]}",
    )


def main() -> int:
    suite = m3.Suite("m7r_p3_fixes")
    REGRESSION.mkdir(parents=True, exist_ok=True)
    verify_m7_2(suite)
    verify_docs(suite)
    verify_regression(suite)
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
