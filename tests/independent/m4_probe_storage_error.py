"""M4 调研：存档写入异常（data/digests 位置被文件占用）时 run 的行为。

目的：验证"意外渲染/存档异常 → 是否有告警 + 是否有 traceback + runs 记录状态"。
只写临时目录，不碰真实 data/。
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
    rfc822,
    run_cli,
    workspace,
)
from m4_support import FakeLLMProbe, Suite, make_m4_config, write_raw  # noqa: E402


def main() -> int:
    suite = Suite("m4_probe_storage_error")
    ws = workspace("m4-storage-error")
    (ws / "data").mkdir(parents=True, exist_ok=True)
    (ws / "data" / "digests").write_text("占位文件：模拟 digests 目录被文件占用", encoding="utf-8")
    entries = [
        {
            "title": f"存档异常演练 AI 新闻 {index}",
            "link": f"https://example.com/storage/{index}",
            "description": "AI",
            "pubDate": rfc822(30 + index),
        }
        for index in range(1, 5)
    ]
    with FakeRSS(entries) as rss, FakeLLMProbe() as llm, FakeWeCom("ok", proxy=True) as wecom:
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
                    "id": "good",
                    "name": "好源",
                    "type": "rss",
                    "url": rss.url,
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
        )
        result = run_cli(["run", "--config", str(cfg)], env_extra=env, timeout=600)
        write_raw("29-storage-error.txt", result.text)
        # 修复后：编排层兜底（logger.exception 会把 traceback 记入日志，属预期），
        # 进程不崩溃：仍输出完整运行摘要且 status=failed。
        suite.check(
            "存档目标被文件占用：编排层兜底完成（无未捕获崩溃）",
            result.rc == 1
            and "状态: failed" in result.stdout
            and "⑤ 推送" in result.stdout,
            f"rc={result.rc}（traceback 仅在 logger.exception 日志中）",
            evidence=result.text,
        )
        suite.check(
            "存档异常时有企微告警",
            len(wecom.text_requests) >= 1,
            f"text_requests={len(wecom.text_requests)}",
        )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
