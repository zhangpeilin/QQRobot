"""Server酱 微信通知模块

通过 Server酱（https://sct.ftqq.com）推送通知到微信服务通知。
免费版每天 5 条额度。通知未启用或发送失败时静默返回 False，不阻塞主流程。
"""

import logging

import aiohttp

from .config import get_config

logger = logging.getLogger("media_archiver.notifier")

_API = "https://sctapi.ftqq.com/{key}.send"


async def send_notification(title: str, content: str = "") -> bool:
    """
    发送微信通知。

    Args:
        title: 通知标题（必填）
        content: 通知正文（markdown 格式，可为空）

    Returns:
        True 表示发送成功；未启用/无 SendKey/失败返回 False。
    """
    cfg = get_config()
    if not cfg.notify.enabled or not cfg.notify.sendkey:
        return False

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                _API.format(key=cfg.notify.sendkey),
                data={"title": title[:32], "desp": content},
            ) as resp:
                ok = resp.status == 200
                if ok:
                    logger.info("微信通知已发送: %s", title)
                else:
                    logger.warning("微信通知发送失败: HTTP %s", resp.status)
                return ok
    except Exception as e:
        logger.warning("微信通知发送异常: %s", e)
        return False
