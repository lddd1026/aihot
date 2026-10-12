"""M4 验收 3 独立验证：`check --quick` 快速预检（代理/企微/LLM）与退出码。

企微类用例走 M3 已验证的「HTTPS CONNECT 假代理 + 自签 CA」方式（webhook 必须为官方域名前缀）。
跑法: .venv\\Scripts\\python.exe tests\\independent\\m4_checks.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 必须先 import m4_support（它设置 AIHOT_M3_EVIDENCE=m4），再 import m3_support
from m3_support import CA_PEM, FakeWeCom, run_cli, workspace  # noqa: E402
from m4_support import FakeLLMProbe, FakeWeComProbe, Suite, make_m4_config, write_raw  # noqa: E402

DUMMY_SOURCE: list[dict[str, object]] = [
    {
        "id": "fake_rss",
        "name": "假源",
        "type": "rss",
        "url": "http://127.0.0.1:1/feed",
        "enabled": True,
        "weight": 1.0,
    }
]
DEAD_SOURCE: list[dict[str, object]] = [
    {
        "id": "dead_source",
        "name": "死源",
        "type": "rss",
        "url": "http://127.0.0.1:1/feed",
        "enabled": True,
        "weight": 1.0,
    }
]
BOGUS_HOOK = (
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=00000000-0000-0000-0000-000000000000"
)


def run_check(
    name: str,
    *,
    llm_base_url: str,
    wecom_webhook: str = "",
    wecom_enabled: bool = False,
    sources: list[dict[str, object]] | None = None,
    args: list[str] | None = None,
    proxy: str | None = None,
    env_extra: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> dict[str, object]:
    ws = workspace(f"m4-checks-{name}")
    cfg_path = make_m4_config(
        ws / "config.yaml",
        llm_base_url=llm_base_url,
        wecom_webhook=wecom_webhook,
        wecom_enabled=wecom_enabled,
        data_dir=ws / "data",
        log_dir=ws / "logs",
        sources=sources if sources is not None else DUMMY_SOURCE,  # type: ignore[arg-type]
    )
    if proxy:
        import yaml

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        raw["proxy"] = {"enabled": True, "url": proxy}
        cfg_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    result = run_cli(
        [*(args or ["check", "--quick"]), "--config", str(cfg_path)],
        timeout=timeout,
        env_extra=env_extra,
    )
    return {"rc": result.rc, "out": result.combined, "text": result.text, "config": str(cfg_path)}


def mask(value: str) -> str:
    return f"<len={len(value)} sha1={hashlib.sha1(value.encode()).hexdigest()[:8]}>"


def main() -> int:
    suite = Suite("m4_checks")

    # ---------- 1. 全部健康 → rc=0（企微走假代理 MITM） ----------
    with FakeLLMProbe() as llm, FakeWeCom("ok", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        case = run_check(
            "all-ok",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            env_extra=env,
        )
        suite.check(
            "3.1 代理/企微/LLM 全健康 → rc=0",
            case["rc"] == 0 and "[OK ] LLM API" in case["out"] and "[OK ] 企业微信" in case["out"],
            f"rc={case['rc']}",
        )
        suite.check(
            "3.2 配置校验通过且预检只含 3 项关键依赖",
            "配置结构校验通过" in case["out"] and "快速预检（关键依赖）" in case["out"],
            "输出含校验通过与快速预检段",
        )

    # ---------- 2. 源不可达不影响 --quick ----------
    with FakeLLMProbe() as llm, FakeWeCom("ok", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        case = run_check(
            "skip-sources",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            sources=DEAD_SOURCE,
            env_extra=env,
        )
        quick_section = case["out"].split("快速预检")[-1]
        suite.check(
            "3.3 --quick 不探测信息源（死源仍 rc=0）",
            case["rc"] == 0 and "dead_source" not in quick_section,
            f"rc={case['rc']}",
        )
        suite.check(
            "3.3b check --net 会探测源（死源 → rc=1）",
            run_check(
                "net-probes",
                llm_base_url=llm.base_url,
                wecom_webhook=wecom.url,
                wecom_enabled=True,
                sources=DEAD_SOURCE,
                args=["check", "--net"],
                env_extra=env,
            )["rc"]
            == 1,
            "对比用例",
        )

    # ---------- 3. LLM 失败 ----------
    with FakeLLMProbe() as llm:
        llm.mode = "http401"
        case = run_check("llm-401", llm_base_url=llm.base_url)
        suite.check(
            "3.4 LLM 401 鉴权失败 → rc=1 且输出指明 LLM",
            case["rc"] == 1 and "[ERR] LLM API" in case["out"],
            f"rc={case['rc']}",
            evidence=case["text"] if case["rc"] != 1 else None,
        )
    with FakeLLMProbe() as llm:
        llm.mode = "http502"
        case = run_check("llm-502", llm_base_url=llm.base_url)
        suite.check(
            "3.4b LLM 502 → rc=1",
            case["rc"] == 1 and "[ERR] LLM API" in case["out"],
            f"rc={case['rc']}",
        )
    case = run_check("llm-refused", llm_base_url="http://127.0.0.1:1/v1")
    suite.check(
        "3.4c LLM 端口不可连 → rc=1",
        case["rc"] == 1 and "[ERR] LLM API" in case["out"],
        f"rc={case['rc']}",
    )

    # ---------- 4. 企微失败 ----------
    with FakeLLMProbe() as llm, FakeWeComProbe("http502", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        case = run_check(
            "wecom-502",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            env_extra=env,
        )
        suite.check(
            "3.5 企微 HTTP 502 → rc=1",
            case["rc"] == 1 and "[ERR] 企业微信" in case["out"],
            f"rc={case['rc']}",
        )
    with FakeLLMProbe() as llm:
        case = run_check(
            "wecom-proxy-dead",
            llm_base_url=llm.base_url,
            wecom_webhook=BOGUS_HOOK,
            wecom_enabled=True,
            proxy="http://127.0.0.1:1",
        )
        suite.check(
            "3.5b 企微请求失败（代理不可用）→ rc=1",
            case["rc"] == 1 and "[ERR] 企业微信" in case["out"],
            f"rc={case['rc']}",
        )

    # ---------- 5. 代理不可用 ----------
    with FakeLLMProbe() as llm:
        case = run_check("proxy-dead", llm_base_url=llm.base_url, proxy="http://127.0.0.1:1")
        suite.check(
            "3.6 代理端口不可连 → rc=1 且输出指明代理",
            case["rc"] == 1 and "[ERR] 代理" in case["out"],
            f"rc={case['rc']}",
        )

    # ---------- 6. 企微 key 失效但域名可达 ----------
    with FakeLLMProbe() as llm, FakeWeComProbe("errcode", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        case = run_check(
            "wecom-bad-key-fake",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            env_extra=env,
        )
        suite.check(
            "3.7 企微 errcode=93000（假服务哨兵）→ 期望 rc=1",
            case["rc"] == 1,
            f"rc={case['rc']}（HTTP 200 但 errcode=93000；当前实现只看状态码）",
            evidence=case["text"],
        )

    # 真实 qyapi：官方域名 + 假 key（GET 不产生消息，走真实代理）
    with FakeLLMProbe() as llm:
        case = run_check(
            "wecom-real-bogus-key",
            llm_base_url=llm.base_url,
            wecom_webhook=BOGUS_HOOK,
            wecom_enabled=True,
            proxy="http://127.0.0.1:7897",
            timeout=180.0,
        )
    suite.check(
        "3.8 企微 webhook key 无效（真实 qyapi）→ 期望 rc=1",
        case["rc"] == 1,
        f"rc={case['rc']}；webhook={mask(BOGUS_HOOK)}（GET 不产生消息）",
        evidence=case["text"],
    )
    write_raw("16-wecom-bogus-key-cli.txt", case["text"])

    # ---------- 7. 密钥不落盘到输出 ----------
    with FakeLLMProbe() as llm, FakeWeCom("ok", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        case = run_check(
            "secret-free",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            env_extra=env,
        )
        suite.check(
            "3.9 check 输出不含密钥明文",
            "sk-" not in case["out"],
            "输出无 sk- 前缀密钥",
        )

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
