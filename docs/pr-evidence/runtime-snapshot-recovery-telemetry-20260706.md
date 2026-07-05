# Runtime Snapshot Recovery Telemetry Evidence - 2026-07-06

## Branch / Scope

- Branch: `codex/runtime-snapshot-recovery-telemetry`
- Base: latest local `main` at `5288b0b10c498b6aa2123850bcc15efd87e0106b`
- Scope: additive runtime snapshot recovery telemetry only
- Safety: no secrets read, no production writes, no push

## What Changed

- `GET /api/threads/{thread_id}/runtime-snapshot` now returns optional `recovery` telemetry when snapshot recovery work happened.
- Stale inflight recovery records `recovered_count`, `run_ids`, common `terminal_reason`, and per-run terminal reasons.
- Terminal snapshot self-heal records whether round/task row repair happened.
- No UI text, toast, metrics system, capability/governance, or real-backend non-replay E2E changes were added.

## Response Shape

When no recovery occurs, `recovery` is `null`.

When recovery occurs:

```json
{
  "recovery": {
    "stale_inflight": {
      "recovered": true,
      "recovered_count": 1,
      "run_ids": ["stale-run"],
      "terminal_reason": "worker_lost",
      "runs": [
        { "run_id": "stale-run", "terminal_reason": "worker_lost" }
      ]
    },
    "snapshot_self_heal": {
      "repaired": true
    }
  }
}
```

Each child field is optional/additive for old clients.

## Commands Run

- `cd /Users/pingxia/projects/deer-flow/backend && uv run pytest tests/test_thread_run_messages_pagination.py tests/test_run_manager.py -q`
  - Result: `120 passed, 2 warnings`
- `cd /Users/pingxia/projects/deer-flow/frontend && pnpm test tests/unit/core/threads/message-merge.test.ts`
  - Result: `80 passed`
- `cd /Users/pingxia/projects/deer-flow/backend && uv run ruff check .`
  - Result: `All checks passed!`
- `cd /Users/pingxia/projects/deer-flow/backend && uv run ruff format --check .`
  - Result: `718 files already formatted`
- `cd /Users/pingxia/projects/deer-flow/frontend && pnpm check`
  - Result: passed (`eslint` and `tsc --noEmit`)
- `cd /Users/pingxia/projects/deer-flow && git diff --check`
  - Result: passed

## Remaining Risks

- Telemetry is response-local only; no durable metrics, dashboards, or alerting.
- It reports whether snapshot self-heal repaired anything, not separate round-row vs task-lane counts.
- It does not add capability, provenance, role-governance, or real production E2E coverage.
