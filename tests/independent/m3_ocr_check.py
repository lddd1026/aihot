"""M3 长图「中文可读」独立验证：Windows 内置 OCR（zh-Hans-CN）+ 与存档 md 标题比对。

用法: .venv\\Scripts\\python.exe tests\\independent\\m3_ocr_check.py <图片路径> <md路径> [证据名]
说明: OCR 通过 powershell.exe (5.1) 调用 WinRT Windows.Media.Ocr；仅读取文件，不改动图片。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import EVIDENCE, PROJECT  # noqa: E402

PS_SCRIPT = PROJECT / "tests" / "independent" / "m3_ocr.ps1"


def run_ocr(image: Path) -> tuple[str, str]:
    """返回 (ocr_text, 控制台输出)。"""
    proc = subprocess.run(
        [
            str(Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_SCRIPT),
            "-Path",
            str(image),
        ],
        capture_output=True,
        timeout=300,
    )
    output = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    ocr_file = image.with_suffix(image.suffix + ".ocr.txt")
    text = ocr_file.read_text(encoding="utf-8") if ocr_file.exists() else ""
    return text, output


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def main() -> int:
    image = Path(sys.argv[1]).resolve()
    md_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
    label = sys.argv[3] if len(sys.argv) > 3 else image.stem

    text, output = run_ocr(image)
    flat = normalize(text)
    lines = [line for line in text.splitlines() if line.strip()]
    print(
        f"OCR 输出行数={len(lines)} 字符数={len(text)} "
        f"引擎信息={output.strip().splitlines()[:2]}"
    )

    titles: list[str] = []
    if md_path and md_path.exists():
        md = md_path.read_text(encoding="utf-8")
        titles = re.findall(r"\[([^\]]+)\]\(http", md)
        titles = [re.sub(r"\s+", "", title) for title in titles]

    hits: list[str] = []
    misses: list[str] = []
    soft_hits: list[str] = []
    recalls: list[float] = []
    for title in titles:
        key = title[:8]
        if key and key in flat:
            hits.append(key)
            continue
        # OCR 可能合并行/丢字符：放宽为 6 字前缀或后缀
        if (len(title) >= 6 and (title[:6] in flat or title[-6:] in flat)):
            soft_hits.append(key)
        else:
            misses.append(key)
        present = sum(1 for char in set(title) if char in flat)
        recalls.append(present / max(1, len(set(title))))
    avg_recall = sum(recalls) / len(recalls) if recalls else 1.0

    result_lines = [
        f"# OCR 验证：{label}",
        f"图片: {image}",
        f"存档 md: {md_path}",
        f"OCR 引擎输出: {output.strip()}",
        f"OCR 行数: {len(lines)}  字符数: {len(text)}",
        f"标题比对（前 8 字严格匹配）: {len(hits)}/{len(titles)} 命中",
        f"标题比对（放宽：6 字前缀或后缀）: {len(hits) + len(soft_hits)}/{len(titles)} 命中",
        f"字符级平均召回率: {avg_recall:.3f}",
        f"未命中: {misses}",
        "",
        "## OCR 文本（前 1200 字）",
        text[:1200],
    ]
    evidence_dir = EVIDENCE / "ocr"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"{label}.md"
    evidence_path.write_text("\n".join(result_lines), encoding="utf-8")
    print(
        f"标题比对: 严格 {len(hits)}/{len(titles)}；"
        f"放宽 {len(hits) + len(soft_hits)}/{len(titles)}；"
        f"字符召回 {avg_recall:.3f}；未命中={misses}"
    )
    print(f"证据 → {evidence_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
