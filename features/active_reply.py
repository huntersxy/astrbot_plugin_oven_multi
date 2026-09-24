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
# Modified from astrbot_plugin_astrbot_enhance_mode (AGPL-3.0) by 阿汐
# Jev 判定与判读注入参考 astrbot_plugin_jev_intent_boost (MIT) by 汐兮雨

import datetime
import random
import re
from collections import defaultdict

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .jev_judge import JevJudge, build_state, decide_reply, format_block

# 每个会话保留的判读上下文条数下限
_HISTORY_LIMIT_MIN = 60


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def strip_mentions(text: str) -> str:
    """去掉开头连续的 @xxx 标记，露出正文（被@场景的长度闸门用）。"""
    return re.sub(r"^((@\S+)[ \t]*)+", "", text).strip()


def _dbg(jev: dict, message: str) -> None:
    """debug_mode 开启时输出一行判读决策轨迹（INFO，WebUI 日志可见）。"""
    if jev.get("debug_mode"):
        logger.info(f"[Jev-DEBUG] {message}")


class ActiveReply:
    """主动回复与 Jev 判读注入。

    - ``record_history``：记录群消息（含@消息）供 Jev 判读上下文，与触发闸门
      解耦，由主插件最高优先级 handler 调用。
    - ``should_active_reply``：主动回复触发。
      - ``probability``：按概率随机触发；命中且 ``jev.inject_on_active_reply``
        开启时调 Jev 做六维判读并挂到事件 extra（判读失败不影响触发）。
      - ``model_choice``：累计 ``model_stack_size`` 条消息后交由 Jev 判定是否
        触发（代替 LLM 文本判定）；判为「回复」且置信度达标才触发。
    - ``mention_reading``：被@/唤醒触发（非主动回复）时的一次多维判读，
      重点是意图与情绪；产出注入块，失败仅告警降级。

    判读块由主插件在 ``on_llm_request`` 中以临时内容注入本轮 LLM 请求，
    不写入对话历史。
    """

    def __init__(self):
        self.stacks: dict[str, list[str]] = defaultdict(list)
        self.histories: dict[str, list[str]] = defaultdict(list)
        self.jev = JevJudge()

    async def close(self) -> None:
        """释放 Jev 客户端的 HTTP 会话。"""
        await self.jev.close()

    # ── 配置 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _jev_cfg(config: dict) -> dict:
        jev = (config or {}).get("jev", {})
        return jev if isinstance(jev, dict) else {}

    @staticmethod
    def _ar_cfg(config: dict) -> dict:
        ar = (config or {}).get("active_reply", {})
        return ar if isinstance(ar, dict) else {}

    @staticmethod
    def _stack_size(ar: dict) -> int:
        return max(1, _int(ar.get("model_stack_size", 8), 8))

    @staticmethod
    def _whitelist_pass(ar: dict, event: AstrMessageEvent) -> bool:
        """主动回复白名单，同时作为 Jev 判读注入的群范围闸门。"""
        whitelist = str(ar.get("whitelist", "") or "").strip()
        if not whitelist:
            return True
        allowed = [x.strip() for x in whitelist.split(",") if x.strip()]
        if not allowed:
            return True
        origin = event.unified_msg_origin
        gid = str(event.get_group_id() or "")
        return origin in allowed or gid in allowed

    # ── 历史记录（与触发闸门解耦，保证判读有上下文） ─────────────────────

    def record_history(self, event: AstrMessageEvent, config: dict) -> bool:
        """记录一条群消息供 Jev 判读上下文；返回是否记录。

        由主插件最高优先级 handler 调用，因此 @消息、非唤醒消息也会入史；
        是否记录只看 jev 开关与白名单，与主动回复是否启用无关。
        """
        jev = self._jev_cfg(config)
        if not jev.get("enabled", False):
            _dbg(jev, f"历史未记录：jev.enabled=false | origin={event.unified_msg_origin}")
            return False
        ar = self._ar_cfg(config)
        if not self._whitelist_pass(ar, event):
            _dbg(
                jev,
                f"历史未记录：白名单未放行 | origin={event.unified_msg_origin}",
            )
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
        history_limit = max(_HISTORY_LIMIT_MIN, self._stack_size(ar) * 6)
        if len(history) > history_limit:
            del history[:-history_limit]
        return True

    @staticmethod
    def _line(event: AstrMessageEvent, text: str) -> str:
        msg = event.message_obj
        nickname = str(getattr(getattr(msg, "sender", None), "nickname", "") or "?")
        now = datetime.datetime.now().strftime("%H:%M:%S")
        return f"[{nickname}/{event.get_sender_id()} {now}]: {text}"

    # ── 入口：主动回复触发 ────────────────────────────────────────────────

    async def should_active_reply(self, event: AstrMessageEvent, config: dict) -> bool:
        ar = self._ar_cfg(config)
        if not ar.get("enable", False):
            return False
        if event.is_at_or_wake_command:
            return False
        if event.get_sender_id() == event.get_self_id():
            return False
        if not self._whitelist_pass(ar, event):
            return False

        msg = event.message_obj
        if not msg or not getattr(msg, "message", None):
            return False
        if not any(
            isinstance(comp, Comp.Plain) and (comp.text or "").strip()
            for comp in msg.message
        ):
            return False
        text = (event.get_message_str() or "").strip()
        if not text:
            return False

        origin = event.unified_msg_origin
        mode = str(ar.get("mode", "probability") or "probability").strip()
        if mode == "model_choice":
            # 触发栈只累积非唤醒消息（唤醒消息本身已有回复链路）
            self.stacks[origin].append(self._line(event, text))
            return await self._model_choice(config, ar, event, origin)
        return await self._probability(config, ar, event, origin, text)

    # ── 概率模式：命中后调 Jev 注入判读 ──────────────────────────────────

    async def _probability(
        self, config: dict, ar: dict, event: AstrMessageEvent, origin: str, text: str
    ) -> bool:
        possibility = _float(ar.get("possibility", 0.1), 0.1)
        hit = random.random() < possibility
        logger.info(
            f"[烤箱-主动回复] probability | origin={origin} "
            f"{'命中' if hit else '未命中'}"
        )
        if not hit:
            return False

        jev = self._jev_cfg(config)
        if not (jev.get("enabled", False) and jev.get("inject_on_active_reply", True)):
            _dbg(
                jev,
                f"命中但不判读 | enabled={jev.get('enabled', False)} "
                f"inject_on_active_reply={jev.get('inject_on_active_reply', True)} "
                f"| origin={origin}",
            )
            return True  # 纯概率触发：不判不注

        result = await self._run_jev(config, origin, text, with_decision=False)
        if result is not None:
            self._attach(config, event, result, scene="active")
            logger.info(
                f"[烤箱-主动回复] 概率命中，已挂载 Jev 判读 | origin={origin} "
                f"说话对象={result.get('addressed')} 意图={result.get('intent')} "
                f"情绪={result.get('emotion')}"
            )
        return True

    # ── 模型判定模式：Jev 代替 LLM 判定是否触发 ─────────────────────────

    async def _model_choice(
        self, config: dict, ar: dict, event: AstrMessageEvent, origin: str
    ) -> bool:
        stack = self.stacks[origin]
        stack_size = self._stack_size(ar)
        if len(stack) < stack_size:
            logger.info(
                f"[烤箱-主动回复] model_choice | 栈填充 | origin={origin} "
                f"progress={len(stack)}/{stack_size}"
            )
            return False
        stack.clear()

        jev = self._jev_cfg(config)
        if not jev.get("enabled", False):
            logger.warning(
                "[烤箱-主动回复] model_choice 模式由 Jev 判定，"
                "但 jev 未启用，本轮跳过"
            )
            return False

        result = await self._run_jev(config, origin, None, with_decision=True)
        if result is None:
            return False
        min_confidence = _float(jev.get("decision_min_confidence", 0.6), 0.6)
        reply = decide_reply(result, min_confidence)
        logger.info(
            f"[烤箱-主动回复] model_choice/Jev | {'触发' if reply else '跳过'} | "
            f"origin={origin} should_reply={result.get('should_reply')}"
            f"({float(result.get('should_reply_confidence') or 0.0):.2f}) "
            f"说话对象={result.get('addressed')} 意图={result.get('intent')}"
        )
        if reply and jev.get("inject_on_active_reply", True):
            self._attach(config, event, result, scene="active")
        return reply

    # ── 被@/唤醒触发：一次多维判读（重点：意向与情绪） ─────────────────────

    async def mention_reading(
        self, event: AstrMessageEvent, config: dict
    ) -> str | None:
        """被@/唤醒触发（非主动回复）时调用一次 Jev 判读，返回注入块。

        只产出六维判读（不消费 should_reply 判定结果）；失败仅告警并降级为
        普通回复，不影响本轮响应。
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
        if event.get_extra("oven_active_reply_triggered", False):
            _dbg(jev, "被@判读跳过：本条已由主动回复路径挂载判读")
            return None  # 主动回复路径自行挂载
        if not self._whitelist_pass(self._ar_cfg(config), event):
            _dbg(jev, f"被@判读跳过：白名单未放行 | origin={event.unified_msg_origin}")
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
        lines = self.histories[origin]
        if not lines:
            # 兜底：历史为空（功能刚启用的首条消息）时以当前消息为 state
            lines = [self._line(event, text)]

        state = build_state(
            lines,
            history_rounds=_int(jev.get("history_rounds", 6), 6),
            max_chars=_int(jev.get("max_state_chars", 1200), 1200),
            redact=bool(jev.get("desensitize", True)),
        )
        if not state:
            _dbg(jev, "被@判读跳过：state 为空（历史与当前消息皆无内容）")
            return None

        _dbg(
            jev,
            f"被@判读调用 Jev | origin={origin} 上下文{len(lines)}条 "
            f"state({len(state)}字)",
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
        return block or None

    # ── Jev 判读公共流程 ─────────────────────────────────────────────────

    async def _run_jev(
        self,
        config: dict,
        origin: str,
        text: str | None,
        *,
        with_decision: bool,
    ) -> dict | None:
        """调用 Jev 做判读；失败/跳过返回 None（不抛）。"""
        jev = self._jev_cfg(config)
        if not jev.get("enabled", False):
            return None

        # 概率模式的逐条调用省钱闸门：过短消息不值得判一次
        if not with_decision and text is not None:
            min_chars = _int(jev.get("min_message_chars", 2), 2)
            if min_chars > 0 and len(re.sub(r"\s+", "", text)) < min_chars:
                _dbg(
                    jev,
                    f"跳过判读：消息过短（<{min_chars} 字）| origin={origin}",
                )
                return None

        history_rounds = _int(jev.get("history_rounds", 6), 6)
        if with_decision:
            # 判定要不要接话时至少看全整个触发栈
            history_rounds = max(history_rounds, self._stack_size(self._ar_cfg(config)))
        state = build_state(
            self.histories[origin],
            history_rounds=history_rounds,
            max_chars=_int(jev.get("max_state_chars", 1200), 1200),
            redact=bool(jev.get("desensitize", True)),
        )
        if not state:
            _dbg(
                jev,
                f"跳过判读：state 为空（历史未记录？jev.enabled 需保持开启）| origin={origin}",
            )
            return None

        _dbg(
            jev,
            f"调用 Jev | origin={origin} decision={with_decision} "
            f"state({len(state)}字)",
        )
        result = await self.jev.judge(jev, state, with_decision=with_decision)
        if not result.get("ok"):
            logger.warning(f"[烤箱-主动回复] Jev 判定失败：{result.get('error')}")
            return None
        return result

    def _attach(
        self, config: dict, event: AstrMessageEvent, result: dict, *, scene: str
    ) -> None:
        """把判读块挂到事件 extra，供主插件注入本轮 LLM 请求。"""
        jev = self._jev_cfg(config)
        block = format_block(
            result,
            scene=scene,
            include_advice=bool(jev.get("enable_advice", True)),
            show_confidence=bool(jev.get("show_confidence", True)),
            max_chars=_int(jev.get("max_injection_chars", 600), 600),
        )
        if block:
            event.set_extra("oven_jev_reading", block)
