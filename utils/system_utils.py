from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException


def _load_app_timezone() -> timezone:
    """加载应用时区；当系统时区库缺失时退回到固定 UTC+8。"""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


class SystemUtils:
    """系统级工具类，负责数据库、时间、图片缓存与通用参数校验。"""

    IMAGE_CACHE_ROUTE = "/image-cache"
    MAX_IMAGE_BYTES = 10 * 1024 * 1024
    MAX_IMAGE_BASE64_CHARS = 16 * 1024 * 1024
    APP_TZ = _load_app_timezone()
    SUPPORTED_SOURCES = {"all", "zzz", "bgi", "sr"}

    @staticmethod
    def get_conn(db_path: Path) -> sqlite3.Connection:
        """创建 SQLite 连接，并启用字典风格取值。"""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def init_db(cls, db_path: Path, cache_dir: Path) -> None:
        """初始化数据库结构，并兼容升级旧表缺失的 source 字段。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        with cls.get_conn(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL DEFAULT 'zzz',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    image TEXT NOT NULL,
                    event_ts INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    created_ts INTEGER NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(items)").fetchall()
            }
            if "source" not in columns:
                conn.execute("ALTER TABLE items ADD COLUMN source TEXT NOT NULL DEFAULT 'zzz'")
                conn.execute("UPDATE items SET source = 'zzz' WHERE source IS NULL OR source = ''")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_day ON items(day)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_event_ts ON items(event_ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_source_day ON items(source, day)")
            conn.commit()

    @staticmethod
    def ensure_json_object(payload: object) -> dict:
        """确保请求体是 JSON 对象。"""
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
        return payload

    @classmethod
    def parse_timestamp(cls, raw_value: object) -> datetime:
        """解析 ISO-8601 或 Unix 时间戳，并统一转换为 UTC。"""
        if isinstance(raw_value, (int, float)):
            timestamp_value = float(raw_value)
            if timestamp_value > 10**12:
                timestamp_value /= 1000
            return datetime.fromtimestamp(timestamp_value, tz=timezone.utc)

        if raw_value is None:
            raise HTTPException(status_code=400, detail="timestamp 不能为空")

        value = str(raw_value).strip()
        if not value:
            raise HTTPException(status_code=400, detail="timestamp 不能为空")
        if value.isdigit():
            return cls.parse_timestamp(int(value))
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="timestamp 必须是 ISO-8601 字符串或 Unix 时间戳",
            ) from exc

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=cls.APP_TZ)
        return dt.astimezone(timezone.utc)

    @classmethod
    def prune_old_data(cls, conn: sqlite3.Connection, cache_dir: Path) -> None:
        """清理超出 7 天的记录和关联图片缓存。"""
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
        conn.execute("DELETE FROM items WHERE event_ts < ?", (cutoff,))
        conn.commit()
        cls.prune_old_cached_images(cache_dir, cutoff)

    @staticmethod
    def prune_old_cached_images(cache_dir: Path, cutoff_ts: int) -> None:
        """删除缓存目录中已经过期的图片文件。"""
        if not cache_dir.exists():
            return
        for file_path in cache_dir.iterdir():
            if not file_path.is_file():
                continue
            try:
                if int(file_path.stat().st_mtime) < cutoff_ts:
                    file_path.unlink(missing_ok=True)
            except OSError:
                continue

    @classmethod
    def to_local_iso(cls, ts: int) -> str:
        """将 UTC 时间戳转换为应用时区的 ISO 字符串。"""
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(cls.APP_TZ).isoformat()

    @staticmethod
    def normalize_text_field(data: dict, field_name: str, max_length: int, required: bool = True) -> str:
        """校验普通文本字段长度与必填性。"""
        raw_value = data.get(field_name, "")
        if raw_value is None:
            raw_value = ""
        value = str(raw_value).strip()
        if required and not value:
            raise HTTPException(status_code=400, detail=f"{field_name} 不能为空")
        if len(value) > max_length:
            raise HTTPException(status_code=400, detail=f"{field_name} 长度不能超过 {max_length}")
        return value

    @staticmethod
    def normalize_optional_text(value: object, field_name: str, max_length: int) -> str:
        """校验可选文本字段；为空时返回空字符串。"""
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        if len(text) > max_length:
            raise HTTPException(status_code=400, detail=f"{field_name} 长度不能超过 {max_length}")
        return text

    @staticmethod
    def normalize_recipient_field(value: object, field_name: str) -> str:
        """将接收者字段标准化为逗号拼接字符串。"""
        if value is None:
            return ""
        if isinstance(value, (list, tuple, set)):
            text = ", ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value).strip()
        if len(text) > 500:
            raise HTTPException(status_code=400, detail=f"{field_name} 长度不能超过 500")
        return text

    @staticmethod
    def guess_image_extension_from_mime(mime_type: str) -> str | None:
        """根据 MIME 类型推断图片扩展名。"""
        normalized = mime_type.split(";")[0].strip().lower()
        if not normalized.startswith("image/"):
            return None
        guessed = mimetypes.guess_extension(normalized)
        if guessed == ".jpe":
            return ".jpg"
        return guessed

    @classmethod
    def guess_image_extension_from_bytes(cls, image_bytes: bytes, mime_type: str = "") -> str:
        """根据 MIME 类型和文件签名推断图片扩展名。"""
        guessed = cls.guess_image_extension_from_mime(mime_type)
        if guessed:
            return guessed

        signatures = (
            (b"\x89PNG\r\n\x1a\n", ".png"),
            (b"\xff\xd8\xff", ".jpg"),
            (b"GIF87a", ".gif"),
            (b"GIF89a", ".gif"),
            (b"BM", ".bmp"),
            (b"RIFF", ".webp"),
        )
        for prefix, extension in signatures:
            if image_bytes.startswith(prefix):
                if extension == ".webp" and image_bytes[8:12] != b"WEBP":
                    continue
                return extension

        if image_bytes.lstrip().startswith(b"<svg"):
            return ".svg"
        return ".png"

    @classmethod
    def decode_base64_image(cls, image_value: str) -> tuple[bytes, str]:
        """解析 data URL 或纯 base64 图片。"""
        value = image_value.strip()
        if not value:
            return b"", ""

        mime_type = ""
        encoded = value
        if value.startswith("data:"):
            header, separator, data_part = value.partition(",")
            if not separator:
                raise HTTPException(status_code=400, detail="image 的 data URL 格式不正确")
            if ";base64" not in header.lower():
                raise HTTPException(status_code=400, detail="image 必须是 base64 数据")
            mime_type = header[5:].split(";")[0].strip()
            encoded = data_part

        encoded = "".join(encoded.split())
        if not encoded:
            return b"", ""
        if len(encoded) > cls.MAX_IMAGE_BASE64_CHARS:
            raise HTTPException(status_code=400, detail="image 数据过大")

        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="image 不是合法的 base64 图片数据") from exc

        if not image_bytes:
            return b"", ""
        if len(image_bytes) > cls.MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="image 解码后大小不能超过 10MB")

        return image_bytes, cls.guess_image_extension_from_bytes(image_bytes, mime_type)

    @classmethod
    def cache_image(cls, cache_dir: Path, image_url: str, event_ts: int) -> str:
        """缓存图片到本地目录，并返回静态资源访问路径。"""
        image_value = image_url.strip()
        if not image_value:
            return ""
        if image_value.startswith(cls.IMAGE_CACHE_ROUTE):
            return image_value

        image_bytes, extension = cls.decode_base64_image(image_value)
        if not image_bytes:
            return ""

        cache_key = hashlib.sha256(image_bytes).hexdigest()
        file_name = f"{cache_key}{extension}"
        file_path = cache_dir / file_name
        if not file_path.exists():
            file_path.write_bytes(image_bytes)
        os.utime(file_path, (event_ts, event_ts))
        return f"{cls.IMAGE_CACHE_ROUTE}/{file_path.name}"

    @classmethod
    def cache_image_bytes(
        cls,
        cache_dir: Path,
        image_bytes: bytes,
        event_ts: int,
        mime_type: str = "",
    ) -> str:
        """直接缓存上传的二进制图片，并返回静态资源访问路径。"""
        if not image_bytes:
            return ""
        if len(image_bytes) > cls.MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="image 解码后大小不能超过 10MB")

        extension = cls.guess_image_extension_from_bytes(image_bytes, mime_type)
        cache_key = hashlib.sha256(image_bytes).hexdigest()
        file_name = f"{cache_key}{extension}"
        file_path = cache_dir / file_name
        if not file_path.exists():
            file_path.write_bytes(image_bytes)
        os.utime(file_path, (event_ts, event_ts))
        return f"{cls.IMAGE_CACHE_ROUTE}/{file_path.name}"

    @staticmethod
    def validate_day(day: str | None) -> str | None:
        """校验日期参数格式。"""
        if day is None:
            return None
        value = day.strip()
        if not value:
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise HTTPException(status_code=400, detail="day 格式必须为 YYYY-MM-DD")
        return value

    @classmethod
    def validate_source(cls, source: str | None) -> str:
        """校验来源筛选参数，仅允许 all、zzz、bgi、sr。"""
        value = (source or "all").strip().lower()
        if value not in cls.SUPPORTED_SOURCES:
            raise HTTPException(status_code=400, detail="source 仅支持 all、zzz、bgi、sr")
        return value

    @classmethod
    def create_item_record(
        cls,
        db_path: Path,
        cache_dir: Path,
        source: str,
        title: str,
        content: str,
        image: str,
        event_utc: datetime,
    ) -> int:
        """写入一条带来源标识的记录。"""
        normalized_source = cls.validate_source(source)
        if normalized_source == "all":
            raise HTTPException(status_code=400, detail="写入记录时 source 不能为 all")

        now_utc = datetime.now(timezone.utc)
        if event_utc < now_utc - timedelta(days=7):
            raise HTTPException(status_code=400, detail="仅允许写入最近 7 天内的数据")

        event_ts = int(event_utc.timestamp())
        day_text = event_utc.astimezone(cls.APP_TZ).date().isoformat()
        created_ts = int(now_utc.timestamp())
        cached_image = cls.cache_image(cache_dir, image, event_ts)

        with cls.get_conn(db_path) as conn:
            cls.prune_old_data(conn, cache_dir)
            cur = conn.execute(
                """
                INSERT INTO items(source, title, content, image, event_ts, day, created_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (normalized_source, title, content, cached_image, event_ts, day_text, created_ts),
            )
            conn.commit()

        return int(cur.lastrowid)
