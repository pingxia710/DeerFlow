# Command Room Run Protocol

This protocol decides how much Command Room governance a round needs. It keeps
small work small and forces role separation only when the risk justifies it.
The role/process/loop/round control contract lives in
`docs/command-room/ai-control-protocol.md`.

## Risk Classes

| Class | Use When | Required Shape |
| --- | --- | --- |
| Small | Read-only lookup, stable explanation, one command, or one local check. | Chair handles directly; Evidence may be lightweight. Do not force Planner, Boundary, or Opposition. |
| Ordinary | Code, docs, tests, bug fixes, project investigation, or local reversible changes. | One implementation/executor lane plus the focused verification required by acceptance. Add governance roles only for a concrete risk, conflict, or evidence gap. |
| High-impact | Architecture, AI-AI protocol, AGENTS, skills, SkillOpt, permissions, bottom boundaries, production, public behavior, credentials, customer/payment data, or destructive actions. | Planner, Boundary, Evidence, Opposition, Chair, Executor, Evidence, Opposition, Chair, Recorder. Run SkillOpt when rules, skills, SOPs, or safety-critical automation changed. |

## Chair Activation Check

For high-impact DeerFlow architecture, AI-AI, roles, loops, governance, quality,
boundary expansion, or durable-rule work, Chair starts with:

```text
Goal:
Boundary:
Evidence Standard:
Capability Release:
Default Authorization Boundary:
Risk Class:
Dispatch Plan:
New Task Startup Branch:
Minimum Evidence Action:
```

`New Task Startup Branch` must be exactly one of Direct, Clarify, Single
Sub-AI, Multi Sub-AI, or Stop. `Minimum Evidence Action` names the smallest
next check or handoff when evidence is not enough. Small tasks may use
`Dispatch Plan: none` with a reason. This is Chair self-activation, not
program-owned scheduling.

Use the startup branches this way:

- Direct: Chair can answer or do harmless local grounding without delegation.
- Clarify: user intent, boundary, required input, or authorization is missing
  and cannot be safely discovered.
- Single Sub-AI: one bounded role or executor lane is enough.
- Multi Sub-AI: independent lanes need separated signals or parallel evidence.
- Stop: the next step touches a bottom boundary, destructive/live action,
  sensitive data exposure, plan/permission change, or a real blocker.

Do not use Clarify for facts the current workspace, docs, logs, or safe
read-only checks can discover. Use `Minimum Evidence Action` for that smallest
next check or handoff instead.

## Chair Code Reading And Thinking

Chair may read code directly to sample decisive refs for truth, boundary, or
acceptance. Delegate broad exploration to Evidence, Boundary, Capability
Governor, or Executor, then return to envelope and Chair decision flow.

Visible thinking/status should stay short and action-oriented: name the current
check, then dispatch, ask, stop, or decide. Do not narrate long private
deliberation.

## Default Authorization Boundary

Default authorization is inherited from the current `Boundary` plus the named
items in `Capability Release`. Direct local read-only grounding, bounded
non-destructive local checks, and already-authorized in-boundary edits may
continue inside that boundary.

Expansion to new write surfaces, live/external systems, credentials,
customer/payment data, public behavior, paid services, production integrations,
or bottom-boundary rules requires a Boundary or Capability Governor signal and
a Chair decision before execution. If the expansion needs user authorization,
the round returns `STOP_CONFIRM`.

Program logic may carry the released capabilities and stop at hard permission
limits. It must not infer new authorization from fluent AI output, worker
self-claims, or a successful command.

When Chair asks `capability-governor` to review an expansion, the role returns
a `Capability Boundary Signal` with `Requested Expansion`, `Current Boundary`,
`Current Capability Release`, `Narrower Release`, `Expansion Risks`,
`Stop-Before`, `EvidenceStrength`, `EvidenceRefs`, `Chair Decision Options`,
`RecommendedDecision`, and `Target Role: Chair`.

Chair answers that signal with a `Capability Decision`: `keep current release`,
`narrow release`, `ask user`, or `stop`, plus signal ref, adopted capability
release, reason, evidence strength, boundary status, and next role. Program
logic must not choose this decision.

## Evidence Strength

Every serious Chair decision should label current evidence as exactly one of:

- Strong: reproducible refs such as command/test output, logs, artifacts,
  source refs, screenshots, or diffs.
- Weak: worker self-claims, summary-only output, stale refs, indirect refs, or
  unchecked assumptions.
- Unverified: no usable EvidenceRefs, or the claim cannot be checked in the
  current boundary.

Only Strong evidence can support `PASS`. Weak or Unverified evidence must
produce `Minimum Evidence Action` or a `NEEDS_MORE` decision.

## High-Impact Role Sequence

This sequence is available for high-impact work. It is not the default path for
ordinary local development.

1. Planner proposes candidate direction and tradeoffs.
2. Boundary names redlines, missing authorization, and safe scope.
3. Evidence defines what proof is enough.
4. Opposition attacks plan, boundary, evidence, and overconfidence when required.
5. Chair decides execute, revise, verify, ask, stop, or continue.
6. Executor performs one bounded task and ends.
7. Evidence checks results against the standard.
8. Chair decides PASS, NEEDS_MORE, BLOCKED, or STOP_CONFIRM.
9. Recorder writes durable decisions, state, `Progress.md`, docs, skills, AGENTS, or SkillOpt only when they changed.

For ordinary work, Chair may dispatch one Executor, consume runtime-observed
evidence, run the smallest focused verification, and stop when acceptance is met.
Later role criticism must cite stronger conflicting evidence; it does not erase
observed edits or passing commands.

## Thin Handoff Runtime

DeerFlow needs a thin runtime that guarantees AI-to-AI handoff continuity:

```text
AI output -> next AI input -> next AI output -> next AI input
```

The handoff payload should preserve:

- source role
- target role
- task or question
- evidence refs, evidence strength, and output refs
- handoff file and artifact refs
- boundary status
- recommended next decision

The runtime must pass the upstream AI's raw output forward as the next role's
input. Extracted envelope fields are index hints for orientation, not a
replacement for the raw natural-language signal.

For important AI-AI work, the envelope should point to durable files instead of
trying to share hidden model context. Use `Handoff File` for the current primary
handoff artifact and `ArtifactRefs` for supporting files. A typical mapping is:
Planner/Chair candidate direction in `spec.md`, Executor/Generator changes in
the app and git worktree, and Evidence/Opposition feedback in `findings.md`.
These files are shared state on disk, not shared chat context; the receiving AI
should read the referenced files when it needs them.

## Round Artifact Contract

Use round artifacts only when they reduce context loss. Small read-only tasks do
not need them. Ordinary multi-agent implementation/review rounds may use them;
high-impact AI-AI protocol, AGENTS, skills, permissions, production, credential,
customer/payment, or destructive rounds should use them.

- Location: current thread workspace, usually
  `command-room/<round-slug>/spec.md` and
  `command-room/<round-slug>/findings.md`. The handoff must carry the exact path;
  program logic must not invent one.
- `spec.md`: Planner drafts candidate direction, boundary, evidence standard,
  task split, and open decisions. Chair adopts or revises it before execution.
- `findings.md`: Evidence records checks, command/test/log/source refs, gaps,
  `EvidenceStrength`, and result strength. Opposition records unresolved
  objections there with `EvidenceStrength` when the finding affects the Chair
  decision.
- Executor/Generator changes live in the project worktree and git diff. Do not
  copy implementation details into `spec.md` or `findings.md` unless they are
  needed as evidence refs.
- Recorder persists only durable decisions and rule changes into docs, skills,
  AGENTS, SkillOpt, or `Progress.md`; round artifacts are working files, not
  project history.

The `task` tool should pass these fields, including `EvidenceStrength`, as an `AI Handoff Envelope` in the
receiving subagent prompt and use the same prompt for compact audit extraction.
When a completed subagent output contains an explicit `Target Role`, the runtime
preserves it as the next-role recommendation and returns the envelope to Chair
by default. It must not automatically start the target role unless a future
round explicitly releases that capability and re-checks the boundary first.
Default runtime role subagents are `planner`, `boundary`, `evidence`,
`opposition`, and `recorder`. `Chair`/`command-room` is deliberately not a
subagent; it is the stopping point for final synthesis and decision.
Projects may override these role subagents with local config by reusing the same
role names.

This is still AI-AI flow. The sending AI chooses the next handoff and the
receiving AI judges the payload. Program logic only carries the envelope,
records it, preserves ordering, enforces hard permissions, and exposes facts. It
must not choose the next role, rewrite the payload, judge PASS/FAIL, or trigger
governance from its own content judgment.
