"""Probe nested-forward image URLs and try HTTP variants.

    venv\\Scripts\\python.exe patches\\probe_forward_images.py
    venv\\Scripts\\python.exe patches\\probe_forward_images.py --id 11912981
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp

WS_URL = "ws://127.0.0.1:3001/onebot/v11/ws"
DEFAULT_ID = "11912981"
OUT = Path(__file__).resolve().parents[1] / "docs" / "samples"


def walk_images(obj, acc: list) -> None:
    if isinstance(obj, dict):
        if obj.get("type") == "image":
            acc.append(obj.get("data") or {})
        for v in obj.values():
            walk_images(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            walk_images(v, acc)


async def call_ws(action: str, params: dict, timeout: float = 90.0) -> dict:
    echo = f"probe-img-{action}"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL, heartbeat=15) as ws:
            await ws.send_json({"action": action, "params": params, "echo": echo})
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"{action} timed out")
                msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("echo") == echo:
                    return data


def swap_appid(url: str, appid: str) -> str:
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    q["appid"] = [appid]
    new_q = urlencode({k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in q.items()}, doseq=True)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))


def swap_rkey_type(url: str) -> str | None:
    """If URL has rkey, leave it; also try flipping appid only."""
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    appid = (q.get("appid") or [""])[0]
    if appid == "1406":
        q["appid"] = ["1407"]
    elif appid == "1407":
        q["appid"] = ["1406"]
    else:
        return None
    new_q = urlencode({k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in q.items()}, doseq=True)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))


async def try_get(session: aiohttp.ClientSession, url: str, headers: dict | None = None) -> dict:
    try:
        async with session.get(url, headers=headers or {}, allow_redirects=True) as resp:
            chunk = await resp.content.read(32)
            return {
                "status": resp.status,
                "length": resp.headers.get("Content-Length", ""),
                "type": resp.headers.get("Content-Type", ""),
                "magic": chunk[:16].hex(),
                "jpeg": chunk[:3] == b"\xff\xd8\xff",
                "png": chunk[:8] == b"\x89PNG\r\n\x1a\n",
                "body_preview": chunk[:40].decode("utf-8", errors="replace"),
            }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default=DEFAULT_ID)
    args = parser.parse_args()

    print(f"get_forward_msg id={args.id}")
    resp = await call_ws("get_forward_msg", {"message_id": args.id, "id": args.id})
    status = resp.get("status")
    retcode = resp.get("retcode")
    print(f"status={status} retcode={retcode} message={resp.get('message')}")
    if status != "ok":
        hist = await call_ws(
            "get_group_msg_history",
            {"group_id": 663964060, "count": 20},
        )
        print("history status", hist.get("status"), hist.get("retcode"))
        msgs = ((hist.get("data") or {}).get("messages") or [])
        for m in msgs:
            raw = str(m.get("raw_message") or m.get("message") or "")[:120]
            print(f"  mid={m.get('message_id')} user={m.get('user_id')} {raw}")
        print(json.dumps(resp, ensure_ascii=False)[:2000])
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    sample = OUT / f"forward_{args.id}_images.json"
    sample.write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {sample}")

    images: list[dict] = []
    walk_images(resp.get("data"), images)
    print(f"TOTAL images={len(images)}")
    if not images:
        return 2

    appids = {}
    for i, data in enumerate(images, 1):
        url = data.get("url") or ""
        p = urlparse(url)
        q = parse_qs(p.query)
        appid = (q.get("appid") or ["?"])[0]
        has_rkey = "rkey" in q and bool(q.get("rkey", [""])[0])
        appids[appid] = appids.get(appid, 0) + 1
        if i <= 3 or i == len(images):
            print(
                f"  img[{i}] appid={appid} rkey={has_rkey} file={data.get('file')} "
                f"size={data.get('file_size')} url={url[:160]}"
            )
    print("appid counts:", appids)

    first = images[0].get("url") or ""
    variants = [("orig", first)]
    flipped = swap_rkey_type(first)
    if flipped:
        variants.append(("flip-appid", flipped))
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for name, url in variants:
            info = await try_get(session, url)
            print(f"GET {name}: {info}")
            info_ua = await try_get(session, url, ua)
            print(f"GET {name}+UA: {info_ua}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
