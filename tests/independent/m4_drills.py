"""M4 验收 9 独立验证：故障演练（部分源失败 / 全部源失败 / 断网 / LLM key 失效 / 配置损坏）。

全部使用假 LLM / 假 RSS / 假企微；真实 webhook 与真实 LLM 均不使用。
跑法: .venv\\Scripts\\python.exe tests\\independent\\m4_drills.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    CA_PEM,
    FakeRSS,
    FakeWeCom,
    default_llm_generator,
    real_state_snapshot,
    rfc822,
    run_cli,
    workspace,
)
from m4_support import (  # noqa: E402
    FakeLLMProbe,
    Suite,
    db_one,
    make_m4_config,
    write_raw,
)


def rss_entries(prefix: str = "drill") -> list[dict[str, str]]:
    return [
        {
            "title": f"{prefix} 演练 AI 新闻 {index}：模型与工具更新",
            "link": f"https://example.com/{prefix}/{index}",
            "description": "AI model and tool update",
            "pubDate": rfc822(30 + index),
        }
        for index in range(1, 13)
    ]


DEAD_SOURCE = {
    "id": "dead_source",
    "name": "死源",
    "type": "rss",
    "url": "http://127.0.0.1:1/feed",
    "enabled": True,
    "weight": 1.0,
}


def main() -> int:
    suite = Suite("m4_drills")
    before = real_state_snapshot()
    env = {"SSL_CERT_FILE": str(CA_PEM)}

    # ---------- 1. 部分源失败 → 仍出日报并推送 ----------
    ws = workspace("m4-drill-partial")
    with (
        FakeRSS(rss_entries("partial")) as rss,
        FakeLLMProbe() as llm,
        FakeWeCom("ok", proxy=True) as wecom,
    ):
        llm.generator = default_llm_generator
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                {
                    "id": "good_feed",
                    "name": "好源",
                    "type": "rss",
                    "url": rss.url,
                    "enabled": True,
                    "weight": 1.0,
                },
                DEAD_SOURCE,
            ],
        )
        result = run_cli(["run", "--config", str(cfg)], env_extra=env, timeout=900)
        write_raw("30-drill-partial.txt", result.text)
        suite.check(
            "9.1 部分源失败：run rc=0 且输出显示 1/2 源成功",
            result.rc == 0 and "1/2 源成功" in result.stdout and "dead_source" in result.stdout,
            f"rc={result.rc}",
        )
        suite.check(
            "9.2 部分源失败：日报三件套已产出",
            (ws / "data" / "digests").exists()
            and any(p.suffix == ".jpg" for p in (ws / "data" / "digests").iterdir())
            and any(p.suffix == ".md" for p in (ws / "data" / "digests").iterdir()),
            f"files={[p.name for p in (ws / 'data' / 'digests').iterdir()]}",
        )
        suite.check(
            "9.3 部分源失败：图 + markdown 均推送成功",
            len(wecom.markdown_requests) >= 1 and len(wecom.image_requests) >= 1,
            f"markdown={len(wecom.markdown_requests)} image={len(wecom.image_requests)}",
        )
        run_status = db_one(
            ws / "data" / "aihot.db",
            'SELECT status FROM runs WHERE stage_stats LIKE \'%"stage": "run"%\''
            " ORDER BY rowid DESC LIMIT 1",
        )
        fetch_status = db_one(
            ws / "data" / "aihot.db",
            'SELECT status FROM runs WHERE stage_stats LIKE \'%"stage": "fetch"%\''
            " ORDER BY rowid DESC LIMIT 1",
        )
        suite.check(
            "9.4 部分源失败：runs 表 run=ok / fetch=partial",
            run_status == "ok" and fetch_status == "partial",
            f"run={run_status} fetch={fetch_status}",
        )
        suite.check(
            "9.5 部分源失败未触发失败告警",
            len(wecom.text_requests) == 0,
            f"text={len(wecom.text_requests)}",
        )

    # ---------- 2. 全部源失败 → 明确告警且不推空日报 ----------
    ws = workspace("m4-drill-all-fail")
    with FakeLLMProbe() as llm, FakeWeCom("ok", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                DEAD_SOURCE,
                {
                    "id": "dead_source2",
                    "name": "死源2",
                    "type": "rss",
                    "url": "http://127.0.0.1:1/feed2",
                    "enabled": True,
                    "weight": 1.0,
                },
            ],
        )
        result = run_cli(["run", "--config", str(cfg)], env_extra=env, timeout=600)
        write_raw("31-drill-all-fail.txt", result.text)
        suite.check(
            "9.6 全部源失败：rc=1 且状态 failed",
            result.rc == 1 and "状态: failed" in result.stdout,
            f"rc={result.rc}",
        )
        suite.check(
            "9.7 全部源失败：发出企微告警且含原因",
            len(wecom.text_requests) == 1
            and "全部信息源采集失败" in wecom.text_requests[0]["payload"]["text"]["content"],
            f"text={len(wecom.text_requests)}",
            evidence=(
                wecom.text_requests[0]["payload"]["text"]["content"]
                if wecom.text_requests
                else "（无）"
            ),
        )
        suite.check(
            "9.8 全部源失败：未推任何日报（图/markdown 均为 0）",
            len(wecom.markdown_requests) == 0 and len(wecom.image_requests) == 0,
            f"markdown={len(wecom.markdown_requests)} image={len(wecom.image_requests)}",
        )
        suite.check(
            "9.9 全部源失败：未产生半成品日报与 digests 行",
            not (ws / "data" / "digests").exists()
            and not db_one(ws / "data" / "aihot.db", "SELECT COUNT(*) FROM digests"),
            f"digests_dir={(ws / 'data' / 'digests').exists()}",
        )
        suite.check(
            "9.10 全部源失败：LLM 未被调用（失败即止）",
            llm.request_count == 0,
            f"llm_calls={llm.request_count}",
        )

    # ---------- 3. 断网演练（源域名不可解析，企微仍可达） ----------
    ws = workspace("m4-drill-offline")
    with FakeLLMProbe() as llm, FakeWeCom("ok", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                {
                    "id": "offline_source",
                    "name": "断网源",
                    "type": "rss",
                    "url": "http://offline.invalid/feed",  # RFC2606：永不解析
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
        )
        result = run_cli(["run", "--config", str(cfg)], env_extra=env, timeout=600)
        write_raw("32-drill-offline.txt", result.text)
        suite.check(
            "9.11 断网（源不可达）：rc=1、状态 failed、发出告警",
            result.rc == 1
            and "状态: failed" in result.stdout
            and len(wecom.text_requests) == 1
            and "全部信息源采集失败" in wecom.text_requests[0]["payload"]["text"]["content"],
            f"rc={result.rc} text={len(wecom.text_requests)}",
            evidence=result.text,
        )
        suite.check(
            "9.11b 断网：未推空日报",
            len(wecom.markdown_requests) == 0 and len(wecom.image_requests) == 0,
            f"markdown={len(wecom.markdown_requests)} image={len(wecom.image_requests)}",
        )

    # ---------- 4. LLM key 失效演练 ----------
    ws = workspace("m4-drill-llm-bad")
    with (
        FakeRSS(rss_entries("llmbad")) as rss,
        FakeLLMProbe() as llm,
        FakeWeCom("ok", proxy=True) as wecom,
    ):
        llm.mode = "http401"
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                {
                    "id": "good_feed",
                    "name": "好源",
                    "type": "rss",
                    "url": rss.url,
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
        )
        result = run_cli(["run", "--config", str(cfg)], env_extra=env, timeout=900)
        write_raw("33-drill-llm-bad.txt", result.text)
        suite.check(
            "9.12 LLM key 失效：降级运行不崩溃（rc=0）",
            result.rc == 0 and "状态: ok" in result.stdout,
            f"rc={result.rc}",
        )
        markdown_body = (
            wecom.markdown_requests[0]["payload"]["markdown"]["content"]
            if wecom.markdown_requests
            else ""
        )
        suite.check(
            "9.13 LLM key 失效：批次失败可见且条目按原文降级",
            "失败 3" in result.stdout
            and "llmbad 演练 AI 新闻" in markdown_body
            and len(wecom.markdown_requests) >= 1,
            f"批次失败显示={'是' if '失败 3' in result.stdout else '否'}；"
            f"markdown 含原文标题={'是' if 'llmbad 演练 AI 新闻' in markdown_body else '否'}",
            evidence=markdown_body,
        )
        suite.check(
            "9.14 LLM key 失效：日报仍完整落盘（三件套）",
            (ws / "data" / "digests").exists()
            and len(list((ws / "data" / "digests").iterdir())) >= 3,
            f"files={[p.name for p in (ws / 'data' / 'digests').iterdir()]}",
        )
        enrich_status = db_one(
            ws / "data" / "aihot.db",
            'SELECT status FROM runs WHERE stage_stats LIKE \'%"stage": "enrich"%\''
            " ORDER BY rowid DESC LIMIT 1",
        )
        suite.note(f"LLM 全失败时 enrich 运行记录 status={enrich_status}（run 记录为 ok）")

    # ---------- 5. 配置损坏演练：启动即报错，不发半成品 ----------
    ws = workspace("m4-drill-broken")
    with FakeWeCom("ok", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        broken = ws / "config.yaml"
        broken.write_text("app: [broken\nsources: []\n", encoding="utf-8")
        result = run_cli(["run", "--dry-run", "--config", str(broken)], env_extra=env, timeout=120)
        write_raw("34-drill-broken-config.txt", result.text)
        suite.check(
            "9.15 配置损坏：rc=3 且无 traceback",
            result.rc == 3 and "Traceback" not in result.combined,
            f"rc={result.rc}",
        )
        suite.check(
            "9.16 配置损坏：未触碰网络（webhook 0 请求）",
            len(wecom.requests) == 0,
            f"requests={len(wecom.requests)}",
        )
        suite.check("9.17 配置损坏：未产生数据目录/日报", not (ws / "data").exists(), "data 不存在")

    after = real_state_snapshot()
    suite.check("9.18 全部演练未改动真实库/存档", before == after, "真实状态一致")
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
