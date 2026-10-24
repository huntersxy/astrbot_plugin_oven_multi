"""Opt-in real bili + real AstrBot SDK + local fake upstream; no paid model calls.

Set BILI_TEST_ENTRY to an already installed bili dist/index.js. All proxy files,
sessions and logs are isolated in pytest's temporary directory.
"""

import asyncio
import json
import os
import shutil
import socket
from pathlib import Path

import httpx
import pytest
from aiohttp import web
from test_bili_requests import Manager, completion, provider

pytest_plugins = ["test_panel_retirement"]


@pytest.mark.asyncio
async def test_real_proxy_keeps_helpers_out_of_compression_sessions(plugin, tmp_path):
    entry = os.environ.get("BILI_TEST_ENTRY")
    if not entry or not Path(entry).is_file():
        pytest.skip("Set BILI_TEST_ENTRY to run the real proxy contract")
    upstream_requests = []
    compressed = False

    async def upstream(request):
        nonlocal compressed
        body = await request.json()
        upstream_requests.append((dict(request.headers), body))
        if body.get("stream"):
            output = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await output.prepare(request)
            for text, finish in [("STREAM_ANSWER", None), ("", "stop")]:
                chunk = {
                    "id": "stream-test",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test",
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}],
                }
                await output.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            await output.write(b"data: [DONE]\n\n")
            await output.write_eof()
            return output
        if "COMPRESS_TEST" in json.dumps(body) and not compressed:
            assert any(
                t.get("function", {}).get("name") == "compress" for t in body.get("tools", [])
            )
            compressed = True
            result = completion()
            result["choices"][0]["finish_reason"] = "tool_calls"
            result["choices"][0]["message"] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "compress-test",
                        "type": "function",
                        "function": {
                            "name": "compress",
                            "arguments": json.dumps(
                                {
                                    "topic": "older turns",
                                    "content": (
                                        "m00001–m00002 Earlier topic\n"
                                        "Earlier question and answer are complete; "
                                        "retain SUMMARY_MARKER_OLDER."
                                    ),
                                }
                            ),
                        },
                    }
                ],
            }
            return web.json_response(result)
        return web.json_response(completion('{"intent":"提问求助","action":"observe"}'))

    app = web.Application()
    app.router.add_post("/v1/chat/completions", upstream)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    upstream_port = site._server.sockets[0].getsockname()[1]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    proxy = f"http://127.0.0.1:{port}"
    config = tmp_path / "billion-context.json"
    config.write_text(
        json.dumps(
            {
                "sessionHeader": "x-astrbot-conversation-id",
                "autoUpdate": False,
                "log": False,
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("ACP_", "BILI_"))}
    environment.update(
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        ACP_AUTO_UPDATE="0",
    )
    log_path = tmp_path / "proxy.log"
    process = None
    prov = None
    try:
        with log_path.open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                shutil.which("node"),
                entry,
                "start",
                "--config",
                str(config),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-auto-update",
                stdout=output,
                stderr=output,
                env=environment,
            )
        async with httpx.AsyncClient(timeout=2) as health:
            for _ in range(80):
                if process.returncode is not None:
                    pytest.fail(log_path.read_text(encoding="utf-8", errors="replace")[-3000:])
                try:
                    if (await health.get(proxy + "/__bili/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                pytest.fail("bili did not start")
        plugin.config["bili"] = {"host": "127.0.0.1", "port": port, "mode": "external"}
        prov = provider(proxy + f"/bili/http://127.0.0.1:{upstream_port}/v1")
        plugin.context.provider_manager = Manager(prov)
        await plugin.initialize()
        for topic in ("PLANNER_JSON", "LEARNING_JSON", "RECALL_JSON"):
            result = await asyncio.wait_for(prov.text_chat(prompt=topic), 15)
            assert json.loads(result.completion_text)["action"] == "observe"
            headers, body = upstream_requests[-1]
            assert body["messages"] == [{"role": "user", "content": topic}]
            assert not body.get("tools") and "x-bili-passthrough" not in {
                k.lower() for k in headers
            }
        for conversation_id in ("room-a", "room-b"):
            result = await asyncio.wait_for(
                prov.text_chat(prompt="MAIN_" + conversation_id, conversation_id=conversation_id),
                15,
            )
            assert result.completion_text
            assert "MAIN_" + conversation_id in json.dumps(upstream_requests[-1][1])
        # Exercise bili's actual hidden-tool compression/refetch loop locally.
        # The fake model explicitly requests compression; no paid LLM is used.
        result = await asyncio.wait_for(
            prov.text_chat(
                prompt="COMPRESS_TEST current question",
                conversation_id="room-compress",
                contexts=[
                    {"role": "user", "content": "OLDER_QUESTION " * 4000},
                    {"role": "assistant", "content": "OLDER_ANSWER " * 4000},
                    {"role": "user", "content": "Older followup"},
                    {"role": "assistant", "content": "Older reply"},
                    {"role": "user", "content": "Another followup"},
                    {"role": "assistant", "content": "Another reply"},
                ],
            ),
            20,
        )
        assert compressed and result.completion_text and not result.tools_call_name
        assert "SUMMARY_MARKER_OLDER" in json.dumps(upstream_requests[-1][1]), (
            result.completion_text
        )
        # bili may retain a folded prefix for cache affinity until pruning is
        # worthwhile. Verify the summary and current target, not instant eviction.
        assert "COMPRESS_TEST current question" in json.dumps(upstream_requests[-1][1])
        assert result.usage.total == 44  # compression and continuation are both counted
        for cid in (None, "room-stream"):
            responses = [
                r
                async for r in prov.text_chat_stream(
                    prompt="STREAM_HELPER" if cid is None else "STREAM_MAIN", conversation_id=cid
                )
            ]
            assert responses[-1].completion_text == "STREAM_ANSWER"
        # Inspect the real proxy's durable session surface, not a stubbed bridge.
        async with httpx.AsyncClient(timeout=3) as admin:
            sessions = await admin.get(proxy + "/__bili/sessions")
            assert sessions.status_code == 200
            raw = sessions.text
            by_id = {s["id"]: s for s in sessions.json()["sessions"]}
            assert set(by_id) == {"room-a", "room-b", "room-compress", "room-stream"}
            assert by_id["room-compress"]["blocks"] >= 1, result.completion_text
            assert all(
                topic not in raw for topic in ("PLANNER_JSON", "LEARNING_JSON", "RECALL_JSON")
            )
            (tmp_path / "session-report.json").write_text(raw, encoding="utf-8")
            (tmp_path / "upstream-report.json").write_text(
                json.dumps(upstream_requests), encoding="utf-8"
            )
    finally:
        await plugin.terminate()
        if prov is not None:
            await prov.client.close()
        if process is not None and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 10)
        await runner.cleanup()
