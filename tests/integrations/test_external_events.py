from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette

from feishu.events.jobs import EventJobStatus, SqliteEventJobStore
from feishu.gateway.config import GatewayConfig


@pytest.fixture
def event_payload():
    return {
        "event_id": "transition-1",
        "event_type": "job.failed",
        "data": {
            "event_id": "job-42",
            "revision": 1,
            "title": "job failed",
            "target": {"receive_id": "oc_allowed", "receive_id_type": "chat_id"},
        },
    }


async def test_stale_revision_ignored(tmp_path, gateway_client, event_payload):
    from feishu.gateway.notifications import SqliteEventMessageStore
    from feishu.integrations.external_events import ExternalEvent, NotificationEventHandler

    path = tmp_path / "messages.db"
    allowed = {"tracker": {("chat_id", "oc_allowed")}}
    store = SqliteEventMessageStore(path)
    handler = NotificationEventHandler(gateway_client, store, allowed_targets=allowed)
    newer = ExternalEvent("tracker", "transition-2", "job.failed", {**event_payload["data"], "revision": 2})
    await handler(newer)
    store.close()
    reopened = SqliteEventMessageStore(path)
    handler = NotificationEventHandler(gateway_client, reopened, allowed_targets=allowed)
    stale = ExternalEvent("tracker", "transition-1", "job.running", event_payload["data"])
    await handler(stale)
    assert len(gateway_client.im.send.calls) == 1
    assert not gateway_client.im.patch.calls
    with pytest.raises(ValueError):
        await handler(ExternalEvent("tracker", "transition-3", "job.running", {**stale.data, "revision": 2}))
    assert not gateway_client.im.patch.calls
    reopened.close()


async def test_persistence_and_dedup(tmp_path, event_payload):
    from feishu.integrations.external_events import create_external_event_route

    db = tmp_path / "events.db"
    store = SqliteEventJobStore(db)
    config = GatewayConfig(app_id="app", app_secret="secret", service_keys={"k": "tracker"})
    app = Starlette(routes=[create_external_event_route(config, store)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/events/external", json=event_payload)).status_code == 401
        first = await client.post("/events/external", headers={"Authorization": "Bearer k"}, json=event_payload)
        duplicate = await client.post("/events/external", headers={"Authorization": "Bearer k"}, json=event_payload)
        changed = {**event_payload, "data": {**event_payload["data"], "title": "different"}}
        conflict = await client.post("/events/external", headers={"Authorization": "Bearer k"}, json=changed)
    assert first.status_code == 202
    assert first.json()["accepted"] is True
    assert duplicate.status_code == 202 and duplicate.json()["accepted"] is False
    assert conflict.status_code == 409
    reopened = SqliteEventJobStore(db)
    job = await reopened.get(first.json()["job_id"])
    assert job and job.status == EventJobStatus.QUEUED
    assert job.payload["source"] == "tracker"
    assert job.payload["data"] == event_payload["data"]


async def test_auth_and_validation(tmp_path, event_payload):
    from feishu.integrations.external_events import create_external_event_route

    store = SqliteEventJobStore(tmp_path / "events.db")
    config = GatewayConfig(
        app_id="app",
        app_secret="secret",
        service_keys={"k": "tracker"},
        service_capabilities={"tracker": frozenset({"/other"})},
    )
    app = Starlette(routes=[create_external_event_route(config, store)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/events/external", headers={"Authorization": "Bearer k"}, json=event_payload)
    assert response.status_code == 403
    config = GatewayConfig(app_id="app", app_secret="secret", service_keys={"k": "tracker"})
    app = Starlette(routes=[create_external_event_route(config, store, max_body_bytes=300)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/events/external", headers={"Authorization": "Bearer k"}, json={"data": {}})
        oversized = await client.post("/events/external", headers={"Authorization": "Bearer k"}, content=b"x" * 301)
    assert missing.status_code == 400
    assert oversized.status_code == 413


async def test_retry_and_unknown(tmp_path, event_payload):
    from feishu.events.job_runner import EventJobRunner
    from feishu.events.jobs import mark_current_event_side_effect_started
    from feishu.integrations.external_events import ExternalEvent, create_external_event_route

    store = SqliteEventJobStore(tmp_path / "events.db")
    config = GatewayConfig(app_id="app", app_secret="secret", service_keys={"k": "tracker"})
    app = Starlette(routes=[create_external_event_route(config, store)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/events/external", headers={"Authorization": "Bearer k"}, json=event_payload)
    received = []

    async def fail_once(event):
        received.append(event)
        raise RuntimeError("safe before effect")

    runner = EventJobRunner(
        store, fail_once, worker_id="worker", payload_decoder=ExternalEvent.from_payload, backoff_base_seconds=0
    )
    result = await runner.run_once()
    assert result and result.status == EventJobStatus.RETRY_WAIT
    assert received[0].source == "tracker" and received[0].event_type == "job.failed"

    async def uncertain(event):
        await mark_current_event_side_effect_started()
        raise ConnectionError("ambiguous result")

    runner = EventJobRunner(store, uncertain, worker_id="worker", payload_decoder=ExternalEvent.from_payload)
    result = await runner.run_once()
    assert result and result.status == EventJobStatus.UNKNOWN
    assert result.job_id == response.json()["job_id"]
    assert await runner.run_once() is None


async def test_destination_authorization_and_isolation(tmp_path, gateway_client, event_payload):
    from feishu.events.job_runner import EventJobRunner
    from feishu.gateway.notifications import InMemoryEventMessageStore
    from feishu.integrations.external_events import ExternalEvent, NotificationEventHandler, create_external_event_route

    store = SqliteEventJobStore(tmp_path / "events.db")
    config = GatewayConfig(app_id="app", app_secret="secret", service_keys={"k": "tracker"})
    notification_store = InMemoryEventMessageStore()
    handler = NotificationEventHandler(
        gateway_client,
        notification_store,
        allowed_targets={"tracker": {("chat_id", "oc_allowed"), ("chat_id", "oc_other")}},
    )
    app = Starlette(routes=[create_external_event_route(config, store, authorize=handler.authorize)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": "Bearer k"}
        a = await client.post("/events/external", headers=headers, json=event_payload)
        b_payload = {
            **event_payload,
            "event_id": "transition-2",
            "data": {**event_payload["data"], "target": {"receive_id": "oc_other"}},
        }
        b = await client.post("/events/external", headers=headers, json=b_payload)
        denied = {
            **event_payload,
            "event_id": "transition-3",
            "data": {**event_payload["data"], "target": {"receive_id": "oc_denied"}},
        }
        c = await client.post("/events/external", headers=headers, json=denied)
    assert a.status_code == b.status_code == 202
    assert c.status_code == 403
    runner = EventJobRunner(store, handler, worker_id="worker", payload_decoder=ExternalEvent.from_payload)
    assert (await runner.run_once()).status == EventJobStatus.COMPLETED
    assert (await runner.run_once()).status == EventJobStatus.COMPLETED
    assert len(gateway_client.im.send.calls) == 2
    assert not gateway_client.im.patch.calls


async def test_gateway_integration(tmp_path, event_payload, gateway_client):
    from feishu.gateway import create_gateway
    from feishu.integrations import ExternalEventIntegration

    store = SqliteEventJobStore(tmp_path / "events.db")
    config = GatewayConfig(app_id="app", app_secret="secret", service_keys={"k": "tracker"})
    app = create_gateway(config, client=gateway_client, integrations=(ExternalEventIntegration(store),))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/events/external", headers={"Authorization": "Bearer k"}, json=event_payload)
    assert response.status_code == 202
    assert await store.get(response.json()["job_id"]) is not None


async def test_missing_message_id(tmp_path, event_payload, gateway_client):
    from feishu.events.job_runner import EventJobRunner
    from feishu.gateway.notifications import SqliteEventMessageStore
    from feishu.integrations.external_events import ExternalEvent, NotificationEventHandler, create_external_event_route

    gateway_client.im.send.result = {}
    store = SqliteEventJobStore(tmp_path / "events.db")
    config = GatewayConfig(app_id="app", app_secret="secret", service_keys={"k": "tracker"})
    handler = NotificationEventHandler(
        gateway_client,
        SqliteEventMessageStore(tmp_path / "messages.db"),
        allowed_targets={"tracker": {("chat_id", "oc_allowed")}},
    )
    app = Starlette(routes=[create_external_event_route(config, store, authorize=handler.authorize)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/events/external", headers={"Authorization": "Bearer k"}, json=event_payload)
    assert response.status_code == 202
    runner = EventJobRunner(store, handler, worker_id="worker", payload_decoder=ExternalEvent.from_payload)
    assert (await runner.run_once()).status == EventJobStatus.UNKNOWN


def test_worker_delivery(tmp_path, event_payload, gateway_client):
    import asyncio
    import threading

    from starlette.testclient import TestClient

    from feishu.gateway import create_gateway
    from feishu.integrations.external_events import ExternalEventIntegration

    delivered = threading.Event()
    store = SqliteEventJobStore(tmp_path / "events.db")
    config = GatewayConfig(app_id="app", app_secret="secret", service_keys={"k": "tracker"})

    async def handler(event):
        assert event.event_id == "transition-1"
        delivered.set()

    integration = ExternalEventIntegration(store, handler=handler, worker_id="worker", poll_seconds=0.01)
    with TestClient(create_gateway(config, client=gateway_client, integrations=[integration])) as client:
        response = client.post("/events/external", headers={"Authorization": "Bearer k"}, json=event_payload)
        assert response.status_code == 202
        assert delivered.wait(2)
    job = asyncio.run(store.get(response.json()["job_id"]))
    assert job.status == EventJobStatus.COMPLETED


@pytest.mark.parametrize("side_effect,status", [(False, EventJobStatus.RETRY_WAIT), (True, EventJobStatus.UNKNOWN)])
def test_worker_shutdown(tmp_path, event_payload, gateway_client, side_effect, status):
    import asyncio
    import threading

    from starlette.testclient import TestClient

    from feishu.events.jobs import mark_current_event_side_effect_started
    from feishu.gateway import create_gateway
    from feishu.integrations.external_events import ExternalEventIntegration

    started = threading.Event()
    store = SqliteEventJobStore(tmp_path / "events.db")
    config = GatewayConfig(app_id="app", app_secret="secret", service_keys={"k": "tracker"})

    async def handler(event):
        if side_effect:
            await mark_current_event_side_effect_started()
        started.set()
        await asyncio.Event().wait()

    integration = ExternalEventIntegration(
        store, handler=handler, worker_id="worker", poll_seconds=0.01, shutdown_grace_seconds=0.01
    )
    with TestClient(create_gateway(config, client=gateway_client, integrations=[integration])) as client:
        response = client.post("/events/external", headers={"Authorization": "Bearer k"}, json=event_payload)
        assert started.wait(2)
    job = asyncio.run(store.get(response.json()["job_id"]))
    assert job.status == status
