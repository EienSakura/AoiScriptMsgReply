from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest

from utils.bgi_utils import BGIUtils
from utils.system_utils import SystemUtils


def create_bgi_router(db_path: Path, cache_dir: Path) -> APIRouter:
    """创建 BGI 业务路由，仅负责接收 BetterGI Webhook。"""

    router = APIRouter(tags=["bgi"])

    async def read_json_payload(request: FastAPIRequest) -> dict:
        """读取并校验 BGI 请求体。"""
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="请求体必须是合法 JSON") from exc
        return SystemUtils.ensure_json_object(payload)

    @router.post("/api/bgi")
    async def receive_bgi_webhook(request: FastAPIRequest) -> dict:
        """接收 BetterGI Webhook 并写入数据库。"""
        payload = await read_json_payload(request)
        title, content, image, event_utc = BGIUtils.normalize_payload(payload)
        item_id = SystemUtils.create_item_record(
            db_path,
            cache_dir,
            BGIUtils.SOURCE,
            title,
            content,
            image,
            event_utc,
        )
        return {"ok": True, "id": item_id, "source": BGIUtils.SOURCE}

    return router
