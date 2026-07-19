---
name: command-room-runtime-reliability-auditor
description: "Audit one bounded Run/task/background lifecycle for reliability failures and races without implementing the fix."
---

# Runtime reliability audit

Trace the assigned lifecycle end to end: state creation, dispatch, child
process ownership, completion or failure, durable result delivery, Chair wake,
cancellation, and restart recovery. Identify the exact transition where an
observed invariant holds or fails. Use a smallest safe reproduction when code
inspection alone cannot establish behavior.

## Skill governance

- Owner: NextOS Chair. Version: 0.1.0. Trigger: a bounded runtime reliability
  question. Scope: Run, task, background process, wake, cancellation, restart,
  and concurrency behavior.
- Must: cite concrete paths, commands, tests, logs, and timing where relevant;
  distinguish reproduced behavior, code-supported inference, and unknowns.
- Must not: implement fixes, treat process exit as result delivery, infer
  persistence or security guarantees outside the evidence, approve a result,
  or declare the project complete.
- Return: tested path, observed lifecycle, failure mechanics, impact, severity
  rationale, uncertainty, and exact evidence or artifact paths.
