"""消息监听器 - 核心业务入口"""


import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)

from .archiver import Archiver
from .classifier import MediaItem, classify_message
from .config import get_config
from .database import close_database, get_database
from .downloader import AsyncDownloader, DownloadError, DownloadResult

if TYPE_CHECKING:
    from .database import MediaDatabase

# 私聊消息的虚拟 group_id（数据库与归档目录用于区分群聊/私聊）
_PRIVATE_GROUP_ID = 0

# 转发拆包消息的 message_id 偏移量：转发拆包出的内层消息 ID 可能与
# 真实消息 ID 冲突（同一原始消息既被普通归档又被转发拆包），导致
# UNIQUE(message_id, media_type) 冲突使 INSERT 被忽略、文件成为孤儿。
# 拆包消息统一加偏移，避免与真实消息 ID 空间重叠。
_FORWARD_ID_OFFSET = 10**17

# 本机 QQ 文件存储根目录（所有账号的 nt_data 均在其下，跨账号搜索用）
# 格式: D:\QQFile\Tencent Files\Tencent Files\{QQ}\nt_qq\nt_data
_QQ_FILE_ROOT = Path(
    "D:\\QQFile\\Tencent Files\\Tencent Files"
)

logger = logging.getLogger("media_archiver.listener")

# ---- 全局组件（在 startup 时初始化）----
_downloader: AsyncDownloader | None = None
_archiver: Archiver | None = None
_db: MediaDatabase | None = None
_background_tasks: set[asyncio.Task] = set()

# 扫描并发信号量：限制同时进行的媒体处理数（get_file 触发 NapCat
# downloadMedia 可能长时间阻塞，串行等待会让扫描极慢）
_scan_sem = asyncio.Semaphore(3)


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
    """处理每一条消息（群聊 + 私聊白名单）"""
    cfg = get_config()

    # 群消息：按 watch_groups 过滤
    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
        user_id = event.user_id
        if not cfg.is_group_watched(group_id):
            return
        scope_label = f"群{group_id}"
    # 私聊消息：按 private_watch_users 白名单过滤
    elif isinstance(event, PrivateMessageEvent):
        user_id = event.user_id
        if not cfg.is_private_user_watched(user_id):
            return
        group_id = _PRIVATE_GROUP_ID
        scope_label = f"私聊[{user_id}]"
    else:
        return

    message_id = event.message_id

    # 检查是否有合并转发（forward）消息段
    forward_segments = [seg for seg in event.message if seg.type == "forward"]
    if forward_segments:
        logger.warning(
            "检测到合并转发消息 | %s | 用户: %d | 消息ID: %d | 共 %d 段",
            scope_label, user_id, message_id, len(forward_segments),
        )
        for seg in forward_segments:
            forward_id = seg.data.get("id", "")
            if not forward_id:
                continue
            task = asyncio.create_task(
                _process_forward_message(
                    bot=bot, forward_id=forward_id,
                    group_id=group_id, user_id=user_id,
                    source_message_id=message_id,
                ),
                name=f"forward-{message_id}",
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        return

    # 提取媒体项
    media_items = classify_message(event.message)
    if not media_items:
        return

    logger.warning(
        "检测到 %d 个媒体项 | %s | 用户: %d | 消息ID: %d",
        len(media_items), scope_label, user_id, message_id,
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

    # 0. 消息级去重：同一消息的同一类型已归档过则跳过（防止 UNIQUE 冲突
    #    导致文件已保存但记录未插入的孤儿文件，避免重复归档）
    if await _db.exists_by_message(message_id, item.media_type):
        logger.info("%s 消息已归档过（message_id 级），删除临时文件", tag)
        result.temp_path.unlink(missing_ok=True)
        return False

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
    row_id = await _db.insert_record(
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

    # 5b. INSERT 被唯一约束忽略（rowid=0）说明记录已存在：文件已 move，
    #     删除刚归档的文件避免成为无记录孤儿（下次扫描无法去重）
    if not row_id:
        logger.warning(
            "%s 记录插入被忽略（消息已存在记录），删除已归档文件", tag,
        )
        storage_path.unlink(missing_ok=True)
        return False

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
    skip_url_refresh: bool = False,
) -> bool:
    """
    处理单个媒体项：去重检查 -> 下载 -> 归档 -> 记录元数据。

    当直连下载失败（如历史消息 URL 过期）时，会尝试通过 NapCat API
    获取新的下载地址再试一次。如果仍然失败，对于视频/语音等类型还会
    尝试从 NapCat 本地缓存目录直接复制。

    Args:
        skip_url_refresh: 为 True 时跳过 _get_fresh_url，直接使用 item.url。
                          用于合并转发消息，避免 get_image API 超时。

    Returns:
        True 表示成功归档，False 表示失败或被去重跳过。
    """
    assert _downloader is not None
    assert _archiver is not None
    assert _db is not None

    cfg = get_config()
    scope = f"私聊[{user_id}]" if group_id == _PRIVATE_GROUP_ID else f"群{group_id}"
    tag = f"[{item.media_type}][{scope}][消息{message_id}]"

    try:
        # 1. 消息级去重：同一消息的同一类型是否已处理
        if cfg.dedup.enabled and cfg.dedup.strategy == "message_id":
            if await _db.exists_by_message(message_id, item.media_type):
                logger.info("%s 消息已处理，跳过", tag)
                return False

        # 2. 下载文件到临时目录
        temp_dir = cfg.get_archive_path() / ".tmp"

        # 合并转发消息的图片无法通过 get_image 获取（NapCat 找不到本地缓存），
        # 用 skip_url_refresh 跳过 API 调用，直接使用 item.url 直连 CDN。
        if skip_url_refresh:
            download_url = item.url
        else:
            # 历史消息的 CDN URL 大概率已过期，优先通过 API 获取新鲜地址
            # 避免直连过期 URL 挂起 120 秒超时
            download_url = await _get_fresh_url(bot, item)
            if not download_url:
                download_url = item.url  # 无新鲜 URL 时用原 URL 碰运气

        logger.warning("  [下载] URL=%s", (download_url or "空")[:120])

        result: DownloadResult | None = None
        # NapCat 历史消息/转发消息中的视频 URL 常为本地文件路径，
        # 检测到本地路径时直接复制，不走 HTTP 下载
        if _is_local_path(download_url):
            local_path = await _copy_local_path(download_url, temp_dir)
            if local_path is None:
                # 目标账号缓存缺失 -> 跨账号按相对路径/文件名搜索
                local_path = await _copy_local_file(
                    item, temp_dir, url=download_url,
                )
            if local_path is None:
                # 本地缓存全无 -> get_file 按文件名命中 NapCat 资源引用
                # 缓存（直接发送的视频 24h 内有效），换取 CDN URL 下载。
                # 转发拆包视频的 downloadMedia 必然超时（消息不在本地库），
                # 跳过避免每个白等 1-2 分钟拖慢扫描
                if not skip_url_refresh:
                    local_path = await _try_get_file_download(
                        bot, item, download_url, temp_dir,
                    )
            if local_path:
                logger.warning("%s 本地路径文件，直接复制成功，继续归档", tag)
                result = DownloadResult(
                    temp_path=local_path,
                    file_size=local_path.stat().st_size,
                )
            else:
                logger.error("%s 本地文件不存在或复制失败: %s", tag, download_url[:80])
                return False
        else:
            try:
                result = await _downloader.download(download_url, temp_dir)
            except DownloadError:
                # CDN 下载失败 -> 尝试从 NapCat 本地缓存复制
                # （图片收到时自动缓存 Pic 目录；get_file 可按文件名
                #   命中缓存或从服务器搜索下载）
                if item.media_type in ("video", "record", "file", "image"):
                    local_path = await _copy_local_file(item, temp_dir)
                    if local_path is None and not skip_url_refresh:
                        # 本地缓存全无 -> get_file 命中资源引用缓存换 CDN URL
                        # （转发拆包视频跳过：downloadMedia 必然超时）
                        local_path = await _try_get_file_download(
                            bot, item, item.url or download_url, temp_dir,
                        )
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
    else:
        logger.info("共 %d 个群需要扫描", len(watched))

        for group_info in watched:
            group_id = group_info.get("group_id") or group_info.get("group_id", 0)
            await _scan_group_history(bot, group_id, scan_cfg)

    # 私聊历史扫描（白名单用户）
    if scan_cfg.private_enabled and cfg.private_watch_users:
        logger.warning(
            "开始扫描私聊历史 | 白名单: %s | 时间范围: %s | 每用户上限: %d",
            cfg.private_watch_users,
            "全部" if scan_cfg.time_range_hours == 0 else f"{scan_cfg.time_range_hours}小时",
            scan_cfg.max_per_group,
        )
        for uid in cfg.private_watch_users:
            await _scan_private_history(bot, uid, scan_cfg)

    logger.warning("历史消息扫描完成")


async def _process_history_message(
    bot: Bot, msg: dict, group_id: int,
    scan_cfg: "StartupScanConfig", cutoff_time: float | None,
) -> int:
    """
    处理单条历史消息（含合并转发/媒体项）。

    Returns:
        -1 表示超出时间范围（应停止扫描）；否则返回成功归档的媒体项数。
    """
    cfg = get_config()
    msg_time = msg.get("time", 0)

    # 检查时间范围
    if cutoff_time and msg_time < cutoff_time:
        return -1

    msg_id = msg.get("message_id", 0)
    user_id = msg.get("user_id", 0) or msg.get("sender", {}).get("user_id", 0)
    raw_segments = msg.get("message", [])

    if not raw_segments:
        return 0

    # 历史消息的 raw_segments 是 list[dict], 需转成 MessageSegment
    try:
        if raw_segments and isinstance(raw_segments, list) and isinstance(raw_segments[0], dict):
            from nonebot.adapters.onebot.v11 import MessageSegment
            segs = [MessageSegment(type=s["type"], data=s.get("data", {})) for s in raw_segments if isinstance(s, dict)]
            onebot_msg = Message(segs)
        else:
            onebot_msg = Message(raw_segments)
    except Exception:
        return 0

    # 处理合并转发消息
    forward_ids = [
        seg.data.get("id", "") for seg in onebot_msg
        if seg.type == "forward" and seg.data.get("id")
    ]
    if forward_ids:
        for fid in forward_ids:
            await _process_forward_message(
                bot=bot, forward_id=fid,
                group_id=group_id, user_id=user_id,
                source_message_id=msg_id,
            )
        return 0

    # 提取媒体项
    media_items = classify_message(onebot_msg)
    if not media_items:
        return 0

    logger.warning(
        "  [扫描] msg_id=%s 找到 %d 个媒体项",
        msg_id, len(media_items),
    )

    archived = 0
    for idx, item in enumerate(media_items):
        if not getattr(cfg.media_types, item.media_type, False):
            continue

        # 并发控制：限制同时进行的媒体处理数
        async with _scan_sem:
            ok = await _process_media_item(
                bot=bot, item=item, group_id=group_id,
                user_id=user_id, message_id=msg_id, seq=idx,
            )
        if ok:
            archived += 1

    return archived


async def _scan_group_history(
    bot: Bot, group_id: int, scan_cfg: "StartupScanConfig",
) -> None:
    """扫描单个群的历史消息并提取媒体文件（批次内并发处理）。"""
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

        # 批次内并发处理所有消息
        tasks = [
            asyncio.create_task(
                _process_history_message(
                    bot=bot, msg=msg, group_id=group_id,
                    scan_cfg=scan_cfg, cutoff_time=cutoff_time,
                ),
                name=f"scan-{msg.get('message_id', 0)}",
            )
            for msg in messages
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        stop_scan = False
        for r in results:
            if isinstance(r, Exception):
                logger.warning("历史消息处理异常: %s", r)
                continue
            if r == -1:
                logger.info(
                    "群 %d 已超出时间范围（%d 条后停止）", group_id, fetched
                )
                stop_scan = True
                break
            if r > 0:
                scanned += 1
                archived += r
        if stop_scan:
            return

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


async def _scan_private_history(
    bot: Bot, user_id: int, scan_cfg: "StartupScanConfig",
) -> None:
    """扫描单个私聊对象的历史消息并提取媒体文件。"""
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
            params: dict = {"user_id": user_id, "count": count}
            if cursor_msg_seq is not None:
                params["message_seq"] = cursor_msg_seq
            result = await bot.call_api("get_friend_msg_history", **params)
        except Exception as e:
            logger.error("拉取私聊 %d 历史消息失败: %s", user_id, e)
            break

        # 解析返回的消息列表
        messages = []
        if isinstance(result, dict):
            messages = result.get("messages", result.get("data", {}).get("messages", []))
        elif isinstance(result, list):
            messages = result

        if not messages:
            logger.info("私聊 %d 历史消息已拉取完毕（共 %d 条）", user_id, fetched)
            break

        # 翻页去重检测
        batch_ids = {msg.get("message_id", 0) for msg in messages}
        if batch_ids.issubset(seen_message_ids):
            logger.info(
                "私聊 %d 翻页无新消息（已拉取 %d 条，均为重复），停止扫描",
                user_id, fetched,
            )
            break
        seen_message_ids.update(batch_ids)

        # 批次内并发处理所有消息（复用 _process_history_message，
        # 用 _PRIVATE_GROUP_ID 作为虚拟群号）
        tasks = [
            asyncio.create_task(
                _process_history_message(
                    bot=bot, msg=msg, group_id=_PRIVATE_GROUP_ID,
                    scan_cfg=scan_cfg, cutoff_time=cutoff_time,
                ),
                name=f"scan-p{user_id}-{msg.get('message_id', 0)}",
            )
            for msg in messages
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        stop_scan = False
        for r in results:
            if isinstance(r, Exception):
                logger.warning("私聊 %d 历史消息处理异常: %s", user_id, r)
                continue
            if r == -1:
                logger.info(
                    "私聊 %d 已超出时间范围（%d 条后停止）", user_id, fetched
                )
                stop_scan = True
                break
            if r > 0:
                scanned += 1
                archived += r
        if stop_scan:
            return

        # 翻页：用 batch 中最小的 message_seq 作为下一页游标
        msg_seqs_in_batch = [
            msg.get("message_seq", 0) for msg in messages if msg.get("message_seq")
        ]
        if msg_seqs_in_batch:
            cursor_msg_seq = min(msg_seqs_in_batch)
        else:
            cursor_msg_seq = None
        fetched += len(messages)

        logger.debug(
            "私聊 %d 已拉取 %d 条消息 | 含媒体: %d | 已归档: %d",
            user_id, fetched, scanned, archived,
        )

    if fetched >= scan_cfg.max_per_group:
        logger.info(
            "私聊 %d 已达拉取上限 %d 条（含媒体 %d 条，归档 %d 个）",
            user_id, scan_cfg.max_per_group, scanned, archived,
        )
    else:
        logger.warning(
            "私聊 %d 扫描完成 | 拉取: %d 条 | 含媒体: %d | 归档: %d",
            user_id, fetched, scanned, archived,
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


def _is_local_path(url: str) -> bool:
    """判断 URL 是否为本地文件路径（Windows 盘符路径或 UNC 路径）"""
    if not url:
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", url):
        return True
    if url.startswith("\\\\"):
        return True
    return False


def _account_data_roots() -> list[Path]:
    """发现本机所有 QQ 账号的 nt_data 数据根目录"""
    roots: list[Path] = []
    if not _QQ_FILE_ROOT.is_dir():
        return roots
    for account_dir in _QQ_FILE_ROOT.iterdir():
        if not account_dir.is_dir():
            continue
        nt_data = account_dir / "nt_qq" / "nt_data"
        if nt_data.is_dir():
            roots.append(nt_data)
    return roots


def _rel_after_nt_data(url: str) -> str:
    """提取本地路径 URL 中 nt_data 之后的相对路径（如 Video/2026-08/Ori/x.mp4）"""
    marker = "nt_data"
    idx = url.find(marker)
    if idx == -1:
        return ""
    return url[idx + len(marker):].strip("\\/ ").replace("\\", "/")


async def _copy_src_to_temp(src: Path, temp_dir: Path) -> Path | None:
    """将本地文件复制到临时目录"""
    dst = temp_dir / src.name
    try:
        async with aiofiles.open(src, "rb") as f_src:
            content = await f_src.read()
        async with aiofiles.open(dst, "wb") as f_dst:
            await f_dst.write(content)
        logger.warning("[本地复制] %s -> %s", src, dst)
        return dst
    except Exception as e:
        logger.warning("[本地复制] 复制失败: %s", e)
        return None


async def _copy_local_path(src_str: str, temp_dir: Path) -> Path | None:
    """将已知的本地文件路径直接复制到临时目录（不走 HTTP）"""
    src = Path(src_str)
    if not src.is_file():
        logger.warning("[本地复制] 源文件不存在: %s", src)
        return None
    return await _copy_src_to_temp(src, temp_dir)


async def _copy_local_file(
    item: MediaItem, temp_dir: Path, url: str = "",
) -> Path | None:
    """
    尝试从本机任意 QQ 账号的 NapCat 本地数据目录复制媒体文件。

    合并转发消息中的视频 URL 指向接收账号（bot 小号）的缓存路径，
    但文件可能实际缓存在同机其他账号下（如日常主账号多端同步），
    因此按 URL 相对路径/文件名跨账号搜索。

    Args:
        url: 本地路径 URL，用于提取 nt_data 相对路径精确匹配，
             失败时回退到按文件名 rglob 搜索。
    """
    # 媒体类型 -> 数据子目录映射
    type_dir = {
        "image": "Pic",
        "video": "Video",
        "record": "Record",
    }
    sub_dir = type_dir.get(item.media_type)
    if sub_dir is None:
        return None

    nt_datas = _account_data_roots()
    if not nt_datas:
        logger.debug("未找到任何 QQ 账号的 nt_data 目录")
        return None

    # 1) 按 URL 相对路径精确尝试（如 Video/2026-08/Ori/xxx.mp4）
    rel = _rel_after_nt_data(url)
    if rel:
        for nt_data in nt_datas:
            src = nt_data / rel
            if src.is_file():
                return await _copy_src_to_temp(src, temp_dir)

    # 2) 按文件名变体跨账号 rglob 搜索
    candidates: list[str] = []
    if item.file_name:
        raw = item.file_name.strip()
        candidates.extend([raw, raw.lower()])
    if url:
        url_name = Path(url).name
        if url_name:
            candidates.extend([url_name, url_name.lower()])

    seen: set[str] = set()
    for variant in candidates:
        if variant in seen:
            continue
        seen.add(variant)
        for nt_data in nt_datas:
            search_root = nt_data / sub_dir
            if not search_root.is_dir():
                continue
            found = list(search_root.rglob(variant))
            if found:
                return await _copy_src_to_temp(found[0], temp_dir)

    logger.debug("[本地复制] 跨账号未找到匹配文件: %s", item.file_name)
    return None


async def _try_get_file_download(
    bot: Bot, item: MediaItem, url: str, temp_dir: Path,
) -> Path | None:
    """
    通过 NapCat get_file API 按文件名换取下载地址。

    NapCat 转换视频段时会将 {peer, msgId, elementId, fileUUID} 以文件名为
    键存入内存缓存（24 小时有效）。get_file(file=文件名) 命中缓存后可
    触发 downloadMedia 从服务器下载，返回 {file: 本地路径, url: CDN 地址}。
    对直接发送/近期收到的视频有效；转发拆包的消息缓存键不匹配，通常无效。
    """
    # 候选参数：消息段 file 名（带扩展名优先）、URL 文件名、file_id
    candidates: list[str] = []
    if item.file_name:
        candidates.append(item.file_name)
    if url:
        url_name = Path(url).name
        if url_name:
            candidates.append(url_name)
    if item.file_id:
        candidates.append(item.file_id)

    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)

        # downloadMedia 内部等待下载完成事件可能超时（大文件下载慢），
        # 但下载可能在后台继续，延迟后重试一次可命中已下载的文件。
        # 注意：转发拆包的视频消息不在本地库，downloadMedia 必然超时，
        # 重试次数过多会浪费大量时间（每次超时 30-60s），只重试一次。
        for attempt in range(2):
            try:
                logger.warning(
                    "[get_file] 按文件名换取下载地址(第%d次): file=%s",
                    attempt + 1, cand[:80],
                )
                # 注意：NoneBot call_api 用 _timeout 参数控制超时（默认 60s，
                # 大文件 downloadMedia 下载可能超时，放宽到 300s）
                resp = await bot.call_api("get_file", file=cand, _timeout=300)
            except Exception as e:
                logger.warning(
                    "[get_file] %s 失败: %s: %s", cand[:60], type(e).__name__, e,
                )
                resp = None

            if isinstance(resp, dict):
                # 1) NapCat 下载到本地缓存后的路径
                local = resp.get("file", "")
                if local and isinstance(local, str):
                    src = Path(local)
                    if src.is_file():
                        logger.warning("[get_file] 服务器重新下载成功: %s", src)
                        return await _copy_src_to_temp(src, temp_dir)

                # 2) 返回的 CDN 下载地址
                http_url = resp.get("url", "")
                if http_url and isinstance(http_url, str) and http_url.startswith("http"):
                    try:
                        result = await _downloader.download(http_url, temp_dir)
                        return result.temp_path
                    except DownloadError as e:
                        logger.warning("[get_file] CDN 下载失败: %s", e)

            # 失败后等待下载在后台继续，再重试
            if attempt < 2:
                logger.warning("[get_file] 等待 %d 秒后重试 %s ...", 20, cand[:60])
                await asyncio.sleep(20)

    return None


# 链接/密码提取正则
_LINK_URL_RE = re.compile(r"https?://[^\s<>\"']+")
# 支持冒号/等号/空格/中文连接词分隔，含常见谐音变体（解鸭/解呀=解压），3 位短码
_CODE_RE = re.compile(
    r"(?:提取码|访问码|密码|解压码|解鸭码|解呀码|解呀吗|解鸭|解呀|"
    r"口令|暗号|pwd|code|key)[=:：\s]*([A-Za-z0-9_-]{3,})",
    re.IGNORECASE,
)


def _extract_links(text: str) -> list[str]:
    """从文本中提取所有 URL 链接"""
    return _LINK_URL_RE.findall(text)


def _extract_codes(text: str) -> list[str]:
    """从文本中提取提取码/密码/解压码等（关键词后跟的 token，支持冒号/等号/空格分隔）"""
    return _CODE_RE.findall(text)


async def _archive_forward_links(
    content: list, group_id: int, source_message_id: int, user_id: int,
) -> None:
    """
    提取转发消息中的网盘链接/密码等信息并存档为 markdown 文件。

    存档位置: {archive_root}/{group_id}/links/{YYYY-MM}/forward_{源消息ID}.md

    只要合集内存在链接或密码类内容，就将合集中的所有文本消息完整
    存档（网盘链接后面的说明/解压密码等上下文一并保留），并标注每
    条消息提取出的链接与密码。
    """

    def _collect_texts(sub: dict) -> str:
        """收集消息的文本段 + json 段（群分享卡片等）内容"""
        texts: list[str] = []
        for s in sub.get("message", []):
            if s.get("type") == "text":
                texts.append(s.get("data", {}).get("text", ""))
            elif s.get("type") == "json":
                j = s.get("data", {}).get("data", "")
                if isinstance(j, str):
                    texts.append(j)
        return "\n".join(texts).strip()

    # 第一遍：判断合集内是否存在链接/密码类内容
    any_hit = False
    for sub in content:
        joined = _collect_texts(sub)
        if joined and (_extract_links(joined) or _extract_codes(joined)):
            any_hit = True
            break
    if not any_hit:
        return

    # 第二遍：存档合集内所有文本消息
    lines: list[str] = []
    for i, sub in enumerate(content, 1):
        sub_uid = sub.get("user_id", 0) or sub.get("sender", {}).get("user_id", 0) or user_id
        joined = _collect_texts(sub)
        if not joined:
            continue

        links = _extract_links(joined)
        codes = _extract_codes(joined)

        lines.append(f"## 消息 {i} (用户 {sub_uid})")
        lines.append("> " + joined.replace("\n", "\n> "))
        if links:
            lines.append("")
            lines.append("链接:")
            lines.extend(f"- {l}" for l in links)
        if codes:
            lines.append("")
            lines.append("密码/提取码:")
            lines.extend(f"- {c}" for c in codes)
        lines.append("")

    now = datetime.now()
    links_dir = (
        get_config().get_archive_path()
        / str(group_id) / "links" / now.strftime("%Y-%m")
    )
    links_dir.mkdir(parents=True, exist_ok=True)
    fpath = links_dir / f"forward_{source_message_id}.md"

    header = [
        "# 转发消息链接存档",
        f"- 转发时间: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 群: {group_id}",
        f"- 发送者: {user_id}",
        f"- 源消息ID: {source_message_id}",
        "",
    ]
    async with aiofiles.open(fpath, "w", encoding="utf-8") as f:
        await f.write("\n".join(header + lines))

    logger.warning("转发链接存档: %s", fpath)


async def _process_forward_content(
    bot: Bot,
    items: list,
    group_id: int,
    default_user_id: int,
    default_msg_id: int,
) -> int:
    """
    递归处理已展开的 forward content 消息列表。
    items 中的每条消息可能包含媒体（image/video/record/file）或嵌套的 forward。

    Returns:
        成功归档的媒体项数量。
    """
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    archived_count = 0

    for i, sub_msg in enumerate(items):
        sub_raw = sub_msg.get("message", [])
        if not sub_raw:
            continue

        # 转成 MessageSegment 对象
        try:
            if isinstance(sub_raw, list) and len(sub_raw) > 0 and isinstance(sub_raw[0], dict):
                segs = [MessageSegment(type=s["type"], data=s.get("data", {})) for s in sub_raw if isinstance(s, dict)]
                sub_onebot = Message(segs)
            else:
                sub_onebot = Message(sub_raw)
        except Exception as e:
            logger.warning("  forward_content[%d] Message 构造失败: %s", i, e)
            continue

        sub_uid = sub_msg.get("user_id", 0) or sub_msg.get("sender", {}).get("user_id", 0) or default_user_id
        sub_mid = sub_msg.get("message_id", 0) or default_msg_id
        # 转发拆包消息 ID 加偏移，避免与真实消息 ID 冲突（见 _FORWARD_ID_OFFSET）
        sub_mid = sub_mid + _FORWARD_ID_OFFSET

        # 递归处理嵌套的 forward
        sub_fwd_segs = [s for s in sub_onebot if s.type == "forward"]
        if sub_fwd_segs:
            for sf_seg in sub_fwd_segs:
                sf_id = sf_seg.data.get("id", "")
                if not sf_id:
                    continue
                sf_content = sf_seg.data.get("content") or sf_seg.data.get("messages")
                if sf_content and isinstance(sf_content, list):
                    archived_count += await _process_forward_content(
                        bot=bot, items=sf_content, group_id=group_id,
                        default_user_id=sub_uid, default_msg_id=sub_mid,
                    )
                else:
                    try:
                        sf_result = await bot.call_api("get_forward_msg", id=sf_id)
                        sf_msgs = []
                        if isinstance(sf_result, dict):
                            sf_msgs = sf_result.get("messages", [])
                        if sf_msgs:
                            archived_count += await _process_forward_content(
                                bot=bot, items=sf_msgs, group_id=group_id,
                                default_user_id=sub_uid, default_msg_id=sub_mid,
                            )
                    except Exception as e:
                        logger.warning("  嵌套 forward %s 无法展开: %s", sf_id, e)

        # 提取并处理媒体项（含 forward 的消息也可能同时有图片）
        media_items = classify_message(sub_onebot)
        if not media_items:
            continue

        cfg = get_config()
        for idx, item in enumerate(media_items):
            if getattr(cfg.media_types, item.media_type, False):
                ok = await _process_media_item(
                    bot=bot, item=item, group_id=group_id,
                    user_id=sub_uid, message_id=sub_mid, seq=idx,
                    skip_url_refresh=True,
                )
                if ok:
                    archived_count += 1

    return archived_count


async def _process_forward_message(
    bot: Bot,
    forward_id: str,
    group_id: int,
    user_id: int,
    source_message_id: int,
) -> None:
    """
    处理合并转发（forward）消息：拆包后递归提取媒体项。

    优先用 get_msg 获取外层消息的 content（嵌套 forward 完整且图片带
    CDN URL）；get_forward_msg 对嵌套 forward 返回 1200 失败，仅作后备。
    """
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    # 1) 主路径：get_msg 拿外层消息 content（含完整嵌套与 URL）
    try:
        msg_resp = await bot.call_api("get_msg", message_id=source_message_id)
        content = None
        if isinstance(msg_resp, dict):
            for seg in (msg_resp.get("data", {}).get("message") or []):
                if seg.get("type") == "forward":
                    content = seg.get("data", {}).get("content") or []
                    break
        if content:
            logger.warning(
                "拆包合并转发消息(get_msg) | forward_id=%s | 源消息ID: %d | 内含 %d 条消息",
                forward_id, source_message_id, len(content),
            )
            # 存档转发中的网盘链接/提取码等文本信息
            await _archive_forward_links(
                content=content, group_id=group_id,
                source_message_id=source_message_id, user_id=user_id,
            )
            await _process_forward_content(
                bot=bot, items=content, group_id=group_id,
                default_user_id=user_id, default_msg_id=source_message_id,
            )
            return
    except Exception as e:
        logger.warning("get_msg 获取转发消息失败，尝试 get_forward_msg: %s", e)

    # 2) 后备：get_forward_msg 拆包
    try:
        result = await bot.call_api("get_forward_msg", id=forward_id)
    except Exception as e:
        logger.warning("合并转发消息无法获取，跳过: forward_id=%s, %s", forward_id, e)
        return

    messages = []
    if isinstance(result, dict):
        messages = result.get("messages", [])

    if not messages:
        logger.debug("合并转发消息 %s 为空", forward_id)
        return

    # 存档转发中的网盘链接/提取码等文本信息
    await _archive_forward_links(
        content=messages, group_id=group_id,
        source_message_id=source_message_id, user_id=user_id,
    )

    logger.warning(
        "拆包合并转发消息 | forward_id=%s | 源消息ID: %d | 内含 %d 条消息",
        forward_id, source_message_id, len(messages),
    )

    for inner in messages:
        inner_msg_id = inner.get("message_id", 0)
        # 转发拆包消息 ID 加偏移，避免与真实消息 ID 冲突（见 _FORWARD_ID_OFFSET）
        inner_msg_id = inner_msg_id + _FORWARD_ID_OFFSET
        inner_user_id = inner.get("user_id", 0) or inner.get("sender", {}).get("user_id", 0)
        inner_user_id = inner_user_id or user_id
        raw_segments = inner.get("message", [])

        if not raw_segments:
            continue

        # 转成 MessageSegment 对象
        try:
            if isinstance(raw_segments, list) and isinstance(raw_segments[0], dict):
                segs = [MessageSegment(type=s["type"], data=s.get("data", {})) for s in raw_segments if isinstance(s, dict)]
                onebot_msg = Message(segs)
            else:
                onebot_msg = Message(raw_segments)
        except Exception:
            continue

        # 递归处理内层 forward
        forward_inner = [s for s in onebot_msg if s.type == "forward"]
        if forward_inner:
            for seg in forward_inner:
                fid = seg.data.get("id", "")
                if not fid:
                    continue

                content = seg.data.get("content") or seg.data.get("messages")
                if content and isinstance(content, list):
                    await _process_forward_content(
                        bot=bot, items=content, group_id=group_id,
                        default_user_id=inner_user_id,
                        default_msg_id=inner_msg_id or source_message_id,
                    )
                else:
                    try:
                        fwd_result = await bot.call_api("get_forward_msg", id=fid)
                        fwd_msgs = []
                        if isinstance(fwd_result, dict):
                            fwd_msgs = fwd_result.get("messages", [])
                        if fwd_msgs:
                            await _process_forward_content(
                                bot=bot, items=fwd_msgs, group_id=group_id,
                                default_user_id=inner_user_id,
                                default_msg_id=inner_msg_id or source_message_id,
                            )
                    except Exception as e:
                        logger.warning("内层 forward %s 无法展开: %s", fid, e)
            continue

        # 提取并处理媒体项
        media_items = classify_message(onebot_msg)
        if not media_items:
            continue

        cfg = get_config()
        for idx, item in enumerate(media_items):
            if not getattr(cfg.media_types, item.media_type, False):
                continue

            await _process_media_item(
                bot=bot, item=item, group_id=group_id,
                user_id=inner_user_id,
                message_id=inner_msg_id or source_message_id,
                seq=idx, skip_url_refresh=True,
            )


async def _on_bot_connect(bot: Bot) -> None:
    """Bot 连接就绪后启动历史消息扫描。"""
    cfg = get_config()
    if not cfg.startup_scan.enabled:
        return

    # 延迟一小段时间等连接稳定
    await asyncio.sleep(2)
    await _scan_all_groups(bot)
