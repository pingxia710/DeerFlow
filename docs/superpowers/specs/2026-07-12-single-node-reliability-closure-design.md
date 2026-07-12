# DeerFlow Single-Node Reliability Closure

## Goal

Make the current single-Gateway DeerFlow deployment reliably deliver the full
lead-agent → subagent → task-event → conversation-recovery loop. The work must
remove known correctness regressions, give the frontend and backend one terminal
task contract, keep multi-conversation recovery bounded, and expose enough
read-only operational data to judge whether subagents create value.

## Scope

This is a high-risk, heavy-mode initiative with five ordered vertical slices:

1. Make task terminal status structured at the producer and cover
   runtime-observed-evidence success in the shared backend/frontend contract.
2. Tighten the single-node run API: stop advertising unsupported `enqueue`, and
   make runtime snapshot recovery bounded without using ordinary reads as a
   general repair worker.
3. Repair the two reproducible frontend regressions: the agent-model page error
   and the completed-subtask card being blocked by the message action overlay.
4. Add a deterministic full-stack golden path that exercises a subagent task,
   terminal task state, conversation switching, and reload recovery together.
5. Add a read-only local value report for run/task outcome, duration, token
   distribution, subagent share, and artifact coverage.

## Design

### Task terminal contract

The backend task execution path is the authority for terminal state. It must
stamp `subagent_status` directly when it creates the ToolMessage rather than
requiring later middleware or frontend code to infer status from prose. The
existing shared fixture remains the compatibility fallback and gains the
runtime-observed-evidence success case. Historical messages without a stamp
continue to use the fallback parser.

### Single-node multi-conversation runtime

Keep one active mutating run per thread and parallel runs across threads. Do not
introduce Redis, a second Gateway worker, a migration, or a shared stream bridge.

The runtime snapshot endpoint remains the recovery projection, but its normal
read path must be bounded and side-effect free. Any stale-run or task-lane repair
needed for compatibility must occur at an explicit lifecycle boundary or via a
narrow, idempotent helper whose writes are observable and tested. Snapshot
message loading must avoid serial per-run fan-out for ordinary chat open/reload.

The public request schema must only advertise concurrency strategies the
RunManager can execute. Existing clients receive a deterministic validation
error rather than a late `501` for `enqueue`.

### Frontend ownership and interaction

Preserve the current ViewScope / execution owner / persisted record identity
model. Fix the two failures at their source: agent model resolution must not
send the route to the generic error page, and a conversation action layer must
not intercept pointer events intended for an open subtask card.

Do not introduce a second global store or rewrite `hooks.ts` wholesale in this
initiative. Only extract or simplify code where a changed path requires it.

### Golden full-stack acceptance

Use the existing deterministic replay/test infrastructure rather than a paid
model. One golden test must prove this sequence:

```text
lead dispatches task → subagent completes with observed evidence
→ durable task terminal event / structured ToolMessage
→ user switches A → B → A → refreshes A
→ A shows terminal subtask and final chat state; B remains isolated
```

The test must exercise the real Gateway and real frontend, while keeping the
model/tool behavior deterministic and local.

### Value report

Add a read-only local command that aggregates existing run, task-lane, and
artifact-provenance data. It reports counts and distributions only; it must not
print prompts, responses, user identifiers, credentials, or artifact content.
The report is an operational decision aid, not billing or a product-quality
score.

## Acceptance Criteria

- A runtime-observed-evidence task success is `completed` in both backend and
  frontend fallback parsing, including reloaded history.
- The public run-create schema no longer claims `enqueue` is supported unless
  the manager actually implements it.
- Normal runtime-snapshot reads do not run broad self-healing writes and do not
  serially fetch one message page for every listed run.
- The two current deterministic browser failures pass, and the full mock browser
  suite is green.
- A real-frontend/real-Gateway deterministic test covers a subagent task plus
  A → B → A switch and reload recovery.
- A value-report command produces sanitized outcome, latency, token, and
  artifact-coverage metrics from a local database.
- Relevant backend/frontend unit, browser, replay, type, lint, format, and diff
  checks pass on the single-node candidate branch.

## Non-Goals

- Multiple Gateway workers, Redis, shared runtime scheduling, or a distributed
  stream bridge.
- Database migration, auth/owner-model changes, or external-service additions.
- Automatic multi-hop role routing or a persistent AI-role workflow.
- A wholesale frontend state-management rewrite.
- Paid live-model validation or production data mutation.

## Risks and Cut Lines

- Snapshot batching may require touching every event-store implementation. If a
  narrow current-run projection cannot meet the acceptance criteria without a
  broad persistence rewrite, retain the existing data model and cut that
  optimization into the next single-node performance task.
- The real-Gateway golden path must not depend on a live LLM. If the existing
  replay model cannot deterministically emit a task call, add the smallest
  test-only model fixture rather than changing production agent behavior.
- Value reporting uses operational counters; accepted human outcome remains a
  later product metric, not something inferred here.
