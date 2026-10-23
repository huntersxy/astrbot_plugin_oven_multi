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
# Jev 判定与判读注入参考 astrbot_plugin_jev_intent_boost (MIT) by 汐兮雨

import datetime
import re
from collections import defaultdict

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .jev_judge import JevJudge, build_state, format_block

# 每个会话保留的判读上下文条数下限
_HISTORY_LIMIT_MIN = 60


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def strip_mentions(text: str) -> str:
    """去掉开头连续的 @xxx 标记，露出正文（被@场景的长度闸门用）。"""
    return re.sub(r"^((@\S+)[ \t]*)+", "", text).strip()


def _dbg(jev: dict, message: str) -> None:
    """debug_mode 开启时输出一行判读决策轨迹（INFO，WebUI 日志可见）。"""
    if jev.get("debug_mode"):
        logger.info(f"[Jev-DEBUG] {message}")


class JevScenes:
    """Jev 判读的场景接入层（原主动回复模块的判读部分）。

    主动回复触发已移除（由烤箱之心的「群聊参与」接棒），本类只保留两件事：

    - ``record_history``：记录群消息（含@消息）供 Jev 判读上下文，由主插件
      最高优先级 handler 调用，与任何触发闸门解耦。
    - ``mention_reading``：被@/唤醒触发时的一次多维判读，重点是意图与情绪；
      产出注入块，失败仅告警降级。

    判读块由主插件在 ``on_llm_request`` 中以临时内容注入本轮 LLM 请求，
    不写入对话历史。
    """

    def __init__(self, context=None):
        self.histories: dict[str, list[str]] = defaultdict(list)
        self.jev = JevJudge()
        # AstrBot star Context：提供原生持久化消息历史读取（可选，离线测试为 None）
        self.context = context

    async def close(self) -> None:
        """释放 Jev 客户端的 HTTP 会话。"""
        await self.jev.close()

    # ── 配置 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _jev_cfg(config: dict) -> dict:
        jev = (config or {}).get("jev", {})
        return jev if isinstance(jev, dict) else {}

    # ── 历史记录（与触发闸门解耦，保证判读有上下文） ─────────────────────

    def record_history(self, event: AstrMessageEvent, config: dict) -> bool:
        """记录一条群消息供 Jev 判读上下文；返回是否记录。

        由主插件最高优先级 handler 调用，因此 @消息、非唤醒消息也会入史；
        是否记录只看 jev 开关。
        """
        jev = self._jev_cfg(config)
        if not jev.get("enabled", False):
            _dbg(jev, f"历史未记录：jev.enabled=false | origin={event.unified_msg_origin}")
            return False

        msg = event.message_obj
        if not msg:
            return False
        text = (event.get_message_str() or "").strip()
        if not text:
            return False

        origin = event.unified_msg_origin
        line = self._line(event, text)
        history = self.histories[origin]
        history.append(line)
        rounds = _int(jev.get("history_rounds", 6), 6)
        history_limit = max(_HISTORY_LIMIT_MIN, rounds * 6)
        if len(history) > history_limit:
            del history[:-history_limit]
        return True

    @staticmethod
    def _line(event: AstrMessageEvent, text: str) -> str:
        msg = event.message_obj
        nickname = str(getattr(getattr(msg, "sender", None), "nickname", "") or "?")
        now = datetime.datetime.now().strftime("%H:%M:%S")
        # 不带发送者 QQ 号：判读不需要，且会原样发往第三方
        return f"[{nickname} {now}]: {text}"

    async def _native_lines(
        self, event: AstrMessageEvent, config: dict, rounds: int
    ) -> list[str] | None:
        """读取 AstrBot 原生持久化消息历史；不可用返回 None（回退插件缓冲）。

        需原生「平台消息历史」开关（provider_ltm_settings.group_message_history_enable）
        开启。返回按时间升序、**不含当前消息**（按当前事件的记录 id 过滤）的行；
        行格式 ``[昵称 时间]: 内容``，天然不含发送者 QQ 号，且机器人自己的回复
        （role=bot）也在内。
        """
        if self.context is None or rounds <= 0:
            return None
        jev = self._jev_cfg(config)
        try:
            ltm = (
                self.context.get_config(umo=event.unified_msg_origin).get(
                    "provider_ltm_settings", {}
                )
                or {}
            )
            if not ltm.get("group_message_history_enable"):
                return None
            current_id = event.get_extra("_current_platform_message_history_id")
            if current_id is None:
                return None  # 当前事件没有原生记录（本条未入史），回退缓冲
            history = await self.context.message_history_manager.get(
                platform_id=event.get_platform_id(),
                user_id=event.unified_msg_origin,
                page_size=max(rounds * 3, 30),
            )
        except Exception as e:  # noqa: BLE001
            _dbg(jev, f"原生历史读取失败，回退插件缓冲：{type(e).__name__}: {e}")
            return None

        lines: list[str] = []
        for rec in history or []:
            try:
                rid = getattr(rec, "id", None)
                if rid is not None and int(rid) >= int(current_id):
                    continue  # 当前消息及之后的行不含（当前条由缓冲补上）
                line = self._format_native(rec)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping invalid native history row (%s)", type(exc).__name__)
                continue
            if line:
                lines.append(line)
        return lines[-rounds:]

    @staticmethod
    def _format_native(rec) -> str | None:
        """把原生持久化记录格式化为判读行；纯图片等无文本记录返回 None。"""
        content = rec.content if isinstance(getattr(rec, "content", None), dict) else {}
        parts = content.get("message") or []
        texts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "plain":
                texts.append(str(part.get("text") or ""))
            elif ptype == "at":
                name = str(part.get("name") or "").strip()
                texts.append(f"@{name}" if name else "@")  # 不输出 user_id（QQ 号）
            elif ptype == "reply":
                who = str(part.get("sender_name") or "").strip()
                texts.append(f"(回复{who})" if who else "(回复)")
        text = " ".join(t.strip() for t in texts if t and str(t).strip()).strip()
        if not text:
            return None
        name = str(getattr(rec, "sender_name", "") or "").strip()
        if not name:
            name = "bot" if content.get("type") == "bot" else "某成员"
        ts = ""
        created = getattr(rec, "created_at", None)
        if created is not None:
            try:
                ts = " " + created.strftime("%H:%M:%S")
            except Exception:  # noqa: BLE001
                ts = ""
        return f"[{name}{ts}]: {text}"

    async def _state_lines(
        self, event: AstrMessageEvent, config: dict, origin: str, rounds: int
    ) -> tuple[list[str], str]:
        """判读上下文行：优先原生持久化历史，回退插件内存缓冲。

        返回 ``(lines, source)``；``lines`` 末尾恒为当前消息行（build_state 约定）。
        """
        native = await self._native_lines(event, config, rounds)
        if native is not None:
            lines = list(native)
            current = self.histories[origin][-1:]
            if not current:
                text = (event.get_message_str() or "").strip()
                current = [self._line(event, text)] if text else []
            lines.extend(current)
            return lines, "原生历史"
        return list(self.histories[origin]), "插件缓冲"

    # ── 被@/唤醒触发：一次多维判读（重点：意向与情绪） ─────────────────────

    async def mention_reading(self, event: AstrMessageEvent, config: dict) -> str | None:
        """被@/唤醒触发时调用一次 Jev 判读，返回注入块。

        只产出六维判读；失败仅告警并降级为普通回复，不影响本轮响应。
        """
        jev = self._jev_cfg(config)
        if not jev.get("enabled", False) or not jev.get("inject_on_mention", True):
            _dbg(
                jev,
                "被@判读跳过 | "
                f"enabled={jev.get('enabled', False)} "
                f"inject_on_mention={jev.get('inject_on_mention', True)}",
            )
            return None
        if not getattr(event, "is_at_or_wake_command", False):
            _dbg(jev, "被@判读跳过：非唤醒消息（未@/未用唤醒前缀/非引用）")
            return None

        text = (event.get_message_str() or "").strip()
        if not text or text.startswith("/"):
            _dbg(jev, f"被@判读跳过：无正文或指令消息 | text={text[:30]!r}")
            return None
        # 正文长度闸门：先去掉开头的 @xxx 标记，纯 "@机器人" 不值得判一次
        content = strip_mentions(text)
        min_chars = _int(jev.get("min_message_chars", 2), 2)
        if min_chars > 0 and len(re.sub(r"\s+", "", content)) < min_chars:
            _dbg(
                jev,
                f"被@判读跳过：去掉@标记后正文过短（{len(content)}<{min_chars}）",
            )
            return None

        origin = event.unified_msg_origin
        rounds = _int(jev.get("history_rounds", 6), 6)
        lines, source = await self._state_lines(event, config, origin, rounds)
        if not lines:
            # 兜底：两端皆空（原生与缓冲都不可用）时以当前消息为 state
            lines = [self._line(event, text)]

        state = build_state(
            lines,
            history_rounds=rounds,
            max_chars=_int(jev.get("max_state_chars", 1200), 1200),
            redact=bool(jev.get("desensitize", True)),
        )
        if not state:
            _dbg(jev, "被@判读跳过：state 为空（历史与当前消息皆无内容）")
            return None

        _dbg(
            jev,
            f"被@判读调用 Jev | origin={origin} 上下文来源={source} "
            f"共{len(lines)}条 state({len(state)}字)",
        )
        result = await self.jev.judge(jev, state, with_decision=False)
        if not result.get("ok"):
            logger.warning(f"[烤箱-被@回复] Jev 判读失败：{result.get('error')}")
            return None

        block = format_block(
            result,
            scene="mention",
            include_advice=bool(jev.get("enable_advice", True)),
            show_confidence=bool(jev.get("show_confidence", True)),
            max_chars=_int(jev.get("max_injection_chars", 600), 600),
        )
        if block:
            logger.info(
                f"[烤箱-被@回复] 已注入 Jev 判读 | origin={origin} "
                f"意图={result.get('intent')} 情绪={result.get('emotion')} "
                f"说话对象={result.get('addressed')}"
            )
            _dbg(jev, f"注入块全文({len(block)}字) | scene=mention:\n{block}")
        return block or None
