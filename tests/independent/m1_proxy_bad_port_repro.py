"""修复复验：非法 proxy.url 的处理 + 合法但不可达代理的隔离。

背景：M1 首轮发现 D1（非法端口代理使 check --net / fetch traceback）。
开发者已修复（config 校验端口 + HttpClient 构造兜底 + fetch 兜底）。
本脚本独立复验新契约：
- 非法 proxy.url  → rc=3（配置错误）、无 traceback、错误信息可操作；
- 合法但不可达代理 → check --net rc=1 且 [ERR] 代理；fetch rc=1 且无 traceback；
- check（不带 --net）对非法 proxy.url 也不再崩溃（D5 同源）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
    TEMPROOT,
    LocalServer,
    Payload,
    Suite,
    ago,
    rss_xml,
    run_cli,
    source,
    write_config,
)

INVALID_VARIANTS = {
    "bad-port": "http://127.0.0.1:abc",
    "bad-ipv6": "http://[::1",
    "no-host": "http://:7890",
    "port-out-of-range": "http://127.0.0.1:99999",
    "socks5-bad-port": "socks5://127.0.0.1:abc",
    "bad-scheme": "ftp://127.0.0.1:7890",
}
VALID_UNREACHABLE = "http://127.0.0.1:9"


def main() -> int:
    suite = Suite("proxy_bad_port")
    server = LocalServer()
    server.routes["/good"] = lambda q: Payload(
        200,
        rss_xml([{"title": "ok", "link": "https://ok.example/1", "date": ago(hours=1)}]),
    )
    server.routes["/llm/models"] = lambda q: Payload(401, b'{"error":"no key"}')
    try:
        TEMPROOT.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="aihot-m1-proxy-", dir=TEMPROOT))
        base = write_config(
            workspace,
            [source("good", server.url("/good"))],
            llm_base_url=server.url("/llm"),
        )
        base_raw = yaml.safe_load(base.read_text(encoding="utf-8"))

        matrix: list[dict[str, object]] = []
        for name, proxy_url in INVALID_VARIANTS.items():
            cfg = workspace / f"config-{name}.yaml"
            raw = dict(base_raw)
            raw["proxy"] = {"enabled": True, "url": proxy_url}
            cfg.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            plain = run_cli(["check", "--config", str(cfg)], timeout=60)
            net = run_cli(["check", "--net", "--config", str(cfg)], timeout=120)
            fetch = run_cli(["fetch", "--config", str(cfg), "--dry-run"], timeout=120)
            row = {
                "proxy_url": proxy_url,
                "check_rc": plain.rc,
                "check_net_rc": net.rc,
                "fetch_rc": fetch.rc,
                "any_traceback": any(
                    "Traceback" in text for text in (plain.stdout + plain.stderr,
                                                     net.stdout + net.stderr,
                                                     fetch.stdout + fetch.stderr)
                ),
                "mentions_proxy_url": "proxy.url" in (plain.stdout + net.stdout + fetch.stdout),
            }
            matrix.append(row)
            suite.evidence(f"{name}-check.txt", plain.text)
            suite.evidence(f"{name}-check-net.txt", net.text)
            suite.evidence(f"{name}-fetch.txt", fetch.text)
        suite.evidence_json("proxy-invalid-matrix.json", matrix)

        bad_rows = [row for row in matrix if row["check_rc"] != 3 or row["fetch_rc"] != 3]
        tb_rows = [row["proxy_url"] for row in matrix if row["any_traceback"]]
        suite.check(
            "复验-非法proxy.url干净报配置错误(rc=3)",
            not bad_rows and not tb_rows,
            f"{len(matrix)} 个非法变体：rc 全为 3={not bad_rows}，无 traceback={not tb_rows}"
            + (f"；异常行={bad_rows}" if bad_rows else ""),
            evidence="见各变体 *.txt",
        )
        suite.check(
            "复验-错误信息可操作(含 proxy.url 与示例)",
            all(row["mentions_proxy_url"] for row in matrix),
            f"全部变体提示含 'proxy.url'="
            f"{all(row['mentions_proxy_url'] for row in matrix)}",
        )

        # 合法但不可达的代理：应走探测/隔离路径，不崩溃
        unreachable_cfg = workspace / "config-unreachable.yaml"
        raw = dict(base_raw)
        raw["proxy"] = {"enabled": True, "url": VALID_UNREACHABLE}
        unreachable_cfg.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        net = run_cli(["check", "--net", "--config", str(unreachable_cfg)], timeout=120)
        fetch = run_cli(["fetch", "--config", str(unreachable_cfg), "--dry-run"], timeout=120)
        suite.check(
            "复验-合法但不可达代理仍走[ERR]代理",
            net.rc == 1
            and "[ERR] 代理" in net.stdout
            and "Traceback" not in (net.stdout + net.stderr)
            and fetch.rc == 1
            and "Traceback" not in (fetch.stdout + fetch.stderr),
            f"check --net rc={net.rc}（期望 1，含[ERR]代理={('[ERR] 代理' in net.stdout)}）；"
            f"fetch rc={fetch.rc}（期望 1）",
            evidence=net.text + "\n\n" + fetch.text,
        )

        # 回归 D5：非法 IPv6 代理下 check（不带 --net）摘要输出不应崩溃
        cfg_ipv6 = workspace / "config-bad-ipv6.yaml"
        raw = dict(base_raw)
        raw["proxy"] = {"enabled": True, "url": "http://[::1"}
        cfg_ipv6.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        plain = run_cli(["check", "--config", str(cfg_ipv6)], timeout=60)
        suite.check(
            "复验-非法IPv6代理 check 摘要不崩溃(D5)",
            plain.rc == 3 and "Traceback" not in (plain.stdout + plain.stderr),
            f"rc={plain.rc}（期望 3）含 traceback={'Traceback' in (plain.stdout + plain.stderr)}",
            evidence=plain.text,
        )
    finally:
        server.stop()
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
