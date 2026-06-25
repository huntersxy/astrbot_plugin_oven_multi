# Copyright (C) 2026 汐兮雨
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Modified from astrbot_plugin_iearning_style (AGPL-3.0) by qa296

from typing import Any

from astrbot.api import logger

from .style_selector import StyleSelector


class StyleInjector:
    def __init__(self, data_manager, config: dict[str, Any]):
        self.data_manager = data_manager
        self.config = config
        self.style_selector = StyleSelector()
        self._read_config()

    def _read_config(self):
        scfg = self.config.get("style_learning", {})
        self._max_universal = int(scfg.get("max_universal_inject", 5))

    def should_inject_style(self, session_id: str) -> bool:
        if not self.config.get("enable_style_injection", True):
            return False
        universal = self.data_manager.get_universal_for_session(session_id)
        return bool(universal)

    def inject_style_to_prompt(self, session_id: str, original_system_prompt: str, user_message: str = "") -> str:
        if not self.should_inject_style(session_id):
            return original_system_prompt

        try:
            universal = self._top_universal(session_id)
            if not universal:
                return original_system_prompt

            style_text = self.style_selector.build_style_text("通用风格", universal)
            full_style_text = f"在回复时，请尽量采用以下风格特点：{style_text}"

            if not original_system_prompt.strip():
                return full_style_text

            logger.debug(
                f"[烤箱-风格学习] 注入 {len(universal)} 条通用风格 | 会话: {session_id}"
            )
            return f"{original_system_prompt}\n\n{full_style_text}"

        except Exception as e:
            logger.error(f"[烤箱-风格学习] 注入风格时发生错误: {e}")
            return original_system_prompt

    def _top_universal(self, session_id: str) -> list[str]:
        traits = self.data_manager.get_universal_for_session(session_id)
        if not traits:
            return []
        # 先按熟练度降序取 top-N，再按时间升序排列，保证内容不变时顺序稳定
        traits.sort(key=lambda t: t.get("proficiency", 0), reverse=True)
        selected = sorted(
            traits[:self._max_universal],
            key=lambda t: t.get("last_updated", 0),
        )
        return [t["content"] for t in selected]

    def build_raw_style_text(self, session_id: str, user_message: str = "") -> str | None:
        if not self.should_inject_style(session_id):
            return None
        try:
            universal = self._top_universal(session_id)
            if not universal:
                return None
            return self.style_selector.build_style_text("通用风格", universal)
        except Exception as e:
            logger.error(f"[烤箱-风格学习] 构建风格文本时发生错误: {e}")
            return None

    def build_injection_text(self, session_id: str) -> str | None:
        """构建注入到 extra_user_content_parts 的完整文本。"""
        style_text = self.build_raw_style_text(session_id)
        if not style_text:
            return None
        return (
            "<style_guidelines>\n"
            "在回复时，请尽量采用以下风格特点："
            f"{style_text}\n"
            "</style_guidelines>"
        )

    def get_style_summary(self, session_id: str) -> dict[str, Any]:
        universal = self.data_manager.get_universal_for_session(session_id)
        total = len(universal)

        if total == 0:
            return {
                "has_styles": False,
                "total_styles": 0,
                "universal_count": 0,
                "universal_preview": [],
            }

        universal_preview = [t["content"] for t in universal[:3]]

        return {
            "has_styles": True,
            "total_styles": total,
            "universal_count": len(universal),
            "universal_preview": universal_preview,
        }
