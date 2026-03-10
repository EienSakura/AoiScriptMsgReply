from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from utils.system_utils import SystemUtils


class SRUtils:
    """SR 工具类，负责解析并整理 March7th Assistant 的表单请求内容。"""

    SOURCE = "sr"

    @staticmethod
    def build_request_summary(raw_bytes: bytes, content_type: str) -> dict:
        """把原始请求体转换为便于查看的请求摘要。"""
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        parsed_json: object = None
        parse_error = ""

        if raw_text.strip():
            try:
                parsed_json = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                parse_error = str(exc)

        return {
            "content_type": content_type,
            "content_length": len(raw_bytes),
            "raw_body": raw_text,
            "json_body": parsed_json,
            "json_error": parse_error,
        }

    @classmethod
    def normalize_form_payload(cls, form_data: dict) -> tuple[str, str, datetime]:
        """从 SR 表单请求中提取标题、内容和事件时间。"""
        title = SystemUtils.normalize_optional_text(form_data.get("title"), "title", 200)
        content = SystemUtils.normalize_optional_text(form_data.get("content"), "content", 5000)
        timestamp_value = form_data.get("timestamp")

        if not title:
            title = "三月七小助手"
        if not content:
            raise HTTPException(status_code=400, detail="content 不能为空")

        event_utc = (
            SystemUtils.parse_timestamp(timestamp_value)
            if timestamp_value not in (None, "")
            else datetime.now(SystemUtils.APP_TZ).astimezone(timezone.utc)
        )
        return title, content, event_utc
