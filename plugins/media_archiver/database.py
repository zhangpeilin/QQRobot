"""SQLite 元数据管理"""



import logging
import time
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger("media_archiver.database")

# 建表 SQL
# 唯一约束 (message_id, media_type, file_md5)：一条消息可含多张图片/多个
# 媒体段（各段文件不同），仅按 (message_id, media_type) 约束会导致同消息
# 后续媒体段记录无法插入、MD5 去重失效、重启后重复归档
_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    INTEGER NOT NULL,
    group_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    media_type    TEXT    NOT NULL,       -- image / video / record / file
    file_name     TEXT,                   -- 原始文件名（如有）
    file_md5      TEXT,                   -- 文件 MD5（下载后计算）
    file_size     INTEGER,               -- 文件大小（字节）
    storage_path  TEXT    NOT NULL,       -- 归档后的本地路径
    source_url    TEXT,                   -- 原始下载 URL
    created_at    REAL    NOT NULL,       -- Unix 时间戳
    UNIQUE(message_id, media_type, file_md5)
);

CREATE INDEX IF NOT EXISTS idx_group ON media_records(group_id);
CREATE INDEX IF NOT EXISTS idx_md5 ON media_records(file_md5);
CREATE INDEX IF NOT EXISTS idx_created ON media_records(created_at);
"""


async def _migrate_legacy_schema(db) -> None:
    """将旧版 UNIQUE(message_id, media_type) 约束迁移为含 file_md5 的新约束"""
    cursor = await db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='media_records'"
    )
    row = await cursor.fetchone()
    if not row or not row[0]:
        return
    sql = row[0]
    # 已是新约束则跳过
    if "UNIQUE(message_id, media_type, file_md5)" in sql:
        return
    logger.warning("检测到旧版唯一约束，迁移 media_records 表...")
    await db.execute("ALTER TABLE media_records RENAME TO media_records_old")
    await db.executescript(_SCHEMA)
    await db.execute(
        """INSERT OR IGNORE INTO media_records
           (message_id, group_id, user_id, media_type, file_name, file_md5,
            file_size, storage_path, source_url, created_at)
           SELECT message_id, group_id, user_id, media_type, file_name, file_md5,
                  file_size, storage_path, source_url, created_at
           FROM media_records_old"""
    )
    await db.execute("DROP TABLE media_records_old")
    await db.commit()
    logger.info("media_records 表迁移完成")


class MediaDatabase:
    """异步 SQLite 元数据库封装"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """打开数据库连接并建表（含旧表迁移）"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await _migrate_legacy_schema(self._db)
        await self._db.commit()
        logger.info("数据库已初始化: %s", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def insert_record(
        self,
        message_id: int,
        group_id: int,
        user_id: int,
        media_type: str,
        storage_path: str,
        file_name: str = "",
        file_md5: str = "",
        file_size: int = 0,
        source_url: str = "",
    ) -> int:
        """
        插入一条媒体记录。

        Returns:
            实际插入的行数（INSERT OR IGNORE 遇到唯一约束冲突时返回 0，
            表示记录已存在、本次未插入）。
        """
        assert self._db is not None
        cursor = await self._db.execute(
            """INSERT OR IGNORE INTO media_records
               (message_id, group_id, user_id, media_type, file_name,
                file_md5, file_size, storage_path, source_url, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id, group_id, user_id, media_type,
                file_name, file_md5, file_size, storage_path,
                source_url, time.time(),
            ),
        )
        await self._db.commit()
        return cursor.rowcount  # type: ignore

    async def exists_by_message(self, message_id: int, media_type: str) -> bool:
        """根据消息 ID + 类型判断是否已记录"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM media_records WHERE message_id = ? AND media_type = ? LIMIT 1",
            (message_id, media_type),
        )
        return await cursor.fetchone() is not None

    async def exists_by_md5(self, file_md5: str) -> bool:
        """根据 MD5 判断文件是否已存在"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM media_records WHERE file_md5 = ? LIMIT 1",
            (file_md5,),
        )
        return await cursor.fetchone() is not None

    async def get_stats(self, group_id: int = 0) -> dict:
        """获取归档统计信息"""
        assert self._db is not None
        where = "WHERE group_id = ?" if group_id else ""
        params = (group_id,) if group_id else ()

        cursor = await self._db.execute(
            f"""SELECT
                  media_type,
                  COUNT(*) as count,
                  COALESCE(SUM(file_size), 0) as total_size
                FROM media_records {where}
                GROUP BY media_type""",
            params,
        )
        rows = await cursor.fetchall()
        return {row["media_type"]: {"count": row["count"], "total_size": row["total_size"]} for row in rows}


# 全局数据库实例
_db: Optional[MediaDatabase] = None


async def get_database(archive_root: Path) -> MediaDatabase:
    """获取或创建全局数据库实例"""
    global _db
    if _db is None:
        _db = MediaDatabase(archive_root / "metadata.db")
        await _db.initialize()
    return _db


async def close_database() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None
