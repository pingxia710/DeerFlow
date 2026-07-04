# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with the DeerFlow frontend. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

## Project Overview

DeerFlow Frontend is a Next.js 16 web interface for an AI agent system. It communicates with a LangGraph-based backend to provide thread-based AI conversations with streaming responses, artifacts, and a skills/tools system.

**Stack**: Next.js 16, React 19, TypeScript 5.8, Tailwind CSS 4, pnpm 10.26.2. Requires Node.js 22+ and pnpm 10.26.2+.

### Core dependencies

- **LangGraph SDK** (`@langchain/langgraph-sdk` ^1.5.3) — Agent orchestration and streaming
- **LangChain Core** (`@langchain/core` ^1.1.15) — Fundamental AI building blocks
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

E2E tests live under `tests/e2e/` and use Playwright with Chromium. They mock all backend APIs via `page.route()` network interception and test real page interactions (navigation, chat input, streaming responses). Config: `playwright.config.ts`; it starts its own frontend on port `6100` by default (`PLAYWRIGHT_PORT` overrides), forces auth-disabled test mode for SSR workspace routes, and does not reuse an existing server unless `PLAYWRIGHT_REUSE_EXISTING_SERVER=1`.

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

- `src/app/workspace/chats/[thread_id]/page.tsx` owns composer busy-state wiring.
- `src/core/threads/hooks.ts` owns pre-submit upload state and thread submission.
- Thread hooks must only render live stream state/history when the source thread
  id matches the visible thread id; switching chats must not display stale
  messages from the previous stream target.
- Subtask UI state must be scoped by thread id when read or written. Do not
  treat backend task/tool-call ids as globally unique across chat sessions.
  Prefer task-event `thread_id`/`run_id` and SDK run callback metadata over the
  currently visible route id; users can switch chats while a run is still alive.
- Persisted `task_event` run messages are control rows: thread history should
  use them to restore subtask state, then keep them out of visible chat bubbles.
  `RunMessage.content` may be either a chat `Message` or a structured control
  event; only real chat messages should enter visible history merging. Legacy
  rows without `metadata.caller` may still be treated as task events when their
  content matches the pinned task-event schema.
- History run messages should honor backend `display.visible_in_chat` when
  present; only fall back to local `caller`/`name`/`hide_from_ui` checks for
  older rows without the display contract.
- History message ordering must treat run-message `seq` as run-local. Rebuild
  history by run list order first, then by `seq` within each run.
- Thread history refreshes should be driven by run-list changes and explicit
  run IDs such as terminal transitions, not timer/focus polling of recent active
  runs.
- Explicit run refresh requests that arrive before the run list contains that
  run should stay pending until the run list catches up; do not fall back to
  broad thread polling.
- Run-specific history refreshes should replace existing rows for that run on
  the refreshed first page; do not append stale same-run rows that disappeared
  from the backend response.
- SSE `run.terminal` custom events should trigger run-specific history refresh;
  do not turn them into task cards or visible chat messages.
- Background completion recovery may probe only known `thread_id` + `run_id`
  pairs that the current frontend session started or resumed; do not add broad
  thread-list polling to compensate for missing run lifecycle events.
- Thread-list running indicators should treat backend `busy`/`pending`/`running`
  plus run-recovery statuses such as `cancelling`/`rolling_back` as active, but
  backend terminal statuses such as `idle`/`error`/`timeout`/`interrupted`,
  `boundary_stopped`, or `worker_lost` override stale local running markers
  from disconnected streams.
- Thread list and auth refreshes should also avoid interval or focus polling;
  prefer explicit user actions, route changes, or recovery probes.
- Generic API fetch helpers should throw on 401 instead of performing global
  browser redirects; route guards and AuthProvider own login navigation.
- Queued follow-up messages while a stream is active are thread-local UI state;
  discard them on chat-thread switches unless a real per-thread queue/draft
  store is implemented.
- Deleting a chat thread must also clear thread-local client state such as
  running/finished activity markers, run lists, metadata, token usage, and
  context usage caches.
- Task event/action_result parsing is pinned by
  `contracts/task_event_contract.json`; do not depend on prose-only status text
  or silently leave unknown structured terminal states in progress.
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
