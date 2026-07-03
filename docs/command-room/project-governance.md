# Command Room Project Governance

These six accounts are the durable project governance surface for Command
Room. They are maintained by AI roles and Chair judgment, while program logic
only records, references, routes, and enforces hard permissions.

They are not a program judge. They must not become automatic scoring,
automatic `PASS`/`FAIL`, automatic rework, UI, or a file generator.

## Accounts

| Account | Purpose | Maintainer | Update When | Do Not Update When |
| --- | --- | --- | --- | --- |
| Goal | What the project is trying to become now. | Chair owns; Planner proposes. | The user confirms or Chair adopts a durable direction, objective, or non-goal after a serious round. | A round-only goal hypothesis changes, `spec.md` is still a draft, or the change is just an implementation detail. |
| Boundary | What must not move. | Boundary owns; Chair confirms; Recorder persists. | A durable redline, stop condition, permission limit, safety rule, or non-goal is discovered or confirmed. | A tool fails, a temporary round constraint appears, or a local reversible choice does not change the project boundary. |
| Decision | What has already been decided. | Chair owns; Recorder persists. | A contested direction, rule, architecture choice, or process choice is settled and should not be re-litigated casually. | The point is still a proposal, a worker preference, or an ordinary local choice that can be changed later. |
| Evidence | What counts as enough proof. | Evidence owns; Chair accepts. | A durable acceptance standard, validation command, evidence source, or completion bar changes. | Recording raw command output, per-round findings, or program-generated PASS/FAIL; those belong in `findings.md` or the final result. |
| Debt | What is known but intentionally not handled now. | Opposition, Boundary, and Evidence can raise; Chair decides; Recorder persists. | A known gap, risk, cleanup, or follow-up is explicitly deferred so it does not disappear. | The issue is vague, already fixed, outside the project goal, or just noisy speculation. |
| Learning | What should enter skill, `AGENTS.md`, or SkillOpt. | Recorder owns promotion; Chair approves; roles propose. | A repeated failure, durable workflow lesson, safety rule, or probe-worthy behavior should become reusable guidance. | The note is one-off background, a long history, or unprobed encyclopedia content. |

## Account Update Protocol

Durable account changes require an Account Update Proposal, not a raw role
claim.

```text
Account Update Proposal
Account:
Proposed Change:
Source Envelope / EvidenceRefs:
Reason:
Requested Chair Decision: adopt / revise / defer / reject
Recorder Target:
```

Roles may propose account updates in their AI Handoff Envelope. Chair decides
`adopt`, `revise`, `defer`, or `reject`. Recorder persists only Chair-accepted
changes to docs, skills, `AGENTS.md`, SkillOpt, or `Progress.md` when those
targets are appropriate.

Program logic may store account text, refs, and permissions. It must not
auto-update accounts, promote temporary signals into durable decisions, or
decide that evidence passes.

## Relationship To Other Artifacts

- `spec.md` and `findings.md` are per-round working files. They can reference
  these accounts, but they do not replace them.
- `Progress.md` is the chronological record of meta-rule changes and validation.
  These accounts do not replace it.
- Skills, `AGENTS.md`, and SkillOpt receive only Learning Account items that are
  durable, small, and probe-worthy.
- Program logic may store account text, references, and permissions. It must not
  decide whether AI work is good, whether evidence passes, or whether to rework.
