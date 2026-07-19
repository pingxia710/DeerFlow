# DeerFlow Backend Instructions

Inherit the repository Goal Lock and frozen program boundary. Read
[`docs/agent-development-reference.md`](docs/agent-development-reference.md)
when changing transport, process, persistence, or API implementation details.

## Goal Lock

- `packages/harness/deerflow/` owns reusable runtime code and must not import
  `app.*`; `app/` owns Gateway, auth, persistence, channels, and API wiring.
- NextOS is the AI-organization layer on DeerFlow. Preserve the internal
  `command-room` identifier, `deerflow.*` namespace, and `nextos-commander`
  Skill unless the user confirms an architecture change.
- `task()` transports one complete natural-language prompt to one
  `codex exec --ephemeral` process and returns its complete result. A role is
  prompt context only; it grants no tools or program authority.
- Command Room children wake a sequential Chair Run with the complete result.
  Programs enforce hard system boundaries and record facts only; they never
  interpret, choose, stage, judge, rework, or close AI work.
- Human input establishes the Goal Mandate: interest, direction, non-goals,
  real-world permissions, and return-to-human boundaries. Within it, a new
  substantive execution plan or material revision uses one Planner proposal →
  Opposition challenge → Chair execution plan → human discussion and
  natural-language confirmation → plan-directed execution. Work already covered
  by a confirmed plan continues directly, including ordinary fixes, stopping
  low-value work, and bounded optimizations. A completed phase
  that changes the next route uses Project Manager proposal → Opposition →
  Chair next-stage plan → human discussion. Independent execution can run in
  parallel; six slots are capacity, not a quota, and independently separable
  professional domains should not be packed into one over-broad child. Programs
  do not enforce this sequence. Execution results are facts for Chair
  continuation, not task acceptance or a required verifier handoff.

## Hard Boundaries

- Use injected `AppConfig` when a request/runtime snapshot exists. Keep HTTP,
  database, auth, and channel dependencies out of the harness.
- Preserve timeout/cancellation, process cleanup, auth, credential filtering,
  owner/path isolation, CSRF/cookie/SSE, idempotency, and migration history.
- Never pass database, channel, business, customer, payment, or unrelated host
  secrets to child processes. Never print or commit credentials.
- Stop before changing AI-AI architecture, auth, storage, network, host access,
  credential policy, production behavior, or persisted contracts.

## Validation

Use Python 3.12+, existing Pydantic/dataclass patterns, and focused tests first.

```bash
cd backend
uv run pytest -q tests/test_task_tool_core_logic.py tests/test_command_room_goal_first.py
uv run pytest -q tests/test_command_room*.py
make lint
make format
```

Run broader tests proportionate to the change. When AI-AI rules, role prompts,
Skills, or `AGENTS.md` change, update root `Progress.md` and run prompt,
role-package, task-transport, and background-wakeup checks.
