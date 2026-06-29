# Command Room Core Invariants 1.0

Purpose: define the architectural anchors that keep Command Room AI-native rather than turning it into a workflow product. These invariants are not a new workflow checklist, gate, PASS/FAIL system, or user-visible dashboard.

## Invariants

1. **User boundaries cannot be expanded by AI alone.** The user sets the current goal, authorization boundary, redlines, and whether to continue. AI may execute inside that boundary, but scope expansion requires user confirmation.
2. **Lead-AI judgment cannot be outsourced.** Sub-AIs, workers, skills, opposition, evidence-checkers, Round metadata, or program helpers may provide inputs; the continuous lead AI remains responsible for reading them and deciding the next response/action.
3. **Evidence cannot be replaced by worker self-claims.** A worker saying it is done, passed, or verified is not hard evidence. Claims need observable references such as commands, exit codes, logs, diffs, artifacts, hashes, source lines, or runtime facts.
4. **Round cannot become Jira, dashboard, or gate.** A Round is high-signal working memory for the lead AI, not a user-facing task tracker, fixed form flow, automatic PASS/FAIL engine, or default review pipeline.
5. **Handoff cannot lose boundaries.** Each AI-AI handoff must preserve the goal, current boundary, relevant context, released capabilities, expected outputs/evidence, forbidden changes, and stop/escalation conditions.
6. **Run/thread/task identity cannot be mixed.** Runtime facts, artifacts, audit records, memory, and task results must stay attached to the correct run, thread, task, user/workspace, and sandbox identity.
7. **Skill cannot become encyclopedia, job role, or process.** A skill stays narrow, trigger-routed, failure-driven, evidence-aware, probe-backed, and deletable. Do not convert broad docs, personas, SOPs, or project knowledge bases into active skills.
8. **User experience cannot become a workflow system.** Users should see natural development collaboration and results, not internal gates, dashboards, mandatory forms, reviewer choreography, or process terminology.
9. **Sub-AI returns are not program-level verdicts.** After a sub-AI task returns, the program layer must not automatically intervene, adjudicate, rewrite, filter, send back for rework, or gate based on the sub-AI text. Program/runtime code may record and present factual runtime observations, audit entries, action results, tool outputs, commands, files, logs, artifacts, status, and boundary signals. The lead AI reads the returned result plus observable facts and makes its own judgment.

## Non-goals

- These anchors do not add a runtime gate, default reviewer, PASS/FAIL label, or automatic rework loop.
- They do not change product direction or require frontend/dashboard behavior.
- They are documentation constraints for Command Room architecture and future changes.
