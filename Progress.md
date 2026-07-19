# Progress

## 2026-07-19 — Frontend recovery, performance, stale checks, and AI-AI-AI governance

- Fixed the real chat recovery failures found in the repository-wide run:
  recovery errors now replace the loading skeleton instead of leaving a blank
  page; a missing history no longer erases its own recovery notice; successful
  bounded snapshots do not immediately repeat the same run-list request; and
  historical subtask wake facts load only when the card is opened.
- Fixed current-round history replacement. When run timestamps tie, the native
  runtime-round order now wins over a stale `/runs` response, so starting a new
  round or resuming its current run removes prior-round history and renders the
  new result. The failing browser scenario passed five consecutive repeats and
  the complete 102-scenario browser run.
- Repaired stale browser fixtures and expectations: the mock roles API, delete
  confirmation, NextOS display name, genuinely old bounded-snapshot timestamps,
  and on-demand wake-fact expansion. Removed the obsolete `@types/gsap` stub.
  Optional local-PostgreSQL integration checks now skip when their explicit
  loopback URL is absent, while unsafe URL validation remains active.
- Moved request-path disk, hashing, encryption, upload, channel attachment,
  MCP/Skill config, prompt-package, and file-conversion work off the backend
  event loop. The static blocking-I/O inventory fell from 73 findings (5 high)
  to zero. Concurrent role-model updates now use an atomic per-user operation;
  malformed role assignment JSON falls back to defaults instead of returning
  HTTP 500. A latent async `grep` option-forwarding defect was also fixed.
- Strengthened AI-AI-AI governance without adding an orchestrator program:
  Command Room keeps read-only factual inspection, the distinct
  Planner → Opposition → Chair → human-confirmed execution contract, and
  optional temporary independent checking. Removed the stale fixed-Verifier
  test/config residue and duplicate built-in role prompt definitions. The root
  rules now state explicitly that child results are facts for continuing the
  plan, there is no task-level acceptance, and completion means the plan's
  actual criteria are satisfied. The permanently frozen program-is-fact-only
  subsection was not changed.
- Evidence: backend full run 6073 passed and 22 optional integrations skipped;
  changed backend selection 938 passed and 2 optional integrations skipped;
  frontend lint/type checks passed; frontend unit run 823 passed; browser run 102
  passed; blocking-I/O inventory returned zero; Ruff and targeted Prettier
  completed without errors.
- Restarted Gateway `:8001`, frontend `:3000`, and Nginx `:2026`. Health,
  direct frontend, and Nginx returned HTTP 200; unauthenticated `/api/roles`
  returned the expected 401 instead of 500, and startup logs contained no error
  signal.
- No production or live business system was accessed or mutated. No credential,
  customer, or payment data was exposed.

## 2026-07-19 — AI Team model configuration in the management UI

- Upgraded the existing Agents workspace into an `AI Team` page with separate
  `Agents` and `Professional Roles` tabs. Command Room is now the first
  built-in agent, can be configured but cannot be created twice or deleted.
- Added one shared model/reasoning dialog. Agent settings are saved in that
  user's agent `config.yaml`; Planner, Executor, Fact Finder, Opposition, and
  Recorder assignments are saved in the user's atomic
  `role-assignments.json`. No provider credential, role enablement, stage,
  acceptance, or program workflow controls were added.
- Agent reasoning settings are read when the next conversation builds its lead
  AI. Professional-role assignments are read when the next `task()` is
  dispatched and override the static `subagents` defaults. Neither path needs a
  service restart after a setting changes, and neither path lets the program
  choose a role, model, sequence, or result judgment.
- Kept model names provider-neutral in storage. The UI lists the models already
  registered by DeerFlow; future Kimi, GLM, or Opus assignments can reuse this
  configuration surface after their actual provider/task runner is connected.
- Backend agent/role/task/client regression selection: 259 passed. Frontend
  agent API/page tests: 14 passed. Ruff passed; `pnpm check` passed with zero
  errors and one pre-existing unused-argument warning in
  `frontend/src/core/threads/hooks.ts`.
- Restarted Gateway `:8001`, frontend `:3000`, and Nginx `:2026`. Gateway health
  returned HTTP 200; the authenticated existing UI session loaded its current
  Command Room page and agent API without a startup or request error. No
  browser-control instance was available for an automated visual click-through.
- No production or live business system was accessed or mutated. No credential,
  customer, or payment data was exposed.

## 2026-07-19 — Role-level model and reasoning allocation

- The Human Owner confirmed the current high-reasoning allocation: NextOS
  Command Room, Planner, and Opposition use the configured `gpt-5.6` alias
  (`gpt-5.6-sol`) with `max`; Executor, Fact Finder, and Recorder retain the
  delegated default `gpt-5.6-terra` with `xhigh`.
- Added optional task-label `reasoning_effort` beside the existing model
  override. Task transport only applies the selected configuration; it does not
  inspect task prose, choose a role, choose a model, or judge AI work.
- Model names remain configuration values rather than role implementation.
  Future Kimi, GLM, or Opus use is not prebuilt here; provider/CLI adaptation
  will be added only when one of those models is actually connected.
- The built-in Command Room profile now defaults to `gpt-5.6`; that model's
  configured default reasoning effort is `max`. A user-owned Command Room
  profile can still replace the built-in model setting.
- Focused configuration, task transport, Command Room model resolution,
  background wake, role package, provider, and config-version checks passed:
  195 tests. Ruff passed. An additional mixed regression selection exposed
  three already-present expectation failures in the independently modified
  Chair prompt/capability snapshot tests; none executes the changed role-level
  model path.
- Restarted Gateway `:8001`, frontend `:3000`, and Nginx `:2026`; all returned
  HTTP 200 and current startup logs contained no matching error signal.
- No production or live business system was accessed or mutated. No credential,
  customer, or payment data was exposed.

## 2026-07-19 — AI-owned governance learning and organizational memory

- The Human Owner confirmed the NextOS governance model: project and role
  `AGENTS.md` files hold purpose, authority, and boundaries; Chair and role
  Skills hold narrow reusable coordination and professional methods; task
  prompts hold current context; `Progress.md` holds factual organizational
  memory and never becomes authority or workflow state.
- Added the Chair-owned failure-to-governance method. The Chair corrects the
  current handoff first, then routes an evidenced recurring failure or serious
  redline failure to the lowest effective layer. It may drive narrow role-Skill
  improvements inside the confirmed model; purpose, permanent boundaries,
  authority, planning-contract, and material workflow changes still require the
  human.
- Added proportionate independent checking for materially risky, conflicting,
  unsupported, or hard-to-check results. It is a temporary AI perspective that
  reports discrepancies and uncertainty, not a fixed Reviewer, task acceptance,
  or program gate.
- Programs do not count failures, identify patterns, choose governance layers,
  edit rules, or decide whether a change helped. The runtime still transports
  complete prompts/results, records facts, and wakes the Chair only.
- Normalized the six active role Skills to minimal `name`/`description`
  frontmatter with owner/version/review information in the body. All six passed
  the standard Skill validator. Focused prompt, role-package, task-transport,
  and model-resolution tests passed: 54. Ruff and `git diff --check` passed.
- Updated `.gitignore` so only each configured NextOS role package's
  `AGENTS.md` and `SKILL.md` are versionable; unrelated custom-Skill contents
  remain local. The role governance can therefore survive a clone or handoff.
- Kept instruction loading below the 32 KiB budget by moving backend and
  frontend implementation detail into module references. The measured chains
  are 32,106 bytes for backend and 32,098 bytes for frontend.
- NextOS contract and SkillOpt checks passed. Gateway `:8001`, frontend `:3000`,
  and Nginx `:2026` were restarted and returned HTTP 200; recent startup logs
  contained no error, exception, traceback, or port-conflict signal.
- This entry supersedes older same-day Progress descriptions of a fixed
  Verifier or task-level acceptance loop. Those entries remain only as factual
  development history.
- No production or live business system was accessed or mutated. No credential,
  customer, or payment data was exposed.

## 2026-07-19 — Role packages and AI-owned result comparison

- Added a role `AGENTS.md` charter and a compact, failure-oriented `SKILL.md`
  for the Chair and every configured reusable Command Room worker role:
  planner, executor, fact-finder, opposition, and recorder.
- Task transport now carries the package for the role the Chair already chose;
  it does not select a role, create a stage, or judge a result. The Chair now
  explicitly compares returned claims and artifacts with the original task
  contract, definition of done, current facts, and the confirmed plan.
- Added the WorkOS-derived Skill writing rule to the repository `AGENTS.md`:
  narrow failure-driven methods, explicit boundaries and factual returns,
  owner/version/review, and focused positive/negative evaluation before a Skill
  remains active. The WorkOS state-machine and PASS-gate mechanisms were not
  imported because DeerFlow's locked program boundary remains AI-owned.
- Focused role-package, task-prompt, Chair-prompt, and tool-exposure tests:
  53 passed. A live configuration probe confirmed the executor package reaches
  the delegated task prompt.

## 2026-07-19 — Plan-confirmed execution, no task acceptance

- Chair now has direct read-only file investigation (`ls`, `read_file`, `glob`,
  and `grep`) so it can inspect code, logs, plans, and artifacts before making
  a command decision. Writes, shell work, and execution remain delegated.
- Replaced the former Planner/Opposition/Verifier spine with a distinct plan
  conversation: Planner proposal → Opposition challenge → Chair execution plan
  → human discussion and explicit execution authorization → plan-directed
  execution → plan completion.
- Removed the built-in `verifier` role and task-level acceptance semantics.
  Child results are working facts; the Chair continues the authorized plan and
  returns to the human only for a core direction, scope, authority, or business
  decision change.

## 2026-07-19 — NextOS AI enterprise identity restored

- Restored NextOS as the user-facing AI-organization product layer on the
  DeerFlow runtime. Internal `deerflow.*` packages and the `command-room` agent
  identifier remain unchanged for API, thread, persistence, and import
  compatibility.
- Rebuilt `nextos-commander` around the accepted enterprise-team model: one
  continuing root Chair, persistent goals/decisions/results, temporary
  professional AI instances, Chair-routed natural-language handoffs, and an
  optional temporary bounded workstream lead only when coordination context
  genuinely needs separation.
- Kept the fixed AI-owned quality spine: Planner proposal → Opposition challenge
  → Chair synthesis → execution → Verifier → Chair decision. Parallel route
  exploration uses additional Planners; changed core direction repeats
  Opposition, and changed execution repeats verification.
- The built-in Command Room profile now describes itself as NextOS, exposes the
  restored Commander Skill, and the frontend displays the stable
  `command-room` route as NextOS.
- Did not restore the superseded SkillOpt adjudicator, deterministic lifecycle,
  fixed Steward/Curator chain, program stage gates, or old parallel
  forward/opposition contract. Programs remain fact-only transport, persistence,
  process-lifecycle, and Chair-wakeup infrastructure.
- Runtime loading probe confirmed that the built-in `command-room` profile has
  the NextOS description, enables `nextos-commander`, loads the Skill from disk,
  and receives the NextOS Command Room system prompt.
- Focused backend prompt, role, Skill, task-transport, wake, provider, security,
  client, and probe regression tests: 412 passed. Frontend unit tests: 818
  passed. Ruff, Python formatting, targeted frontend/MDX Prettier, and
  `git diff --check` passed; `pnpm check` completed with zero errors and one
  pre-existing unused-argument warning in `frontend/src/core/threads/hooks.ts`.
- Restarted Gateway, frontend, and Nginx. Gateway `/health`, direct frontend,
  and the Nginx entrypoint all returned HTTP 200; startup logs contained no
  error, exception, traceback, or port-conflict signal, and the services settled
  idle.
- No production or live business system was accessed or mutated, and no secret
  or customer data was exposed.

## 2026-07-19 — Command Room orchestration restrictions removed

- Removed the performance workaround that forced Command Room lead reasoning
  from `high`/`xhigh`/`max` down to `medium`.
- Removed `SubagentLimitMiddleware` and the six-call request boundary. The lead
  AI now chooses the useful `task()` count; legacy `max_concurrent_subagents`
  inputs are ignored instead of controlling prompts, cache keys, or execution.
- Added the built-in `verifier` role and standardized the Chinese name as
  “独立核验 AI”; no `Reviewer` role or alias remains. Substantive Command Room
  work now follows an AI-owned planner proposal → one opposition challenge →
  Chair synthesis → execution → independent verification → Chair decision
  contract. Opposition runs after the complete proposal, repeats only when
  synthesis changes the core direction, and never becomes program state.
- Parallel independent proposals remain available as additional planning
  perspectives; they are not labeled opposition. Opposition reports hidden
  assumptions, counterevidence, failure modes, and materially different
  alternatives without forced disagreement or authority over the decision.
- Verification checks the completed result and actual artifacts and reports
  discrepancies and unresolved facts. It does not approve, reject, modify the
  work, or replace Chair judgment; no program state or parsed verdict enforces
  this collaboration flow.
- Preserved each complete child prompt during provider replay. Background task
  receipts and terminal wake messages now carry facts without directing the
  Chair to end, continue, compare, or ask for specific human input.
- Updated the repository/backend contracts, English and Chinese documentation,
  middleware diagrams, and the literature-review Skill. Hard child timeout,
  cancellation, process cleanup, authentication, credential isolation, and
  owner scoping remain unchanged.
- Restart exposed a pre-existing migration incompatibility: 64 valid historical
  wake Runs predated `command_room_wake_id`. Migration `0011` now preserves
  those rows with a null dedicated identity while still rejecting malformed or
  duplicate non-null IDs. The local database reached revision `0012` without
  deleting or rewriting historical records.
- Focused backend integration: 395 passed; migration/persistence: 39 passed.
  Backend excluding the external-PostgreSQL-only contract file: 6045 passed, 20
  skipped. The unfiltered run had 6052 passed and only two failures because
  `DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL` is not set. Frontend: 817 passed;
  `pnpm check` completed with one pre-existing unused-argument warning.
- The sequential Planner/Opposition/Chair contract passed 317 focused prompt,
  role, probe, task-transport, wake, provider, security, and client tests. The
  Command Room suite excluding its external-PostgreSQL-only file passed 51;
  the unfiltered selection passed 57 and had only the same two missing-environment
  failures. Ruff, Python formatting, MDX Prettier, and `git diff --check` passed;
  `pnpm check` again had zero errors and the same pre-existing warning.
- A Nextra/Turbopack cold documentation compile exposed a stale 7.0 GB ignored
  `frontend/.next` cache and sustained multi-core compilation. The stopped
  cache was moved recoverably to the user's Trash, rebuilt to about 905 MB,
  and the frontend settled near idle before final health checks.
- Restarted Gateway, frontend, and Nginx. Gateway `/health`, direct frontend,
  and the Nginx entrypoint all returned healthy/HTTP 200 with no startup errors.
- No production/live business system was accessed or mutated, and no secret or
  customer data was exposed.

## 2026-07-17 — AI-AI-AI fact-only program boundary

- Human Owner explicitly authorized this repository-wide reset and the protected
  amendment under `AGENTS.md > What is DeerFlow`.
- DeerFlow now permanently defines deterministic code as a factual recorder and
  transport mechanism only. AI natural-language work can be assessed and
  directed only by AI; code cannot make those decisions or turn recorded facts
  into authority over AI work.
- Removed the superseded deterministic work-control assets, specialized Skills,
  state transitions, automatic follow-up chains, compatibility fields, UI
  projections, tests, probes, plans, evidence documents, and historical
  descriptions that encoded that behavior.
- `task()` carries one complete natural-language prompt to one short-lived AI
  process and returns its complete natural-language result. Command Room
  background completion still wakes the lead AI with facts.
- Preserved transport recovery, hard timeout and cancellation, authentication,
  credential isolation, process cleanup, owner scoping, redaction, and
  non-mutating factual timelines. These mechanisms do not evaluate AI output or
  decide the next AI action.
- No live service or production system was accessed or mutated. No credential
  or customer data was exposed.
