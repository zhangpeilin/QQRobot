"""消息监听器 - 核心业务入口"""


import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
)

from .archiver import Archiver
from .classifier import MediaItem, classify_message
from .config import get_config
from .database import close_database, get_database
from .downloader import AsyncDownloader, DownloadError

if TYPE_CHECKING:
    from .database import MediaDatabase

# NapCat 本地数据根目录（从 get_image 返回的路径反推）
# 格式: D:\QQFile\Tencent Files\Tencent Files\{QQ}\nt_qq\nt_data
_NAP_CAT_DATA = Path(
    "D:\\QQFile\\Tencent Files\\Tencent Files\\2065277052\\nt_qq\\nt_data"
)

logger = logging.getLogger("media_archiver.listener")

# ---- 全局组件（在 startup 时初始化）----
_downloader: AsyncDownloader | None = None
_archiver: Archiver | None = None
_db: MediaDatabase | None = None
_background_tasks: set[asyncio.Task] = set()


async def _startup() -> None:
    """在 NoneBot2 启动时初始化各组件"""
    global _downloader, _archiver, _db

    cfg = get_config()
    archive_root = cfg.get_archive_path()

    _downloader = AsyncDownloader(cfg)
    _archiver = Archiver(archive_root)
    _db = await get_database(archive_root)

    # 注册 Bot 连接钩子（必须在初始化之后，确保 _startup 已完成）
    driver.on_bot_connect(_on_bot_connect)

    logger.warning(
        "媒体归档系统已启动 | 归档目录: %s | 监听群: %s",
        archive_root,
        cfg.watch_groups or "全部",
    )


async def _shutdown() -> None:
    """在 NoneBot2 关闭时清理资源"""
    global _downloader, _db

    # 等待所有后台下载任务完成
    if _background_tasks:
        logger.info("等待 %d 个后台任务完成...", len(_background_tasks))
        await asyncio.gather(*_background_tasks, return_exceptions=True)

    if _downloader:
        await _downloader.close()
    if _db:
        await close_database()

    logger.info("媒体归档系统已关闭")


# 注册启动/关闭钩子
driver = get_driver()
driver.on_startup(_startup)
driver.on_shutdown(_shutdown)

# ---- 消息处理器 ----

# 使用 on_message 监听所有消息，block=False 不阻止其他插件处理
msg_handler = on_message(priority=50, block=False)


@msg_handler.handle()
async def handle_message(bot: Bot, event: Event) -> None:
    """处理每一条消息"""
    # 只处理群消息
    if not isinstance(event, GroupMessageEvent):
        return

    cfg = get_config()
    group_id = event.group_id
    user_id = event.user_id
    message_id = event.message_id

    # 检查是否在监听列表中
    if not cfg.is_group_watched(group_id):
        return

    # 提取媒体项
    media_items = classify_message(event.message)
    if not media_items:
        return

    logger.warning(
        "检测到 %d 个媒体项 | 群: %d | 用户: %d | 消息ID: %d",
        len(media_items), group_id, user_id, message_id,
    )

    # 为每个媒体项创建后台下载任务
    for idx, item in enumerate(media_items):
        # 检查媒体类型是否启用
        if not getattr(cfg.media_types, item.media_type, False):
            logger.debug("跳过已禁用的媒体类型: %s", item.media_type)
            continue

        task = asyncio.create_task(
            _process_media_item(
                bot=bot,
                item=item,
                group_id=group_id,
                user_id=user_id,
                message_id=message_id,
                seq=idx,
            ),
            name=f"download-{message_id}-{idx}",
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


async def _finalize_media_item(
    result: DownloadResult,
    item: MediaItem,
    group_id: int,
    user_id: int,
    message_id: int,
    tag: str,
) -> bool:
    """
    下载成功后的归档流程：MD5 去重 -> 归档 -> 记录元数据。

    Returns:
        True 表示成功归档，False 表示被去重跳过。
    """
    cfg = get_config()

    # 3. MD5 去重（下载后计算哈希）
    if cfg.dedup.enabled and cfg.dedup.strategy == "md5":
        from .dedup import compute_file_md5

        md5 = await compute_file_md5(result.temp_path)
        if await _db.exists_by_md5(md5):
            logger.info("%s 文件已存在 (MD5 重复), 删除临时文件", tag)
            result.temp_path.unlink(missing_ok=True)
            return False

    # 4. 归档文件
    storage_path, file_md5 = await _archiver.archive_file(
        temp_path=result.temp_path,
        media_type=item.media_type,
        group_id=group_id,
        user_id=user_id,
        url=item.url,
        file_name=item.file_name,
    )

    # 5. 记录元数据
    await _db.insert_record(
        message_id=message_id,
        group_id=group_id,
        user_id=user_id,
        media_type=item.media_type,
        storage_path=str(storage_path),
        file_name=item.file_name,
        file_md5=file_md5,
        file_size=result.file_size,
        source_url=item.url,
    )

    logger.warning(
        "%s 保存成功 | %s | %d bytes | %s",
        tag, storage_path.name, result.file_size, file_md5[:8],
    )
    return True


async def _process_media_item(
    bot: Bot,
    item: MediaItem,
    group_id: int,
    user_id: int,
    message_id: int,
    seq: int,
) -> bool:
    """
    处理单个媒体项：去重检查 -> 下载 -> 归档 -> 记录元数据。

    当直连下载失败（如历史消息 URL 过期）时，会尝试通过 NapCat API
    获取新的下载地址再试一次。如果仍然失败，对于视频/语音等类型还会
    尝试从 NapCat 本地缓存目录直接复制。

    Returns:
        True 表示成功归档，False 表示失败或被去重跳过。
    """
    assert _downloader is not None
    assert _archiver is not None
    assert _db is not None

    cfg = get_config()
    tag = f"[{item.media_type}][群{group_id}][消息{message_id}]"

    try:
        # 1. 消息级去重：同一消息的同一类型是否已处理
        if cfg.dedup.enabled and cfg.dedup.strategy == "message_id":
            if await _db.exists_by_message(message_id, item.media_type):
                logger.info("%s 消息已处理，跳过", tag)
                return False

        # 2. 下载文件到临时目录
        temp_dir = cfg.get_archive_path() / ".tmp"

        # 历史消息的 CDN URL 大概率已过期，优先通过 API 获取新鲜地址
        # 避免直连过期 URL 挂起 120 秒超时
        download_url = await _get_fresh_url(bot, item)
        if not download_url:
            download_url = item.url  # 无新鲜 URL 时用原 URL 碰运气

        logger.warning("  [下载] URL=%s", (download_url or "空")[:120])

        result: DownloadResult | None = None
        try:
            result = await _downloader.download(download_url, temp_dir)
        except DownloadError:
            # CDN 下载失败 -> 尝试从 NapCat 本地缓存复制
            if item.media_type in ("video", "record", "file"):
                local_path = await _copy_local_file(item, temp_dir)
                if local_path:
                    logger.warning(
                        "%s CDN 下载失败，本地缓存复制成功，继续归档", tag,
                    )
                    result = DownloadResult(
                        temp_path=local_path,
                        file_size=local_path.stat().st_size,
                    )
            if result is None:
                logger.error("%s 下载失败（直连+API+本地均无效）: %s", tag, download_url[:80])
                return False

        # 下载成功 -> 继续后续步骤
        return await _finalize_media_item(
            result=result,
            item=item,
            group_id=group_id,
            user_id=user_id,
            message_id=message_id,
            tag=tag,
        )

    except Exception as e:
        logger.exception("%s 处理异常: %s", tag, e)
    return False


# ============================================================
# 启动时历史消息扫描
# ============================================================

_scan_started: bool = False
"""防止重复扫描的标志"""


async def _scan_all_groups(bot: Bot) -> None:
    """扫描所有监听群的历史消息。"""
    global _scan_started

    if _scan_started:
        return
    _scan_started = True

    cfg = get_config()
    scan_cfg = cfg.startup_scan

    logger.warning(
        "开始扫描历史消息 | 时间范围: %s | 每群上限: %d",
        "全部" if scan_cfg.time_range_hours == 0 else f"{scan_cfg.time_range_hours}小时",
        scan_cfg.max_per_group,
    )

    try:
        group_list = await bot.get_group_list()
    except Exception as e:
        logger.error("获取群列表失败: %s", e)
        _scan_started = False
        return

    watched = [
        g for g in group_list
        if cfg.is_group_watched(g.get("group_id") or g.get("group_id", 0))
    ]

    if not watched:
        logger.info("没有需要扫描的群（watch_groups 列表为空，或 Bot 未加入任何群）")
        _scan_started = False
        return

    logger.info("共 %d 个群需要扫描", len(watched))

    for group_info in watched:
        group_id = group_info.get("group_id") or group_info.get("group_id", 0)
        await _scan_group_history(bot, group_id, scan_cfg)

    logger.warning("历史消息扫描完成")


async def _scan_group_history(
    bot: Bot, group_id: int, scan_cfg: "StartupScanConfig",
) -> None:
    """扫描单个群的历史消息并提取媒体文件。"""
    cfg = get_config()
    cutoff_time: float | None = None
    if scan_cfg.time_range_hours > 0:
        cutoff_time = time.time() - scan_cfg.time_range_hours * 3600

    fetched = 0
    cursor_msg_seq: int | None = None  # None = 从最新开始
    seen_message_ids: set[int] = set()  # 翻页去重检测
    scanned = 0
    archived = 0

    while fetched < scan_cfg.max_per_group:
        count = min(20, scan_cfg.max_per_group - fetched)
        try:
            params: dict = {"group_id": group_id, "count": count}
            if cursor_msg_seq is not None:
                params["message_seq"] = cursor_msg_seq
            result = await bot.call_api("get_group_msg_history", **params)
        except Exception as e:
            logger.error("拉取群 %d 历史消息失败: %s", group_id, e)
            break

        # 解析返回的消息列表
        messages = []
        if isinstance(result, dict):
            messages = result.get("messages", result.get("data", {}).get("messages", []))
        elif isinstance(result, list):
            messages = result

        if not messages:
            logger.info("群 %d 历史消息已拉取完毕（共 %d 条）", group_id, fetched)
            break

        # 翻页去重检测（有些实现不响应 message_id 参数）
        batch_ids = {msg.get("message_id", 0) for msg in messages}
        if batch_ids.issubset(seen_message_ids):
            logger.info(
                "群 %d 翻页无新消息（已拉取 %d 条，均为重复），停止扫描",
                group_id, fetched,
            )
            break
        seen_message_ids.update(batch_ids)

        for msg in messages:
            msg_time = msg.get("time", 0)

            # 检查时间范围
            if cutoff_time and msg_time < cutoff_time:
                logger.info(
                    "群 %d 已超出时间范围（%d 条后停止）", group_id, fetched
                )
                return

            msg_id = msg.get("message_id", 0)
            user_id = msg.get("user_id", 0) or msg.get("sender", {}).get("user_id", 0)
            raw_segments = msg.get("message", [])

            if not raw_segments:
                continue

            # 历史消息的 raw_segments 是 list[dict], 需转成 MessageSegment
            try:
                if raw_segments and isinstance(raw_segments, list) and isinstance(raw_segments[0], dict):
                    from nonebot.adapters.onebot.v11 import MessageSegment
                    segs = [MessageSegment(type=s["type"], data=s.get("data", {})) for s in raw_segments if isinstance(s, dict)]
                    onebot_msg = Message(segs)
                else:
                    onebot_msg = Message(raw_segments)
            except Exception:
                continue

            # 提取媒体项
            media_items = classify_message(onebot_msg)
            if not media_items:
                continue

            scanned += 1
            logger.warning(
                "  [扫描] msg_id=%s 找到 %d 个媒体项: %s",
                msg_id, len(media_items),
                [(m.media_type, m.url[:60] if m.url else "无URL", m.file_name[:30] if m.file_name else "") for m in media_items],
            )

            for idx, item in enumerate(media_items):
                if not getattr(cfg.media_types, item.media_type, False):
                    continue

                ok = await _process_media_item(
                    bot=bot,
                    item=item,
                    group_id=group_id,
                    user_id=user_id,
                    message_id=msg_id,
                    seq=idx,
                )
                if ok:
                    archived += 1

        # 翻页：用 batch 中最小的 message_seq 作为下一页游标（NapCat 返回此 seq 之前的消息）
        msg_seqs_in_batch = [
            msg.get("message_seq", 0) for msg in messages if msg.get("message_seq")
        ]
        if msg_seqs_in_batch:
            cursor_msg_seq = min(msg_seqs_in_batch)
        else:
            cursor_msg_seq = None
        fetched += len(messages)

        logger.debug(
            "群 %d 已拉取 %d 条消息 | 含媒体: %d | 已归档: %d",
            group_id, fetched, scanned, archived,
        )

    if fetched >= scan_cfg.max_per_group:
        logger.info(
            "群 %d 已达拉取上限 %d 条（含媒体 %d 条，归档 %d 个）",
            group_id, scan_cfg.max_per_group, scanned, archived,
        )
    else:
        logger.warning(
            "群 %d 扫描完成 | 拉取: %d 条 | 含媒体: %d | 归档: %d",
            group_id, fetched, scanned, archived,
        )


async def _get_fresh_url(bot: Bot, item: MediaItem) -> str | None:
    """
    通过 NapCat API 获取媒体文件的新的下载地址。

    支持类型映射：
    - image  -> get_image （有效，返回新鲜 CDN URL）
    - record -> get_record（需 out_format="mp3", 返回转码后的 URL）
    - video  -> 不支持，返回 None（使用原始 CDN URL 或本地复制）
    - file   -> 不支持，返回 None（使用原始 URL）
    """
    # 仅 image / record 有专用刷新 API
    api_by_type = {
        "image": "get_image",
        "record": "get_record",
    }

    api_name = api_by_type.get(item.media_type)
    if api_name is None:
        return None

    candidates = []
    if item.file_id:
        candidates.append(("file_id", item.file_id))
    if item.file_name:
        candidates.append(("file_name", item.file_name))
    if item.url:
        candidates.append(("url", item.url))
    if not candidates:
        logger.warning("刷新 URL 失败: 无任何可用参数 (url=%s)", item.url[:60] if item.url else "空")
        return None

    for param_name, param_value in candidates:
        for kw in ("file", "file_id"):
            try:
                kwargs = {kw: param_value}
                if api_name == "get_record":
                    kwargs["out_format"] = "mp3"
                result = await bot.call_api(api_name, **kwargs)
                logger.warning(
                    "[URL刷新] %s(%s=%s...) 返回: %s",
                    api_name, kw, str(param_value)[:60], str(result)[:300],
                )
                if isinstance(result, dict):
                    url = result.get("url", "")
                    if url and isinstance(url, str) and url.startswith("http"):
                        return url
                elif isinstance(result, str) and result.startswith("http"):
                    return result
            except Exception as e:
                logger.warning(
                    "[URL刷新] %s 失败 (尝试 %s=%s...): %s: %s",
                    item.media_type, kw, str(param_value)[:60], type(e).__name__, e,
                )

    return None


async def _copy_local_file(item: MediaItem, temp_dir: Path) -> Path | None:
    """
    尝试从 NapCat 本地数据目录复制媒体文件。

    当 CDN URL 过期且 API 刷新不可用时，NapCat 实际已在本地缓存了
    媒体文件（通过 get_image 结果可知），可以直接复制而非 HTTP 下载。

    Returns:
        复制到的临时文件路径，未找到返回 None。
    """
    if not item.file_name:
        return None

    # 媒体类型 -> 数据子目录映射
    type_dir = {
        "image": "Pic",
        "video": "Video",
        "record": "Record",
    }
    sub_dir = type_dir.get(item.media_type)
    if sub_dir is None:
        return None

    # 文件名字符串清理
    raw_name = item.file_name.strip()

    # 要尝试的文件名变体（原始 + 小写）
    candidates = [raw_name, raw_name.lower()]

    search_root = _NAP_CAT_DATA / sub_dir
    if not search_root.is_dir():
        logger.debug("NapCat 数据目录不存在: %s", search_root)
        return None

    for variant in candidates:
        found = list(search_root.rglob(variant))
        if found:
            src = found[0]
            dst = temp_dir / src.name
            logger.warning("[本地复制] 找到文件: %s -> %s", src, dst)
            try:
                async with aiofiles.open(src, "rb") as f_src:
                    content = await f_src.read()
                async with aiofiles.open(dst, "wb") as f_dst:
                    await f_dst.write(content)
                return dst
            except Exception as e:
                logger.warning("[本地复制] 复制失败: %s", e)
                return None

    logger.debug("[本地复制] 未找到匹配文件: %s (in %s)", raw_name, search_root)
    return None


async def _on_bot_connect(bot: Bot) -> None:
    """Bot 连接就绪后启动历史消息扫描。"""
    cfg = get_config()
    if not cfg.startup_scan.enabled:
        return

    # 延迟一小段时间等连接稳定
    await asyncio.sleep(2)
    await _scan_all_groups(bot)
