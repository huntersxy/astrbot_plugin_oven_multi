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
        self.embedding_provider = None
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

    async def perform_maintenance(self, verbose: bool = False) -> list[str]:
        """
        对所有有情境表征的会话执行表征合并和容量清理。

        参数:
            verbose: 是否收集并返回详细操作日志

        返回:
            详细日志行列表（verbose=True 时），timer 调用时为空列表
        """
        logs = []
        all_sessions = list(self.data_manager.contextual.keys())
        logs.append(f"📋 待处理会话: {len(all_sessions)} 个")

        total_merged_uni = 0
        total_merged_spec = 0
        total_remained = 0
        total_buffers = 0
        used_mode = "difflib"

        style_cfg = self.config.get("style_learning", {})
        embedding_threshold = float(
            style_cfg.get("embedding_threshold", 0.75)
        )

        for session_id in all_sessions:
            display = session_id.split("_")[-1] if "_" in session_id else session_id
            try:
                stats = await self.data_manager.merge_contextual_buffer(
                    session_id,
                    threshold=embedding_threshold,
                    embedding_provider=getattr(self, 'embedding_provider', None),
                )
                if stats.get("mode") == "embedding":
                    used_mode = "embedding"
                if stats["total_buffers"] == 0:
                    logs.append(f"  ⏭️  {display}: 无缓冲条目，跳过")
                else:
                    logs.append(
                        f"  🔄 {display}: "
                        f"缓冲 {stats['total_buffers']} 条 → "
                        f"通用 {stats['merged_to_universal']} / "
                        f"特定 {stats['merged_to_specific']} / "
                        f"滞留 {stats['remained']}"
                    )
                    if verbose and stats["details"]:
                        for d in stats["details"]:
                            logs.append(f"    {d}")
                total_buffers += stats["total_buffers"]
                total_merged_uni += stats["merged_to_universal"]
                total_merged_spec += stats["merged_to_specific"]
                total_remained += stats["remained"]
            except Exception as e:
                logs.append(f"  ❌ {display}: 出错 — {e}")
                logger.error(f"[烤箱-风格学习] 维护会话 {session_id} 时出错: {e}")

        await self.data_manager.force_save()
        summary = (
            f"✅ 维护完成: 共处理 {total_buffers} 条缓冲条目 → "
            f"合并到通用 {total_merged_uni} / 特定 {total_merged_spec} / "
            f"滞留 {total_remained}"
            f" | 模式: {used_mode}"
            f"{' (阈值 ' + str(embedding_threshold) + ')' if used_mode == 'embedding' else ''}"
        )
        logs.append(summary)
        logger.info(f"[烤箱-风格学习] {summary}")

        if verbose:
            return logs
        return []
