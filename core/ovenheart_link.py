"""Optional, versioned services for OvenHeart; no import dependency on that plugin."""

from __future__ import annotations

import hashlib
import json
import re

from astrbot.api import logger
from astrbot.api.platform import MessageType
from astrbot.core.star.session_plugin_manager import SessionPluginManager

from .constants import PLUGIN_NAME
from .jev_judge import build_advice, build_state


class OvenHeartLink:
    def __init__(self, plugin):
        self.plugin = plugin

    def core(self):
        lookup = getattr(self.plugin.context, "get_registered_star", None)
        meta = lookup("astrbot_plugin_ovenheart") if callable(lookup) else None
        core = meta.star_cls if meta and meta.activated else None
        if core is None or not getattr(core, "ready", False) or getattr(core, "closing", False):
            return None
        settings = getattr(core, "settings", {})
        return core if settings.get("enabled") and settings.get("apply_enabled") else None

    async def owned(self, event):
        core = self.core()
        if core is None:
            return False
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return False
        method = getattr(core, "owns_group_reply", None)
        if callable(method):
            return bool(await method(event))
        # Older OvenHeart versions already own reply addressing. Respect their
        # native permissions during a rolling upgrade as well.
        permitted = getattr(core, "permitted", None)
        return bool(callable(permitted) and await permitted(event))

    @staticmethod
    def reply_text(event, fallback: str) -> str:
        """Use the frozen reply target even when it contains no text."""
        plan = event.get_extra("ovenheart_decision")
        if plan:
            return (plan.get("target") or {}).get("text", "")
        direct = event.get_extra("ovenheart_direct_context")
        if direct:
            return (direct.get("reply_message") or {}).get("text", "")
        return fallback

    async def record_partial_delivery(self, event):
        """A stopped native hook will skip OvenHeart's later completion hook."""
        core = self.core()
        record = getattr(core, "record_delivery", None)
        if callable(record) and event.get_extra("ovenheart_actual_text_segments"):
            await record(event)

    async def jev_available(self, event):
        cfg = self.plugin.config.get("jev", {})
        if (
            self.plugin.closing
            or self.plugin._blocked(event)
            or not cfg.get("enabled")
            or not cfg.get("inject_on_mention", True)
            or not str(cfg.get("api_key") or "").strip()
        ):
            return False
        plugins = self.plugin.context.get_config(event.unified_msg_origin).get("plugin_set", ["*"])
        if plugins != ["*"] and PLUGIN_NAME not in plugins:
            return False
        return await SessionPluginManager.is_plugin_enabled_for_session(
            event.unified_msg_origin, PLUGIN_NAME
        )

    def signature(self):
        cfg = self.plugin.config.get("jev", {})
        return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def state(payload, cfg):
        messages = list(payload.get("messages", [])[-40:])
        focus = payload.get("focus", {})
        present = {m.get("source") for m in messages}
        for item in [*focus.get("same_sender_before", []), *focus.get("recent_self", [])]:
            if item.get("source") not in present:
                messages.append(item)
                present.add(item.get("source"))
        messages.sort(key=lambda m: m.get("time") or 0)
        target = payload["target"]
        aliases = {}
        for message in [*messages, target]:
            for identity in (
                message.get("sender_id"),
                (message.get("reply_to") or {}).get("sender_id"),
            ):
                if identity:
                    aliases.setdefault(str(identity), f"member_{len(aliases) + 1}")
        bot_ids = {str(m.get("sender_id")) for m in messages if m.get("self")} | {
            str(payload.get("bot_sender_id", ""))
        }
        for identity in bot_ids:
            if identity:
                aliases[identity] = "bot"

        def clean(text):
            text = str(text or "")
            for identity, alias in aliases.items():
                if identity.isdigit():
                    text = re.sub(
                        r"(?<!\d)" + re.escape(identity) + r"(?!\d)",
                        "bot" if identity in bot_ids else alias,
                        text,
                    )
            # Unmapped structured @ IDs must not be sent to the external judge.
            return re.sub(r"\(\d{5,}\)", "(member)", text)

        def line(message):
            identity = str(message.get("sender_id", ""))
            speaker = "bot (self)" if message.get("self") else aliases.get(identity, "member")
            text = clean(message.get("text"))[:700]
            if message.get("source") == target.get("source") and payload.get("direct_target"):
                text = "(directly addressed to bot) " + text
            if message.get("source") == target.get("source") and payload.get("bare_mention"):
                text = (
                    "@bot (standalone mention; interpret earlier same-member context, "
                    "not automatically a greeting)"
                )
            quote = message.get("reply_to") or {}
            if quote.get("text"):
                text += " | quoted: " + clean(quote["text"])[:300]
            media = [
                clean(m.get("text"))[:240] or str(m.get("status", "present"))
                for m in message.get("media", [])[:2]
            ]
            if media:
                text += " | image/media: " + "; ".join(media)
            return f"[{speaker}]: {text or '(no text)'}"

        history = [m for m in messages if m.get("source") != target.get("source")]
        # Keep actual same-member context and self speech when a noisy room would
        # otherwise replace all relevant lines with other members' chatter.
        count = max(0, int(cfg.get("history_rounds", 6)))
        ranked = sorted(
            enumerate(history),
            key=lambda item: (
                item[1].get("sender_id") == target.get("sender_id") or bool(item[1].get("self")),
                item[0],
            ),
            reverse=True,
        )[:count]
        lines = [line(m) for _, m in sorted(ranked)] + [line(target)]
        return build_state(
            lines,
            history_rounds=count,
            max_chars=int(cfg.get("max_state_chars", 1200)),
            redact=bool(cfg.get("desensitize", True)),
        )

    async def analyze(self, event, payload):
        if not await self.jev_available(event):
            return {"status": "disabled"}
        cfg = self.plugin.config.get("jev", {})
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(payload.get("target"), dict)
            or not isinstance(payload.get("messages"), list)
        ):
            return {"status": "unsupported"}
        core = self.core()
        if (
            core is None
            or getattr(core, "oven_multi_protocol", None) != 1
            or not await self.owned(event)
        ):
            return {"status": "unavailable"}
        scope, _, _ = await core.resolve_scope(event)
        if payload.get("scope") != scope:
            return {"status": "stale"}
        epoch = payload.get("epoch")
        if epoch and not await core.conversation.valid(event, scope, epoch):
            return {"status": "stale"}
        target = payload["target"]
        if (
            not payload.get("bare_mention")
            and not target.get("media")
            and len(str(target.get("text", "")).strip()) < int(cfg.get("min_message_chars", 2))
        ):
            return {"status": "short"}
        cfg = dict(cfg)
        # Retries here would delay the primary reply. The owning plugin supplies
        # a hard total deadline and continues without optional judgment on failure.
        cfg["retries"] = 0
        result = await self.plugin.jev_scenes.jev.judge(
            cfg, self.state(payload, cfg), with_decision=False, dimensions=("intent", "emotion")
        )
        if not await self.jev_available(event):
            return {"status": "stale"}
        if not result.get("ok"):
            logger.warning("Jev 联动判读未完成，烤箱之心继续原流程")
            return {"status": "error"}
        # A bounded, explicit contract: no raw API data, credentials or independent
        # reply decisions cross into the core. Keep user presentation preferences.
        fields = {key: str(result.get(key) or "")[:80] for key in ("intent", "emotion")}
        advice = build_advice(result)[:4] if cfg.get("enable_advice", True) else []
        reading = {"fields": fields, "advice": advice}
        budget = max(100, int(cfg.get("max_injection_chars", 600)))

        def fits():
            return len(json.dumps(reading, ensure_ascii=False)) <= budget

        while advice and not fits():
            advice.pop()
        if cfg.get("show_confidence", True):
            for key in ("intent", "emotion"):
                confidence = result.get(f"{key}_confidence")
                if isinstance(confidence, (float, int)) and 0 <= confidence <= 1:
                    fields[f"{key}_confidence"] = round(confidence, 3)
                    if not fits():
                        fields.pop(f"{key}_confidence")
        if not fits():
            return {"status": "oversize"}
        return {"status": "ready", **reading}
