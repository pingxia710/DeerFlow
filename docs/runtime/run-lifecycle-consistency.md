# Run Lifecycle Consistency Design

Date: 2026-07-04

Scope: design review and implementation plan only. This document does not
implement active-run leases, RunManager changes, RunRepository changes,
MemoryStreamBridge changes, checkpoint/rollback changes, migrations, Gateway API
behavior changes, authorization changes, Target Role auto-dispatch, or skill
loading policy changes.

## Current Problems

The current run lifecycle is safe enough for a single Gateway worker, but it is
not a cross-worker consistency model.

- Active-run exclusion is process-local. `RunManager.create_or_reject()` checks
  `_runs_by_thread` under one `asyncio.Lock`, then writes the run row through
  `RunStore.put()`. A second Gateway worker with the same `RunRepository` does
  not share that lock, so two workers can create active runs for one thread.
- Store writes are last-writer-wins. `RunRepository.update_status()` and
  `update_run_completion()` update by `run_id` only. A late success, late error,
  shutdown recovery, or rollback completion can overwrite a newer terminal
  decision.
- Startup recovery is too broad for multiple workers. SQLite startup currently
  lists every persisted `pending` or `running` row and marks it `error` unless
  the local process owns it. In a multi-worker deployment this can mark another
  live worker's run as failed.
- Cancellation is local. `RunManager.cancel()` can only set the in-memory
  `abort_event` and cancel the local `asyncio.Task`. If a cancel request lands
  on a non-owner worker, routers return a conflict for active store-only runs.
- Rollback status is overloaded. User rollback currently records
  `RunStatus.error` with `"Rolled back by user"` and attempts checkpoint restore
  best-effort. Success, failure, user cancel, model/tool failure, and recovery
  failure are not durable, queryable terminal causes.
- Checkpoint rollback has no fencing. `_rollback_to_pre_run_checkpoint()` writes
  to the thread checkpoint without proving that the run still owns the active
  generation. A late rollback can restore over a newer run's checkpoint.
- SSE replay is process-local. `MemoryStreamBridge` keeps a bounded in-memory
  buffer and an in-memory `ended` flag. `Last-Event-ID` cannot survive worker
  restart or route to a different Gateway worker.
- `/wait` depends on bridge completion first. It waits for `END_SENTINEL`, then
  re-reads the run status. If the bridge was cleaned up or the request lands on
  another worker, it has no durable terminal-event fallback.
- Durable run events and task events exist, but they are not the SSE replay
  source. `RunEventStore` records journal/task events with thread `seq`, while
  the bridge emits separate SSE frame ids and does not persist `END_SENTINEL`.

## Goals And Non-Goals

Goals:

- Enforce one active mutating run per thread across multiple Gateway workers.
- Make active ownership explicit with lease owner, lease token, generation,
  expiry, and heartbeat fields.
- Move active-slot acquisition from `RunManager` memory into the store as an
  atomic operation.
- Use compare-and-set transitions so terminal states cannot be overwritten by
  late owners, late recovery, late rollback, or duplicate completion writes.
- Fence checkpoint writes and rollback writes by lease token/generation.
- Make cancellation, rollback, timeout, model/tool failure, and recovery failure
  distinguishable in durable metadata.
- Provide durable SSE replay so `Last-Event-ID` can resume across worker restart
  or cross-worker join.
- Let `/wait` fall back to durable terminal state when in-memory bridge state is
  gone.
- Keep the existing `deerflow.task-event/v1` and compact `action_result`
  contract compatible.

Non-goals:

- No implementation in this document.
- No immediate database migration in this phase.
- No Gateway route shape or authz behavior change in this phase.
- No switch to Redis, pub/sub, sticky sessions, or owner proxy as a first
  requirement.
- No changes to Target Role auto-chain behavior or skill loading policy.
- No secret access.

## Data Model Draft

### Runs Table Additions

Add nullable fields first so old rows remain readable.

| Field                             | Purpose                                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------------------------ |
| `owner_worker_id`                 | Gateway worker currently allowed to drive the run.                                               |
| `lease_token`                     | Opaque random token required for heartbeat, completion, cancel consumption, and rollback writes. |
| `generation`                      | Per-thread monotonic fencing generation for the active slot.                                     |
| `lease_expires_at`                | Time after which recovery may claim the run abandoned.                                           |
| `lease_heartbeat_at`              | Last successful owner heartbeat.                                                                 |
| `cancellation_requested_at`       | Durable cancellation intent timestamp.                                                           |
| `cancel_action`                   | `interrupt` or `rollback`.                                                                       |
| `terminal_reason`                 | Durable reason independent from broad status.                                                    |
| `completed_at`                    | Terminal timestamp.                                                                              |
| `rollback_requested_at`           | Rollback intent timestamp.                                                                       |
| `rollback_checkpoint_id`          | Pre-run checkpoint id captured for rollback.                                                     |
| `rollback_restored_checkpoint_id` | Checkpoint id returned by the restore write.                                                     |
| `rollback_error`                  | Rollback failure detail, if restore failed.                                                      |
| `fenced_checkpoint_id`            | Optional latest checkpoint id written under this generation, for diagnostics.                    |

The active slot can be represented in one of two ways:

- Recommended for first DB-backed implementation: a dedicated
  `thread_active_runs` table keyed by `thread_id`, with `run_id`,
  `owner_worker_id`, `lease_token`, `generation`, and lease timestamps. This
  keeps active ownership out of historical run rows and makes acquisition a
  simple insert/update CAS.
- Acceptable alternative: a partial unique index on `runs(thread_id)` where
  `status in ('running', 'cancelling', 'rolling_back')`. This is smaller but
  couples status semantics to lease ownership and makes queued `pending`
  replacement runs harder to model.

The dedicated active-slot table is the safer option because `pending` rows can
exist without owning the active slot. It also makes lease fencing independent
from historical run rows. The first lease/CAS implementation should not add a
pending replacement queue; queueing is described below as a later PR contract.

### Active Slot Table

Recommended schema shape:

| Field                | Purpose                                              |
| -------------------- | ---------------------------------------------------- |
| `thread_id`          | Primary key. One active slot per thread.             |
| `run_id`             | Current active run.                                  |
| `owner_worker_id`    | Worker allowed to heartbeat and finish the run.      |
| `lease_token`        | Opaque owner token.                                  |
| `generation`         | Per-thread fencing value from `thread_run_counters`. |
| `lease_expires_at`   | Recovery may claim only after this time.             |
| `lease_heartbeat_at` | Last successful heartbeat.                           |
| `created_at`         | Slot creation time.                                  |
| `updated_at`         | Last slot mutation time.                             |

### Generation Source

`generation` is monotonic per `thread_id`, not globally monotonic.

Recommended source: `thread_run_counters(thread_id primary key,
next_generation integer not null)`.

- Acquiring an active slot obtains `generation = next_generation`, then advances
  `next_generation`.
- Deleting `thread_active_runs` on terminal completion does not delete the
  counter row, so a later run cannot reuse an old generation.
- Gaps are allowed. Conflicts and transaction retries may burn a generation if
  the database cannot cheaply roll it back.
- A memory store must keep the same semantic with an in-process
  `dict[thread_id, next_generation]`; tests should assert that deleting the
  memory active slot does not reset the counter.

Alternative generation sources:

- Keep a tombstone `thread_active_runs` row after release, with nullable
  `run_id` and an incremented `generation`. This avoids a second table but makes
  active-slot predicates more complex.
- Store the counter in thread metadata. This couples runtime fencing to thread
  lifecycle writes and should be used only if that metadata row is already
  transactionally available.

## Active-Slot Transaction Contract

All active-slot operations are store-level contracts. `rowcount = 0` is a normal
fencing failure, not an exceptional condition. The caller must stop writing run
status, stream frames, checkpoints, completion metadata, and final messages when
any fenced write returns false.

### Acquire Active Slot

`pending -> running` and active-slot acquire must happen in the same transaction.

Pseudo SQL:

```sql
BEGIN;

INSERT INTO thread_run_counters(thread_id, next_generation)
VALUES (:thread_id, 1)
ON CONFLICT(thread_id) DO NOTHING;

UPDATE thread_run_counters
SET next_generation = next_generation + 1
WHERE thread_id = :thread_id
RETURNING next_generation - 1 AS generation;

INSERT INTO thread_active_runs (
  thread_id,
  run_id,
  owner_worker_id,
  lease_token,
  generation,
  lease_expires_at,
  lease_heartbeat_at,
  created_at,
  updated_at
)
VALUES (
  :thread_id,
  :run_id,
  :owner_worker_id,
  :lease_token,
  :generation,
  :lease_expires_at,
  :now,
  :now,
  :now
);

UPDATE runs
SET
  status = 'running',
  owner_worker_id = :owner_worker_id,
  lease_token = :lease_token,
  generation = :generation,
  lease_expires_at = :lease_expires_at,
  lease_heartbeat_at = :now,
  updated_at = :now
WHERE
  run_id = :run_id
  AND thread_id = :thread_id
  AND status = 'pending'
  AND completed_at IS NULL;

COMMIT;
```

If the active-slot insert conflicts or the run update affects 0 rows before
commit, rollback and return conflict/fenced. Do not leave an active slot without
a running row. SQLAlchemy implementations can use the same transaction even if
SQLite/Postgres syntax differs.

### Heartbeat

Heartbeat is a fenced update on the active-slot row, optionally mirrored onto
the run row in the same transaction:

```sql
UPDATE thread_active_runs
SET
  lease_expires_at = :new_lease_expires_at,
  lease_heartbeat_at = :now,
  updated_at = :now
WHERE
  thread_id = :thread_id
  AND run_id = :run_id
  AND lease_token = :lease_token
  AND generation = :generation
  AND lease_expires_at >= :now;
```

If `rowcount = 0`, the owner is stale or expired and must stop writing.

### Cancel Intent

Cancel intent is written on the run row, not by mutating the active-slot owner:

```sql
UPDATE runs
SET
  cancellation_requested_at = COALESCE(cancellation_requested_at, :now),
  cancel_action =
    CASE
      WHEN cancel_action = 'rollback' THEN 'rollback'
      WHEN :action = 'rollback' THEN 'rollback'
      ELSE 'interrupt'
    END,
  rollback_requested_at =
    CASE
      WHEN :action = 'rollback' THEN COALESCE(rollback_requested_at, :now)
      ELSE rollback_requested_at
    END,
  updated_at = :now
WHERE
  run_id = :run_id
  AND status IN ('pending', 'running', 'cancelling', 'rolling_back')
  AND completed_at IS NULL;
```

Duplicate requests are idempotent. `rollback` is stronger than `interrupt` and
cannot be downgraded by a later interrupt request.

### Terminal Complete And Release

Terminal CAS and active-slot release must happen in the same transaction.

```sql
BEGIN;

UPDATE runs
SET
  status = :terminal_status,
  terminal_reason = :terminal_reason,
  completed_at = :now,
  error = :error,
  updated_at = :now
WHERE
  run_id = :run_id
  AND thread_id = :thread_id
  AND lease_token = :lease_token
  AND generation = :generation
  AND status IN ('running', 'cancelling', 'rolling_back')
  AND completed_at IS NULL;

DELETE FROM thread_active_runs
WHERE
  thread_id = :thread_id
  AND run_id = :run_id
  AND lease_token = :lease_token
  AND generation = :generation;

COMMIT;
```

Both statements must affect the expected row. Otherwise rollback and return
false; a stale owner is not allowed to complete or release another owner. After
this transaction commits, terminal status is immutable.

### Metadata Backfill

Completion metadata that arrives after terminal CAS, such as token usage, final
message ids, or aggregate counters, may be written only by the same
`lease_token + generation` and only without changing `status` or
`terminal_reason`:

```sql
UPDATE runs
SET
  total_input_tokens = COALESCE(total_input_tokens, :total_input_tokens),
  total_output_tokens = COALESCE(total_output_tokens, :total_output_tokens),
  total_tokens = COALESCE(total_tokens, :total_tokens),
  llm_call_count = COALESCE(llm_call_count, :llm_call_count),
  updated_at = :now
WHERE
  run_id = :run_id
  AND lease_token = :lease_token
  AND generation = :generation
  AND status = :already_terminal_status
  AND (
    terminal_reason = :same_terminal_reason
    OR (terminal_reason IS NULL AND :same_terminal_reason IS NULL)
  );
```

Old `update_run_completion(status=...)` last-writer-wins behavior cannot remain
the main path. Stale-owner metadata writes must be rejected the same way stale
status writes are rejected.

### Expired Recovery

Recovery may claim only an expired generation:

```sql
BEGIN;

UPDATE thread_active_runs
SET
  owner_worker_id = :recovery_worker_id,
  lease_token = :recovery_lease_token,
  lease_heartbeat_at = :now,
  lease_expires_at = :short_recovery_lease_expires_at,
  updated_at = :now
WHERE
  thread_id = :thread_id
  AND run_id = :run_id
  AND generation = :old_generation
  AND lease_expires_at < :now;

UPDATE runs
SET
  status = :terminal_status,
  terminal_reason = :terminal_reason,
  completed_at = :now,
  error = :error,
  updated_at = :now
WHERE
  run_id = :run_id
  AND thread_id = :thread_id
  AND generation = :old_generation
  AND status IN ('pending', 'running', 'cancelling', 'rolling_back')
  AND completed_at IS NULL;

DELETE FROM thread_active_runs
WHERE
  thread_id = :thread_id
  AND run_id = :run_id
  AND generation = :old_generation
  AND lease_token = :recovery_lease_token;

COMMIT;
```

If any step affects 0 rows, another owner or recovery worker won the race. The
caller returns fenced/no-op and stops.

## Durable SSE Event Log

The current `RunEventStore` is useful but should not be overloaded blindly as
the full SSE replay source, because trace content can be truncated and journal
events are not one-to-one with SSE frames.

Recommended model: add a dedicated durable stream table/interface:

| Field         | Purpose                                                                  |
| ------------- | ------------------------------------------------------------------------ |
| `thread_id`   | Thread owner boundary and replay partition.                              |
| `run_id`      | Run stream partition.                                                    |
| `stream_seq`  | Monotonic per-run sequence used as the SSE id.                           |
| `event`       | SSE event name such as `metadata`, `values`, `messages`, `error`, `end`. |
| `data_json`   | Full serialized SSE payload.                                             |
| `is_terminal` | True for persisted end/terminal marker.                                  |
| `created_at`  | Ordering and retention.                                                  |

`RunEventStore` can still record display/audit/task events. The durable SSE log
is the source for `Last-Event-ID` replay and must not truncate event payloads
needed by clients.

## State Machine

Proposed canonical store statuses:

| Status            | Class               | Meaning                                                         | Active slot owner? |
| ----------------- | ------------------- | --------------------------------------------------------------- | ------------------ |
| `pending`         | inactive or waiting | Created but not yet running. May be waiting for an active slot. | No                 |
| `running`         | active              | Worker owns lease and is streaming/model-running.               | Yes                |
| `cancelling`      | stopping            | Cancel intent accepted; owner is stopping without rollback.     | Yes                |
| `rolling_back`    | stopping            | Rollback intent accepted; owner is restoring checkpoint.        | Yes                |
| `success`         | terminal            | Completed normally.                                             | No                 |
| `error`           | terminal            | Model/tool/runtime failure.                                     | No                 |
| `interrupted`     | terminal legacy     | Existing compatibility value for interrupted runs.              | No                 |
| `cancelled`       | terminal canonical  | User cancellation completed.                                    | No                 |
| `timed_out`       | terminal canonical  | Run exceeded execution or lease timeout.                        | No                 |
| `rolled_back`     | terminal canonical  | User rollback completed and checkpoint restore succeeded.       | No                 |
| `rollback_failed` | terminal canonical  | User rollback requested, but checkpoint restore failed.         | No                 |

Compatibility aliases:

| Existing value                       | Canonical interpretation                                                                        |
| ------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `timeout`                            | `timed_out`                                                                                     |
| `interrupted`                        | legacy user cancel/interruption; keep readable and writable until routers/frontend are migrated |
| `error` with `"Rolled back by user"` | legacy rollback request without success/failure metadata                                        |

Transition table:

| From                      | To                           | Required CAS predicate                                                 |
| ------------------------- | ---------------------------- | ---------------------------------------------------------------------- |
| none                      | `pending`                    | Store creates run row.                                                 |
| `pending`                 | `running`                    | Active slot acquired for thread generation.                            |
| `running`                 | `cancelling`                 | Same lease token/generation and cancellation intent.                   |
| `running`                 | `rolling_back`               | Same lease token/generation and rollback intent.                       |
| `running`                 | `success`                    | Same lease token/generation, not terminal.                             |
| `running`                 | `error`                      | Same lease token/generation, not terminal.                             |
| `running`                 | `timed_out`                  | Same lease token/generation or expired lease recovery CAS.             |
| `cancelling`              | `cancelled` or `interrupted` | Same lease token/generation.                                           |
| `rolling_back`            | `rolled_back`                | Same lease token/generation and checkpoint restore fenced.             |
| `rolling_back`            | `rollback_failed`            | Same lease token/generation and rollback error recorded.               |
| active with expired lease | `error` or `timed_out`       | Recovery CAS proves `lease_expires_at < now` and generation unchanged. |
| terminal                  | terminal                     | Not allowed except idempotent same-status write with same generation.  |

Terminal status writes must release the active slot in the same transaction as
the terminal CAS.

Terminal immutability rule:

- Once terminal CAS commits, `status` and `terminal_reason` are immutable.
- A repeated terminal write with the same `lease_token + generation` may be
  accepted only as idempotent no-op or metadata backfill.
- A terminal write from an old owner, old generation, recovery loser, or late
  worker completion must return fenced and stop.

## Compatibility With RunStatus, action_result, And terminal_reason

Run lifecycle status and task-event status are related but not the same layer.

- `RunStatus` is the top-level run row lifecycle.
- `task_event.action_result.status` is the subtask/tool terminal contract pinned
  by `contracts/task_event_contract.json`.
- `terminal_reason` is the durable reason that explains a terminal status.

Keep `deerflow.task-event/v1` unchanged. The task event cases remain:

| Task event       | action_result.status | terminal_reason  |
| ---------------- | -------------------- | ---------------- |
| `task_completed` | `completed`          | null             |
| `task_failed`    | `failed`             | `failed`         |
| `task_cancelled` | `cancelled`          | `user_cancelled` |
| `task_timed_out` | `timed_out`          | `timed_out`      |

For run rows, use compatible terminal reasons:

| Run outcome                | Proposed run status                 | terminal_reason                    |
| -------------------------- | ----------------------------------- | ---------------------------------- |
| Normal completion          | `success`                           | null                               |
| Model/tool/runtime failure | `error`                             | `failed` or `model_or_tool_failed` |
| User interrupt             | `cancelled` or legacy `interrupted` | `user_cancelled`                   |
| Timeout                    | `timed_out` or legacy `timeout`     | `timed_out`                        |
| Recovery of expired lease  | `error`                             | `lease_expired_recovered`          |
| Rollback success           | `rolled_back`                       | `user_rolled_back`                 |
| Rollback failure           | `rollback_failed`                   | `rollback_failed`                  |

API compatibility plan:

- Existing readers must continue to accept `pending`, `running`, `success`,
  `error`, `timeout`, and `interrupted`.
- The first implementation batch should not write new canonical statuses such as
  `cancelled`, `timed_out`, `rolled_back`, or `rollback_failed` into
  `runs.status` until all readers, enums, routers, and frontend code accept
  them. Use legacy `interrupted`, `timeout`, or `error` plus
  `terminal_reason`/rollback metadata during the compatibility window.
- After every reader is compatible, a later writer-flip PR may write canonical
  statuses.
- Active checks must be centralized in a store/helper predicate such as
  `is_active_status(status)` and `is_terminal_status(status)`. Do not scatter
  ad hoc checks like `(pending, running)` across routers, stores, and workers.
- New internal code may normalize aliases to canonical states internally, but
  public route response changes should be a separate compatibility decision.
- `RunResponse` can add optional fields later only if additive route response
  behavior is accepted. This design does not require changing it in the first
  implementation PR.

## Internal Interface Plan

### RunStore

Add store-level methods instead of expanding in-memory checks:

- `create_pending_run(...) -> RunRecord`
- `try_acquire_active_slot(thread_id, run_id, owner_worker_id, ttl) -> Lease`
- `create_or_acquire_active_run(...) -> RunRecord | Conflict`
- `heartbeat_lease(run_id, lease_token, generation, ttl) -> bool`
- `request_cancel(run_id, action, requested_by) -> bool`
- `consume_cancel_intent(run_id, lease_token, generation) -> CancelIntent | None`
- `cas_status(run_id, from_statuses, to_status, lease_token, generation, **fields) -> bool`
- `complete_run(run_id, from_statuses, terminal_status, lease_token, generation, completion_fields) -> bool`
- `backfill_completion_metadata(run_id, terminal_status, lease_token, generation, metadata) -> bool`
- `release_active_slot(thread_id, run_id, generation) -> bool`
- `list_expired_active_leases(now) -> list[RunLeaseRow]`
- `recover_expired_lease(run_id, generation, terminal_status, terminal_reason) -> bool`

All mutating methods that touch an active run must include the fencing token.
Methods that cannot prove the token/generation should return false rather than
falling back to last-writer-wins.

### RunManager

Future RunManager work should become orchestration over store primitives:

- Generate a stable `worker_id` on process startup.
- Ask the store to atomically create/acquire active slot.
- Start a heartbeat task after `running`.
- Poll durable cancellation intent while the worker owns the lease.
- Use CAS completion methods instead of direct `set_status()`.
- Treat false CAS as a fenced stale-owner condition and stop writing stream,
  checkpoint, or final status.
- Keep the in-memory registry as a local task map, not as the source of truth
  for active-run exclusion.

### Worker And Checkpointer

The worker should receive `lease_token` and `generation` in `RunRecord` or a
small `RunLease` object. It must check ownership before:

- setting `running`;
- writing final status;
- persisting completion counters;
- writing rollback checkpoint restore;
- syncing thread status from final result.

Checkpoint fencing can be implemented with a wrapper around the checkpointer
that verifies `run_id + generation + lease_token` before writes. Rollback is
fail-closed:

- Check generation before restore/delete-thread.
- Execute the checkpoint operation.
- Check generation again before considering the restore successful.
- If the checkpointer cannot provide native CAS and the wrapper cannot guarantee
  that no newer generation was overwritten, record `rollback_failed` and do not
  perform an unfenced restore.
- Delete-thread rollback operations must be fenced by the same lease token and
  generation.
- DB-backed checkpointers need a follow-up transactional fencing PR; this is not
  only a diagnostic improvement.

## SSE, wait, And Replay Design

### Durable Publish Path

On every bridge publish:

1. Serialize the exact SSE frame payload.
2. Insert it into the durable stream log with the next `stream_seq`.
3. Notify local subscribers.
4. Emit the SSE id from the durable `stream_seq`.

`publish_end()` must persist an `end` event or terminal marker. This makes
process restart indistinguishable from late reconnect: the subscriber can replay
until it sees durable end.

Terminal success ordering:

1. Write the final checkpoint/state needed by `/wait` success.
2. Verify the same `lease_token + generation` still owns the run.
3. Commit terminal CAS and active-slot release in one store transaction.
4. Persist durable `end`.
5. Notify live subscribers.

There must be no success path where the run row is terminal `success` but
`/wait` cannot read the final checkpoint. If final checkpoint write fails,
completion must choose a non-success terminal reason instead of publishing a
successful run.

Failure handling:

- Durable `end` written but live notify failed: replay recovers from the durable
  row; live subscribers may wake on polling.
- Terminal status written but durable `end` failed: `subscribe()` and `/wait`
  synthesize an end from the run row terminal status, and a background repair job
  backfills the durable end row.
- Durable `end` must not remain permanently ahead of the run row. If a durable
  terminal frame is written before terminal CAS, the writer must retry terminal
  CAS immediately; if CAS fails, append a correction/error frame and treat the
  stale writer as fenced. The preferred implementation is terminal CAS before
  durable end.

### Last-Event-ID

Current `MemoryStreamBridge` ids are `{timestamp_ms}-{per_run_seq}`. Future
durable ids should be DB-backed and opaque to clients.

Recommendation:

- Emit numeric `stream_seq` as the new SSE id once durable replay is enabled.
- Keep accepting old ids only when the retained in-memory buffer still has the
  matching old `{timestamp_ms}-{per_run_seq}` event. Do not parse the trailing
  sequence and use it against the durable table; the namespaces are unrelated.
- Treat unknown or evicted ids as replay-from-earliest retained/durable event,
  matching current safe behavior. This may duplicate events, so clients must
  treat event ids as idempotency keys and handle repeated task/message frames.

### Subscribe Semantics

`subscribe(run_id, last_event_id)` should become a layered reader:

- First replay durable stream rows after the requested id.
- If a terminal durable `end` exists, yield `END_SENTINEL` and return.
- If no terminal row exists and the owner is local, continue with low-latency
  local condition notifications.
- If no terminal row exists and the owner is remote, poll durable rows or use a
  shared notification backend when configured.

### `/wait` Fallback

`wait_for_run_completion()` should not require an in-memory bridge to observe
completion.

Recommended behavior:

- Prefer bridge subscription when local and available.
- In parallel or as fallback, poll `RunStore.get(run_id)` for a terminal status.
- On terminal `success`, read the checkpoint and return the same final state as
  today.
- On terminal non-success, return the existing `{status, error}` shape.
- On client disconnect with `on_disconnect=cancel`, write durable cancellation
  intent instead of only calling local `RunManager.cancel()`.
- Poll interval should start small, for example 250 ms, and cap with jitter at
  about 1 second. The route-level timeout remains the maximum wait budget.
- Client disconnect stops the HTTP wait loop. It does not cancel the run unless
  `on_disconnect=cancel` is configured, in which case it writes durable cancel
  intent and returns.

This keeps the external `/wait` response shape compatible while removing the
bridge as the only completion signal.

## Cross-Worker Cancel And Join

### Recommended First Phase

Support cross-worker behavior with durable replay plus cancellation intent, no
owner proxy.

Cancel:

- A cancel request on any worker resolves the run by store row and owner
  boundary.
- If the run is active but not local, the router writes
  `cancellation_requested_at` and `cancel_action` with CAS against active
  status.
- The owner worker heartbeat/poll loop consumes that intent and transitions
  `running -> cancelling` or `running -> rolling_back`.
- `wait=true` waits through durable run status or durable stream end.

Join/reconnect:

- A join request on any worker reads durable SSE rows after `Last-Event-ID`.
- If the owner is local, it also follows the local bridge for low latency.
- If the owner is remote, it keeps polling durable rows until terminal end or
  uses configured shared notification when available.

This avoids owner proxy and shared bus as a first requirement.

### Cancellation And Rollback Semantics

Store-level rules:

- `rollback` has priority over `interrupt` while the run is non-terminal.
- A duplicate `interrupt` or duplicate `rollback` is an idempotent success.
- If `interrupt` is requested first and the owner has not reached terminal, a
  later `rollback` may upgrade the intent to rollback.
- If `rollback` is already requested, a later `interrupt` is a no-op and must
  not downgrade the action.
- If the owner has already transitioned to `cancelling`, a later rollback may
  upgrade only while the run is still active and the pre-run checkpoint metadata
  exists.
- If the owner has already transitioned to `rolling_back`, interrupt cannot
  downgrade it.
- After terminal, cancel returns store-level no-op success with the current
  terminal outcome. Routers can preserve today's API mapping, but the store must
  not report a terminal run as a fresh conflict.
- If a non-owner writes cancel intent and the owner crashes, recovery observes
  the intent after lease expiry.
- If `cancel_action = 'interrupt'` at recovery time, recovery records a user
  cancel terminal reason, using legacy status during rollout.
- If `cancel_action = 'rollback'` at recovery time, recovery must not attempt an
  unfenced rollback. It records `rollback_failed` semantics, or legacy `error`
  plus `terminal_reason='rollback_failed_owner_lost'` during status rollout.

### Pending Replacement Runs

The first lease/CAS implementation should not add a pending replacement queue.
It should keep the active-slot invariant simple: one active run per thread, and
no automatic scheduler for replacement runs.

First-phase behavior:

- `reject` treats any active slot as conflict.
- Inactive `pending` rows created before the lease rollout do not own the active
  slot and must not be auto-started by recovery.
- `interrupt`/`rollback` should be implemented as cancellation intent against
  the active run. The first phase should not create a replacement pending run;
  clients can create the next run after the active run reaches terminal.

If a later PR preserves the current "create replacement immediately" behavior,
it needs an explicit queue contract:

- Order pending replacements by `(created_at, run_id)`.
- `reject` considers both the active slot and queued replacements as conflict.
- Active-slot release wakes exactly one queued run by store-level acquire CAS.
- Multiple workers race through the same `try_acquire_pending_replacement()`;
  only one can move `pending -> running` because acquire owns the active slot in
  the same transaction.
- A pending replacement whose predecessor ends in `rollback_failed` starts only
  if the rollback policy allows subsequent runs for that checkpoint backend.

### Alternatives

Owner proxy:

- Non-owner workers forward cancel/join to `owner_worker_id`.
- Lower latency, but requires worker discovery, internal auth, failure handling,
  and more moving parts.

Pub/sub or shared stream bus:

- Redis streams or Postgres LISTEN/NOTIFY can wake remote subscribers.
- Good latency, but adds operational dependency or backend-specific behavior.

Sticky session:

- Route run URLs to the owning worker.
- Simple for one load balancer, but weak across restart and does not solve
  durable replay or cancel after owner death.

The first phase should not require owner proxy. Durable replay plus cancellation
intent is enough to make behavior correct; pub/sub can improve latency later.

## Migration And Compatibility

No migration is performed in this design-only phase.

Future migration strategy:

- Add nullable lease, generation, terminal_reason, completion, cancel, and
  rollback columns.
- Add durable stream table with `(run_id, stream_seq)` uniqueness.
- Backfill nothing for terminal old rows.
- Do not automatically kill legacy `pending`/`running` rows with null lease
  fields during a rolling deploy. An old worker may still be executing them.
- Use one of these deployment fences before new recovery is enabled:
  - stop-the-world deploy for all Gateway workers;
  - feature flag with recovery disabled until all workers run lease-aware code;
  - grace period longer than the maximum old run duration, followed by one-time
    legacy orphan recovery.
- New readers must be deployed before new writers. Readers accept nullable lease
  fields, old statuses, future canonical statuses, and missing durable stream
  rows. Writers start emitting lease/generation/durable stream data only after
  reader compatibility is live.
- Legacy rows may be recovered once with
  `terminal_reason='legacy_orphan_recovered'` only after the deployment fence
  proves no old worker can still own them.
- Single-worker memory store keeps behavior compatible by implementing the same
  interface with an in-process lock and generation counter.
- Existing API response fields remain. Optional additions must be separately
  approved.
- Existing SSE clients treat event ids as opaque. Switching from `{ts}-{seq}` to
  DB `stream_seq` should not require a client change, but the server should keep
  old id support during a transition window only for retained in-memory buffers.
- Current `RunEventStore` message and task-event contracts remain readable.
  Durable SSE replay is additive and does not replace
  `contracts/task_event_contract.json`.

## Test Plan

Required tests for the implementation PRs:

- Two `RunManager` instances sharing one `RunRepository` concurrently call
  `create_or_reject(..., multitask_strategy='reject')`; exactly one acquires
  the active slot and the other gets conflict.
- Active-slot acquire and `pending -> running` commit in one transaction; forced
  failure of either side leaves neither a running row nor an orphan slot.
- Terminal CAS and active-slot release commit in one transaction; forced failure
  leaves the prior active owner recoverable and does not release another owner.
- Per-thread generation never decreases after active-slot release; the memory
  store matches the SQL store semantic.
- Stale generation CAS returns `rowcount = 0`/false and the caller stops
  writing.
- Worker A heartbeats an active run; worker B startup recovery does not mark it
  error.
- Recovery can mark a run only after `lease_expires_at < now`.
- Late owner completion with stale `lease_token` or `generation` cannot
  overwrite a terminal status.
- Late error and late success cannot overwrite each other after terminal CAS.
- Terminal metadata backfill succeeds only for the same
  `lease_token + generation` and does not change `status` or `terminal_reason`.
- Stale-owner completion metadata is rejected.
- Canonical status rollout tests prove readers accept future statuses before
  writers emit them.
- Active status predicates are covered through a shared helper/store predicate,
  not duplicated `(pending, running)` checks.
- Cancel sent to a non-owner worker writes cancellation intent; owner consumes
  it and reaches the expected terminal status.
- Concurrent interrupt and rollback requests resolve with rollback priority.
- Duplicate cancel and duplicate rollback are idempotent.
- Interrupt can be upgraded to rollback before terminal; rollback cannot be
  downgraded to interrupt.
- Cancel after terminal is store-level no-op success with the current terminal
  outcome.
- Owner crash after non-owner cancel intent is recovered after lease expiry.
- Owner crash after rollback intent records rollback-failed semantics without
  unfenced checkpoint writes.
- `wait=true` cancel on a non-owner worker waits by durable status, not local
  task object.
- First-phase pending replacement behavior is tested explicitly: no automatic
  queue start. If the later queue PR lands, add ordering and single-acquirer
  tests for pending replacements.
- Rollback with a stale generation cannot write a checkpoint over a newer run.
- Rollback restore/delete-thread checks generation before and after the
  checkpoint operation.
- A checkpointer without native CAS fails closed to rollback_failed semantics.
- Rollback success records `rolled_back`, `rollback_checkpoint_id`, and
  `rollback_restored_checkpoint_id`.
- Rollback failure records `rollback_failed` and `rollback_error`; later runs
  are allowed only after active slot release or explicit recovery decision.
- Bridge cleanup or worker restart does not prevent `Last-Event-ID` replay from
  durable log.
- Old `{timestamp_ms}-{per_run_seq}` `Last-Event-ID` is honored only when the
  in-memory retained buffer has the matching event.
- Unknown or evicted `Last-Event-ID` replays from earliest retained/durable event
  and clients tolerate duplicate ids.
- `/wait` returns a deterministic terminal result when the memory bridge is gone
  but the run store is terminal.
- `/wait` cannot return success unless the final checkpoint is readable.
- Terminal status without durable end produces a synthetic end and schedules
  durable-end backfill.
- Durable event written but live notify failed is replayed on reconnect.
- Durable end written while run status is still active is repaired or corrected;
  tests prevent long-lived `end + running` inconsistency.
- Durable `end` replay yields the same terminal SSE behavior as in-memory
  `END_SENTINEL`.
- Existing task-event contract tests continue to pass; cancelled is not
  boundary_blocked, timed_out is not generic failed.
- Legacy old rows with null lease fields are not recovered during rolling deploy
  until the feature flag/deployment fence allows it.
- After the fence, legacy old rows with null lease fields are recovered
  deterministically and do not block new single-worker runs forever.

## Implementation PR Split

1. Store contract and tests only.
   - Add lease/CAS methods to `RunStore` interfaces and memory/sql tests.
   - Add shared `is_active_status()` and `is_terminal_status()` predicates.
   - Add per-thread generation counter behavior to memory store tests.
   - Do not wire into `RunManager` yet.

2. Reader compatibility and schema migration.
   - Make readers tolerate nullable lease fields and future canonical statuses.
   - Add nullable columns and active-slot table or indexes.
   - Add `thread_run_counters` if the dedicated active-slot table is selected.
   - Keep writers on legacy status values.

3. Repository CAS writers.
   - Implement `RunRepository` atomic acquire, heartbeat, cancellation intent,
     recovery, and terminal CAS.
   - Implement terminal metadata backfill with same `lease_token + generation`.
   - Keep recovery behind a feature flag/deployment fence.

4. RunManager lease ownership.
   - Generate `worker_id`.
   - Wire create/acquire, heartbeat, cancellation-intent polling, and fenced
     completion into `RunManager`.
   - Keep public route behavior unchanged.
   - Do not add pending replacement queue in this PR.

5. Worker and rollback fencing.
   - Pass lease token/generation into `run_agent`.
   - Fence terminal writes, completion counters, thread status sync, and
     rollback checkpoint writes.
   - Split rollback terminal outcomes into `rolled_back` and
     `rollback_failed` internally, using legacy status until writer rollout.
   - Fail closed when the checkpointer cannot guarantee fenced restore/delete.

6. Durable SSE stream log.
   - Add durable stream store.
   - Persist each SSE frame and terminal end.
   - Replay from durable `Last-Event-ID`.
   - Restrict old `{timestamp_ms}-{per_run_seq}` ids to retained memory-buffer
     hits.

7. `/wait`, join, and cross-worker cancel.
   - Add durable terminal fallback for `/wait`.
   - Let join stream from durable log on non-owner workers.
   - Let cancel write durable intent when owner is remote.
   - Add synthetic durable end/backfill behavior when terminal status exists
     without end.

8. Optional pending replacement queue.
   - Define queued replacement ordering and single-acquirer startup.
   - Preserve or intentionally revise immediate replacement semantics in a
     separate API compatibility decision.

9. Canonical status writer rollout and cleanup.
   - Decide whether public `RunResponse.status` keeps legacy aliases forever or
     exposes canonical `cancelled`/`timed_out`/`rolled_back`.
   - Flip writers to canonical statuses only after all readers are compatible.
   - Add operator docs for lease TTL and recovery.

## Risks And Rollback Strategy

Risks:

- Lease TTL too short can falsely recover slow but live runs.
- Lease TTL too long delays recovery after a crashed owner.
- DB-backed durable SSE replay can add write pressure on high-volume streams.
- Split rollback statuses can surprise clients if exposed too early.
- Fencing checkpointer writes through a wrapper is weaker than native
  transactional checkpoint fencing.
- `interrupt`/`rollback` multitask strategy may need a pending replacement run
  that waits for active-slot release; this preserves response shape but changes
  start timing.

Rollback strategy:

- Ship nullable columns and new store methods behind code paths that keep the
  existing single-worker behavior until fully wired.
- Keep old status aliases readable and writable during rollout.
- Feature-flag durable SSE replay so the process-local bridge can be restored
  quickly if replay writes cause pressure.
- If lease acquisition causes false conflicts, disable cross-worker active-slot
  enforcement and fall back to current single-worker lock while retaining
  terminal CAS tests.
- If rollback fencing blocks legitimate restores, fail closed to
  `rollback_failed` rather than writing an unfenced checkpoint.

## Open Decisions

- Active-slot representation: dedicated `thread_active_runs` table
  (recommended) versus partial unique index on `runs`.
- Public status exposure: keep legacy `interrupted`/`timeout` in API responses
  or expose canonical `cancelled`/`timed_out` in a separate compatibility PR.
- Durable stream storage: separate table/interface (recommended) versus
  carefully extending `RunEventStore`.
- Cross-worker latency target: DB polling only for first phase, or add
  LISTEN/NOTIFY/Redis after correctness lands.
- Rollback failure policy: allow subsequent runs after active-slot release with
  `rollback_failed`, or require an explicit operator repair flag for some
  checkpoint backends.
