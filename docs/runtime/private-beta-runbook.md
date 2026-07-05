# DeerFlow Private Beta Runbook

## Purpose

Run DeerFlow in private beta without changing core runtime unless real
regressions are observed.

## Startup Config

Use the standard single-Gateway-worker shape:

```bash
GATEWAY_WORKERS=1
WEB_CONCURRENCY=1
UVICORN_WORKERS=1
ENVIRONMENT=production
```

For shared/private beta deployments, `config.yaml` must use a persistent event
store:

```yaml
database:
  backend: sqlite # single-node private beta
  sqlite_dir: .deer-flow/data

run_events:
  backend: db
  max_trace_content: 10240
  track_token_usage: true
```

Current code still uses the legacy `checkpointer` section for LangGraph store
persistence. Without it, Gateway can boot with `database.backend: sqlite`, but
startup logs `InMemoryStore` for the store. Until the store follows the unified
`database` section, include:

```yaml
checkpointer:
  type: sqlite
  connection_string: .deer-flow/data/checkpoints.db
```

Use `postgres` instead of `sqlite` only when the database is already operated as
a managed service. Do not raise worker count until a shared runtime/stream
bridge/cancel signal exists.

## Known Limits

- Gateway run state and stream bridge are process-local; one worker only.
- JSONL run events are local/single-process only, not private-beta production.
- SSE reconnect can replay persisted terminal runs, but live in-flight stream
  ownership is still worker-local.
- LangGraph store persistence still depends on the legacy `checkpointer`
  section; `database` alone persists app tables/run events/checkpoints but does
  not stop the store fallback to memory in current code.
- Legacy ownerless rows need one-time migration/claim before judging old history
  visibility.
- Artifact provenance is indexed best-effort from observed run artifact events;
  missing files stay represented as unavailable artifacts.
- Provider stream failures such as
  `Codex API stream ended without response.completed event` previously blocked
  real Command Room usage. A retry fix is in place and post-fix run/API/browser
  replay checks have passed; keep observing through the 1-2 day window. If the
  failure recurs repeatedly, runs must still reach terminal `error` and must
  not leave the thread busy.

## Preflight

```bash
git status --short
cd backend && PYTHONPATH=. uv run pytest tests/test_gateway_worker_guard.py tests/test_deployment_security_guards.py -q
cd frontend && pnpm test tests/unit/core/threads/message-merge.test.ts
cd frontend && pnpm exec playwright test tests/e2e/thread-history.spec.ts
```

Before first beta boot on an existing data directory:

```bash
cd backend
PYTHONPATH=. uv run python scripts/migrate_user_isolation.py --dry-run --user-id <owner-user-id>
```

Run without `--dry-run` only after the report assigns legacy data to the
intended owner and confirms it inspected the live database. The script reports
the SQLite path it inspected; by default it checks `{base_dir}/data/deerflow.db`
with a legacy `{base_dir}/deer-flow.db` fallback. Use `--db-path <path>` if the
runtime database is elsewhere.

## 1-2 Day Smoke

Record one row per session in `docs/runtime/private-beta-smoke-log.md`. Do not
record duplicate heartbeat rows; add a row only for a new real-use session, a
new run/test result, a new failure/warning, or a meaningful checkpoint that
changes the evidence state.

Minimum daily flow:

1. Start stack from a clean shell.
2. Create two independent chat threads.
3. Send a normal prompt in each thread and switch between them during one run.
4. Refresh the browser after a terminal run and confirm visible history order.
5. Trigger an artifact-producing task and open the artifact list.
6. Disconnect/reload during a run; confirm terminal state or replay is visible.
7. Cancel one active run and confirm the thread does not stay busy.
8. Restart Gateway once after a terminal run and confirm the terminal run can
   replay from persisted events.

Stop and open a separate fix if any of these appears:

- cross-thread messages, tasks, artifacts, or busy state
- history missing after refresh
- run stuck busy after terminal/cancel
- repeated provider stream terminal errors, even if they do not leave the run
  stuck busy
- artifact path resolves to the wrong owner bucket
- startup accepts unsafe worker/event-store config in production
- raw secrets or customer-like data appear in logs or UI

Use `docs/runtime/private-beta-open-fix-handoffs.md` for the current separate
fix-line handoffs.

## Evidence To Keep

- commit hash
- startup command and config summary
- browser route/thread ids used
- run ids for cancel/reconnect/restart checks
- screenshot or log for any failure
- exact test command before and after any fix
