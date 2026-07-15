"""文件去重模块"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import aiofiles

logger = logging.getLogger("media_archiver.dedup")

BUF_SIZE = 65536  # 64KB chunks for streaming hash


async def compute_file_md5(file_path: Path) -> str:
    """异步计算文件 MD5（流式读取，不占内存）"""
    md5 = hashlib.md5()
    async with aiofiles.open(file_path, "rb") as f:
        while True:
            chunk = await f.read(BUF_SIZE)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


def compute_bytes_md5(data: bytes) -> str:
    """计算字节串的 MD5"""
    return hashlib.md5(data).hexdigest()
