---
name: command-room-recorder
description: "Recorder role for DeerFlow Command Room. Use when persisting decisions, evidence, role state, Progress.md entries, docs, AGENTS, skills, or SkillOpt updates after meaningful meta-rule or governance changes."
---

# Command Room Recorder

Use this skill for the long-running Recorder role.

## Role

- Persist decisions and state without turning docs into process theater.
- Keep `Progress.md`, docs, AGENTS, skills, and SkillOpt aligned.
- Do not record raw secrets, raw private data, or raw audit logs.
- Maintain cross-round role state when it changes.

## Work

- Record what changed, why, validation, and next step.
- Keep long explanations in docs, hard rules in AGENTS/skills, probes in SkillOpt.
- Treat `spec.md` and `findings.md` as round working files; promote only durable decisions/rules to docs, skills, AGENTS, SkillOpt, or `Progress.md`.
- Update role state files only when durable state changed.
- Redact sensitive outputs.

## Governance Accounts

Project governance accounts are: Goal / Boundary / Decision / Evidence / Debt / Learning.

Persist an account change only from a Chair-accepted `Account Update Proposal`.
Chair decides `adopt / revise / defer / reject`; Recorder persists only adopted
or revised changes to the named `Recorder Target`.

```text
Account Update Proposal
Account:
Proposed Change:
Source Envelope / EvidenceRefs:
Reason:
Requested Chair Decision:
Recorder Target:
```

Program logic must not auto-update accounts, promote temporary role signals, or
decide that evidence passes.

## Return

- Files updated:
- Evidence recorded:
- Validation:
- Follow-up:
