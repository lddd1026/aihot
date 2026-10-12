"""M3 端到端：CLI `run` / `preview` / `resend` 全链路（全假服务，零真实外发）。

链路：假 RSS → 预筛/聚类 → 假 LLM（计数）→ 组装 → 长图（系统 Edge）→
      假企微（HTTPS CONNECT 代理 + MITM）→ 假 SMTP → 存档 → 幂等/补发。

覆盖验收项 4/5/6/7 的集成部分：
- run 一次成功：图 + markdown + 邮件三路送达，存档三件套 + digests/runs 落库
- 同日重复 run：不重复推送、不重复调 LLM
- --dry-run：不发送但产出三件套
- preview：不发送、rc=0
- resend --channel wecom/email：定向补发且不重跑 LLM
- --top 覆盖日报精选条数
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    CA_PEM,
    FakeLLM,
    FakeRSS,
    FakeSMTP,
    FakeWeCom,
    Suite,
    db_one,
    db_query,
    default_llm_generator,
    parse_email,
    real_state_snapshot,
    rfc822,
    run_cli,
    workspace,
    write_config,
)

WECOM_LIMIT = 4096

TOPICS = [
    "OpenAI 发布 GPT-6 Sol 模型，推理成本下降 40%",
    "Anthropic 开源 Claude Agent 工具链 claude-tools",
    "Meta 推出新一代开源大模型 Llama 5",
    "Google DeepMind 发布 AlphaFold 4 蛋白质预测模型",
    "Hugging Face 上线 Daily Papers 中文摘要功能",
    "vLLM 发布 1.2 版本，推理吞吐提升 2 倍",
    "微软发布 Copilot 企业版 Agent 工作流",
    "英伟达发布 Rubin GPU 架构，AI 算力翻倍",
    "斯坦福发布 2026 年 AI 指数报告",
    "中国信通院发布大模型合规评估标准",
    "Mistral 发布欧洲语言专用模型 Mistral Large 3",
    "阿里通义千问开源 Qwen4 系列模型",
    "字节跳动开源多模态模型 Seed-VL",
    "xAI 发布 Grok 4，主打实时推理",
    "腾讯混元发布 3D 生成大模型",
    "DeepSeek 发布 V4 模型并开源权重",
    "AI 芯片初创公司完成 5 亿美元融资",
    "欧盟 AI 法案实施细则正式生效",
]


def main() -> int:
    suite = Suite("m3_e2e")
    before = real_state_snapshot()
    root = workspace("e2e")
    data_dir = root / "data"
    digests_dir = data_dir / "digests"
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    entries = [
        {
            "title": title,
            "link": f"https://example.com/news/{index}",
            "description": f"AI news: {title}",
            "pubDate": rfc822(30 + index),
        }
        for index, title in enumerate(TOPICS, start=1)
    ]

    with (
        FakeRSS(entries) as rss,
        FakeLLM() as llm,
        FakeWeCom("ok", proxy=True) as wecom,
        FakeSMTP() as smtp,
    ):
        llm.generator = default_llm_generator
        cfg_path = write_config(
            root / "config.yaml",
            data_dir=data_dir,
            log_dir=root / "logs",
            sources=[
                {
                    "id": "fake_feed",
                    "name": "Fake AI Feed",
                    "type": "rss",
                    "url": rss.url,
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
            llm_base_url=llm.base_url,
            wecom_enabled=True,
            wecom_webhook=wecom.url,
            email_enabled=True,
            email_overrides={
                "smtp_host": "127.0.0.1",
                "smtp_port": smtp.port,
                "use_ssl": False,
                "attach_image": False,
            },
            top_n=12,
        )
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}

        # ---------- 1. 首次 run ----------
        run1 = run_cli(["run", "--config", str(cfg_path)], env_extra=env, timeout=600)
        suite.evidence("1-run1.txt", run1.text)
        suite.check(
            "6.1 run 首次执行 rc=0 且状态 ok",
            run1.rc == 0 and "状态: ok" in run1.stdout,
            f"rc={run1.rc} ({run1.seconds:.1f}s)",
        )
        suite.check(
            "6.2 采集：假 RSS 被访问且抓到条目",
            rss.hits >= 1 and "新增 18 条" in run1.stdout,
            f"rss_hits={rss.hits} "
            f"输出片段={[line for line in run1.stdout.splitlines() if '① 采集' in line]}",
        )
        suite.check(
            "6.3 加工：假 LLM 被调用（批量+综述）",
            llm.request_count >= 4 and "② 加工" in run1.stdout,
            f"llm_requests={llm.request_count}",
        )
        image_requests = wecom.image_requests
        markdown_requests = wecom.markdown_requests
        suite.check(
            "6.4 企微：先图后文，图 1 张 + markdown 分片",
            len(image_requests) == 1
            and len(markdown_requests) >= 1
            and wecom.requests[0]["payload"]["msgtype"] == "image"
            and all(r["tls"] for r in wecom.requests),
            f"image={len(image_requests)} markdown={len(markdown_requests)} "
            f"tls={wecom.requests[0]['tls']}",
        )
        image_path = digests_dir / f"{today}.jpg"
        body = image_requests[0]["payload"]["image"]
        suite.check(
            "6.5 企微图片 base64+md5 与存档文件一致",
            image_path.exists()
            and base64.b64decode(body["base64"]) == image_path.read_bytes()
            and body["md5"] == hashlib.md5(image_path.read_bytes()).hexdigest(),
            f"file={image_path.name} "
            f"size={image_path.stat().st_size if image_path.exists() else 0}",
        )
        md_sizes = [
            len(r["payload"]["markdown"]["content"].encode("utf-8")) for r in markdown_requests
        ]
        md_text = "\n\n".join(r["payload"]["markdown"]["content"] for r in markdown_requests)
        suite.check(
            "6.6 企微 markdown：≤4096B/条 且含综述与条目链接",
            all(size <= WECOM_LIMIT for size in md_sizes)
            and "今日综述" in md_text
            and "https://example.com/news/" in md_text,
            f"sizes={md_sizes} 综述={'今日综述' in md_text}",
        )
        email_count = len(smtp.messages)
        message = parse_email(smtp.messages[0]["data"]) if smtp.messages else None
        email_html = ""
        if message is not None:
            for part in message.walk():
                if part.get_content_type() == "text/html":
                    email_html = part.get_content()
        suite.check(
            "6.7 邮件：1 封、主题/收件人正确、HTML 含综述与链接",
            email_count == 1
            and message is not None
            and message["Subject"] == f"AI 热点日报 · {today}"
            and message["To"] == "receiver@example.com"
            and "今日综述" in email_html
            and "https://example.com/news/" in email_html,
            f"email={email_count} subject={message['Subject'] if message else None}",
        )
        suite.check(
            "6.8 邮件：attach_image=false 无内嵌图",
            "image/" not in " ".join(part.get_content_type() for part in message.walk())
            if message is not None
            else False,
            "无 image/* 部件",
        )
        suite.check(
            "6.9 存档：三件套文件 + digests 表 + runs 表",
            (digests_dir / f"{today}.jpg").exists()
            and (digests_dir / f"{today}.md").exists()
            and (digests_dir / f"{today}.html").exists()
            and db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM digests") == 1
            and db_one(
                data_dir / "aihot.db",
                'SELECT COUNT(*) FROM runs WHERE stage_stats LIKE \'%"stage": "run"%\'',
            )
            == 1,
            f"digest_rows={db_one(data_dir / 'aihot.db', 'SELECT COUNT(*) FROM digests')}",
        )
        digest_row = db_query(data_dir / "aihot.db", "SELECT * FROM digests")[0]
        payload = json.loads(digest_row["payload"])
        suite.check(
            "6.10 存档 payload：Top 12 + 简讯 6 + 综述 + 统计",
            len(payload["top_items"]) == 12
            and len(payload["briefs"]) == 6
            and bool(payload["summary"])
            and payload["stats"]["selected"] == 12,
            f"top={len(payload['top_items'])} briefs={len(payload['briefs'])} "
            f"summary_len={len(payload['summary'])}",
        )
        archive_md = (digests_dir / f"{today}.md").read_text(encoding="utf-8")
        suite.check(
            "6.11 存档 md 含综述/条目链接/统计页脚",
            "今日综述" in archive_md
            and "https://example.com/news/" in archive_md
            and "共扫描" in archive_md,
            f"bytes={len(archive_md.encode('utf-8'))}",
        )
        llm_after_run1 = llm.request_count
        push_after_run1 = len(wecom.requests)
        email_after_run1 = len(smtp.messages)
        run1_seconds = run1.seconds

        # ---------- 2. 同日重复 run（幂等） ----------
        run2 = run_cli(["run", "--config", str(cfg_path)], env_extra=env, timeout=600)
        suite.evidence("2-run2-repeat.txt", run2.text)
        suite.check(
            "6.12 同日重复 run：rc=0、无新推送、无新 LLM 调用",
            run2.rc == 0
            and len(wecom.requests) == push_after_run1
            and len(smtp.messages) == email_after_run1
            and llm.request_count == llm_after_run1,
            f"rc={run2.rc} wecom={len(wecom.requests)}(+{len(wecom.requests) - push_after_run1}) "
            f"email={len(smtp.messages)} llm={llm.request_count}",
        )
        suite.check(
            "6.13 重复 run 提示无待加工条目",
            "没有待加工条目" in run2.stdout,
            [line for line in run2.stdout.splitlines() if "没有待加工" in line or "状态:" in line],
        )

        # ---------- 3. --dry-run（不发送、产出三件套） ----------
        for suffix in ("jpg", "md", "html"):
            (digests_dir / f"{today}.{suffix}").unlink()
        runs_before_dry = db_one(
            data_dir / "aihot.db",
            'SELECT COUNT(*) FROM runs WHERE stage_stats LIKE \'%"stage": "run"%\'',
        )
        llm_before_dry = llm.request_count
        run3 = run_cli(["run", "--config", str(cfg_path), "--dry-run"], env_extra=env, timeout=600)
        suite.evidence("3-run3-dry-run.txt", run3.text)
        suite.check(
            "6.14 --dry-run：rc=0、不发送、产出三件套",
            run3.rc == 0
            and len(wecom.requests) == push_after_run1
            and len(smtp.messages) == email_after_run1
            and all((digests_dir / f"{today}.{s}").exists() for s in ("jpg", "md", "html")),
            f"rc={run3.rc} wecom={len(wecom.requests)} email={len(smtp.messages)} "
            f"files={sorted(p.name for p in digests_dir.iterdir())}",
        )
        runs_after_dry = db_one(
            data_dir / "aihot.db",
            'SELECT COUNT(*) FROM runs WHERE stage_stats LIKE \'%"stage": "run"%\'',
        )
        suite.check(
            "6.15 --dry-run：不写 runs 记录（LLM 综述补写次数单独记录）",
            runs_after_dry == runs_before_dry,
            f"runs_before={runs_before_dry} runs_after={runs_after_dry} "
            f"llm_delta={llm.request_count - llm_before_dry}（无待加工条目时补写综述）",
        )

        # ---------- 4. preview（不发送） ----------
        preview = run_cli(["preview", "--config", str(cfg_path)], timeout=600)
        suite.evidence("4-preview.txt", preview.text)
        suite.check(
            "6.16 preview：rc=0、展示文件与企微预览、不发送",
            preview.rc == 0
            and "企微 markdown 预览" in preview.stdout
            and f"日报日期: {today}" in preview.stdout
            and len(wecom.requests) == push_after_run1
            and len(smtp.messages) == email_after_run1,
            f"rc={preview.rc} ({preview.seconds:.1f}s)",
        )
        preview_no_image = run_cli(
            ["preview", "--config", str(cfg_path), "--no-image"], timeout=600
        )
        (digests_dir / f"{today}.jpg").unlink()
        suite.check(
            "6.17 preview --no-image：不产图但 md/html 正常",
            preview_no_image.rc == 0
            and not (digests_dir / f"{today}.jpg").exists()
            and (digests_dir / f"{today}.md").exists()
            and (digests_dir / f"{today}.html").exists(),
            f"rc={preview_no_image.rc}",
        )

        # ---------- 5. --top 覆盖 ----------
        run_top = run_cli(
            ["run", "--config", str(cfg_path), "--dry-run", "--top", "3"],
            env_extra=env,
            timeout=600,
        )
        suite.evidence("5-run-top3.txt", run_top.text)
        suite.check(
            "6.18 run --top 3：日报精选 3 条 + 简讯取满 12 条（BRIEF_COUNT 上限）",
            run_top.rc == 0 and "精选 3 条" in run_top.stdout and "简讯 12 条" in run_top.stdout,
            [line for line in run_top.stdout.splitlines() if "③ 日报" in line],
        )

        # ---------- 6. resend 定向补发（不重跑 LLM） ----------
        llm_before_resend = llm.request_count
        resend_wecom = run_cli(
            ["resend", "--config", str(cfg_path), "--channel", "wecom"], env_extra=env, timeout=600
        )
        suite.evidence("6-resend-wecom.txt", resend_wecom.text)
        suite.check(
            "6.19 resend --channel wecom：补发图+文、不发邮件、不调 LLM",
            resend_wecom.rc == 0
            and "补发完成" in resend_wecom.stdout
            and len(wecom.image_requests) == 2
            and len(wecom.markdown_requests) > len(markdown_requests)
            and len(smtp.messages) == email_after_run1
            and llm.request_count == llm_before_resend,
            f"rc={resend_wecom.rc} images={len(wecom.image_requests)} llm={llm.request_count}",
        )
        resend_email = run_cli(
            ["resend", "--config", str(cfg_path), "--channel", "email"], env_extra=env, timeout=600
        )
        suite.evidence("7-resend-email.txt", resend_email.text)
        suite.check(
            "6.20 resend --channel email：补发邮件、不发企微、不调 LLM",
            resend_email.rc == 0
            and len(smtp.messages) == email_after_run1 + 1
            and len(wecom.image_requests) == 2
            and llm.request_count == llm_before_resend,
            f"rc={resend_email.rc} emails={len(smtp.messages)} llm={llm.request_count}",
        )
        resend_missing = run_cli(
            ["resend", "--config", str(cfg_path), "--date", "1999-01-01"],
            env_extra=env,
            timeout=120,
        )
        suite.check(
            "6.21 resend 无存档日期：rc=1 且提示明确（无 traceback）",
            resend_missing.rc == 1 and "没有 1999-01-01 的日报存档" in resend_missing.stdout,
            f"rc={resend_missing.rc}",
        )

        suite.evidence_json(
            "e2e-metrics.json",
            {
                "run1_seconds": round(run1_seconds, 1),
                "llm_requests": llm.request_count,
                "wecom_requests": len(wecom.requests),
                "email_messages": len(smtp.messages),
                "markdown_parts_run1": len(markdown_requests),
                "markdown_sizes": md_sizes,
                "image_bytes": image_path.stat().st_size if image_path.exists() else None,
                "today": today,
            },
        )

    after = real_state_snapshot()
    suite.check("6.22 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
