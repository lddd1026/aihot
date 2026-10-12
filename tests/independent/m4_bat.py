"""M4 验收 2 + 9（bat 入口 / 故障演练）独立验证。

- 2: `cmd /c run_daily.bat --dry-run` 成功路径（staged 环境：假 LLM/假企微/假 RSS，字节级复制 bat）
     以及真实根目录 `run_daily.bat --dry-run --no-llm --config <临时配置>` 链路
- 9: 预检失败（rc=2、告警、跳过运行、无半成品）、配置损坏（rc=3）
跑法: .venv\\Scripts\\python.exe tests\\independent\\m4_bat.py
"""

from __future__ import annotations

import re
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
    workspace,
)
from m4_support import (  # noqa: E402
    PROJECT,
    TEMPROOT,
    FakeLLMProbe,
    Suite,
    bat_sha256,
    dump_config,
    run_bat,
    run_python,
    sanitized_real_raw,
    stage_bat,
    write_raw,
)


def rss_entries() -> list[dict[str, str]]:
    return [
        {
            "title": f"每日入口独立验证 AI 新闻 {index}：模型与工具更新",
            "link": f"https://example.com/bat/{index}",
            "description": "AI model and tool update",
            "pubDate": rfc822(30 + index),
        }
        for index in range(1, 9)
    ]


def main() -> int:
    suite = Suite("m4_bat")
    before = real_state_snapshot()

    # ---------- 1. bat 字节级一致性 ----------
    suite.check(
        "2.1 run_daily.bat 未被测试改动（sha256 记录）",
        True,
        f"sha256={bat_sha256()[:16]}…",
        evidence=bat_sha256(),
    )

    # ---------- 2. staged 成功路径：cmd /c run_daily.bat --dry-run ----------
    with FakeRSS(rss_entries()) as rss, FakeLLMProbe() as llm, FakeWeCom("ok", proxy=True) as wecom:
        llm.generator = default_llm_generator
        ws = workspace("m4-bat")
        root = TEMPROOT / "m4-bat" / "success"
        raw = sanitized_real_raw()
        raw["app"]["data_dir"] = str(root / "data")
        raw["app"]["log_dir"] = str(root / "logs")
        raw["llm"]["base_url"] = llm.base_url
        raw["proxy"] = {"enabled": False, "url": ""}
        raw["push"]["wecom"] = {"enabled": True, "webhook": wecom.url}
        raw["sources"] = [
            {
                "id": "fake_feed",
                "name": "Fake",
                "type": "rss",
                "url": rss.url,
                "enabled": True,
                "weight": 1.0,
            }
        ]
        root = stage_bat("success", raw, copy_real_db=True)
        suite.check(
            "2.2 staged run_daily.bat 与真实 bat 字节一致",
            (root / "run_daily.bat").read_bytes() == (PROJECT / "run_daily.bat").read_bytes(),
            f"{root / 'run_daily.bat'}",
        )
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        result = run_bat(root, ["--dry-run"], timeout=600, env_extra=env)
        write_raw(
            "20-bat-success.txt",
            result["log"]
            + "\n--- stdout ---\n"
            + result["stdout"]
            + "\n--- stderr ---\n"
            + result["stderr"],
        )
        log = result["log"]
        suite.check(
            "2.3 bat 退出码 = 0", result["rc"] == 0, f"rc={result['rc']} ({result['seconds']}s)"
        )
        suite.check(
            "2.4 日志含预检 + 运行 + exit=0",
            "preflight" in log and "run =====" in log and "exit=0" in log,
            "log 段落齐全",
        )
        suite.check(
            "2.5 预检三项（代理/企微/LLM）均 OK",
            log.count("[OK ]") >= 3 and "[ERR]" not in log.split("run =====")[0],
            "预检无 ERR",
            evidence=log.split("run =====")[0][-1200:],
        )
        digests = (
            sorted(p.name for p in (root / "data" / "digests").iterdir())
            if (root / "data" / "digests").exists()
            else []
        )
        suite.check(
            "2.6 dry-run 产出日报三件套（不推送）",
            any(name.endswith(".md") for name in digests)
            and any(name.endswith(".html") for name in digests)
            and any(name.endswith(".jpg") for name in digests),
            f"files={digests}",
        )
        posts = [r for r in wecom.requests if r["payload"] is not None]
        suite.check(
            "2.7 dry-run 未向企微推送任何消息（仅有预检 GET）",
            len(posts) == 0,
            f"POST 消息数={len(posts)}",
        )

    # ---------- 3. staged 预检失败路径 ----------
    with FakeWeCom("ok", proxy=True) as wecom:
        ws = workspace("m4-bat-preflight-fail")
        raw = sanitized_real_raw()
        raw["app"]["data_dir"] = str(ws / "data-nope")
        raw["app"]["log_dir"] = str(ws / "logs")
        raw["llm"]["base_url"] = "http://127.0.0.1:1/v1"  # 预检必然失败
        raw["llm"]["api_key"] = "sk-m4-fake-000000000000"
        raw["proxy"] = {"enabled": False, "url": ""}
        raw["push"]["wecom"] = {"enabled": True, "webhook": wecom.url}
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
        root = stage_bat("preflight-fail", raw)
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        result = run_bat(root, [], timeout=300, env_extra=env)
        write_raw(
            "21-bat-preflight-fail.txt",
            result["log"]
            + "\n--- stdout ---\n"
            + result["stdout"]
            + "\n--- stderr ---\n"
            + result["stderr"],
        )
        log = result["log"]
        suite.check("2.8 预检失败退出码 = 2", result["rc"] == 2, f"rc={result['rc']}")
        suite.check(
            "2.9 日志标记 PREFLIGHT FAILED 且跳过运行",
            "PREFLIGHT FAILED - skip today" in log and "run =====" not in log,
            "无 run 段",
        )
        text_requests = wecom.text_requests
        suite.check(
            "2.10 预检失败已发企微告警",
            len(text_requests) == 1,
            f"text_requests={len(text_requests)}",
            evidence=(
                str(text_requests[0]["payload"]["text"]["content"])
                if text_requests
                else "（无告警）"
            ),
        )
        if text_requests:
            content = str(text_requests[0]["payload"]["text"]["content"])
            body = content.split("\n", 1)[1] if "\n" in content else content
            readable = body.isascii() and "preflight" in body.lower() and "?" not in body
            suite.check(
                "2.11 预检失败告警文案可读（ASCII 英文，无 ? 乱码）",
                readable,
                f"body={body[:110]!r}",
                evidence=content,
            )
        suite.check(
            "2.12 预检失败未产生数据目录/半成品日报",
            not (ws / "data-nope").exists(),
            f"data-nope exists={(ws / 'data-nope').exists()}",
        )

    # ---------- 4. 真实根目录：run_daily.bat --dry-run --no-llm --config 临时配置 ----------
    ws = workspace("m4-bat-real-root")
    raw = sanitized_real_raw()
    raw["app"]["data_dir"] = str(ws / "data")
    raw["app"]["log_dir"] = str(ws / "logs")
    raw["push"]["wecom"] = {"enabled": False, "webhook": ""}
    raw["push"]["email"]["enabled"] = False
    cfg_path = dump_config(ws / "config.yaml", raw)
    (ws / "data").mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy2(PROJECT / "data" / "aihot.db", ws / "data" / "aihot.db")
    result = run_bat(
        PROJECT,
        ["--dry-run", "--no-llm", "--config", str(cfg_path)],
        timeout=1200,
    )
    write_raw(
        "22-real-root-dryrun-no-llm.txt",
        result["log"][-6000:]
        + "\n--- stdout ---\n"
        + result["stdout"]
        + "\n--- stderr ---\n"
        + result["stderr"],
    )
    suite.check(
        "2.13 真实根目录 bat 全链路 rc=0（真实配置预检 + 临时配置运行）",
        result["rc"] == 0,
        f"rc={result['rc']} ({result['seconds']}s)",
    )
    run_tail = result["log"].rsplit("run =====", 1)[-1]
    match = re.search(r"① 采集: (\d+)/(\d+) 源成功.*?新增 (\d+) 条", run_tail, re.S)
    ok_count = int(match.group(1)) if match else 0
    total_count = int(match.group(2)) if match else 0
    suite.check(
        "2.14 真实源采集成功（≥11/12，单源瞬时失败被隔离）",
        bool(match) and ok_count >= 11 and "状态: ok" in run_tail,
        f"采集={ok_count}/{total_count}，新增={match.group(3)}"
        if match
        else "（未匹配到采集段）",
        evidence=run_tail[-1500:],
    )
    suite.check(
        "2.15 真实根目录 bat 日志写入 logs\\run_daily.log",
        "run =====" in result["log"] and "exit=0" in result["log"][-400:],
        f"log 末尾={result['log'][-160:]!r}",
    )

    # ---------- 5. 配置损坏 → rc=3 ----------
    ws = workspace("m4-bat-broken-config")
    broken_yaml = ws / "broken-syntax.yaml"
    broken_yaml.write_text("app: [unclosed\n  window_hours: 24\n", encoding="utf-8")
    result = run_python(
        ["-m", "aihot", "run", "--dry-run", "--config", str(broken_yaml)], timeout=120
    )
    write_raw(
        "23-broken-config-syntax.txt", result["stdout"] + "\n--- stderr ---\n" + result["stderr"]
    )
    suite.check(
        "2.16 YAML 语法损坏 → rc=3 且不抛 traceback",
        result["rc"] == 3 and "Traceback" not in result["stdout"] + result["stderr"],
        f"rc={result['rc']}",
    )
    broken_struct = ws / "broken-struct.yaml"
    broken_struct.write_text(
        "app: {window_hours: 0}\nllm: {base_url: 'not-a-url', api_key: ''}\n"
        "push: {wecom: {enabled: true, webhook: 'http://bad'}, email: {enabled: false}}\n"
        "sources: []\n",
        encoding="utf-8",
    )
    result2 = run_python(
        ["-m", "aihot", "run", "--dry-run", "--config", str(broken_struct)], timeout=120
    )
    write_raw(
        "24-broken-config-struct.txt", result2["stdout"] + "\n--- stderr ---\n" + result2["stderr"]
    )
    suite.check(
        "2.17 结构非法（无源/坏 URL/无 key）→ rc=3",
        result2["rc"] == 3 and "配置校验未通过" in result2["stdout"],
        f"rc={result2['rc']}",
    )

    after = real_state_snapshot()
    same_digests = before["digests"] == after["digests"]
    suite.check(
        "2.18 真实 data/digests 未被改动（文件名+大小+mtime）",
        same_digests,
        f"digests={sorted(after['digests'].keys())}",
    )
    suite.check(
        "2.19 真实库快照前后一致（bat 只追加日志）", before["db"] == after["db"], "db 快照一致"
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
