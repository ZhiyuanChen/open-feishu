from __future__ import annotations

import asyncio

import httpx
import pytest
from starlette.applications import Starlette

from feishu.events.dispatcher import EventDispatcher
from feishu.events.idempotency import InMemorySeenStore
from feishu.events.jobs import InMemoryEventJobStore
from feishu.events.receiver import create_card_route, create_event_route


@pytest.mark.parametrize("factory", [create_card_route, create_event_route])
def test_durable_card_commit_failure_can_retry_same_callback(factory):
    async def scenario():
        seen = InMemorySeenStore()
        jobs = InMemoryEventJobStore()
        attempts = 0

        async def durable_handler(event):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("test persistence unavailable")
            await jobs.enqueue(event.event_id, dict(event.raw), partition_key="chat:thread")
            return {"toast": {"type": "info", "content": "accepted"}}

        dispatcher = EventDispatcher(seen_store=seen)
        route = factory(dispatcher, seen_store=seen, card_handler=durable_handler)
        body = {"schema": "2.0", "header": {"event_id": "card-1", "event_type": "card.action.trigger"}, "event": {}}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=Starlette(routes=[route])), base_url="https://test"
        ) as client:
            first = await client.post(route.path, json=body)
            assert first.status_code == 503
            assert not await seen.seen("card-1")
            second = await client.post(route.path, json=body)
            assert second.status_code == 200
            assert second.json()["toast"]["content"] == "accepted"
            duplicate = await client.post(route.path, json=body)
            assert duplicate.status_code == 200
        assert attempts == 3
        job = await jobs.claim_next("worker")
        assert job.event_id == "card-1"
        await jobs.complete(job.job_id, "worker")
        assert await jobs.claim_next("worker") is None
        assert not await seen.seen("card-1")

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", [create_card_route, create_event_route])
def test_durable_card_handler_remains_behind_authentication(factory):
    async def scenario():
        called = False

        async def handler(event):
            nonlocal called
            called = True
            return {}

        route = factory(EventDispatcher(), verification_token="test-token", card_handler=handler)
        body = {
            "schema": "2.0",
            "header": {"event_id": "card-1", "event_type": "card.action.trigger", "token": "wrong"},
            "event": {},
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=Starlette(routes=[route])), base_url="https://test"
        ) as client:
            response = await client.post(route.path, json=body)
        assert response.status_code == 401
        assert not called

    asyncio.run(scenario())
