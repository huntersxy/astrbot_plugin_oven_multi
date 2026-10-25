"""Optional standalone Jev reply admission; OvenHeart always owns shared groups."""

from __future__ import annotations

import asyncio
import re
import time
import weakref

from astrbot.core.star.session_llm_manager import SessionServiceManager

from .jev_judge import build_state
from .jev_transport import REPLY_QUESTION, SystemOne, probability


class JevActive:
    def __init__(self, plugin):
        self.plugin = plugin
        self.client = SystemOne()
        self.checked = {}
        self.latest = {}
        self.active = {}

    def room(self, event):
        return (
            event.get_platform_id()
            if hasattr(event, "get_platform_id")
            else event.unified_msg_origin.split(":")[0],
            event.get_self_id(),
            event.get_group_id()
            if hasattr(event, "get_group_id")
            else event.unified_msg_origin.rsplit(":", 1)[-1],
        )

    def notice(self, event, owned=False):
        room = self.room(event)
        self.latest[room] = id(event)
        if len(self.latest) > 128:
            self.latest.pop(next(iter(self.latest)))
        if owned or event.is_at_or_wake_command:
            entry = self.active.pop(room, None)
            if entry:
                previous, task = entry[0](), entry[1]()
                if previous and task and not task.done() and task is not asyncio.current_task():
                    previous.set_extra("agent_stop_requested", True)
                    previous.stop_event()
                    previous.clear_result()
                    if not task.cancelling():
                        task.cancel()

    async def consider(self, event):
        plugin = self.plugin
        cfg = plugin.config.get("jev", {})
        if not cfg.get("enabled") or not cfg.get("active_reply_enabled", False):
            return False
        if plugin.closing or plugin._blocked(event) or await plugin.ovenheart.owned(event):
            return False
        if (
            event.is_at_or_wake_command
            or event.call_llm
            or getattr(event, "_has_send_oper", False)
            or event.get_extra("provider_request")
            or event.get_result()
            or event.get_extra("handlers_parsed_params")
            or event.get_sender_id() == event.get_self_id()
        ):
            return False
        if any(
            type(f).__name__ in ("CommandFilter", "CommandGroupFilter", "RegexFilter")
            for handler in event.get_extra("activated_handlers", [])
            for f in handler.event_filters
        ):
            return False
        text = event.get_message_str().strip()
        if not text or text.startswith("/") or len(text) < int(cfg.get("min_message_chars", 2)):
            return False
        host = plugin.context.get_config(event.unified_msg_origin)
        if not host.get("provider_settings", {}).get("enable", True):
            return False
        if not await SessionServiceManager.should_process_llm_request(event):
            return False
        room = self.room(event)
        now = time.monotonic()
        if now - self.checked.get(room, -1e10) < int(cfg.get("active_interval_sec", 60)):
            return False
        self.checked[room] = now
        if len(self.checked) > 128:
            self.checked.pop(next(iter(self.checked)))
        ticket = id(event)
        self.latest[room] = ticket
        task = asyncio.current_task()
        entry = (weakref.ref(event), weakref.ref(task))
        self.active[room] = entry
        task.add_done_callback(
            lambda _: self.active.pop(room, None) if self.active.get(room) is entry else None
        )
        signature = plugin.ovenheart.signature()
        # Use per-event pseudonyms; numeric QQ IDs never form speaker labels.
        lines = [
            str(line) for line in plugin.jev_scenes.histories.get(event.unified_msg_origin, [])
        ]
        if lines and lines[-1] == event.get_extra("oven_multi_jev_history_line"):
            lines.pop()  # The early observer already recorded this exact event.
        lines = lines[-int(cfg.get("history_rounds", 6)) :]
        lines.append("[target member]: " + text)
        if cfg.get("desensitize", True):
            lines = [re.sub(r"(?<!\d)\d{5,}(?!\d)", "[member_or_number]", line) for line in lines]
        result = await self.client.evaluate(
            cfg,
            build_state(
                lines,
                int(cfg.get("history_rounds", 6)),
                int(cfg.get("max_state_chars", 1200)),
                bool(cfg.get("desensitize", True)),
            ),
            {"should_reply": REPLY_QUESTION},
        )
        value = probability((result.get("answers") or {}).get("should_reply"))
        if (
            signature != plugin.ovenheart.signature()
            or self.latest.get(room) != ticket
            or event.is_stopped()
            or await plugin.ovenheart.owned(event)
        ):
            return False
        if value is None or value < float(cfg.get("active_reply_threshold", 0.6)):
            return False
        event.set_extra("oven_multi_jev_active", {"probability": value})
        event.is_at_or_wake_command = True
        return True

    async def close(self):
        current = asyncio.current_task()
        for previous, task in list(self.active.values()):
            owner, task = previous(), task()
            if owner:
                owner.stop_event()
            if task and task is not current and not task.done() and not task.cancelling():
                task.cancel()
        self.active.clear()
        await self.client.close()
