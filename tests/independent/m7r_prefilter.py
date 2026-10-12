"""M7 独立验收 · 声明 1/2/3：规则层主题剔除、误杀护栏、配置行为。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m7r_prefilter.py
证据: reports/evidence/m7r/m7r_prefilter/ 与 m7r_prefilter.json
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m7r_support as m7  # noqa: E402

from aihot.config import ConfigError, load_config  # noqa: E402

# 额外构造的反例：验证「露 GPU/显存/型号名」的工程内容不会被误杀
EXTRA_ENGINEERING: list[str] = [
    "如何在单张 2080 Ti 上训练 LoRA：省钱路线",
    "手机端侧小模型实测：8GB 内存跑 3B 全流程",
    "用 RTX 4090 做多卡推理的性价比分析",
    "国产显卡运行 DeepSeek 蒸馏模型初体验",
    "A100 与 H100 显存带宽对比：推理到底差多少",
    "我们在服务器上部署 vLLM 的经验：并发调优",
    "显存不足？试试梯度检查点与分页优化器",
    "Serving 70B on a single 4090: VRAM tricks we learned",
]


def run_topic_matrix() -> tuple[dict[str, Any], m3.Suite]:
    """声明 1+2：混合批次一次跑完，逐条核对期望。"""
    suite = m3.Suite("m7r_prefilter")
    drop = m7.TOPIC_DROP_SAMPLES
    keep = m7.TOPIC_KEEP_SAMPLES
    extra_keep = [{"kind": "engineering-extra", "title": title} for title in EXTRA_ENGINEERING]

    mixed = drop + keep + extra_keep
    titles = [(sample["title"], f"{sample['kind']} m7r fixture") for sample in mixed]
    result, _ = m7.run_prefilter_titles(titles, pipeline=m7.base_pipeline_raw())

    dropped_titles = {item.title for item in result.items}
    per_row: list[dict[str, Any]] = []
    all_expect_ok = True
    for sample in mixed:
        expect_drop = sample["kind"] in {"hardware", "business"}
        actually_dropped = sample["title"] not in dropped_titles
        ok = actually_dropped == expect_drop
        all_expect_ok = all_expect_ok and ok
        per_row.append(
            {
                "kind": sample["kind"],
                "title": sample["title"],
                "expect": "drop" if expect_drop else "keep",
                "actual": "drop" if actually_dropped else "keep",
                "ok": ok,
            }
        )
    suite.evidence_json("1-topic-matrix.json", per_row)

    expect_drop_count = sum(1 for sample in mixed if sample["kind"] in {"hardware", "business"})
    expect_keep_count = len(mixed) - expect_drop_count
    suite.check(
        "1.1 混合批次统计：total/dropped_topic/kept 与期望一致",
        result.stats.total == len(mixed)
        and result.stats.dropped_topic == expect_drop_count
        and result.stats.kept == expect_keep_count,
        f"total={result.stats.total} dropped_topic={result.stats.dropped_topic}"
        f" kept={result.stats.kept}（期望 total={len(mixed)}"
        f" dropped_topic={expect_drop_count} kept={expect_keep_count}）",
    )
    suite.check(
        "1.2 逐条期望核对：13 类硬件/商业剔除 + 20 类工程保留全部一致",
        all_expect_ok,
        f"不一致 {sum(1 for row in per_row if not row['ok'])} 条（详见 1-topic-matrix.json）",
    )
    reason_key = "主题不符（硬件/商业）"
    sample_bucket = result.stats.dropped_samples.get(reason_key, [])
    drop_titles = {sample["title"] for sample in drop}
    suite.check(
        "1.3 剔除样本：原因键与样本标题正确（≤5 条截断）",
        len(sample_bucket) == min(5, len(drop)) and all(t in drop_titles for t in sample_bucket),
        f"样本={sample_bucket}",
    )
    suite.check(
        "1.4 剔除计数 dropped_topic 精确等于硬件/商业标题数",
        result.stats.dropped_topic == len(drop),
        f"dropped_topic={result.stats.dropped_topic} 期望={len(drop)}",
    )
    kept_titles = [item.title for item in result.items]
    suite.check(
        "1.5 保留集恰为工程标题集合（顺序保持）",
        kept_titles == [sample["title"] for sample in keep + extra_keep],
        f"保留 {len(kept_titles)} 条；首条={kept_titles[0] if kept_titles else '无'}",
    )
    guard_rows = [row for row in per_row if row["kind"].startswith("engineering")]
    guard_ok = all(row["ok"] and row["actual"] == "keep" for row in guard_rows)
    suite.check(
        "2.1 误杀护栏：GPU/显存/型号名工程条目（含新构反例）全部保留",
        guard_ok,
        f"护栏样本 {len(guard_rows)} 条全部保留："
        + "；".join(f"{row['title']}({row['actual']})" for row in guard_rows[:4])
        + " …",
    )
    positive = [row for row in per_row if row["kind"] in {"hardware", "business"}]
    suite.check(
        "2.2 同批次正对照：硬件/商业仍被剔除（排除“模式整体失效”假象）",
        all(row["actual"] == "drop" for row in positive),
        f"正对照 {len(positive)} 条全部剔除",
    )
    return {"stats_kept": result.stats.kept, "rows": per_row}, suite


def run_config_matrix(suite: m3.Suite) -> None:
    """声明 3：exclude_keywords 追加 / use_builtin_exclude 开关 / 配置校验。"""
    base = m7.base_pipeline_raw()
    hardware_title = "台积电 2nm 晶圆产能被 AI 公司预订一空"
    custom_title = "web3 钱包与 AI 智能体的结合实践"

    def pipeline_with(**prefilter_extra: Any) -> dict[str, Any]:
        pipeline = json.loads(json.dumps(base))
        prefilter = pipeline.setdefault("prefilter", {})
        prefilter.update(prefilter_extra)
        return pipeline

    # (a) 默认（真实配置）：内置生效
    result, _ = m7.run_prefilter_titles(
        [(hardware_title, "hardware"), (custom_title, "custom")],
        pipeline=pipeline_with(),
        ws_name="m7r-config-default",
    )
    defaults_ok = (
        result.stats.dropped_topic == 1
        and [item.title for item in result.items] == [custom_title]
    )
    suite.check(
        "3.1 默认配置：内置 exclude 生效（硬件剔除），未命中自定义的 web3 标题保留",
        defaults_ok,
        f"dropped_topic={result.stats.dropped_topic} kept={[i.title for i in result.items]}",
    )

    # (b) 追加自定义 exclude_keywords：内置 + 自定义同时生效
    result, _ = m7.run_prefilter_titles(
        [(hardware_title, "hardware"), (custom_title, "custom")],
        pipeline=pipeline_with(exclude_keywords=["web3 钱包"]),
        ws_name="m7r-config-append",
    )
    append_ok = result.stats.dropped_topic == 2 and result.stats.kept == 0
    suite.check(
        "3.2 exclude_keywords 追加生效：内置硬件 + 自定义 web3 同时剔除",
        append_ok,
        f"dropped_topic={result.stats.dropped_topic} kept={result.stats.kept}",
    )

    # (c) use_builtin_exclude=false：仅自定义生效（硬件放行）
    result, _ = m7.run_prefilter_titles(
        [(hardware_title, "hardware"), (custom_title, "custom")],
        pipeline=pipeline_with(exclude_keywords=["web3 钱包"], use_builtin_exclude=False),
        ws_name="m7r-config-custom-only",
    )
    only_custom_ok = (
        result.stats.dropped_topic == 1
        and [item.title for item in result.items] == [hardware_title]
    )
    suite.check(
        "3.3 use_builtin_exclude=false：仅自定义生效（内置硬件词放行）",
        only_custom_ok,
        f"dropped_topic={result.stats.dropped_topic} kept={[i.title for i in result.items]}",
    )

    # (d) 配置校验：接受新键 / 错误类型报错
    def load_with(prefilter_extra: dict[str, Any]) -> tuple[bool, str]:
        ws = m3.workspace("m7r-config-validate")
        raw = m4_sanitized_with(prefilter_extra)
        cfg_path = m4_dump(ws, raw)
        try:
            cfg = load_config(cfg_path)
        except ConfigError as exc:
            return False, str(exc)
        return True, cfg.pipeline.prefilter.exclude_keywords.__repr__() + (
            f" use_builtin_exclude={cfg.pipeline.prefilter.use_builtin_exclude}"
        )

    accepted, detail = load_with({"exclude_keywords": ["web3"], "use_builtin_exclude": False})
    suite.check(
        "3.4 配置校验接受新键（exclude_keywords / use_builtin_exclude）",
        accepted and "use_builtin_exclude=False" in detail,
        f"loaded: {detail[:120]}",
    )
    error_cases = [
        ("exclude_keywords 为字符串", {"exclude_keywords": "web3"}, "exclude_keywords"),
        ("exclude_keywords 含非字符串", {"exclude_keywords": ["ok", 3]}, "exclude_keywords"),
        ("use_builtin_exclude 为字符串", {"use_builtin_exclude": "false"}, "use_builtin_exclude"),
        ("use_builtin_exclude 为数字", {"use_builtin_exclude": 1}, "use_builtin_exclude"),
    ]
    for label, patch, fragment in error_cases:
        accepted, detail = load_with(patch)
        ok = (not accepted) and fragment in detail
        suite.check(
            f"3.5 配置校验报错：{label}",
            ok,
            f"accepted={accepted}；报错片段命中={fragment in detail}（{detail[:100]}）",
        )


def m4_sanitized_with(prefilter_extra: dict[str, Any]) -> dict[str, Any]:
    raw = copy.deepcopy(m7.m4.sanitized_real_raw())
    pipeline = raw.setdefault("pipeline", {})
    prefilter = pipeline.setdefault("prefilter", {})
    prefilter.update(prefilter_extra)
    return raw


def m4_dump(ws: Path, raw: dict[str, Any], name: str = "config.yaml") -> Path:
    raw["app"]["data_dir"] = str(ws / "data")
    raw["app"]["log_dir"] = str(ws / "logs")
    raw["push"]["wecom"] = {"enabled": False, "webhook": ""}
    raw["push"].setdefault("email", {})["enabled"] = False
    return m7.m4.dump_config(ws / name, raw)


def run_cli_config_checks(suite: m3.Suite) -> None:
    """声明 3 补充：CLI `check` 走同一套校验（rc 与文案）。"""
    ws = m3.workspace("m7r-config-cli")
    good = m4_dump(
        ws,
        m4_sanitized_with({"exclude_keywords": ["web3"], "use_builtin_exclude": True}),
        name="good.yaml",
    )
    bad = m4_dump(
        ws, m4_sanitized_with({"use_builtin_exclude": "false"}), name="bad.yaml"
    )
    good_result = m3.run_cli(["check", "--config", str(good)], timeout=120)
    suite.evidence("3.6-cli-check-good.txt", good_result.text)
    suite.check(
        "3.6 CLI check：新键合法配置 rc=0 且结构校验通过",
        good_result.rc == 0 and "[OK] 配置结构校验通过" in good_result.stdout,
        f"rc={good_result.rc}",
    )
    bad_result = m3.run_cli(["check", "--config", str(bad)], timeout=120)
    suite.evidence("3.7-cli-check-bad.txt", bad_result.text)
    suite.check(
        "3.7 CLI check：错误类型 rc=3 且报错点名 use_builtin_exclude",
        bad_result.rc == 3 and "use_builtin_exclude" in bad_result.combined,
        f"rc={bad_result.rc}；片段="
        + next(
            (line.strip() for line in bad_result.combined.splitlines() if "use_builtin" in line),
            "",
        ),
    )


def main() -> int:
    summary, suite = run_topic_matrix()
    run_config_matrix(suite)
    run_cli_config_checks(suite)
    suite.evidence_json("summary.json", summary)
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
