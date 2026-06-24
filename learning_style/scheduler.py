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


DEFAULT_ANALYSIS_INTERVAL = 21600  # 6 小时


class Scheduler:
    def __init__(self, data_manager: DataManager, learning_manager: LearningManager, config: dict):
        self.data_manager = data_manager
        self.learning_manager = learning_manager
        self.config = config
        self.analysis_task: asyncio.Task | None = None
        self.is_running = False

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.analysis_task = asyncio.create_task(self._run_analysis())
            logger.info("[烤箱-风格学习] 定时分析任务已启动（每 6 小时执行一次）。")

    async def stop(self):
        if self.is_running:
            self.is_running = False
            if self.analysis_task:
                self.analysis_task.cancel()
                try:
                    await asyncio.gather(self.analysis_task, return_exceptions=True)
                except asyncio.CancelledError:
                    pass
            logger.info("[烤箱-风格学习] 定时分析任务已停止。")

    async def _run_analysis(self):
        style_cfg = self.config.get("style_learning", {})
        interval = int(style_cfg.get("analysis_interval_seconds", DEFAULT_ANALYSIS_INTERVAL))
        provider_id = (style_cfg.get("style_provider_id", "") or "").strip()

        logger.info(
            f"[烤箱-风格学习] 定时分析间隔: {interval // 3600} 小时 "
            f"({interval} 秒)"
        )
        while self.is_running:
            await asyncio.sleep(interval)
            logger.info("[烤箱-风格学习] 开始执行周期性聊天记录分析...")
            all_sessions = list(self.data_manager.chat_history.keys())
            for session_id in all_sessions:
                try:
                    await self.learning_manager.analyze_and_learn(
                        session_id, provider_id=provider_id
                    )
                    await asyncio.sleep(0)
                except Exception as e:
                    logger.error(
                        f"[烤箱-风格学习] 分析会话 {session_id} 时出错: {e}"
                    )
            await self.data_manager.force_save()
