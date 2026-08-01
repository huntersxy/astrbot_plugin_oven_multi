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
# Reference:
#   - astrbot_plugin_qq_group_daily_analysis (MIT) by SXP-Simon — provider selection pattern
#   - astrbot_plugin_group_chat_plus (AGPL-3.0) by Him666233 — system prompt LTM 清理

"""风格学习：注入、学习与定时调度（合并原 style_injector / style_selector /
learning_manager / scheduler / system_prompt_rewriter）。"""

from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any

from astrbot.api import logger

from .data_manager import CATEGORY_SITUATIONAL, CATEGORY_STABLE, DataManager


# ── system prompt 清理：剥离平台 LTM + 去重 ─────────────────────────────

_LTM_PATTERNS = [
    re.compile(
        (
            r"You are now in a chatroom\. The chat history is as follows:\s*\n?"
            r"(?:\[[^\]]+/\d{2}:\d{2}:\d{2}\]:.*(?:\n(?!---\n).*)*)"
            r"(?:\n---\n\[[^\]]+/\d{2}:\d{2}:\d{2}\]:.*(?:\n(?!---\n).*)*)*"
        ),
        re.IGNORECASE,
    ),
    re.compile(
        (
            r"You are now in a chatroom\. The chat history is as follows:\s*"
            r"[\s\S]*?Now, a new message is coming:\s*`[\s\S]*?`\."
            r"\s*Please react to it\."
        ),
        re.IGNORECASE,
    ),
]


# ── 防“学步”启发式：识别数字梗/短重复串 ──────────────────────────────

_MEME_DIGIT_RE = re.compile(r"(?<!\d)\d{2,4}(?!\d)")
_MEME_REPEAT_RE = re.compile(r"^(.)\1{2,}$")


def looks_like_meme(content: str) -> bool:
    """判断特征是否像特定数字梗/短重复串（如 666、233、2333、哈哈哈）。"""
    text = (content or "").strip()
    if not text:
        return True
    if len(text) <= 12 and _MEME_DIGIT_RE.search(text):
        return True
    if len(text) <= 6 and _MEME_REPEAT_RE.search(text):
        return True
    return False


def clean_system_prompt(text: str) -> str:
    """剥离平台 LTM 注入并按段落去重，避免 prompt 膨胀。"""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern in _LTM_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    parts, seen = [], set()
    for part in cleaned.split("\n\n"):
        part = part.strip()
        fingerprint = re.sub(r"\s+", " ", part).lower()
        if not part or fingerprint in seen:
            continue
        seen.add(fingerprint)
        parts.append(part)
    return "\n\n".join(parts)


# ── 风格注入器（含嵌入向量选择） ───────────────────────────────────────


class StyleInjector:
    """从本地/跨群风格特征中挑选最相关的注入到 LLM 请求。"""

    def __init__(self, data_manager: DataManager, config: dict, context=None):
        self.data = data_manager
        self.config = config
        self.context = context
        scfg = config.get("style_learning", {})
        self._max_universal = int(scfg.get("max_universal_inject", 5) or 5)
        self._enable_cross_group = bool(scfg.get("enable_cross_group", False))
        self._enable_emb = bool(scfg.get("enable_emb_style_selection", True))
        self._max_global = int(scfg.get("max_global_styles", 3) or 3)
        self._debug_mode = bool(scfg.get("debug_mode", False))
        self._embedding_provider_id = str(scfg.get("embedding_provider_id", "") or "").strip()
        self._enable_situational = bool(scfg.get("enable_situational_inject", True))
        self._max_situational = int(scfg.get("max_situational_inject", 2) or 2)
        self._sit_threshold = float(scfg.get("situational_similarity_threshold", 0.4) or 0.4)
        # 稳定风格文本缓存（按数据版本失效），保证命中时不重复计算与嵌入调用
        self._stable_cache: dict[str, tuple[int, str | None]] = {}
        self._sit_emb_cache: dict[str, dict[str, list[float]]] = {}
        self._sit_cache_version: dict[str, int] = {}

    def should_inject(self, session_id: str) -> bool:
        scfg = self.config.get("style_learning", {})
        if not scfg.get("enable_style_injection", True):
            return False
        if self.data.get_universal_for_session(session_id):
            return True
        if self._enable_cross_group:
            return any(self.data.get_all_universal_except(session_id).values())
        return False

    async def build_injection_text(
        self, session_id: str, user_message: str = ""
    ) -> str | None:
        """构建注入到 extra_user_content_parts 的风格文本，无需注入返回 None。"""
        if not self.should_inject(session_id):
            return None

        traits = self.data.get_universal_for_session(session_id)
        local_stable = [
            t for t in traits if t.get("category", CATEGORY_STABLE) == CATEGORY_STABLE
        ]
        situational = [
            t for t in traits if t.get("category") == CATEGORY_SITUATIONAL
        ]

        # 跨群只参考稳定风格，避免把其他群的情景梗带进本群
        global_stable: list[dict] = []
        if self._enable_cross_group:
            for ts in self.data.get_all_universal_except(session_id).values():
                global_stable.extend(
                    t for t in ts if t.get("category", CATEGORY_STABLE) == CATEGORY_STABLE
                )

        stable_text = self._build_stable_text(session_id, local_stable, global_stable)
        sit_text = await self._build_situational_text(session_id, situational, user_message)

        parts = [p for p in (stable_text, sit_text) if p]
        if not parts:
            return None
        if self._debug_mode:
            logger.info(
                f"[烤箱-风格学习] Debug - 注入 | 会话: {session_id}\n注入文本:\n"
                + "\n\n".join(parts)
            )
        return "\n\n".join(parts)

    def _build_stable_text(
        self, session_id: str, local_stable: list[dict], global_stable: list[dict]
    ) -> str | None:
        """构建稳定风格文本（含防复读约束），按数据版本缓存。"""
        version = self.data.get_version()
        cached = self._stable_cache.get(session_id)
        if cached is not None and cached[0] == version:
            return cached[1]

        local = self.select_by_proficiency(local_stable, self._max_universal)
        global_ = self.select_by_proficiency(global_stable, self._max_global)
        if local or global_:
            style_text = self.build_style_text(local, global_)
            text = (
                "<style_guidelines>\n"
                "以下是从本群聊天记录中提炼的风格参考，仅用于让回复更贴合群聊氛围：\n"
                f"{style_text}\n"
                "使用要求：\n"
                "1. 风格只是参考，不是复读指令；不要为了让回复“像群聊”而在每句话里"
                "强行加入 666、233 等数字、梗或语气词。\n"
                "2. 仅在语境自然合适时使用，优先回应当前用户消息；风格不得覆盖或干扰当前话题。\n"
                "3. 不要机械堆砌，也不要每条回复都刻意体现全部风格。\n"
                "</style_guidelines>"
            )
        else:
            text = None
        self._stable_cache[session_id] = (version, text)
        return text

    async def _build_situational_text(
        self, session_id: str, situational: list[dict], user_message: str
    ) -> str | None:
        """场景化表达只在当前语境匹配时注入（触发词命中或嵌入相似度达标）。"""
        if not self._enable_situational or not situational:
            return None
        message = (user_message or "").strip()

        embedding_provider = None
        query_emb = None
        if message and self._enable_emb:
            embedding_provider = self._resolve_embedding_provider()
            if embedding_provider:
                query_emb = await self.get_embedding(message, embedding_provider)

        version = self.data.get_version()
        if self._sit_cache_version.get(session_id) != version:
            self._sit_emb_cache[session_id] = {}
            self._sit_cache_version[session_id] = version
        emb_cache = self._sit_emb_cache.setdefault(session_id, {})

        matched: list[tuple[dict, float]] = []
        for trait in situational:
            content = (trait.get("content") or "").strip()
            context = (trait.get("context") or "").strip()
            if not content:
                continue
            # 用户消息直接出现该梗/场景描述 → 可自然接梗
            if message and (content in message or (context and context in message)):
                matched.append((trait, 1.0))
                continue
            if query_emb and context and embedding_provider:
                trait_emb = emb_cache.get(content)
                if trait_emb is None:
                    trait_emb = await self.get_embedding(
                        f"{content}：{context}", embedding_provider
                    )
                    if trait_emb:
                        emb_cache[content] = trait_emb
                if trait_emb:
                    score = self.cosine_similarity(query_emb, trait_emb)
                    if score >= self._sit_threshold:
                        matched.append((trait, score))

        if not matched:
            return None
        matched.sort(key=lambda x: x[1], reverse=True)
        selected = [t for t, _ in matched[: self._max_situational]]
        lines = ["当前语境相关的场景化表达（仅供理解与自然接梗，不要主动扩散）："]
        for trait in selected:
            content = trait.get("content", "")
            context = trait.get("context", "")
            lines.append(f"- 「{content}」：{context}" if context else f"- 「{content}」")
        return "\n".join(lines)

    def _resolve_embedding_provider(self):
        """解析嵌入 Provider：优先配置项，否则第一个可用 EmbeddingProvider。"""
        if not self.context:
            return None
        if self._embedding_provider_id:
            provider = self.context.get_provider_by_id(self._embedding_provider_id)
            if provider and (
                hasattr(provider, "get_embedding") and callable(provider.get_embedding)
            ):
                return provider
            logger.warning(
                f"[烤箱-风格学习] 配置的 embedding_provider_id 无效: {self._embedding_provider_id}"
            )
        try:
            providers = self.context.get_all_embedding_providers()
            return providers[0] if providers else None
        except Exception:
            return None

    def get_style_summary(self, session_id: str) -> dict[str, Any]:
        traits = self.data.get_universal_for_session(session_id)
        stable = [t for t in traits if t.get("category", CATEGORY_STABLE) == CATEGORY_STABLE]
        situational = [t for t in traits if t.get("category") == CATEGORY_SITUATIONAL]
        cross_group_sources = (
            sum(len(v) for v in self.data.get_all_universal_except(session_id).values())
            if self._enable_cross_group
            else 0
        )
        return {
            "has_styles": bool(traits),
            "universal_count": len(stable),
            "situational_count": len(situational),
            "universal_preview": [t["content"] for t in stable[:3]],
            "situational_preview": [
                {"content": t["content"], "context": t.get("context", "")}
                for t in situational[:3]
            ],
            "cross_group_trait_sources": cross_group_sources,
        }

    # ── 选择算法（静态工具） ────────────────────────────────────────────

    @staticmethod
    def build_style_text(local_styles: list[str], global_styles: list[str] | None = None) -> str:
        parts = []
        if local_styles:
            parts.append(f"本群风格：{'、'.join(local_styles)}")
        if global_styles:
            parts.append(f"全局风格：{'、'.join(global_styles)}")
        return "；".join(parts)

    @staticmethod
    def select_by_proficiency(traits: list[dict], top_n: int = 5) -> list[str]:
        sorted_traits = sorted(
            traits, key=lambda x: x.get("proficiency", 0), reverse=True
        )
        return [
            t["content"]
            for t in sorted_traits[:top_n]
        ]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na > 1e-10 and nb > 1e-10 else 0.0

    @staticmethod
    async def get_embedding(text: str, provider) -> list[float] | None:
        """尝试通过 Embedding Provider 获取文本嵌入向量。"""
        if not provider:
            return None
        for method in ("get_embedding", "get_embeddings", "text_embedding"):
            fn = getattr(provider, method, None)
            if not callable(fn):
                continue
            try:
                result = await fn(text)
            except Exception as e:
                logger.debug(f"[烤箱-风格选择] Embedding 获取失败 (可忽略): {e}")
                continue
            if isinstance(result, list) and result:
                return result[0] if isinstance(result[0], list) else result
        return None


# ── 风格学习器 ─────────────────────────────────────────────────────────


class StyleLearner:
    """把积累的聊天记录交给 LLM 提炼为风格特征，并更新到 DataManager。"""

    def __init__(self, context, data_manager: DataManager, config: dict):
        self.context = context
        self.data = data_manager
        self.config = config

    async def analyze_and_learn(self, session_id: str, provider_id: str = ""):
        scfg = self.config.get("style_learning", {})
        min_history = int(
            scfg.get("min_history_for_analysis", 10)
            or self.config.get("min_history_for_analysis", 10)
            or 10
        )
        chat_history = self.data.get_chat_history(session_id, limit=100)
        if len(chat_history) < min_history:
            return

        prompt = self._build_prompt(session_id, chat_history)
        try:
            provider = (
                self.context.get_provider_by_id(provider_id=provider_id)
                if provider_id
                else None
            )
            if provider is None:
                if provider_id:
                    logger.warning(
                        f"[烤箱-风格学习] 指定的 Provider '{provider_id}' 不存在，回退到当前会话 Provider"
                    )
                provider = self.context.get_using_provider()

            llm_response = await provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个群聊文化分析师，从聊天记录中提取这个群的说话风格和语言习惯。",
            )
            if llm_response.role != "assistant":
                logger.warning(
                    f"[烤箱-风格学习] LLM 调用失败或返回非预期的角色: {llm_response.role}"
                )
                return

            traits = self._extract_traits_from_json(llm_response.completion_text)
            if traits:
                self.data.replace_universal(
                    session_id, traits["universal"], traits["situational"]
                )
                logger.info(
                    f"[烤箱-风格学习] 为会话 {session_id} 更新风格特征: "
                    f"稳定 {len(traits['universal'])} 条，场景化 {len(traits['situational'])} 条"
                )
            await self.data.clear_chat_history(session_id)
        except Exception as e:
            logger.error(f"[烤箱-风格学习] 分析学习过程中发生错误: {e}")

    def _build_prompt(self, session_id: str, chat_history: list[dict]) -> str:
        history_str = "\n".join(
            f"{msg['sender']}: {msg['content']}" for msg in chat_history
        )
        existing = self.data.get_universal_for_session(session_id)
        stable_lines = [
            f"- {t['content']}"
            for t in existing
            if t.get("category", CATEGORY_STABLE) == CATEGORY_STABLE
        ]
        sit_lines = [
            f"- {t['content']}（场景：{t.get('context', '')}）"
            for t in existing
            if t.get("category") == CATEGORY_SITUATIONAL
        ]
        hint_parts = []
        if stable_lines:
            hint_parts.append("稳定风格：\n" + "\n".join(stable_lines))
        if sit_lines:
            hint_parts.append("场景化表达：\n" + "\n".join(sit_lines))
        existing_hint = (
            "\n\n已有的风格特征（请根据新聊天记录保留或更新）：\n" + "\n".join(hint_parts)
            if hint_parts
            else ""
        )
        return f"""
分析以下聊天记录，提取该群的整体说话风格和语言习惯。

聊天记录：
```
{history_str}
```{existing_hint}

要求：
1. 只返回有效 JSON，不要解释
2. 格式：
   {{"universal": ["稳定风格特征1", "特征2"], "situational": [{{"content": "场景化表达", "context": "触发场景描述"}}]}}
3. universal 只收「跨场景稳定」的风格特征：语气、句式、措辞习惯、话题偏好等。
   最多 8 条，每条约 10-30 字。
4. situational 只收「有明确触发场景」的表达：特定场合才用的梗、数字梗（如 666、233）、
   刷屏用语等，每条必须附带触发场景描述。最多 6 条。
5. 重要：特定数字梗、一次性梗、刷屏用语绝不能放进 universal，只属于 situational；
   若某表达只是偶尔出现、没有稳定场景，则不收录。
6. 如果已有历史特征，从中保留合适的并补充新的。
7. 如果没有聊天记录或没有明显风格特征，返回 {{"universal": [], "situational": []}}
8. 注意：特征描述中不要包含引号或双引号，直接写内容即可。

示例输出：
{{"universal": ["爱用表情包和语气词，对话节奏快", "喜欢自嘲和损人，互怼但不破防", "常用缩写和圈内黑话"],
  "situational": [{{"content": "666", "context": "表示佩服或认同，群友发 666 时可自然接梗"}},
                  {{"content": "233", "context": "表示好笑，群友玩梗或讲笑话时可接"}}]}}"""

    def _extract_traits_from_json(self, text: str) -> dict | None:
        """从 LLM 输出中提取 universal + situational 特征，含 JSON 修复回退与梗过滤。"""
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        json_str = match.group(1) if match else None
        if not json_str:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                json_str = text[start : end + 1]
        if not json_str:
            logger.warning("[烤箱-风格学习] 未能在 LLM 输出中找到 JSON 结构")
            return None

        try:
            data = json.loads(json_str)
            universal = data.get("universal", []) or []
            situational = data.get("situational", []) or []
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(
                f"[烤箱-风格学习] JSON 解析失败: {e}\n提取的 JSON 片段: {json_str[:300]}"
            )
            universal = self._manual_extract_strings(json_str) or []
            situational = []
            if universal:
                logger.info("[烤箱-风格学习] 使用手动回退解析成功")

        universal = [u for u in universal if isinstance(u, str) and u.strip()]
        situational_items = []
        for item in situational if isinstance(situational, list) else []:
            if isinstance(item, str) and item.strip():
                situational_items.append({"content": item.strip(), "context": ""})
            elif isinstance(item, dict):
                content = str(item.get("content") or "").strip()
                if content:
                    situational_items.append(
                        {
                            "content": content,
                            "context": str(item.get("context") or "").strip(),
                        }
                    )

        # 启发式护栏：把数字梗/短重复串从 universal 降级到 situational
        stable, moved = [], []
        for content in universal:
            if looks_like_meme(content):
                moved.append(
                    {
                        "content": content,
                        "context": "当群友使用或刷出该表达时自然接梗回应，不要主动扩散",
                    }
                )
            else:
                stable.append(content)
        situational_items = moved + situational_items

        # 按内容去重，避免同一条被反复注入
        seen, dedup = set(), []
        for item in situational_items:
            key = item["content"]
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        situational_items = dedup

        if not stable and not situational_items:
            return None
        return {"universal": stable, "situational": situational_items}

    @staticmethod
    def _manual_extract_strings(text: str) -> list[str] | None:
        """从 JSON-like 文本中提取字符串列表，容忍内部未转义的双引号。"""
        arr_start = text.find("[", text.find("universal"))
        if arr_start == -1:
            return None
        depth, in_str, i, arr_end = 0, False, arr_start, -1
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        arr_end = i
                        break
            i += 1
        if arr_end == -1:
            return None

        content = text[arr_start + 1 : arr_end]
        strings, i = [], 0
        while i < len(content):
            if content[i] != '"':
                i += 1
                continue
            i += 1
            buf = []
            while i < len(content):
                c = content[i]
                if c == "\\":
                    buf.append(c)
                    i += 1
                    if i < len(content):
                        buf.append(content[i])
                    i += 1
                elif c == '"':
                    tail = content[i + 1 :].lstrip()
                    if not tail or tail[0] in ",]":
                        strings.append("".join(buf))
                        i += 1
                        break
                    buf.append(c)
                    i += 1
                else:
                    buf.append(c)
                    i += 1
            else:
                break
        return strings or None


# ── 风格学习管理（生命周期 + 定时分析） ────────────────────────────────


class StyleManager:
    """风格学习总管理：聊天记录收集（main 中）、定时分析、注入与持久化。"""

    def __init__(self, star, data_dir):
        self.data = DataManager(data_dir, star.config)
        self.injector = StyleInjector(self.data, star.config, star.context)
        self.learner = StyleLearner(star.context, self.data, star.config)
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_analysis())
        scfg = self.data.config.get("style_learning", {})
        interval = int(scfg.get("analysis_interval_seconds", 21600) or 21600)
        logger.info(f"[烤箱-风格学习] 定时分析任务已启动（每 {interval} 秒执行一次）。")

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        logger.info("[烤箱-风格学习] 定时分析任务已停止。")

    async def learn(self, session_id: str, provider_id: str = ""):
        await self.learner.analyze_and_learn(session_id, provider_id=provider_id)

    async def _run_analysis(self):
        scfg = self.data.config.get("style_learning", {})
        interval = int(scfg.get("analysis_interval_seconds", 21600) or 21600)
        provider_id = (scfg.get("style_provider_id", "") or "").strip()
        while self._running:
            await asyncio.sleep(interval)
            for session_id in list(self.data.chat_history.keys()):
                try:
                    await self.learner.analyze_and_learn(session_id, provider_id)
                    await asyncio.sleep(0)
                except Exception as e:
                    logger.error(f"[烤箱-风格学习] 分析会话 {session_id} 时出错: {e}")
            await self.data.force_save()
