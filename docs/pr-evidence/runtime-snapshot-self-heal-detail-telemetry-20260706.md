# Runtime Snapshot Self-Heal Detail Telemetry Evidence - 2026-07-06

## Branch / Scope

- Branch: `codex/runtime-snapshot-self-heal-detail-telemetry`
- Base: latest local `main` at `6a7070e9a44658fc7283e50ec4aa9b347b5a4969`
- Scope: additive snapshot self-heal detail telemetry only
- Safety: no secrets read, no production writes, no push

## What Changed

- `recovery.snapshot_self_heal.repaired` is preserved for old clients.
- Snapshot self-heal telemetry now includes low-sensitive counts and row summaries:
  - `round_count`
  - `task_lane_count`
  - `rounds[]` with `run_id`, `round_id`, `state`
  - `task_lanes[]` with `run_id`, `round_id`, `task_id`, `status`
- No prompt, message, stack trace, artifact content, or evidence content is returned.
- No UI copy, toast, metrics/dashboard, capability/governance, or non-replay real-backend E2E changes were added.

## Response Shape

```json
{
  "recovery": {
    "snapshot_self_heal": {
      "repaired": true,
      "round_count": 1,
      "task_lane_count": 1,
      "rounds": [
        { "run_id": "run-old", "round_id": "round-old", "state": "blocked" }
      ],
      "task_lanes": [
        {
          "run_id": "run-old",
          "round_id": "round-old",
          "task_id": "task-old",
          "status": "failed"
        }
      ]
    }
  }
}
```

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

- Telemetry is still response-local only; no durable metrics, dashboard, or alerting.
- Detail rows identify repaired runtime rows, but do not include repair timestamps or per-operation error diagnostics.
- No capability, provenance, role-governance, or production real-backend E2E coverage is added.
