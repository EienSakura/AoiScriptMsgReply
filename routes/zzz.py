from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest

from utils.system_utils import SystemUtils
from utils.zzz_utils import ZZZUtils


def create_zzz_router(db_path: Path, cache_dir: Path) -> APIRouter:
    """创建 ZZZ 业务路由，仅负责接收 `/api/zzz` 写入请求。"""

    router = APIRouter(tags=["zzz"])

    async def read_json_payload(request: FastAPIRequest) -> dict:
        """读取并校验 ZZZ 请求体。"""
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="请求体必须是合法 JSON") from exc
        return SystemUtils.ensure_json_object(payload)

    @router.post("/api/zzz")
    async def create_item(request: FastAPIRequest) -> dict:
        """接收 ZZZ 上报内容并写入数据库。"""
        payload = await read_json_payload(request)
        title, content, image, event_utc = ZZZUtils.normalize_payload(payload)
        item_id = SystemUtils.create_item_record(
            db_path,
            cache_dir,
            ZZZUtils.SOURCE,
            title,
            content,
            image,
            event_utc,
        )
        return {"ok": True, "id": item_id, "source": ZZZUtils.SOURCE}

    return router
