# Native Runtime Snapshot Round Loop Evidence - 2026-07-05

## Branch / Worktree

- Branch: `codex/native-runtime-snapshot-round-loop`
- Worktree: `/Users/pingxia/projects/deer-flow`
- Reference directory: `/Users/pingxia/Downloads/deerflow-ai-operating-plane` (read-only; not copied as a second runtime)

## Commits

- Final commit hash is reported in the Codex handoff after validation. This file is committed with the branch evidence, so embedding its own final hash here would make the hash stale.

## What Changed

- Backend runtime snapshot now returns one owner-scoped recovery envelope with runs, latest per-run message pages, native rounds, task lanes, display metadata, and terminal reasons.
- Runtime snapshot recovery converges stale native state when a terminal run is readable but `rounds.state` or task lanes are still active, so reload cannot recreate fake running state.
- SSE durable replay and live `END_SENTINEL` handling surface `run.terminal` before `end`, including a fallback synthesized from the terminal run row when the custom terminal event is missing.
- Run listing is deterministic newest-first across memory/store rows, and store-only terminal rows preserve `terminal_reason`.
- Frontend thread history hydrates from runtime snapshot, hides control rows from chat, restores terminal task lanes, shows terminal notices when no final AI reply exists, and clears stale local streaming ownership after recovery settles.
- Real-backend e2e uses the replay Gateway and environment-configured frontend port; visual snapshot comparison is opt-in with `PLAYWRIGHT_REAL_BACKEND_VISUAL=1`.

## New / Updated Coverage

- Backend runtime snapshot covers run/message/round/task-lane recovery and stale terminal run with open round plus active task lane.
- Backend SSE coverage includes durable and live `run.terminal` fallback before `end`.
- Backend run manager/store/repository tests cover deterministic run order, store-only recovery statuses, terminal reasons, cancel intent, and startup/read-side recovery.
- Frontend unit coverage covers snapshot hydration, stream recovery ownership, terminal status mapping, task lane restoration, and terminal notices.
- Real-backend Playwright coverage checks auth-disabled contract, multi-run ordering, hidden internal rows, terminal no-reply notice, task-lane subtask recovery, and replayed model render.

## Commands Run

- `cd /Users/pingxia/projects/deer-flow/backend && PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/test_thread_run_messages_pagination.py tests/test_run_manager.py tests/test_run_repository.py tests/test_runs_api_endpoints.py tests/test_sse_format.py tests/test_threads_router.py -q`
  - Result: `231 passed, 2 warnings`
- `cd /Users/pingxia/projects/deer-flow/backend && PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/test_run_worker_terminal_event.py tests/test_run_worker_rollback.py -q`
  - Result: `38 passed, 1 warning`
- `cd /Users/pingxia/projects/deer-flow/backend && uv run ruff check app/gateway/routers/thread_runs.py app/gateway/routers/threads.py app/gateway/services.py packages/harness/deerflow/persistence/run/sql.py packages/harness/deerflow/runtime/runs/manager.py packages/harness/deerflow/runtime/runs/store/memory.py tests/seed_runs_router.py tests/test_persistence_scaffold.py tests/test_run_manager.py tests/test_run_repository.py tests/test_runs_api_endpoints.py tests/test_sse_format.py tests/test_thread_run_messages_pagination.py tests/test_threads_router.py`
  - Result: `All checks passed!`
- `cd /Users/pingxia/projects/deer-flow/backend && uv run ruff format --check app/gateway/routers/thread_runs.py app/gateway/routers/threads.py app/gateway/services.py packages/harness/deerflow/persistence/run/sql.py packages/harness/deerflow/runtime/runs/manager.py packages/harness/deerflow/runtime/runs/store/memory.py tests/seed_runs_router.py tests/test_persistence_scaffold.py tests/test_run_manager.py tests/test_run_repository.py tests/test_runs_api_endpoints.py tests/test_sse_format.py tests/test_thread_run_messages_pagination.py tests/test_threads_router.py`
  - Result: `14 files already formatted`
- `cd /Users/pingxia/projects/deer-flow/frontend && pnpm test tests/unit/core/threads/message-merge.test.ts tests/unit/core/threads/hooks.test.ts tests/unit/core/threads/infinite.test.ts tests/unit/core/api/api-client.test.ts`
  - Result: `160 passed`
- `cd /Users/pingxia/projects/deer-flow/frontend && pnpm check`
  - Result: passed (`eslint` and `tsc --noEmit`)
- `cd /Users/pingxia/projects/deer-flow/frontend && pnpm exec playwright test -c playwright.real-backend.config.ts`
  - Result: `6 passed`
- `cd /Users/pingxia/projects/deer-flow && git diff --check`
  - Result: passed

## Warnings Observed

- Existing dependency warnings from `langgraph.checkpoint.serde.encrypted` and `fastapi.testclient`.
- Real-backend replay run logs auth-disabled/test-only warnings by design.
- Turbopack emitted an existing NFT trace warning for `next.config.js` through a mock artifact route.
- Test JWT secret length warnings appeared only in auth-disabled replay test mode.

## Remaining Risks

- This closes the P0 recovery loop for deterministic replay and targeted backend/frontend coverage, but it does not add P1 capability/provenance/role-governance behavior.
- Real-backend e2e uses the local replay Gateway, not production services.
- Runtime snapshot read-side convergence is intentionally minimal; normal worker/finalizer terminal writes remain the primary path.

## Manual Verification

1. Start the replay Gateway and frontend through `pnpm exec playwright test -c playwright.real-backend.config.ts`.
2. Open a seeded multi-run thread and confirm older answers render above newer answers.
3. Confirm middleware/tool/subagent control rows do not become chat bubbles.
4. Seed or reproduce a terminal run without a final AI reply and confirm the UI shows the terminal reason instead of fake streaming.
5. Seed or reproduce a terminal run with an active task lane and confirm reload restores a terminal subtask card, not a running card.
