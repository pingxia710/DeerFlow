---
name: command-room-evidence
description: "Use for an independent Review handoff after a Command Room execution cycle."
---

# Command Room review helper

Read the accepted goal, optional Context/technical plan, every execution handoff admitted for the current work package and delivery cycle, and the real workspace. Independently choose checks proportionate to the deliverable: a simple file action may need only existence and identity checks; code work needs goal-to-change alignment, behavior, tests, regression, and boundary checks where relevant.

Write natural-language findings to the assigned review artifact: observed facts, evidence, deviations from the goal, required corrected state, correct work that must be preserved, and remaining limits. Worker prose is not proof.

For a fixed-snapshot conclusion, require a reproducible manifest and compare it
at the start and end of the same freeze. Evidence gathered before a later
worktree drift cannot support the final conclusion.

For a dynamic/E3 conclusion, preserve hard assertions for the claimed
invariant, the exact invocation, exit status, and harness source hash. A script
that only prints values or proves reachability is narrow evidence, not proof of
the claimed invariant. For SSE replay, assert event IDs are ordered with no
duplicates or omissions, including the replay suffix after the cursor.

## Minimum review record

Record the accepted scope, input artifact identities, snapshot ID and capture
time when applicable, verified and unverified claims, E1/E2/E3 level for each
claim, commands with exit status, and the limits on conclusion extension.
Review input completeness, actual change identity, implementation behavior,
contract/state transitions, evidence freshness, runtime/safety boundaries, and
regression before reaching a judgment.

Use one explicit outcome: pass, conditional pass, rework, or blocked. State
whether merge is allowed, disallowed, or allowed only after named conditions.
The reviewer authors this judgment; do not turn the record into a programmatic
score or dispatch rule.

## Snapshot and evidence freshness

For a conclusion about a worktree sampled at recorded UTC times, require an
enforcing pre/post gate around the claimed work. It must compare the declared
expected hashes for porcelain,
tracked patch, staged patch, untracked-path list, and regular-file manifest;
return nonzero on a mismatch; and preserve raw inputs, command, exit status,
UTC timestamps, and non-secret environment identity. Take at least two samples
across a declared observation window. Drift stops the affected lane and makes
earlier evidence historical until a new freeze is accepted.

Use deterministic UTF-8 path records, a declared sort order, fixed field
separators and final newline, per-component SHA-256 values, and a separately
recorded aggregate hash. Reject noncanonical or lossy path conversion instead
of silently normalizing it. Classify harness/setup failure separately from a
product failure; an exit code alone does not establish product causation.

Do not repair the result, dictate implementation when the required state is enough, make the Chair's final decision, choose the next AI, or trigger rework. Return the same complete findings and end.
