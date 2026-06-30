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
    """风格注入器，支持跨群风格和嵌入向量选择。"""

    def __init__(self, data_manager, config: dict[str, Any], context=None):
        self.data_manager = data_manager
        self.config = config
        self.context = context
        self.style_selector = StyleSelector()
        self._read_config()

    def _read_config(self):
        scfg = self.config.get("style_learning", {})
        self._max_universal = int(scfg.get("max_universal_inject", 5))
        self._enable_cross_group = bool(scfg.get("enable_cross_group", False))
        self._enable_emb = bool(scfg.get("enable_emb_style_selection", True))
        self._max_global = int(scfg.get("max_global_styles", 3))
        self._debug_mode = bool(scfg.get("debug_mode", False))
        self._embedding_provider_id = str(scfg.get("embedding_provider_id", "")).strip()

    def should_inject_style(self, session_id: str) -> bool:
        scfg = self.config.get("style_learning", {})
        if not scfg.get("enable_style_injection", True):
            return False
        # 只要本群有风格或跨群有风格就注入
        local_universal = self.data_manager.get_universal_for_session(session_id)
        if local_universal:
            return True
        if self._enable_cross_group:
            all_others = self.data_manager.get_all_universal_except(session_id)
            if any(v for v in all_others.values()):
                return True
        return False

    def _resolve_embedding_provider(self):
        """解析嵌入向量 Provider，参考 enhance_mode 的实现。

        优先使用配置的 embedding_provider_id，否则自动选择第一个可用的 EmbeddingProvider。
        """
        if not self.context:
            return None

        # 尝试从配置中获取指定的 embedding provider
        if self._embedding_provider_id:
            provider = self.context.get_provider_by_id(self._embedding_provider_id)
            if provider:
                # 检查是否为 EmbeddingProvider 类型
                try:
                    from astrbot.core.provider.provider import EmbeddingProvider
                    if isinstance(provider, EmbeddingProvider):
                        return provider
                except ImportError:
                    pass
                # 兼容：检查是否有 get_embedding 方法
                if hasattr(provider, "get_embedding") and callable(provider.get_embedding):
                    return provider
            logger.warning(
                f"[烤箱-风格学习] 配置的 embedding_provider_id 无效: {self._embedding_provider_id}"
            )

        # 回退：使用第一个可用的 EmbeddingProvider
        try:
            all_embedding_providers = self.context.get_all_embedding_providers()
            if all_embedding_providers:
                return all_embedding_providers[0]
        except (AttributeError, Exception):
            pass

        return None

    # ── 暴露给外部调用的注入方法 ──

    def inject_style_to_prompt(
        self,
        session_id: str,
        original_system_prompt: str,
        user_message: str = "",
    ) -> str:
        """（同步）注入风格到 system prompt —— 保留兼容但不再主动使用。"""
        if not self.should_inject_style(session_id):
            return original_system_prompt
        universal = self._top_local(session_id)
        if not universal:
            return original_system_prompt
        style_text = self.style_selector.build_style_text(universal)
        full_text = f"在回复时，请尽量采用以下风格特点：{style_text}"
        if not original_system_prompt.strip():
            return full_text
        logger.debug(
            f"[烤箱-风格学习] 注入 {len(universal)} 条通用风格 | 会话: {session_id}"
        )
        return f"{original_system_prompt}\n\n{full_text}"

    def build_raw_style_text(self, session_id: str, user_message: str = "") -> str | None:
        """（同步，旧接口）构建纯风格文本。"""
        local = self._top_local(session_id)
        if not local:
            return None
        return self.style_selector.build_style_text(local)

    async def build_injection_text(
        self,
        session_id: str,
        user_message: str = "",
        provider=None,
    ) -> str | None:
        """构建注入到 extra_user_content_parts 的完整文本。

        Args:
            session_id: 当前会话 ID。
            user_message: 当前用户消息（用作嵌入查询）。
            provider: LLM Provider 实例（已弃用，保留兼容）。

        Returns:
            注入文本字符串，无需注入则返回 None。
        """
        if not self.should_inject_style(session_id):
            return None

        # 解析嵌入 Provider（优先使用专用 EmbeddingProvider）
        embedding_provider = self._resolve_embedding_provider() if self._enable_emb else None

        # 1. 获取本地风格
        local_traits = self.data_manager.get_universal_for_session(session_id)

        # 2. 获取全局（跨群）风格
        global_traits: list[dict] = []
        if self._enable_cross_group:
            all_others = self.data_manager.get_all_universal_except(session_id)
            for sid, traits in all_others.items():
                global_traits.extend(traits)

        # 3. 选择风格
        local_selected = await self._select_styles(
            local_traits, session_id, user_message, embedding_provider, self._max_universal
        )
        global_selected = []
        if self._enable_cross_group and global_traits:
            global_selected = await self._select_styles(
                global_traits, None, user_message, embedding_provider, self._max_global
            )

        if not local_selected and not global_selected:
            return None

        # 4. 构建新格式文本
        style_text = self.style_selector.build_style_text(local_selected, global_selected)

        logger.debug(
            f"[烤箱-风格学习] 注入风格 | 本群={len(local_selected)} 条"
            f"{f', 全局={len(global_selected)} 条' if global_selected else ''}"
            f" | 会话: {session_id}"
        )

        injected_text = (
            "<style_guidelines>\n"
            "在回复时，请尽量采用以下风格特点："
            f"{style_text}\n"
            "</style_guidelines>"
        )

        if self._debug_mode:
            logger.info(
                f"[烤箱-风格学习] Debug 模式 - 注入内容 | 会话: {session_id}\n"
                f"本地风格: {local_selected}\n"
                f"全局风格: {global_selected}\n"
                f"完整注入文本:\n{injected_text}"
            )

        return injected_text

    # ── 内部选择方法 ──

    async def _select_styles(
        self,
        traits: list[dict],
        session_id: str | None,
        user_message: str,
        provider,
        top_n: int,
    ) -> list[str]:
        """根据嵌入或熟练度选择风格。

        当 enable_emb 为 True 且有 provider 和 user_message 时，
        优先用嵌入相似度；否则回退到熟练度排序。
        """
        if not traits:
            return []

        if self._enable_emb and user_message and provider:
            query_emb = await self.style_selector.get_embedding(user_message, provider)
            if query_emb:
                selected = self.style_selector.select_by_embedding(
                    traits, query_emb, top_n
                )
                if selected:
                    # 缓存缺失的 embedding
                    self._cache_missing_embeddings(traits, query_emb, provider)
                    return selected
                # embedding 获取成功但无一匹配，回退
                return self.style_selector.select_by_proficiency(traits, top_n)

        return self.style_selector.select_by_proficiency(traits, top_n)

    def _cache_missing_embeddings(self, traits, query_emb, provider):
        """占位：后续可异步写入缺失的 embedding 到 data_manager 持久化。"""
        # 当前仅用于记录，避免每次请求都重新计算
        pass

    def _top_local(self, session_id: str) -> list[str]:
        """获取本群 top-N 风格（旧接口兼容）。"""
        traits = self.data_manager.get_universal_for_session(session_id)
        return self.style_selector.select_by_proficiency(traits, self._max_universal)

    # ── 查询接口 ──

    def get_style_summary(self, session_id: str) -> dict[str, Any]:
        universal = self.data_manager.get_universal_for_session(session_id)
        total = len(universal)

        if total == 0:
            return {
                "has_styles": False,
                "total_styles": 0,
                "universal_count": 0,
                "universal_preview": [],
                "cross_group_trait_sources": 0,
            }

        universal_preview = [t["content"] for t in universal[:3]]
        cross_group_sources = 0
        if self._enable_cross_group:
            all_others = self.data_manager.get_all_universal_except(session_id)
            cross_group_sources = sum(len(v) for v in all_others.values())

        return {
            "has_styles": True,
            "total_styles": total,
            "universal_count": len(universal),
            "universal_preview": universal_preview,
            "cross_group_trait_sources": cross_group_sources,
        }
