"""Translate proxy-only compression receipts into bounded native continuation."""

from __future__ import annotations

import copy
import json
import re
from functools import wraps

PREFIXES = ("[Compressed ", "[Compression FAILED:", "[Compression refused:")
MAX_RECEIPT = 8192


class ReferenceFilter:
    """Remove orphan ACP reference lines only on the adapted proxy route.

    Preserve code fences and any reference explicitly present in the original
    native input. Hold at most a possible short reference, not whole replies.
    """

    def __init__(self, protected=()):
        self.protected = set(protected)
        self.pending = ""
        self.passing = False
        self.fenced = False

    def feed(self, text, *, final=False):
        output = []
        for char in text:
            if self.passing:
                output.append(char)
                if char == "\n":
                    self.passing = False
                continue
            self.pending += char
            if self.pending.lstrip().startswith(("```", "~~~")):
                self.fenced = not self.fenced
                output.append(self.pending)
                self.pending = ""
                self.passing = True
            elif char == "\n":
                output.append(self._line())
            elif not (
                re.fullmatch(r"[ \t]{0,8}(?:m[0-9]{0,10})?[ \t]{0,8}\r?", self.pending)
                or self.pending.lstrip() in ("`", "``", "~", "~~")
            ):
                output.append(self.pending)
                self.pending = ""
                self.passing = True
        if final:
            output.append(self._line())
        return "".join(output)

    def _line(self):
        line, self.pending = self.pending, ""
        ref = line.strip()
        return (
            ""
            if not self.fenced and re.fullmatch(r"m\d{5,10}", ref) and ref not in self.protected
            else line
        )


def protected_refs(args, kwargs):
    # Protect the current literal input, not old assistant echoes in contexts.
    current = kwargs.get("prompt", args[0] if args else "")
    return re.findall(r"\bm\d{5,10}\b", json.dumps(current, ensure_ascii=False, default=str))


def clean_refs(response, protected):
    response = copy.copy(response)
    response.completion_text = ReferenceFilter(protected).feed(
        response.completion_text or "", final=True
    )
    chain = getattr(response, "result_chain", None)
    if chain and getattr(chain, "chain", None):
        from astrbot.core.message.components import Plain

        response.result_chain = copy.copy(chain)
        response.result_chain.chain = [copy.copy(c) for c in chain.chain]
        parser = ReferenceFilter(protected)
        for item in response.result_chain.chain:
            if isinstance(item, Plain):
                item.text = parser.feed(item.text, final=True)
    return response


def receipt(text):
    return text.startswith(PREFIXES[1:]) or (
        text.startswith(PREFIXES[0])
        and "→" in text
        and " block(s), ~" in text
        and " tokens saved." in text
    )


class ReceiptFilter:
    """Retain only a possible bracketed receipt, never the whole answer stream."""

    def __init__(self):
        self.pending = ""
        self.removed = False
        self.finished = False

    def feed(self, text, *, final=False):
        if self.finished:
            return ""
        self.pending += text or ""
        output = []
        while self.pending:
            start = self.pending.find("[")
            if start < 0:
                output.append(self.pending)
                self.pending = ""
                break
            if start:
                output.append(self.pending[:start])
                self.pending = self.pending[start:]
            possible = any(
                p.startswith(self.pending) or self.pending.startswith(p) for p in PREFIXES
            )
            if not possible:
                output.append(self.pending[0])
                self.pending = self.pending[1:]
                continue
            depth, end = 0, None
            for index, char in enumerate(self.pending):
                depth += (char == "[") - (char == "]")
                if depth == 0:
                    end = index + 1
                    break
            if end is None:
                if not final and len(self.pending) <= MAX_RECEIPT:
                    break
                output.append(self.pending)
                self.pending = ""
                break
            segment = self.pending[:end]
            if receipt(segment):
                self.removed = True
                # bili appends the receipt AFTER the model's visible answer.
                # Its suffix can contain unbracketed fingerprints and next-step
                # maintenance advice, which must remain internal as well.
                self.finished = True
                self.pending = ""
                break
            else:
                output.append(segment)
            self.pending = self.pending[end:]
        return "".join(output)


def clean(text):
    parser = ReceiptFilter()
    visible = parser.feed(text, final=True)
    return visible, parser.removed


def install_reply(bridge, provider):
    """Preserve SDK flow, permissions and cancellation; retry maintenance only once."""

    def active(kwargs):
        return (
            not bridge.plugin.closing
            and kwargs.get("conversation_id")
            and bridge._route(getattr(getattr(provider, "client", None), "base_url", ""))
        )

    original = getattr(provider, "text_chat", None)
    if callable(original):

        @wraps(original)
        async def chat(*args, **kwargs):
            if not active(kwargs):
                return await original(*args, **kwargs)
            previous_usage = None
            for attempt in range(2):
                response = await original(*args, **kwargs)
                text, removed = clean(response.completion_text)
                if removed and not text.strip() and not response.tools_call_name and attempt == 0:
                    # No user tool was executed by this response. The proxy has
                    # updated its own state; replay the unchanged native request.
                    previous_usage = response.usage
                    continue
                if removed or previous_usage:
                    response = copy.copy(response)
                    response.completion_text = text.strip() if removed else text
                    if previous_usage:
                        response.usage = (
                            previous_usage + response.usage if response.usage else previous_usage
                        )
                return clean_refs(response, protected_refs(args, kwargs))

        bridge._replace(provider, "text_chat", chat)

    original_stream = getattr(provider, "text_chat_stream", None)
    if callable(original_stream):

        @wraps(original_stream)
        async def stream(*args, **kwargs):
            previous_usage = None
            for attempt in range(2):
                filtering = bool(active(kwargs))
                parser = ReceiptFilter()
                refs = ReferenceFilter(protected_refs(args, kwargs))
                final_response = None
                iterator = original_stream(*args, **kwargs)
                try:
                    async for response in iterator:
                        if not filtering:
                            yield response
                        elif response.is_chunk:
                            visible = refs.feed(parser.feed(response.completion_text))
                            response = copy.copy(response)
                            response.completion_text = visible
                            yield response
                        else:
                            final_response = response
                finally:
                    await iterator.aclose()
                if not filtering or final_response is None:
                    return
                # Any incomplete bracket was ordinary text, not a verified receipt.
                tail = refs.feed(parser.feed("", final=True), final=True)
                if tail:
                    chunk = copy.copy(final_response)
                    chunk.is_chunk = True
                    chunk.completion_text = tail
                    yield chunk
                text, removed = clean(final_response.completion_text)
                if (
                    removed
                    and not text.strip()
                    and not final_response.tools_call_name
                    and attempt == 0
                ):
                    previous_usage = final_response.usage
                    continue
                final_response = copy.copy(final_response)
                final_response.completion_text = text.strip() if removed else text
                if previous_usage:
                    final_response.usage = (
                        previous_usage + final_response.usage
                        if final_response.usage
                        else previous_usage
                    )
                yield clean_refs(final_response, protected_refs(args, kwargs))
                return

        bridge._replace(provider, "text_chat_stream", stream)
