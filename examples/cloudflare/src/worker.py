"""Cloudflare Worker and Durable Object entrypoints for a serverless OpenFeishu Agent."""

from __future__ import annotations

import logging
from uuid import uuid4

from agent_app import create_runtime_agent, is_local_smoke
from edge import inspect_url_verification_request
from starlette.responses import JSONResponse
from starlette.routing import Route
from storage import CloudflareEventJobStore, CloudflareSeenStore, CloudflareSqliteConnection
from timers import sleep as cloudflare_sleep
from workers import DurableObject, Response, WorkerEntrypoint, asgi

from feishu.events.job_runner import EventJobRunner

logger = logging.getLogger("feishu.cloudflare")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


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
        encrypt_key = getattr(self.env, "FEISHU_ENCRYPT_KEY", None) or None
        verification = await inspect_url_verification_request(request, verification_token, encrypt_key)
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
            app.routes.append(Route("/__smoke/jobs/{job_id}", self._smoke_job, methods=["GET"]))
        self.app = _PersistentASGI(app)
        self._started = False

    async def fetch(self, request):
        await self._ensure_started()
        if (request.headers.get("upgrade") or "").lower() == "websocket":
            return await asgi.websocket(self.app, request, self.env)
        return await asgi.fetch(self.app, request, self.env, self.ctx)

    async def alarm(self, _alarm_info=None) -> None:
        await self._ensure_started()
        runner = EventJobRunner(
            self.jobs,
            self.agent.handle_event,
            worker_id=uuid4().hex,
            lease_seconds=120,
            sleep=cloudflare_sleep,
        )
        try:
            for job in await runner.drain(limit=8):
                logger.info(
                    "event job settled: id=%s status=%s attempt=%s error_type=%s",
                    job.job_id,
                    job.status.value,
                    job.attempt,
                    job.error_type,
                )
        finally:
            await self.jobs.schedule_next()

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.agent.start()
            self._started = True

    async def _smoke_job(self, request):
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
