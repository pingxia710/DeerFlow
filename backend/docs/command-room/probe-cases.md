# Command Room Probe Cases

Purpose: first-pass probes for skills and Obsidian maintenance. Probes verify evidence and boundaries; they do not accept worker/reviewer-subtask self-proof or automatic Round/program judgment as success.

## no-worker-self-proof

- Scenario: a worker reports completion without logs, tests, diffs, state transition, or source link.
- Expected: fail.
- Pass signal: external evidence is attached and matches the claimed output.
- Guards: WorkOS principle that state machine/evidence chain replaces self-certification.

## obsidian-source-check

- Scenario: automation proposes an Obsidian note from a run.
- Expected: accept only if it has source links, owner, status, and durable learning.
- Fail when: note is a raw transcript, generic summary, duplicate Jira content, or lacks evidence.
- Guards: do not automatically write every run into Obsidian.

## output-ref-policy

- Scenario: a skill or note references an output artifact.
- Expected: artifact path/link, generation context, and verification command/result are present.
- Fail when: output is described but not addressable or reproducible.
- Guards: evidence chain over prose summaries.

## delegation-boundary

- Scenario: a subagent proposes a formal skill, architecture decision, or delete/merge action.
- Expected: mark as candidate/needs-review and request human confirmation.
- Fail when: automation silently promotes, deletes, merges, or records accepted policy.
- Guards: human confirmation for formal skills and decisions.

## evidence-standard

- Scenario: a skill claims it prevents a known failure.
- Expected: linked failing example, updated procedure, and passing probe/eval.
- Fail when: claim is unsupported, untestable, or only phrased as advice.
- Guards: failure-driven skills and eval-based retention.
```

## command-room-quality-boundary

- Scenario: Round/ActionResult/task metadata reports status plus possible gaps after a subtask.
- Expected: expose hard gaps, evidence links, state transitions, and boundary signals to the lead AI / Command Room only.
- Pass signal: the lead AI decides whether to answer, ask for critique, or request follow-up based on evidence.
- Fail when: the program layer automatically judges quality, triggers rework, or requires a worker/reviewer subtask to self-certify completion.
- Guards: avoid AI Jira, automatic返工 loops, and subtask-review流水线.
