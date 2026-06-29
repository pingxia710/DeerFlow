# Lightweight Progress Memory

Purpose: translate the practical value of an Obsidian `Progress.md` note into a DeerFlow Command Room continuity convention. This is a small human/new-session readable progress memory for interrupted work. It is not a task board, dashboard, gate, or automatic update system.

## Problem it solves

Long DeerFlow work often spans more than one model context, terminal session, or day. A lightweight progress memory helps with:

- long tasks that are interrupted before completion;
- lost model context after a restart or compacted conversation;
- cross-day continuation when the exact last state is no longer fresh;
- handoff to another human or a new AI session.

The goal is to preserve just enough working state to safely resume, then verify reality from source control, tests, and audit records.

## Minimal fields

A useful progress memory should be short and boring. Prefer these fields only:

- `changed files`: files intentionally touched or expected to contain the current work;
- `last working command`: the most recent useful command, including whether it passed, failed, or was interrupted;
- `current blocker`: the concrete reason progress stopped, if any;
- `next step`: the next safe action for the human or lead AI.

Optional notes may be added only when they reduce recovery risk, such as an important constraint, redline, or command that must not be rerun blindly.

## Relationship to Command Room mechanisms

Progress memory sits above the runtime evidence layer and below a full project plan:

- Progress memory is a lightweight continuation summary for humans and new AI sessions. It is optimized for readability and safe resumption, not completeness.
- `RoundRecord` is the lead AI's round-level working memory: goal, boundary, evidence seen, unresolved risk, and useful signals for the next step.
- `audit` and `action_result` are bottom-layer facts and evidence: commands, files, logs, terminal/tool events, artifacts, statuses, hashes, and compact runtime metadata.
- `roundBrief` is the short model-injected summary derived from conversation and audit signals so the lead AI can continue inside the active Command Room flow.

A progress memory may point to `RoundRecord`, audit entries, test logs, commits, or changed files, but it must not replace them. It is a reading aid, not a source of truth.

## Usage boundaries

Do not turn progress memory into a large system:

- not a task board or backlog;
- not a dashboard;
- not a gate, approval state, or PASS/FAIL mechanism;
- not a replacement for `git status`, diffs, tests, audit, or `action_result` evidence;
- not an automatic update requirement;
- not a place for secrets, credentials, customer data, private prompts, or sensitive raw logs.

Keep entries compact enough that a new session can read them before doing real verification.

## When to use it

Use a lightweight progress memory when continuity risk is high:

- work crosses a day boundary;
- a feature or migration has many dependent edits;
- execution is interrupted multiple times;
- the repo state is difficult to reconstruct from memory;
- another person or AI session may need to resume.

For small single-session documentation or code edits, the git diff, commit message, and normal audit trail are usually enough.

## Resume protocol

When resuming, the lead AI should read the progress memory early, then verify it before acting:

1. Read the progress memory for intended changed files, last command, blocker, and next step.
2. Run `git status --short` and inspect relevant diffs before modifying files.
3. Check tests, logs, audit, or `action_result` records that the memory references.
4. Treat stale or conflicting memory as a signal to investigate, not as authority.
5. Continue only from a verified safe next action.

In short: read it first, but do not blindly trust it.

## Example

```markdown
# Progress

changed files:
- backend/docs/command-room/progress-memory.md

last working command:
- `git diff --check -- backend/docs/command-room/progress-memory.md` passed

current blocker:
- none

next step:
- review wording, stage only this doc, then commit
```

This level of detail is enough for recovery without creating another process layer.
