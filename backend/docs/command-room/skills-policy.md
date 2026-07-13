# Command Room Skills Policy

Purpose: keep DeerFlow/Command Room skills few, narrow, failure-driven, trigger-routed, evidence-aware, probe-backed, and deletable. WorkOS principle: fewer skills, promoted only when repeated failures prove the need; eval/probe results decide keep/change/delete. Skillopt-style probes are behavior-regression evals, not runtime, task ledgers, adjudicators, or gates.

## Readiness/evidence/risk signal ownership

- Command Room is the main development AI, not a workflow system. It keeps the goal, plan, progress, context, boundaries, and final judgment while disposable one-shot professional sub-AIs perform execution, checking, acceptance, and opposition through natural-language prompts.
- A Round is optional factual working memory for the lead AI, not Jira, a user-visible dashboard, a form, or a PASS/FAIL machine. It may preserve explicit AI-authored fields and objective task/event/artifact references. It must not parse natural-language prompts/results or normalize them into evidence, readiness, safety, completion, gap, or next-action signals.
- A different checking/review/acceptance AI examines each worker result, and an independent opposition AI works from the other direction. They return natural-language judgments and end; they do not replace the lead AI's final judgment or become a second command room. Focused guidance lives in `evidence-checker-skill.md` and `opposition-skill.md`.
- The self-contained natural-language prompt is the core AI-AI handoff: include the goal, boundary, confirmed context, authority, expected result/evidence, and stop conditions without requiring a fixed packet or response form. A worker result and its prompt belong together; weak results should make the lead inspect the prompt/context/boundary/model/role and evidence request, not simply blame the worker.
- Running workers do not freeze the lead AI's user conversation. The lead AI may continue discussing strategy, constraints, trade-offs, and next moves while workers run; that discussion is advisory context by default and changes a running worker only after explicit user intervention.
- Worker completion creates a new AI-AI handoff: the lead passes the natural result to a checking AI and an opposition AI, then decides whether further execution is needed. Boundary-changing work still needs user confirmation.
- Professional roles are AI prompt context, not program states. Do not create programmatic gates, dashboards, forms, automatic review loops, automatic acceptance, automatic adjudication, or automatic rework.
- The discoverable Command Room role index is `subtask-interfaces.md`; it describes professional prompt perspectives without enabling programmatic dispatch.
- Program code exposes factual status and explicit AI-authored boundaries only; it must not judge project quality, trigger rework, choose roles, or dispatch reviewers/opposition. The lead AI sends the prompts, consumes the natural results, and makes the judgment.
- Human Needed / user confirmation is a redline or authorization-boundary signal, not a label for ordinary small blockers, function names, test choices, commit splits, or implementation details inside an authorized scope. Use it for destructive operations, production writes, credentials/secrets, customer data, external sends, payment/security/legal impact, or project-direction changes. The lead AI may continue safe in-scope diagnostics and evidence collection through sub-AIs; compatibility fields such as `next_round_is_safe` remain neutral because program code does not make that judgment.

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
- Programmatic role workflows, dashboards, automatic reviewers, or gates; a skill is professional AI guidance, not a workflow engine.

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
- Keep quality ownership in the lead AI / Command Room: skills guide checking and opposition AIs, but must not encode automatic program-level judgment or rework loops.

## Eval and probe rules

- Every active skill must have at least one probe case.
- An independent review AI decides whether observed behavior supports keeping the skill; prose alone does not.
- Runtime probes capture natural results and objective events. They do not turn those facts into a program verdict.
- Give the review AI external evidence, not worker self-proof, and let the Command Room adjudicate its natural-language assessment.
- A negative AI review gives the Command Room three options: fix the skill, propose merge/deletion, or record a product/runtime gap.
- Keep static checks minimal and deterministic, but limit them to factual schema, path, and text-presence regressions.

## SkillOpt probe checklist

Use SkillOpt-style probes to send realistic behavior-drift scenarios to an independent review AI. They are not runtime or task ledgers. The review AI should identify regressions where:

- Worker self-claim is treated as evidence.
- A bare "tests passed" summary is treated as hard evidence.
- A worker result is accepted without another AI checking it or without an independent opposition view.
- A skill becomes a program-controlled process, dashboard, or gate instead of one-shot AI role guidance.
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
