# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with the DeerFlow frontend. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

## Project Overview

DeerFlow Frontend is a Next.js 16 web interface for an AI agent system. It communicates with a LangGraph-based backend to provide thread-based AI conversations with streaming responses, artifacts, and a skills/tools system.

**Stack**: Next.js 16, React 19, TypeScript 5.8, Tailwind CSS 4, pnpm 10.26.2. Requires Node.js 22+ and pnpm 10.26.2+.

### Core dependencies

- **LangGraph SDK** (`@langchain/langgraph-sdk` ^1.5.3) — Agent orchestration and streaming
- **LangChain Core** (`@langchain/core` ^1.2.2) — Fundamental AI building blocks
- **TanStack Query** (`@tanstack/react-query` ^5.90.17) — Server state management
- **UI**: Shadcn UI, MagicUI, React Bits, and Vercel AI SDK elements (generated from registries — see Code Style)

## Commands

| Command          | Purpose                                                                                                        |
| ---------------- | -------------------------------------------------------------------------------------------------------------- |
| `pnpm dev`       | Dev server with Turbopack (http://localhost:3000 by default; root `make dev` may set `PORT` when 3000 is busy) |
| `pnpm build`     | Production build                                                                                               |
| `pnpm check`     | Lint + type check (run before committing)                                                                      |
| `pnpm lint`      | ESLint only                                                                                                    |
| `pnpm lint:fix`  | ESLint with auto-fix                                                                                           |
| `pnpm format`    | Prettier check (`pnpm format:write` to apply)                                                                  |
| `pnpm test`      | Run unit tests with Rstest                                                                                     |
| `pnpm test:e2e`  | Run E2E tests with Playwright (Chromium)                                                                       |
| `pnpm typecheck` | TypeScript type check (`tsc --noEmit`)                                                                         |
| `pnpm start`     | Start production server                                                                                        |

Unit tests live under `tests/unit/` and mirror the `src/` layout (e.g., `tests/unit/core/api/stream-mode.test.ts` tests `src/core/api/stream-mode.ts`). Powered by Rstest; import source modules via the `@/` path alias.

E2E tests live under `tests/e2e/` and use Playwright with Chromium. They mock all backend APIs via `page.route()` network interception and test real page interactions (navigation, chat input, streaming responses). Config: `playwright.config.ts`; it starts its own frontend on port `6100` by default (`PLAYWRIGHT_PORT` overrides), forces auth-disabled test mode for SSR workspace routes, and does not reuse an existing server unless `PLAYWRIGHT_REUSE_EXISTING_SERVER=1`. Cross-stack replay tests live under `tests/e2e-real-backend/` and use `playwright.real-backend.config.ts` to run the real frontend against a deterministic replay Gateway; default ports are frontend `3100` and Gateway `8011`, with reuse disabled unless `PLAYWRIGHT_REUSE_EXISTING_SERVER=1`. Use this suite when frontend history behavior depends on real run/message endpoints; set `PLAYWRIGHT_REAL_BACKEND_VISUAL=1` only when intentionally checking the local screenshot baseline.

## Architecture

```
Frontend (Next.js) ──▶ LangGraph SDK ──▶ LangGraph Backend (lead_agent)
                                              ├── Sub-Agents
                                              └── Tools & Skills
```

The frontend is a stateful chat application. Users create **threads** (conversations), send messages, and receive streamed AI responses. The backend orchestrates agents that can produce **artifacts** (files/code) and **todos**.

### Source Layout (`src/`)

- **`app/`** — Next.js App Router. Routes include `/` (landing), `/workspace/chats/[thread_id]` (chat), `/workspace/agents/[agent_name]` and `/workspace/agents/new` (custom agents), `/blog/…`, the `(auth)/{login,setup,auth/callback}` flow, `/[lang]/docs/…`, and `/api/…` route handlers (e.g. `/api/memory`).
- **`components/`** — React components:
  - `ui/` — Shadcn UI primitives (auto-generated, ESLint-ignored)
  - `ai-elements/` — Vercel AI SDK elements (auto-generated, ESLint-ignored)
  - `workspace/` — Chat page components (messages, artifacts, settings)
  - `landing/` — Landing page sections
  - `docs/` — Docs / MDX rendering components
- **`core/`** — Business logic, the heart of the app. Domains include `threads/` (creation, streaming, state), `api/` (LangGraph client singleton), `agents/` (custom agents), `auth/` (authentication), `artifacts/`, `channels/` (IM connections), `i18n/` (en-US, zh-CN), `settings/`, `memory/`, `skills/`, `messages/`, `mcp/`, `models/`, `suggestions/`, `tasks/`, `todos/`, `tools/`, `config/`, `notification/`, `blog/`, plus rendering helpers (`rehype/`, `streamdown/`) and `utils/`.
- **`hooks/`** — Shared React hooks
- **`lib/`** — Utilities (`cn()` from clsx + tailwind-merge)
- **`content/`** — MDX content (blog posts, docs) rendered by the app
- **`styles/`** — Global CSS with Tailwind v4 `@import` syntax and CSS variables for theming
- **`typings/`** — Ambient TypeScript declarations
- Root files: `env.js` (env validation), `mdx-components.ts` (MDX component map)

### Data Flow

1. User input → thread hooks (`core/threads/hooks.ts`) → LangGraph SDK streaming
2. Stream events update thread state (messages, artifacts, todos)
3. TanStack Query manages server state; localStorage stores user settings
4. Components subscribe to thread state and render updates

### Key Patterns

- **Server Components by default**, `"use client"` only for interactive components
- **Thread hooks** (`useThreadStream`, `useSubmitThread`, `useThreads`) are the primary API interface
- **LangGraph client** is a singleton obtained via `getAPIClient()` in `core/api/`
- **Environment validation** uses `@t3-oss/env-nextjs` with Zod schemas (`src/env.js`). Skip with `SKIP_ENV_VALIDATION=1`

### Interaction Ownership

- Runtime ownership uses three separate identities: route/UI `ViewScope`, live
  `ExecutionOwner` (`runtimeKey`/`runtimeOwnerId`/thread/run), and persisted
  `RecordIdentity` (thread/run/optional round). Do not collapse them into one
  universal owner or add a second global runtime store beside LangGraph state
  and TanStack Query.
- Keep thread orchestration in `core/threads/hooks.ts`; put reusable pure logic
  in `message-history.ts`, `run-recovery.ts`, `task-events.ts`,
  `effect-policy.ts`, and `command-room-read-model.ts`. Preserve LangGraph SSE
  wire compatibility and normalize/project only at the frontend boundary.
- Artifact UI state belongs to the mounted chat subtree. The prompt-input
  controller stays workspace-scoped because page-level chat-mode hooks consume
  it, while its active scope lives in React context and is keyed by Composer.
  A background owner may update its own caches/activity, but toast, routing,
  scroll, panel, and composer effects must pass the visible-view policy.
- Build thread/run/snapshot/artifact/upload cache keys through
  `core/threads/query-keys.ts`; thread deletion must remove every matching
  thread-scoped key and reject late callbacks via the existing tombstone path.
- Command Room UI projects authoritative Gateway `runs`/`rounds`/`task_lanes`.
  Legacy records without `round_id` are compatibility-only and must not
  overwrite strong thread/run/round state or implement governance decisions in
  frontend code.
- `src/app/workspace/chats/[thread_id]/page.tsx` owns composer busy-state wiring.
- The composer model picker keeps `context.model_name` as the persisted value
  and groups `/api/models` entries by their public `provider` display label.
- `src/core/threads/hooks.ts` owns pre-submit upload state and thread submission;
  upload responses with `success: false` or any `skipped_files` must abort the
  message submit instead of silently dropping attachments.
- Visible conversation turns are presentation-only: prefer persisted
  `deerflow_run_id`/`run_id`, then fall back to human-message boundaries. Do not
  use Command Room round state or mutate history to construct them.
- When a reader has left the live edge, stream updates and prepended history must
  preserve that reader's visible anchor until they explicitly return to the reply.
- Thread hooks must only render live stream state/history when the source thread
  id matches the visible thread id; switching chats must not display stale
  messages from the previous stream target.
- Subtask UI state must be scoped by thread id when read or written. Do not
  treat backend task/tool-call ids as globally unique across chat sessions.
  Prefer task-event `thread_id`/`run_id` and SDK run callback metadata over the
  currently visible route id; users can switch chats while a run is still alive.
- Persisted `task_event`, raw tool, middleware, and subagent run messages are
  control rows: thread history should use task events to restore subtask state,
  then keep internal rows out of visible chat bubbles. `RunMessage.content` may
  be either a chat `Message` or a structured control event; only user messages
  and lead-agent user-facing AI responses should enter visible history merging.
  Legacy rows without `metadata.caller` may still be treated as task events when
  their content matches the pinned task-event schema.
- History run messages should honor backend `display.visible_in_chat` when
  present, and use `display.message_type` / `display.payload_types` for typed
  replay state. Only fall back to local `caller`/`name`/`type`/`hide_from_ui`
  checks for older rows without the display contract.
- Initial thread reload should hydrate from
  `/api/threads/{thread_id}/runtime-snapshot` before falling back to individual
  run-list/message endpoints. Snapshot rows use the same `display` contract and
  should seed both visible history and control-row task recovery; snapshot
  `task_lanes` should also settle existing subtask cards when task-event rows
  are missing or paged out.
- History message ordering must treat run-message `seq` as run-local. Rebuild
  history by run list order first, then by `seq` within each run.
- Run responses may include `round_id`/`round_state`. When the latest run's
  `round_id` changes, default history should reset to the current round/current
  run.
- Initial history rebuild should continue through older runs while visible
  messages are being restored; only consecutive empty/control-only runs should
  stop at the bounded empty-run safety cap.
- Thread history refreshes should be driven by run-list changes and explicit
  run IDs such as terminal transitions, not timer/focus polling of recent active
  runs.
- Per-run pagination should still fetch one run-message page at a time; do not
  recursively drain every `has_more` page while switching chats or scrolling
  history.
- Run-list history pagination should request one older 100-run page through the
  Gateway `before` cursor only after known runs are exhausted. Ignore stale
  page results after thread or history-generation changes.
- Explicit run refresh requests that arrive before the run list contains that
  run should stay pending until the run list catches up; do not fall back to
  broad thread polling.
- Explicit run refresh requests that arrive while a history page is in flight
  should queue and reset that run's loaded/cursor state before the next page
  selection, so an old in-flight page cannot swallow a terminal refresh.
- Run-specific history refreshes should replace existing rows for the returned
  `seq` window, while preserving already-loaded older pages for that run; do not
  append stale same-run rows from the refreshed window.
- SSE `run.terminal` custom events should trigger run-specific history refresh;
  they should also invalidate thread run-list and usage queries so pending run
  refreshes can resolve. Do not turn them into task cards or visible chat
  messages.
- A terminal latest run with no visible AI row, including `success`, should show
  the non-message terminal notice instead of leaving the chat silently blank;
  suppress terminal/recovery notices once that user turn has a visible assistant
  answer.
- Background completion recovery may probe only known `thread_id` + `run_id`
  pairs that the current frontend session started or resumed; do not add broad
  thread-list polling to compensate for missing run lifecycle events.
- A stream error for a known `thread_id` + `run_id` is not terminal when runs
  use resumable `onDisconnect=continue`; keep the thread busy, keep current
  visible/optimistic rows, and let the background probe or run list terminal
  state settle it. While that recovery owns the visible run, do not expose the
  transient stream error as the chat/input error state; the UI should keep
  showing streaming until terminal evidence arrives.
- If known-run background probing exhausts its bounded attempts or hits a
  permanent auth/not-found response, clear the local recovery ownership and
  invalidate run/thread lists; do not leave the visible chat/input in indefinite
  fake streaming without terminal evidence.
- Thread-list running indicators should treat backend `busy`/`pending`/`running`
  plus run-recovery statuses such as `cancelling`/`rolling_back` as active, but
  backend terminal statuses such as `idle`/`error`/`timeout`/`interrupted`,
  `boundary_stopped`, or `worker_lost` override stale local running markers
  from disconnected streams.
- Thread list and auth refreshes should also avoid interval or focus polling;
  prefer explicit user actions, route changes, or recovery probes.
- Browser auth URLs must honor `getBackendBaseURL()`, and the LangGraph SDK must
  use the authenticated fetcher so ordinary/SSE requests include credentials.
  Generic API fetch helpers should throw and dispatch the shared unauthorized
  event on 401; route guards and AuthProvider own login navigation.
- Optional background UI requests must not swallow 401 as ordinary empty data;
  use the fetcher's `UnauthorizedError`/`isUnauthorizedError()` path and let
  AuthProvider or the query caller refresh user state.
- Artifact skill-install actions must use `canManageSkills` for both visibility
  and handler authorization; only non-static administrators may invoke them.
- Queued follow-up messages while a stream is active are thread-local UI state
  owned by the per-thread runtime slot. They may survive chat-thread switches,
  but they may only auto-release through the queued message's owning thread
  runtime and must never retarget the currently visible thread.
- Deleting a chat thread must also clear thread-local client state such as
  running/finished activity markers, run lists, metadata, token usage, and
  context usage caches.
- Task event/action_result parsing is pinned by
  `contracts/task_event_contract.json`; do not depend on prose-only status text
  or silently leave unknown structured terminal states in progress.
- Historical subagent `task` tool calls without terminal result evidence must
  not render indefinite running cards unless their owning run is still active
  or the card belongs to the current live turn; stale history should wait for
  durable task lanes/events or a terminal run recovery state.
- Prompt input draft and attachment state must be cleared on chat-thread
  switches unless a per-thread draft store is implemented.
- Agent chat pages must pass `isMock` through `ThreadContext` and disable the
  prompt input for mock/static demo threads, matching the normal chat page.
- Agent chat pages must wire assistant-message regeneration through the same
  `useThreadStream.regenerateMessage` path as normal chat pages.

## Code Style

- **Imports**: Enforced ordering (builtin → external → internal → parent → sibling), alphabetized, newlines between groups. Use inline type imports: `import { type Foo }`.
- **Unused variables**: Prefix with `_`.
- **Class names**: Use `cn()` from `@/lib/utils` for conditional Tailwind classes.
- **Path alias**: `@/*` maps to `src/*`.
- **Components**: `ui/` and `ai-elements/` are generated from registries (Shadcn, MagicUI, React Bits, Vercel AI SDK) — don't manually edit these.

## Environment

Backend API URLs are optional; an nginx proxy is used by default:

```
NEXT_PUBLIC_BACKEND_BASE_URL=http://localhost:8001
NEXT_PUBLIC_LANGGRAPH_BASE_URL=http://localhost:8001/api
```

Leave these unset for the standard `make dev` / Docker flow, where nginx serves the public `/api/langgraph/*` prefix and rewrites it to Gateway's native `/api/*` routes.

Root local startup supports `DEER_FLOW_FRONTEND_PORT` when port `3000` is not
available. If it is unset and `3000` is occupied by a non-DeerFlow process,
`scripts/serve.sh` chooses a free `6001+` port and renders the local nginx
proxy config to the same port.

## Resources

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [LangChain Core Concepts](https://js.langchain.com/docs/concepts)
- [TanStack Query Documentation](https://tanstack.com/query/latest)
- [Next.js App Router](https://nextjs.org/docs/app)

## Contributing

When adding features:

1. Follow the established `src/` structure
2. Add TypeScript types and proper error handling
3. Write unit tests under `tests/unit/` (`pnpm test`) and E2E tests under `tests/e2e/` (`pnpm test:e2e`)
4. Run `pnpm check` before committing
5. Update this `AGENTS.md` when architecture, commands, or conventions change
