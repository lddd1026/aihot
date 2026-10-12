"""M7 独立验收 · 声明 8：文档一致（README / DESIGN §3.8 / PLAN v1.5 / 配置模板）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m7r_docs.py
证据: reports/evidence/m7r/m7r_docs/ 与 m7r_docs.json
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m7r_support as m7  # noqa: E402

from aihot.config import DEFAULT_INTEREST_BONUS  # noqa: E402
from aihot.pipeline.prefilter import DEFAULT_EXCLUDE_PATTERNS  # noqa: E402
from aihot.pipeline.prompts import DIGEST_SUMMARY_SYSTEM, ENRICH_SYSTEM  # noqa: E402
from aihot.presentation.compose import EXCLUDED_CATEGORIES  # noqa: E402

PROJECT = m7.PROJECT


def section(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    if begin < 0:
        return ""
    finish = text.find(end, begin + len(start))
    return text[begin : finish if finish > 0 else len(text)]


def main() -> int:
    suite = m3.Suite("m7r_docs")
    readme = (PROJECT / "README.md").read_text(encoding="utf-8")
    design = (PROJECT / "DESIGN.md").read_text(encoding="utf-8")
    plan = (PROJECT / "PLAN.md").read_text(encoding="utf-8")

    # ---------- README ----------
    readme_block = section(readme, "## 内容范围（开发者向）", "\n## ")
    suite.evidence("1-readme-content-range.txt", readme_block)
    readme_requirements = [
        "模型能力突破与发布",
        "开源工具",
        "工程实践",
        "研究突破",
        "硬件资讯：显卡/晶圆/数据中心/液冷/散热/算力基建",
        "公司间商业消息：融资/收购/合作/财报/人事变动",
        "政策监管",
        "论文精选",
        "exclude_keywords",
        "interest_bonus",
        "三道过滤",
        "一层排序加权",
        "整体替换",
    ]
    suite.check(
        "8.1 README「内容范围」表：收录/不收录与三道过滤+一层排序加权机制句齐全（含整体替换说明）",
        all(token in readme_block for token in readme_requirements),
        "缺失=" + str([t for t in readme_requirements if t not in readme_block]),
    )

    # ---------- DESIGN §3.8 ----------
    design_block = section(design, "### 3.8", "\n---")
    suite.evidence("2-design-3.8.txt", design_block)
    design_requirements = [
        "三道过滤 + 一层排序加权",
        "prefilter.exclude_keywords",
        "compose.is_publishable",
        "rank.interest_bonus",
        "行业动态/融资商业/政策监管",
        "importance ≤10",
        "RTX 3090",
        "整体替换",
    ]
    suite.check(
        "8.2 DESIGN §3.8：四道机制（规则/语义/组装/排序）与实现符号一一对应",
        all(token in design_block for token in design_requirements),
        "缺失=" + str([t for t in design_requirements if t not in design_block]),
    )
    suite.check(
        "8.3 DESIGN §3.8 四层描述完整（①规则 ②LLM ③组装 ④排序）",
        all(mark in design_block for mark in ("① ", "② ", "③ ", "④ ")),
        "四个序号标记齐全",
    )

    # ---------- PLAN v1.5 ----------
    plan_row = next((line for line in plan.splitlines() if "| v1.5 |" in line), "")
    suite.evidence("3-plan-v1.5-row.txt", plan_row)
    plan_requirements = [
        "内容范围调整为开发者向",
        "三道过滤 + 一层排序加权",
        "exclude_keywords",
        "interest_bonus",
        "importance ≤10",
        "compose",
        "行业动态/融资商业/政策监管",
    ]
    suite.check(
        "8.4 PLAN 附录 A v1.5 变更记录：四层要点与实现一致",
        all(token in plan_row for token in plan_requirements)
        and plan_row.count("①") == 1
        and plan_row.count("①") + plan_row.count("②") + plan_row.count("③") + plan_row.count("④")
        == 4,
        f"行内容命中={all(t in plan_row for t in plan_requirements)}；"
        f"①②③④计数={sum(plan_row.count(m) for m in '①②③④')}",
    )

    # ---------- 配置模板 / 真实配置 ----------
    example = yaml.safe_load((PROJECT / "config.example.yaml").read_text(encoding="utf-8"))
    example_prefilter = example["pipeline"]["prefilter"]
    real = yaml.safe_load((PROJECT / "config.yaml").read_text(encoding="utf-8"))
    real_prefilter = real["pipeline"]["prefilter"]
    keys_ok = all(
        key in section_prefilter
        for section_prefilter in (example_prefilter, real_prefilter)
        for key in ("exclude_keywords", "use_builtin_exclude")
    )
    suite.check(
        "8.5 配置模板与真实配置均含新键（exclude_keywords / use_builtin_exclude）",
        keys_ok,
        f"example={sorted(example_prefilter)}；real={sorted(real_prefilter)}",
    )
    example_bonus = example["pipeline"]["rank"]["interest_bonus"]
    real_bonus = real["pipeline"]["rank"]["interest_bonus"]
    suite.check(
        "8.6 配置示例/真实配置的 interest_bonus 与代码默认值一致",
        {k: float(v) for k, v in example_bonus.items()} == DEFAULT_INTEREST_BONUS
        and {k: float(v) for k, v in real_bonus.items()} == DEFAULT_INTEREST_BONUS,
        f"example={example_bonus} real={real_bonus} default={DEFAULT_INTEREST_BONUS}",
    )

    # ---------- 文档承诺与代码事实交叉核对 ----------
    joined_patterns = "|".join(DEFAULT_EXCLUDE_PATTERNS)
    bare_word_ok = (
        joined_patterns.count("显卡") == 2
        and "显卡价格" in joined_patterns
        and "显卡涨价" in joined_patterns
        and "gpu" not in joined_patterns.lower()
        and "芯片" not in joined_patterns
    )
    guard_desc = "裸词" in design_block and "可在消费级显卡运行的小模型" in design_block
    suite.check(
        "8.7 「裸词显卡/GPU/芯片不入排除表」的文档承诺与代码事实一致",
        bare_word_ok and guard_desc,
        f"排除表含显卡次数={joined_patterns.count('显卡')}（仅 显卡价格/显卡涨价）；"
        f"含 GPU={'gpu' in joined_patterns.lower()}；含裸芯片={'芯片' in joined_patterns}；"
        f"文档护栏描述={guard_desc}",
    )
    prompt_ok = (
        all(token in ENRICH_SYSTEM for token in ("开发者向", "不收录硬件资讯", "公司间商业消息"))
        and "行业动态" in ENRICH_SYSTEM
        and "融资商业" in ENRICH_SYSTEM
        and "importance" in ENRICH_SYSTEM
        and "只写技术向内容" in DIGEST_SUMMARY_SYSTEM
    )
    suite.check(
        "8.8 提示词与文档承诺一致（LLM 层：硬件/商业 → 行业动态/融资商业 + 低 importance）",
        prompt_ok and "LLM 语义" in design_block,
        "ENRICH_SYSTEM / DIGEST_SUMMARY_SYSTEM 含内容政策",
    )
    compose_ok = frozenset({"行业动态", "融资商业", "政策监管"}) == EXCLUDED_CATEGORIES
    suite.check(
        "8.9 组装层排除集合与文档一致（行业动态/融资商业/政策监管）",
        compose_ok,
        f"EXCLUDED_CATEGORIES={sorted(EXCLUDED_CATEGORIES)}",
    )

    # PLAN v1.5 引用的真实数据复核证据文件可核验（78 条 → 主题剔除 1 条 + 工程零误杀）
    evidence_path = PROJECT / "docs" / "evidence" / "content-policy" / "2026-10-12-recheck.txt"
    evidence_text = evidence_path.read_text(encoding="utf-8") if evidence_path.exists() else ""
    suite.evidence("4-plan-evidence-recheck.txt", evidence_text)
    suite.check(
        "8.10 PLAN v1.5 真实数据复核证据存在且与记述一致（78 条→主题不符 1、工程内容在报）",
        bool(evidence_text)
        and "78" in evidence_text
        and "主题不符 1" in evidence_text
        and "AirLLM" in evidence_text
        and "RTX 3090" in evidence_text
        and "docs/evidence/content-policy/2026-10-12-recheck.txt" in plan_row,
        f"证据文件={evidence_path.name if evidence_text else '缺失'}；"
        f"PLAN 引用={'docs/evidence/content-policy/2026-10-12-recheck.txt' in plan_row}",
    )

    # 文档-行为一致性：interest_bonus「整体替换」在 README/DESIGN 有说明，且行为确为替换
    doc_replacement = "整体替换" in readme_block and "整体替换" in design_block
    partial_ws = m3.workspace("m7r-docs-partial-bonus")
    partial_pipeline = m7.base_pipeline_raw()
    partial_pipeline.setdefault("rank", {})["interest_bonus"] = {"模型发布": 20}
    _, partial_cfg = m7.write_and_load(partial_ws, pipeline=partial_pipeline)
    behavior_replacement = len(partial_cfg.pipeline.rank.interest_bonus) == 1
    suite.check(
        "8.11 interest_bonus「整体替换」语义：文档已说明且行为一致",
        doc_replacement and behavior_replacement,
        f"文档说明={doc_replacement}；部分配置仅保留 1 类={behavior_replacement}",
    )

    # 口径一致性观察：三份文档是否都已统一为「三道过滤 + 一层排序加权」
    labels = {
        "README": "三道过滤" in readme_block and "一层排序加权" in readme_block,
        "DESIGN": "三道过滤" in design_block and "一层排序加权" in design_block,
        "PLAN": "三道过滤" in plan_row and "一层排序加权" in plan_row,
    }
    if all(labels.values()):
        suite.note(
            "口径已统一：README/DESIGN/PLAN 均为「三道过滤 + 一层排序加权」，"
            "与此前 P3-1 建议一致。"
        )
    else:
        suite.note(f"P3（措辞）：三份文档口径未完全统一：{labels}")
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
