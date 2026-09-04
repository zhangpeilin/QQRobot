"""Archive images from a live forward using current OneBot URLs.

    venv\\Scripts\\python.exe patches\\archive_forward_images.py --id 11912981
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import nonebot

nonebot.init()

from plugins.media_archiver.archiver import Archiver
from plugins.media_archiver.classifier import MediaItem
from plugins.media_archiver.config import get_config
from plugins.media_archiver.database import get_database
from plugins.media_archiver.downloader import AsyncDownloader, DownloadError
from plugins.media_archiver.listener import _FORWARD_ID_OFFSET, _gchatpic_url_from_filename

WS_URL = "ws://127.0.0.1:3001/onebot/v11/ws"
GROUP_ID = 663964060


async def call_ws(action: str, params: dict, timeout: float = 90.0) -> dict:
    echo = f"archive-img-{action}"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL, heartbeat=15) as ws:
            await ws.send_json({"action": action, "params": params, "echo": echo})
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError(action)
                msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("echo") == echo:
                    return data


def walk_images(obj, acc: list, user_id: int = 0, message_id: int = 0) -> None:
    if isinstance(obj, dict):
        uid = obj.get("user_id") or (obj.get("sender") or {}).get("user_id") or user_id
        mid = obj.get("message_id") or message_id
        if obj.get("type") == "image":
            data = obj.get("data") or {}
            acc.append((int(uid or 0), int(mid or 0), data))
        for v in obj.values():
            walk_images(v, acc, int(uid or 0), int(mid or 0))
    elif isinstance(obj, list):
        for v in obj:
            walk_images(v, acc, user_id, message_id)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default="11912981")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    resp = await call_ws("get_forward_msg", {"message_id": args.id, "id": args.id})
    if resp.get("status") != "ok":
        print(json.dumps(resp, ensure_ascii=False)[:1500])
        return 1

    images: list[tuple[int, int, dict]] = []
    walk_images(resp.get("data"), images)
    if args.limit:
        images = images[: args.limit]
    print(f"images={len(images)}")
    if not images:
        return 2

    cfg = get_config()
    archive_root = cfg.get_archive_path()
    downloader = AsyncDownloader(cfg)
    archiver = Archiver(archive_root)
    db = await get_database(archive_root)
    temp_dir = archive_root / ".tmp"
    ok = 0
    fail = 0
    skip = 0
    try:
        for uid, mid, data in images:
            url = data.get("url") or ""
            name = data.get("file") or ""
            item = MediaItem(media_type="image", url=url, file_name=name)
            message_id = (mid or int(args.id)) + _FORWARD_ID_OFFSET
            user_id = uid or 1094950020
            try:
                result = await downloader.download(url, temp_dir)
            except DownloadError as e:
                md5_url = _gchatpic_url_from_filename(name)
                if not md5_url:
                    print(f"FAIL no-md5 {name} {e}")
                    fail += 1
                    continue
                try:
                    result = await downloader.download(md5_url, temp_dir)
                    print(f"MD5-fallback {name}")
                except DownloadError as e2:
                    print(f"FAIL {name} {e} / {e2}")
                    fail += 1
                    continue
            from plugins.media_archiver.dedup import compute_file_md5

            file_md5 = await compute_file_md5(result.temp_path)
            if await db.exists_by_md5(file_md5):
                result.temp_path.unlink(missing_ok=True)
                skip += 1
                continue
            storage_path, file_md5 = await archiver.archive_file(
                temp_path=result.temp_path,
                media_type="image",
                group_id=GROUP_ID,
                user_id=user_id,
                url=url,
                file_name=name,
            )
            await db.insert_record(
                message_id=message_id,
                group_id=GROUP_ID,
                user_id=user_id,
                media_type="image",
                storage_path=str(storage_path),
                file_name=name,
                file_md5=file_md5,
                file_size=result.file_size,
                source_url=url,
            )
            ok += 1
            print(f"OK {storage_path.name} {result.file_size}")
    finally:
        await downloader.close()
        await db.close()
    print(f"done ok={ok} skip={skip} fail={fail}")
    return 0 if fail == 0 else 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
