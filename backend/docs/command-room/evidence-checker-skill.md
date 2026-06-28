# Evidence Checker Skill

```yaml
name: evidence-checker
status: candidate
source: command-room-maintainers
last_verified: 2026-06-28
```

## Trigger

Use this optional subtask when a previous round made a claim whose evidence is ambiguous, weak, contradictory, or important enough to verify before the lead AI decides the next step within the user-authorized round boundary.

Do not use it as an always-on reviewer, a default CI gate, an automatic PASS/FAIL judge, or an auto-rework trigger.

## Stable subtask interface

Inputs:

- `goal`: verify specific prior claim/evidence, not the whole project.
- `claim`: exact claim from the prior round.
- `evidence_refs`: cited commands, logs, diffs, paths, artifacts, hashes, or state transitions.
- `boundary`: what must not be changed while checking.
- `evidence_standard`: what would count as reproducible support.

Procedure:

1. Read only the cited sources plus the minimum context needed to understand them.
2. Check whether each claim is bound to reproducible evidence.
3. Label mechanical gaps such as summary-only, tests-passed-alone, output-ref-only, missing command/output/exit code, missing artifact/path, or contradiction.
4. Return findings to the lead AI; do not decide final project quality.

Outputs:

- `supported_claims`: claims with concrete evidence references.
- `weak_or_missing_evidence`: claim-to-gap list.
- `conflicts`: evidence that contradicts the claim.
- `open_questions`: what the lead AI must decide or ask.
- `evidence_refs`: hard references used by the checker.

Evidence requirements:

- Prefer command plus output/exit code, log/artifact/hash/diff/source path/state transition.
- Invalid alone: `tests passed`, `output_ref`, worker self-report, reviewer self-report, unlinked summary.

The checker validates prior claim/evidence for the lead AI. It never replaces the lead AI's verdict, never acts as a default reviewer or gate, and never decides whether a new round is authorized.
