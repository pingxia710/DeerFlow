# DeerFlow Backend Instructions

This guide applies under `backend/` and inherits the repository contract,
including the frozen `What is DeerFlow` definition. Keep this file concise so
the complete instruction chain remains under 32 KiB.

## Scope And Boundaries

The backend has two Python ownership layers:

- `packages/harness/deerflow/`: reusable `deerflow.*` agent, config, tool,
  sandbox, capability, Command Room, and runtime code. It must not import
  `app.*`.
- `app/`: FastAPI Gateway, authentication, persistence, channels, API routers,
  run management, and startup/shutdown wiring. It may import `deerflow.*`.

Key locations:

```text
packages/harness/deerflow/agents/lead_agent/   lead graph and prompt
packages/harness/deerflow/tools/builtins/      built-in tools, including task
packages/harness/deerflow/subagents/           one-shot Codex transport/roles
packages/harness/deerflow/runtime/             run execution and streaming
packages/harness/deerflow/command_room/        factual AI-authored records
packages/harness/deerflow/config/              config schemas and resolution
app/gateway/                                   REST/SSE APIs and services
app/persistence/                               database models/repositories
tests/                                         backend tests
```

Keep HTTP, database, authentication, and channel dependencies out of the
harness. Put shared behavior in the harness and adapt it at the Gateway edge.
Use injected `AppConfig` in runtime/request paths; do not silently fall back to
ambient global config when an explicit snapshot exists.

## One-Shot Task Contract

`task()` is transport between intelligent agents, not a program-controlled
agent loop:

1. The lead AI chooses a professional role and writes one self-contained
   natural-language handoff with goal, confirmed context, boundaries, allowed
   work, starting paths, definition of done, and requested natural result.
2. DeerFlow resolves developer-authored role context in this order:
   `system_prompt`, role `description`, then the general-purpose fallback. The
   role is prompt context only; it grants no DeerFlow tools.
3. DeerFlow adds the task and applicable path/`AGENTS.md` orientation once, then
   sends that same audited prompt on stdin to one `codex exec --ephemeral`
   process. Codex owns its plan, native tool use, checks, and response.
4. On success, ordinary agents receive the complete final Codex text as the
   `ToolMessage` result. Command Room receives an immediate background receipt;
   Gateway preserves the terminal task event and starts a new sequential Chair
   Run with the complete result. No prose is parsed, scored, or truncated.
5. Command Room may dispatch a bounded task with only `description`, `prompt`,
   and `subagent_type`. `work_package_id`, `container`,
   `container_artifact`, and `delivery_cycle_index` are optional factual labels
   for display and optional Markdown routing. An explicit package ID creates an
   isolated `packages/<work_package_id>/` namespace; program code never infers
   one. Labels never authorize, block, sequence, or choose a task.
6. Context, Planning, Technical Design, Execution, and Review artifacts are
   optional shared AI state, not stages. The Chair may issue independent tasks
   in parallel regardless of labels and may use artifacts in any order. A plan
   is discussion context, never approval or an Execution gate. When the Chair
   chooses a Review, it performs only the smallest targeted landing check,
   records exact facts and gaps, and stops. It does not implement, repair,
   refactor, broaden scope, or run an unrequested full suite. The Chair alone
   reads the natural result and chooses the next task or stop.
7. `close_task` deterministically starts Project Steward only after explicit
   Chair acceptance. The Chair then emits `continue`, `project_complete`, or
   `blocked`; only explicit `project_complete` starts fixed Debt and Learning
   Curators. Their updates still require a later Execution → Review and explicit
   terminal `closed`.
8. A failed Planning or Technical Design artifact handoff may be retried with a new task.
   If its assigned Markdown changed before transport failure, only the Chair
   may inspect and explicitly accept it with `accept_handoff`; the program
   records hashes/status but does not judge completeness or quality.

The transport may emit `task_started` and exactly one terminal factual event
(`task_completed`, `task_failed`, `task_timed_out`, or `task_cancelled`). Do not
reintroduce `task_running` heartbeats, child turns, broad graph nodes,
resident workers, structured handoff packets, program-generated plans, or
programmatic checking/rework. Short frontend snapshot polling while background
work or wakeup is pending is transport observability, not child polling.
Optional labels may be checked only for field/path shape, task identity, and
concurrent writes to one explicit artifact. The fixed close lifecycle is separate.

A single lead model response can contain at most six task calls. This limit is
enforced at the response boundary; it is not a global queue or concurrency
scheduler. Independent child processes may run concurrently.

## Child Configuration And Process Boundary

The current default contract is:

```yaml
config_version: 18
subagents:
  model: gpt-5.6-terra
  reasoning_effort: xhigh
  timeout_seconds: 3600
```

When the Chair explicitly labels a task `review`, it has a narrower hard ceiling
of 900 seconds. This factual label applies only the landing-check boundary;
ordinary delegated work keeps the configured 3600-second timeout.

- Use the exact Codex effort name `xhigh`; do not rename it to “Extra high” in
  config or assume a provider-specific alias.
- `subagents.model` is explicit and independent from the lead model. Legacy
  execution fields and `model: inherit` are ignored with a warning rather than
  reviving the old runtime.
- The parent run no-progress watchdog must remain longer than the 60-minute
  child timeout (currently 65 minutes).
- Invoke the pinned Codex CLI, write the final response to a private temporary
  file, keep diagnostic logs bounded, and terminate the process group on normal
  exit, timeout, cancellation, or failure.
- Pass core runtime variables and explicit Codex authentication variables to
  the Codex process. Configure Codex shell environment policy so child shell
  commands do not receive credentials or unrelated host variables. Never pass
  database, channel, business, customer, payment, or arbitrary provider secrets.
- `cwd`, allowed add-directories, and sandbox mode are hard transport
  boundaries. This local project intentionally permits trusted host paths; do
  not switch it to a new isolation architecture without user confirmation.

The Codex CLI and a valid `codex login` or supported Codex authentication
environment are runtime requirements whenever the lead may call `task()`, even
when the lead model uses another provider. Docker images install the pinned CLI,
but user authentication is not baked into an image and must be supplied at
runtime through an intentional local overlay or supported environment.

## Command Room Records

Command Room modules may preserve AI-authored handoffs, decisions, risks,
conflicts, questions, recommendations, and evidence references exactly as
submitted. They may calculate only objective metadata such as IDs, ownership,
timestamps, explicit statuses, source kinds, hashes, byte counts, and counts.

They must not parse natural-language prompts/results to derive fields; infer
evidence strength, correctness, safety, verdicts, gaps, warnings, or next
actions; or dynamically dispatch an AI. After explicit Chair lifecycle status,
Gateway may dispatch only fixed Project Steward, Debt Curator, and Learning
Curator roles and wake the Chair after terminal work. Gateway may retry a
failed sequential Chair wake a bounded number of times and include sibling task
IDs, roles, and statuses as factual context. The handoff transport may derive only explicit
container/cycle labels, terminal status, artifact path/hash/size, and
whether the assigned artifact differs from its pre-dispatch content. It may
validate label/path shape and prevent duplicate task identity or concurrent
writes to one explicit artifact, but it must not enforce stage order or decide
whether the artifact is adequate. Compatibility fields such as
`quality_verdict`, `next_round_is_safe`, and `auto_rework` stay neutral. API
read models must distinguish explicit AI-authored content from factual program
metadata. The lead AI, checker AI, and opposition AI perform judgment.

## Runtime, Persistence, And Ownership

- Gateway runs use `RunManager`, `run_agent()`, and `StreamBridge`; Nginx maps
  `/api/langgraph/*` to Gateway-native `/api/*` routes.
- Runtime state that must survive restart belongs in the configured database,
  not a second process-global store. Preserve migrations and repository
  ownership when changing persisted contracts.
- Scope reads and writes by authenticated owner plus thread/run identity.
  Preserve CSRF, cookie, SSE, and owner-isolation behavior. Do not leak whether
  another owner's object exists.
- Stream and persisted task events must follow pinned contracts in `contracts/`.
  Additive compatibility is preferred; coordinate frontend parsers and tests in
  the same change.
- Cancellation and terminal events must be idempotent. A child timeout must not
  leave descendants, active lanes, or frontend cards indefinitely running.
- Keep blocking filesystem, subprocess, SDK, and database work off the async
  event loop unless the API is natively async.

## Tests And Style

Backend work is test-driven: reproduce behavior with a focused test, implement
the smallest change, then run the relevant group and full checks before
completion.

```bash
cd backend
uv run pytest -q tests/test_task_tool_core_logic.py tests/test_codex_cli_subagent.py
uv run pytest -q tests/test_command_room*.py
make test
make lint
make format
```

Use Python 3.12+, type annotations, Ruff formatting/import order, and existing
Pydantic/dataclass patterns. Keep edits inside the owning layer; do not refactor
adjacent code. Update `config.example.yaml`, API/event contracts, docs, and the
frontend consumer when behavior crosses those boundaries.

After changing the AI-AI contract, role prompts, Command Room rules, skills,
`AGENTS.md`, or SkillOpt assets, also update root `Progress.md` and run
`make skillopt-probe` from the repository root.
