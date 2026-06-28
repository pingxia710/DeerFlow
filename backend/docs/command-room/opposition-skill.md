# Opposition Skill

```yaml
name: opposition
status: candidate
source: command-room-maintainers
last_verified: 2026-06-28
```

## Trigger

Use this optional subtask when a prior round's plan, claim, or evidence has high ambiguity, hidden trade-offs, known disagreement, safety/boundary risk, or a costly failure mode.

Do not use it as an always-on reviewer pipeline, default review step, program-level quality裁决, or automatic rework trigger.

## Stable subtask interface

Inputs:

- `goal`: challenge a specific prior claim, plan, or evidence chain.
- `claim_or_plan`: exact statement to challenge.
- `evidence_refs`: cited hard references from the prior round.
- `boundary`: allowed critique scope and forbidden changes.
- `risk_focus`: concrete risk, assumption, contradiction, or alternative to test.

Procedure:

1. Restate the claim/plan and its cited evidence.
2. Identify strongest plausible objections, missing assumptions, and boundary risks.
3. Check whether objections are backed by concrete references rather than persona-style skepticism.
4. Return concise challenge points and what evidence would resolve them.

Outputs:

- `objections`: claim-bound objections with references.
- `unsupported_objections`: critique that lacks evidence and should be discounted.
- `risk_signals`: boundary, reversibility, safety, or ambiguity signals.
- `open_questions`: decisions reserved for the lead AI.
- `evidence_refs`: references used by the opposition subtask.

Evidence requirements:

- Use source paths, diffs, command output/exit code, logs, artifacts, hashes, state transitions, or documented constraints.
- Invalid alone: broad “be careful” advice, worker/reviewer self-report, `tests passed`, `output_ref`, unlinked summaries.

Opposition validates and stress-tests a previous round's claim/evidence. It does not make the final verdict and does not force rework.
