# Cloudflare Workers

This example runs one OpenFeishu Agent on a Python Worker and a SQLite-backed Durable Object. The Durable Object owns the Agent runtime and persistent state: sessions, approvals, authorizations, execution results, OAuth tokens, shared files, optional memory, webhook deduplication, and the event inbox.

Normal Feishu events are validated and written to the durable inbox before the webhook returns success. A Durable Object alarm processes up to eight queued events per run. Card-action callbacks remain synchronous because Feishu expects the callback result in the HTTP response.

The initial topology is one Durable Object per bot deployment. Alarm processing is sequential, so this is intended for a single bot with modest traffic. Sharding would require a routing plan for session, OAuth, and approval state; this example does not pretend one Durable Object scales horizontally.

## Local run

Install `uv` and Node.js 22 or newer, then run:

```bash
cp .dev.vars.example .dev.vars
uv sync
uv run pywrangler dev
```

The example `.dev.vars` enables a local smoke backend that makes no model or Feishu API calls. Check readiness and Feishu URL verification:

```bash
curl -i http://localhost:8787/ready
curl -i -X POST http://localhost:8787/feishu/event \
  -H 'content-type: application/json' \
  -d '{"type":"url_verification","challenge":"local-probe","token":"cf-smoke"}'
```

The challenge response should echo `local-probe`. To exercise alarm-driven event processing and Durable Object persistence, run the Python tests from the repository root:

```bash
pytest -q --no-cov tests/cloudflare
```

## Deploy

Set the Worker secrets before deploying. `OPENAI_MODEL` selects the model; `OPENAI_BASE_URL` is optional and defaults to `https://api.openai.com/v1`. `AGENT_PUBLIC_URL` and `AGENT_OAUTH_STATE_SECRET` are needed when enabling user OAuth. Use a dedicated, stable OAuth state secret so authorization links remain valid across Durable Object restarts.

```bash
uv run pywrangler secret put FEISHU_APP_ID
uv run pywrangler secret put FEISHU_APP_SECRET
uv run pywrangler secret put FEISHU_ENCRYPT_KEY
uv run pywrangler secret put FEISHU_VERIFICATION_TOKEN
uv run pywrangler secret put OPENAI_API_KEY
uv run pywrangler secret put OPENAI_MODEL
```

If using a compatible provider other than OpenAI, set `OPENAI_BASE_URL` as well.

When using user OAuth, also set:

```bash
uv run pywrangler secret put AGENT_PUBLIC_URL
uv run pywrangler secret put AGENT_OAUTH_STATE_SECRET
```

Then deploy:

```bash
uv run pywrangler deploy
```

Wrangler creates the SQLite-backed Durable Object namespace using the migration in `wrangler.jsonc`. Set the Feishu event subscription URL to `https://<worker-host>/feishu/event`; configure the same Encrypt Key and Verification Token in the Feishu app. The OAuth callback URL is `https://<worker-host>/oauth/callback`.

Check the final bundle before deployment with `uv run pywrangler deploy --dry-run --outdir /tmp/open-feishu-cloudflare-bundle`. Wrangler reports the uncompressed upload size; Cloudflare's current [Worker size limit is 64 MiB](https://developers.cloudflare.com/changelog/post/2026-09-04-increased-worker-size-limit/). Worker source lives in `src/` so host and Pyodide build environments are not collected as Worker modules.

## Delivery and recovery behavior

- Failures before an external side effect use bounded exponential retry. If a side effect may have happened before the outcome was recorded, the event enters `unknown` and is not replayed automatically.
- An operator must reconcile the external side effect before calling `CloudflareEventJobStore.requeue_unknown()`. The example deliberately does not expose a public job-administration endpoint.
- A `failed` or `unknown` job blocks later work for the same conversation partition until it is reconciled or requeued; other partitions can continue.
- Completed jobs are retained for seven days; `unknown` jobs are retained for thirty days. The SDK's default seen-event store also keeps a seven-day deduplication window.
- Card callbacks are synchronous and do not pass through the durable inbox.
- Local smoke mode verifies application wiring without exercising real OpenAI or Feishu network calls. Validate those integrations in a Cloudflare test deployment before relying on them in production.

Python Workers run on Pyodide and support pure Python and Pyodide-compatible packages. Check the [Cloudflare Python package guidance](https://developers.cloudflare.com/workers/languages/python/packages/) for the current compatibility status. See also Cloudflare's docs for [Python Workers](https://developers.cloudflare.com/workers/languages/python/), [SQLite-backed Durable Objects](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/), and [Durable Object alarms](https://developers.cloudflare.com/durable-objects/api/alarms/).
