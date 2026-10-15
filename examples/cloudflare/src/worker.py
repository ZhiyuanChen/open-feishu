"""Cloudflare Worker and Durable Object entrypoints for a serverless OpenFeishu Agent."""

from __future__ import annotations

import logging
from uuid import uuid4

from edge import inspect_url_verification_request
from workers import DurableObject, Response, WorkerEntrypoint

logger = logging.getLogger("feishu.cloudflare")


class _PersistentASGI:
    """Suppress per-request ASGI lifespan so one Durable Object keeps one Agent runtime."""

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "lifespan":
            return
        await self._app(scope, receive, send)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        verification_token = getattr(self.env, "FEISHU_VERIFICATION_TOKEN", None) or None
        verification = await inspect_url_verification_request(request, verification_token)
        if verification is not None:
            status, body = verification
            return Response.from_json(body, status=status)

        namespace = self.env.AGENT
        stub = namespace.get(namespace.idFromName("open-feishu-agent"))
        return await stub.fetch(request)


class FeishuAgent(DurableObject):
    """One Agent instance and its durable inbox per configured bot deployment."""

    def __init__(self, ctx, env) -> None:
        super().__init__(ctx, env)
        from agent_app import create_runtime_agent, is_local_smoke
        from storage import CloudflareEventJobStore, CloudflareSeenStore, CloudflareSqliteConnection

        self.ctx = ctx
        self.env = env
        self.connection = CloudflareSqliteConnection(ctx.storage)
        self.jobs = CloudflareEventJobStore(self.connection, ctx.storage)
        self.seen_store = CloudflareSeenStore(self.connection)
        self.agent = create_runtime_agent(
            env,
            connection=self.connection,
            jobs=self.jobs,
            seen_store=self.seen_store,
        )
        app = self.agent.asgi_app()
        self._local_smoke = is_local_smoke(env)
        if self._local_smoke:
            from starlette.routing import Route

            app.routes.append(Route("/__smoke/jobs/{job_id}", self._smoke_job, methods=["GET"]))
        self.app = _PersistentASGI(app)
        self._started = False

    async def fetch(self, request):
        await self._ensure_started()
        from workers import asgi

        if (request.headers.get("upgrade") or "").lower() == "websocket":
            return await asgi.websocket(self.app, request, self.env)
        return await asgi.fetch(self.app, request, self.env, self.ctx)

    async def alarm(self, _alarm_info=None) -> None:
        await self._ensure_started()
        from feishu.events.job_runner import EventJobRunner

        runner = EventJobRunner(
            self.jobs,
            self.agent.handle_event,
            worker_id=uuid4().hex,
            lease_seconds=120,
        )
        try:
            await runner.drain(limit=8)
        finally:
            await self.jobs.schedule_next()

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.agent.start()
            self._started = True

    async def _smoke_job(self, request):
        from starlette.responses import JSONResponse

        if not self._local_smoke:
            return JSONResponse({"msg": "not found"}, status_code=404)
        job = await self.jobs.get(request.path_params["job_id"])
        if job is None:
            return JSONResponse({"msg": "not found"}, status_code=404)
        return JSONResponse(
            {
                "event_id": job.event_id,
                "status": job.status.value,
                "attempt": job.attempt,
                "side_effect_started": job.side_effect_started,
                "error_type": job.error_type,
            }
        )
