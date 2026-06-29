# Command Room Skills Policy

Purpose: keep DeerFlow/Command Room skills few, narrow, failure-driven, trigger-routed, evidence-aware, probe-backed, and deletable. WorkOS principle: fewer skills, promoted only when repeated failures prove the need; eval/probe results decide keep/change/delete. Skillopt-style probes are behavior-regression evals, not runtime, task ledgers, adjudicators, or gates.

## Readiness/evidence/risk signal ownership

- Command Room is the main development AI, not a workflow system. The user controls direction: the current round goal, boundaries, and whether to enter another round. Within that confirmed round, final execution judgment lives in the continuous lead AI brain, not in disposable one-shot sub-AIs, an always-on reviewer pipeline, or program-level gates.
- A Round is the lead AI's working memory for following project quality; it is not Jira, a user-visible dashboard, a form, or a PASS/FAIL machine. For technical/program-development Command Room collaborations that dispatch subagents/tasks, terminal task `action_result` metadata may be collected into Round/RoundContext signals for the lead AI to inspect before deciding the next step. `action_result` is a runtime/adapter-normalized observation from terminal events, metadata, tool-observed outputs, commands, files, logs, and artifacts; it must not depend on a sub-AI hand-writing a fixed format. Ordinary lightweight chat or no-task conversations are not forced into a blocking Round by default.
- Evidence-checker and opposition are optional small evidence/risk checks for a prior round's claim/evidence when risk, ambiguity, or contradiction warrants it. They do not replace the lead AI's final judgment. Opposition is not a second command room: it does not decide, accept, schedule, or keep long-term accounts. Their stable candidate interfaces live in `evidence-checker-skill.md` and `opposition-skill.md`.
- Handoff packets are the core unit for AI-AI collaboration governance: goal, boundary, inherited context, released capabilities, expected evidence, and stop conditions. A worker result and its handoff belong together; weak results should make us inspect prompt/context/boundary/tool/model/skill choice and evidence request, not simply blame the worker.
- Running workers do not freeze the lead AI's user conversation. The lead AI may continue discussing strategy, constraints, trade-offs, and next moves while workers run; that discussion is advisory context by default and changes a running worker only after explicit user intervention.
- Worker completion can create a new handoff. The lead AI may redispatch when completed worker information plus current user intent reveals a new executable issue inside the same authorization boundary; boundary-changing redispatch needs user confirmation.
- Workflow terms are only internal compression and recheck aids. They do not create fixed roles, gates, dashboards, forms, automatic review loops, automatic acceptance, automatic adjudication, automatic rework, default reviewers, or mandatory default dispatch. Ordinary technical choices inside the current boundary should be handled autonomously rather than escalated to the user.
- The discoverable Command Room subtask index is `subtask-interfaces.md`; it summarizes executor, fact-finder, evidence-checker, opposition, and synthesis-checker boundaries without enabling automatic dispatch.
- Program code should expose hard gaps, evidence/risk/unresolved RoundContext signals, and boundary signals only; it must not auto-judge project quality, auto-trigger rework, or silently dispatch default reviewers/opposition. The lead AI consumes these signals and makes the judgment. Users see natural development results; internals keep only minimal facts, memory, and safety boundaries.
- Human Needed / user confirmation is a redline or authorization-boundary signal, not a label for ordinary small blockers, function names, test-writing choices, commit splits, or implementation details inside an authorized round. Use it for destructive operations, production writes, credentials/secrets, customer data, external sends, payment/security/legal impact, or project-direction changes. Incomplete rounds that only need safe read-only diagnostics, evidence collection, log/file/status inspection, or clarification within the current boundary should remain unresolved / needs follow-up with `next_round_is_safe=True` so AI-AI rounds can continue autonomously. Dangerous follow-up actions such as killing processes, clearing locks, deleting files, or production writes must still stop for human confirmation before execution.

## What qualifies as a skill

Accept a skill only when it is:

- A repeated operational pattern that has failed or regressed at least twice; without repeated failure samples, do not promote it to an active skill.
- Narrow enough to be invoked by a clear trigger.
- Backed by observable outputs, state transitions, or evidence links.
- Testable by probes/evals without relying on agent self-proof.
- Useful across more than one task, repo, or user workflow.
- A stable subtask type whose interface can be narrowed to: goal, inputs, outputs, failure conditions, and evidence requirements.

Do not accept:

- One-off task notes, Jira summaries, meeting minutes, or run logs.
- Broad persona prompts such as “be careful” or “think harder”.
- Hidden policy, credentials, production config, or environment-specific secrets.
- Claims that only the worker can self-certify.
- Automatic quality gates that make the Round/program layer judge success, trigger rework, or require subagents/review subagents to prove completion.
- Content that belongs in architecture docs, runbooks, API references, README material, or project encyclopedias.
- A mechanical conversion of docs into skills without a narrow trigger, repeated failure evidence, and probes.
- Fixed roles, SOPs, dashboards, reviewers, or gates; a skill is a pitfall-avoidance card, not a workflow system.

## Minimal structure

Each skill must contain:

- `name`: stable kebab-case identifier.
- `trigger`: when to apply it.
- `inputs`: required artifacts/context.
- `procedure`: short engineering steps.
- `outputs`: concrete files, diffs, checks, or state updates.
- `evidence`: required proof source; prefer logs, tests, state machine events, commits.
- `probes`: eval cases that must pass.
- `owner`: human reviewer or owning team.
- `status`: `candidate`, `draft`, `active`, `deprecated`, or `merged`.
- `review_after` or `expiry`: when the skill must be rechecked, renewed, merged, or deleted.

## Lifecycle rules

- Candidate/draft is the default state. Promotion to active skill requires human confirmation plus passing probe/eval evidence.
- Architecture decisions embedded in a skill require human confirmation before use as policy.
- Delete/merge/deprecate decisions require human confirmation.
- Keep the catalog small: merge overlapping skills before adding new ones.
- If a skill has no recent trigger, no passing eval, or creates noisy behavior, deprecate it.
- Prefer changing probes before changing prose when behavior is ambiguous.
- Keep quality ownership in the lead AI / Command Room: skills may define critique triggers and evidence checks, but must not encode automatic program-level judgment or rework loops.

## Eval and probe rules

- Every active skill must have at least one probe case.
- Eval decides whether a skill stays; prose alone does not.
- Probes must verify external evidence, not worker self-proof.
- State machine transitions and evidence chains are preferred over natural-language assertions.
- A failed probe opens one of three actions: fix skill, merge/delete skill, or mark product/runtime gap.
- New probes should be minimal and deterministic enough to run in CI or local review.

## SkillOpt probe checklist

Use SkillOpt-style probes as minimal behavior-drift evals for skill policy. They are not runtime, task ledgers, adjudicators, or gates. At minimum, active-skill probes should catch regressions where:

- Worker self-claim is treated as evidence.
- A bare "tests passed" summary is treated as hard evidence.
- Opposition or evidence-checker appears by default instead of on demand.
- A skill becomes a fixed process, role, reviewer, dashboard, or gate.
- Obsidian/progress memory is used as the runtime task ledger.
- Production, credentials/secrets, customer data, or scope expansion proceeds without stopping to ask.

## Evidence standard

Valid evidence must bind each claim to a reproducible reference, such as:

- Command plus output and/or exit code.
- Git diff, file path, artifact path, hash, log, persisted state transition, audit record, or linked source line.
- Human approval record for formal skills, architecture decisions, deletion, and merge.

Weak or invalid evidence includes:

- “tests passed” / “测试通过” without command output, exit code, log, or artifact.
- `output_ref` alone; it is a pointer, not evidence.
- “The worker says it completed.”
- “The reviewer subtask says it passed.”
- Program/Round metadata that lacks an external gap, state transition, log, diff, or linked artifact.
- Unlinked summaries, including natural sub-AI summaries that are not backed by observable terminal metadata, tool output, command output, files, logs, or artifacts.
- Obsidian notes without source references.
- Screenshots or chat snippets without reproducible context.
