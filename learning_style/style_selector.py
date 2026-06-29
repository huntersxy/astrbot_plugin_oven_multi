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

import math
from typing import Any

from astrbot.api import logger


class StyleSelector:
    """风格选择器，支持熟练度排序和嵌入向量语义选择。"""

    @staticmethod
    def build_style_text(
        local_styles: list[str],
        global_styles: list[str] | None = None,
    ) -> str:
        """构建新格式的注入文本。

        Args:
            local_styles: 本群风格列表。
            global_styles: 全局（跨群）风格列表，为 None 或空则不输出。

        Returns:
            格式如 "本群风格：xxx、yyy；全局风格：zzz、www"
        """
        parts = []
        if local_styles:
            parts.append(f"本群风格：{'、'.join(local_styles)}")
        if global_styles:
            parts.append(f"全局风格：{'、'.join(global_styles)}")
        return "；".join(parts)

    @staticmethod
    def select_by_proficiency(
        traits: list[dict[str, Any]], top_n: int = 5
    ) -> list[str]:
        """按熟练度降序选取风格特征。

        Args:
            traits: 风格特征列表（含 proficiency 字段）。
            top_n: 最多选取条数。

        Returns:
            风格内容字符串列表。
        """
        if not traits:
            return []
        sorted_traits = sorted(
            traits, key=lambda t: t.get("proficiency", 0), reverse=True
        )
        return [t["content"] for t in sorted_traits[:top_n]]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """计算两个向量的余弦相似度。"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na < 1e-10 or nb < 1e-10:
            return 0.0
        return dot / (na * nb)

    @classmethod
    def select_by_embedding(
        cls,
        traits: list[dict[str, Any]],
        query_embedding: list[float],
        top_n: int = 5,
    ) -> list[str]:
        """通过嵌入向量相似度选取最相关的风格特征。

        Args:
            traits: 风格特征列表（含 embedding 和 content 字段）。
            query_embedding: 查询文本的嵌入向量。
            top_n: 最多选取条数。

        Returns:
            按相似度降序排列的风格内容列表。
        """
        if not traits or not query_embedding:
            return []

        scored = []
        for trait in traits:
            emb = trait.get("embedding")
            if emb and len(emb) == len(query_embedding):
                sim = cls.cosine_similarity(query_embedding, emb)
                scored.append((trait, sim))

        if not scored:
            # 都没有 embedding 时回退到熟练度排序
            return cls.select_by_proficiency(traits, top_n)

        scored.sort(key=lambda x: x[1], reverse=True)
        return [t["content"] for t, _ in scored[:top_n]]

    @classmethod
    async def get_embedding(
        cls, text: str, provider
    ) -> list[float] | None:
        """尝试通过 Embedding Provider 获取文本嵌入向量。

        优先使用标准 EmbeddingProvider.get_embedding() 接口，
        兼容旧的 get_embeddings/text_embedding 方法。

        Args:
            text: 要嵌入的文本。
            provider: AstrBot EmbeddingProvider 或 LLM Provider 实例。

        Returns:
            嵌入向量列表，失败返回 None。
        """
        if not provider:
            return None
        try:
            # 优先使用标准 EmbeddingProvider 接口
            if hasattr(provider, "get_embedding") and callable(provider.get_embedding):
                result = await provider.get_embedding(text)
                if result and isinstance(result, list):
                    return result
            # 兼容旧接口
            if hasattr(provider, "get_embeddings") and callable(
                provider.get_embeddings
            ):
                result = await provider.get_embeddings(text)
                if result and isinstance(result, list):
                    if result and isinstance(result[0], list):
                        return result[0]
                    return result
            if hasattr(provider, "text_embedding") and callable(
                provider.text_embedding
            ):
                result = await provider.text_embedding(text)
                if result and isinstance(result, list):
                    return result
        except Exception as e:
            logger.debug(f"[烤箱-风格选择] Embedding 获取失败 (可忽略): {e}")
        return None
