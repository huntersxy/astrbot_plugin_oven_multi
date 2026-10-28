"""Keep headerless helper calls out of bili's persistent conversation pipeline.

Only supported runtime HTTP clients are adapted, reversibly, on their instances.
Request bodies, conversation IDs, provider settings and global SDK classes stay intact.
"""

from __future__ import annotations

import inspect
from functools import wraps
from urllib.parse import urlsplit

from .bili_reply import install_reply
from .bili_tools import BiliNativeTools

CONVERSATION_HEADER = "x-astrbot-conversation-id"
PASSTHROUGH_HEADER = "x-bili-passthrough"


class BiliRequestBridge:
    def __init__(self, plugin):
        self.plugin = plugin
        self._manager = None
        self._hooks = []
        self._clients = {}
        self._providers = {}
        self.native_tools = BiliNativeTools(self)

    def _replace(self, owner, name, wrapper):
        original = getattr(owner, name)
        local = name in vars(owner)
        setattr(owner, name, wrapper)
        self._hooks.append((owner, name, original, wrapper, local))

    def disconnect(self):
        self.native_tools.bound.clear()
        for owner, name, original, wrapper, local in reversed(self._hooks):
            if getattr(owner, name, None) is wrapper:
                if local:
                    setattr(owner, name, original)
                else:
                    delattr(owner, name)
        self._hooks.clear()
        self._clients.clear()
        self._providers.clear()
        self._manager = None

    def _route(self, url):
        """Trust only the configured proxy origin and its /bili/ prefix."""
        try:
            value = urlsplit(str(url))
            expected = urlsplit(self.plugin.bili_proxy.url)
            return (value.scheme, value.hostname, value.port) == (
                expected.scheme,
                expected.hostname,
                expected.port,
            ) and value.path.startswith("/bili/")
        except ValueError:
            return False

    def attach(self, provider):
        automatic = getattr(self.plugin, "bili_auto", None)
        if automatic is not None:
            automatic.sync_runtime(provider)
        sdk = getattr(provider, "client", None)
        if not self._route(getattr(sdk, "base_url", "")):
            return
        client = getattr(sdk, "_client", None)
        original = getattr(client, "send", None)
        if (
            client is None
            or not inspect.iscoroutinefunction(original)
            or getattr(client, "is_closed", False)
            or not hasattr(client, "__dict__")
        ):
            return
        if id(provider) not in self._providers and hasattr(provider, "__dict__"):
            install_reply(self, provider)
            self._providers[id(provider)] = provider
        if id(client) in self._clients:
            return

        @wraps(original)
        async def send(request, *args, **kwargs):
            # The SDK constructs a separate Request/Headers for each attempt.
            # Applying the header here also covers streaming and SDK client copies.
            cid = str(request.headers.get(CONVERSATION_HEADER, "")).strip()
            if cid and cid in self.native_tools.bound and self._route(request.url):
                request.headers["x-bili-plugin"] = "astrbot"
                request.headers["x-bili-plugin-conversation"] = cid
            if (
                not self.plugin.closing
                and self._route(request.url)
                and request.method == "POST"
                and not str(request.headers.get(CONVERSATION_HEADER, "")).strip()
            ):
                request.headers[PASSTHROUGH_HEADER] = "1"
            return await original(request, *args, **kwargs)

        self._replace(client, "send", send)
        self._clients[id(client)] = client

    def _resolver(self, manager, name):
        original = getattr(manager, name, None)
        if not callable(original):
            return
        if inspect.iscoroutinefunction(original):

            @wraps(original)
            async def resolve(*args, **kwargs):
                provider = await original(*args, **kwargs)
                if not self.plugin.closing:
                    self.attach(provider)
                return provider
        else:

            @wraps(original)
            def resolve(*args, **kwargs):
                provider = original(*args, **kwargs)
                if not self.plugin.closing:
                    self.attach(provider)
                return provider

        self._replace(manager, name, resolve)

    def install(self):
        manager = getattr(self.plugin.context, "provider_manager", None)
        if self.plugin.closing or manager is None:
            return
        if manager is not self._manager:
            self.disconnect()
            self._manager = manager
            for name in ("get_provider_by_id", "get_using_provider_async", "get_using_provider"):
                self._resolver(manager, name)
            self._resolver(self.plugin.context, "get_provider_by_id")
        # Provider reloads close old SDK transports. Do not retain their clients
        # (or chains of bound methods) for the lifetime of the plugin.
        for key, client in list(self._clients.items()):
            if not getattr(client, "is_closed", False):
                continue
            self._clients.pop(key)
            retained = []
            for hook in self._hooks:
                owner, name, original, wrapper, local = hook
                if owner is not client:
                    retained.append(hook)
                elif getattr(owner, name, None) is wrapper:
                    if local:
                        setattr(owner, name, original)
                    else:
                        delattr(owner, name)
            self._hooks = retained
        for key, provider in list(self._providers.items()):
            client = getattr(getattr(provider, "client", None), "_client", None)
            if not getattr(client, "is_closed", False):
                continue
            self._providers.pop(key)
            retained = []
            for hook in self._hooks:
                owner, name, original, wrapper, local = hook
                if owner is not provider:
                    retained.append(hook)
                elif getattr(owner, name, None) is wrapper:
                    if local:
                        setattr(owner, name, original)
                    else:
                        delattr(owner, name)
            self._hooks = retained
        for provider in getattr(manager, "provider_insts", []) or []:
            self.attach(provider)

    def status(self):
        return {
            "auxiliary_passthrough": any(not c.is_closed for c in self._clients.values()),
            "adapted_clients": sum(not c.is_closed for c in self._clients.values()),
            "native_context_tools": len(self.native_tools.schemas),
            "native_tool_sessions": len(self.native_tools.bound),
        }
