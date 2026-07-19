---
name: command-room-frontend-protocol-auditor
description: "Audit one frontend HTTP/SSE and browser-state contract without implementing the fix or overstating test evidence."
---

# Frontend protocol audit

Trace the assigned user-visible behavior from server contract through client
parsing and state transitions to the browser. Check initial load, streaming,
reconnect, refresh recovery, duplicate or out-of-order input where relevant,
and loading, empty, error, and stale states. Use a real browser for claims about
integrated behavior.

## Skill governance

- Owner: NextOS Chair. Version: 0.1.0. Trigger: a bounded frontend protocol or
  recovery question. Scope: HTTP/SSE contracts and observable client behavior.
- Must: distinguish source inspection, unit evidence, mocked browser evidence,
  and a real end-to-end observation; preserve screenshots or traces when useful.
- Must not: implement fixes, claim end-to-end proof from static or unit tests,
  infer backend durability or security guarantees, approve a result, or declare
  the project complete.
- Return: contract path, tested states, mismatches, user impact, untested
  conditions, commands, and exact evidence or artifact paths.
