# Command Room Obsidian Maintenance

Purpose: maintain an Obsidian knowledge base as curated engineering memory, not as an automatic transcript sink.

## Directory shape

Recommended vault area:

```text
Command Room/
  Skills/
    active/
    candidates/
    deprecated/
  Decisions/
  Probes/
  Incidents/
  Sources/
```

Rules:

- `Skills/active` mirrors formal skills only after human confirmation.
- `Skills/candidates` may contain generated drafts awaiting review.
- `Decisions` stores human-approved architecture/product decisions.
- `Probes` stores eval/probe definitions and results summaries.
- `Incidents` stores failure-driven learnings with linked evidence.
- `Sources` stores pointers to source docs, commits, tickets, logs, or run IDs; do not duplicate secrets.

## Automatic write candidates

Automation may propose notes for:

- Repeated failure patterns that could become candidate skills.
- Probe failures with source links and suggested owner.
- Incident summaries with reproducible evidence.
- Skill deprecation candidates based on failed/stale evals.
- Cross-run evidence indexes, when tied to explicit source IDs.

Automation must not automatically write every run, every turn, or every agent log into Obsidian. Task metadata may be cited only as objective identity, lifecycle, state-transition, or artifact references. A reviewing AI, not program logic, decides whether those facts reveal a gap or boundary concern.

## Human confirmation boundary

Human confirmation is required before:

- Promoting a candidate into a formal skill.
- Recording architecture decisions as accepted policy.
- Deleting, merging, or deprecating formal skills.
- Publishing knowledge that changes team operating rules.
- Storing sensitive customer, production, or security-relevant context.

## Anti-garbage rules

Obsidian is not an AI Jira, chat archive, or log warehouse.

Reject notes that are:

- Raw run transcripts, worker self-reports, reviewer-subtask self-reports, or verbose task logs.
- Auto-generated Jira-style task/review流水线 entries without durable learning and source evidence.
- Duplicate Jira/GitHub issue content without added durable learning.
- Unverified claims without source links.
- Generic advice that cannot be probed.
- Secrets, credentials, production config, or private customer data.

## Source and evidence policy

Every maintained note should include:

- Source links: commit, PR, issue, log path, test run, trace ID, or document path.
- Status: `candidate`, `confirmed`, `deprecated`, or `needs-review`.
- Owner/reviewer.
- Last verified date.

If source evidence cannot be checked, keep the note as `needs-review` or do not write it.
