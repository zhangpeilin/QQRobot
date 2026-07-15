"""文件归档存储模块"""



import logging
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .classifier import get_media_type_dir, guess_extension
from .dedup import compute_file_md5

logger = logging.getLogger("media_archiver.archiver")

# 中国标准时间
_CST = timezone(timedelta(hours=8))


class Archiver:
    """
    文件归档器。

    职责：
    1. 根据媒体类型和日期计算目标路径
    2. 将下载的临时文件移动到归档目录并重命名
    3. 命名规则：{user_id}_{timestamp}_{md5[:8]}{ext}
    """

    def __init__(self, archive_root: Path):
        self.archive_root = archive_root
        self.archive_root.mkdir(parents=True, exist_ok=True)

    def build_storage_path(
        self,
        media_type: str,
        group_id: int,
        user_id: int,
        url: str = "",
        file_name: str = "",
        md5_prefix: str = "",
    ) -> Path:
        """
        计算归档路径（不创建文件）。

        目录结构: {archive_root}/{group_id}/{type_dir}/{YYYY-MM}/{DD}/{filename}
        """
        now = datetime.now(_CST)
        type_dir = get_media_type_dir(media_type)
        date_dir = now.strftime("%Y-%m")
        day_dir = now.strftime("%d")

        # 文件名
        ts = int(time.time())
        ext = guess_extension(url, file_name, media_type)
        short_md5 = md5_prefix[:8] if md5_prefix else "00000000"
        filename = f"{user_id}_{ts}_{short_md5}{ext}"

        return self.archive_root / str(group_id) / type_dir / date_dir / day_dir / filename

    async def archive_file(
        self,
        temp_path: Path,
        media_type: str,
        group_id: int,
        user_id: int,
        url: str = "",
        file_name: str = "",
    ) -> tuple[Path, str]:
        """
        将临时文件归档到目标路径。

        Returns:
            (storage_path, file_md5) 元组
        """
        # 计算 MD5
        md5 = await compute_file_md5(temp_path)

        # 计算目标路径
        storage_path = self.build_storage_path(
            media_type=media_type,
            group_id=group_id,
            user_id=user_id,
            url=url,
            file_name=file_name,
            md5_prefix=md5,
        )

        # 确保目录存在
        storage_path.parent.mkdir(parents=True, exist_ok=True)

        # 移动文件
        shutil.move(str(temp_path), str(storage_path))

        logger.info(
            "归档完成: %s -> %s",
            file_name or url[:50],
            storage_path,
        )

        return storage_path, md5

    def get_stats_summary(self) -> dict:
        """统计各目录的文件数和总大小（遍历所有群目录）"""
        stats = {}
        type_names = ["images", "videos", "audios", "files", "others"]
        for type_name in type_names:
            stats[type_name] = {"count": 0, "size": 0}

        for group_dir in self.archive_root.iterdir():
            if not group_dir.is_dir() or group_dir.name == ".tmp":
                continue
            for type_name in type_names:
                type_path = group_dir / type_name
                if not type_path.exists():
                    continue
                for f in type_path.rglob("*"):
                    if f.is_file():
                        stats[type_name]["count"] += 1
                        stats[type_name]["size"] += f.stat().st_size
        return stats
