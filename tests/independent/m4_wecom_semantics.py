"""M4 临时探测：企微 webhook GET 语义（只发 GET，不产生消息）。

- 真实 webhook 用已配置 key 发一次 GET（不产生消息），看响应码/errcode
- 再用随机假 key 发一次 GET，对比响应
报告中只记录 errcode/状态与 key 掩码，不打印 key 原文。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import yaml

PROJECT = Path(__file__).resolve().parents[2]
CONFIG = PROJECT / "config.yaml"
PROXY = "http://127.0.0.1:7897"


def mask(value: str) -> str:
    return f"<len={len(value)} sha1={hashlib.sha1(value.encode()).hexdigest()[:8]}>"


def probe(url: str) -> dict[str, object]:
    response = httpx.get(url, timeout=15.0, proxy=PROXY)
    try:
        body = response.json()
    except ValueError:
        body = response.text[:200]
    errcode = body.get("errcode") if isinstance(body, dict) else None
    errmsg = body.get("errmsg") if isinstance(body, dict) else None
    return {"status": response.status_code, "errcode": errcode, "errmsg": errmsg}


def main() -> int:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    real = str(data["push"]["wecom"]["webhook"])
    print("real webhook:", mask(real))
    print("real GET  :", probe(real))
    bogus = real.split("key=")[0] + "key=00000000-0000-0000-0000-000000000000"
    print("bogus GET :", probe(bogus))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
