from __future__ import annotations

import json
import sqlite3
from importlib import import_module

import httpx

from feishu.agent import Agent
from feishu.events.envelope import Event
from feishu.events.jobs import InMemoryEventJobStore

agent_app = import_module("examples.cloudflare.src.agent_app")


async def test_runtime_streams_through_http_without_importing_openai_sdk(monkeypatch):
    writes = []

    def respond(request):
        if request.url.path.endswith("/chat/completions"):
            payload = json.loads(request.content)
            assert payload["model"] == "test-model"
            assert payload["stream"] is True
            chunks = [
                {"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]},
            ]
            sse = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"})
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "test", "expire": 7200})
        if request.method == "PUT":
            writes.append(json.loads(request.content)["content"])
        return httpx.Response(200, json={"code": 0, "data": {"card_id": "card_test", "message_id": "om_reply"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    def forbidden_sdk(_self):
        raise AssertionError("the Worker must not construct the full OpenAI SDK")

    monkeypatch.setattr(Agent, "_openai_client_from_config", forbidden_sdk)
    connection = sqlite3.connect(":memory:")
    agent = None
    try:
        agent = agent_app.create_runtime_agent(
            {
                "FEISHU_APP_ID": "cli_test",
                "FEISHU_APP_SECRET": "test",
                "OPENAI_API_KEY": "test",
                "OPENAI_MODEL": "test-model",
            },
            connection=connection,
            jobs=InMemoryEventJobStore(),
            seen_store=None,
        )
        await agent.start()
        await agent.handle_event(
            Event.from_payload(
                {
                    "schema": "2.0",
                    "header": {"event_type": "im.message.receive_v1", "event_id": "http-model"},
                    "event": {
                        "message": {
                            "chat_id": "oc_test",
                            "message_id": "om_in",
                            "message_type": "text",
                            "content": '{"text":"hello"}',
                        }
                    },
                }
            )
        )
        assert writes[0] == "Hello"
        assert writes[-1] == "Hello world"
        assert agent.describe_analyzer is not None
    finally:
        if agent is not None:
            await agent.aclose()
            await agent.client.aclose()
            await agent.model_client.aclose()
        await client.aclose()
        connection.close()
