"""转发视频 B1 回退：get_msg 解包、get_file 超时、并发不卡死"""

import asyncio
import time
from pathlib import Path

import pytest

from plugins.media_archiver.classifier import MediaItem
from plugins.media_archiver.listener import (
    _forward_content_from_msg,
    _messages_from_forward_resp,
    _try_get_file_download,
    _unwrap_api_data,
)


def test_unwrap_api_data_nonebot_already_unwrapped():
    raw = {"message_id": 1, "message": [{"type": "text", "data": {"text": "a"}}]}
    assert _unwrap_api_data(raw) is raw


def test_unwrap_api_data_wrapped_data():
    inner = {"message_id": 1, "message": []}
    assert _unwrap_api_data({"status": "ok", "data": inner}) is inner


def test_forward_content_from_msg_reads_unwrapped_and_wrapped():
    content = [{"user_id": 1, "message": [{"type": "video", "data": {}}]}]
    unwrapped = {
        "message_id": 1860081663,
        "message": [{"type": "forward", "data": {"id": "abc", "content": content}}],
    }
    wrapped = {"status": "ok", "retcode": 0, "data": unwrapped}

    assert _forward_content_from_msg(unwrapped) is content
    assert _forward_content_from_msg(wrapped) is content
    # 旧逻辑只看 data.message，NoneBot 已解包时永远拿不到
    assert unwrapped.get("data", {}).get("message") in (None, [])


def test_messages_from_forward_resp():
    msgs = [{"message_id": 1}]
    assert _messages_from_forward_resp({"messages": msgs}) == msgs
    assert _messages_from_forward_resp({"data": {"messages": msgs}}) == msgs
    assert _messages_from_forward_resp({}) == []
    assert _messages_from_forward_resp(None) == []


class _HangBot:
    """call_api 永不返回，用来验证 get_file 超时不会卡死。"""

    def __init__(self):
        self.calls = 0

    async def call_api(self, action, **kwargs):
        self.calls += 1
        await asyncio.sleep(30)
        return {}


class _NotFoundBot:
    async def call_api(self, action, **kwargs):
        raise RuntimeError("file not found")


@pytest.mark.asyncio
async def test_get_file_timeout_does_not_hang(tmp_path: Path):
    bot = _HangBot()
    item = MediaItem(
        media_type="video",
        url=r"D:\QQFile\missing\847bceaa5385d27184b796256345d9e5.mp4",
        file_name="847bceaa5385d27184b796256345d9e5.mp4",
    )
    started = time.perf_counter()
    result = await _try_get_file_download(bot, item, item.url, tmp_path, timeout=0.2)
    elapsed = time.perf_counter() - started

    assert result is None
    assert bot.calls == 1
    assert elapsed < 3, f"get_file 超时未生效，耗时 {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_get_file_not_found_does_not_retry_wait(tmp_path: Path):
    bot = _NotFoundBot()
    item = MediaItem(media_type="video", url="", file_name="a.mp4")
    started = time.perf_counter()
    result = await _try_get_file_download(bot, item, "", tmp_path, timeout=40)
    elapsed = time.perf_counter() - started

    assert result is None
    assert elapsed < 1, f"file not found 不应空等，耗时 {elapsed:.1f}s"


class _ConcurrentBot:
    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = asyncio.Lock()

    async def call_api(self, action, **kwargs):
        async with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.15)
            raise RuntimeError("file not found")
        finally:
            async with self._lock:
                self.in_flight -= 1


@pytest.mark.asyncio
async def test_get_file_concurrency_is_capped(tmp_path: Path):
    bot = _ConcurrentBot()

    async def one(i: int):
        it = MediaItem(media_type="video", url="", file_name=f"{i}.mp4")
        await _try_get_file_download(bot, it, "", tmp_path, timeout=0.2)

    await asyncio.gather(*[one(i) for i in range(8)])
    # 信号量上限 3；允许瞬时调度误差，但绝不能 8 个全开
    assert 1 <= bot.max_in_flight <= 3
