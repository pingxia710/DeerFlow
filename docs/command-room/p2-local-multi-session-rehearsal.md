# P2-a Local Multi-session Rehearsal

P2-a is a minimal, local-only rehearsal for command-room session isolation. It uses deterministic fake task events and the in-memory journal/event-store path to verify that replay is scoped by `thread_id` + `run_id`.

## What it validates

Backend journal replay (`tests/test_task_event_journal.py`):

- 5 fake command rooms.
- 2 conversations per room.
- 2 rounds per conversation.
- 5/6 subtasks per round.
- Reused `task_id` values across different thread/run pairs.
- Interleaved writes into `MemoryRunEventStore`.
- `list_messages_by_run` and `list_events` replay do not leak across thread/run boundaries.
- Terminal task events (`task_completed` / `task_failed`) retain correct task/thread/run metadata.
- Wrong thread/run lookups return empty results.

## What it does not validate

This rehearsal intentionally does **not** cover real LLMs, external APIs, providers, SSE, browsers, Playwright/E2E, service startup, production behavior, or production concurrency. It is a pure fake/deterministic local test of journal persistence and replay/session isolation.

## Suggested command

```bash
cd backend && python -m pytest tests/test_task_event_journal.py -q
```

One-shot Codex transport behavior is covered separately by
`tests/test_codex_cli_subagent.py` and `tests/test_task_tool_core_logic.py`; this
rehearsal intentionally does not simulate a global executor queue or scheduler.


## P2-b Frontend Offline Replay/Merge Rehearsal

P2-b is the frontend-side companion rehearsal for P2-a. It is a local-only deterministic unit test that uses fake `RunMessage` and `task_event` rows to exercise offline history replay, visible-message merge, and subtask state hydration without touching live services.

### What it validates

- 5 fake command rooms, with owner/thread naming reflected in generated `thread_id` / `run_id` values.
- 2 conversations per room and 2 rounds/runs per conversation.
- 5 subtasks in round 1 and 6 subtasks in round 2.
- The same `task_id` values reused under different `thread_id` + `run_id` scopes.
- Interleaved `task_event` control rows and visible lead-agent AI/human rows across threads/runs.
- Replay filtered by `thread_id` through `applyTaskEventRunMessages` and `applySubtaskUpdateInState` keeps each run's subtask state isolated.
- Wrong-thread replay produces no subtask updates.
- `buildVisibleHistoryMessages` excludes `task_event` control rows from chat-visible history.
- Visible history follows backend run-list order first, then run-local `seq`, so same-thread multi-round visible messages stay attributed to the correct run.

### What it does not validate

P2-b intentionally does **not** validate real LLMs, external APIs, providers, SSE, browser behavior, Playwright/E2E behavior, service startup, production behavior, or production concurrency. It is a pure frontend offline/unit-layer rehearsal.

### Suggested command

```bash
cd frontend && pnpm test tests/unit/core/threads/message-merge.test.ts tests/unit/core/tasks/context.test.ts
```
