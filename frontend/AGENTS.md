# DeerFlow Frontend Instructions

Inherit the repository Goal Lock and frozen program boundary. Read
[`docs/agent-development-reference.md`](docs/agent-development-reference.md)
for thread, replay, API, UI, and ownership implementation details.

## Goal Lock

- This Next.js App Router frontend displays the NextOS AI organization through
  the stable internal `command-room` identity. Do not rename thread, API,
  persistence, or package identifiers.
- The browser displays factual process state and complete AI-authored text. It
  never judges correctness, completion, safety, acceptance, evidence strength,
  or the next AI action and never dispatches roles or enforces workflow stages.
- Preserve the complete successful child result on live and reload paths. Do
  not replace it with event descriptions, previews, summaries, scores, or
  program-derived verdicts.
- Scope task state by thread, run, optional round, and task identity. Keep task
  events and hidden control rows out of visible chat while using them to restore
  factual task cards. Unknown terminal states must settle explicitly.
- The Chair owns planning, human plan confirmation, plan-directed execution,
  result interpretation, and plan completion. UI state and compatibility fields
  must remain neutral and cannot become task acceptance or quality gates.

## Hard Boundaries

- Use existing thread hooks, TanStack Query caches, API clients, auth paths,
  event contracts, and component primitives. Do not add a second runtime store.
- Preserve visible-view versus execution-owner isolation across navigation,
  streaming, recovery, deletion, drafts, uploads, task state, and late callbacks.
- Preserve credentials, CSRF/cookie/SSE behavior, unauthorized handling, static
  mode isolation, capability checks, responsive layout, keyboard/touch access,
  labels, and tooltips. Do not edit generated UI/AI-element components directly.
- Coordinate task/event contract changes with backend payloads, parsers, types,
  replay logic, tests, and English/Chinese docs.

## Validation

Use Server Components by default and existing TypeScript/style conventions.

```bash
cd frontend
pnpm test
pnpm check
pnpm test:e2e   # when a browser workflow changes
```

Add focused unit coverage for changed logic and Playwright coverage for changed
user workflows. AI-AI, Skill, or instruction changes also require root
`Progress.md` and focused prompt, role-package, and task-transport tests.
