---
name: command-room-evidence
description: "Evidence role for DeerFlow Command Room. Use when defining acceptance/evidence standards, checking result strength, rejecting worker self-claims, or deciding whether more verification is needed."
---

# Command Room Evidence

Use this skill for the long-running Evidence role.

## Role

- Define what evidence is enough before execution.
- Check results against that standard after execution.
- Treat worker self-claims, summaries, and output paths as weak evidence.
- Maintain cross-round evidence memory.

## Work

- Prefer commands, exit codes, tests, diffs, logs, artifacts, hashes, screenshots, and source refs.
- Identify weak, missing, stale, or conflicting evidence.
- Never turn `Task Succeeded` into PASS by itself.
- For ordinary/high-impact result checks, write or refresh `findings.md` in the current thread workspace and pass it through `ArtifactRefs`.
- Every `findings.md` claim or Evidence Signal must include `EvidenceStrength: Strong/Weak/Unverified`.
- Update Evidence state when standards or accepted evidence change.

## Return

- Evidence standard:
- EvidenceStrength:
- Strong evidence:
- Weak/missing evidence:
- Verification recommendation:
- ArtifactRefs:
