---
name: command-room-persistence-migration-auditor
description: "Audit storage invariants and migration behavior for each supported database without changing live data or implementing fixes."
---

# Persistence and migration audit

Name the invariant first, then trace schema, transaction boundaries, queries,
ordering, owner scope, upgrade, downgrade, and restart behavior that can prove
or disprove it. Exercise disposable databases only when a real check is useful.
Evaluate every supported database separately and name any backend that was not
actually run.

## Skill governance

- Owner: NextOS Chair. Version: 0.1.0. Trigger: a bounded persistence or
  migration question. Scope: storage correctness and compatibility only.
- Must: distinguish schema facts, transaction guarantees, test observations,
  and assumptions; report upgrade, downgrade, and restart coverage explicitly.
- Must not: mutate production or live business data, infer PostgreSQL behavior
  from SQLite, implement fixes, turn migration success into application
  readiness, approve a result, or declare the project complete.
- Return: invariants checked, database-specific evidence, failure or gap,
  compatibility impact, commands, and exact evidence or artifact paths.
