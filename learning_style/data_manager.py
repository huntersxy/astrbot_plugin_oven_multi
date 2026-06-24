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

import asyncio
import json
import os
from typing import Any

from astrbot.api import logger


class DataManager:
    def __init__(self, data_dir: str, config: dict):
        self.data_dir = data_dir
        self.universal_file = os.path.join(data_dir, "universal.json")
        self.chat_history_file = os.path.join(data_dir, "chat_history.json")

        self.universal: dict[str, list[dict[str, Any]]] = {}
        self.chat_history: dict[str, list[dict[str, Any]]] = {}

        self.config = config

        self._ensure_data_dir()
        self.load_universal()
        self.load_chat_history()
        self.lock = asyncio.Lock()

        self._dirty_universal = False
        self._dirty_chat_history = False
        self._save_timer = None
        self._save_delay = 5.0

    def _ensure_data_dir(self):
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            logger.info(f"[烤箱-风格学习] 创建数据目录: {self.data_dir}")

    # ==================== 通用风格表征 ====================

    def load_universal(self):
        if os.path.exists(self.universal_file):
            try:
                with open(self.universal_file, encoding="utf-8") as f:
                    self.universal = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.error(f"[烤箱-风格学习] 加载通用表征文件失败: {e}")
                self.universal = {}
        else:
            self.universal = {}

    def get_universal_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.universal.get(session_id, [])

    def replace_universal(self, session_id: str, contents: list[str]):
        current_time = asyncio.get_running_loop().time()
        old_map = {}
        for trait in self.universal.get(session_id, []):
            old_map[trait["content"]] = trait

        new_traits = []
        for content in contents:
            if content in old_map:
                old = old_map[content]
                new_traits.append({
                    "content": content,
                    "proficiency": min(100, old.get("proficiency", 0) + 5),
                    "confirmed_rounds": old.get("confirmed_rounds", 0) + 1,
                    "last_updated": current_time,
                })
            else:
                new_traits.append({
                    "content": content,
                    "proficiency": 10,
                    "confirmed_rounds": 1,
                    "last_updated": current_time,
                })

        self.universal[session_id] = new_traits
        self._dirty_universal = True
        asyncio.create_task(self._schedule_save())

    async def save_universal(self):
        async with self.lock:
            try:
                with open(self.universal_file, "w", encoding="utf-8") as f:
                    json.dump(self.universal, f, ensure_ascii=False, indent=4)
                self._dirty_universal = False
            except OSError as e:
                logger.error(f"[烤箱-风格学习] 保存通用表征文件失败: {e}")

    # ==================== 聊天记录 ====================

    def load_chat_history(self):
        if os.path.exists(self.chat_history_file):
            try:
                with open(self.chat_history_file, encoding="utf-8") as f:
                    self.chat_history = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.error(f"[烤箱-风格学习] 加载聊天记录文件失败: {e}")
                self.chat_history = {}
        else:
            self.chat_history = {}

    async def save_chat_history(self):
        async with self.lock:
            try:
                with open(self.chat_history_file, "w", encoding="utf-8") as f:
                    json.dump(self.chat_history, f, ensure_ascii=False, indent=4)
                self._dirty_chat_history = False
            except OSError as e:
                logger.error(f"[烤箱-风格学习] 保存聊天记录文件失败: {e}")

    async def add_message_to_history(self, session_id: str, message: dict[str, Any]):
        if session_id not in self.chat_history:
            self.chat_history[session_id] = []
        self.chat_history[session_id].append(message)
        self._dirty_chat_history = True
        await self._schedule_save()

    def get_chat_history(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.chat_history.get(session_id, [])[-limit:]

    async def clear_chat_history(self, session_id: str):
        if session_id in self.chat_history:
            self.chat_history[session_id] = []
            self._dirty_chat_history = True
            await self._schedule_save()

    # ==================== 公共保存逻辑 ====================

    async def _schedule_save(self):
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = asyncio.create_task(self._delayed_save())

    async def _delayed_save(self):
        await asyncio.sleep(self._save_delay)
        if self._dirty_universal:
            await self.save_universal()
        if self._dirty_chat_history:
            await self.save_chat_history()
        self._save_timer = None

    async def force_save(self):
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        if self._dirty_universal:
            await self.save_universal()
        if self._dirty_chat_history:
            await self.save_chat_history()
