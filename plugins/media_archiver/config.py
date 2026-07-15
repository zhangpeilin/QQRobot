"""业务配置加载模块"""



import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger("media_archiver.config")

# 项目根目录（bot.py 所在目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class DownloadConfig(BaseModel):
    max_concurrent: int = Field(default=3, ge=1, le=20)
    timeout: int = Field(default=120, ge=10)
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_base_delay: float = Field(default=2.0, ge=0.5)


class MediaTypesConfig(BaseModel):
    image: bool = True
    video: bool = True
    record: bool = True
    file: bool = True


class DedupConfig(BaseModel):
    enabled: bool = True
    strategy: str = Field(default="md5", pattern=r"^(md5|message_id)$")


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = ""
    max_size_mb: int = 50
    backup_count: int = 5


class StartupScanConfig(BaseModel):
    """启动时历史消息扫描配置"""

    enabled: bool = False
    """是否启用启动扫描"""

    time_range_hours: int = Field(default=0, ge=0)
    """时间范围（小时），0 = 全部历史消息"""

    max_per_group: int = Field(default=500, ge=1, le=5000)
    """每个群最多拉取的消息条数"""


class AppConfig(BaseModel):
    watch_groups: list[int] = Field(default_factory=list)
    archive_root: str = "./data/archive"
    download: DownloadConfig = Field(default_factory=DownloadConfig)
    media_types: MediaTypesConfig = Field(default_factory=MediaTypesConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    startup_scan: StartupScanConfig = Field(default_factory=StartupScanConfig)

    def get_archive_path(self) -> Path:
        """解析归档根目录为绝对路径"""
        p = Path(self.archive_root)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    def is_group_watched(self, group_id: int) -> bool:
        """判断群是否在监听列表中（空列表 = 监听全部）"""
        if not self.watch_groups:
            return True
        return group_id in self.watch_groups


_config: AppConfig | None = None


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """从 YAML 文件加载配置，找不到则使用默认值"""
    global _config

    if config_path is None:
        config_path = PROJECT_ROOT / "config.yaml"
    else:
        config_path = Path(config_path)

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
        _config = AppConfig(**raw)
        logger.info("配置已加载: %s", config_path)
    else:
        _config = AppConfig()
        logger.warning("配置文件不存在，使用默认配置: %s", config_path)

    return _config


def get_config() -> AppConfig:
    """获取已加载的配置单例"""
    global _config
    if _config is None:
        _config = load_config()
    return _config
