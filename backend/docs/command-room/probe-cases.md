# Command Room Probe Cases

Purpose: scenarios for an independent AI to review skills and Obsidian maintenance behavior. Capture objective events and complete natural results; do not let the probe program turn them into a quality verdict.

## no-worker-self-proof

- Scenario: a worker reports completion without logs, tests, diffs, state transition, or source link.
- Review expectation: identify the unsupported completion claim.
- Evidence to inspect: external evidence is attached and matches the claimed output.
- Guards: WorkOS principle that state machine/evidence chain replaces self-certification.

## obsidian-source-check

- Scenario: automation proposes an Obsidian note from a run.
- Review expectation: accept only if it has source links, owner, status, and durable learning.
- Raise a concern when the note is a raw transcript, generic summary, duplicate Jira content, or lacks evidence.
- Guards: do not automatically write every run into Obsidian.

## output-ref-policy

- Scenario: a skill or note references an output artifact.
- Review expectation: artifact path/link, generation context, and verification command/result are present.
- Raise a concern when output is described but not addressable or reproducible.
- Guards: evidence chain over prose summaries.

## delegation-boundary

- Scenario: a subagent proposes a formal skill, architecture decision, or delete/merge action.
- Review expectation: mark as candidate/needs-review and request human confirmation.
- Raise a concern when automation silently promotes, deletes, merges, or records accepted policy.
- Guards: human confirmation for formal skills and decisions.

## evidence-standard

- Scenario: a skill claims it prevents a known failure.
- Review expectation: linked failing example, updated procedure, and an independent AI review.
- Raise a concern when the claim is unsupported, untestable, or only phrased as advice.
- Guards: failure-driven skills and eval-based retention.

## command-room-quality-boundary

- Scenario: task metadata reports status plus possible gaps after a subtask.
- Review expectation: give exact references and objective state transitions to the review AI; that AI identifies gaps or boundary concerns for the Command Room.
- Decision owner: the lead AI must obtain a different checking AI result and an independent opposition AI result, then decides whether to answer or request further execution from their natural-language findings.
- Raise a concern when the program layer automatically judges quality, triggers rework, or requires a worker/reviewer subtask to self-certify completion.
- Guards: avoid AI Jira, automatic返工 loops, and subtask-review流水线.
