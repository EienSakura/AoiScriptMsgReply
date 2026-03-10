from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest

from utils.sr_utils import SRUtils
from utils.system_utils import SystemUtils


def create_sr_router(db_path: Path, cache_dir: Path) -> APIRouter:
    """创建 SR 路由，接收 March7th Assistant 的 multipart 表单通知。"""

    router = APIRouter(tags=["sr"])

    @router.post("/api/sr")
    async def receive_sr_webhook(request: FastAPIRequest) -> dict:
        """接收 SR 请求，解析表单字段、打印摘要，并写入数据库。"""
        content_type = request.headers.get("content-type", "")

        if content_type.startswith("multipart/form-data") or content_type.startswith(
            "application/x-www-form-urlencoded"
        ):
            form = await request.form()
            title, content, event_utc = SRUtils.normalize_form_payload(form)

            upload = form.get("image")
            cached_image = ""
            summary = {
                "content_type": content_type,
                "fields": list(form.keys()),
                "title": title,
                "content": content,
                "has_image": False,
                "timestamp": event_utc.astimezone(SystemUtils.APP_TZ).isoformat(),
            }

            if upload is not None:
                filename = getattr(upload, "filename", "") or ""
                file_content_type = getattr(upload, "content_type", "") or ""
                image_bytes = await upload.read()
                if image_bytes:
                    cached_image = SystemUtils.cache_image_bytes(
                        cache_dir,
                        image_bytes,
                        int(event_utc.timestamp()),
                        file_content_type,
                    )
                    summary["has_image"] = True
                    summary["image_filename"] = filename
                    summary["image_content_type"] = file_content_type
                    summary["image_size"] = len(image_bytes)

            item_id = SystemUtils.create_item_record(
                db_path,
                cache_dir,
                SRUtils.SOURCE,
                title,
                content,
                cached_image,
                event_utc,
            )
            return {
                "ok": True,
                "id": item_id,
                "source": SRUtils.SOURCE,
                "message": "SR 请求已接收并写入",
                "received": summary,
            }

        raw_bytes = await request.body()
        request_summary = SRUtils.build_request_summary(raw_bytes, content_type)

        raise HTTPException(
            status_code=400,
            detail={
                "message": "SR 当前仅支持 multipart/form-data 或表单请求",
                "received": request_summary,
            },
        )

    return router
