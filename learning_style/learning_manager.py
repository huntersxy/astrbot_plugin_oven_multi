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
                logger.info(
                    f"[烤箱-风格学习] 使用 Provider: {provider_id or '当前会话默认'}"
                )
            else:
                provider = self.context.get_using_provider()

            llm_response = await provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个群聊文化分析师，从聊天记录中提取这个群的说话风格、社交模式和内部梗。",
            )

            if llm_response.role == "assistant":
                await self._parse_and_store_results(session_id, llm_response.completion_text)
                await self.data_manager.clear_chat_history(session_id)
            else:
                logger.warning(f"[烤箱-风格学习] LLM 调用失败或返回非预期的角色: {llm_response.role}")

        except Exception as e:
            logger.error(f"[烤箱-风格学习] 分析学习过程中发生错误: {e}")

    def _build_prompt(self, session_id: str, chat_history: list[dict[str, Any]]) -> str:
        history_str = "\n".join(
            [f"{msg['sender']}: {msg['content']}" for msg in chat_history]
        )

        universal = self.data_manager.get_universal_for_session(session_id)
        universal_list = [t["content"] for t in universal] if universal else []
        universal_str = "\n".join([f"- {c}" for c in universal_list]) if universal_list else "(无)"

        threshold = self.config.get("specific_promotion_threshold", 5)
        promotion_candidates = self.data_manager.get_specific_for_promotion(session_id, threshold)
        promotion_str = ""
        if promotion_candidates:
            lines = [
                f"- {t['content']} (触发 {t['trigger_count']} 次)"
                for t in promotion_candidates
            ]
            promotion_str = "\n".join(lines)

        buffer_items = self.data_manager.get_contextual_buffer(session_id)
        contextual_hint = ""
        if buffer_items:
            lines = [f"- {t['scene']}\u2192{t['behavior']}" for t in buffer_items]
            contextual_hint = "\n".join(lines)

        universal_section = ""
        if universal_str and universal_str != "(无)":
            universal_section = f"""
上一轮已确认的通用风格：
{universal_str}
"""

        promotion_section = ""
        if promotion_str:
            promotion_section = f"""
以下特征频繁出现（触发次数\u2265{threshold}），请考虑是否应纳入通用：
{promotion_str}
"""

        contextual_section = ""
        if contextual_hint:
            contextual_section = f"""
以下情境表征在观察中，判断是否可以合并到通用风格或特定梗释义中：
{contextual_hint}
"""

        prompt = f"""
分析以下聊天记录，提取该群的三层群聊文化特征。

聊天记录：
```
{history_str}
"""
        prompt += universal_section + promotion_section + contextual_section
        prompt += """
要求：
1. 只返回有效 JSON，不要解释
2. 格式：
{
  "universal": ["特征1", "特征2"],
  "contextual": [
    {"scene": "场景描述", "behavior": "行为描述"},
    ...
  ],
  "specific": [
    {"content": "梗+释义", "trigger_regex": "正则"},
    ...
  ]
}

3. universal 是"这个群整体说话是什么风格"——语气、用词习惯、聊天氛围。属于全群底色。
   至少1条最多10条。如果已有上一轮，从中保留合适的并加入新的。

4. contextual 是"群内存在什么社交模式"——某个场景出现时，群友会有怎样的固定反应。
   格式为 scene（触发条件）\u2192 behavior（群体反应）。没有则留空。

5. specific 是"群里在用什么内部梗/暗号/流行语"——带释义，让外人也能理解。
   content 包含释义（如"xx（用于表达xxx）"），trigger_regex 是能匹配用户相关表达的正则。
   trigger_regex 必须是合法正则。没有则留空。

示例输出：
{"universal": ["爱用表情包", "喜欢玩烂梗", "语气夸张"], "contextual": [{"scene": "有人发消息", "behavior": "全员复读"}, {"scene": "群友自称萌新", "behavior": "假装也是萌新"}], "specific": [{"content": "xx（表达喜欢的意思）", "trigger_regex": "xx|x"}]}}
"""
        return prompt

    async def _parse_and_store_results(self, session_id: str, llm_output: str):
        try:
            json_pattern = r"```json\s*(\{.*?\})\s*```"
            match = re.search(json_pattern, llm_output, re.DOTALL)

            if match:
                json_str = match.group(1)
            else:
                json_str = llm_output[llm_output.find("{") : llm_output.rfind("}") + 1]

            results = json.loads(json_str)

            universal = results.get("universal", [])
            if universal:
                self.data_manager.replace_universal(session_id, universal)
                logger.info(f"[烤箱-风格学习] 为会话 {session_id} 更新通用表征: {universal}")

            contextual = results.get("contextual", [])
            for item in contextual:
                scene = item.get("scene", "")
                behavior = item.get("behavior", "")
                if scene and behavior:
                    self.data_manager.add_contextual(session_id, scene, behavior)

            if contextual:
                items_str = ", ".join(f"{c['scene']}→{c['behavior']}" for c in contextual)
                logger.info(
                    f"[烤箱-风格学习] 为会话 {session_id} 添加情境表征: {items_str}"
                )

            specific = results.get("specific", [])
            for item in specific:
                content = item.get("content", "")
                trigger_regex = item.get("trigger_regex", "")
                if content and trigger_regex:
                    self.data_manager.add_or_update_specific(session_id, content, trigger_regex)

            if specific:
                logger.info(
                    f"[烤箱-风格学习] 为会话 {session_id} 添加特定表征: {[s['content'] for s in specific]}"
                )

            self.data_manager.check_specific_capacity(session_id)

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"[烤箱-风格学习] 解析 LLM 输出失败: {e}\n原始输出: {llm_output}")
