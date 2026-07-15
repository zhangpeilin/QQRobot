"""异步下载引擎"""



import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import aiofiles
import aiohttp

from .config import AppConfig

logger = logging.getLogger("media_archiver.downloader")


class DownloadError(Exception):
    """下载异常"""


class DownloadResult:
    """下载结果"""

    def __init__(self, temp_path: Path, file_size: int):
        self.temp_path = temp_path
        self.file_size = file_size


class AsyncDownloader:
    """
    异步文件下载器。

    特性：
    - 并发控制（Semaphore）
    - 指数退避重试
    - 超时保护
    - 先下载到临时文件，成功后由 archiver 移动到目标路径
    """

    def __init__(self, config: AppConfig):
        self._semaphore = asyncio.Semaphore(config.download.max_concurrent)
        self._timeout = aiohttp.ClientTimeout(total=config.download.timeout)
        self._max_retries = config.download.max_retries
        self._retry_base = config.download.retry_base_delay
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def download(self, url: str, temp_dir: Path) -> DownloadResult:
        """
        下载文件到临时目录，返回 DownloadResult。

        Args:
            url: 文件下载地址
            temp_dir: 临时文件存放目录

        Returns:
            DownloadResult 包含临时文件路径和文件大小

        Raises:
            DownloadError: 所有重试均失败时抛出
        """
        temp_dir.mkdir(parents=True, exist_ok=True)

        async with self._semaphore:
            last_error: Optional[Exception] = None

            for attempt in range(self._max_retries + 1):
                try:
                    return await self._do_download(url, temp_dir)
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                    last_error = e
                    if attempt < self._max_retries:
                        delay = self._retry_base * (2 ** attempt)
                        logger.warning(
                            "下载失败 (第%d次), %s秒后重试: %s - %s",
                            attempt + 1, delay, url[:80], e,
                        )
                        await asyncio.sleep(delay)

            raise DownloadError(
                f"下载失败，已重试{self._max_retries}次: {url[:80]} - {last_error}"
            )

    async def _do_download(self, url: str, temp_dir: Path) -> DownloadResult:
        """执行单次下载"""
        session = await self._ensure_session()

        # 生成临时文件名
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=temp_dir, suffix=".download")
        os.close(tmp_fd)  # 关闭 fd，否则 Windows 会锁定文件
        tmp_path = Path(tmp_path_str)

        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise DownloadError(f"HTTP {resp.status}: {url[:80]}")

                file_size = 0
                async with aiofiles.open(tmp_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        await f.write(chunk)
                        file_size += len(chunk)

            logger.info("下载完成: %d bytes <- %s", file_size, url[:80])
            return DownloadResult(temp_path=tmp_path, file_size=file_size)

        except Exception:
            # 下载失败时清理临时文件
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
