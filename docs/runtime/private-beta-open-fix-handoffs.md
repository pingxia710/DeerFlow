# Private Beta Open Fix Handoffs

These are separate fix lines. Do not batch them into the observation log unless
new evidence is produced.

## Provider Stream Reliability

Status: code-level retry fix added and one real Command Room/Codex smoke passed;
keep this line on the observation list through the 1-2 day beta smoke window.

Original evidence:

- Runs `0e582444-dee2-4190-a65b-e7ad68c754fa`,
  `54ee1cae-9cca-4be9-a637-5700d3598e22`,
  `3797322c-af03-4c56-af7e-06a0f68d8534`,
  `1bcfcf14-1a26-4495-b14a-59e0c73ba359`, and
  `163c309f-e6d0-4d3c-82e8-d75791dbf5b5` ended terminal `error` with
  `Codex API stream ended without response.completed event`.
- Observed invariant: terminal state persisted and no run stayed `running`.

Changed entry point:

- `backend/packages/harness/deerflow/models/openai_codex_provider.py`
- `backend/tests/test_codex_provider.py`
- `backend/tests/test_cli_auth_providers.py`

Minimum acceptance:

- Stream ending without `response.completed` is either retried within the
  provider retry budget or surfaced as a typed transient provider failure.
- Existing terminal persistence invariant remains true: no stuck `running` run.
- Real-use confirmation: 2026-07-05 12:18 CST run
  `d6591a6f-5240-42b1-a2ad-dc5acc59cfd6` used `assistant_id=command-room` with
  `model_name=gpt-5.5`, returned `success`, matched marker
  `codex-provider-smoke-20260705121835-1735`, wrote owner-scoped JSONL events,
  and did not reproduce the incomplete stream failure.

Useful checks:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_codex_provider.py -q
PYTHONPATH=. uv run pytest tests/test_llm_error_handling_middleware.py -q
```

## Migration Dry-Run DB Path

Status: fixed in the migration script; keep the useful checks below for
regression coverage. Actual beta migration still needs owner/conflict review.

Original evidence:

- `PYTHONPATH=. uv run python scripts/migrate_user_isolation.py --dry-run`
  exited `0` but looked for `backend/.deer-flow/deer-flow.db`.
- The active SQLite DB in the current local stack is
  `backend/.deer-flow/data/deerflow.db`.
- Dry-run therefore did not inspect live SQL owner rows.

Changed entry point:

- `backend/scripts/migrate_user_isolation.py`
- `backend/tests/test_migration_user_isolation.py`

Minimum acceptance:

- Dry-run reports which DB path it inspected.
- It inspects `{base_dir}/data/deerflow.db` by default, preserves legacy
  `{base_dir}/deer-flow.db` fallback, and supports explicit `--db-path`.
- Running dry-run still makes no filesystem or SQL changes.
- Beta-owner review remains separate from the DB-path fix: latest dry-run
  evidence at 2026-07-05 12:32 CST inspected
  `backend/.deer-flow/data/deerflow.db`, found `35` thread ownership records,
  kept SQL null-owner counts at zero, made no git-visible changes, and still
  reported `21` ownerless legacy thread dirs, `15` conflict thread dirs, and
  `1` legacy `command-room` agent that need intended-owner review before any
  non-dry-run migration.

Useful checks:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_migration_user_isolation.py -q
PYTHONPATH=. uv run python scripts/migrate_user_isolation.py --dry-run --user-id <owner-user-id>
```
