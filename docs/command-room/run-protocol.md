# Command Room Run Protocol

The Command Room lead AI keeps the user's goal, progress, context, boundaries,
and final judgment. One-shot sub-AIs receive complete natural-language prompts,
write durable Markdown handoffs, return their complete natural result, and end.

## Activation

- Conversation, clarification, and direct Chair answers use no container.
- If goal, boundary, and observable completion are clear, skip Context and
  Planning.
- Use one explicit lowercase `work_package_id` for independently tracked work.
  It creates a separate `packages/<work_package_id>/` workspace and factual
  receipt ledger.
- Calls without an ID stay in the single legacy package at the thread root.
  They may continue legacy delivery/review, but once legacy Delivery exists a
  new Context, Planning, or Technical Design handoff must provide an explicit
  ID; DeerFlow never guesses a new package ID or merges it into legacy state.
- For unknown, cross-module, or runtime work, use optional Context: parallel
  bounded discovery handoffs first, then a Recorder preserves
  `00-context/context.md` before Planning or Technical Design.
- Use optional Planning when direction or route needs synthesis.
- Use optional Technical Design when implementation choices materially affect
  code, architecture, interfaces, data, automation, security, or risk.
- Every real action uses Execution and must be followed by independent Review.

The resulting paths are:

```text
Chair only
Execution 1 -> Review 1 -> Chair decision
Technical Design -> Execution 1 -> Review 1 -> Chair decision
Planning -> optional Technical Design -> Execution 1 -> Review 1 -> Chair decision
Context -> Planning -> human confirmation -> Execution 1 -> Review 1 -> Chair decision
```

## Optional Angles

Planning and Technical Design each use two independent AIs that start from the
same Chair brief and completed Context snapshot and do not read or review each
other. The forward AI develops the strongest route; the opposition AI exposes
contrary routes, hidden assumptions, boundaries, failure modes, and
alternatives. After both return, the Chair decides and a Recorder preserves that exact decision in
`01-planning/spec.md` or `02-technical-design/technical-plan.md`.

The Chair presents the recorded plan to the human and waits for explicit
confirmation or revision before beginning Execution. Approval is never inferred
from a file or task status.

## Delivery Loop

Execution cycle N works in the real workspace and records actual changes,
evidence, checks, limits, and unresolved facts under
`03-delivery/cycle-NN/execution/`. A different AI then performs Review cycle N,
inspects the actual result with checks proportionate to the goal, and writes
facts, deviations, evidence, and the required corrected state under
`03-delivery/cycle-NN/review/`.

The Chair may admit multiple independent Execution N handoffs in parallel.
Review N starts only after every admitted Execution N handoff in that work
package is terminal. A confirmed package may execute in parallel with Context
or Planning for a separately scoped package, but no package overlaps its own
planning and execution.

The Chair reads the complete natural-language results. It may finish after one
acceptable review, begin a fresh Command Room run for Planning/Technical Design
when the accepted route is wrong, or explicitly call Execution N+1 with the
unchanged goal, current workspace, and prior findings. Review never launches
rework itself, and no fixed number of cycles is required.

Program code records only explicit container/cycle labels, terminal status,
artifact path/hash/size, and whether the assigned artifact changed. It may
reject a wrong-order, wrong-cycle, or missing-artifact handoff. It never reads
Markdown for quality, chooses roles, judges completion, advances the workflow,
or triggers rework.

Stop for user authorization before production/public operations, credentials,
funds, customer data, destructive or irreversible changes, or permission/scope
expansion.
