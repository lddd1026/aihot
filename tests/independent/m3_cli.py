"""M3 验收项 8：CLI 行为与退出码（run / preview / resend / --no-image / --top / --channel）。

约定（M0/M2 已建立，M3 回归）：
- 成功 0；运行期错误 1；用法错误（argparse）2；配置错误 3
- 非法参数值必须 rc=2 且不出现 traceback
- preview/resend 缺失存档时必须给出可操作错误
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    Suite,
    has_traceback,
    real_state_snapshot,
    seed_enriched_items,
    workspace,
    write_config,
)


def main() -> int:
    suite = Suite("m3_cli")
    before = real_state_snapshot()
    root = workspace("cli")
    data_dir = root / "data"
    db_path = data_dir / "aihot.db"
    seed_enriched_items(
        db_path,
        [
            {
                "source_id": "qbitai",
                "title": f"CLI 测试条目 {index}",
                "url": f"https://example.com/cli/{index}",
                "cn_title": f"CLI 测试条目 {index}",
                "cn_summary": f"摘要 {index}",
                "category": "模型发布",
                "importance": 90 - index,
                "cluster_id": index,
            }
            for index in range(1, 8)
        ],
    )
    cfg_path = write_config(root / "config.yaml", data_dir=data_dir, log_dir=root / "logs", top_n=5)

    # ---------- 1. help / version / check ----------
    for args, label in (
        (["--help"], "根 --help"),
        (["run", "--help"], "run --help"),
        (["preview", "--help"], "preview --help"),
        (["resend", "--help"], "resend --help"),
        (["version"], "version"),
        (["check", "--config", str(cfg_path)], "check（临时配置）"),
    ):
        result = __import__("m3_support").run_cli(args, timeout=120)
        suite.check(
            f"8.1 {label} → rc=0",
            result.rc == 0 and not has_traceback(result.combined),
            f"rc={result.rc}",
        )

    # ---------- 2. 非法参数 → rc=2 ----------
    for args, label in (
        (["run", "--top", "-1"], "run --top -1"),
        (["run", "--top", "abc"], "run --top abc"),
        (["run", "--no-such-flag"], "run 未知选项"),
        (["resend", "--channel", "bogus"], "resend --channel bogus"),
        (["unknown-command"], "未知子命令"),
    ):
        result = __import__("m3_support").run_cli(args, timeout=120)
        suite.check(
            f"8.2 {label} → rc=2 且无 traceback",
            result.rc == 2 and not has_traceback(result.combined),
            f"rc={result.rc} stderr_head={result.stderr.strip().splitlines()[:1]}",
        )

    # ---------- 3. 非法日期（--date 值非法） ----------
    for command in ("preview", "resend"):
        result = __import__("m3_support").run_cli(
            [command, "--config", str(cfg_path), "--date", "not-a-date"], timeout=120
        )
        suite.check(
            f"8.3 {command} --date not-a-date：不崩溃且给出可操作错误",
            not has_traceback(result.combined) and result.rc != 0,
            f"rc={result.rc} traceback={has_traceback(result.combined)} "
            f"stderr_head={result.stderr.strip().splitlines()[:1]}",
        )
        result2 = __import__("m3_support").run_cli(
            [command, "--config", str(cfg_path), "--date", "2026-13-45"], timeout=120
        )
        suite.check(
            f"8.4 {command} --date 2026-13-45：不崩溃且给出可操作错误",
            not has_traceback(result2.combined) and result2.rc != 0,
            f"rc={result2.rc} traceback={has_traceback(result2.combined)}",
        )

    # ---------- 4. 配置错误 → rc=3 ----------
    result = __import__("m3_support").run_cli(
        ["run", "--config", str(root / "missing.yaml")], timeout=120
    )
    suite.check(
        "8.5 配置缺失 → rc=3 且无 traceback",
        result.rc == 3 and not has_traceback(result.combined),
        f"rc={result.rc}",
    )

    # ---------- 5. preview 行为 ----------
    preview_missing = __import__("m3_support").run_cli(
        ["preview", "--config", str(cfg_path), "--date", "1999-01-01"], timeout=300
    )
    suite.evidence("preview-missing-archive.txt", preview_missing.text)
    suite.check(
        "8.6 preview 无存档日期：改用库内数据重新组装（rc=0，不崩溃）",
        preview_missing.rc == 0 and not has_traceback(preview_missing.combined),
        f"rc={preview_missing.rc} 提示={'没有 1999-01-01 的存档' in preview_missing.stdout}",
    )
    preview_today = __import__("m3_support").run_cli(
        ["preview", "--config", str(cfg_path)], timeout=300
    )
    suite.check(
        "8.7 preview 默认日期：rc=0 且展示三件套文件",
        preview_today.rc == 0
        and "markdown" in preview_today.stdout
        and "html" in preview_today.stdout
        and "日报日期:" in preview_today.stdout,
        f"rc={preview_today.rc}",
    )

    # ---------- 6. run --no-image / --top ----------
    import shutil  # noqa: E402

    shutil.rmtree(data_dir / "digests", ignore_errors=True)
    no_image = __import__("m3_support").run_cli(
        ["run", "--config", str(cfg_path), "--dry-run", "--no-image"], timeout=600
    )
    suite.evidence("run-no-image.txt", no_image.text)
    digests = (
        sorted(p.name for p in (data_dir / "digests").iterdir())
        if (data_dir / "digests").exists()
        else []
    )
    suite.check(
        "8.8 run --no-image：纯文字渲染（无 jpg，md/html 正常）",
        no_image.rc == 0
        and "纯文字（未渲染长图）" in no_image.stdout
        and not any(name.endswith(".jpg") for name in digests)
        and any(name.endswith(".md") for name in digests)
        and any(name.endswith(".html") for name in digests),
        f"rc={no_image.rc} files={digests}",
    )
    top3 = __import__("m3_support").run_cli(
        ["run", "--config", str(cfg_path), "--dry-run", "--top", "3"], timeout=600
    )
    suite.evidence("run-top3.txt", top3.text)
    suite.check(
        "8.9 run --top 3：日报精选 3 条",
        top3.rc == 0 and "精选 3 条" in top3.stdout,
        [line for line in top3.stdout.splitlines() if "③ 日报" in line],
    )
    top0 = __import__("m3_support").run_cli(
        ["run", "--config", str(cfg_path), "--dry-run", "--top", "0"], timeout=600
    )
    suite.check(
        "8.10 run --top 0：回退配置 top_n=5",
        top0.rc == 0 and "精选 5 条" in top0.stdout,
        [line for line in top0.stdout.splitlines() if "③ 日报" in line],
    )

    # ---------- 7. --config 位置（前后均可）与 --debug ----------
    for args, label in (
        (["--config", str(cfg_path), "preview"], "--config 在前"),
        (["preview", "--config", str(cfg_path), "--debug"], "--debug 在后"),
    ):
        result = __import__("m3_support").run_cli(args, timeout=300)
        suite.check(f"8.11 {label} → rc=0", result.rc == 0, f"rc={result.rc}")

    # ---------- 8. resend 渠道选择（全新工作区：确无存档） ----------
    fresh_root = workspace("cli-fresh")
    fresh_data = fresh_root / "data"
    seed_enriched_items(
        fresh_data / "aihot.db",
        [
            {
                "source_id": "qbitai",
                "title": "全新条目",
                "url": "https://example.com/fresh/1",
                "cn_title": "全新条目",
                "cn_summary": "摘要",
                "category": "模型发布",
                "importance": 80,
                "cluster_id": 1,
            }
        ],
    )
    fresh_cfg = write_config(
        fresh_root / "config.yaml", data_dir=fresh_data, log_dir=fresh_root / "logs"
    )
    for channel, label in (("wecom", "wecom"), ("email", "email"), ("all", "all")):
        result = __import__("m3_support").run_cli(
            ["resend", "--config", str(fresh_cfg), "--date", "1999-01-01", "--channel", channel],
            timeout=120,
        )
        suite.check(
            f"8.12 resend --channel {label}（无存档）→ rc=1 且提示明确",
            result.rc == 1
            and "没有 1999-01-01 的日报存档" in result.stdout
            and not has_traceback(result.combined),
            f"rc={result.rc}",
        )

    after = real_state_snapshot()
    suite.check("8.13 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
