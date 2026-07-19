# Backend Agent Development Reference

This is implementation context for backend work. The repository and nearest
`AGENTS.md` files remain authoritative when they conflict with this reference.

## Ownership Map

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

Keep shared behavior in the harness and adapt it at the Gateway edge. Use
injected `AppConfig` in runtime and request paths instead of ambient global
config when an explicit snapshot exists.

## One-Shot Task Transport

The lead AI chooses a professional perspective and writes the semantic task
contract: goal, confirmed context, boundaries, allowed work, starting paths,
definition of done, and requested natural result. DeerFlow resolves authored
role context from `system_prompt`, role `description`, then the general fallback.
It adds path and `AGENTS.md` orientation once and sends the same audited prompt
to one Codex process. Codex owns its reasoning, native tools, checks, and result.

Ordinary agents receive the complete final text. Command Room receives an
immediate background receipt; Gateway preserves the terminal fact and wakes a
new sequential Chair Run with the complete result. Transport may emit
`task_started` and one terminal factual event: `task_completed`, `task_failed`,
`task_timed_out`, or `task_cancelled`.

The lead chooses useful task count and independence. DeerFlow does not use a
response boundary to drop, queue, batch, or defer task calls. Host/model capacity
and hard process timeout remain transport facts.

## Child Process Contract

The current default configuration is:

```yaml
config_version: 18
subagents:
  model: gpt-5.6-terra
  reasoning_effort: xhigh
  timeout_seconds: 3600
```

`subagents.model` is independent of the lead model. Use the exact effort name
`xhigh`. Keep the parent no-progress watchdog longer than the child timeout.
Invoke the pinned Codex CLI, write its final response to a private temporary
file, bound diagnostics, and terminate the process group on normal exit,
timeout, cancellation, or failure.

Pass only core runtime and explicit Codex authentication variables. Configure
the Codex shell environment so child shell commands do not receive credentials
or unrelated host variables. `cwd`, add-directories, and sandbox mode are hard
transport boundaries. This local project intentionally permits trusted host
paths; changing isolation architecture requires user confirmation. Codex CLI
and valid supported authentication are runtime requirements and are not baked
into container images.

## Records, Runtime, And Persistence

Command Room modules may preserve complete AI-authored text plus objective IDs,
timestamps, process status, hashes, byte counts, and references. They do not
derive evidence strength, correctness, recommendations, routing, or closure.
A failed sequential Chair wake may retry delivery of the same complete result as
transport recovery only.

Gateway runs use `RunManager`, `run_agent()`, and `StreamBridge`; Nginx maps
`/api/langgraph/*` to Gateway-native `/api/*` routes. Durable runtime facts
belong in the configured database, not a second process-global store. Scope
reads and writes by authenticated owner plus thread/run identity, preserve
migrations, and avoid leaking whether another owner's object exists.

Stream and persisted task events follow the pinned contracts under
`contracts/`. Coordinate additive contract changes with frontend parsers and
tests. Keep blocking filesystem, subprocess, SDK, and database work off the
async event loop unless the API is natively async.

## Broader Validation

```bash
cd backend
uv run pytest -q tests/test_task_tool_core_logic.py tests/test_codex_cli_subagent.py
uv run pytest -q tests/test_command_room*.py
make test
make lint
make format
```

Update `config.example.yaml`, API/event contracts, docs, and the frontend
consumer when behavior crosses those boundaries.
