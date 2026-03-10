from __future__ import annotations

from datetime import datetime

from utils.system_utils import SystemUtils


class ZZZUtils:
    """ZZZ 工具类，负责把 ZZZ 的请求体规范化为统一消息结构。"""

    SOURCE = "zzz"

    @classmethod
    def normalize_payload(cls, payload: dict) -> tuple[str, str, str, datetime]:
        """从 ZZZ 请求中提取标题、正文、图片和事件时间。"""
        title = SystemUtils.normalize_text_field(payload, "title", 200, required=True)
        content = SystemUtils.normalize_text_field(payload, "content", 5000, required=True)
        image = SystemUtils.normalize_text_field(
            payload,
            "image",
            SystemUtils.MAX_IMAGE_BASE64_CHARS,
            required=False,
        )
        event_utc = SystemUtils.parse_timestamp(payload.get("timestamp"))
        return title, content, image, event_utc
