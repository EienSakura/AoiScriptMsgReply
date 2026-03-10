from __future__ import annotations

from datetime import datetime, timezone

from utils.system_utils import SystemUtils


class BGIUtils:
    """BGI 工具类，负责把 BetterGI Webhook 数据转成统一消息结构。"""

    SOURCE = "bgi"
    EVENT_LABELS = {
        "notify.test": "测试通知",
        "domain.reward": "自动秘境奖励",
        "domain.start": "自动秘境启动",
        "domain.end": "自动秘境结束",
        "domain.retry": "自动秘境重试",
        "task.cancel": "任务启动",
        "task.error": "任务错误",
        "group.start": "配置组启动",
        "group.end": "配置组结束",
        "dragon.start": "一条龙启动",
        "dragon.end": "一条龙结束",
        "tcg.start": "七圣召唤启动",
        "tcg.end": "七圣召唤结束",
        "album.start": "自动音游专辑启动",
        "album.end": "自动音游专辑结束",
        "album.error": "自动音游专辑错误",
    }

    @classmethod
    def get_event_label(cls, event_name: str) -> str:
        """把 BetterGI 的事件代码转换为中文标题。"""
        return cls.EVENT_LABELS.get(event_name, event_name or "BGI 通知")

    @classmethod
    def normalize_payload(cls, payload: dict) -> tuple[str, str, str, datetime]:
        """从 BetterGI Webhook 中提取可入库的统一数据。"""
        event_name = SystemUtils.normalize_optional_text(payload.get("event"), "event", 100) or "notify.test"
        result_text = SystemUtils.normalize_optional_text(payload.get("result"), "result", 200)
        message_text = SystemUtils.normalize_optional_text(payload.get("message"), "message", 5000)
        title_text = SystemUtils.normalize_optional_text(payload.get("title"), "title", 200)
        content_text = SystemUtils.normalize_optional_text(payload.get("content"), "content", 5000)
        screenshot_text = SystemUtils.normalize_optional_text(
            payload.get("screenshot"),
            "screenshot",
            SystemUtils.MAX_IMAGE_BASE64_CHARS,
        )
        send_from = SystemUtils.normalize_optional_text(payload.get("send_from"), "send_from", 200) or SystemUtils.normalize_optional_text(
            payload.get("from"), "from", 200
        )
        send_to = SystemUtils.normalize_recipient_field(payload.get("send_to"), "send_to") or SystemUtils.normalize_recipient_field(
            payload.get("to"), "to"
        )
        send_to_group = SystemUtils.normalize_recipient_field(
            payload.get("send_to_group"),
            "send_to_group",
        ) or SystemUtils.normalize_recipient_field(payload.get("to_group"), "to_group")

        raw_timestamp = payload.get("timestamp")
        event_utc = (
            SystemUtils.parse_timestamp(raw_timestamp)
            if raw_timestamp not in (None, "")
            else datetime.now(SystemUtils.APP_TZ).astimezone(timezone.utc)
        )

        event_label = cls.get_event_label(event_name)
        title = title_text or event_label
        content_lines: list[str] = []

        if message_text:
            content_lines.append(message_text)
        if content_text and content_text != message_text:
            content_lines.append(content_text)
        content_lines.append(f"事件：{event_label}")
        if result_text:
            content_lines.append(f"结果：{result_text}")
        if send_from:
            content_lines.append(f"发送来源：{send_from}")
        if send_to:
            content_lines.append(f"发送对象：{send_to}")
        if send_to_group:
            content_lines.append(f"发送群组：{send_to_group}")

        return title, "\n".join(content_lines), screenshot_text, event_utc
