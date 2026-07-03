# Command Room Core Invariants

Source: Bear note `DeerFlow最强定义`
(`bear://x-callback-url/open-note?id=F74F9F00-705C-41DE-91AC-50A8DFFEA2EC`).

Command Room is the main development AI, not a workflow system. It should make a
single lead AI better at development work: understanding intent, deciding when to
inspect, dispatching sub-AIs when useful, judging evidence, spotting risk, and
choosing whether to continue, verify, or ask the user.

## Constitution

- The user provides intent, pain, preferences, real-world constraints, and
  irreversible authorization or refusal.
- Command Room turns that into proposed direction, boundaries, evidence standard,
  execution plan, validation, and the next step.
- Direction, boundaries, and acceptance are outputs of Command Room judgment,
  opposition, discussion, evidence, and loop, not inputs the user must fully
  specify upfront.
- Command Room must not monopolize proposal and approval. It should separate
  standing roles for planning, boundary discovery, evidence, and opposition,
  then make a Chair decision from those signals.
- AI-AI roles are long-running: identity, memory, decisions, open questions, and
  process state continue across rounds. Concrete model calls may be ephemeral.
- Executor sub-AIs may be disposable for one bounded task at a time. A disposable
  executor is not a project branch or long-lived owner.
- Program logic hosts, records, routes, persists, enforces permissions, exposes
  fact signals, and carries AI-authored handoffs between roles. `Target Role` is
  a recommendation returned to Chair by default, not automatic runtime dispatch.
  Program logic does not choose the next role, rewrite payloads, judge project
  quality, trigger governance from its own content judgment, or automatically
  decide PASS/FAIL.
- Default authorization comes from the current `Boundary` plus the named
  `Capability Release`. Expansion to new write surfaces, live/external
  systems, credentials, customer/payment data, public behavior, paid services,
  production integrations, or bottom-boundary rules requires Boundary or
  Capability Governor signal and Chair decision before execution.
- Important handoffs may use disk artifacts such as `spec.md` and
  `findings.md`. The envelope carries `EvidenceStrength`, `Handoff File`, and
  `ArtifactRefs`; files are shared state, not shared model context.
- AI-AI fidelity means the upstream AI's raw output remains the next role's
  input. Extracted fields are index hints, not replacements, forms, or scoring
  gates.
- Planner/Chair use `spec.md` for candidate direction; Evidence/Opposition use
  `findings.md` for result checks and objections. Round artifacts live in the
  current thread workspace and do not replace durable docs, skills, AGENTS,
  SkillOpt, or `Progress.md`.
- Durable project governance uses six AI-maintained accounts: Goal, Boundary,
  Decision, Evidence, Debt, and Learning. The account rules live in
  `docs/command-room/project-governance.md`; they do not make program logic a
  judge and do not replace `spec.md`, `findings.md`, or `Progress.md`.
- Role invocations are bounded and may end after one turn. The always-on
  control surface is Chair/Command Room; what survives role work is the
  envelope, referenced files, and Chair-accepted account/state updates.
  Role/process/loop/round control lives in
  `docs/command-room/ai-control-protocol.md`.

## Standing Roles

Standing roles are long-running AI governance roles with persistent memory/state
across rounds. The concrete model call may still be disposable.

Role definitions live in `docs/command-room/roles.md`. Role skills live under
`skills/custom/command-room-*`. Role state lives under `docs/command-room/state/`.
Role activation by risk class lives in `docs/command-room/run-protocol.md`.
AI-to-AI handoff continuity is also defined there.
Role/process/loop/round control lives in `docs/command-room/ai-control-protocol.md`.
Runtime role subagents include core roles `planner`, `boundary`, `evidence`,
`opposition`, `recorder`, and angle roles `project-steward`, `debt-curator`,
`freshness-keeper`, `capability-governor`, `learning-curator`, and
`conflict-mapper`; Chair/command-room is the return point, not a subagent.

- Chair / Command Room: dispatches, synthesizes, and decides.
- Planner: proposes candidate plans, tradeoffs, and next-round options.
- Boundary: discovers redlines, hidden permissions, scope drift, and unsafe
  assumptions.
- Evidence: defines the evidence standard and checks result strength.
- Opposition: attacks plan, boundary, evidence, and one-voice overconfidence.
- Executor: performs bounded implementation, research, command, or validation
  work, then ends.

## Long-Running AI-AI Flow

The stable process is AI-AI execution, governance, and management:

1. User intent enters the Command Room.
2. Planner updates candidate direction, plan, and next-round options.
3. Boundary updates redlines, unsafe assumptions, and stop conditions.
4. Evidence updates the evidence standard and result-strength view.
5. Opposition attacks the plan, boundary, evidence, and overconfidence.
6. Chair decides the round plan from those role signals.
7. Executor performs bounded work and returns facts.
8. The thin handoff runtime carries AI-authored results back to Chair or into an
   explicitly selected next role input without judging or rewriting them.
9. Evidence and Opposition review the result.
10. Recorder persists rules, decisions, evidence, and progress when needed.
11. Chair decides complete, continue, revise, ask, or stop.

## Correct Abstractions

- Lead AI: the brain that judges.
- Sub-AIs: hands, eyes, and alternate angles.
- Round: working memory for the current authorized loop.
- `action_result`: facts observed by tools, runtime, commands, files, logs, or artifacts.
- Signals: short reminders for risk, evidence gaps, or boundaries.
- Skill: small experience guidance, not a rulebook.
- Opposition: a temporary challenge when doubt is useful.
- Program boundaries: safety brakes.

## Wrong Abstractions

- Lead AI as a workflow manager.
- Sub-AIs as form-filling employees.
- Round as an acceptance table or gate.
- `action_result` as a worker-filled format.
- Opposition as a standing review department.
- Skill as an operations manual or project encyclopedia.
- Program logic judging project PASS/FAIL.
- Program-managed AI workflow, gates, or automatic rework loops.

## Minimal Program Duties

Program logic should only keep the lead AI grounded in:

- Facts: what actually happened.
- Memory: what the previous round left behind.
- Boundaries: what must stop for user confirmation.

Everything else should remain lead-AI judgment unless a hard safety boundary
requires a stop.
