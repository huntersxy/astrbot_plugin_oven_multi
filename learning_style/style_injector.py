import re
from typing import Any

from astrbot.api import logger

from .style_selector import StyleSelector


class StyleInjector:
    def __init__(self, data_manager, config: dict[str, Any]):
        self.data_manager = data_manager
        self.config = config
        self.style_selector = StyleSelector()

    def should_inject_style(self, session_id: str) -> bool:
        if not self.config.get("enable_style_injection", True):
            return False
        universal = self.data_manager.get_universal_for_session(session_id)
        contextual = self.data_manager.get_contextual_for_session(session_id)
        specific = self.data_manager.get_specific_for_session(session_id)
        return bool(universal) or bool(contextual) or bool(specific)

    def inject_style_to_prompt(self, session_id: str, original_system_prompt: str, user_message: str = "") -> str:
        if not self.should_inject_style(session_id):
            return original_system_prompt

        try:
            style_parts = []

            universal = self.data_manager.get_universal_for_session(session_id)
            if universal:
                contents = [t["content"] for t in universal]
                style_parts.append(self.style_selector.build_style_text("通用风格", contents))

            contextual = self.data_manager.get_contextual_for_session(session_id)
            if contextual:
                style_parts.append(self.style_selector.build_contextual_text(contextual))

            specific = self.data_manager.get_specific_for_session(session_id)
            matched = []
            for trait in specific:
                regex = trait.get("trigger_regex", "")
                content = trait.get("content", "")
                if regex and content and user_message:
                    try:
                        if re.search(regex, user_message):
                            matched.append(content)
                    except re.error:
                        continue

            if matched and user_message:
                style_parts.append(self.style_selector.build_style_text("当前话题相关说法", matched))

            if not style_parts:
                return original_system_prompt

            style_text = "；".join(style_parts)
            full_style_text = f"在回复时，请尽量采用以下风格特点：{style_text}"

            if not original_system_prompt.strip():
                return full_style_text

            new_prompt = f"{original_system_prompt}\n\n{full_style_text}"
            logger.debug(f"[烤箱-风格学习] 为会话 {session_id} 注入风格提示")
            return new_prompt

        except Exception as e:
            logger.error(f"[烤箱-风格学习] 注入风格时发生错误: {e}")
            return original_system_prompt

    def build_raw_style_text(self, session_id: str, user_message: str = "") -> str | None:
        """构建纯风格文本，不包装为系统提示格式。"""
        if not self.should_inject_style(session_id):
            return None

        try:
            style_parts = []

            universal = self.data_manager.get_universal_for_session(session_id)
            if universal:
                contents = [t["content"] for t in universal]
                style_parts.append(self.style_selector.build_style_text("通用风格", contents))

            contextual = self.data_manager.get_contextual_for_session(session_id)
            if contextual:
                style_parts.append(self.style_selector.build_contextual_text(contextual))

            specific = self.data_manager.get_specific_for_session(session_id)
            matched = []
            for trait in specific:
                regex = trait.get("trigger_regex", "")
                content = trait.get("content", "")
                if regex and content and user_message:
                    try:
                        if re.search(regex, user_message):
                            matched.append(content)
                    except re.error:
                        continue

            if matched and user_message:
                style_parts.append(self.style_selector.build_style_text("当前话题相关说法", matched))

            if not style_parts:
                return None

            return "；".join(style_parts)

        except Exception as e:
            logger.error(f"[烤箱-风格学习] 构建风格文本时发生错误: {e}")
            return None

    def get_style_summary(self, session_id: str) -> dict[str, Any]:
        universal = self.data_manager.get_universal_for_session(session_id)
        contextual = self.data_manager.get_contextual_for_session(session_id)
        specific = self.data_manager.get_specific_for_session(session_id)

        total = len(universal) + len(contextual) + len(specific)

        if total == 0:
            return {
                "has_styles": False,
                "total_styles": 0,
                "universal_count": 0,
                "contextual_count": 0,
                "specific_count": 0,
                "universal_preview": [],
                "contextual_preview": [],
                "specific_preview": [],
            }

        universal_preview = [t["content"] for t in universal[:3]]
        contextual_preview = [f"{t['scene']}\u2192{t['behavior']}" for t in contextual[:3]]
        specific_sorted = sorted(specific, key=lambda t: t.get("trigger_count", 0), reverse=True)
        specific_preview = [t["content"] for t in specific_sorted[:3]]

        return {
            "has_styles": True,
            "total_styles": total,
            "universal_count": len(universal),
            "contextual_count": len(contextual),
            "specific_count": len(specific),
            "universal_preview": universal_preview,
            "contextual_preview": contextual_preview,
            "specific_preview": specific_preview,
        }
