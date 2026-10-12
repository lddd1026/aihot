"""M1 recon：真实源可达性 + 时延（只读，不落库）。"""

from __future__ import annotations

import json
import time

import httpx

SOURCES = [
    ("qbitai", "https://www.qbitai.com/feed"),
    ("infoq_cn", "https://www.infoq.cn/feed"),
    ("techcrunch_ai", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("openai_news", "https://openai.com/news/rss.xml"),
    (
        "hackernews",
        "https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=1",
    ),
]

UA = "Mozilla/5.0 (compatible; aihot/0.1; AI news aggregator)"


def main() -> None:
    rows = []
    for source_id, url in SOURCES:
        start = time.perf_counter()
        try:
            response = httpx.get(
                url, timeout=30, follow_redirects=True, headers={"User-Agent": UA}
            )
            status: int | str = response.status_code
            size = len(response.content)
        except Exception as exc:  # noqa: BLE001 - recon 用，记录任何异常
            status = f"{type(exc).__name__}"
            size = 0
        duration = round((time.perf_counter() - start) * 1000)
        rows.append({"id": source_id, "status": status, "bytes": size, "ms": duration})
        print(f"{source_id:<15} {status} {size} bytes {duration} ms")
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    main()
