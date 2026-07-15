"""媒体消息类型识别与分类"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from nonebot.adapters.onebot.v11 import Message, MessageSegment

logger = logging.getLogger("media_archiver.classifier")


@dataclass
class MediaItem:
    """从消息段中提取出的单个媒体项"""

    media_type: str  # image / video / record / file
    url: str  # 下载地址
    file_name: str = ""  # 原始文件名
    file_id: str = ""  # 文件 busid/id（群文件用）
    extra: dict = field(default_factory=dict)  # 其他字段


# OneBot v11 消息段类型 -> 我们内部类型的映射
_TYPE_MAP = {
    "image": "image",
    "video": "video",
    "record": "record",
    "file": "file",
}


def _extract_url(data: dict) -> str:
    """
    从消息段 data 中提取可用的 URL。

    OneBot v11 实现差异较大：
    - 官方 NTQQ 消息: data 中有 url 字段
    - 部分实现: file 字段直接存放 URL
    - 群文件: 可能有 url 也可能只有 busid
    """
    url = data.get("url", "")
    if url and isinstance(url, str) and url.startswith("http"):
        return url

    # 回退：检查 file 字段是否为 URL
    file_val = data.get("file", "")
    if isinstance(file_val, str) and file_val.startswith("http"):
        return file_val

    return url or ""


def classify_message(message: Message) -> list[MediaItem]:
    """
    解析 OneBot v11 消息，提取所有媒体项。

    支持的消息段类型：
    - image: 图片（含 url, file 字段）
    - video: 短视频（含 url, file 字段）
    - record: 语音（含 url 字段）
    - file: 群文件（含 name, url/busid 字段）
    """
    items: list[MediaItem] = []

    for seg in message:
        if not isinstance(seg, MessageSegment):
            continue

        seg_type = seg.type
        data = seg.data

        if seg_type not in _TYPE_MAP:
            continue

        media_type = _TYPE_MAP[seg_type]

        # 图片/视频/语音消息
        if seg_type in ("image", "video", "record"):
            url = _extract_url(data)
            if not url:
                logger.debug("跳过无 URL 的 %s 消息段: %s", seg_type, data)
                continue
            items.append(
                MediaItem(
                    media_type=media_type,
                    url=url,
                    file_name=data.get("file", ""),
                    file_id=data.get("file_id", ""),
                    extra={"sub_type": data.get("sub_type", "")},
                )
            )

        # 群文件
        elif seg_type == "file":
            file_url = _extract_url(data)
            items.append(
                MediaItem(
                    media_type="file",
                    url=file_url,
                    file_name=data.get("name", data.get("file", "unknown_file")),
                    file_id=data.get("id", ""),
                    extra={
                        "busid": data.get("busid", ""),
                        "size": data.get("size", 0),
                    },
                )
            )

    return items


def get_media_type_dir(media_type: str) -> str:
    """获取媒体类型对应的目录名"""
    return {
        "image": "images",
        "video": "videos",
        "record": "audios",
        "file": "files",
    }.get(media_type, "others")


def guess_extension(url: str, file_name: str = "", media_type: str = "") -> str:
    """从 URL 或文件名中推测文件扩展名"""
    import os
    from urllib.parse import urlparse

    # 优先从文件名获取
    if file_name and "." in file_name:
        ext = os.path.splitext(file_name)[1].lower()
        if ext and len(ext) <= 6:  # 合理的扩展名长度
            return ext

    # 从 URL 路径获取
    if url:
        parsed = urlparse(url)
        path = parsed.path
        last_segment = path.split("/")[-1]
        if "." in last_segment:
            ext = os.path.splitext(last_segment)[1].lower()
            if ext and len(ext) <= 6:
                return ext

    # 根据类型给默认扩展名
    return {
        "image": ".jpg",
        "video": ".mp4",
        "record": ".silk",
        "file": "",
    }.get(media_type, "")
