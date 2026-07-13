# DeerFlow Frontend Instructions

This guide applies under `frontend/` and inherits the repository contract,
including the frozen `What is DeerFlow` definition. Keep the complete
instruction chain under 32 KiB; detailed history belongs in docs and tests.

## Purpose And Layout

The frontend is the Next.js App Router interface for threads, streaming lead-AI
responses, one-shot subtask state, artifacts, settings, agents, and docs.

```text
src/app/                 routes and route handlers
src/components/          UI, AI elements, and workspace components
src/core/threads/        thread streams, history, recovery, task events
src/core/tasks/          subtask state and result parsing
src/core/api/            Gateway/LangGraph clients
src/core/                auth, agents, artifacts, skills, models, settings
src/content/{en,zh}/     product documentation
tests/unit/              Rstest unit tests
tests/e2e/               Playwright tests
```

Server Components are the default. Add `"use client"` only for interactive
state. Use the existing thread hooks and TanStack Query cache instead of adding
a second global runtime store. Build cache keys with
`src/core/threads/query-keys.ts`.

## AI-AI Task Presentation Contract

The browser displays factual execution state and AI-authored text; it does not
judge sub-AIs.

- Scope every subtask by `thread_id`, `run_id`, optional `round_id`, and
  `task_id`. A tool-call ID is not globally unique across chats.
- Current transport emits `task_started` and one terminal event. Do not depend
  on `task_running` heartbeats or invent client polling to simulate them.
- Successful `task()` output is the complete natural result from one Codex CLI
  AI. Preserve it exactly on the live path and reload it from the terminal
  `ToolMessage`, including internal/hidden control rows. Do not replace it with
  event descriptions, preview slices, structured summaries, scores, or token
  attribution.
- Task events, raw tool messages, middleware rows, and run-control rows are not
  ordinary chat bubbles. Use them to restore task cards and state, then keep
  them out of visible conversation history.
- Unknown structured terminal states must settle explicitly rather than remain
  indefinitely in progress. Historical tool calls without terminal evidence
  render as running only while their owning run is actually active/current.
- The UI may show configured child facts such as model, reasoning effort,
  timeout, and sandbox mode. It must not label work as correct, complete, safe,
  accepted, or needing rework unless that wording is explicit AI-authored text.
- Compatibility fields that are `null`, empty, or `false` remain neutral. Do not
  derive a quality verdict, evidence strength, next action, warning, or gap in
  TypeScript.
- Another sub-AI performs checking and an independent sub-AI provides
  opposition; the lead AI decides. The frontend must not dispatch either one.

Task event/action-result parsing is pinned by the contracts in `../contracts/`.
Coordinate parser, types, replay logic, backend payload, and tests in one change.

## Thread And Runtime Ownership

Keep these identities separate:

- `ViewScope`: the route/UI currently visible;
- `ExecutionOwner`: live `runtimeKey`, owner, thread, and run;
- `RecordIdentity`: persisted thread/run/optional round/task.

Background runs may update only their own caches/activity. Toasts, navigation,
scroll, composer state, and panels must pass the visible-view policy. Switching
threads must never show rows, errors, uploads, queued messages, or task state
from the previous thread.

Important ownership rules:

- `src/core/threads/hooks.ts` owns submission, upload gating, stream state, and
  recovery. Put reusable transformations in the existing pure helper modules.
- Use persisted run IDs first when rebuilding visible turns; run-message `seq`
  is local to a run, so order by run then by `seq`.
- Honor backend `display.visible_in_chat` and typed display metadata. Fall back
  to legacy caller/name/type checks only for old rows without that contract.
- Hydrate initial history from the runtime snapshot, then use bounded paginated
  run/message APIs. Do not recursively drain history or add broad thread/focus
  polling.
- SSE `run.terminal` triggers run-specific refresh and relevant query
  invalidation. A disconnected resumable stream is not automatically terminal;
  known-run recovery may probe only thread/run pairs started or resumed by this
  frontend session.
- Terminal runs without a visible assistant row need a non-message terminal
  notice. Clear it when a visible answer exists.
- Thread deletion clears all thread-scoped caches, recovery ownership, task
  state, usage/context state, queued messages, and late callbacks.
- Composer drafts/attachments clear on thread switches unless an explicit
  per-thread draft store owns them. Queued follow-ups stay with their originating
  thread and may never retarget the visible route.
- Artifact UI belongs to the mounted chat subtree. Skill installation requires
  `canManageSkills` both for visibility and handler authorization.

## API, Auth, And Static Mode

- Use `getAPIClient()` and authenticated fetch helpers. Browser auth URLs honor
  `getBackendBaseURL()`; ordinary and SSE requests include credentials.
- A 401 is not ordinary empty data. Dispatch the shared unauthorized path and
  let `AuthProvider`/route guards own navigation.
- Static website mode must not call Gateway runtime, context, channel, skill, or
  suggestion APIs. Return the established disabled/empty read model locally.
- Keep Gateway SSE and REST wire compatibility. Prefer additive parsing and
  normalize at the frontend boundary.
- Standard nginx operation leaves these optional variables unset:

```text
NEXT_PUBLIC_BACKEND_BASE_URL=http://localhost:8001
NEXT_PUBLIC_LANGGRAPH_BASE_URL=http://localhost:8001/api
```

## UI And Accessibility

Match the existing restrained workspace UI and component library. Use Lucide
icons and existing Shadcn/AI element primitives; do not manually edit generated
`components/ui/` or `components/ai-elements/` files. Avoid nested cards and
layout shifts. Keep text inside stable responsive bounds.

Icon-only controls require accessible labels and tooltips where meaning is not
obvious. Hover-revealed actions must remain reachable by keyboard and touch.
Mock/static agent chats disable prompt input. Agent-chat regeneration uses the
same thread regeneration path as normal chats.

Use `cn()` for conditional classes, `@/*` imports, inline `type` imports, and the
repository's ESLint/Prettier ordering. Prefix intentionally unused variables
with `_`.

## Validation

Write focused unit tests for changed pure logic and Playwright coverage for
meaningful user workflows. Verify responsive UI when presentation changes.

```bash
cd frontend
pnpm test
pnpm check              # lint, format check, and typecheck
pnpm test:e2e           # when a browser workflow changes
```

When task events, hidden message replay, runtime snapshots, or capability
payloads change, run the corresponding focused tests plus the full unit and
type checks. Update English and Chinese MDX docs together. AI-AI rule or skill
changes also require root `Progress.md` and `make skillopt-probe`.
