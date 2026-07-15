"""
QQ 群媒体自动归档插件

监听群消息中的图片、视频、语音、文件，自动下载并分类归档到本地目录。
"""

from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="media_archiver",
    description="QQ 群媒体自动归档 - 图片/视频/语音/文件自动下载分类存储",
    usage="被动监听，无需命令。自动保存目标群的媒体内容到本地归档目录。",
)

# 加载子模块（listener 中注册了消息处理器和启动/关闭钩子）
from . import listener as _listener  # noqa: F401, E402
