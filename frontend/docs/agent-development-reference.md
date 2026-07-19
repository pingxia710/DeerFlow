# Frontend Agent Development Reference

This is implementation context for frontend work. The repository and nearest
`AGENTS.md` files remain authoritative when they conflict with this reference.

## Layout And Conventions

```text
src/app/                 routes and route handlers
src/components/          UI, AI elements, and workspace components
src/core/threads/        streams, history, recovery, and task events
src/core/tasks/          subtask state and result parsing
src/core/api/            Gateway/LangGraph clients
src/core/                auth, agents, artifacts, skills, models, settings
src/content/{en,zh}/     product documentation
tests/unit/              Rstest unit tests
tests/e2e/               Playwright tests
```

Server Components are the default; add `"use client"` only for interactive
state. Reuse thread hooks and TanStack Query and build cache keys with
`src/core/threads/query-keys.ts`. Use `cn()`, `@/*` imports, inline `type`
imports, Lucide icons, existing Shadcn/AI primitives, and repository formatting.
Prefix intentionally unused variables with `_`.

## AI Task Presentation

Scope subtasks by `thread_id`, `run_id`, optional `round_id`, and `task_id`; tool
call IDs are not globally unique. Current transport emits `task_started` and one
terminal event. Do not depend on heartbeats or invent polling to simulate them.

Preserve successful `task()` output exactly on live and replay paths, including
terminal `ToolMessage` content and hidden control rows needed for restoration.
Task events, raw tool messages, middleware rows, and run-control rows restore
task state but are not ordinary chat bubbles. Historical calls without terminal
evidence appear running only while their owning run is active/current.

Configured model, effort, timeout, and sandbox values are displayable facts.
Compatibility fields that are null, empty, or false stay neutral. TypeScript may
not derive an AI-work decision from them. Parser contracts live under
`../contracts/`; coordinate payload, types, replay, and tests together.

## Thread And Runtime Ownership

Keep `ViewScope`, live `ExecutionOwner`, and persisted `RecordIdentity`
separate. Background runs update only their own cache and activity. Toasts,
navigation, scroll, composer state, and panels follow the visible view. Switching
threads must not leak rows, errors, uploads, queued messages, or task state.

`src/core/threads/hooks.ts` owns submission, upload gating, streams, and
recovery. Use persisted run IDs when rebuilding turns; message `seq` is local to
a run. Honor typed display metadata, falling back to legacy name/type rules only
for old rows. Hydrate from the runtime snapshot, then use bounded paginated APIs.
Do not recursively drain history or add broad focus/thread polling.

`run.terminal` refreshes its run and relevant queries. A disconnected resumable
stream is not automatically terminal. Recovery may probe only thread/run pairs
started or resumed by this frontend session. Clear all thread-owned cache,
recovery, tasks, usage, queues, drafts, and late callbacks on deletion/switch as
appropriate. Artifact UI belongs to the mounted chat subtree; Skill installation
requires `canManageSkills` in both UI and handler.

## API, Auth, Static Mode, And Accessibility

Use `getAPIClient()` and authenticated fetch helpers. Browser auth URLs honor
`getBackendBaseURL()` and ordinary/SSE requests include credentials. Route 401s
through the shared unauthorized path. Static mode must not call Gateway runtime,
context, channel, Skill, or suggestion APIs.

Prefer additive wire parsing and normalization at the frontend boundary. Normal
Nginx operation leaves direct backend URL overrides unset. Cross-host deployment
requires an explicit CORS/cookie/CSRF design.

Keep the restrained responsive workspace style. Avoid nested cards and layout
shifts. Icon-only controls need accessible labels and, when useful, tooltips;
hover actions remain keyboard- and touch-reachable. Mock/static chats disable
input, and agent-chat regeneration uses the normal regeneration path.

## Validation

```bash
cd frontend
pnpm test
pnpm check
pnpm test:e2e
```

Run focused tests plus full unit/type checks for task events, hidden replay,
runtime snapshots, or capability payloads. Update English and Chinese MDX docs
together.
