# Command Room Fact Finder Role Evidence (2026-07-06)

## Changed files

- `backend/packages/harness/deerflow/subagents/builtins/command_room_roles.py`
  - Added built-in `fact-finder` role with skill `command-room-fact-finder`, role label `Fact Finder`, and a read-only fact-gathering description.
- `backend/packages/harness/deerflow/subagents/registry.py`
  - Added `fact-finder` to `_CUSTOM_OVERRIDABLE_BUILTINS` so local `custom_agents.fact-finder` can override the built-in role.
- `skills/custom/command-room-fact-finder/SKILL.md`
  - Added concise Fact Finder skill: read-only, short-lived, narrow questions; source priority; facts/inferences/conflicts/unknowns/next clue; reproducible sources; no code edits, decisions, gates, privilege expansion, anonymous Feishu/Lark access, secrets, or browser automation unless explicitly authorized.
- `backend/tests/test_subagent_skills_config.py`
  - Added `fact-finder -> command-room-fact-finder` to built-in Command Room role coverage.
  - Added skill-content assertions for read-only sources, facts/inferences/conflicts/unknowns, and Chair/main AI decision boundary.
  - Changed custom override coverage to verify `custom_agents.fact-finder` overrides the built-in.
- `backend/tests/test_task_tool_core_logic.py`
  - Added `fact-finder` as an advisory-only Target Role.
  - Added real registry smoke for `subagent_type="fact-finder"`, asserting executor config `("fact-finder", ["command-room-fact-finder"])` and `task_started.subagent_type == "fact-finder"`.

## Behavior

- `fact-finder` is a native built-in Command Room subagent role.
- It is evidence input only: it gathers facts, source refs, conflicts, unknowns, and next clues.
- It does not decide, approve, gate, dispatch, write code, edit files, or expand access.
- `Target Role: fact-finder` remains advisory in task-tool output and does not cause automatic redispatch.

## Validation

- `cd backend && uv run pytest tests/test_subagent_skills_config.py tests/test_task_tool_core_logic.py -q`
  - Result: `98 passed, 1 warning in 2.37s`
- `cd backend && uv run ruff check packages/harness/deerflow/subagents tests/test_subagent_skills_config.py tests/test_task_tool_core_logic.py`
  - Result: `All checks passed!`
- `cd backend && uv run ruff format --check packages/harness/deerflow/subagents tests/test_subagent_skills_config.py tests/test_task_tool_core_logic.py`
  - Result: `13 files already formatted`
- `git diff --check`
  - Result: passed with no output.

## Not done / boundaries

- Did not push.
- Did not read `.env`, secrets, tokens, or credentials.
- Did not perform production writes or external publication.
- Did not add an external researcher dependency.
- Did not mix the `codex/runtime-snapshot-self-heal-detail-telemetry` P1.5 work into this role change.
