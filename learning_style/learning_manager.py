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

import json
import re
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Star

from .data_manager import DataManager


class LearningManager:
    def __init__(self, star_instance: Star, data_manager: DataManager, config: dict):
        self.star = star_instance
        self.context = star_instance.context
        self.data_manager = data_manager
        self.config = config

    async def analyze_and_learn(self, session_id: str, provider_id: str = ""):
        min_history = self.config.get("min_history_for_analysis", 10)
        chat_history = self.data_manager.get_chat_history(session_id, limit=100)
        if len(chat_history) < min_history:
            return

        prompt = self._build_prompt(session_id, chat_history)

        try:
            if provider_id:
                provider = self.context.get_provider_by_id(provider_id=provider_id)
                if provider is None:
                    logger.warning(
                        f"[烤箱-风格学习] 指定的 Provider '{provider_id}' 不存在，"
                        "回退到当前会话 Provider"
                    )
                    provider = self.context.get_using_provider()
            else:
                provider = self.context.get_using_provider()

            llm_response = await provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个群聊文化分析师，从聊天记录中提取这个群的说话风格和语言习惯。",
            )

            if llm_response.role == "assistant":
                await self._parse_and_store_results(session_id, llm_response.completion_text)
                await self.data_manager.clear_chat_history(session_id)
            else:
                logger.warning(
                    f"[烤箱-风格学习] LLM 调用失败或返回非预期的角色: {llm_response.role}"
                )

        except Exception as e:
            logger.error(f"[烤箱-风格学习] 分析学习过程中发生错误: {e}")

    def _build_prompt(self, session_id: str, chat_history: list[dict[str, Any]]) -> str:
        history_str = "\n".join(
            [f"{msg['sender']}: {msg['content']}" for msg in chat_history]
        )

        universal = self.data_manager.get_universal_for_session(session_id)
        universal_list = [t["content"] for t in universal] if universal else []
        universal_str = "\n".join([f"- {c}" for c in universal_list]) if universal_list else ""

        existing_hint = ""
        if universal_str:
            existing_hint = f"""

已有的风格特征（请根据新聊天记录保留或更新）：
{universal_str}
"""

        prompt = f"""
分析以下聊天记录，提取该群的整体说话风格和语言习惯。

聊天记录：
```
{history_str}
```"""
        prompt += existing_hint
        prompt += """

要求：
1. 只返回有效 JSON，不要解释
2. 格式：{"universal": ["特征1", "特征2"]}
3. 每条特征描述这个群的总体风格——语气、用词习惯、聊天氛围、常用梗的概括。每条约 10-30 字。
   至少1条最多10条。如果已有历史特征，从中保留合适的并补充新的。
4. 如果没有聊天记录或没有明显风格特征，返回 {"universal": []}
5. 注意：特征描述中不要包含引号或双引号，直接写内容即可。

示例输出：
{"universal": ["爱用表情包和语气词，对话节奏快", "喜欢自嘲和损人，互怼但不破防", "常用缩写和圈内黑话"]}"""
        return prompt

    async def _parse_and_store_results(self, session_id: str, llm_output: str):
        universal = self._extract_universal_from_json(llm_output)
        if universal:
            self.data_manager.replace_universal(session_id, universal)
            logger.info(
                f"[烤箱-风格学习] 为会话 {session_id} 更新通用风格表征: {universal}"
            )

    def _extract_universal_from_json(self, text: str) -> list[str] | None:
        """从 LLM 输出中提取 universal 风格列表，含 JSON 修复回退。"""
        # 第一步：提取 JSON 候选字符串
        json_str = None
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_str = text[start : end + 1]

        if not json_str:
            logger.warning(
                "[烤箱-风格学习] 未能在 LLM 输出中找到 JSON 结构"
            )
            return None

        # 第二步：尝试标准 JSON 解析
        try:
            results = json.loads(json_str)
            universal = results.get("universal", [])
            return universal if universal else None
        except (json.JSONDecodeError, KeyError):
            pass

        # 第三步：回退 —— 手动提取数组中的字符串
        try:
            universal = self._manual_extract_strings(json_str)
            if universal:
                logger.info(
                    "[烤箱-风格学习] 使用手动回退解析成功"
                )
                return universal
        except Exception as e:
            logger.warning(
                f"[烤箱-风格学习] 手动回退解析也失败: {e}"
            )

        logger.error(
            f"[烤箱-风格学习] 解析 LLM 输出失败\n原始输出: {text[:500]}"
        )
        return None

    @staticmethod
    def _manual_extract_strings(text: str) -> list[str] | None:
        """手动从 JSON-like 文本中提取字符串列表，容忍内部未转义的双引号。"""
        # 定位 "universal": 后的数组
        arr_start = text.find("[", text.find("universal"))
        if arr_start == -1:
            return None

        # 从 arr_start 开始扫描，跟踪括号深度找到真正的数组结尾
        depth = 0
        arr_end = -1
        in_str = False
        i = arr_start
        while i < len(text):
            ch = text[i]
            if ch == '\\':
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0:
                        arr_end = i
                        break
            i += 1

        if arr_end == -1:
            return None

        content = text[arr_start + 1 : arr_end]

        # 手动提取字符串
        strings = []
        i = 0
        while i < len(content):
            # 跳过非引号字符（逗号、空格等）
            if content[i] != '"':
                i += 1
                continue

            i += 1  # 跳过开头的 "
            buf = []
            while i < len(content):
                c = content[i]
                if c == '\\':
                    buf.append(c)
                    if i + 1 < len(content):
                        i += 1
                        buf.append(content[i])
                    i += 1
                elif c == '"':
                    # 检查这个引号是否为字符串结束（即其后紧跟 , 或 ] 或空白+逗号/]）
                    # 如果是内嵌引号（后面还有字符），则继续收集
                    tail = content[i + 1 :].lstrip()
                    if tail and tail[0] in ',]':
                        # 这是真正的字符串结束引号
                        strings.append("".join(buf))
                        i += 1
                        break
                    else:
                        # 内嵌引号，当作普通字符处理
                        buf.append(c)
                        i += 1
                else:
                    buf.append(c)
                    i += 1
            else:
                # 字符串未正确关闭，放弃
                break

        return strings if strings else None
