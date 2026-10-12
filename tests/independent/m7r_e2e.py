"""M7 独立验收 · 声明 6：端到端（假 RSS + 假 LLM，技术/硬件/商业混合）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m7r_e2e.py
证据: reports/evidence/m7r/m7r_e2e/ 与 m7r_e2e.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m7r_support as m7  # noqa: E402

# (标题, 期望去向)  —— rule=规则层剔除 / escape=规则放行、语义层与组装层负责
FIXTURES: list[tuple[str, str]] = [
    ("AirLLM 新版发布：单卡 4GB GPU 跑 70B 大模型", "keep"),
    ("vLLM 推理优化：PagedAttention 让吞吐提升 3 倍", "keep"),
    ("Qwen3-Coder 发布：面向智能体的开源代码模型", "keep"),
    ("从 0 搭建 RAG 评测流水线：踩坑与经验", "keep"),
    ("台积电 2nm 晶圆产能被 AI 公司预订一空", "rule"),
    ("AI startup raises $50M to reinvent search", "rule"),
    ("RTX 5090 涨价，AI 公司抢购高端显卡", "escape"),
    ("Mistral AI partners with EuroCloud in multi-year cloud deal", "escape"),
]

FORBIDDEN_IN_DIGEST = ["台积电", "raises $50M", "RTX 5090", "涨价", "EuroCloud"]
TECH_MARKERS = ["AirLLM", "vLLM", "Qwen3-Coder", "RAG 评测流水线"]
TOP_LINE = re.compile(r"^\s*\d+\.\s*\[\s*([-\d.]+)\]\s+\(([^)]+)\)", re.MULTILINE)


def mixed_generator(body: dict[str, Any] | None) -> str:
    """假 LLM：按标题语义给出分类（硬件/商业条目故意给低 importance）。"""
    messages = (body or {}).get("messages") or []
    system = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    user = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
    if "今日综述" in user or "主编" in system:
        return "今日综述：开源推理框架与模型发布活跃，工程实践与评测方法值得关注。"
    items: list[dict[str, Any]] = []
    for line in user.splitlines():
        match = re.match(r"^\[(\d+)\]\s*来源：.*?\|\s*标题：(.*?)\s*\|\s*摘要：", line)
        if match is None:
            continue
        index, title = int(match.group(1)), match.group(2)
        if "晶圆" in title or "涨价" in title:
            category, importance = "行业动态", 10
        elif "partners" in title or "合作" in title:
            category, importance = "融资商业", 10
        elif "Qwen" in title:
            category, importance = "模型发布", 85
        elif "vLLM" in title or "AirLLM" in title:
            category, importance = "开源工具", 80
        else:
            category, importance = "研究突破", 65
        items.append(
            {
                "index": index,
                "cn_title": title,
                "cn_summary": f"第 {index} 条夹具摘要：开发者向内容验证。",
                "category": category,
                "importance": importance,
                "is_ai": True,
            }
        )
    return json.dumps({"items": items}, ensure_ascii=False)


def main() -> int:
    suite = m3.Suite("m7r_e2e")
    entries = [
        {
            "title": title,
            "link": f"https://example.com/m7r/e2e/{index}",
            "description": "m7r fixture",
            "pubDate": m3.rfc822(10 + index),
        }
        for index, (title, _) in enumerate(FIXTURES, start=1)
    ]
    env = m7.m5.clean_proxy_env({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

    with m3.FakeRSS(entries) as rss, m3.FakeLLM() as llm:
        llm.generator = mixed_generator
        ws = m3.workspace("m7r-e2e")
        assert rss.url.endswith("/feed.xml")
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
            llm_base_url=llm.base_url,
            llm_api_key="sk-m7r-fake",
            top_n=12,
        )

        # 1) 先把 8 条混合条目落库（run --dry-run 不落库，需要先 fetch）
        fetched = m3.run_cli(["fetch", "--config", str(cfg_path)], env_extra=env, timeout=300)
        suite.evidence("1-fetch.txt", fetched.text)
        suite.check(
            "6.1 fetch 落库 8 条混合条目 rc=0",
            fetched.rc == 0 and "新增 8 条" in fetched.stdout,
            f"rc={fetched.rc}；" + next(
                (line.strip() for line in fetched.stdout.splitlines() if "① 采集" in line), ""
            ),
        )

        # 2) run --dry-run：完整跑一轮（假 LLM），不推送
        llm_before = llm.request_count
        run1 = m3.run_cli(
            ["run", "--dry-run", "--no-image", "--config", str(cfg_path)],
            env_extra=env,
            timeout=600,
        )
        suite.evidence("2-run-dry-run.txt", run1.text)
        suite.check(
            "6.2 run --dry-run rc=0 且状态 ok，推送已跳过",
            run1.rc == 0 and "状态: ok" in run1.stdout and "已跳过（dry-run）" in run1.stdout,
            f"rc={run1.rc}",
        )
        suite.check(
            "6.3 规则层先剔除 2 条（8 入 → 预筛保留 6）",
            "8 条 → 预筛保留 6" in run1.stdout,
            next((line.strip() for line in run1.stdout.splitlines() if "② 加工" in line), ""),
        )
        suite.check(
            "6.4 组装层再剔除 2 条擦边（精选 4 条 + 简讯 0 条）",
            "精选 4 条" in run1.stdout and "简讯 0 条" in run1.stdout,
            next((line.strip() for line in run1.stdout.splitlines() if "③ 日报" in line), ""),
        )
        run_llm_requests = len(llm.requests) - llm_before

        # 3) 日报存档：只含技术类
        md_files = sorted((ws / "data" / "digests").glob("*.md"))
        suite.check(
            "6.5 日报 markdown 已产出（dry-run 也会存档预览）",
            len(md_files) == 1 and md_files[0].stat().st_size > 0,
            f"files={[p.name for p in md_files]}",
        )
        md_text = md_files[0].read_text(encoding="utf-8") if md_files else ""
        suite.evidence("3-digest-archive.md", md_text)
        missing_tech = [marker for marker in TECH_MARKERS if marker not in md_text]
        leaked = [marker for marker in FORBIDDEN_IN_DIGEST if marker in md_text]
        suite.check(
            "6.6 最终日报只含技术类：4 个技术标题全在，硬件/商业词零出现",
            not missing_tech and not leaked,
            f"缺失技术标题={missing_tech} 泄漏非技术词={leaked}",
        )
        suite.check(
            "6.7 日报 markdown 无 行业动态/融资商业/政策监管 分类标签",
            all(tag not in md_text for tag in ("行业动态", "融资商业", "政策监管")),
            "分类标签检查完成",
        )
        payload_row = m3.db_query(
            ws / "data" / "aihot.db",
            "SELECT payload FROM digests ORDER BY created_at DESC LIMIT 1",
        )
        payload = json.loads(payload_row[0]["payload"]) if payload_row else {}
        payload_categories = [
            item.get("category")
            for key in ("top_items", "papers", "briefs")
            for item in payload.get(key, [])
        ]
        suite.check(
            "6.8 存档 payload 各区块分类全部非排除分类（结构化核对）",
            bool(payload_row)
            and all(
                category not in {"行业动态", "融资商业", "政策监管"}
                for category in payload_categories
            ),
            f"categories={payload_categories}",
        )

        # 4) 假 LLM 实际收到的系统提示词（不是只查常量）
        enrich_systems = [
            system_content(request)
            for request in llm.requests
            if "资深 AI 技术编辑" in system_content(request)
        ]
        summary_systems = [
            system_content(request)
            for request in llm.requests
            if "主编" in system_content(request)
        ]
        enrich_users = [
            user_content(request)
            for request in llm.requests
            if "资深 AI 技术编辑" in system_content(request)
        ]
        all_enrich_user = "\n".join(enrich_users)
        suite.evidence("4-enrich-system-prompt.txt", enrich_systems[0] if enrich_systems else "")
        if summary_systems:
            suite.evidence("4-summary-system-prompt.txt", summary_systems[0])
        suite.check(
            "6.9 假 LLM 实际收到的加工系统提示词含新内容政策",
            bool(enrich_systems)
            and all(
                all(token in system for token in ("开发者向", "不收录硬件资讯", "公司间商业消息"))
                for system in enrich_systems
            ),
            f"加工批次={len(enrich_systems)}；片段="
            + (enrich_systems[0][:80] if enrich_systems else "（无）"),
        )
        suite.check(
            "6.10 综述系统提示词同步内容政策（只写技术向、不写硬件/商业）",
            bool(summary_systems)
            and all(
                all(token in system for token in ("只写技术向内容", "不要写硬件资讯"))
                for system in summary_systems
            ),
            f"综述请求={len(summary_systems)}",
        )
        suite.check(
            "6.11 载荷核对：擦边条目确实被送给 LLM（而非规则层提前剔除）",
            "RTX 5090" in all_enrich_user
            and "EuroCloud" in all_enrich_user
            and "台积电" not in all_enrich_user
            and "raises $50M" not in all_enrich_user,
            f"LLM 请求总次数（本轮）={run_llm_requests}；"
            f"含 RTX 5090={'RTX 5090' in all_enrich_user}",
        )

        # 5) CLI 排序证据：enrich --dry-run 打印 Top（含负分商业条目）
        enrich = m3.run_cli(
            ["enrich", "--dry-run", "--top", "10", "--config", str(cfg_path)],
            env_extra=env,
            timeout=600,
        )
        suite.evidence("5-enrich-top.txt", enrich.text)
        topic_sample_line = next(
            (line.strip() for line in enrich.stdout.splitlines() if "主题不符" in line), ""
        )
        suite.check(
            "6.12 CLI 预筛样本显示「主题不符（硬件/商业）」原因",
            "主题不符（硬件/商业）" in enrich.stdout and "台积电" in enrich.stdout,
            topic_sample_line,
        )
        rows = [
            (float(score), category)
            for score, category in TOP_LINE.findall(enrich.stdout)
        ]
        business_rows = [score for score, cat in rows if cat in {"行业动态", "融资商业"}]
        tech_rows = [
            score
            for score, cat in rows
            if cat not in {"行业动态", "融资商业", "政策监管"}
        ]
        suite.check(
            "6.13 CLI 分数验证：商业/硬件类分数低于全部技术类（负分生效）",
            bool(business_rows)
            and bool(tech_rows)
            and max(business_rows) < min(tech_rows),
            f"商业类={sorted(business_rows)}；技术类={sorted(tech_rows)}",
        )
        suite.evidence_json(
            "6-cli-top-rows.json",
            {"rows": [{"score": score, "category": category} for score, category in rows]},
        )

    return suite.finish()


def system_content(request: dict[str, Any]) -> str:
    return _role_content(request, "system")


def user_content(request: dict[str, Any]) -> str:
    return _role_content(request, "user")


def _role_content(request: dict[str, Any], role: str) -> str:
    body = request.get("body") or {}
    messages = body.get("messages") or []
    return " ".join(str(m.get("content") or "") for m in messages if m.get("role") == role)


if __name__ == "__main__":
    raise SystemExit(main())
