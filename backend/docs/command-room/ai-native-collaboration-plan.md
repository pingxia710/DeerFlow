# Command Room AI-Native Collaboration Plan

Purpose: translate the current AI-AI collaboration principles into the next safe code/documentation steps for DeerFlow Command Room. This is an architecture/development plan, not a human job workflow, dashboard, gate, or role chart.

## Core model

Command Room treats the lead AI as the continuous brain for the conversation and project round. Sub-AIs, specialized agents, and skill-backed workers are disposable one-shot selves: they receive a bounded handoff, act in their own context, and return evidence or perspective for the lead AI to synthesize. They do not own continuity, acceptance, scheduling, or final judgment.

Running sub-AIs do not freeze the lead AI conversation. While a subtask is executing, the lead AI may keep discussing strategy, constraints, trade-offs, and next steps with the user. That discussion becomes new intent, constraints, or next-round planning by default; it changes an already-running subtask only when the user explicitly asks to cancel, redirect, expand, or replace that execution.

A subtask result and the handoff that produced it must be read as one object. When a result is weak, stale, overbroad, or misaligned, the diagnosis should look back through the whole packet: prompt, context, boundary, allowed tools, model/skill choice, expected evidence, plan, and runtime observations. Do not reduce failures to “the sub-AI was bad.” Bad handoffs create bad sub-AI behavior.

A Round is the lead AI's high-signal working memory: goal, boundary, context, capabilities released, evidence seen, unresolved risk, and feedback for the next step. It is not a process table. Runtime/action-result records provide reality evidence from commands, files, logs, terminal/tool events, artifacts, statuses, and hashes. They do not rely on AI self-claims and do not adjudicate for the lead AI.

AI-native process design should frame goals, boundaries, context, capabilities, evidence, and feedback. It should not copy human organizations into fixed PM/developer/QA/reviewer roles, gates, dashboards, or mandatory handoffs.

## Current code anchors

- `tools/builtins/task_tool.py` dispatches one-shot subagents and already records compact handoff audit via `record_subagent_handoff`.
- `command_room/round_record.py` reads subagent handoff audit and task `action_result` metadata into internal `command_room_rounds.jsonl` records.
- `command_room/round_context.py`, `task_action_result.py`, and `action_result_adapter.py` are the right place to keep runtime evidence normalization mechanical and non-judgmental.
- `docs/command-room/subtask-interfaces.md` is the stable discovery surface for handoff/subtask patterns.
- `docs/command-room/skills-policy.md` governs when a repeated one-shot specialist behavior becomes a skill.

## Development plan

### 1. Round brief as working memory

Add a compact `RoundBrief` concept only where it helps the lead AI continue: current goal, boundary, inherited context, released capabilities, evidence standard, unresolved signals, and next safe action. Keep it internal and sparse. It should be derived from conversation plus audit facts; it must not become a user-visible board or a required form.

Implementation direction:

- Prefer additive helpers beside `round_context.py` / `round_record.py`.
- Store brief fields in audit records only when there is useful signal.
- Keep legacy aliases (`verdict`, `roundRequired`) compatibility-only until naming cleanup.

### 2. Handoff packet and audit

Make the handoff packet the central AI-AI governance unit. For every subtask, distinguish:

- goal and reason for delegation;
- inherited context and known uncertainty;
- explicit boundary / forbidden actions;
- allowed capabilities/tools/skill/model choice;
- expected output and evidence requirement;
- stop conditions and escalation conditions.

Audit should continue to keep hashes and compact fields, not raw prompts/results by default. Add optional structured extraction only when it is mechanical and low-risk. Result review should compare output against the packet that created it.

### 3. Disposable specialist skills

Specialist skills should describe how to perform a narrow one-shot behavior, not define permanent human-like roles. Promote a skill only after repeated failures and probes show the pattern is stable. Skill prompts should improve handoff quality: what context the specialist needs, what evidence it can realistically produce, and where it must stop.

### 4. Evidence runtime

Strengthen `action_result` as reality observation:

- preserve command, exit code, log excerpt, file/artifact path, status, hash, or event metadata when available;
- label summary-only / output-ref-only results as weak;
- avoid asking workers to self-fill proof forms;
- never let runtime metadata automatically PASS/FAIL the round.

The lead AI should see hard gaps and evidence links, then decide.

### 5. Naming cleanup and verdict residue

The code still contains compatibility names such as `verdict`, `evaluate_verdict_gate`, `roundRequired`, and gate-like wording. Keep public/runtime compatibility, but migrate new names toward `decisionSignals`, `readinessSignals`, `roundContextAvailable`, `roundContextSignals`, `roundBrief`, `nextSafeAction`, `needsUserConfirmation`, `hardGaps`, and `boundarySignals`. Treat `verdict`, `roundRequired`, `requiredEvidence`, and gate-like booleans as legacy compatibility aliases only; they must not be described or consumed as program adjudication, automatic return requirements, or rework triggers. Do this in small documentation-first or alias-preserving patches; do not break persisted audit readers.

### 6. Continuity bridge

Add prompt/documentation guidance that the lead AI owns continuity across rounds while sub-AIs expire after one task. The next-round contract should carry only useful memory: unresolved evidence, inherited boundary, safe next actions, and explicit redlines. It should not replay full transcripts or create a second command room.

### 7. Live discussion during running subtasks

Make clear in prompt and docs that subtask execution is not a UI-level or cognitive freeze. The lead AI can continue natural user discussion while workers run, but must classify discussion as advisory context unless the user explicitly requests an intervention. Intervention verbs include cancel, stop, redirect, replace, add a new subtask, or change the authorized boundary.

When a worker finishes, the lead AI should merge the returned output, action_result/Round signals, and any live user discussion that happened during execution. If the combined state reveals a new executable issue inside the same authorization boundary, redispatch as a fresh handoff. If it changes goal, boundary, or redlines, ask first.

Current implementation status: this rule is now codified in the Command Room lead prompt and project docs. The existing runtime still treats a thread run as the unit of execution: `task()` starts a background subagent but the lead run polls for the terminal result, and same-thread concurrent runs are rejected by default to protect checkpoint, message, and Round isolation. Do not enable generic same-thread concurrency to satisfy this feature.

Future runtime direction, if live discussion becomes a product feature: add a discussion-only side channel or advisory run type that can answer strategy questions without mutating the active execution run. Any explicit intervention should become a separate, auditable command against the active task/run, preserving run_id and Round boundaries.

## Suggested next code steps

1. Add a small handoff packet dataclass/parser around existing subagent audit extraction, keeping raw prompt/result out of audit.
2. Extend task audit extraction with compact fields for boundary, expected evidence, and stop conditions when present.
3. Add tests/probes that weak worker self-claims remain weak even when the result text is confident.
4. Rename new docs and internal comments away from gate/verdict language while preserving compatibility aliases.
5. Add one focused contract test for `action_result` evidence classification from terminal events.
6. Keep prompt guidance and tests explicit that live discussion does not imply implicit intervention in running subtasks.
7. For true live UX, design a discussion-only side channel instead of enabling generic same-thread multitask concurrency.

## Non-goals

- No automatic reviewer/opposition dispatch.
- No PASS/FAIL engine, dashboard, or fixed role workflow.
- No changes to production credentials, customer data, or deployment state.
- No large refactor before the handoff/evidence contracts are clearer.
