# Command Room AI Control Protocol

This protocol explains how roles, process, loops, and rounds fit together
without turning program logic into a judge.

## Core Model

- Command Room / Chair is the always-on controlling AI surface.
- Roles are invoked for one bounded turn, then end.
- What survives a role invocation is its AI Handoff Envelope, referenced files,
  and any Chair-accepted account or state update.
- Program logic carries envelopes, records refs, enforces permissions, and stops
  at mechanical limits. It must not judge quality, choose goals, or decide
  `PASS`/`FAIL`.

## Roles

Use roles as angles, not employees. A role is valid when it has one question,
one output, and no final authority.

| Role Angle | Question | Output |
| --- | --- | --- |
| Planner | What candidate direction or task split should be tried? | `spec.md` update or plan envelope |
| Boundary | What must not move or needs confirmation? | boundary signal |
| Evidence | What proof is enough, and is current proof strong? | `findings.md` update or evidence signal |
| Opposition | What is wrong, missing, circular, or overconfident? | objection signal |
| Project Steward | What stage is the project in and what should wait? | priority/sequence signal |
| Debt Curator | What known gap is deferred instead of fixed now? | Debt Account update proposal |
| Freshness Keeper | Which old fact, rule, or decision may be stale? | revalidation signal |
| Capability Governor | Are released tools, paths, writes, models, or external access too broad? | capability boundary signal |
| Learning Curator | What should become skill, AGENTS, SkillOpt, or nothing? | Learning Account proposal |
| Conflict Mapper | Which role outputs conflict and need Chair resolution? | conflict map |
| Recorder | What durable state/rule/progress should be persisted? | docs/state/Progress update proposal |

Only Chair decides. Non-Chair roles return signals and recommended next target.
Runtime role subagents are registered for these angles as
`project-steward`, `debt-curator`, `freshness-keeper`,
`capability-governor`, `learning-curator`, and `conflict-mapper`; local project
config may override them by reusing the same names.

## Process

Process is the ordered passing of envelopes:

```text
Chair intent -> role signal -> envelope -> Chair decision -> optional next role
```

The sending AI names `Target Role` as a recommendation. Runtime preserves that
signal and returns to Chair by default; Chair decides whether any next role
actually runs.
The upstream AI's raw output remains the next role's input; extracted fields
are index hints, not a replacement or scoring checklist.

Before acting on DeerFlow architecture, AI-AI, roles, loops, governance,
quality, boundary, development execution, or durable-rule work, Chair performs
a Chair Activation Check: Goal, Boundary, Evidence Standard, Capability
Release, Default Authorization Boundary, Risk Class, Dispatch Plan, New Task
Startup Branch, and Minimum Evidence Action. The startup branch must be one of
Direct, Clarify, Single Sub-AI, Multi Sub-AI, or Stop. Clarify only when intent,
boundary, required input, or authorization is missing and cannot be safely
discovered. Stop when the next step touches a bottom boundary,
destructive/live action, sensitive data exposure, plan/permission change, or a
real blocker. Default authorization allows only the named capabilities in
Capability Release plus the current Boundary. Expansion to new write surfaces,
live/external systems, credentials, customer/payment data, public behavior,
paid services, production integrations, or bottom-boundary rules requires a
Boundary or Capability Governor signal and Chair decision before execution.
Small tasks may set `Dispatch Plan: none` with a reason. Program logic does
not perform this check or own scheduling.

Evidence Standard includes an evidence-strength label: Strong, Weak, or
Unverified. Strong means reproducible refs such as command/test output, logs,
artifacts, source refs, screenshots, or diffs. Weak means worker self-claims,
summary-only output, stale refs, indirect refs, or unchecked assumptions.
Unverified claims have no usable EvidenceRefs or cannot be checked in the
current boundary. Only Strong evidence can support `PASS`.

Chair may read code directly to sample decisive refs for truth, boundary, or
acceptance. Broad exploration belongs to Evidence, Boundary, Capability
Governor, or Executor, and Chair returns to envelope and decision flow after
reading. Visible thinking/status should be brief and action-oriented; do not
narrate long private deliberation.

## Loop Hierarchy

The Big Loop is invariant: every material AI-AI chain must return information
to Chair / Command Room before it can be accepted, revised, stopped, or
promoted into durable state.

Small loops may run inside a round, but they are local control loops, not final
judges:

- Planning small loop: Planner, Boundary, Evidence, and Opposition refine the
  candidate direction. The working state is `spec.md`.
- Decomposition small loop: Planner or Project Steward splits a development
  task into bounded envelopes. The split is recorded in `spec.md` or a named
  handoff file.
- Development small loop: an executor produces a worktree/git diff, command
  output, logs, or artifacts; Evidence and Opposition check those refs.
- Evidence small loop: Evidence and Opposition update or challenge
  `findings.md` with `EvidenceStrength` until the gap, conflict, or proof
  standard is visible.
- Governance small loops: Capability, Freshness, Debt, and Learning roles send
  boundary, stale-fact, debt, or skill/SkillOpt proposals back to Chair.

All Small loops must return to Chair / Command Room with an AI Handoff Envelope
that includes current `spec.md`, `findings.md`, worktree/git diff or evidence
refs, `EvidenceStrength`, and a recommended next decision. No role, executor,
or program loop may self-close into final `PASS`/`FAIL`.

## Round Routing

When an AI Handoff Envelope returns to Chair, Chair chooses exactly one next
move:

| Next Move | Use When |
| --- | --- |
| Continue the same small loop | The same question still needs a bounded follow-up and boundaries are unchanged. |
| Switch to another small loop | The envelope exposes a different control question, such as capability, evidence, conflict, freshness, debt, or learning. |
| Start the next round | Chair accepts the result and a new goal hypothesis or task is now needed. |
| Stop/ask | The next step touches `STOP_CONFIRM`, remains `BLOCKED`, hits hop/unavailable-target limits, or needs user authorization. |

Program logic may stop at mechanical limits such as hop count or unavailable
targets. It must not choose the next move from content quality, confidence,
or project judgment.

## Loops

Loops control result quality. They are AI judgment loops, not program gates.
Use `docs/command-room/loop-scenarios.md` to choose the smallest useful loop;
do not force six lanes unless independent audit angles buy real signal.

| Loop | Use When | Typical Path | Stop When |
| --- | --- | --- | --- |
| Plan Loop | Direction, task split, or tradeoff is unclear. | Planner -> Boundary -> Evidence -> Opposition -> Chair | Chair has an executable `spec.md` or asks/stops. |
| Capability Loop | Tools, paths, writes, models, external access, or data scope may be too broad. | Capability Governor returns `Capability Boundary Signal` -> Boundary -> Chair returns `Capability Decision` | Chair keeps current release, narrows release, asks user, or stops. |
| Execution Loop | A bounded implementation/research/check must happen. | Chair -> Executor -> Evidence -> Opposition -> Chair | Evidence is strong enough, or Chair revises/stops. |
| Conflict Loop | Role outputs disagree. | Conflict Mapper -> relevant role(s) -> Chair | Chair resolves, revises, or asks. |
| Freshness Loop | A relied-on rule, fact, dependency, or decision may be stale. | Freshness Keeper -> Evidence -> Chair | Fact is refreshed or marked Unknown/Stale. |
| Debt Loop | A known gap should not disappear. | Debt Curator -> Chair -> Recorder | Debt is fixed, deferred, or rejected as noise. |
| Learning Loop | A repeated failure or durable rule should be retained. | Learning Curator -> Recorder -> SkillOpt -> Chair | Guidance is promoted or discarded. |

## Rounds

A round is one bounded control cycle from Chair intent to Chair decision.

Each serious round should leave:

- intent and goal hypothesis
- boundary and capability release
- `spec.md` when context would otherwise be lost
- role envelopes and referenced evidence
- `findings.md` when results are checked or challenged
- Chair decision: `PASS`, `NEEDS_MORE`, `BLOCKED`, or `STOP_CONFIRM`
- optional account/state updates accepted by Chair

Rounds do not need UI. Their durable value is the envelopes, files, account
updates, and final Chair decision.
