# Multi-conversation / Command Room runtime contract

This document records the first contract slice for the Command Room multi-conversation, multi-run, and AI-AI collaboration runtime. It is intentionally limited to observed backend/runtime contracts and does not introduce production migrations or new isolation semantics.

## Current ownership map

| Object | Current owner / primary keys | Notes |
| --- | --- | --- |
| Thread | `thread_id` | User-facing conversation container and the stable root for checkpoints. |
| Run | `thread_id + run_id` | One execution attempt within a thread. Run-scoped APIs and event rows use `run_id` for filtering/timeline views. |
| Message timeline | `thread_id`, usually filtered by `run_id`, ordered by event `seq`/created order | User and AI messages are stored as run journal/event rows. Display contract classifies chat, control, and hidden surfaces. |
| Checkpoint history | `thread_id + checkpoint_ns + checkpoint_id` | LangGraph checkpoint identity is thread-rooted. `checkpoint_ns` is currently effectively the empty string (`""`). `run_id` is not a checkpoint isolation key. |
| Task event | Parent `thread_id + run_id + task_id` | Task lifecycle/control events belong to the parent run timeline. |
| Action result | Parent `thread_id + run_id + task_id` | Results are attributed to the invoking parent task/run rather than a child conversation. |
| Subagent finding | Parent `thread_id + run_id + task_id` | Current phase keeps findings attached to the parent task/run. Subagents are isolated by runtime configuration, not by child thread identity. |
| Artifact | String refs today, associated through parent event/result context | Future work should add structured provenance, but first phase remains string-compatible. |

## Three histories that must not be conflated

- **Thread timeline**: the durable user-facing conversation stream for a `thread_id`. It spans runs and is the umbrella history users expect when they reopen a conversation.
- **Run timeline**: the execution/event stream for a specific `thread_id + run_id`. It is the correct place for task events, action results, subagent findings, middleware messages, and per-run display filtering.
- **Checkpoint history**: LangGraph state snapshots addressed by `thread_id + checkpoint_ns + checkpoint_id`. This is not the same as either timeline. In the current implementation `checkpoint_ns` is basically fixed to `""`, and `run_id` does **not** isolate checkpoints.

## Current checkpoint semantics

The checkpoint contract is currently:

```text
thread_id + checkpoint_ns(usually "") + checkpoint_id
```

Do not assume `run_id` creates checkpoint isolation. Subagent execution is currently isolated by running without the parent checkpointer (`checkpointer=False`), not by allocating a separate checkpoint namespace or child thread.

## First-phase Command Room principle

For the first slice, keep AI-AI collaboration records owned by the parent execution:

```text
parent thread_id + parent run_id + task_id
```

That means subagent findings, task events, and action results stay on the parent run timeline. Do **not** introduce `child_thread_id` yet. A later phase may add child conversations, but it must first define migration, checkpoint namespace, permissions, provenance, and display contracts.

## Artifact references

Artifact references remain backward-compatible strings in this slice. Future structured refs should include provenance such as producing thread/run/task/action, artifact type, storage root, and trust boundary, while preserving existing string consumers during migration.

## Explicit non-goals for round one

This contract slice does not change:

- production migrations;
- permission defaults;
- cross-owner sharing semantics;
- artifact root/storage layout;
- external channel trust or authentication rules;
- checkpoint keying or subagent checkpoint isolation;
- frontend runtime behavior.

## Next contract decisions

Before introducing child conversations or cross-run sharing, define the owner model for child thread/run creation, checkpoint namespace strategy, artifact provenance schema, and display rules for AI-AI collaboration surfaces.
