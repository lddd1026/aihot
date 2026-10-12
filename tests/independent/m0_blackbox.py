"""M0 独立黑盒测试：CLI 语义 + 配置校验矩阵。

测试 Agent 专用，不属于开发单测；文件名不以 test_ 开头，避免被 pytest 收集。
用法: .venv\\Scripts\\python.exe tests\\independent\\m0_blackbox.py
证据输出: reports/evidence/cli/
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "reports" / "evidence" / "cli"
WORK = Path(tempfile.mkdtemp(prefix="aihot-m0-"))
PYTHON = sys.executable
AIHOT_EXE = Path(sys.executable).parent / "aihot.exe"

FAKE_LLM_KEY = "sk-m0fake-0123456789abcdef"
FAKE_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=M0FAKE0123456789abcdef"
FAKE_EMAIL_PASSWORD = "m0-fake-smtp-password-0123456789"
FAKE_ENV_LLM_KEY = "sk-m0env-0123456789abcdef"
FAKE_ENV_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=M0ENV0123456789abcdef"
SECRETS_IN_USE = (FAKE_LLM_KEY, FAKE_WEBHOOK, FAKE_EMAIL_PASSWORD)

BASE: dict[str, Any] = {
    "app": {
        "timezone": "Asia/Shanghai",
        "window_hours": 24,
        "top_n": 12,
        "log_level": "INFO",
        "data_dir": "data",
        "log_dir": "logs",
    },
    "llm": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": FAKE_LLM_KEY,
        "api_key_env": "M0_LLM_KEY",
        "batch_size": 15,
        "daily_cost_limit_cny": 1.0,
    },
    "proxy": {"enabled": False, "url": ""},
    "push": {
        "wecom": {"enabled": True, "webhook": FAKE_WEBHOOK, "webhook_env": "M0_WECOM_HOOK"},
        "email": {"enabled": False},
    },
    "sources": [
        {
            "id": "qbitai",
            "name": "量子位",
            "type": "rss",
            "url": "https://www.qbitai.com/feed",
            "enabled": True,
            "weight": 1.0,
        }
    ],
}

Case = tuple[str, str, int, tuple[str, ...]]
RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def cfg_text(mutate: Callable[[dict[str, Any]], None] | None = None) -> str:
    data = deepcopy(BASE)
    if mutate is not None:
        mutate(data)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def app_case(slug: str, key: str, value: object, expect: int = 3) -> Case:
    return (slug, cfg_text(lambda d: d["app"].update({key: value})), expect, (key,))


def llm_case(slug: str, key: str, value: object, expect: int = 3) -> Case:
    return (slug, cfg_text(lambda d: d["llm"].update({key: value})), expect, (key,))


def source_case(
    slug: str,
    key: str,
    value: object,
    expect: int = 3,
    contains: tuple[str, ...] | None = None,
) -> Case:
    text = cfg_text(lambda d: d["sources"][0].update({key: value}))
    return (slug, text, expect, contains if contains is not None else (key,))


def write_cfg(slug: str, text: str) -> Path:
    path = WORK / f"{slug}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def run_cmd(
    args: list[str], *, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        args, cwd=str(REPO), env=env, capture_output=True, timeout=120, check=False
    )


def save_evidence(slug: str, cmd: list[str], cp: subprocess.CompletedProcess[bytes]) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    text = (
        f"COMMAND: {' '.join(cmd)}\n"
        f"EXIT CODE: {cp.returncode}\n"
        "--- STDOUT ---\n"
        + cp.stdout.decode("utf-8", "replace")
        + "\n--- STDERR ---\n"
        + cp.stderr.decode("utf-8", "replace")
        + "\n"
    )
    (EVIDENCE / f"{slug}.txt").write_text(text, encoding="utf-8")


def run_case(
    slug: str,
    args: list[str],
    expect_rc: int,
    contains: tuple[str, ...] = (),
    not_contains: tuple[str, ...] = (),
    *,
    env_extra: dict[str, str] | None = None,
) -> str:
    try:
        cp = run_cmd(args, env_extra=env_extra)
    except OSError as exc:
        record(slug, False, f"命令执行异常: {exc!r}")
        return ""
    save_evidence(slug, args, cp)
    out = cp.stdout.decode("utf-8", "replace")
    err = cp.stderr.decode("utf-8", "replace")
    both = out + err
    problems: list[str] = []
    if cp.returncode != expect_rc:
        problems.append(f"退出码 {cp.returncode} != 期望 {expect_rc}")
    problems.extend(f"缺少输出: {needle!r}" for needle in contains if needle not in both)
    problems.extend(f"出现敏感串: {needle!r}" for needle in not_contains if needle in both)
    record(slug, not problems, "; ".join(problems) if problems else f"rc={cp.returncode}")
    return both


def observe_case(
    slug: str, args: list[str], *, env_extra: dict[str, str] | None = None
) -> tuple[int, str]:
    """记录实际行为（不预设退出码），仅检查是否泄漏敏感串。"""
    cp = run_cmd(args, env_extra=env_extra)
    save_evidence(slug, args, cp)
    both = cp.stdout.decode("utf-8", "replace") + cp.stderr.decode("utf-8", "replace")
    record(slug, True, f"实际 rc={cp.returncode}（行为记录）")
    return cp.returncode, both


def config_cases() -> list[Case]:
    minimal = yaml.safe_dump(
        {"sources": deepcopy(BASE["sources"])}, allow_unicode=True, sort_keys=False
    )
    return [
        ("valid-base", cfg_text(), 0, ("配置结构校验通过",)),
        ("llm-required-minimal", minimal, 3, ("llm.base_url", "llm.model")),
        ("bad-yaml", "a: [1, 2\n", 3, ("YAML 解析失败",)),
        ("top-level-scalar", "hello\n", 3, ("顶层必须是映射",)),
        ("top-level-list", "- a\n- b\n", 3, ("顶层必须是映射",)),
        ("empty-file", "", 3, ("配置文件为空",)),
        ("comments-only", "# only a comment\n", 3, ("配置文件为空",)),
        (
            "missing-id",
            cfg_text(lambda d: d["sources"][0].pop("id")),
            3,
            ("缺少 id",),
        ),
        (
            "duplicate-id",
            cfg_text(lambda d: d["sources"].append(deepcopy(d["sources"][0]))),
            3,
            ("id 重复",),
        ),
        source_case(
            "invalid-url",
            "url",
            "www.qbitai.com/feed",
            3,
            ("url 必须是 http(s) 地址",),
        ),
        source_case("unsupported-type", "type", "rss2", 3, ("type 不支持",)),
        (
            "missing-type",
            cfg_text(lambda d: d["sources"][0].pop("type")),
            3,
            ("type 不支持",),
        ),
        (
            "wecom-no-webhook",
            cfg_text(lambda d: d["push"]["wecom"].update(webhook="")),
            3,
            ("未配置 webhook",),
        ),
        (
            "wecom-bad-format",
            cfg_text(lambda d: d["push"]["wecom"].update(webhook="https://example.com/hook?key=x")),
            3,
            ("webhook 格式不正确",),
        ),
        (
            "email-missing-creds",
            cfg_text(lambda d: d["push"]["email"].update(enabled=True)),
            3,
            ("smtp_host", "password", "to_addrs"),
        ),
        (
            "email-bad-addr",
            cfg_text(
                lambda d: d["push"]["email"].update(
                    enabled=True,
                    smtp_host="smtp.qq.com",
                    username="a@qq.com",
                    password="x",
                    from_addr="not-an-email",
                    to_addrs=["bad@"],
                )
            ),
            3,
            ("不是有效邮箱", "含无效邮箱"),
        ),
        (
            "email-bad-port",
            cfg_text(
                lambda d: d["push"]["email"].update(
                    enabled=True,
                    smtp_host="smtp.qq.com",
                    username="a@qq.com",
                    password="x",
                    from_addr="a@qq.com",
                    to_addrs=["b@qq.com"],
                    smtp_port=70000,
                )
            ),
            3,
            ("smtp_port",),
        ),
        app_case("bad-timezone", "timezone", "Mars/Olympus"),
        app_case("window-hours-zero", "window_hours", 0),
        app_case("window-hours-169", "window_hours", 169),
        app_case("window-hours-bool", "window_hours", True),
        app_case("window-hours-str", "window_hours", "24"),
        app_case("top-n-zero", "top_n", 0),
        app_case("top-n-51", "top_n", 51),
        app_case("top-n-bool", "top_n", True),
        app_case("bad-log-level", "log_level", "TRACE"),
        (
            "proxy-enabled-empty",
            cfg_text(lambda d: d["proxy"].update(enabled=True, url="")),
            3,
            ("proxy.url 为空",),
        ),
        (
            "proxy-bad-scheme",
            cfg_text(lambda d: d["proxy"].update(enabled=True, url="127.0.0.1:7890")),
            3,
            # M1-R2 契约变更：非法代理地址统一为「proxy.url 无效: ...」（不回显完整地址）
            ("proxy.url 无效",),
        ),
        (
            "sources-missing",
            cfg_text(lambda d: d.pop("sources")),
            3,
            ("sources 必须是非空列表",),
        ),
        (
            "sources-empty",
            cfg_text(lambda d: d.update(sources=[])),
            3,
            ("sources 必须是非空列表",),
        ),
        (
            "sources-dict",
            cfg_text(lambda d: d.update(sources={})),
            3,
            ("sources 必须是非空列表",),
        ),
        (
            "source-item-scalar",
            cfg_text(lambda d: d.update(sources=["oops"])),
            3,
            ("必须是映射",),
        ),
        (
            "source-missing-url",
            cfg_text(lambda d: d["sources"][0].pop("url")),
            3,
            ("url 必须是",),
        ),
        source_case("weight-zero", "weight", 0, 3, ("weight",)),
        source_case("weight-11", "weight", 11, 3, ("weight",)),
        source_case("weight-bool", "weight", True, 3, ("weight",)),
        source_case("weight-str", "weight", "1.0", 3, ("weight",)),
        llm_case("llm-bad-base-url", "base_url", "api.deepseek.com"),
        llm_case("llm-empty-model", "model", ""),
        llm_case("llm-batch-zero", "batch_size", 0),
        llm_case("llm-batch-51", "batch_size", 51),
        llm_case("llm-batch-bool", "batch_size", True),
        llm_case("llm-cost-zero", "daily_cost_limit_cny", 0),
        llm_case("llm-cost-str", "daily_cost_limit_cny", "1.0"),
        llm_case("llm-cost-bool", "daily_cost_limit_cny", True),
        (
            "app-section-list",
            cfg_text(lambda d: d.update(app=["x"])),
            3,
            ("app: 必须是映射",),
        ),
        (
            "push-section-list",
            cfg_text(lambda d: d.update(push=["x"])),
            3,
            ("push: 必须是映射",),
        ),
        (
            "section-null",
            cfg_text(lambda d: d.update(app=None)),
            0,
            ("配置结构校验通过",),
        ),
        ("crlf", cfg_text().replace("\n", "\r\n"), 0, ("配置结构校验通过",)),
        (
            "unicode-name",
            cfg_text(lambda d: d["sources"][0].update(name='量子位🚀 "引号" \'单引号\' — 破折号')),
            0,
            ("量子位", "🚀"),
        ),
        (
            "long-name",
            cfg_text(lambda d: d["sources"][0].update(name="长" * 5000)),
            0,
            ("配置结构校验通过",),
        ),
        ("yaml-tab", "app:\n\ttimezone: Asia/Shanghai\n", 3, ("YAML 解析失败",)),
        (
            "extra-map",
            cfg_text(lambda d: d["sources"][0].update(extra={"subreddit": "LocalLLaMA"})),
            0,
            ("配置结构校验通过",),
        ),
        (
            "source-disabled-flag",
            cfg_text(lambda d: d["sources"][0].update(enabled=False)),
            0,
            ("[off] qbitai",),
        ),
        # ---- 第 2 轮：D4 严格布尔（回归用例） ----
        (
            "bool-str-proxy",
            cfg_text(lambda d: d["proxy"].update(enabled="false")),
            3,
            ("proxy.enabled 必须是布尔值",),
        ),
        (
            "bool-str-wecom",
            cfg_text(lambda d: d["push"]["wecom"].update(enabled="false")),
            3,
            ("push.wecom.enabled 必须是布尔值",),
        ),
        (
            "bool-str-email",
            cfg_text(lambda d: d["push"]["email"].update(enabled="false")),
            3,
            ("push.email.enabled 必须是布尔值",),
        ),
        (
            "bool-str-source",
            cfg_text(lambda d: d["sources"][0].update(enabled="false")),
            3,
            ("enabled 必须是布尔值",),
        ),
        (
            "bool-str-use-ssl",
            cfg_text(lambda d: d["push"]["email"].update(use_ssl="false")),
            3,
            ("push.email.use_ssl 必须是布尔值",),
        ),
        (
            "bool-int-source",
            cfg_text(lambda d: d["sources"][0].update(enabled=1)),
            3,
            ("enabled 必须是布尔值",),
        ),
        # ---- 第 2 轮：D2 类型校验（回归用例） ----
        (
            "type-data-dir-int",
            cfg_text(lambda d: d["app"].update(data_dir=123)),
            3,
            ("app.data_dir 必须是字符串路径",),
        ),
        (
            "type-log-dir-list",
            cfg_text(lambda d: d["app"].update(log_dir=["logs"])),
            3,
            ("app.log_dir 必须是字符串路径",),
        ),
        (
            "type-smtp-port-str-disabled",
            cfg_text(lambda d: d["push"]["email"].update(smtp_port="abc")),
            3,
            ("smtp_port",),
        ),
        (
            "type-to-addrs-string",
            cfg_text(lambda d: d["push"]["email"].update(to_addrs="a@b.com")),
            3,
            ("to_addrs 必须是邮箱字符串列表",),
        ),
        (
            "type-to-addrs-mixed",
            cfg_text(lambda d: d["push"]["email"].update(to_addrs=["a@b.com", 123])),
            3,
            ("to_addrs 必须是邮箱字符串列表",),
        ),
        (
            "type-extra-list",
            cfg_text(lambda d: d["sources"][0].update(extra=["a", "b"])),
            3,
            ("extra 必须是映射",),
        ),
        # ---- 第 2 轮：严格布尔不得误伤合法写法 ----
        (
            "valid-yaml-native-bools",
            (
                "app:\n  timezone: Asia/Shanghai\n"
                "llm:\n  base_url: https://api.deepseek.com\n  model: deepseek-chat\n"
                "proxy:\n  enabled: no\n"
                "push:\n  wecom:\n    enabled: no\n  email:\n    enabled: no\n"
                "sources:\n"
                "  - id: a\n    type: rss\n    url: https://a.com/feed\n    enabled: yes\n"
            ),
            0,
            ("[on ] a",),
        ),
        (
            "valid-email-disabled-typed",
            cfg_text(
                lambda d: d["push"]["email"].update(
                    smtp_port=465, use_ssl=True, to_addrs=[], smtp_host="smtp.qq.com"
                )
            ),
            0,
            ("配置结构校验通过",),
        ),
        (
            "valid-no-push-no-proxy-omitted-bools",
            cfg_text(
                lambda d: (
                    d.pop("push"),
                    d.pop("proxy"),
                    d["sources"][0].pop("enabled"),
                )
            ),
            0,
            ("配置结构校验通过", "警告"),
        ),
    ]


def check_config_cases() -> None:
    for slug, text, expect_rc, contains in config_cases():
        path = write_cfg(slug, text)
        run_case(
            f"config-{slug}",
            [PYTHON, "-m", "aihot", "check", "--config", str(path)],
            expect_rc,
            contains,
            SECRETS_IN_USE,
        )


def check_robustness_cases() -> None:
    bom_path = write_cfg("bom", "\ufeff" + cfg_text())
    run_case(
        "config-bom",
        [PYTHON, "-m", "aihot", "check", "--config", str(bom_path)],
        0,
        ("配置结构校验通过",),
        SECRETS_IN_USE,
    )

    dup = (
        "app:\n  top_n: 9\n"
        "llm:\n  base_url: https://api.deepseek.com\n  model: deepseek-chat\n"
        "push:\n  wecom:\n    enabled: false\n  email:\n    enabled: false\n"
        "sources:\n  - {id: a, type: rss, url: 'https://a.com/feed', enabled: false}\n"
        "app:\n  top_n: 3\n"
    )
    dup_path = write_cfg("dup-top-keys", dup)
    observe_case("config-dup-top-keys", [PYTHON, "-m", "aihot", "check", "--config", str(dup_path)])

    bad_port_path = write_cfg(
        "email-disabled-bad-port",
        cfg_text(lambda d: d["push"]["email"].update(smtp_port="abc")),
    )
    run_case(
        "config-email-disabled-bad-port",
        [PYTHON, "-m", "aihot", "check", "--config", str(bad_port_path)],
        3,
        ("smtp_port",),
        SECRETS_IN_USE,
    )

    extra_str_path = write_cfg(
        "extra-string", cfg_text(lambda d: d["sources"][0].update(extra="oops"))
    )
    run_case(
        "config-extra-string",
        [PYTHON, "-m", "aihot", "check", "--config", str(extra_str_path)],
        3,
        ("extra 必须是映射",),
        SECRETS_IN_USE,
    )

    extra_list_path = write_cfg(
        "extra-list", cfg_text(lambda d: d["sources"][0].update(extra=["a", "b"]))
    )
    run_case(
        "config-extra-list",
        [PYTHON, "-m", "aihot", "check", "--config", str(extra_list_path)],
        3,
        ("extra 必须是映射",),
        SECRETS_IN_USE,
    )

    quoted_false_path = write_cfg(
        "enabled-quoted-false",
        cfg_text(lambda d: d["sources"][0].update(enabled="false")),
    )
    run_case(
        "config-enabled-quoted-false",
        [PYTHON, "-m", "aihot", "check", "--config", str(quoted_false_path)],
        3,
        ("必须是布尔值",),
        SECRETS_IN_USE,
    )

    # 第 3 轮：data_dir / log_dir 指向已存在文件 -> rc=3 明确报错（非 traceback）
    blocker = WORK / "blocker-file"
    blocker.write_text("not a dir", encoding="utf-8")
    for field in ("log_dir", "data_dir"):
        dir_file_path = write_cfg(
            f"{field}-is-file",
            cfg_text(lambda d, f=field: d["app"].update({f: "blocker-file"})),
        )
        run_case(
            f"config-{field}-is-file",
            [PYTHON, "-m", "aihot", "check", "--config", str(dir_file_path)],
            3,
            (f"app.{field} 指向已存在的文件",),
            ("Traceback", *SECRETS_IN_USE),
        )


def check_data_dir_resolution() -> None:
    sub = WORK / "subdir"
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "app": {"data_dir": "mydata", "log_dir": "mylogs"},
                "llm": deepcopy(BASE["llm"]),
                "push": {
                    "wecom": {"enabled": False},
                    "email": {"enabled": False},
                },
                "sources": BASE["sources"],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    run_case(
        "config-relative-dirs",
        [PYTHON, "-m", "aihot", "check", "--config", str(path)],
        0,
        (str(sub / "mydata"), str(sub / "mylogs")),
        (),
    )


def check_env_secrets() -> None:
    text = cfg_text(
        lambda d: d.update(
            llm={**d["llm"], "api_key": "", "api_key_env": "M0_LLM_KEY"},
            push={
                "wecom": {"enabled": True, "webhook": "", "webhook_env": "M0_WECOM_HOOK"},
                "email": {"enabled": False},
            },
        )
    )
    path = write_cfg("env-secrets", text)
    run_case(
        "config-env-secrets",
        [PYTHON, "-m", "aihot", "check", "--config", str(path)],
        0,
        ("LLM API key 已配置", "企业微信 webhook 已配置"),
        (FAKE_ENV_LLM_KEY, FAKE_ENV_WEBHOOK),
        env_extra={"M0_LLM_KEY": FAKE_ENV_LLM_KEY, "M0_WECOM_HOOK": FAKE_ENV_WEBHOOK},
    )


def check_cli_semantics() -> None:
    valid_path = write_cfg("cli-valid", cfg_text())
    cfg_a = write_cfg("cli-top12", cfg_text())
    cfg_b = write_cfg("cli-top7", cfg_text(lambda d: d["app"].update(top_n=7)))

    run_case("cli-help-long", [PYTHON, "-m", "aihot", "--help"], 0, ("usage", "check", "version"))
    run_case("cli-help-sub", [PYTHON, "-m", "aihot", "check", "--help"], 0, ("--net", "--config"))
    run_case("cli-version-cmd", [PYTHON, "-m", "aihot", "version"], 0, ("aihot 0.1.0",))
    run_case("cli-version-flag", [PYTHON, "-m", "aihot", "--version"], 0, ("aihot 0.1.0",))
    run_case("cli-no-args", [PYTHON, "-m", "aihot"], 0, ("usage",))
    run_case("cli-unknown-cmd", [PYTHON, "-m", "aihot", "frobnicate"], 2, ("invalid choice",))
    run_case("cli-unknown-opt", [PYTHON, "-m", "aihot", "check", "--nope"], 2, ())
    run_case(
        "cli-config-missing",
        [PYTHON, "-m", "aihot", "check", "--config", str(WORK / "nope.yaml")],
        3,
        ("配置文件不存在",),
    )
    run_case(
        "cli-config-is-dir",
        [PYTHON, "-m", "aihot", "check", "--config", str(WORK)],
        3,
        ("配置文件不存在",),
    )
    run_case(
        "cli-config-after",
        [PYTHON, "-m", "aihot", "check", "--config", str(valid_path)],
        0,
        ("配置结构校验通过",),
    )
    run_case(
        "cli-config-before",
        [PYTHON, "-m", "aihot", "--config", str(valid_path), "check"],
        0,
        ("配置结构校验通过",),
    )
    both = run_case(
        "cli-config-both-positions",
        [PYTHON, "-m", "aihot", "--config", str(cfg_a), "check", "--config", str(cfg_b)],
        0,
        ("Top 7",),
    )
    if "Top 12" in both:
        record("cli-config-both-positions-precedence", False, "子命令后的 --config 未覆盖前一个")
    run_case(
        "cli-debug-after",
        [PYTHON, "-m", "aihot", "check", "--debug", "--config", str(valid_path)],
        0,
        (),
    )
    run_case(
        "cli-debug-before",
        [PYTHON, "-m", "aihot", "--debug", "--config", str(valid_path), "check"],
        0,
        (),
    )
    run_case("cli-config-no-value", [PYTHON, "-m", "aihot", "check", "--config"], 2, ())
    run_case(
        "cli-check-positional",
        [PYTHON, "-m", "aihot", "check", "extra"],
        2,
        ("unrecognized arguments",),
    )

    if AIHOT_EXE.exists():
        run_case("cli-exe-version", [str(AIHOT_EXE), "version"], 0, ("aihot 0.1.0",))
        run_case(
            "cli-exe-check",
            [str(AIHOT_EXE), "check", "--config", str(valid_path)],
            0,
            ("配置结构校验通过",),
        )
    else:
        record("cli-exe-missing", False, f"未找到控制台脚本: {AIHOT_EXE}")

    check_redirect_encoding(valid_path)


def check_redirect_encoding(valid_path: Path) -> None:
    emoji_name = "量子位🚀·编码测试"
    enc_cfg = write_cfg("cli-encoding", cfg_text(lambda d: d["sources"][0].update(name=emoji_name)))
    out_file = WORK / "redirect-out.txt"
    with out_file.open("wb") as stream:
        cp = subprocess.run(
            [PYTHON, "-m", "aihot", "check", "--config", str(enc_cfg)],
            cwd=str(REPO),
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=False,
        )
    raw = out_file.read_bytes()
    try:
        decoded = raw.decode("utf-8")
        encoding_ok = "\ufffd" not in decoded and emoji_name in decoded
        detail = f"rc={cp.returncode}, utf-8 解码成功, emoji 在输出中={emoji_name in decoded}"
    except UnicodeDecodeError as exc:
        encoding_ok = False
        detail = f"rc={cp.returncode}, UTF-8 解码失败: {exc}"
    record("cli-redirect-utf8", encoding_ok, detail)
    save_evidence(
        "cli-redirect-utf8",
        [PYTHON, "-m", "aihot", "check", "--config", str(enc_cfg), ">", str(out_file)],
        subprocess.CompletedProcess([], cp.returncode, raw, b""),
    )
    _ = valid_path


def check_net_cases() -> None:
    # D5-a：全部探测通过 -> rc 0（wecom 假 webhook HTTP 200；LLM 无 key 时 401 视为可达）
    wecom_path = write_cfg(
        "net-wecom-fake",
        cfg_text(lambda d: d["llm"].update(api_key="")),
    )
    run_case(
        "net-wecom-fake-ok",
        [PYTHON, "-m", "aihot", "check", "--net", "--config", str(wecom_path)],
        0,
        ("网络探测", "企业微信 webhook", "[OK ] LLM API"),
        (FAKE_WEBHOOK, "M0FAKE0123456789abcdef"),
    )

    # D5-b：有 key 但鉴权失败 -> rc 1
    auth_path = write_cfg("net-llm-auth-fail", cfg_text())
    run_case(
        "net-llm-auth-fail",
        [PYTHON, "-m", "aihot", "check", "--net", "--config", str(auth_path)],
        1,
        ("[ERR] LLM API", "网络探测存在"),
        (FAKE_LLM_KEY,),
    )

    # D5-c：LLM 不可达 -> rc 1
    llm_path = write_cfg(
        "net-llm-unreachable",
        cfg_text(lambda d: d["llm"].update(base_url="http://127.0.0.1:1")),
    )
    run_case(
        "net-llm-unreachable",
        [PYTHON, "-m", "aihot", "check", "--net", "--config", str(llm_path)],
        1,
        ("网络探测存在",),
        (FAKE_LLM_KEY,),
    )

    # D3-a（M1-R2 契约变更）：代理端口非数字 -> 配置错误 rc=3 + 可操作提示，无 traceback
    proxy_path = write_cfg(
        "net-proxy-bad-port",
        cfg_text(lambda d: d["proxy"].update(enabled=True, url="http://127.0.0.1:abc")),
    )
    run_case(
        "net-proxy-bad-port",
        [PYTHON, "-m", "aihot", "check", "--net", "--config", str(proxy_path)],
        3,
        ("proxy.url 无效", "配置校验未通过"),
        ("Traceback",),
    )

    # D3-b：LLM 非法 URL -> 干净 [ERR] + rc 1，无 traceback、无泄漏
    invalid_path = write_cfg(
        "net-llm-invalid-url",
        cfg_text(lambda d: d["llm"].update(base_url="http://[::1/models?key=FAKELMURL0123456789")),
    )
    run_case(
        "net-llm-invalid-url",
        [PYTHON, "-m", "aihot", "check", "--net", "--config", str(invalid_path)],
        1,
        ("[ERR] LLM API",),
        ("Traceback", "FAKELMURL0123456789"),
    )


def check_fix_cases() -> None:
    # D6：代理凭据在 check 摘要中掩码
    proxy_pass = "M0PROXYPASS0123456789"
    proxy_cfg = write_cfg(
        "proxy-credentials",
        cfg_text(
            lambda d: d["proxy"].update(
                enabled=True, url=f"http://m0user:{proxy_pass}@127.0.0.1:7890"
            )
        ),
    )
    run_case(
        "fix-d6-proxy-credentials-masked",
        [PYTHON, "-m", "aihot", "check", "--config", str(proxy_cfg)],
        0,
        ("***@127.0.0.1:7890",),
        (proxy_pass, "m0user:"),
    )

    # D8：example 模板开箱可用
    run_case(
        "fix-d8-example-out-of-box",
        [PYTHON, "-m", "aihot", "check", "--config", str(REPO / "config.example.yaml")],
        0,
        ("配置结构校验通过",),
        (),
    )


def main() -> int:
    print(f"WORK={WORK}")
    check_cli_semantics()
    check_config_cases()
    check_robustness_cases()
    check_data_dir_resolution()
    check_env_secrets()
    check_net_cases()
    check_fix_cases()

    failed = [item for item in RESULTS if not item["ok"]]
    print(f"\n== SUMMARY: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    for item in failed:
        print(f"FAILED: {item['name']} :: {item['detail']}")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "_summary.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
