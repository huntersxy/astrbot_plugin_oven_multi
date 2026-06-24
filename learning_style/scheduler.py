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

from astrbot.api import logger

from .data_manager import DataManager
from .learning_manager import LearningManager


class Scheduler:
    def __init__(self, data_manager: DataManager, learning_manager: LearningManager, config: dict):
        self.data_manager = data_manager
        self.learning_manager = learning_manager
        self.config = config
        self.analysis_task: asyncio.Task | None = None
        self.maintenance_task: asyncio.Task | None = None
        self.is_running = False

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.analysis_task = asyncio.create_task(self._run_analysis())
            self.maintenance_task = asyncio.create_task(self._run_maintenance())
            logger.info("[烤箱-风格学习] 定时任务已启动。")

    async def stop(self):
        if self.is_running:
            self.is_running = False
            tasks = []
            if self.analysis_task:
                self.analysis_task.cancel()
                tasks.append(self.analysis_task)
            if self.maintenance_task:
                self.maintenance_task.cancel()
                tasks.append(self.maintenance_task)

            if tasks:
                try:
                    await asyncio.gather(*tasks, return_exceptions=True)
                except asyncio.CancelledError:
                    pass

            logger.info("[烤箱-风格学习] 定时任务已停止。")

    async def _run_analysis(self):
        analysis_interval = self.config.get("analysis_interval_seconds", 3600)
        style_cfg = self.config.get("style_learning", {})
        provider_id = (style_cfg.get("style_provider_id", "") or "").strip()
        while self.is_running:
            await asyncio.sleep(analysis_interval)
            logger.info("[烤箱-风格学习] 开始执行周期性聊天记录分析...")
            all_sessions = list(self.data_manager.chat_history.keys())
            for session_id in all_sessions:
                try:
                    await self.learning_manager.analyze_and_learn(session_id, provider_id=provider_id)
                    await asyncio.sleep(0)
                except Exception as e:
                    logger.error(f"[烤箱-风格学习] 分析会话 {session_id} 时出错: {e}")
            await self.data_manager.force_save()

    async def _run_maintenance(self):
        maintenance_interval = self.config.get("maintenance_interval_seconds", 86400)
        while self.is_running:
            await asyncio.sleep(maintenance_interval)
            logger.info("[烤箱-风格学习] 开始执行周期性风格维护...")
            await self.perform_maintenance()
            await asyncio.sleep(0)

    async def perform_maintenance(self):
        """对所有有情境表征的会话执行表征合并和容量清理。"""
        all_sessions = list(self.data_manager.contextual.keys())
        for session_id in all_sessions:
            try:
                self.data_manager.merge_contextual_buffer(session_id)
            except Exception as e:
                logger.error(f"[烤箱-风格学习] 维护会话 {session_id} 时出错: {e}")

        await self.data_manager.force_save()
        logger.info("[烤箱-风格学习] 风格维护完成（情境缓冲合并）。")
