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

import asyncio
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class ForwardHandler:
    """合并转发消息处理

    检测并提取合并转发消息的文本内容。
    """

    def __init__(self):
        self._cache: dict[str, str] = {}

    def get_cached(self, origin: str) -> Optional[str]:
        """获取并移除缓存的转发内容"""
        return self._cache.pop(origin, None)

    def set_cache(self, origin: str, content: str) -> None:
        """缓存转发内容"""
        self._cache[origin] = content

    async def extract(self, event: AstrMessageEvent) -> Optional[str]:
        """检测并提取合并转发消息内容

        遍历消息组件检测 Forward/Reply 组件，
        调用 OneBot11 get_forward_msg API 提取文本内容。

        Args:
            event: 消息事件

        Returns:
            格式化后的合并转发文本，未检测到则返回 None
        """
        message_segments = getattr(event.message_obj, "message", None)
        if not message_segments or not isinstance(message_segments, list):
            logger.debug(f"[烤箱-合并转发] 消息段为空或非列表: {type(message_segments)}")
            return None

        seg_types = []
        for seg in message_segments:
            if isinstance(seg, dict):
                seg_types.append(f"dict:{seg.get('type', 'unknown')}")
            else:
                seg_types.append(type(seg).__name__)
        logger.info(f"[烤箱-合并转发] 消息段类型: {seg_types}")

        forward_id = self._detect_forward_id(message_segments)
        if not forward_id:
            forward_id = await self._detect_from_reply(message_segments, event)

        if not forward_id:
            logger.info("[烤箱-合并转发] 未检测到 forward_id")
            return None

        return await self._fetch_forward_content(forward_id, event)

    def _detect_forward_id(self, segments: list) -> str:
        """从消息段中检测 Forward 组件 ID"""
        try:
            from astrbot.api.message_components import Forward as ForwardComp

            for seg in segments:
                if isinstance(seg, ForwardComp):
                    fid = str(getattr(seg, "id", ""))
                    logger.info(f"[烤箱-合并转发] 检测到 Forward 组件: id={fid}")
                    return fid
                if isinstance(seg, dict) and seg.get("type") == "forward":
                    fid = str(seg.get("data", {}).get("id", ""))
                    logger.info(f"[烤箱-合并转发] 检测到 dict forward: id={fid}")
                    return fid
        except ImportError as e:
            logger.debug(f"[烤箱-合并转发] Forward 组件导入失败: {e}")
        except Exception as e:
            logger.debug(f"[烤箱-合并转发] Forward 检测异常: {e}")
        return ""

    async def _detect_from_reply(self, segments: list, event: AstrMessageEvent) -> str:
        """从 Reply 组件中提取引用的合并转发 ID"""
        try:
            from astrbot.api.message_components import Reply as ReplyComp

            for seg in segments:
                if not isinstance(seg, ReplyComp):
                    continue
                reply_id = getattr(seg, "id", "")
                logger.debug(f"[烤箱-合并转发] 检测到 Reply 组件: id={reply_id}")
                if not reply_id:
                    break
                bot = getattr(event, "bot", None)
                if bot is None:
                    logger.debug("[烤箱-合并转发] event.bot 不存在")
                    break
                try:
                    result = await asyncio.wait_for(
                        bot.call_action("get_msg", message_id=int(str(reply_id))),
                        timeout=5.0,
                    )
                    if result and isinstance(result, dict):
                        original_segments = result.get("message", [])
                        if isinstance(original_segments, list):
                            for segment in original_segments:
                                if (
                                    isinstance(segment, dict)
                                    and segment.get("type") == "forward"
                                ):
                                    fid = str(segment.get("data", {}).get("id", ""))
                                    logger.debug(
                                        f"[烤箱-合并转发] 从 Reply 中提取 forward_id: {fid}"
                                    )
                                    return fid
                except asyncio.TimeoutError:
                    logger.debug(f"[烤箱-合并转发] get_msg 超时: reply_id={reply_id}")
                except Exception as e:
                    logger.debug(f"[烤箱-合并转发] get_msg 失败: {e}")
                break
        except ImportError as e:
            logger.debug(f"[烤箱-合并转发] Reply 组件导入失败: {e}")
        except Exception as e:
            logger.debug(f"[烤箱-合并转发] Reply 检测异常: {e}")
        return ""

    async def _fetch_forward_content(
        self, forward_id: str, event: AstrMessageEvent
    ) -> Optional[str]:
        """调用 API 获取合并转发内容"""
        bot = getattr(event, "bot", None)
        if bot is None:
            logger.info("[烤箱-合并转发] event.bot 不存在，无法调用 API")
            return None

        try:
            forward_data = await asyncio.wait_for(
                bot.call_action("get_forward_msg", id=forward_id),
                timeout=10.0,
            )
            logger.info(
                f"[烤箱-合并转发] get_forward_msg API 返回: "
                f"{type(forward_data)}, "
                f"keys={list(forward_data.keys()) if isinstance(forward_data, dict) else 'N/A'}"
            )
        except asyncio.TimeoutError:
            logger.info(f"[烤箱-合并转发] get_forward_msg API 超时: forward_id={forward_id}")
            return None
        except Exception as e:
            logger.info(f"[烤箱-合并转发] get_forward_msg API 失败: {e}")
            return None

        if not forward_data or not isinstance(forward_data, dict):
            logger.info(f"[烤箱-合并转发] API 返回无效: forward_data={forward_data}")
            return None

        messages = forward_data.get("messages", [])
        logger.info(f"[烤箱-合并转发] messages 数量: {len(messages)}")
        if not messages:
            logger.info("[烤箱-合并转发] messages 为空数组，可能是 NapCat API 限制")
            return None

        return self._format_messages(messages)

    def _format_messages(self, messages: list) -> Optional[str]:
        """格式化合并转发消息为文本"""
        lines: list[str] = ["【合并转发内容】"]
        for node in messages:
            sender_name = node.get("sender", {}).get("nickname", "未知用户") or "未知用户"
            raw_content = node.get("message") or node.get("content", [])

            text_parts: list[str] = []
            if isinstance(raw_content, list):
                for seg in raw_content:
                    if isinstance(seg, dict):
                        seg_type = seg.get("type", "")
                        seg_data = seg.get("data", {})
                        if seg_type == "text":
                            text_parts.append(seg_data.get("text", ""))
                        elif seg_type == "at":
                            text_parts.append(f"[At: {seg_data.get('qq', '')}]")
                        elif seg_type == "image":
                            text_parts.append("[图片]")
            elif isinstance(raw_content, str):
                text_parts.append(raw_content)

            node_text = f"{sender_name}: {''.join(text_parts)}".strip()
            if node_text and node_text != f"{sender_name}:":
                lines.append(node_text)

        if len(lines) == 1:
            return None
        return "\n".join(lines)
