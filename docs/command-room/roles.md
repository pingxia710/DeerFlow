# Command Room Roles

Command Room roles are available long-running AI governance identities. Their
identity, memory, decisions, open questions, and process state may persist
across rounds, but they are activated by risk rather than used as mandatory
lanes. A concrete model call may still be ephemeral.

Role skills live under `skills/custom/command-room-*`. Role state templates
live under `docs/command-room/state/`; Chair-accepted runtime role summaries
are owner-scoped thread audit records in `role_state.jsonl`.
Risk-class activation lives in `docs/command-room/run-protocol.md`.
Thin AI-to-AI handoff continuity also lives there.
Role/process/loop/round control lives in `docs/command-room/ai-control-protocol.md`.
For important handoffs, roles should pass `EvidenceStrength`, `Handoff File`,
and `ArtifactRefs` to point at disk artifacts such as `spec.md` or
`findings.md`; files are shared state, not hidden shared context.
The path and ownership contract is in `docs/command-room/run-protocol.md`.

Runtime role subagents are registered as core roles `planner`, `boundary`,
`evidence`, `opposition`, and `recorder`, plus angle roles `project-steward`,
`debt-curator`, `freshness-keeper`, `capability-governor`,
`learning-curator`, and `conflict-mapper`. `Chair`/`command-room` remains the
return point, not a subagent.

## Roles

| Role | Skill | State | Responsibility |
| --- | --- | --- | --- |
| Chair | `command-room-chair` | `state/chair.md` | Synthesize signals and decide continue, revise, execute, verify, ask, or stop. |
| Planner | `command-room-planner` | `state/planner.md` | Propose candidate directions, plans, tradeoffs, and next-round options. |
| Boundary | `command-room-boundary` | `state/boundary.md` | Track redlines, permissions, scope drift, unsafe assumptions, and stop-before conditions. |
| Evidence | `command-room-evidence` | `state/evidence.md` | Define evidence standards and evaluate result strength. |
| Opposition | `command-room-opposition` | `state/opposition.md` | Attack plans, boundaries, evidence, assumptions, and one-voice decisions. |
| Recorder | `command-room-recorder` | `state/recorder.md` | Persist durable decisions, evidence, Progress entries, docs, skills, and probes. |

Angle roles such as Project Steward, Debt Curator, Freshness Keeper,
Capability Governor, Learning Curator, and Conflict Mapper are defined in the
AI control protocol and can be targeted by their runtime names in an envelope.
Capability Governor returns a `Capability Boundary Signal`, not permission:
requested expansion, current boundary/release, narrower release, expansion
risks, stop-before, evidence refs/strength, Chair decision options, recommended
decision, and `Target Role: Chair`.
Chair answers with `Capability Decision`: keep current release, narrow release,
ask user, or stop. Program logic must not choose it.

Round artifact ownership: Planner owns draft `spec.md`; Chair adopts or revises
it; Evidence owns `findings.md` entries with `EvidenceStrength`; Opposition
adds blocking objections there with `EvidenceStrength` when they affect the
Chair decision; Recorder promotes only durable rule/state
changes out of round artifacts.

## Call Shape

For high-impact rounds, Chair should collect the independent Planner, Boundary,
Evidence, and Opposition signals warranted by the risk before deciding. Ordinary
local development may use one implementation lane and focused acceptance
verification. Recorder runs when durable state, docs, skills, AGENTS, SkillOpt,
or `Progress.md` should change.

Program logic may host, record, route, persist, enforce permissions, and expose
fact signals. It must not manage the AI-AI flow, judge project quality, or
automatically trigger governance.

## State Rule

Update role state only when durable cross-round knowledge changes. Do not store
secrets, raw private data, raw audit logs, or noisy turn-by-turn summaries.
Unresolved `Target Role` / next-task suggestions may be recorded in
`pending_handoffs.jsonl` for Chair review, but program logic must not dispatch
them automatically.
