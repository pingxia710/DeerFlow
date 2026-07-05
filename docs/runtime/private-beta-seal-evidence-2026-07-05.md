# Private Beta Seal Evidence - 2026-07-05

## Scope

This records the production-seal evidence used to start private beta observation.
It does not claim multi-day runtime smoke is complete.

## Git Baseline

- Repo: `/Users/pingxia/projects/deer-flow`
- Branch: `main`
- HEAD: `9e3316c110e77d1706482e86155afab517f75472`
- Local delta: `main` is ahead of `origin/main` by 180 commits.
- Worktree at audit start: clean.

Recent seal commits:

- `9e3316c1 test(persistence): update bootstrap head expectation`
- `7b4228de feat(persistence): add artifact provenance index`
- `9f2a26a7 fix(migration): claim ownerless sql rows`
- `b7ac5646 fix(gateway): require db run events in production`
- `81718bf9 test(gateway): replay terminal store-only stream`

## Evidence

Backend full regression on this HEAD:

```text
PYTHONPATH=. uv run pytest -q
5587 passed, 19 skipped, 12 warnings
```

Private-beta seal subset re-run:

```text
PYTHONPATH=. uv run pytest \
  tests/test_gateway_worker_guard.py \
  tests/test_compose_default_workers.py \
  tests/test_deployment_security_guards.py \
  tests/test_thread_run_messages_pagination.py::test_stream_store_only_terminal_run_replays_persisted_events_after_restart \
  tests/test_migration_user_isolation.py \
  tests/test_artifact_provenance.py \
  tests/test_persistence_bootstrap_concurrency.py \
  -q

56 passed, 2 warnings
```

Frontend private-beta history/replay regression:

```text
cd frontend && pnpm test tests/unit/core/threads/message-merge.test.ts
62 passed
```

## Sealed Contracts

- Non-development Gateway startup fails fast when `GATEWAY_WORKERS`,
  `WEB_CONCURRENCY`, or `UVICORN_WORKERS` is greater than 1.
- Staging/shared/production startup requires `run_events.backend: db` and a
  persistent `database.backend` (`sqlite` or `postgres`).
- Terminal store-only runs can replay persisted task events and `run.terminal`
  after worker-local stream state is gone.
- `scripts/migrate_user_isolation.py` can stamp legacy SQL rows whose
  `user_id` is still `NULL` without overwriting existing owners.
- `artifact_provenance` is now a persisted SQL index with owner-scoped reads and
  best-effort writes from the run artifact endpoint.

## Remaining Private Beta Observation

The next gate is operational, not another code seal: run 1-2 days of real usage
smoke using `docs/runtime/private-beta-runbook.md`, then file separate fixes for
observed regressions.

## Post-Seal Observation Index

This section points to later observation evidence without changing the original
seal baseline above.

- Latest observation HEAD checked: `56616bad611eb9f8517b36e444bbe43947ba6a49`
  (`56616bad docs(runtime): record log health checkpoint`).
- Authoritative ongoing log:
  `docs/runtime/private-beta-smoke-log.md`.
- Latest checkpoint time: 2026-07-05 12:36 CST.
- Current local stack remained healthy: public nginx entry returned `200`,
  Gateway `/health` returned healthy, and DB run counts stayed
  `error=63`, `interrupted=7`, `success=452`, `running=0`.
- Still not complete: the 1-2 day real-use smoke window remains open, and the
  existing data migration still needs intended-owner/conflict review before any
  non-dry-run migration.
