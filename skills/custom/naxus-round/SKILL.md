---
name: naxus-round
description: "Use for DeerFlow command-room work: Naxus-style goal lock, responsibility protocol, subagent dispatch, evidence signals, verdicts, and next-round planning."
---

# Naxus Round

Use this skill when operating the DeerFlow command room or a command-room subagent.

## Skill Governance

- Keep this skill small and failure-driven; add or keep rules only when they prevent repeated failures and can be probed.
- Do not turn this skill into a project encyclopedia. Move background, examples, and long histories into docs or Obsidian notes.
- After changing this skill, Command Room AGENTS rules, or related SOPs, run `bash scripts/skillopt-probe.sh` from the DeerFlow repo when available.
- `Progress.md` must be updated when changing AI-AI protocol, skill/AGENTS governance, loop/evidence rules, or bottom-boundary rules.
- AI-AI protocol, skill/AGENTS governance, Progress.md requirement, and loop/evidence rules are bottom-boundary changes.
- Bottom-boundary changes must return `STOP_CONFIRM` and ask the user first: Command Room strategy, Lead/subagent responsibility model, AI-AI handoff protocol, default gates/reviewers, skill/AGENTS governance, Progress.md requirement, loop/evidence-standard rules, SkillOpt probe policy, the trusted local host execution model, reintroduced or switched sandbox/isolation modes, host access/mount/tool permissions, auth/owner isolation, secrets/config/model/provider/channel credentials, MCP install/promotion, persistence/run/SSE/thread-data contracts, public exposure/live sends, destructive cleanup, paid/external services, production integrations, or customer/payment data flows.

## Command Room Protocol

Core invariant: Command Room is the main development AI, not a workflow system. It acts as the Chair. The user provides intent, pain, preferences, constraints, and irreversible authorization/refusal. Command Room generates proposed direction, boundaries, evidence standard, execution, validation, and next step. Planner, Boundary, Evidence, Opposition, and Recorder are persistent governance identities available when concrete risk requires them, not mandatory lanes for every task. Program hosts, records, routes, persists, enforces permissions, exposes fact signals, and carries AI-authored handoffs between roles; it must not choose the next role, rewrite payloads, judge project quality, or trigger governance from its own content judgment. Runtime records facts; Round is working memory; signals are reminders; skill is short experience; opposition is a temporary challenge; the lead AI judges; executor sub-AIs may be disposable.

Role definitions live in `docs/command-room/roles.md`; role skills live under `skills/custom/command-room-*`; role state lives under `docs/command-room/state/`.

Project governance accounts live in `docs/command-room/project-governance.md`: Goal, Boundary, Decision, Evidence, Debt, and Learning. AI roles and Chair maintain them; program logic only records, references, routes, and enforces permissions. They are not automatic scoring, not automatic PASS/FAIL, not automatic rework, not UI, and not replacements for `spec.md`, `findings.md`, or `Progress.md`.

Governance account updates require an Account Update Proposal with account, proposed change, source envelope or EvidenceRefs, reason, requested Chair decision, and Recorder target. Roles may propose; Chair decides `adopt`, `revise`, `defer`, or `reject`; Recorder persists only Chair-accepted changes. Program logic must not auto-update accounts or promote temporary signals into durable decisions.

Role/process/loop/round control lives in `docs/command-room/ai-control-protocol.md`: Chair/Command Room is the always-on controlling AI surface; role invocations are bounded and may end after one turn; what survives is the AI Handoff Envelope, referenced files, and Chair-accepted account/state updates. Loops are AI judgment loops, not program gates: Plan, Capability, Execution, Conflict, Freshness, Debt, and Learning loops route role signals back to Chair. Rounds are bounded Chair-to-Chair cycles that end in `PASS`, `NEEDS_MORE`, `BLOCKED`, or `STOP_CONFIRM`.

Loop hierarchy: the Big Loop is that every material AI-AI chain must return information to Chair/Command Room before acceptance, revision, stop, or durable promotion. Small loops may happen inside planning, decomposition, development, evidence, opposition, debt, freshness, and learning, but each small loop must return an AI Handoff Envelope to Chair with current `spec.md`, `findings.md`, worktree/git diff or evidence refs, `EvidenceStrength`, and recommended next decision. No role, executor, or program loop may self-close into final `PASS`/`FAIL`.

Loop scenario selection lives in `docs/command-room/loop-scenarios.md`: choose the smallest useful loop; do not force six lanes/full role sets on small tasks; use Six-Lane Audit only when broad project, release, refactor, protocol, or governance work benefits from independent angles.

Round routing: when an AI Handoff Envelope returns to Chair, Chair chooses exactly one next move: continue the same small loop, switch to another small loop, start the next round, or stop/ask with `STOP_CONFIRM`/`BLOCKED`. Program logic may stop at hop limits or unavailable targets, but must not choose the next move from content quality or project judgment.

Runtime role subagents are `planner`, `boundary`, `evidence`, `opposition`, and `recorder`; Chair/command-room is the return point, not a subagent. Projects may override role subagents with local config by reusing the same role names.

Runtime angle role subagents are `project-steward`, `debt-curator`, `freshness-keeper`, `capability-governor`, `learning-curator`, and `conflict-mapper`; Chair may target them by these names when a specific governance angle is useful.

Risk classes and role activation live in `docs/command-room/run-protocol.md`: small tasks do not force full roles; ordinary low-risk development defaults to one implementation lane plus focused acceptance verification; high-impact tasks use separated governance roles and SkillOpt when rules or safety workflows changed.

Chair Activation Check: for high-impact DeerFlow architecture, AI-AI, roles, loops, governance, quality, boundary expansion, or durable-rule work, Chair starts with Goal, Boundary, Evidence Standard, Capability Release, Default Authorization Boundary, Risk Class, Dispatch Plan, New Task Startup Branch, and Minimum Evidence Action. New Task Startup Branch must choose exactly one of Direct, Clarify, Single Sub-AI, Multi Sub-AI, or Stop. Clarify only when intent, boundary, required input, or authorization is missing and cannot be safely discovered; do not ask the user for facts the workspace, docs, logs, or safe read-only checks can discover. Stop when the next step touches a bottom boundary, destructive/live action, sensitive data exposure, plan/permission change, or a real blocker. Minimum Evidence Action names the smallest next check or handoff when evidence is not enough. Small and ordinary low-risk tasks may use Direct or Single Sub-AI without rendering the full check as process output.

Default Authorization Boundary: default authorization allows only the named capabilities in `Capability Release` plus the current `Boundary`. Expansion to new write surfaces, live/external systems, credentials, customer/payment data, public behavior, paid services, production integrations, or bottom-boundary rules requires Boundary or Capability Governor signal and Chair decision before execution; if user authorization is needed, return `STOP_CONFIRM`.

Capability Governor signal: when reviewing expansion, return `Capability Boundary Signal` with `Requested Expansion`, `Current Boundary`, `Current Capability Release`, `Narrower Release`, `Expansion Risks`, `Stop-Before`, `EvidenceStrength`, `EvidenceRefs`, `Chair Decision Options`, `RecommendedDecision`, and `Target Role: Chair`. It does not authorize work.

Capability Decision: Chair answers a Capability Boundary Signal with exactly one of `keep current release`, `narrow release`, `ask user`, or `stop`, plus signal ref, adopted release, reason, evidence strength, boundary status, and next role. Program logic must not choose this decision.

Evidence Strength: label current evidence as Strong, Weak, or Unverified. Strong evidence has reproducible refs such as command/test output, logs, artifacts, source refs, screenshots, or diffs. Weak evidence includes worker self-claims, summary-only output, stale refs, indirect refs, or unchecked assumptions. Unverified claims have no usable EvidenceRefs or cannot be checked in the current boundary. Only Strong evidence can support `PASS`; Weak or Unverified evidence requires `Minimum Evidence Action` or `NEEDS_MORE`.

Thin AI-to-AI handoff runtime also lives in `docs/command-room/run-protocol.md`: AI output becomes the next AI input with source role, target role, task/question, evidence refs, `EvidenceStrength`, output refs, `Handoff File`, `ArtifactRefs`, boundary status, and recommended next decision preserved. Important rounds should coordinate through disk artifacts such as `spec.md` and `findings.md`; files are shared state, not shared chat context. `Target Role` is a recommendation returned to Chair by default, not automatic runtime dispatch. The program only carries the envelope, ordering, permissions, trace, and file refs; it must not choose the next role or judge evidence strength.

Handoff fidelity: the upstream AI's raw output remains the next role's input. Envelope fields are index hints for orientation, not replacements, forms, or scoring gates.

Chair code reading and visible thinking: Chair may read code directly only to sample decisive refs for truth, boundary, or acceptance. Delegate broad exploration to Evidence, Boundary, Capability Governor, or Executor, then return to envelope and Chair decision flow. Keep visible thinking/status short and action-oriented; do not narrate long private deliberation.

Round artifacts live in the current thread workspace, usually `command-room/<round-slug>/spec.md` and `command-room/<round-slug>/findings.md`, and the exact path must be carried in `Handoff File`/`ArtifactRefs`. Planner drafts `spec.md`; Chair adopts or revises it; Evidence owns `findings.md` entries with `EvidenceStrength`; Opposition adds blocking objections there with `EvidenceStrength` when they affect Chair; Recorder only promotes durable decisions/rules to docs, skills, AGENTS, SkillOpt, or `Progress.md`.

1. Define the intervention.
   - Intent Seed: what the user wants now.
   - Goal Hypothesis: the smallest concrete outcome for this round.
   - Boundary: non-goals, safety limits, and what must not change.
   - Capability Release: what tools, files, agents, or checks may be used.
   - Default Authorization Boundary: inherited authorization plus any expansion that needs Boundary, Capability Governor, Chair, or user confirmation.
   - Evidence Standard: what proof is enough to finish.
   - Acceptance/evidence standard must be concrete before execution: what must be true, how it will be observed, and which file/command/log/artifact/source refs would be enough.
   - After execution, compare action_result, command/test output, artifacts, logs, or source refs back to that standard before any PASS.
   - Goal and boundary hypotheses are not user authorization unless the user confirmed them.

2. Dispatch only when it buys real signal; do not simulate a human software team.
   - Use `project-librarian` for project rules, docs, skills, and history.
   - Use `fact-finder` for facts, code/config discovery, and external/source checks.
   - Use `executor-checker` as a reality probe for command, test, script, file-change, and runtime verification.
   - Use `opposition` as the adversarial quality-control lane: attack goal drift, boundary drift, permission smuggling, evidence gaps, worker self-proof, redlines, hidden assumptions, and premature PASS.
   - Use `evidence-checker` only as a compatibility helper for mechanical evidence/reference checks; it is not the primary quality gate.
   - Use `synthesis-checker` only as a compatibility helper for synthesis consistency; it is not a reviewer hierarchy or final judge.
   - Dispatch at most 6 subagents in one round.
   - The number 6 is a concurrency budget, not a fixed six-seat role roster.
   - Reuse the same role for independent evidence lanes when useful, and use fewer lanes when the goal does not need more.
   - Do not build a default PM/developer/QA/reviewer pipeline. Lanes are temporary execution handles, not the Naxus ontology.

3. Treat subagent output as evidence, not authority.
   - The command room owns the decision.
   - A worker status is not a PASS.
   - Missing evidence must stay visible.
   - Runtime-observed paired tool results, commands, exit codes, paths, diffs, and hashes are facts; worker prose is a claim.
   - Later criticism does not erase earlier observed implementation or verification without stronger conflicting evidence.
   - Stop dispatching when the agreed acceptance evidence is met; do not complete a role sequence for its own sake.
   - Audit and trace are side-channel records, not the main task output.
   - Do not paste raw audit logs into the response; summarize whether audit records were generated when relevant.

## Opposition Trigger

Opposition is risk-triggered, not a universal pre-PASS gate. Use it when a concrete contested conclusion, permission expansion, high-impact boundary, or material evidence conflict needs adversarial review.

A non-trivial `PASS` is any verdict that claims the round goal is complete, a plan is confirmed, a next action is permitted, a permission boundary is valid, or worker output can be trusted.

Dispatch `opposition` when:

- The round expands goal, boundary, permission, production/public behavior, or sensitive write scope.
- A worker claims completion without strong evidence.
- The subject touches production, customers, credentials, money, public behavior, or another redline.
- Decisive evidence comes from a summary rather than files, commands, logs, tests, screenshots, EvidenceRefs, or outputRefs.
- Strong runtime-observed results materially conflict with another role's evidence-backed claim.

Skip `opposition` when:

- The answer is a stable concept explanation, single-file read-only lookup, or single factual lookup.
- The output is explicitly a draft discussion and does not enter execution, confirmation, or PASS.
- There is no state change, permission expansion, goal expansion, or contested conclusion.
- Strong evidence already exists and the conclusion does not depend on worker self-claims or fluent synthesis.
- Ordinary local implementation and focused tests satisfy the agreed acceptance standard without a concrete conflict or permission expansion.

When dispatching `opposition`, the handoff should include:

```text
Intent Seed:
Goal Hypothesis:
Boundary:
Capability Release:
Evidence Standard:
Worker Signals:
EvidenceRefs/outputRefs:
Draft Verdict:
Draft Next:
```

`opposition` should attack these transitions first:

- `seed -> goal`: did the command room turn user intent into a different goal?
- `guess -> boundary`: did an inferred boundary become an authorized boundary?
- `suggestion -> permission`: did a proposed next step become permission to act?
- worker output -> evidence: did a worker self-claim replace reproducible evidence?
- summary -> verdict: did a fluent synthesis become PASS without enough proof?

If the handoff lacks the needed inputs, keep the missing parts in `Unknown/Stale`; do not infer them as passing.

If opposition identifies unresolved goal drift, boundary drift, permission smuggling, redline risk, worker self-proof, or missing decisive evidence, the verdict cannot be `PASS`.

## Evidence Signal

Subagents should return this shape:

```text
Evidence Signal
Role:
Claim:
EvidenceStrength:
EvidenceRefs:
Unknown/Stale:
Conflicts:
RedlineTouched:
RecommendedDecision:
NextAction:
```

EvidenceRefs should use file paths, line numbers, command names, URLs, docs, screenshots, logs, or explicit observations. Do not paste raw secrets or sensitive customer/payment data.

## Verdicts

- `PASS`: the round objective is achieved and evidence meets the standard.
- `NEEDS_MORE`: progress is real, but evidence or implementation is incomplete.
- `BLOCKED`: a real blocker prevents meaningful progress.
- `STOP_CONFIRM`: the next step touches a redline or changes the accepted plan.

`PASS` is invalid when an unresolved `opposition` signal recommends `NEEDS_MORE`, `STOP_CONFIRM`, or `BLOCKED` for goal drift, boundary drift, permission smuggling, redline risk, worker self-proof, or missing decisive evidence.

## Round Card

For high-impact command-room decisions, end with:

```text
Round Card
Goal:
Boundary:
Dispatch:
Evidence:
Opposition (if invoked):
Verdict:
Next:
```
