"""M2 抽样：导出真实库的翻译/摘要抽检样本（20 条，只读）与真实调用候选集（6 条）。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
DB = Path(__file__).resolve().parents[2] / "data" / "aihot.db"
OUT = Path(__file__).resolve().parents[2] / "reports" / "evidence" / "m2" / "real-llm"
OUT.mkdir(parents=True, exist_ok=True)

CJK = range(0x4E00, 0x9FFF + 1)


def has_cjk(text: str) -> bool:
    return any(ord(ch) in CJK for ch in text or "")


conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

# 抽检 20 条：优先「英文原文 + 中文加工」以验证翻译
rows = [dict(r) for r in conn.execute(
    "SELECT id, source_id, title, url, raw_summary, category, importance, cn_title, cn_summary"
    " FROM items WHERE enriched_at IS NOT NULL AND category != '过滤' ORDER BY id"
)]
english = [r for r in rows if not has_cjk(r["title"]) and has_cjk(r["cn_title"])]
others = [r for r in rows if r not in english]
sample = (english[:14] + others[:6])[:20]
(OUT / "sample-20.json").write_text(
    json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"英文原文样本 {len(english)} 条；抽检 {len(sample)} 条 → {OUT / 'sample-20.json'}")
for row in sample:
    print(f"\n[{row['id']}] {row['source_id']} | {row['category']} | imp={row['importance']}")
    print(f"  EN : {row['title']}")
    print(f"  CN : {row['cn_title']}")
    print(f"  SUM: {row['cn_summary']}")

# 真实调用候选：跨源 6 条（排除过滤）
candidates = [
    r for r in rows
    if r["source_id"] in ("qbitai", "infoq_cn", "openai_news", "techcrunch_ai")
][:6]
(OUT / "real-call-candidates.json").write_text(
    json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"\n真实调用候选 {len(candidates)} 条 → {OUT / 'real-call-candidates.json'}")
conn.close()
