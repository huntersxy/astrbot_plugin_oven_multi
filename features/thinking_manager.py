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
# Modified from astrbot_plugin_iamthinking (AGPL-3.0) by sssn-tech

from astrbot.api import logger


class ThinkingManager:
    """思考表情管理

    在 LLM 请求等待期间发送表情回应，完成后移除。
    """

    def is_aiocqhttp(self, event) -> bool:
        """检查是否为 aiocqhttp 平台的群消息"""
        return (
            getattr(event, "platform_meta", None)
            and event.get_platform_name() == "aiocqhttp"
            and bool(event.get_group_id())
        )

    async def emoji(self, event, msg_id: int, ids: list, set_: bool):
        """设置或取消表情回应

        Args:
            event: 消息事件
            msg_id: 消息ID
            ids: 表情ID列表
            set_: True 为设置，False 为取消
        """
        if not ids:
            return
        bot = getattr(event, "bot", None)
        if bot and hasattr(bot, "set_msg_emoji_like"):
            for eid in set(ids):
                try:
                    await bot.set_msg_emoji_like(
                        message_id=msg_id, emoji_id=eid, set=set_
                    )
                except Exception:
                    pass
