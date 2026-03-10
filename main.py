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

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data.db")))
STATIC_DIR = BASE_DIR / "static"
CACHE_DIR = Path(os.getenv("IMAGE_CACHE_DIR", str(BASE_DIR / "image_cache")))
IMAGE_CACHE_ROUTE = "/image-cache"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BASE64_CHARS = 16 * 1024 * 1024


def load_app_timezone() -> timezone:
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        # Windows 某些 Python 环境缺少系统时区库时，退回到固定 UTC+8。
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


APP_TZ = load_app_timezone()

CACHE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Simple Message Board", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount(IMAGE_CACHE_ROUTE, StaticFiles(directory=CACHE_DIR), name="image-cache")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                image TEXT NOT NULL,
                event_ts INTEGER NOT NULL,
                day TEXT NOT NULL,
                created_ts INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_day ON items(day)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_event_ts ON items(event_ts)"
        )
        conn.commit()


def parse_timestamp(raw_value: object) -> datetime:
    if isinstance(raw_value, (int, float)):
        timestamp_value = float(raw_value)
        if timestamp_value > 10**12:
            timestamp_value = timestamp_value / 1000
        return datetime.fromtimestamp(timestamp_value, tz=timezone.utc)

    if raw_value is None:
        raise HTTPException(status_code=400, detail="timestamp 不能为空")

    value = str(raw_value).strip()
    if not value:
        raise HTTPException(status_code=400, detail="timestamp 不能为空")
    if value.isdigit():
        return parse_timestamp(int(value))
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
        dt = dt.replace(tzinfo=APP_TZ)
    return dt.astimezone(timezone.utc)


def prune_old_data(conn: sqlite3.Connection) -> None:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    conn.execute("DELETE FROM items WHERE event_ts < ?", (cutoff,))
    conn.commit()
    prune_old_cached_images(cutoff)


def prune_old_cached_images(cutoff_ts: int) -> None:
    if not CACHE_DIR.exists():
        return
    for file_path in CACHE_DIR.iterdir():
        if not file_path.is_file():
            continue
        try:
            if int(file_path.stat().st_mtime) < cutoff_ts:
                file_path.unlink(missing_ok=True)
        except OSError:
            continue


def to_local_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(APP_TZ).isoformat()


def normalize_text_field(data: dict, field_name: str, max_length: int, required: bool = True) -> str:
    raw_value = data.get(field_name, "")
    if raw_value is None:
        raw_value = ""
    value = str(raw_value).strip()
    if required and not value:
        raise HTTPException(status_code=400, detail=f"{field_name} 不能为空")
    if len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"{field_name} 长度不能超过 {max_length}")
    return value


def guess_image_extension_from_mime(mime_type: str) -> str | None:
    normalized = mime_type.split(";")[0].strip().lower()
    if not normalized.startswith("image/"):
        return None
    guessed = mimetypes.guess_extension(normalized)
    if guessed == ".jpe":
        return ".jpg"
    return guessed


def guess_image_extension_from_bytes(image_bytes: bytes, mime_type: str = "") -> str:
    guessed = guess_image_extension_from_mime(mime_type)
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


def decode_base64_image(image_value: str) -> tuple[bytes, str]:
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
    if len(encoded) > MAX_IMAGE_BASE64_CHARS:
        raise HTTPException(status_code=400, detail="image 数据过大")

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="image 不是合法的 base64 图片数据") from exc

    if not image_bytes:
        return b"", ""
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="image 解码后大小不能超过 10MB")

    return image_bytes, guess_image_extension_from_bytes(image_bytes, mime_type)


def cache_image(image_url: str, event_ts: int) -> str:
    image_value = image_url.strip()
    if not image_value:
        return ""
    if image_value.startswith(IMAGE_CACHE_ROUTE):
        return image_value

    image_bytes, extension = decode_base64_image(image_value)
    if not image_bytes:
        return ""

    cache_key = hashlib.sha256(image_bytes).hexdigest()
    file_name = f"{cache_key}{extension}"
    file_path = CACHE_DIR / file_name
    if not file_path.exists():
        file_path.write_bytes(image_bytes)
    os.utime(file_path, (event_ts, event_ts))
    return f"{IMAGE_CACHE_ROUTE}/{file_name}"


def validate_day(day: str | None) -> str | None:
    if day is None:
        return None
    value = day.strip()
    if not value:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise HTTPException(status_code=400, detail="day 格式必须为 YYYY-MM-DD")
    return value


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    with get_conn() as conn:
        prune_old_data(conn)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/items")
async def create_item(request: FastAPIRequest) -> dict:
    try:
        payload = await request.json()
        print(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是合法 JSON") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    title = normalize_text_field(payload, "title", 200, required=True)
    content = normalize_text_field(payload, "content", 5000, required=True)
    image = normalize_text_field(payload, "image", MAX_IMAGE_BASE64_CHARS, required=False)
    event_utc = parse_timestamp(payload.get("timestamp"))
    now_utc = datetime.now(timezone.utc)
    if event_utc < now_utc - timedelta(days=7):
        raise HTTPException(status_code=400, detail="仅允许写入最近 7 天内的数据")

    event_ts = int(event_utc.timestamp())
    day_text = event_utc.astimezone(APP_TZ).date().isoformat()
    created_ts = int(now_utc.timestamp())
    cached_image = cache_image(image, event_ts)

    with get_conn() as conn:
        prune_old_data(conn)
        cur = conn.execute(
            """
            INSERT INTO items(title, content, image, event_ts, day, created_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title, content, cached_image, event_ts, day_text, created_ts),
        )
        conn.commit()

    return {"ok": True, "id": cur.lastrowid}


@app.get("/api/days")
def get_days() -> dict:
    with get_conn() as conn:
        prune_old_data(conn)
        rows = conn.execute(
            "SELECT DISTINCT day FROM items ORDER BY day DESC"
        ).fetchall()
    return {"days": [row["day"] for row in rows]}


@app.get("/api/items")
def get_items(day: str | None = None) -> dict:
    with get_conn() as conn:
        prune_old_data(conn)
        selected_day = validate_day(day)
        if not selected_day:
            row = conn.execute(
                "SELECT day FROM items ORDER BY day DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {"day": None, "items": []}
            selected_day = row["day"]

        rows = conn.execute(
            """
            SELECT id, title, content, image, event_ts
            FROM items
            WHERE day = ?
            ORDER BY event_ts DESC, id DESC
            """,
            (selected_day,),
        ).fetchall()

    return {
        "day": selected_day,
        "items": [
            {
                "id": row["id"],
                "title": row["title"],
                "content": row["content"],
                "image": row["image"],
                "timestamp": to_local_iso(row["event_ts"]),
            }
            for row in rows
        ],
    }
