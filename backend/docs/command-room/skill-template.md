# Skill Template

```yaml
name: short-kebab-case-name
status: candidate # candidate | active | deprecated | merged
agent_ref: source-or-maintainer-ref
last_verified: YYYY-MM-DD
```

## Trigger

Use this skill when:

- Concrete condition 1.
- Concrete condition 2.

Do not use when:

- Boundary condition 1.
- Boundary condition 2.

## Inputs

- Required file/path/log/state.
- Required user or system context.
- Stable subtask interface: goal, inputs, outputs, failure conditions, evidence requirements.

## Procedure

1. Check source evidence before acting.
2. Execute the smallest safe change.
3. Record outputs and state transitions.
4. Run the required probe/eval.

## Outputs

- Expected diff, document, test, state update, or handoff artifact.

## Evidence

Required proof:

- Claim-bound command output and/or exit code.
- Test log, artifact path, hash, diff, source path, state transition, commit, or link.

Invalid proof:

- `tests passed` / `测试通过` alone.
- `output_ref` alone.
- Collaborator or checker self-report without external evidence.
- Any automatic Round/program quality, completion, safety, next-action, or rework judgment, regardless of available metadata.

## Probes

- `probe-name`: scenario, captured facts, and the question for an independent review AI.

## Human confirmation required for

- Promotion to `active`.
- Architecture/policy changes.
- Delete, merge, or deprecate.
```
