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
# 参考 astrbot_plugin_self_learning（黑话与风格分离）与
# astrbot_plugin_angel_memory（记忆筛选/衰减）的设计。

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

# 特征类别
CATEGORY_STABLE = "stable"        # 跨场景稳定的风格（语气、句式、措辞习惯）
CATEGORY_SITUATIONAL = "situational"  # 有明确触发场景的表达/梗（如 666/233）


class DataManager:
    """风格数据持久化：通用风格特征 + 聊天记录，均以 JSON 存储，延迟合并写盘。"""

    def __init__(self, data_dir: str | Path, config: dict):
        self.data_dir = Path(data_dir)
        self.config = config
        self.universal: dict[str, list[dict[str, Any]]] = {}
        self.chat_history: dict[str, list[dict[str, Any]]] = {}
        self.lock = asyncio.Lock()
        self._version = 0
        self._dirty_universal = False
        self._dirty_chat_history = False
        self._save_timer: asyncio.Task | None = None
        self._save_delay = 5.0

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.universal = self._load("universal.json")
        self.chat_history = self._load("chat_history.json")

    def _load(self, filename: str) -> dict:
        path = self.data_dir / filename
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"[烤箱-风格学习] 加载 {filename} 失败: {e}")
            return {}

    # ── 通用风格特征 ─────────────────────────────────────────────────────

    def get_universal_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.universal.get(session_id, [])

    def get_version(self) -> int:
        """数据版本号：特征变更时递增，供注入缓存失效使用。"""
        return self._version

    def get_all_universal_except(self, session_id: str) -> dict[str, list[dict[str, Any]]]:
        """获取除指定会话外所有会话的通用风格（用于跨群注入）。"""
        return {
            sid: traits
            for sid, traits in self.universal.items()
            if sid != session_id and traits
        }

    def replace_universal(
        self,
        session_id: str,
        contents: list[str],
        situational: list[dict[str, Any]] | None = None,
    ):
        """用新提炼的风格特征替换旧特征，保留既有特征的熟练度累积。

        Args:
            contents: 稳定风格特征内容列表。
            situational: 场景化表达列表，元素为 {"content", "context"}。
        """
        now = time.time()
        old_map = {t["content"]: t for t in self.universal.get(session_id, [])}

        def trait(content: str, category: str, context: str = "") -> dict[str, Any]:
            old = old_map.get(content, {})
            return {
                "content": content,
                "category": category,
                "context": context,
                "proficiency": min(100, int(old.get("proficiency", 0) or 0) + 5),
                "confirmed_rounds": int(old.get("confirmed_rounds", 0) or 0) + 1,
                "last_seen": now,
                "last_updated": now,
            }

        new_traits = [trait(c, CATEGORY_STABLE) for c in contents]
        for item in situational or []:
            content = (item.get("content") or "").strip()
            if content:
                new_traits.append(
                    trait(content, CATEGORY_SITUATIONAL, str(item.get("context", "") or "").strip())
                )
        self.universal[session_id] = new_traits
        self._version += 1
        self._dirty_universal = True
        asyncio.create_task(self._schedule_save())

    async def clear_universal(self, session_id: str):
        self.universal.pop(session_id, None)
        self._version += 1
        self._dirty_universal = True
        await self._schedule_save()

    # ── 聊天记录 ─────────────────────────────────────────────────────────

    async def add_message_to_history(self, session_id: str, message: dict[str, Any]):
        self.chat_history.setdefault(session_id, []).append(message)
        self._dirty_chat_history = True
        await self._schedule_save()

    def get_chat_history(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.chat_history.get(session_id, [])[-limit:]

    async def clear_chat_history(self, session_id: str):
        self.chat_history.pop(session_id, None)
        self._dirty_chat_history = True
        await self._schedule_save()

    # ── 保存逻辑（延迟 5 秒合并写盘）────────────────────────────────────

    async def _schedule_save(self):
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = asyncio.create_task(self._delayed_save())

    async def _delayed_save(self):
        await asyncio.sleep(self._save_delay)
        await self.force_save()
        self._save_timer = None

    async def force_save(self):
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        if self._dirty_universal:
            await self._write("universal.json", self.universal)
            self._dirty_universal = False
        if self._dirty_chat_history:
            await self._write("chat_history.json", self.chat_history)
            self._dirty_chat_history = False

    async def _write(self, filename: str, data: dict):
        async with self.lock:
            try:
                (self.data_dir / filename).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError as e:
                logger.error(f"[烤箱-风格学习] 保存 {filename} 失败: {e}")
