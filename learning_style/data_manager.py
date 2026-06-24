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
import difflib
import json
import math
import os
import re
from typing import Any

from astrbot.api import logger

try:
    from astrbot.core.provider.provider import EmbeddingProvider
except ImportError:
    EmbeddingProvider = None

CONTEXTUAL_BUFFER_RATIO = 0.2
DIFFLIB_THRESHOLD = 0.85


class DataManager:
    def __init__(self, data_dir: str, config: dict):
        self.data_dir = data_dir
        self.universal_file = os.path.join(data_dir, "universal.json")
        self.contextual_file = os.path.join(data_dir, "contextual.json")
        self.specific_file = os.path.join(data_dir, "specific.json")
        self.chat_history_file = os.path.join(data_dir, "chat_history.json")

        self.universal: dict[str, list[dict[str, Any]]] = {}
        self.contextual: dict[str, list[dict[str, Any]]] = {}
        self.specific: dict[str, list[dict[str, Any]]] = {}
        self.chat_history: dict[str, list[dict[str, Any]]] = {}

        self.config = config

        self._ensure_data_dir()
        self._handle_old_format()
        self.load_universal()
        self.load_contextual()
        self.load_specific()
        self.load_chat_history()
        self.lock = asyncio.Lock()

        self._dirty_universal = False
        self._dirty_contextual = False
        self._dirty_specific = False
        self._dirty_chat_history = False
        self._save_timer = None
        self._save_delay = 5.0

    def _ensure_data_dir(self):
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            logger.info(f"[烤箱-风格学习] 创建数据目录: {self.data_dir}")

    def _handle_old_format(self):
        old_file = os.path.join(self.data_dir, "styles.json")
        if os.path.exists(old_file):
            logger.warning(
                "[烤箱-风格学习] 检测到旧版数据格式 (styles.json)，已重命名为 styles.json.bak"
            )
            os.rename(old_file, old_file + ".bak")

    # ==================== 通用表征 ====================

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

    # ==================== 情境表征 ====================

    def load_contextual(self):
        if os.path.exists(self.contextual_file):
            try:
                with open(self.contextual_file, encoding="utf-8") as f:
                    self.contextual = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.error(f"[烤箱-风格学习] 加载情境表征文件失败: {e}")
                self.contextual = {}
        else:
            self.contextual = {}

    def get_contextual_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.contextual.get(session_id, [])

    def get_contextual_buffer(self, session_id: str) -> list[dict[str, Any]]:
        return [
            t
            for t in self.contextual.get(session_id, [])
            if t.get("_in_buffer")
        ]

    def add_contextual(self, session_id: str, scene: str, behavior: str):
        current_time = asyncio.get_running_loop().time()
        if session_id not in self.contextual:
            self.contextual[session_id] = []

        self.contextual[session_id].append({
            "scene": scene,
            "behavior": behavior,
            "created_at": current_time,
            "_in_buffer": True,
        })

        max_capacity = self.config.get("max_contextual_per_session", 50)
        while len(self.contextual[session_id]) > max_capacity:
            removed = self.contextual[session_id].pop(0)
            logger.debug(
                f"[烤箱-风格学习] FIFO 淘汰情境表征: {removed.get('scene', '?')}\u2192{removed.get('behavior', '?')}"
            )

        self._refresh_buffer_markers(session_id)
        self._dirty_contextual = True
        asyncio.create_task(self._schedule_save())

    def _refresh_buffer_markers(self, session_id: str):
        traits = self.contextual.get(session_id, [])
        if not traits:
            return
        buffer_count = max(1, int(len(traits) * CONTEXTUAL_BUFFER_RATIO))
        for i, t in enumerate(traits):
            t["_in_buffer"] = (i >= len(traits) - buffer_count)

    def mark_contextual_merged(self, session_id: str, index: int):
        if session_id in self.contextual and 0 <= index < len(self.contextual[session_id]):
            self.contextual[session_id].pop(index)
            self._refresh_buffer_markers(session_id)
            self._dirty_contextual = True
            asyncio.create_task(self._schedule_save())

    # ---------------------------------------------------------------------------
    # Embedding 工具
    # ---------------------------------------------------------------------------

    @staticmethod
    def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        if len(vec_a) != len(vec_b) or not vec_a:
            return -1.0
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for a, b in zip(vec_a, vec_b):
            dot += a * b
            norm_a += a * a
            norm_b += b * b
        if norm_a <= 0 or norm_b <= 0:
            return -1.0
        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))

    @staticmethod
    def _text_key(text: str) -> str:
        return text.strip()[:200]

    # ---------------------------------------------------------------------------
    # 情境缓冲合并（支持 Embedding / difflib 双模式）
    # ---------------------------------------------------------------------------

    async def merge_contextual_buffer(
        self,
        session_id: str,
        threshold: float = 0.75,
        embedding_provider: Any = None,
    ) -> dict:
        """
        将缓冲位的情境表征尝试合并到通用/特定。

        参数:
            session_id: 会话标识
            threshold:  相似度阈值。embedding 模式默认 0.75，difflib 模式用内部 DIFFLIB_THRESHOLD (0.85)
            embedding_provider: EmbeddingProvider 实例。为 None 时使用 difflib。

        返回合并统计:
        {
            "total_buffers": N,
            "merged_to_universal": N,
            "merged_to_specific": N,
            "remained": N,
            "details": ["场景→行为 → 合并到通用「...」(相似度 0.92)", ...],
            "mode": "embedding" | "difflib"
        }
        """
        stats = {
            "total_buffers": 0,
            "merged_to_universal": 0,
            "merged_to_specific": 0,
            "remained": 0,
            "details": [],
            "mode": "difflib",
        }
        if session_id not in self.contextual:
            return stats

        use_embedding = (
            embedding_provider is not None
            and EmbeddingProvider is not None
            and isinstance(embedding_provider, EmbeddingProvider)
        )

        effective_threshold = (
            threshold if use_embedding else DIFFLIB_THRESHOLD
        )

        # —— Pre‑compute universal / specific embeddings  ——
        uni_texts: list[str] = []
        spec_texts: list[str] = []
        if use_embedding:
            uni_texts = [
                u["content"] for u in
                (self.universal.get(session_id) or [])
            ]
            spec_texts = [
                s["content"] for s in
                (self.specific.get(session_id) or [])
            ]
            # 并发请求所有 embedding
            uni_embs, spec_embs = await asyncio.gather(
                self._batch_embed(uni_texts, embedding_provider),
                self._batch_embed(spec_texts, embedding_provider),
            )
        else:
            uni_embs = []
            spec_embs = []

        # —— 逐条处理缓冲  ——
        remaining = []
        for item in self.contextual[session_id]:
            if not item.get("_in_buffer"):
                remaining.append(item)
                continue

            text = f"{item['scene']}→{item['behavior']}"
            stats["total_buffers"] += 1
            merged = False

            # 匹配通用
            if use_embedding:
                merged, detail = await self._match_embedding(
                    text, uni_texts, uni_embs, effective_threshold,
                    "通用", embedding_provider,
                )
                if merged:
                    idx = detail["_idx"]
                    u = self.universal[session_id][idx]
                    u["proficiency"] = min(100, u.get("proficiency", 0) + 5)
                    stats["merged_to_universal"] += 1
                    stats["details"].append(detail["log"])
                    continue
            else:
                if session_id in self.universal:
                    for u in self.universal[session_id]:
                        score = difflib.SequenceMatcher(None, text, u["content"]).ratio()
                        if score > effective_threshold:
                            u["proficiency"] = min(100, u.get("proficiency", 0) + 5)
                            merged = True
                            stats["merged_to_universal"] += 1
                            log = f"情境「{text}」→ 合并到通用「{u['content'][:40]}」(difflib {score:.2f})"
                            stats["details"].append(log)
                            logger.debug(f"[烤箱-风格学习] {log}")
                            break
                if merged:
                    continue

            # 匹配特定
            if use_embedding:
                merged, detail = await self._match_embedding(
                     text, spec_texts, spec_embs, effective_threshold,
                     "特定", embedding_provider,
                 )
                if merged:
                    idx = detail["_idx"]
                    s = self.specific[session_id][idx]
                    s["trigger_count"] = s.get("trigger_count", 0) + 1
                    stats["merged_to_specific"] += 1
                    stats["details"].append(detail["log"])
                    continue
            else:
                if not merged and session_id in self.specific:
                    for s in self.specific[session_id]:
                        score = difflib.SequenceMatcher(None, text, s["content"]).ratio()
                        if score > effective_threshold:
                            s["trigger_count"] = s.get("trigger_count", 0) + 1
                            merged = True
                            stats["merged_to_specific"] += 1
                            log = f"情境「{text}」→ 合并到特定「{s['content'][:40]}」(difflib {score:.2f})"
                            stats["details"].append(log)
                            logger.debug(f"[烤箱-风格学习] {log}")
                            break

            if not merged:
                stats["remained"] += 1
                stats["details"].append(f"情境「{text}」→ 未匹配，留在缓冲")
                remaining.append(item)

        self.contextual[session_id] = remaining
        self._refresh_buffer_markers(session_id)
        self._dirty_contextual = True
        asyncio.create_task(self._schedule_save())

        if use_embedding:
            stats["mode"] = "embedding"
        return stats

    async def _batch_embed(
        self, texts: list[str], provider: Any
    ) -> list[list[float]]:
        """并发获取一批文本的 embedding 向量。"""
        if not texts:
            return []
        tasks = [provider.get_embedding(t) for t in texts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[list[float]] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"[烤箱-风格学习] embedding 请求失败: {r}")
                out.append([])
            elif isinstance(r, list) and r:
                out.append(r)
            else:
                out.append([])
        return out

    async def _match_embedding(
        self,
        text: str,
        candidates_texts: list[str],
        candidates_embs: list[list[float]],
        threshold: float,
        label: str,
        provider: Any,
    ) -> tuple[bool, dict]:
        """
        用 embedding 余弦相似度在候选列表中找最佳匹配。

        返回:
            (matched, {"_idx": int, "log": str}) 或 (False, {})
        """
        if not candidates_texts or not candidates_embs:
            return False, {}

        try:
            text_emb = await provider.get_embedding(text)
        except Exception as e:
            logger.warning(f"[烤箱-风格学习] 缓冲条目 embedding 失败: {e}")
            return False, {}

        if not text_emb:
            return False, {}

        best_idx = -1
        best_score = -1.0
        for i, cand_emb in enumerate(candidates_embs):
            score = self.cosine_similarity(text_emb, cand_emb)
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx >= 0 and best_score > threshold:
            matched_text = candidates_texts[best_idx][:50]
            log = (
                f"情境「{text}」→ 合并到{label}「{matched_text}」"
                f"(余弦 {best_score:.3f})"
            )
            logger.debug(f"[烤箱-风格学习] {log}")
            return True, {"_idx": best_idx, "log": log}

        return False, {}

    async def save_contextual(self):
        async with self.lock:
            try:
                with open(self.contextual_file, "w", encoding="utf-8") as f:
                    json.dump(self.contextual, f, ensure_ascii=False, indent=4)
                self._dirty_contextual = False
            except OSError as e:
                logger.error(f"[烤箱-风格学习] 保存情境表征文件失败: {e}")

    # ==================== 特定表征 ====================

    def load_specific(self):
        if os.path.exists(self.specific_file):
            try:
                with open(self.specific_file, encoding="utf-8") as f:
                    self.specific = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.error(f"[烤箱-风格学习] 加载特定表征文件失败: {e}")
                self.specific = {}
        else:
            self.specific = {}

    def get_specific_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.specific.get(session_id, [])

    def add_or_update_specific(self, session_id: str, content: str, trigger_regex: str):
        try:
            re.compile(trigger_regex)
        except re.error as e:
            logger.error(
                f"[烤箱-风格学习] 特定表征 '{content}' 的正则表达式无效: {trigger_regex}, 错误: {e}"
            )
            return

        current_time = asyncio.get_running_loop().time()
        if session_id not in self.specific:
            self.specific[session_id] = []

        for trait in self.specific[session_id]:
            if trait["content"] == content:
                trait["trigger_count"] = trait.get("trigger_count", 0) + 1
                trait["last_seen"] = current_time
                self._dirty_specific = True
                asyncio.create_task(self._schedule_save())
                return

        self.specific[session_id].append({
            "content": content,
            "trigger_regex": trigger_regex,
            "trigger_count": 1,
            "first_seen": current_time,
            "last_seen": current_time,
        })
        self._dirty_specific = True
        asyncio.create_task(self._schedule_save())

    def get_specific_for_promotion(self, session_id: str, threshold: int) -> list[dict[str, Any]]:
        return [
            t for t in self.specific.get(session_id, [])
            if t.get("trigger_count", 0) >= threshold
        ]

    def remove_lowest_specific(self, session_id: str, count: int):
        if session_id not in self.specific or count <= 0:
            return
        traits = sorted(self.specific[session_id], key=lambda t: t.get("trigger_count", 0))
        self.specific[session_id] = traits[count:]
        self._dirty_specific = True
        asyncio.create_task(self._schedule_save())

    def check_specific_capacity(self, session_id: str):
        max_specific = self.config.get("max_specific_per_session", 200)
        if session_id in self.specific and len(self.specific[session_id]) > max_specific:
            excess = len(self.specific[session_id]) - max_specific
            self.remove_lowest_specific(session_id, excess)

    async def save_specific(self):
        async with self.lock:
            try:
                with open(self.specific_file, "w", encoding="utf-8") as f:
                    json.dump(self.specific, f, ensure_ascii=False, indent=4)
                self._dirty_specific = False
            except OSError as e:
                logger.error(f"[烤箱-风格学习] 保存特定表征文件失败: {e}")

    # ==================== 公共保存逻辑 ====================

    async def _schedule_save(self):
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = asyncio.create_task(self._delayed_save())

    async def _delayed_save(self):
        await asyncio.sleep(self._save_delay)
        if self._dirty_universal:
            await self.save_universal()
        if self._dirty_contextual:
            await self.save_contextual()
        if self._dirty_specific:
            await self.save_specific()
        if self._dirty_chat_history:
            await self.save_chat_history()
        self._save_timer = None

    async def force_save(self):
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        if self._dirty_universal:
            await self.save_universal()
        if self._dirty_contextual:
            await self.save_contextual()
        if self._dirty_specific:
            await self.save_specific()
        if self._dirty_chat_history:
            await self.save_chat_history()

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
