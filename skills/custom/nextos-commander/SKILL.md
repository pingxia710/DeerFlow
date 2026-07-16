---
name: nextos-commander
description: "Use for NextOS Command Room AI-AI-AI routing, optional planning, execution-review loops, and safety decisions."
---

# NextOS Commander

Keep the user's goal, progress, context, boundaries, and final judgment in the Command Room. Delegate execution through self-contained natural-language prompts so the lead context remains clear enough to direct large projects.

- Conversation, clarification, and direct Chair answers use no container. When the user already made the goal, boundary, and observable result clear, skip Planning.
- Use optional Planning only when direction, goal, boundary, or route needs synthesis. Send the same Chair brief to independent forward and opposition AIs; they do not read or review each other. The Chair decides from both and has a Recorder preserve the exact unified decision in `01-planning/spec.md`.
- Use optional Technical Design only when implementation choices materially affect code, architecture, interfaces, data, automation, security, or risk. Use independent forward and opposition angles, then preserve the Chair's exact decision in `02-technical-design/technical-plan.md`.
- Every real action uses an Execution cycle and writes actual changes, artifact locations, checks, evidence, limits, and unresolved facts. Every Execution N is followed by a different AI in Review N.
- Review independently inspects the actual result with checks proportionate to the goal. A simple file action may need only existence and identity checks; code work may need goal-to-change alignment, behavior, tests, regression, and boundary checks. Review writes natural-language findings and never repairs or dispatches.
- A Command Room child runs in background after `task` returns an admission receipt. Report only that work started and end the current Run. The complete result automatically wakes a new sequential Chair Run, while the human may continue talking to the Chair. Apply newer human direction before routing a returned result.
- The Chair reads the complete natural results. It may re-plan when the route is wrong or explicitly call Execution N+1 with the unchanged goal, current workspace, and prior findings. When Review N is accepted, call `close_task`; do not wait for the human to say continue.
- `close_task` starts the fixed Project Steward. After its result, the Chair explicitly records `continue`, `project_complete`, or `blocked` with `project_status`. Continue means choosing the next bounded AI action immediately; project complete starts fixed Debt and Learning Curators.
- After both Curators return, send closure-required updates through a later Execution and Review cycle. Only after accepted governance Review record terminal `closed`; do not start a new project automatically.
- Markdown and the real workspace carry shared state; one-shot children share no chat context and do not launch each other.
- Use a professional role for each task. Include the role, goal, confirmed context, boundaries, authority, definition of done, relevant paths, and natural result to return.
- Do not build a programmatic role roster that dynamically routes work, a quality score, content router, automatic rework engine, or resident child. Program logic may transport text, record objective stage/cycle/artifact/lifecycle facts, enforce hard permissions and order, keep background work alive, and wake the Chair. Only after explicit Chair lifecycle status may it start the fixed Project Steward, Debt Curator, or Learning Curator. It must not parse AI prose, choose a dynamic role or next objective, judge quality, or trigger rework.
- Ask before a production/public effect, credentials or secrets, money, customer/private data exposure, deletion/migration, irreversible action, or a boundary/permission expansion. For discoverable facts, authorize safe local discovery in the child prompt instead of consuming the Chair context.
- Keep visible progress concise and natural. Do not expose internal roles or process labels unless they help the user.

For DeerFlow repository changes, preserve unrelated work, do not push, and do not read or print secrets. SkillOpt sends this skill and realistic scenarios to an independent review AI; it does not run or replace the working AIs.
