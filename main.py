from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.bgi import create_bgi_router
from routes.sr import create_sr_router
from routes.zzz import create_zzz_router
from utils.system_utils import SystemUtils

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data.db")))
STATIC_DIR = BASE_DIR / "static"
CACHE_DIR = Path(os.getenv("IMAGE_CACHE_DIR", str(BASE_DIR / "image_cache")))

CACHE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AoiScriptMsgReply", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount(SystemUtils.IMAGE_CACHE_ROUTE, StaticFiles(directory=CACHE_DIR), name="image-cache")
app.include_router(create_zzz_router(DB_PATH, CACHE_DIR))
app.include_router(create_bgi_router(DB_PATH, CACHE_DIR))
app.include_router(create_sr_router(DB_PATH, CACHE_DIR))


@app.on_event("startup")
def on_startup() -> None:
    """应用启动时初始化数据库并清理过期数据。"""
    print("Aoi小葵正在启动")
    SystemUtils.init_db(DB_PATH, CACHE_DIR)
    with SystemUtils.get_conn(DB_PATH) as conn:
        SystemUtils.prune_old_data(conn, CACHE_DIR)


@app.get("/")
def index() -> FileResponse:
    """返回前端首页。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/days")
def get_days(source: str | None = None) -> dict:
    """根据来源筛选返回可选日期列表。"""
    selected_source = SystemUtils.validate_source(source)
    with SystemUtils.get_conn(DB_PATH) as conn:
        SystemUtils.prune_old_data(conn, CACHE_DIR)
        if selected_source == "all":
            rows = conn.execute("SELECT DISTINCT day FROM items ORDER BY day DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT day FROM items WHERE source = ? ORDER BY day DESC",
                (selected_source,),
            ).fetchall()
    return {"source": selected_source, "days": [row["day"] for row in rows]}


@app.get("/api/items")
def get_items(day: str | None = None, source: str | None = None) -> dict:
    """按日期和来源返回内容列表。"""
    selected_source = SystemUtils.validate_source(source)
    with SystemUtils.get_conn(DB_PATH) as conn:
        SystemUtils.prune_old_data(conn, CACHE_DIR)
        selected_day = SystemUtils.validate_day(day)

        if not selected_day:
            if selected_source == "all":
                row = conn.execute("SELECT day FROM items ORDER BY day DESC LIMIT 1").fetchone()
            else:
                row = conn.execute(
                    "SELECT day FROM items WHERE source = ? ORDER BY day DESC LIMIT 1",
                    (selected_source,),
                ).fetchone()
            if not row:
                return {"day": None, "source": selected_source, "items": []}
            selected_day = row["day"]

        if selected_source == "all":
            rows = conn.execute(
                """
                SELECT id, source, title, content, image, event_ts
                FROM items
                WHERE day = ?
                ORDER BY event_ts DESC, id DESC
                """,
                (selected_day,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, source, title, content, image, event_ts
                FROM items
                WHERE day = ? AND source = ?
                ORDER BY event_ts DESC, id DESC
                """,
                (selected_day, selected_source),
            ).fetchall()

    return {
        "day": selected_day,
        "source": selected_source,
        "items": [
            {
                "id": row["id"],
                "source": row["source"],
                "title": row["title"],
                "content": row["content"],
                "image": row["image"],
                "timestamp": SystemUtils.to_local_iso(row["event_ts"]),
            }
            for row in rows
        ],
    }
