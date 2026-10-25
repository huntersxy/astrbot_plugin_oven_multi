"""Exercise real HTTP sharing, cancellation, overload and exact-cache boundaries."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web

root = Path(__file__).resolve().parents[1]
source = root / "jev_transport.py"
if not source.exists():
    source = root / "features/jev_transport.py"
spec = importlib.util.spec_from_file_location("tested_jev_transport", source)
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)
pytestmark = pytest.mark.asyncio
QUESTION = {"reply": {"type": "noul", "instructions": "Should the bot reply?"}}


@pytest_asyncio.fixture
async def service():
    control = {
        "calls": [],
        "entered": asyncio.Event(),
        "release": asyncio.Event(),
        "status": 200,
    }
    control["release"].set()

    async def handle(request):
        payload = await request.json()
        control["calls"].append(payload)
        control["entered"].set()
        await control["release"].wait()
        return web.json_response(
            control.get(
                "body",
                {
                    "model": "jev-test",
                    "answers": {"reply": {"type": "noul", "noul": 0.8}},
                    "usage": {"input_tokens": 8, "output_tokens": 2},
                },
            ),
            status=control["status"],
            headers=control.get("headers", {}),
        )

    app = web.Application()
    app.router.add_post("/v1/systemone", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    cfg = {
        "base_url": f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}",
        "api_key": "synthetic-secret",
        "model": "jev-test",
    }
    client = transport.SystemOne()
    try:
        yield client, cfg, control
    finally:
        control["release"].set()
        await client.close()
        await runner.cleanup()


async def test_concurrent_identical_requests_pay_once_and_cache_is_detached(service):
    client, cfg, control = service
    control["release"].clear()
    tasks = [
        asyncio.create_task(client.evaluate(cfg, "private-message", QUESTION))
        for _ in range(12)
    ]
    await asyncio.wait_for(control["entered"].wait(), 2)
    control["release"].set()
    results = await asyncio.gather(*tasks)
    assert len(control["calls"]) == 1
    assert client.metrics["coalesced"] == 11
    results[0]["answers"]["reply"]["noul"] = 0.01
    cached = await client.evaluate(cfg, "private-message", QUESTION)
    assert cached["answers"]["reply"]["noul"] == 0.8 and cached["cache_hit"]
    assert client.metrics["input_tokens"] == 8 and client.metrics["output_tokens"] == 2
    assert "private-message" not in json.dumps(client.status())
    assert "synthetic-secret" not in json.dumps(client.status())


async def test_cancelled_waiter_does_not_cancel_current_peer(service):
    client, cfg, control = service
    control["release"].clear()
    first = asyncio.create_task(client.evaluate(cfg, "same", QUESTION))
    await asyncio.wait_for(control["entered"].wait(), 2)
    second = asyncio.create_task(client.evaluate(cfg, "same", QUESTION))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    control["release"].set()
    assert (await second)["status"] == "ready"
    assert len(control["calls"]) == 1 and client.metrics["cancelled"] == 0


async def test_last_waiter_cancellation_aborts_network_without_failure_penalty(service):
    client, cfg, control = service
    control["release"].clear()
    task = asyncio.create_task(client.evaluate(cfg, "obsolete", QUESTION))
    await asyncio.wait_for(control["entered"].wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.inflight and not client.failures
    assert client.metrics["cancelled"] == 1


async def test_retry_after_prevents_immediate_paid_retry(service):
    client, cfg, control = service
    control.update(status=429, headers={"Retry-After": "120"})
    assert (await client.evaluate(cfg, "first", QUESTION))["http_status"] == 429
    result = await client.evaluate(cfg, "new input", QUESTION)
    assert result["status"] == "backoff" and result["retry_after"] >= 119
    assert len(control["calls"]) == 1


async def test_invalid_measurements_are_never_success_cached(service):
    client, cfg, control = service
    control["body"] = {"answers": {"reply": {"type": "noul", "noul": True}}}
    assert (await client.evaluate(cfg, "same", QUESTION))["status"] == "invalid"
    assert (await client.evaluate(cfg, "same", QUESTION))["status"] == "invalid"
    assert not client.cache and len(control["calls"]) == 2
    assert (await client.evaluate(cfg, "same", QUESTION))["status"] == "backoff"


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "choice", "choice": [], "confidence": 0.9},
        {"type": "choice", "choice": "help", "confidence": True},
        {"type": "choice", "choice": "help"},
        {"type": "noul", "noul": 0.9},
    ],
)
async def test_malformed_typed_choice_is_invalid_not_an_exception(service, answer):
    client, cfg, control = service
    control["body"] = {"answers": {"reply": answer}}
    result = await client.evaluate(
        cfg, "input", {"reply": {"type": "choice", "criteria": {"help": "A request"}}}
    )
    assert result["status"] == "invalid" and not client.cache


@pytest.mark.parametrize("status", [401, 403, 422])
async def test_configuration_failure_backs_off_but_a_new_key_can_recover(
    service, status
):
    client, cfg, control = service
    control["status"] = status
    await client.evaluate(cfg, "input", QUESTION)
    assert (await client.evaluate(cfg, "another input", QUESTION))[
        "status"
    ] == "backoff"
    control["status"] = 200
    assert (
        await client.evaluate(
            cfg | {"api_key": "repaired-synthetic"}, "input", QUESTION
        )
    )["status"] == "ready"
    assert len(control["calls"]) == 2


async def test_changed_credential_or_model_cannot_reuse_old_judgment(service):
    client, cfg, control = service
    await client.evaluate(cfg, "same", QUESTION)
    await client.evaluate(cfg | {"api_key": "another-synthetic"}, "same", QUESTION)
    await client.evaluate(cfg | {"model": "other-model"}, "same", QUESTION)
    assert len(control["calls"]) == 3


async def test_concurrent_unique_calls_are_bounded_instead_of_queueing(service):
    client, cfg, control = service
    control["release"].clear()
    tasks = [
        asyncio.create_task(client.evaluate(cfg, str(index), QUESTION))
        for index in range(9)
    ]
    await asyncio.wait_for(control["entered"].wait(), 2)
    assert len(client.inflight) == 8
    control["release"].set()
    results = await asyncio.gather(*tasks)
    assert sum(r["status"] == "busy" for r in results) == 1
    assert len(control["calls"]) == 8


async def test_shutdown_cannot_reopen_a_client_or_leave_requests_running(service):
    client, cfg, control = service
    control["release"].clear()
    task = asyncio.create_task(client.evaluate(cfg, "pending", QUESTION))
    await asyncio.wait_for(control["entered"].wait(), 2)
    await client.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.inflight and client.session is None
    assert (await client.evaluate(cfg, "later", QUESTION))["status"] == "closed"
