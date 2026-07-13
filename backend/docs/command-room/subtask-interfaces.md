# Command Room Subtask Interfaces

Purpose: make stable Command Room delegation patterns discoverable without turning them into automatic runtime gates. The user sets the current round goal, boundaries, and whether to continue; the lead AI chooses execution, evidence gathering, and whether any subtask helps within that confirmed round. See `core-invariants.md` for the architectural anchors behind these boundaries.

## Common boundary

- Invoke subtasks only when they add tool access, independent inspection, critique, or synthesis value. Subtasks are execution means, not fixed process steps.
- When several subtasks are independent, clear-bounded, and within the same authorization boundary, dispatch them in parallel in the same response/round up to the operational cap. Do not serialize independent work out of habit.
- Serialize only for true dependencies, likely shared-write conflicts, risk/boundary confirmation, user choices, or cap overflow. For possible write conflicts, prefer parallel read-only discovery first and one synthesized landing action. A single sub-AI is still correct when only one lane has value.
- A subtask does not inherit the lead conversation. Write its prompt as a self-contained one-shot handoff: give it the goal, relevant confirmed context and starting points, authority for ordinary in-scope choices, scope and stop boundaries, an observable definition of done, and the natural result to return. Do not prescribe a tool-by-tool procedure or require a fixed handoff form. Audit may retain only compact extracted fields such as `context`, `requiredInputs`, `expectedOutput`, `releasedCapabilities`, and `stopConditions`, never raw prompts.
- The Command Room Loop is AI-AI collaboration after a worker returns, not a tool-frequency limit inside one worker. Do not attach `LoopDetectionMiddleware` to subagent runtimes. Keep the 1800-second governance timeout, cancellation, model-call, and structural recursion limits; the lead decides whether a later evidence, opposition, review, or execution subtask is useful.
- Running subtasks do not freeze lead-AI conversation. While a worker runs, the lead AI may discuss strategy, constraints, trade-offs, and next steps with the user; treat that discussion as advisory context or next-round planning unless the user explicitly requests cancellation, redirection, replacement, expansion, or another intervention.
- After a worker finishes, the lead AI may combine the returned result, action_result/Round signals, and live user discussion into a fresh handoff. Redispatch is fine when it is a new bounded task inside the current authorization; boundary-changing redispatch needs user confirmation.
- These are prompt/interface hints, not a fixed output-format dependency: a sub-AI may answer naturally. Runtime/adapters should normalize terminal task events, metadata, tool-observed outputs, commands, files, logs, and artifacts into `action_result`/Round signals; `action_result` is not a sub-AI self-filled form.
- Treat a subtask output together with the handoff that produced it. If the result is weak, stale, or misaligned, inspect prompt/context/boundary/tool/model/skill choice, plan, and evidence request before attributing the failure to the worker.
- Treat subtask output as evidence or perspective, not as an automatic verdict, default reviewer, PASS/FAIL gate, or rework trigger. If the only observable result is a natural-language summary or worker self-claim, keep it summary-only / weak and do not promote it to evidence. Historical fields such as `verdict`, `roundRequired`, or `requiredEvidence` may still appear for compatibility; new code and docs should prefer advisory names such as `decisionSignals`, `readinessSignals`, `roundContextSignals`, `roundBrief`, `nextSafeAction`, and `needsUserConfirmation`.
- Do not use these interfaces to auto-dispatch reviewers, auto-trigger rework, create gates/dashboards/forms/automatic review loops, or change `task()` public returns. Users should see natural development results; internals keep only minimal facts, memory, and boundaries.

## Stable types

| Type | Use when | Expected output | Boundary |
| --- | --- | --- | --- |
| `executor` | A bounded implementation, file edit, command run, or mechanical check is needed. | Changed files/artifacts, exact commands, exit codes, logs, diff summary, issues. | Does not expand scope or touch production/secret config without explicit authorization. |
| `fact-finder` | The lead AI needs source-backed facts from files, docs, web pages, logs, or code paths. | Claim-to-source notes with paths/URLs/line refs and uncertainty. | Read-focused; no edits unless explicitly requested. |
| `evidence-checker` | A prior claim has weak, stale, ambiguous, contradictory, or high-stakes evidence. See `evidence-checker-skill.md`. | Supported claims, weak/missing evidence, conflicts, open questions, hard refs. | Optional/on-demand only; does not judge final project quality. |
| `opposition` | A prior plan/claim/evidence chain needs adversarial stress-testing for hidden assumptions, trade-offs, safety, or boundary risk. See `opposition-skill.md`. | Objections with refs, unsupported objections, risk signals, open questions. | Triggered/stateless short check only; not a second command room, not a default reviewer, no decision, acceptance, scheduling, long-term accounting, automatic veto, or rework. |
| `synthesis-checker` | Multiple subtask results conflict or the final answer needs consistency checking before the lead AI replies. | Inconsistencies, missing links, unresolved decisions, concise synthesis risks. | Checks synthesis quality only; final decision remains with lead AI. |

## Discovery links

- Policy and lifecycle: `docs/command-room/skills-policy.md`
- Evidence signal candidate: `docs/command-room/evidence-checker-skill.md`
- Opposition/risk signal candidate: `docs/command-room/opposition-skill.md`
