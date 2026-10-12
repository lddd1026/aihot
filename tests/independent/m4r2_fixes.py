"""M4 第 2 轮回归：6 项修复独立复验。

覆盖：企微 key / 渲染告警 / bat 文案 / arxiv 探测 / dry-run 回落 / 分片提示 / link_selector。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m4r2_fixes.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["AIHOT_M4_EVIDENCE"] = "m4/regression2"

from m3_support import (  # noqa: E402
    CA_PEM,
    REAL_CONFIG,
    FakeRSS,
    FakeWeCom,
    rfc822,
    run_cli,
    seed_enriched_items,
    workspace,
    write_config,
)
from m4_support import (  # noqa: E402
    FakeHTTP,
    FakeLLMProbe,
    FakeWeComProbe,
    Suite,
    db_one,
    make_m4_config,
    write_raw,
)

BOGUS_HOOK = (
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=00000000-0000-0000-0000-000000000000"
)


def mask(value: str) -> str:
    return f"<len={len(value)} sha1={hashlib.sha1(value.encode()).hexdigest()[:8]}>"


def check_with_wecom(
    name: str,
    mode: str,
    *,
    timeout: float = 120.0,
) -> tuple[dict[str, object], FakeWeComProbe]:
    """用假企微（HTTPS MITM）跑 check --quick，返回结果（调用方负责停止服务）。"""
    ws = workspace(f"m4r2-{name}")
    with FakeLLMProbe() as llm:
        wecom = FakeWeComProbe(mode, proxy=True)
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
        )
        result = run_cli(["check", "--quick", "--config", str(cfg)], env_extra=env, timeout=timeout)
    return {"rc": result.rc, "out": result.combined, "text": result.text}, wecom


# --------------------------------------------------------------------------- F1 企微 key


def suite_wecom_key(suite: Suite) -> None:
    # 93000 → 必须失败
    case, wecom = check_with_wecom("wecom-93000", "errcode")
    suite.check(
        "R2-1.1 假企微 errcode=93000 → check rc=1 且提示 key 失效",
        case["rc"] == 1 and "[ERR] 企业微信" in case["out"] and "93000" in str(case["out"]),
        f"rc={case['rc']}",
        evidence=str(case["text"]),
    )
    suite.check(
        "R2-1.2 探测只发 GET，未向 webhook POST 任何消息",
        len(wecom.requests) == 0,
        f"POST 记录={len(wecom.requests)}",
    )
    wecom.stop()

    # 93017（GET 的正常结果）→ 必须成功
    case, wecom = check_with_wecom("wecom-93017", "errcode_other")
    suite.check(
        "R2-1.3 假企微 errcode=93017 → check rc=0（key 有效，GET 体非法属预期）",
        case["rc"] == 0 and "[OK ] 企业微信" in case["out"] and "93017" in str(case["out"]),
        f"rc={case['rc']}",
        evidence=str(case["text"]),
    )
    wecom.stop()

    # errcode=0 → 成功
    case, wecom = check_with_wecom("wecom-0", "ok")
    suite.check(
        "R2-1.4 假企微 errcode=0 → rc=0",
        case["rc"] == 0 and "[OK ] 企业微信" in case["out"],
        f"rc={case['rc']}",
    )
    wecom.stop()

    # 非 JSON → 保守成功
    case, wecom = check_with_wecom("wecom-nonjson", "nonjson")
    suite.check(
        "R2-1.5 企微响应非 JSON → 保守判定可达（rc=0）",
        case["rc"] == 0 and "非 JSON" in case["out"],
        f"rc={case['rc']}",
        evidence=str(case["text"]),
    )
    wecom.stop()

    # JSON 数组（对抗）：不应裸 traceback
    case, wecom = check_with_wecom("wecom-json-array", "json_array")
    traceback = "Traceback (most recent call last)" in str(case["out"])
    suite.check(
        "R2-1.6 企微返回 JSON 数组（畸形体）→ 不裸 traceback",
        not traceback,
        f"rc={case['rc']} traceback={traceback}",
        evidence=str(case["text"]) if traceback else None,
    )
    wecom.stop()

    # 真实 qyapi：假 key（GET 不产生消息）→ 必须 rc=1
    ws = workspace("m4r2-wecom-real-bogus")
    with FakeLLMProbe() as llm:
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url=llm.base_url,
            wecom_webhook=BOGUS_HOOK,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
        )
        import yaml

        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        raw["proxy"] = {"enabled": True, "url": "http://127.0.0.1:7897"}
        cfg.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        case = run_cli(["check", "--quick", "--config", str(cfg)], timeout=180)
    suite.check(
        "R2-1.7 真实 qyapi + 假 key → rc=1（上轮 P1-1 复现用例已转绿）",
        case.rc == 1 and "93000" in case.combined,
        f"rc={case.rc}；webhook={mask(BOGUS_HOOK)}",
        evidence=case.text,
    )

    # 真实 qyapi + 真实 key → GET 返回 93017，判成功（不发送消息）
    import yaml as _yaml

    real_raw = _yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    real_hook = str(real_raw["push"]["wecom"]["webhook"])
    ws = workspace("m4r2-wecom-real-key")
    with FakeLLMProbe() as llm:
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url=llm.base_url,
            wecom_webhook=real_hook,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
        )
        raw = _yaml.safe_load(cfg.read_text(encoding="utf-8"))
        raw["proxy"] = {"enabled": True, "url": "http://127.0.0.1:7897"}
        cfg.write_text(
            _yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        case = run_cli(["check", "--quick", "--config", str(cfg)], timeout=180)
    suite.check(
        "R2-1.8 真实 qyapi + 真实 key → rc=0（GET 仅探测，未发消息）",
        case.rc == 0 and "93017" in case.combined,
        f"rc={case.rc}；webhook={mask(real_hook)}",
        evidence=case.text,
    )


# --------------------------------------------------------------------------- F2 渲染/存档告警


def suite_failure_alerts(suite: Suite) -> None:
    ws = workspace("m4r2-alerts")
    with FakeWeCom("ok", proxy=True) as wecom:
        os.environ["SSL_CERT_FILE"] = str(CA_PEM)
        cfg_path = write_config(
            ws / "config.yaml",
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                {
                    "id": "fake_feed",
                    "name": "Fake",
                    "type": "rss",
                    "url": "http://127.0.0.1:1/feed",
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
            llm_base_url="http://127.0.0.1:1/v1",
            wecom_enabled=True,
            wecom_webhook=wecom.url,
        )
        import yaml

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        raw["proxy"] = {"enabled": True, "url": f"http://127.0.0.1:{wecom.port}"}
        cfg_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

        from m3_support import make_item

        import aihot.runner as runner
        from aihot.config import load_config
        from aihot.presentation import RenderError
        from aihot.runner import EnrichOutcome, FetchOutcome, RenderOutcome
        from aihot.sources import SourceStat

        cfg = load_config(cfg_path)
        original = {
            "fetch": runner.fetch_stage,
            "enrich": runner.enrich_stage,
            "render": runner.render_outputs,
        }
        runner.fetch_stage = lambda cfg, **kw: FetchOutcome(
            stats=[SourceStat(source_id="fake_feed", name="Fake", ok=True, count=1)],
            window_hours=24,
        )
        runner.enrich_stage = lambda cfg, **kw: EnrichOutcome(
            pending=1, kept=1, clusters=1, news_items=[make_item(1, source_id="fake_feed")]
        )

        def boom(cfg, digest, **kw):
            raise RenderError("R2 独立测试：模拟渲染失败")

        runner.render_outputs = boom
        try:
            outcome = runner.run_all(cfg, dry_run=False, no_llm=True)
        finally:
            runner.render_outputs = original["render"]

        alerts = [
            r["payload"]["text"]["content"]
            for r in wecom.text_requests
            if "告警" in (r["payload"] or {}).get("text", {}).get("content", "")
        ]
        suite.check(
            "R2-2.1 RenderError → status=failed + 企微告警（含「渲染失败」）",
            outcome.status == "failed"
            and len(alerts) >= 1
            and "渲染失败" in alerts[0],
            f"status={outcome.status} alerts={len(alerts)} errors={outcome.errors}",
            evidence=alerts[0] if alerts else "（未收到告警）",
        )

        # 长图降级（image_error）路径
        runner.render_outputs = lambda cfg, digest, **kw: RenderOutcome(
            wecom_parts=["**R2 测试**（长图降级）"],
            email_html="<html></html>",
            image_error="R2 独立测试：模拟长图失败",
        )
        before = len(wecom.text_requests)
        try:
            outcome2 = runner.run_all(cfg, dry_run=False, no_llm=True)
        finally:
            runner.render_outputs = original["render"]
        new_texts = [
            r["payload"]["text"]["content"] for r in wecom.text_requests[before:]
        ]
        suite.check(
            "R2-2.2 长图降级（image_error）→ 期望有告警（信息项）",
            len(new_texts) >= 1,
            f"status={outcome2.status} errors={outcome2.errors} 新告警={len(new_texts)}",
            evidence=str(new_texts) if new_texts else "（未收到告警）",
        )

        # 存档异常（真实路径）：data/digests 被文件占用
        ws2 = workspace("m4r2-storage")
        (ws2 / "data").mkdir(parents=True, exist_ok=True)
        (ws2 / "data" / "digests").write_text("occupied", encoding="utf-8")
        with FakeRSS(
            [
                {
                    "title": f"R2 存档异常新闻 {i}",
                    "link": f"https://example.com/r2storage/{i}",
                    "description": "AI",
                    "pubDate": rfc822(30 + i),
                }
                for i in range(1, 5)
            ]
        ) as rss:
            cfg2 = make_m4_config(
                ws2 / "config.yaml",
                llm_base_url="http://127.0.0.1:1/v1",
                wecom_webhook=wecom.url,
                wecom_enabled=True,
                data_dir=ws2 / "data",
                log_dir=ws2 / "logs",
                sources=[
                    {
                        "id": "fake",
                        "name": "Fake",
                        "type": "rss",
                        "url": rss.url,
                        "enabled": True,
                        "weight": 1.0,
                    }
                ],
            )
            raw2 = yaml.safe_load(cfg2.read_text(encoding="utf-8"))
            # 关键：抓取走直连，仅告警走 HTTPS_PROXY（避免把 RSS 请求导向企微 MITM 代理）
            raw2["proxy"] = {"enabled": False, "url": ""}
            cfg2.write_text(
                yaml.safe_dump(raw2, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            before = len(wecom.text_requests)
            result = run_cli(
                ["run", "--config", str(cfg2), "--no-llm"],
                timeout=600,
                env_extra={
                    "SSL_CERT_FILE": str(CA_PEM),
                    "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}",
                },
            )
            new_alerts = [
                r["payload"]["text"]["content"] for r in wecom.text_requests[before:]
            ]
        suite.check(
            "R2-2.3 存档异常 → 编排层兜底（输出运行摘要、status=failed、无未捕获崩溃）",
            result.rc == 1
            and "状态: failed" in result.stdout
            and "⑤ 推送" in result.stdout
            # logger.exception 会把 traceback 记入日志，属预期（进程未崩溃）
            and "Traceback (most recent call last)" in result.combined,
            f"rc={result.rc}（traceback 仅出现在 logger.exception 日志中）",
            evidence=result.text,
        )
        suite.check(
            "R2-2.4 存档异常 → 企微收到「生成异常」告警",
            any("异常" in text for text in new_alerts),
            f"新告警={len(new_alerts)}",
            evidence=str(new_alerts),
        )
        run_status = db_one(
            ws2 / "data" / "aihot.db",
            "SELECT status FROM runs WHERE stage_stats LIKE '%\"stage\": \"run\"%' "
            "ORDER BY rowid DESC LIMIT 1",
        )
        suite.check(
            "R2-2.5 存档异常 → runs 记录为 failed（不再误记 ok）",
            run_status == "failed",
            f"run_status={run_status}",
        )
        runner.fetch_stage = original["fetch"]
        runner.enrich_stage = original["enrich"]


# --------------------------------------------------------------------------- F3 P2 修复


def suite_p2_fixes(suite: Suite) -> None:
    # ---- R2-3.1/3.2 bat ASCII + 英文告警 ----
    bat = (Path("run_daily.bat")).read_bytes()
    suite.check(
        "R2-3.1 run_daily.bat 全 ASCII 且 CRLF",
        all(byte < 128 for byte in bat)
        and bat.count(b"\r\n") > 0
        and bat.count(b"\n") == bat.count(b"\r\n"),
        f"bytes={len(bat)} crlf={bat.count(bytes([13, 10]))}",
    )
    with FakeWeComProbe("ok", proxy=True) as wecom:
        ws = workspace("m4r2-bat-preflight")
        raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
        raw["app"]["data_dir"] = str(ws / "data-nope")
        raw["app"]["log_dir"] = str(ws / "logs")
        raw["llm"]["base_url"] = "http://127.0.0.1:1/v1"
        raw["llm"]["api_key"] = "sk-m4r2-fake-000000"
        raw["llm"].pop("api_key_env", None)
        raw["proxy"] = {"enabled": False, "url": ""}
        raw["push"]["wecom"] = {"enabled": True, "webhook": wecom.url}
        raw["push"]["email"]["enabled"] = False
        raw["sources"] = [
            {
                "id": "fake_feed",
                "name": "Fake",
                "type": "rss",
                "url": "http://127.0.0.1:1/feed",
                "enabled": True,
                "weight": 1.0,
            }
        ]
        from m4_support import run_bat, stage_bat

        root = stage_bat("r2-preflight-fail", raw)
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        result = run_bat(root, [], timeout=300, env_extra=env)
        write_raw(
            "R2-3-bat-preflight-fail.txt",
            result["log"] + "\n--- stdout ---\n" + result["stdout"],
        )
        texts = wecom.text_requests
        body = str(texts[0]["payload"]["text"]["content"]).split("\n", 1)[-1] if texts else ""
        suite.check(
            "R2-3.2 预检失败 rc=2 且告警文案为可读英文（无 ? 乱码）",
            result["rc"] == 2
            and bool(texts)
            and body.isascii()
            and "preflight" in body.lower()
            and "?" not in body,
            f"rc={result['rc']}；body={body[:110]!r}",
            evidence=body,
        )

    # ---- R2-3.3 arxiv 探测补参数（假服务确定性验证） ----
    from aihot.checks import probe_sources
    from aihot.config import load_config

    with FakeHTTP() as fake:
        fake.add(
            "/api/query",
            200,
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>',
            "application/atom+xml",
        )
        ws = workspace("m4r2-arxiv-probe")
        cfg_path = make_m4_config(
            ws / "config.yaml",
            llm_base_url="http://127.0.0.1:1/v1",
            wecom_enabled=False,
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                {
                    "id": "arxiv",
                    "name": "arXiv",
                    "type": "arxiv",
                    "url": fake.url("/api/query"),
                    "enabled": True,
                    "weight": 0.7,
                }
            ],
        )
        cfg = load_config(cfg_path)
        results = probe_sources(cfg)
        request = fake.requests[0]
        suite.check(
            "R2-3.3 arxiv 探测自动补 search_query/max_results 且判定 OK",
            results
            and results[0].ok
            and request["query"].get("search_query") == "cat:cs.AI"
            and request["query"].get("max_results") == "1",
            f"results={[(r.name, r.ok, r.detail) for r in results]} query={request['query']}",
        )

    # 真实 arxiv 探测（可能受 429 限流；仅记录）
    ws = workspace("m4r2-arxiv-live")
    cfg_path = make_m4_config(
        ws / "config.yaml",
        llm_base_url="http://127.0.0.1:1/v1",
        wecom_enabled=False,
        data_dir=ws / "data",
        log_dir=ws / "logs",
        sources=[
            {
                "id": "arxiv",
                "name": "arXiv",
                "type": "arxiv",
                "url": "http://export.arxiv.org/api/query",
                "enabled": True,
                "weight": 0.7,
            }
        ],
    )
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    raw["llm"]["api_key"] = ""
    raw["proxy"] = {"enabled": True, "url": "http://127.0.0.1:7897"}
    cfg_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    live = run_cli(["check", "--net", "--config", str(cfg_path)], timeout=300)
    write_raw("R2-3-arxiv-live-probe.txt", live.text)
    arxiv_line = next((line for line in live.stdout.splitlines() if "源 arxiv" in line), "")
    suite.note(f"真实 arxiv 探测：rc={live.rc} {arxiv_line.strip()!r}（如遇 429 属远端限流）")
    suite.check(
        "R2-3.4 真实 arxiv 探测不再因缺参数误报（成功或受限流，不出现 400）",
        "HTTP 400" not in arxiv_line,
        f"探测行={arxiv_line.strip()!r}",
    )

    # ---- R2-3.5 dry-run 无新条目回落数据库 ----
    ws = workspace("m4r2-dryrun-fallback")
    db_path = ws / "data" / "aihot.db"
    seed_enriched_items(
        db_path,
        [
            {
                "source_id": "qbitai",
                "title": f"R2 dry-run 回落条目 {i}",
                "url": f"https://example.com/r2dry/{i}",
                "cn_title": f"R2 dry-run 回落条目 {i}",
                "cn_summary": f"摘要 {i}",
                "category": "模型发布",
                "importance": 90 - i,
                "cluster_id": i,
            }
            for i in range(1, 8)
        ],
    )
    with FakeRSS(
        [
            {
                "title": "R2 假源新条目",
                "link": "https://example.com/r2feed/1",
                "description": "AI",
                "pubDate": rfc822(10),
            }
        ]
    ) as rss, FakeLLMProbe() as llm:
        cfg_path = write_config(
            ws / "config.yaml",
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                {
                    "id": "fake_feed",
                    "name": "Fake",
                    "type": "rss",
                    "url": rss.url,
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
            llm_base_url=llm.base_url,
            top_n=5,
        )
        result = run_cli(
            ["run", "--dry-run", "--top", "3", "--config", str(cfg_path)], timeout=600
        )
        write_raw("R2-3-dryrun-fallback.txt", result.text)
        suite.check(
            "R2-3.5 run --dry-run 无新条目 → 回落到库内已加工条目（精选 3 条）",
            result.rc == 0 and "精选 3 条" in result.stdout,
            [line for line in result.stdout.splitlines() if "③ 日报" in line] or "无日报行",
        )
        md_files = sorted((ws / "data" / "digests").glob("*.md"))
        md_text = md_files[-1].read_text(encoding="utf-8") if md_files else ""
        suite.check(
            "R2-3.6 回落日报内容来自数据库（md 存档含种子标题）",
            "R2 dry-run 回落条目" in md_text,
            f"files={[p.name for p in md_files]}",
            evidence=md_text,
        )

    # ---- R2-3.7 企微分片触顶可见提示 ----
    from datetime import date as _date

    from aihot.models import NewsItem
    from aihot.presentation.render_text import render_wecom_parts

    huge = []
    for index in range(1, 13):
        item = NewsItem(
            source_id="qbitai",
            title=f"R2 超长条目 {index}",
            url=f"https://example.com/r2huge/{index}",
            published_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            raw_summary="",
            lang="zh",
            extra={},
            cluster_id=index,
            category="模型发布",
            importance=90,
            cn_title=f"R2 超长条目 {index}：" + "很长的标题" * 40,
            cn_summary="长摘要" * 1200,
        )
        huge.append(item)
    from aihot.models import Digest as _Digest

    digest = _Digest(
        digest_date=_date(2026, 9, 25),
        summary="R2 超长综述" * 200,
        top_items=huge,
        briefs=[],
        stats={"scanned": 12, "selected": 12},
    )
    parts = render_wecom_parts(digest)
    sizes = [len(part.encode("utf-8")) for part in parts]
    suite.check(
        "R2-3.7 分片触顶时末片含可见省略提示",
        len(parts) == 10 and "（内容过长，后续条目已省略）" in parts[-1],
        f"parts={len(parts)} 末片提示={'（内容过长，后续条目已省略）' in parts[-1]}",
        evidence=parts[-1][-300:],
    )
    suite.check(
        "R2-3.8 加提示后每片仍 ≤4096 字节",
        all(size <= 4096 for size in sizes),
        f"max={max(sizes)} sizes={sizes}",
    )

    # ---- R2-3.9 link_selector 零匹配抛错 ----
    from aihot.sources.base import SourceError
    from aihot.sources.html import HtmlAdapter

    with FakeHTTP() as fake2:
        fake2.add("/news", 200, "<html><body><ul><li><h3>标题</h3></li></ul></body></html>")
        from aihot.config import SourceConfig
        from aihot.net import HttpClient

        source = SourceConfig(
            id="html_probe",
            name="html probe",
            type="html",
            url=fake2.url("/news"),
            enabled=True,
            weight=1.0,
            extra={"item_selector": "li", "link_selector": "a.no-such"},
        )
        adapter = HtmlAdapter(source, HttpClient(retries=0, backoff=0.0))
        try:
            old = __import__("datetime").datetime(2020, 1, 1, tzinfo=__import__("datetime").UTC)
            adapter.fetch(old)
            ok, detail = False, "未抛 SourceError（仍静默返回 0 条）"
        except SourceError as exc:
            ok, detail = True, f"SourceError: {exc}"
        suite.check("R2-3.9 link_selector 零匹配 → 抛 SourceError", ok, detail)


def main() -> int:
    suite = Suite("m4r2_fixes")
    suite_wecom_key(suite)
    suite_failure_alerts(suite)
    suite_p2_fixes(suite)
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
