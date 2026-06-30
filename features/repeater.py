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
# Modified from astrbot_plugin_repetition by FengYing1314

import random
from collections import defaultdict

import astrbot.api.message_components as Comp


class Repeater:
    """消息复读功能

    检测连续相同消息并在达到阈值时复读或打断。
    """

    def __init__(self):
        self.last = defaultdict(str)
        self.count = defaultdict(int)

    def check(self, session_id: str, message, config: dict):
        """检查是否触发复读

        Args:
            session_id: 会话ID
            message: 消息组件列表
            config: 复读配置

        Returns:
            (类型, 内容) 元组，未触发返回 None
        """
        if not config.get("enabled"):
            return None
        if any(isinstance(m, Comp.Poke) for m in message):
            return None

        msg_id = str([str(m) for m in message])
        threshold = config.get("repeat_threshold", 2)

        if msg_id == self.last[session_id]:
            self.count[session_id] += 1
            if self.count[session_id] >= threshold:
                # 重置计数器，但保留 last，使下一条相同消息从 1 开始计数
                self.count[session_id] = 0
                if random.random() < config.get("break_spell_probability", 0.3):
                    return ("break", config.get("break_spell_text", "打断施法！"))
                else:
                    chain = [
                        Comp.Image.fromURL(m.url) if isinstance(m, Comp.Image) else m
                        for m in message
                    ]
                    return ("repeat", chain)
        else:
            self.count[session_id] = 1
        self.last[session_id] = msg_id
        return None
