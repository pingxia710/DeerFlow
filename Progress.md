# Progress

## 2026-07-19 — Codex child-process performance boundary confirmed

- A controlled one-shot Codex A/B probe confirmed that the remaining resource
  cost is not a DeerFlow process leak. With the current complete user Codex
  configuration, one idle-tool task peaked at 18 processes and 1,124.5 MiB
  aggregate RSS. `--ignore-user-config` reduced that to 2 processes and
  171.9 MiB, but removed the user's configured MCP/plugin capability surface.
- The installed and current npm release is Codex CLI 0.144.6. It still eagerly
  starts every enabled MCP server for each session; there is no supported lazy
  startup or shared-server switch. DeerFlow already terminates the complete
  child process group on success, failure, timeout, and cancellation, and the
  probe left no DeerFlow child process behind.
- Human Owner explicitly chose to retain all MCP, plugin, Skill, and agent
  capabilities. Therefore no `--ignore-user-config`, MCP filter, tool profile,
  reduced task concurrency, shared AI process, or programmatic tool gate was
  added. The measured memory is an accepted capacity cost until Codex provides
  lossless lazy/shared MCP lifecycle support, not a reason for program control
  over AI capabilities.
- Gateway `:8001` and Nginx `:2026` remain healthy and running for clean-window
  testing. The benchmark invoked only a no-tool Codex response; it made no
  production or business-system mutation and exposed no credential or customer
  data.

## 2026-07-19 — One-plan flow and professional audit roles restored

- A real restart exposed one remaining program-lifecycle defect: every pending
  Chair wake was retried after restart, even when its previous Run was
  `interrupted`, and a gracefully cancelled child was recovered into another
  automatic Chair Run. That revived the stopped audit twice and created two
  unwanted tasks before the Gateway was stopped.
- Corrected only the objective transport boundary. A cancelled child is now
  recorded with terminal wake fact `child_cancelled` without starting Chair;
  an interrupted or cancelled Chair wake is recorded as stopped and is not
  retried. Other genuine delivery failures retain the existing bounded retry.
  No prompt content is parsed, no result is accepted, and no AI workflow or
  quality gate was added.
- Focused background recovery, wake admission, Chair prompt, and stop-boundary
  regression: **43 passed**; full backend `make lint` passed. Live restart then
  settled the cancelled task without creating a new Run or an eleventh task;
  Gateway, frontend, and Nginx are healthy at `http://localhost:2026`.
- The Human Owner stopped the low-value real-project audit. Three independent
  audit children completed and preserved their reports; the overloaded backend
  child ran for about 51 minutes and ended without a final message, after which
  the old Chair attempted one retry. That retry and the local Gateway were
  stopped so the stale Thread could not continue waking or spending resources.
- The observed first step—one Planner—was correct. The immediately preceding
  “six-way planning wave” entry overcorrected a task-count concern and is now
  explicitly superseded. The AI-owned organization flow is one Planner → one
  Opposition → Chair execution plan → human discussion and explicit
  natural-language confirmation → plan-directed parallel execution. After a
  phase report changes the route, it is Project Manager → Opposition → Chair
  next-stage plan → another human discussion pause. Programs do not enforce
  either sequence. This planning sequence applies only to a new substantive
  execution plan or material revision; confirmed-plan execution, ordinary
  fixes, stopping low-value work, and bounded optimizations continue directly.
- Six outstanding children remain resource capacity, never a quota. The Chair
  chooses the useful plan-derived count and should split independently
  separable professional domains instead of overloading one child. Every child
  handoff now requires exact working, input, and output paths.
- Added fixed, reusable role charters and method Skills for runtime reliability,
  persistence and migrations, frontend protocol, security, and platform
  operations. These labels inject professional prompt context only; they do not
  grant tools, route work, score results, or approve completion. In particular,
  the previously overloaded backend audit can now be split into runtime and
  persistence work without inventing tasks merely to fill six slots.
- Aligned repository/backend/frontend instructions, the Chair prompt and Skill,
  English and Chinese user documentation, role API expectations, and the
  NextOS SkillOpt scenarios. The focused prompt/role/task/background regression
  selection passed **195** tests with one upstream TestClient deprecation
  warning; the later confirmed-plan boundary correction passed **43** focused
  tests. The full backend `make lint` and targeted MDX Prettier checks passed;
  SkillOpt hard/soft was **1.0** on train, validation, and test splits; `git
  diff --check` passed.
- No scheduler, queue, retry, persistence, API, Goal Cell, History, or frontend
  runtime behavior changed in this correction. No production or external
  business system was accessed, and no credential or customer data was exposed.
  The normal local Gateway remains intentionally stopped pending a clean new
  pilot conversation or an explicit decision about the stale Thread.

## 2026-07-19 — Superseded: broad planning restored to a six-way parallel wave

- Superseded by the correction above. Kept only as development history; do not
  use this entry as the current organization contract.

- The first real-project conversation dispatched only one Planner because the
  Chair prompt required that single proposal to return before Opposition or
  execution. Human Owner clarified that the established planning flow remains,
  but broad planning and information collection must not be serialized behind
  one Planner.
- The AI-owned flow is now parallel planning and fact finding → Opposition →
  Chair plan → parallel execution → completion. Broad work may use all six
  Command Room slots in its planning wave; the Gateway still supplies only the
  existing content-blind 6/12/64 resource capacity and makes no AI decision.
- Removed the singular Planner bottleneck from the Chair prompt and aligned the
  repository/backend contract, Commander Skill, user documentation, focused
  tests, and SkillOpt wording. No background scheduler, persistence, API,
  Goal Cell, History, or Project Manager runtime code changed.
- Focused prompt/role/task/background tests: 55 passed. Ruff, targeted MDX
  Prettier, and the NextOS SkillOpt probe passed; SkillOpt hard/soft remained
  1.0 on train, validation, and test cases. The already-running old Gateway was
  not restarted while its child process remained active.

## 2026-07-19 — V3 基础能力最终收口与本地 Gateway 核查

- 按授权仅运行 Ruff 对 `backend/app/gateway/command_room_background.py` 的格式化；文件已符合格式，未产生逻辑或文本变化。
- 全仓 `backend make lint` 全绿：Ruff check 通过，744 个文件格式检查通过。
- 隔离本地 replay Gateway 的真实 HTTP 健康检查、专用 thread 创建和 owner-scoped History 路由可达性通过；正常本地 Gateway 无授权会话返回预期 401，因此未伪造会话或扩大权限，带 owner 的多版本/结果/ack/Chair 全链路证据留待 Human Owner 提供本地测试会话后再做。
- 前端单元 829 passed；Work Record Playwright 6 passed。未访问生产或外部业务系统，未修改业务逻辑。
- 未发现需要第三切片处理的 P0。按 Human Owner 授权停止在第三切片确认点，建议转入真实项目使用。

## 2026-07-19 — NextOS V3 second slice: Workspace factual history and context recovery

- The Human Owner confirmed the bounded second slice: Workspace factual history
  and context recovery with only minimal Prompt, Skill, and read-only cockpit
  projections. P1/Later items and every explicitly excluded workflow,
  organization-graph, RAG, persistent-queue, approval, scoring, automatic-role,
  and automatic-project-manager idea remain unimplemented.
- Reused the existing append-only `workspace_events` store without a table,
  migration, or event type. SQL and memory stores now expose an exact-owner,
  newest-first, 1–100-item history page with an exclusive revision cursor and
  unchanged body, metadata, hash, author Run, and timestamp values.
- Added the Chair-only `read_goal_workspace_history` tool and owner-scoped
  `GET /api/threads/{thread_id}/goal-workspace/history`. The existing current
  context remains latest Mandate/Brief/Map plus the pending Inbox; the new route
  contains all factual event types, including previously acknowledged complete
  results and notification/acknowledgement delivery facts. Reading changes
  nothing, and acknowledgement remains neither acceptance, completion, deletion,
  nor hiding.
- Added a lazy Work Record History section. It loads no history until expanded,
  then reads bounded pages of raw facts and can load older pages. It adds no
  approval, acceptance, acknowledgement, dispatch, priority, retry, phase, or
  completion control. Chair/Project Manager handoff text now states that history
  is selected by the Chair, Brief is a current AI-owned index, and Goal Cells/
  PMs receive only Chair-selected facts.
- Validation: relevant backend Workspace/API/background/Goal Cell/prompt/role
  regression selection passed **235** tests (one upstream TestClient deprecation
  warning); frontend unit suite passed **829** tests; `pnpm check` and targeted
  Prettier passed; the six Work Record Playwright scenarios passed; `git diff
  --check` passed; and the NextOS SkillOpt static probe scored hard/soft **1.0**
  on train, validation, and both test cases.
- Targeted Ruff checks and formatting for this slice passed. The repository-wide
  backend `make lint` format check remains blocked only by the pre-existing,
  untouched formatting difference in `backend/app/gateway/command_room_background.py`;
  no queue behavior was changed here.
- No production or external business system was accessed or mutated. No
  credential, customer, or payment data was exposed. Stop at the third-slice
  confirmation point; do not extend this implementation without a new Human
  Owner decision.

## 2026-07-19 — NextOS V3 Goal-Mandate autonomous organization foundation

- The Human Owner confirmed the V3 product principle: people contribute
  interest, direction, possibility, real-world permission, and material
  boundaries; AI owns professional planning, opposition, organization,
  execution, comparison, correction, and completion inside that Goal Mandate.
  This entry supersedes earlier same-day requirements for per-plan human
  confirmation; those entries remain only as development history.
- Added an append-only, owner-scoped Goal Workspace event store and migration.
  Command Room can persist the complete opaque Goal Mandate and Current
  Operating Brief, and every later Run receives the current records without a
  program parsing or interpreting their prose.
- Added a durable Result Inbox. Every complete child return is persisted as a
  separate unchanged envelope; reads never acknowledge or delete it, explicit
  Chair acknowledgement is monotonic but is not acceptance, and concurrent
  completions may coalesce only their wake signal. Recovery preserves the
  result high-water mark and can finish notification after an already
  successful wake without replaying child work.
- Added recursive Goal Cells by reusing owner-scoped `command-room` Threads.
  Each Cell inherits the complete Goal Mandate, receives a local opaque Brief,
  can create narrower Cells through the same Chair loop, and explicitly returns
  its complete result and artifact references to the parent Result Inbox.
  Public metadata cannot forge the parent/root linkage.
- Added the factual Activity-panel view for Goal Workspace, complete Result
  Inbox envelopes, and the recursive Goal Tree in English and Chinese. The UI
  contains no approval, acceptance, acknowledgement, retry, or completion
  control for these records; runtime status is labelled only as a runtime fact.
  The user-facing Lead Agent and Subagent documentation now describes
  Goal-Mandate autonomy rather than plan-by-plan authorization.
- Capability and workspace references currently remain structural facts and do
  not grant permissions. Each Cell has an isolated Thread workspace. The next
  product decision is the default parent/child material boundary: sealed input
  snapshot, read-only parent view plus isolated writes, or shared writable
  state. No sharing or capability expansion was silently selected.
- The Human Owner selected the default material boundary: a Chair names exact
  parent files; the Gateway copies their bytes into the child Cell's sealed
  read-only input capsule, while the Cell keeps its own writable workspace and
  outputs. The parent file list, child capsule paths, SHA-256 values, and byte
  counts are objective transport facts. The program neither selects materials,
  interprets their content, assesses relevance or quality, grants AI
  capabilities, accepts a result, gates an AI-AI handoff, or decides whether
  work is complete.
- Goal Cell `task` execution now always uses `workspace-write`, even on a local
  development profile that otherwise permits unrestricted host access. The
  child Chair's direct read-only tools are likewise kept to the child's virtual
  thread paths, so they can read the sealed capsule but not fall through to a
  direct host path. This is an access-isolation boundary only: it prevents a
  child from modifying the parent capsule or unrelated host state; it does not
  impose a role, workflow, approval, score, result check, or any other program
  judgment. A server-owned durable Cell marker supplies the capsule path only
  when a snapshot exists.
- Validation for the sealed-capsule increment: 245 focused backend tests passed,
  including exact-byte snapshot immutability, idempotent recovery after a parent
  source changes, read-only file mode, context injection, and forced child sandbox scope; Ruff
  lint/format passed. The NextOS SkillOpt static probe remained hard 1.0 on its
  train, validation, and two test cases.
- Validation: backend full suite 6089 passed and 19 optional tests skipped;
  focused persistence, recovery, recursion, prompt, role, wake, router, and
  migration suite 221 passed; Ruff lint and format checks passed. Frontend unit
  suite 827 passed, `pnpm check` and targeted Prettier passed, and the changed
  desktop/mobile Activity workflow passed 6 Playwright scenarios with an
  inspected browser screenshot. The NextOS Skill validator passed; the new
  SkillOpt static probe scored hard 1.0 on train, validation, and two test
  cases.
- Restarted the local development stack. Gateway startup applied migration
  `0013_factual_round_records -> 0014_goal_workspace_events`; Gateway, frontend,
  and Nginx returned HTTP 200, while an unauthenticated Goal Workspace request
  returned the expected HTTP 401. Docker Desktop already owned port 3000, so
  the project launcher correctly selected frontend port 6001 behind Nginx
  `:2026`.
- A first embedded-client AI-behaviour probe exposed that the Planner and
  Opposition override resolved to the Codex CLI model id `gpt-5.6-sol`, which
  this local CLI account could not start. Both roles now use the already proven
  local `gpt-5.6-terra` transport at maximum reasoning. The rerun completed the
  intended Planner -> Opposition -> Chair synthesis -> Fact Finder -> Chair
  result sequence without plan-by-plan human confirmation. The embedded client
  intentionally bypasses Gateway persistence, so it was not used as Result
  Inbox evidence.
- A separate authenticated local Gateway smoke thread
  `v3-gateway-smoke-1784448869` then proved the actual V3 transport. The first
  Chair Run durably wrote one Goal Mandate and one Operating Brief, dispatched
  one background Fact Finder, and ended. The child result was independently
  appended unchanged; the automatic wake created one second successful Chair
  Run, which explicitly acknowledged the result and wrote a revised Brief. The
  notification fact was recorded only after that wake succeeded. SQLite shows
  the six append-only facts in order: mandate, brief, result received,
  acknowledgement, revised brief, notification. No result body was merged or
  interpreted by the program.
- The post-fix prompt/role selection passed 42 focused tests, Ruff passed, the
  NextOS Skill validator passed, and the SkillOpt static probe remained hard
  1.0 across train, validation, and two test cases. The one-off Gateway polling
  wrapper was interrupted after the durable success facts appeared because its
  exit predicate still required an unacknowledged result after the Chair had
  already acknowledged it; that wrapper condition did not affect either Run or
  the persisted events.
- The permanently frozen program-is-fact-only subsection was not changed. No
  production or live business system was accessed or mutated, and no
  credential, customer, or payment data was exposed.

## 2026-07-19 — NextOS V3 organization map, project manager, and factual capacity

- Added `organization.map.revised` as an owner-scoped append-only Goal
  Workspace fact. A Chair can record its complete temporary organization map;
  the latest opaque body is returned in later Chair context, `/goal-workspace`,
  and the existing read-only Work Record panel. The implementation reuses the
  generic Workspace events table, so it adds no schema or migration.
- Added the fixed `project-manager` professional role and its role package. It
  receives a Chair-provided Goal Mandate, Brief, Organization Map, and complete
  stage facts, then returns a proposed next stage, dependencies, temporary
  organization, risks, and handoff. It cannot dispatch work, alter Workspace
  facts, approve results, close a goal, or advance a program state. Its two
  role files are explicitly versionable alongside the other built-in NextOS
  role packages.
- Replaced the unbounded Command Room background child launch with one
  process-local FIFO resource pool: 12 child `execute` slots across the
  Gateway, 64 waiting slots, and at most 6 admitted queued-or-running children
  for one `(user, Command Room thread)`. Admission consults only identity and
  fixed numeric capacity; it never reads prompt or result content, chooses a
  role, ranks priority, judges quality, recognizes a phase, or decides a next
  AI action. A finished child releases its execution slot before its Chair wake
  runs, so waiting work is not held behind conversational wake handling.
- TaskLane now exposes raw `queued`, `running`, and `finished` execution facts
  using the existing factual handoff envelope. The pool is deliberately local
  to the single-Gateway topology: a restart still never invents a replacement
  Python callable or claims durable distributed queuing.
- Focused verification passed: 209 backend tests (one dependency deprecation
  warning), Ruff, 8 frontend unit tests, `pnpm check`, 6 Work Record Playwright
  scenarios, `git diff --check`, and the NextOS SkillOpt static probe with hard
  1.0 on train, validation, and test cases. No production or live business
  system was accessed or mutated, and no secret, customer, or payment data was
  exposed.

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
