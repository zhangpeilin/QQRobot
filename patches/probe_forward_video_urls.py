"""Live probe: get_forward_msg and report nested video URL types.

Does not archive. Talks to NapCat OneBot WS as a short-lived extra client.

    venv\\Scripts\\python.exe patches\\probe_forward_video_urls.py
    venv\\Scripts\\python.exe patches\\probe_forward_video_urls.py --id 1860081663 --download
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from urllib.parse import urlparse

import aiohttp

WS_URL = "ws://127.0.0.1:3001/onebot/v11/ws"
DEFAULT_ID = "1860081663"


def walk_videos(obj, acc: list) -> None:
    if isinstance(obj, dict):
        if obj.get("type") == "video":
            acc.append(obj.get("data") or {})
        for v in obj.values():
            walk_videos(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            walk_videos(v, acc)


def classify_url(url: str) -> str:
    if not url:
        return "empty"
    if isinstance(url, str) and url.startswith("http"):
        return "http"
    return "local"


async def call_ws(action: str, params: dict, timeout: float = 90.0) -> dict:
    echo = f"probe-{action}"
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


async def head_or_get(url: str) -> dict:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.head(url, allow_redirects=True) as resp:
                length = resp.headers.get("Content-Length", "")
                ctype = resp.headers.get("Content-Type", "")
                if resp.status < 400 and (length or ctype.startswith("video") or "octet" in ctype):
                    return {
                        "status": resp.status,
                        "length": length,
                        "type": ctype,
                        "method": "HEAD",
                    }
        except Exception as e:
            head_err = f"{type(e).__name__}: {e}"
        else:
            head_err = f"HEAD {resp.status}"
        async with session.get(url, allow_redirects=True) as resp:
            chunk = await resp.content.read(16)
            length = resp.headers.get("Content-Length", "")
            return {
                "status": resp.status,
                "length": length,
                "type": resp.headers.get("Content-Type", ""),
                "magic": chunk[:8].hex(),
                "is_mp4": chunk[4:8] == b"ftyp",
                "method": "GET",
                "head": head_err,
            }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default=DEFAULT_ID)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    print(f"get_forward_msg id={args.id}")
    resp = await call_ws("get_forward_msg", {"message_id": args.id, "id": args.id})
    status = resp.get("status")
    retcode = resp.get("retcode")
    print(f"status={status} retcode={retcode}")
    if status != "ok":
        print(json.dumps(resp, ensure_ascii=False)[:2000])
        return 1

    videos: list[dict] = []
    walk_videos(resp.get("data"), videos)
    stats = {"http": 0, "local": 0, "empty": 0}
    samples = []
    for i, data in enumerate(videos, 1):
        url = data.get("url") or ""
        kind = classify_url(url)
        stats[kind] += 1
        preview = url[:120] if url else ""
        print(f"  video[{i}] {kind} size={data.get('file_size')} url={preview}")
        if kind == "http" and len(samples) < 3:
            samples.append(url)

    print(f"TOTAL videos={len(videos)} http={stats['http']} local={stats['local']} empty={stats['empty']}")

    if args.download and samples:
        print("download-check first http url...")
        info = await head_or_get(samples[0])
        print("  ", info)
        if info.get("status", 500) >= 400:
            return 2
        if info.get("method") == "GET" and not info.get("is_mp4") and not str(info.get("type", "")).startswith("video"):
            host = urlparse(samples[0]).netloc
            print(f"  warning: body not obviously mp4 (host={host})")
    elif args.download and not samples:
        print("no http url to download")
        return 3
    return 0 if stats["http"] == len(videos) and videos else (0 if stats["http"] else 4)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
