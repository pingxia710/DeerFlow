# Private Beta Open Fix Handoffs

These are separate fix lines. Do not batch them into the observation log unless
new evidence is produced.

## Provider Stream Reliability

Evidence:

- Runs `0e582444-dee2-4190-a65b-e7ad68c754fa`,
  `54ee1cae-9cca-4be9-a637-5700d3598e22`,
  `3797322c-af03-4c56-af7e-06a0f68d8534`,
  `1bcfcf14-1a26-4495-b14a-59e0c73ba359`, and
  `163c309f-e6d0-4d3c-82e8-d75791dbf5b5` ended terminal `error` with
  `Codex API stream ended without response.completed event`.
- Observed invariant: terminal state persisted and no run stayed `running`.

Likely narrow entry point:

- `backend/packages/harness/deerflow/models/openai_codex_provider.py`
- `backend/tests/test_codex_provider.py`

Minimum acceptance:

- Stream ending without `response.completed` is either retried within the
  provider retry budget or surfaced as a typed transient provider failure.
- Existing terminal persistence invariant remains true: no stuck `running` run.

Useful checks:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_codex_provider.py -q
PYTHONPATH=. uv run pytest tests/test_llm_error_handling_middleware.py -q
```

## Migration Dry-Run DB Path

Evidence:

- `PYTHONPATH=. uv run python scripts/migrate_user_isolation.py --dry-run`
  exited `0` but looked for `backend/.deer-flow/deer-flow.db`.
- The active SQLite DB in the current local stack is
  `backend/.deer-flow/data/deerflow.db`.
- Dry-run therefore did not inspect live SQL owner rows.

Likely narrow entry point:

- `backend/scripts/migrate_user_isolation.py`
- `backend/tests/test_migration_user_isolation.py`
- `backend/packages/harness/deerflow/config/paths.py`

Minimum acceptance:

- Dry-run reports which DB path it inspected.
- It inspects the same DB path used by the configured runtime, or supports an
  explicit DB path.
- Running dry-run still makes no filesystem or SQL changes.

Useful checks:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_migration_user_isolation.py -q
PYTHONPATH=. uv run python scripts/migrate_user_isolation.py --dry-run --user-id <owner-user-id>
```
