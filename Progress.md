# Progress

## 2026-07-13 — Feishu Command Room independent top-level tasks

- Command Room 的飞书顶层消息不再消费旧的 pending clarification 状态，因此每条新任务保留自己的 `message_id` / `topic_id`，可与其他任务并行；原飞书回复串仍沿用已有 DeerFlow thread。
- 非 Command Room Feishu 的 plain-message clarification 兼容行为保持不变；复用现有 manager 并发上限 5，没有新增队列、配置项、数据库字段或依赖。
- TDD 先复现顶层消息被旧 topic 劫持的失败，再通过 Command Room 专用路由条件修复；完整 parser/channel 共 259 个测试通过，Ruff 与 Command Room contract check 也已通过。
- Deliberately skipped: no Gateway restart, no active-run cancellation, no configuration/credential change, no main-worktree modification, no live Feishu validation before integration.

## 2026-07-13 — Feishu Command Room long-run/error response correction

- Channel-triggered Command Room runs now receive the same `recursion_limit: 1000` default as the browser Command Room; an explicit default, channel, or user limit still wins.
- Channel streaming now relays Gateway's public terminal error payload instead of incorrectly returning `(No response from agent)` when a run ends without an AI final message.
- TDD coverage first reproduced both failures: the Command Room channel default was `100`, and an `error` stream frame was discarded. Focused regressions then passed, followed by the complete channel suite and Ruff checks.
- Deliberately skipped: no Gateway restart, no active-run cancellation, no configuration/credential change, no main-worktree modification, no commit/push, and no live Feishu validation before integration.

## 2026-07-13 — Command Room custom-role timeout precedence correction

- Removed three unconfirmed Command Room policies introduced in the local timeout fix: a mandatory direct-inspection rule for project reviews, a two-task total cap, and a shared five-minute delegation window. The existing per-response concurrency cap remains unchanged.
- Fixed custom Command Room role resolution so a locally configured `fact-finder` keeps its own timeout and turn limit; global defaults apply only when the selected configuration is genuinely built-in, while explicit per-agent overrides still take precedence.
- TDD first reproduced the failure: a custom 300-second `fact-finder` was incorrectly resolved to the global 1800-second timeout. The regression passes after the precedence fix.
- Validation: focused registry/task-tool/prompt suites passed `132`; full backend suite passed `6353` with `20` conditional skips; Ruff check and format check passed.
- Deliberately skipped: no service restart, active-run cancellation, config/credential change, deployment, push, or live-data mutation.

## 2026-07-13 — Command Room stale-goal loop correction

- Traced the repeated full-audit behavior to two live inputs: an owner-scoped legacy Command Room `SOUL.md` that required opposition/Round Card behavior, and a full 100-fact memory where equal-confidence old goals crowded out later corrections.
- Restored the active owner-scoped Command Room SOUL to natural goal-first behavior: the latest user turn wins, “继续/下一步” advances one previously identified bounded action, and routine work does not reopen full audits or review rounds.
- Changed memory retention and prompt selection to rank by confidence first and `createdAt` newest-first on ties, preserving newer explicit corrections without deleting historical memory records.
- Added a direct no-deferral prompt rule: identify a requested next step plainly, execute it only when requested, and do not postpone in-scope safe work to a future round.
- Validation: RED tests reproduced both stale-fact failures; GREEN memory suites passed 90 tests, Command Room prompt suites passed 30 tests, active SOUL assertions passed 3 checks, and the live AI-native probe passed all six behavior checks with no sub-AI/opposition dispatch or next-round deferral.
- Deliberately skipped: no Gateway/frontend restart, no current-run cancellation, no memory-history deletion, no database/config/credential change, no commit/push, and no full-repository audit.

## 2026-07-12 — Stable conversation-turn documentation

- Preserved the implemented single-conversation turn and reader-controlled scroll contract as a focused design record.
- Synced the user-facing behavior into both READMEs and the durable frontend architecture guidance.
- No runtime code, model behavior, persistence contract, or deployment behavior changed.
- Validation: `git diff --check` passed; `make skillopt-probe` kept baseline and candidate hard/soft scores at `1.0` and passed all three behavior decisions, with no automatic rule edits.

## 2026-07-12 — Single-node reliability closure

- Closed the one-Gateway/one-node reliability pass: terminal task state is stamped at its source, runtime snapshots are read-only and bounded, and frontend task-card/model-selection regressions are covered.
- Added a deterministic local AI-to-AI path that exercises lead delegation, subagent evidence writing, task-lane persistence, A → B → A conversation isolation, and reload recovery through the real Gateway and browser.
- Added `backend/scripts/runtime_value_report.py` for read-only, aggregate-only SQLite evidence covering outcomes, task duration, token distribution, and artifact coverage; its output omits prompts, responses, IDs, and artifact paths.
- Validation: backend lint/format plus the full 6,281-test backend suite (20 skipped); frontend check/format plus 748 unit tests, 83 mock-browser tests, and 8 real-Gateway browser tests all passed.
- Deliberately kept the deployment boundary: one Gateway and one node; no Redis, multi-worker runtime, migration, external dependency, production mutation, or automatic multi-hop routing.

## 2026-07-07 — Command Room confirmed-plan local execution rule

- Clarified that after the user confirms a plan and boundary, Command Room should autonomously continue local low-risk code, tests, docs, reversible validation, and evidence gathering instead of repeatedly asking about ordinary technical details.
- Kept STOP_CONFIRM for major risk or permission expansion: production/customer-visible effects, secrets or customer/payment data, destructive cleanup or history/evidence deletion, real provider cost or external side effects, deploy/public exposure, architecture commitment changes, and bottom-boundary changes.
- Synced the rule into root `AGENTS.md` and `docs/command-room/state/` guidance while preserving boundary, evidence, and opposition checks and rejecting worker self-claims as PASS evidence.
- Validation: grep/read verification and `git diff --check`.
- Deliberately skipped: no system prompts or hidden config, no personal notes, no credential/raw-user-data access, no history deletion, no runtime behavior change, and no external/provider side effects.

## 2026-07-06 — Command Room P1/P2 operating plane closure

- Integrated runtime snapshot self-heal detail telemetry so `recovery.snapshot_self_heal` reports repaired round/task counts and low-sensitive row IDs/status while preserving the old `repaired` flag.
- Added owner-scoped Command Room role state and pending handoff audit records, with run APIs and compact internal context injection. Role state is Chair-accepted memory; pending handoffs are AI-authored next-role suggestions for Chair review only.
- Wired the task tool to record completed worker outputs with `Target Role` as pending handoff suggestions without dispatching the next role.
- Hardened Codex provider stream-incomplete handling by treating `CodexStreamIncompleteError` as transient, hiding the internal `response.completed` detail from user-facing error copy, and committing new-chat route state when an error still carries run metadata.
- Validation: backend targeted tests passed with 251 tests and 2 dependency warnings; frontend targeted tests passed with 127 tests; backend `ruff check .`, backend `ruff format --check .`, frontend `pnpm check`, `git diff --check`, and `git diff --cached --check` passed.
- Deliberately skipped: no UI management surface, no metrics/dashboard/alerting, no automatic dispatch/rework/quality verdict, no production/live mutation, and no push.

## 2026-07-05 — Runtime chat recovery hardening

- Hardened the chat recovery path around the current user goal: visible history now relies on the backend display contract, runtime snapshot hydration, per-run message pages, task-lane recovery, terminal notices for terminal runs without visible AI replies, and known-run stream-error recovery instead of clearing chat state on transient stream disconnects.
- Backend recovery state is closer to one authority: `/api/threads/{thread_id}/runtime-snapshot` aggregates owner-scoped runs, run-message pages, round state, and task lanes; SSE durable replay and live `END_SENTINEL` handling synthesize `run.terminal` from terminal run rows when the custom terminal event is missing; worker-lost/lease-recovery reasons normalize to `worker_lost`.
- Closed the adjacent stale-native-state recovery gap: if the runtime snapshot sees a terminal run while native round_state is still active or a task lane is still `in_progress`/`running`/`pending`/`executing`, it now persists a minimal convergence before returning the snapshot, so reload cannot recreate fake running state from old round/task rows.
- Fixed two run-list roots that could make frontend recovery point at the wrong run or miss a run: `RunManager.list_by_thread()` now fetches a full store `limit` before memory/store merge, and memory/SQL stores return deterministic newest-first order with tie-breaks.
- Fixed frontend stream-error recovery ownership: known-run disconnects keep the visible run busy while probing, hide the transient error from the input state, but bounded probe exhaustion or permanent auth/not-found responses now clear local fake streaming ownership and invalidate run/thread lists instead of leaving the UI stuck forever.
- Validation: targeted backend SSE, run-manager/store/repository, runtime snapshot, worker-terminal, rollback, and thread-router tests passed; targeted frontend thread history/recovery unit tests and typecheck passed; `pnpm exec playwright test -c playwright.real-backend.config.ts` passed with the real frontend against the replay Gateway, covering runtime snapshot reload, hidden internal rows, terminal no-reply notice, task-lane subtask recovery, and replayed model render.
- Deliberately skipped: no P1 capability hard boundary, no evidence provenance rewrite, no role/governance engine changes, no production/live operation, and no new external dependency.

## 2026-07-05 — Phase 7 Runtime Chair Brief Wiring

- Wired the compact Chair Operating Brief into command-room lead runtime context through `CommandRoomRoundContextMiddleware`, using the run's `Runtime.context` thread/run/round facts instead of requiring Chair to query the API manually.
- Kept the brief internal, compact, and fact-only: it is not a gate, does not choose the next step, and does not dispatch reviewers or rework.
- Added regression coverage for empty data returning mechanical `known_gaps` and missing `thread_id` skipping runtime context injection without blocking the run.
- Synced `README.md` and `backend/AGENTS.md` to describe Phase 7 as runtime context wiring, not an automatic governance layer.
- Validation: `cd backend && uv run pytest tests/test_round_context_injection.py tests/test_command_room_chair_brief.py tests/test_run_chair_brief_api.py tests/test_lead_agent_prompt.py -q` passed with 39 tests and 2 dependency deprecation warnings; `cd backend && uv run ruff check packages/harness/deerflow/command_room/brief.py tests/test_command_room_chair_brief.py tests/test_round_context_injection.py` passed; `cd backend && uv run ruff format --check packages/harness/deerflow/command_room/brief.py tests/test_command_room_chair_brief.py tests/test_round_context_injection.py` reported 3 files already formatted.
- Deliberately skipped: no UI, no Browser replay, no new external dependency, no `task()` public return change, no raw prompt/result injection, no program PASS/FAIL, no automatic reviewer/opposition dispatch, and no automatic rework.

## 2026-07-05 — Phase 6 Chair Operating Brief read model

- Added `ChairOperatingBrief` as a compact AI-readable read model over existing handoffs, evidence refs, capability snapshot version, quality signals, review invocations, account proposals, and Chair decisions.
- Added owner-scoped `GET /api/threads/{thread_id}/runs/{run_id}/chair-brief` with optional `round_id` and `task_id` filters; it reads existing state and writes no durable decision record.
- Extended command-room round context injection with a short internal Chair Operating Brief block while keeping existing capability, native round, quality, review, account, and legacy round signal blocks.
- Added mechanical `known_gaps` only for missing capability snapshot, evidence refs, or quality signals; the brief does not emit PASS/FAIL, choose next steps, dispatch reviewers, or trigger rework.
- Validation: `cd backend && uv run pytest tests/test_command_room_handoff.py tests/test_command_room_evidence_ref.py tests/test_command_room_quality_signal.py tests/test_command_room_review_invocation.py tests/test_command_room_account_ledger.py tests/test_run_evidence_api.py tests/test_run_quality_loop_api.py tests/test_run_review_invocation_api.py tests/test_run_account_ledger_api.py tests/test_round_context_injection.py tests/test_command_room_chair_brief.py tests/test_run_chair_brief_api.py -q` passed with 42 tests; targeted `ruff check` and `ruff format --check` passed for changed Python files.
- Deliberately skipped: no UI, no Browser replay, no new external dependency, no raw prompt/result storage, no program quality judgment, no automatic reviewer/opposition dispatch, no automatic rework, and no account auto-apply.

## 2026-07-05 — Phase 5 AI decision ledger records

- Added `AccountUpdateProposal` and `AccountDecision` as compact AI-authored governance records for Goal, Boundary, Decision, Evidence, Debt, and Learning account updates.
- Added owner-scoped thread audit storage at `audit/account_ledger.jsonl`; proposals and decisions are append-only records, not account writers.
- Added `GET/POST /api/threads/{thread_id}/runs/{run_id}/account-proposals` and `POST /api/threads/{thread_id}/runs/{run_id}/account-decisions`.
- Extended command-room round context injection with compact account ledger state only: account type, proposed-by role, Chair decision, target role, and created time.
- Validation: `cd backend && uv run pytest tests/test_command_room_quality_signal.py tests/test_command_room_review_invocation.py tests/test_run_quality_loop_api.py tests/test_run_review_invocation_api.py tests/test_round_context_injection.py tests/test_command_room_account_ledger.py tests/test_run_account_ledger_api.py -q` passed with 21 tests; targeted `ruff check` passed for changed Python files.
- Deliberately skipped: no automatic governance, no program quality judgment, no PASS/FAIL output, no auto-rework, no auto-apply, no automatic AGENTS/README/rules updates, no UI, no Browser replay, and no external dependency.

## 2026-07-05 — Phase 4 AI review invocation records

- Added `ReviewInvocation` as a compact AI-authored record for why Chair/lead asks `evidence_checker`, `opposition`, `synthesis_checker`, or `reviewer` to inspect a focused question, plus the returned short summary and evidence refs.
- Added owner-scoped thread audit storage at `audit/review_invocations.jsonl`; completion appends a new snapshot for the same `invocation_id` rather than mutating prior audit rows.
- Added `GET/POST /api/threads/{thread_id}/runs/{run_id}/review-invocations` and `POST /api/threads/{thread_id}/runs/{run_id}/review-invocations/{invocation_id}/complete`.
- Extended command-room round context injection with compact review invocation state only: reviewer role, status, focus, target role, and result summary.
- Validation: `cd backend && uv run pytest tests/test_command_room_quality_signal.py tests/test_run_quality_loop_api.py tests/test_round_context_injection.py tests/test_subagent_handoff_audit.py -q` passed with 23 tests; `cd backend && uv run pytest tests/test_command_room_review_invocation.py tests/test_run_review_invocation_api.py -q` passed with 5 tests; targeted `ruff check` passed for changed Python files.
- Deliberately skipped: no automatic reviewer/opposition dispatch, no program verdict, no automatic rework, no UI, no Browser replay, no external dependency, and no `task()` public return change.

## 2026-07-05 — Phase 3 AI-readable quality loop data plane

- Added `QualitySignal` as a compact AI-authored recommendation record with `continue`, `needs_more_evidence`, `needs_revision`, `escalate`, and `stop`; it deliberately has `quality_verdict: null`, `auto_rework: false`, and `programmatic_decision: false`.
- Added owner-scoped thread audit storage at `audit/quality_signals.jsonl`.
- Added `GET /api/threads/{thread_id}/runs/{run_id}/quality-context` to aggregate run handoffs, redacted evidence summary, capability snapshot, native round state, and existing quality signals for Chair/lead AI.
- Added `POST /api/threads/{thread_id}/runs/{run_id}/quality-signals` to save AI-authored recommendations without requiring strong evidence refs.
- Extended command-room round context injection with short compact quality signals only; no raw prompt, large text, reviewer scheduling, automatic rework, or program-side verdict was added.
- Validation: `cd backend && uv run pytest tests/test_command_room_handoff.py tests/test_command_room_evidence.py tests/test_command_room_evidence_ref.py tests/test_run_evidence_api.py tests/test_round_context_injection.py tests/test_command_room_quality_signal.py tests/test_run_quality_loop_api.py -q` passed with 30 tests; targeted `ruff check` passed for changed Python files.
- Deliberately skipped: no Command Room UI, no Browser replay, no automatic reviewer/opposition/rework trigger, and no program-owned quality gate.

## 2026-07-03 — Command Room Chair Activation Check landing

- Added Chair Activation Check as the minimal startup guard for DeerFlow architecture, AI-AI, role, loop, governance, quality, boundary, development execution, and durable-rule work.
- Required startup fields: Goal, Boundary, Evidence Standard, Capability Release, Risk Class, and Dispatch Plan.
- Small tasks may explicitly use `Dispatch Plan: none` with a reason, so simple work stays small and does not force a full role pipeline.
- Preserved the core boundary: this is Chair self-activation, not program-owned scheduling, automatic role dispatch, automatic PASS/FAIL, or a workflow engine.
- Synced the rule into the real command-room lead-agent prompt, run protocol, AI control protocol, root/backend `AGENTS.md`, the local Naxus Round skill, and the Naxus SkillOpt probe.
- Validation: `cd backend && uv run pytest tests/test_lead_agent_prompt.py -q` passed with 25 tests; targeted `ruff check` passed; SkillOpt JSON files parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` completed with train hard=1.0, val hard=1.0, test hard=1.0 across 17 test tasks.
- Deliberately skipped: no automatic scheduler, no mandatory full role pipeline for every task, no program-side quality judgment, and no program-owned PASS/FAIL gate.

## 2026-07-03 — Command Room governance account update protocol landing

- Added the Account Update Proposal rule for durable governance account changes.
- Proposal fields: account, proposed change, source envelope or EvidenceRefs, reason, requested Chair decision, and Recorder target.
- Chair is the only decision point for `adopt`, `revise`, `defer`, or `reject`; Recorder persists only Chair-accepted changes to appropriate durable targets.
- Preserved the program boundary: program logic may store account text, refs, and permissions, but must not auto-update accounts, promote temporary signals into durable decisions, or decide that evidence passes.
- Extended the Naxus SkillOpt probe with a governance-account-update task so future answers reject sub-AI direct account writes and program automatic account updates.
- Validation: SkillOpt JSON files parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` completed with train hard=1.0, val hard=1.0, test hard=1.0 across 16 test tasks; local RED/GREEN keyword check now finds Account Update Proposal rules in `docs/command-room/project-governance.md` and `skills/custom/naxus-round/SKILL.md`.
- Deliberately skipped: no account database, no account writer, no UI, no automatic project scoring, and no program-owned governance update loop.

## 2026-07-03 — Command Room round routing landing

- Added the Round Routing rule: when an AI Handoff Envelope returns to Chair, Chair chooses exactly one next move.
- The four allowed moves are: continue the same small loop, switch to another small loop, start the next round, or stop/ask with `STOP_CONFIRM`/`BLOCKED`.
- Preserved the runtime boundary: program logic may stop at hop limits or unavailable targets, but must not choose the next move from content quality, confidence, or project judgment.
- Extended the Naxus SkillOpt probe with a round-routing task so future answers preserve the four-way Chair decision and reject program-owned routing.
- Validation: SkillOpt JSON files parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` completed with train hard=1.0, val hard=1.0, test hard=1.0 across 15 test tasks; local RED/GREEN keyword check now finds Round Routing in `docs/command-room/ai-control-protocol.md` and `skills/custom/naxus-round/SKILL.md`.
- Deliberately skipped: no scheduler, no content-based program router, no automatic retry engine, and no program-side PASS/FAIL gate.

## 2026-07-03 — Command Room loop hierarchy landing

- Clarified the loop hierarchy: the Big Loop is that every material AI-AI chain must return information to Chair/Command Room before acceptance, revision, stop, or durable promotion.
- Defined Small loops as local control loops inside planning, decomposition, development, evidence/opposition, capability, freshness, debt, and learning; they may iterate internally but must return an AI Handoff Envelope to Chair.
- Fixed the "where to loop" rule: planning/decomposition state lives in `spec.md` or a named handoff file; development state lives in worktree/git diff, command output, logs, artifacts, and evidence refs; evidence/opposition state lives in `findings.md`; durable governance updates go through Chair and Recorder.
- Extended the Naxus SkillOpt probe with a loop-hierarchy task so future answers must preserve Big Loop, Small loops, `spec.md`, `findings.md`, worktree/git diff, and no worker/program self-approval.
- Validation: SkillOpt JSON files parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` completed with train hard=1.0, val hard=1.0, test hard=1.0 across 14 test tasks; local RED/GREEN keyword check now finds the loop hierarchy in `docs/command-room/ai-control-protocol.md` and `skills/custom/naxus-round/SKILL.md`.
- Deliberately skipped: no UI, no automatic scheduler, no program-owned loop engine, and no program-side PASS/FAIL gate.

## 2026-07-03 — Command Room project governance accounts landing

- Added `docs/command-room/project-governance.md` as the minimal durable governance surface for six accounts: Goal, Boundary, Decision, Evidence, Debt, and Learning.
- Rule: AI roles and Chair maintain these accounts; program logic only records, references, routes, and enforces permissions. The accounts are not automatic scoring, automatic PASS/FAIL, automatic rework, UI, or replacements for `spec.md`, `findings.md`, or `Progress.md`.
- Account ownership: Chair owns Goal and Decision; Boundary owns Boundary; Evidence owns Evidence; Opposition/Boundary/Evidence can raise Debt for Chair decision; Recorder promotes Learning into skills, `AGENTS.md`, or SkillOpt when durable and probe-worthy.
- Synced the rule into `docs/command-room/core-invariants.md`, the local Naxus Round skill, and the Naxus SkillOpt probe.
- Validation: SkillOpt JSON files parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 12 test tasks; `git diff --check` passed.
- Deliberately skipped: no runtime code, UI, automatic account file generator, automatic scoring, automatic PASS/FAIL, or automatic rework loop.

## 2026-07-03 — Command Room loop / evidence-standard update

- Source signal: Obsidian note `知识库/抖音/Loop比模型更重要/...7654899046544444687...md` says the useful pattern is not a stronger model alone, but a loop where planning, generation, and judging iterate against clear acceptance rules.
- Direction fit: This maps to DeerFlow Command Room's round model: define Goal/Boundary/Evidence Standard, dispatch useful sub-AIs only when they add signal, then synthesize and verify with concrete evidence.
- Prompt update: `backend/packages/harness/deerflow/agents/lead_agent/prompt.py` now tells `command-room` to make each round's acceptance/evidence standard concrete before execution, then compare `action_result`, command/test output, artifact paths, logs, or source refs back to that standard before completion.
- Path hygiene fix in the same prompt area: thread-specific local paths were replaced with stable `/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/319f6c6b-538d-4624-9713-25f2b9466a4f/user-data/*` and `/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/319f6c6b-538d-4624-9713-25f2b9466a4f/acp-workspace` guidance so generated prompts do not bake in a stale thread id.
- Rule sync: root `AGENTS.md`, `backend/AGENTS.md`, and `skills/custom/naxus-round/SKILL.md` now carry the same acceptance/evidence-standard rule.
- Validation: `cd backend && uv run pytest tests/test_lead_agent_prompt.py -q` passed with `24 passed`; `cd backend && uv run ruff check packages/harness/deerflow/agents/lead_agent/prompt.py tests/test_lead_agent_prompt.py` passed; `git diff --check` passed.
- Deliberately skipped: no new planner/generator/judge runtime, no extra default reviewer/opposition workflow, and no new project skill. Existing Round + subagent + Evidence Signal machinery is enough for this iteration.

## 2026-07-03 — WorkOS-style skill governance + SkillOpt probe

- Source signal: Obsidian note `Projects/Development/多Agent知识库/DeerFlow/workOS案例/workos-skills-evals-state-machine.md` says skill value comes from small failure-driven pitfalls, state/evidence gates, and evals; large encyclopedia skills can add noise.
- Global Codex rule update: `/Users/pingxia/.codex/AGENTS.md` now says default replies should be Chinese unless the user/artifact requires another language, and important project skills should be failure-driven, minimal, and SkillOpt-probed.
- DeerFlow project update: root `AGENTS.md` and `Makefile` now expose `make skillopt-probe`; new wrapper `scripts/skillopt-probe.sh` runs SkillOpt's reusable static template against the local `skills/custom/naxus-round/SKILL.md`.
- Probe assets: `docs/skillopt/naxus-round/{README.md,config.json,tasks.json,response_rubrics.json}` define realistic tasks and rules for minimal skills, no project encyclopedia, probe-after-skill-change, concrete evidence standards, worker self-claim rejection, opposition before non-trivial PASS, and no default PM/developer/QA/reviewer pipeline.
- Local skill update: `skills/custom/naxus-round/SKILL.md` now has a Skill Governance section requiring small failure-driven rules, no encyclopedia content, and `bash scripts/skillopt-probe.sh` after relevant changes. This file remains local because `skills/custom/*` is gitignored.
- Validation: `bash scripts/skillopt-probe.sh` passed; baseline train/val/test all scored hard=1.0 and soft=1.0, and the gate correctly rejected extra edits because no improvement was available.
- Deliberately skipped: no automatic WorkOS-style hard state machine for every task and no mandatory SkillOpt run for routine work; the probe is a project-quality gate for skill/rule/SOP changes.

## 2026-07-03 — DeerFlow bottom-boundary confirmation list

- Added a root `AGENTS.md` bottom-boundary confirmation rule: before touching DeerFlow direction or low-level safety/runtime boundaries, ask the user.
- Boundaries covered: Command Room MVP strategy; Lead Agent/subagent responsibility model; default reviewer/gate/dashboard behavior; the current trusted local host execution model; reintroduced or switched sandbox/isolation modes; host bash/direct host access, mounts, guardrails, and tool permissions; auth/CSRF/owner isolation; secrets/config/model/provider/OAuth/channel credentials; MCP install/promotion policy; database/checkpointer/run-events/stream-bridge/migrations/thread-data/uploads/artifacts/SSE/cancellation contracts; public/network exposure and live channel sends/writes; deletion/cleanup of logs, audits, ledgers, workspaces, DBs, migrations, generated evidence, or git history; new paid/external services, production integrations, customer/payment data flows, or turning the local learning setup into production behavior.
- Synced the same bottom-boundary rule into local `skills/custom/naxus-round/SKILL.md`: crossing these boundaries should return `STOP_CONFIRM` and ask first.
- Extended `docs/skillopt/naxus-round` with a bottom-boundary probe covering sandbox/isolation mode changes, host execution surface changes, model provider default changes, and public deployment.
- Validation: `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 3 test tasks; `git diff --check` passed.
- Intent: ordinary local code edits, docs, tests, read-only inspection, and safe validation can continue; changes that cross those bottom boundaries require user confirmation first.

## 2026-07-03 — Meta-rule boundary clarification

- Added AI-AI protocol, skill/AGENTS governance, required `Progress.md` records, SkillOpt probe policy, and loop/evidence-standard rules to DeerFlow's bottom-boundary list.
- Rule: when those meta-rules change, update `Progress.md` in the same change set; ordinary code-only edits do not require a Progress entry.
- Extended the Naxus Round SkillOpt probe with a meta-rule task covering AI-AI, skill rules, AGENTS rules, Progress, and loop/evidence drift.
- Validation: `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 4 test tasks; `git diff --check` passed.

## 2026-07-03 — Bear note core invariant capture

- Captured Bear note `DeerFlow最强定义` into `docs/command-room/core-invariants.md`.
- Preserved the core rule: Command Room is the main development AI, not a workflow system; runtime records facts, Round keeps working memory, signals remind, skill gives short experience, opposition challenges temporarily, and the lead AI judges.
- Extended the Naxus Round SkillOpt probe with a workflow-drift task covering Round-as-gate, `action_result` as worker form, standing opposition, encyclopedia skills, and program PASS/FAIL.
- Validation: `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 5 test tasks; JSON probe files parsed cleanly; `git diff --check` passed.

## 2026-07-03 — Command Room constitution landing

- Clarified the constitution: users provide intent, pain, preferences, constraints, and irreversible authorization/refusal; Command Room generates proposed direction, boundaries, evidence standard, execution, validation, and next step.
- Added the same rule to the local Naxus Round skill and SkillOpt probe so Command Room does not require the user to pre-own direction, boundary, or acceptance criteria.
- Validation: `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 6 test tasks; JSON probe files parsed cleanly; `git diff --check` passed.

## 2026-07-03 — Chair role-separation landing

- Clarified that Command Room/Chair should not generate a plan and approve it alone.
- Added standing Planner, Boundary, Evidence, and Opposition roles as governance responsibilities; executor sub-AIs remain disposable one-round selves.
- Extended the Naxus Round SkillOpt probe with a Chair-not-one-voice task.
- Validation: `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 7 test tasks; JSON probe files parsed cleanly; `git diff --check` passed.

## 2026-07-03 — Long-running AI-AI flow landing

- Clarified that DeerFlow needs long-running AI-AI execution, governance, and management flow, not AI-program/program-AI workflow.
- Standing Planner, Boundary, Evidence, Opposition, Recorder, and Chair are long-running AI governance roles with persistent memory/state across rounds; concrete model calls may be ephemeral.
- Program logic may host, record, route, persist, enforce permissions, and expose fact signals, but must not manage AI flow, judge project quality, or auto-trigger governance.
- Updated Command Room prompt anchors and SkillOpt with a long-running-AI-AI-not-program-flow probe.
- Validation: `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 8 test tasks; `cd backend && uv run pytest tests/test_lead_agent_prompt.py -q` passed with 24 tests; `cd backend && uv run ruff check packages/harness/deerflow/agents/lead_agent/prompt.py tests/test_lead_agent_prompt.py` passed; JSON probe files parsed cleanly; `git diff --check` passed.

## 2026-07-03 — Command Room role fixation landing

- Fixed the long-running Command Room roles as durable role surfaces instead of temporary prompts.
- Added `docs/command-room/roles.md` as the role registry for Chair, Planner, Boundary, Evidence, Opposition, and Recorder.
- Added lightweight role state templates under `docs/command-room/state/` so cross-round memory has an explicit place without storing secrets or noisy turn logs.
- Created local role skills under `skills/custom/command-room-{chair,planner,boundary,evidence,opposition,recorder}`. These remain local runtime skills because `skills/custom/*` is gitignored.
- Synced the role-definition, role-skill, and role-state rule into root `AGENTS.md`, `docs/command-room/core-invariants.md`, the local Naxus Round skill, and the SkillOpt probe.
- Validation: all six role skills passed `quick_validate.py`; SkillOpt JSON files parsed cleanly; `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 9 test tasks; `git diff --check` passed.
- Deliberately skipped: no runtime role scheduler, no program-managed AI flow, and no automatic PASS/FAIL governance; this round fixed the role operating surfaces and the regression probe.

## 2026-07-03 — Command Room run-protocol landing

- Added `docs/command-room/run-protocol.md` as the minimal operating protocol for Small, Ordinary, and High-impact rounds.
- Rule: small read-only/simple checks should not force the full role set; ordinary development uses Planner, Boundary, Evidence, Chair, Executor, Evidence, Chair; high-impact governance, permission, safety, production, credential, data, or destructive work requires separated Planner, Boundary, Evidence, Opposition, Chair, Recorder, and SkillOpt when rules or safety workflows changed.
- Synced the run-protocol pointer into root `AGENTS.md`, `docs/command-room/core-invariants.md`, `docs/command-room/roles.md`, the local Naxus Round skill, and SkillOpt probe assets.
- Validation: SkillOpt JSON files parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 10 test tasks.
- Deliberately skipped: no runtime scheduler, no program-triggered role pipeline, and no automatic AI-AI flow manager.

## 2026-07-03 — AI-to-AI handoff runtime clarification

- Corrected the run-protocol boundary: DeerFlow does need a thin runtime that guarantees AI output is handed directly to the next AI input across AI-AI-AI chains.
- Clarified the split: the sending AI chooses the next handoff, the receiving AI judges the payload, and program logic only carries the envelope, ordering, permissions, trace, and facts.
- Added the handoff payload minimum: source role, target role, task/question, evidence refs, output refs, boundary status, and recommended next decision.
- Synced this clarification into root `AGENTS.md`, `docs/command-room/core-invariants.md`, `docs/command-room/roles.md`, the local Naxus Round skill, and SkillOpt probe assets.
- Validation: SkillOpt JSON files parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 11 test tasks.
- Deliberately preserved: program logic still must not choose the next role, rewrite payloads, judge PASS/FAIL, or trigger governance from its own content judgment.

## 2026-07-03 — Thin handoff envelope code landing

- Extended the existing compact subagent handoff audit packet instead of adding a scheduler or queue.
- `extract_handoff_packet()` now preserves AI-to-AI envelope fields: source role, target role, task/question, evidence refs, output refs, boundary status, and recommended next decision.
- `record_command_room_round()` now backfills source role, target role, and task/question for older packet records before exposing them through `dispatchPlan[].handoffPacket`.
- Synced backend `AGENTS.md` so backend guidance says program logic may carry AI-authored handoffs, but must not choose the next role, rewrite payloads, judge project quality, or trigger governance from its own content judgment.
- Validation: `cd backend && uv run pytest tests/test_subagent_handoff_audit.py tests/test_command_room_round_record.py -q` passed with 26 tests; targeted `ruff check` passed; `make command-room-contract-check` passed; `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 11 test tasks.
- Deliberately skipped: no runtime role scheduler, no automatic role trigger, and no program PASS/FAIL decision.

## 2026-07-03 — Task prompt handoff envelope landing

- Added an `AI Handoff Envelope` block to the real `task` tool subagent prompt, so the receiving AI gets the same source role, target role, task/question, evidence refs, output refs, boundary status, and recommended next decision that audit extracts.
- Kept task stream events on the original prompt so UI/event consumers do not suddenly show the internal envelope.
- Reused the existing `extract_handoff_packet()` parser; no new scheduler, queue, or program role selector was added.
- Validation: `cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_handoff_audit.py tests/test_command_room_round_record.py -q` passed with 59 tests; targeted `ruff check` passed; `make command-room-contract-check` passed; `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 11 test tasks; `git diff --check` passed.
- Deliberately preserved: program logic carries the envelope but still does not choose the next AI role, rewrite the payload, or judge PASS/FAIL.

## 2026-07-03 — Automatic AI handoff chain landing

- Completed the minimal automatic AI-AI handoff loop in the `task` tool: when a completed subagent output contains an explicit `Target Role` that matches an available subagent, the runtime hands that output to the target subagent.
- The chain stops and returns to Chair/Command Room when the target is `Chair`/`command-room`, unavailable, or the hop limit is reached.
- Audit now records both input `handoff_packet` and completed-output `output_handoff_packet`, so the envelope can be traced on both sides of an AI handoff.
- Synced AGENTS/docs/skill/SkillOpt language: program logic may execute AI-authored `Target Role`, but must not choose the next role, rewrite payloads, judge project quality, or trigger governance from its own content judgment.
- Validation: SkillOpt JSON files parsed cleanly; `cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_handoff_audit.py tests/test_command_room_round_record.py -q` passed with 62 tests; targeted `ruff check` passed; `make command-room-contract-check` passed; `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 11 test tasks; `git diff --check` passed.
- Deliberately skipped: no program-selected role planning and no program PASS/FAIL decision.

## 2026-07-03 — Command Room runtime role registration landing

- Registered default runtime role subagents: `planner`, `boundary`, `evidence`, `opposition`, and `recorder`; `Chair`/`command-room` remains the return point, not a subagent.
- Each built-in role subagent points at its matching local role skill when available, such as `command-room-planner` and `command-room-boundary`.
- Preserved local override behavior for Command Room role subagents, so project-specific `planner`, `boundary`, `evidence`, `opposition`, or `recorder` configs can keep richer local prompts instead of being swallowed by built-in fallbacks.
- Added a full automatic role-chain test for `planner -> boundary -> evidence -> opposition -> Chair`.
- Synced AGENTS/docs/Naxus skill/SkillOpt language so role registration is part of the durable Command Room rule set.
- Validation: SkillOpt JSON files parsed cleanly; `cd backend && uv run pytest tests/test_subagent_skills_config.py tests/test_task_tool_core_logic.py tests/test_subagent_handoff_audit.py tests/test_command_room_round_record.py -q` passed with 107 tests; targeted `ruff check` passed; `make command-room-contract-check` passed; `make skillopt-probe` passed with train hard=1.0, val hard=1.0, test hard=1.0 across 11 test tasks; `skills/custom/naxus-round` passed `quick_validate.py`; `git diff --check` passed.
- Follow-up hardening: all runtime role subagents, not only `opposition`, can be overridden by project-local config using the same role name. Validation: `cd backend && uv run pytest tests/test_subagent_skills_config.py tests/test_task_tool_core_logic.py -q` passed with 80 tests; targeted `ruff check` passed; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` passed; `git diff --check` passed.
- Smoke: added a deterministic registry smoke in `tests/test_task_tool_core_logic.py` that uses the real built-in role registry and fake executor outputs to verify `planner -> boundary -> evidence -> opposition -> Chair` resolves with the expected role skills. Validation: `cd backend && uv run pytest tests/test_task_tool_core_logic.py::test_task_tool_smoke_uses_real_command_room_role_registry tests/test_task_tool_core_logic.py tests/test_subagent_skills_config.py -q` passed with 81 tests; targeted `ruff check` passed.
- Deliberately skipped: no program-selected planning and no Chair/command-room subagent.

## 2026-07-03 — Filesystem handoff artifact landing

- Extended the AI Handoff Envelope and compact handoff packet with `Handoff File` / `handoffFile` and `ArtifactRefs` / `artifactRefs`.
- The `task` tool now passes these file-artifact references into receiving subagent prompts and audit extraction preserves them for both input and output handoff packets.
- Updated built-in Command Room role prompts so Planner, Boundary, Evidence, Opposition, and Recorder include file handoff fields when continuing the AI-AI chain.
- Synced run protocol, core invariants, roles docs, root/backend `AGENTS.md`, local Naxus Round skill, and SkillOpt assets with the rule that important handoffs use disk artifacts such as `spec.md` and `findings.md` as shared state, not shared chat context.
- Validation: JSON SkillOpt files parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `cd backend && uv run pytest tests/test_subagent_skills_config.py tests/test_task_tool_core_logic.py tests/test_subagent_handoff_audit.py tests/test_command_room_round_record.py -q` passed with 108 tests; targeted `ruff check` passed; `make command-room-contract-check` passed; `make skillopt-probe` completed with train hard=1.0, val hard=1.0, test hard=1.0 across 11 test tasks; `git diff --check` passed.
- Deliberately preserved: the program carries file references but does not create the spec/findings content, choose the next role, judge PASS/FAIL, or trigger governance from its own content judgment.

## 2026-07-03 — Round artifact ownership landing

- Fixed the minimal round artifact contract without adding a runtime file generator.
- Round artifacts live in the current thread workspace, usually `command-room/<round-slug>/spec.md` and `command-room/<round-slug>/findings.md`; the exact path must come from the AI handoff, not program guessing.
- Planner drafts `spec.md`; Chair adopts or revises it; Evidence owns `findings.md`; Opposition adds blocking objections there when they affect Chair; Recorder promotes only durable decisions/rules into docs, skills, AGENTS, SkillOpt, or `Progress.md`.
- Synced the rule into run protocol, core invariants, roles docs, root/backend `AGENTS.md`, Naxus Round skill, Planner/Evidence/Opposition/Recorder role skills, and SkillOpt assets.
- Validation: SkillOpt JSON files parsed cleanly; `skills/custom/naxus-round`, `command-room-planner`, `command-room-evidence`, `command-room-opposition`, and `command-room-recorder` passed `quick_validate.py`; `make skillopt-probe` completed with train hard=1.0, val hard=1.0, test hard=1.0 across 11 test tasks; `git diff --check` passed.
- Deliberately skipped: no automatic `spec.md`/`findings.md` writer, no new storage directory, and no program-side path selection.

## 2026-07-03 — AI control protocol landing

- Added `docs/command-room/ai-control-protocol.md` to connect roles, process, loops, and rounds without adding UI or a program judge.
- Clarified the model: Chair/Command Room is the always-on controlling AI surface; role invocations are bounded and may end after one turn; the durable handoff is the AI Handoff Envelope, referenced files, and Chair-accepted account/state updates.
- Defined role angles for Planner, Boundary, Evidence, Opposition, Project Steward, Debt Curator, Freshness Keeper, Capability Governor, Learning Curator, Conflict Mapper, and Recorder. Only Chair decides.
- Defined AI judgment loops: Plan, Capability, Execution, Conflict, Freshness, Debt, and Learning. These loops route signals back to Chair and are not program gates.
- Defined rounds as bounded Chair-to-Chair cycles that may leave intent, boundary, capability release, `spec.md`, envelopes, `findings.md`, final Chair decision, and optional account/state updates.
- Synced pointers into core invariants, roles, run protocol, root/backend `AGENTS.md`, Naxus Round skill, and SkillOpt assets.
- Validation: SkillOpt JSON files parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` completed with train hard=1.0, val hard=1.0, test hard=1.0 across 13 test tasks; `git diff --check` passed.
- Deliberately skipped: no runtime registration for optional angle roles, no UI, no automatic scheduler, and no program-side PASS/FAIL gate.

## 2026-07-03 — Command Room angle role registration landing

- Registered runtime angle role subagents: `project-steward`, `debt-curator`, `freshness-keeper`, `capability-governor`, `learning-curator`, and `conflict-mapper`.
- Each angle role points at a matching local skill under `skills/custom/command-room-*` and can be targeted by AI Handoff Envelope `Target Role`.
- Preserved local override behavior for these angle role names, so project-local config can replace built-in prompts/skills.
- Added a real-registry smoke test proving `planner -> capability-governor -> Chair` routes through the `task` tool without program-selected planning.
- Synced role docs, core invariants, root/backend `AGENTS.md`, Naxus Round skill, and SkillOpt assets with the angle role runtime names.
- Validation: `cd backend && uv run pytest tests/test_subagent_skills_config.py tests/test_task_tool_core_logic.py -q` passed with 82 tests; targeted `ruff check` passed; SkillOpt JSON files parsed cleanly; six new angle role skills plus `naxus-round` passed `quick_validate.py`; `make skillopt-probe` completed with train hard=1.0, val hard=1.0, test hard=1.0 across 13 test tasks; `make command-room-contract-check` passed; `git diff --check` passed.
- Deliberately preserved: no UI, no automatic scheduler, no program-side role choice, and no program PASS/FAIL gate.

## 2026-07-01 — Feishu/Lark private-link handoff status

- Previous anonymous access to Feishu Wiki/Doc/Base links could misclassify private resources as unreadable.
- Added/loaded the `feishu-cli-boundary` skill and related subagent guidance; Feishu links now default to private handling.
- Prefer `HOME=/Users/pingxia /Users/pingxia/.npm-global/lib/node_modules/cli/scripts/run.js ... --as user` for the local authorized user-mode path.
- Verified the local authorization path can read Feishu documents that anonymous web access could not read.
- Future failures should be classified by link type, identity, tenant, permission, and command shape.
- Red lines: do not record or output token, webhook, chat_id, Authorization, Bearer, cookie, passwords, private keys, or `.env` contents; real send/write-table/permission/route changes or large-scale outbound actions require confirmation per the skill boundary.

## 2026-07-03 — New task startup branch landing

- Folded the Command Room pressure-test `NEEDS_MORE` finding into the Chair Activation Check.
- New high-impact task startup now requires `New Task Startup Branch` with exactly one of Direct, Clarify, Single Sub-AI, Multi Sub-AI, or Stop.
- Added `Minimum Evidence Action` so evidence gaps become the smallest next check or handoff instead of a vague continuation.
- Synced the rule into the real Command Room prompt, run protocol, AI control protocol, root/backend `AGENTS.md`, Naxus Round skill, SkillOpt config/rubrics, and prompt tests.
- Validation: watched the new prompt test fail first; then `cd backend && uv run pytest tests/test_lead_agent_prompt.py -q` passed with 25 tests; targeted `ruff check` passed; SkillOpt JSON parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` passed train/val/test hard=1.0 across 17 test tasks; `make command-room-contract-check` passed; `git diff --check` passed.
- Deliberately skipped: no automatic `spec.md`/`findings.md` generator, no new storage layer, and no program-side task-start router.

## 2026-07-03 — Startup Stop/Clarify boundary landing

- Tightened the new-task startup branches so `Clarify` is only for missing intent, boundary, required input, or authorization that cannot be safely discovered.
- Added the rule that Chair must not ask the user for facts the workspace, docs, logs, or safe read-only checks can discover; it should use `Minimum Evidence Action` instead.
- Defined `Stop` for bottom-boundary, destructive/live action, sensitive data exposure, plan/permission change, or real blockers.
- Synced the rule into the real Command Room prompt, run protocol, AI control protocol, root/backend `AGENTS.md`, Naxus Round skill, and SkillOpt config/rubrics.
- Validation: watched the new prompt assertions fail first; then `cd backend && uv run pytest tests/test_lead_agent_prompt.py -q` passed with 25 tests; targeted `ruff check` passed; SkillOpt JSON parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` passed train/val/test hard=1.0 across 17 test tasks; `make command-room-contract-check` passed.
- Deliberately skipped: no runtime auto-router and no program-owned decision tree.

## 2026-07-03 — Evidence strength grading landing

- Added a three-level evidence-strength label for Command Room decisions: Strong, Weak, and Unverified.
- Defined Strong evidence as reproducible refs such as command/test output, logs, artifacts, source refs, screenshots, or diffs.
- Defined Weak evidence as worker self-claims, summary-only output, stale refs, indirect refs, or unchecked assumptions; Unverified claims have no usable EvidenceRefs or cannot be checked inside the boundary.
- Set the rule that only Strong evidence can support `PASS`; Weak or Unverified evidence must route to `Minimum Evidence Action` or `NEEDS_MORE`.
- Synced the rule into the real Command Room prompt, run protocol, AI control protocol, root/backend `AGENTS.md`, Naxus Round skill, SkillOpt config/rubrics/tasks, and prompt tests.
- Validation: watched the new prompt assertions fail first; then `cd backend && uv run pytest tests/test_lead_agent_prompt.py -q` passed with 25 tests; targeted `ruff check` passed; SkillOpt JSON parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` passed train/val/test hard=1.0 across 17 test tasks; `make command-room-contract-check` passed.
- Deliberately skipped: no automatic evidence scorer and no program-owned PASS/FAIL gate.

## 2026-07-03 — EvidenceStrength handoff field landing

- Added `EvidenceStrength` to the AI Handoff Envelope passed by the `task` tool so sub-AI evidence strength travels with the handoff.
- Extended compact audit extraction to preserve `evidenceStrength` in input and output handoff packets, and to parse `EvidenceStrength` from Evidence Signal output.
- Updated built-in Command Room role subagent prompts so Planner, Boundary, Evidence, Opposition, Recorder, and angle roles emit `EvidenceStrength`.
- Synced run protocol, AI control protocol, core invariants, role docs, root/backend `AGENTS.md`, Naxus Round skill, and SkillOpt assets with the handoff field.
- Validation: watched new handoff/audit/role assertions fail first; then `cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_handoff_audit.py tests/test_subagent_skills_config.py -q` passed with 95 tests; targeted `ruff check` passed; SkillOpt JSON parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make skillopt-probe` passed train/val/test hard=1.0 across 17 test tasks; `make command-room-contract-check` passed; `git diff --check` passed.
- Deliberately preserved: program logic carries `EvidenceStrength` but does not judge evidence strength or decide PASS/FAIL.

## 2026-07-03 — Findings EvidenceStrength constraint landing

- Tightened Evidence and Opposition role skills so every `findings.md` claim or objection must include `EvidenceStrength: Strong/Weak/Unverified`.
- Added the same requirement to Naxus Round, run protocol, AI control protocol, role docs, root/backend `AGENTS.md`, and SkillOpt assets.
- Added a regression test that reads the local Evidence/Opposition role skills and fails if `findings.md` output loses `EvidenceStrength`.
- Validation: watched the new skill regression test fail first; then `cd backend && uv run pytest tests/test_subagent_skills_config.py tests/test_lead_agent_prompt.py -q` passed with 70 tests; targeted `ruff check` passed; SkillOpt JSON parsed cleanly; `command-room-evidence`, `command-room-opposition`, and `naxus-round` passed `quick_validate.py`; `make skillopt-probe` passed train/val/test hard=1.0 across 17 test tasks; `make command-room-contract-check` passed; `git diff --check` passed.
- Deliberately skipped: no findings.md writer and no program-side evidence scoring.

## 2026-07-03 — Default Authorization Boundary landing

- Added `Default Authorization Boundary` to the Chair Activation Check so new-task startup distinguishes released capabilities from authorization expansion.
- Defined the default: Chair/sub-AIs may use only the current `Boundary` plus named items in `Capability Release`; expanding to new write surfaces, live/external systems, credentials, customer/payment data, public behavior, paid services, production integrations, or bottom-boundary rules requires Boundary or Capability Governor signal and Chair decision before execution.
- Synced the rule into the real Command Room prompt, run protocol, AI control protocol, core invariants, root/backend `AGENTS.md`, Naxus Round skill, and SkillOpt config/tasks/rubrics.
- Validation: watched the new prompt assertion fail first; then `cd backend && uv run pytest tests/test_lead_agent_prompt.py::test_command_room_prompt_requires_chair_activation_check -q` passed; `cd backend && uv run pytest tests/test_lead_agent_prompt.py -q` passed with 25 tests; targeted `ruff check` passed; SkillOpt JSON parsed cleanly; `skills/custom/naxus-round` passed `quick_validate.py`; `make command-room-contract-check` passed; `make skillopt-probe` completed with train/val/test hard=1.0 across 17 test tasks; `git diff --check` passed.
- Deliberately skipped: no program-side authorization router, no automatic capability scorer, and no UI/dashboard.

## 2026-07-03 — Capability Governor signal format landing

- Added a fixed `Capability Boundary Signal` return shape to the Capability Governor role skill: requested expansion, current boundary/release, narrower release, expansion risks, stop-before, evidence strength/refs, Chair decision options, recommended decision, and `Target Role: Chair`.
- Clarified that Capability Governor does not authorize work; expansion still returns to Chair, and bottom-boundary/user-authorization expansion recommends `STOP_CONFIRM`.
- Synced the signal contract into Naxus Round, run protocol, AI control protocol, role docs, root/backend `AGENTS.md`, and SkillOpt config/tasks/rubrics.
- Validation: watched the new skill regression test fail first; then `cd backend && uv run pytest tests/test_subagent_skills_config.py::test_command_room_capability_governor_skill_defines_boundary_signal -q` passed; `cd backend && uv run pytest tests/test_subagent_skills_config.py tests/test_lead_agent_prompt.py -q` passed with 71 tests; targeted `ruff check` passed; SkillOpt JSON parsed cleanly; `command-room-capability-governor` and `naxus-round` passed `quick_validate.py`; `make command-room-contract-check` passed; `make skillopt-probe` completed with train/val hard=1.0 and test hard=1.0 across 18 test tasks.
- Deliberately skipped: no schema parser, no program-side capability adjudication, and no automatic authorization expansion.

## 2026-07-03 — Chair Capability Decision landing

- Added a fixed `Capability Decision` return shape to the Chair role skill for Capability Boundary Signal results.
- Chair now chooses exactly one of `keep current release`, `narrow release`, `ask user`, or `stop`, with signal ref, adopted capability release, reason, evidence strength, boundary status, and next role.
- Synced the decision contract into Naxus Round, run protocol, AI control protocol, role docs, root/backend `AGENTS.md`, and SkillOpt config/tasks/rubrics.
- Validation: watched the new Chair skill regression test fail first; then `cd backend && uv run pytest tests/test_subagent_skills_config.py::test_command_room_chair_skill_defines_capability_decision -q` passed; `cd backend && uv run pytest tests/test_subagent_skills_config.py tests/test_lead_agent_prompt.py -q` passed with 72 tests; targeted `ruff check` passed; SkillOpt JSON parsed cleanly; `command-room-chair` and `naxus-round` passed `quick_validate.py`; `make command-room-contract-check` passed; `make skillopt-probe` completed with train/val/test hard=1.0 across 18 test tasks.
- Deliberately skipped: no runtime schema parser, no program-selected capability decision, and no new authorization router.

## 2026-07-03 — AI-AI handoff fidelity landing

- Added a runtime handoff fidelity line to the `task` tool envelope: `Task Prompt` is the raw upstream AI output; envelope fields are index hints, not replacements.
- Added a chain-level regression test proving a downstream role receives the upstream AI's full raw natural-language judgment, not only extracted envelope fields.
- Clarified the same rule in Naxus Round, run protocol, AI control protocol, core invariants, root/backend `AGENTS.md`, and SkillOpt assets.
- Preserved the existing thin runtime behavior: upstream AI output is passed forward as the next role input; no field-completeness gate, schema parser, or program-side scoring was added.
- Validation: watched the new task-tool assertion fail first; then `cd backend && uv run pytest tests/test_task_tool_core_logic.py::test_with_ai_handoff_envelope_preserves_explicit_fields -q` passed; chain-level raw-output regression passed without runtime changes; `cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_handoff_audit.py tests/test_subagent_skills_config.py -q` passed with 98 tests; targeted `ruff check` passed; SkillOpt JSON parsed cleanly; `naxus-round` passed `quick_validate.py`; `make command-room-contract-check` passed; `make skillopt-probe` completed with train/val/test hard=1.0 across 18 test tasks.
- Deliberately skipped: no required field checklist, no form validation, and no program-side weakening of evidence based on missing fields.

## 2026-07-03 — Subagent concurrency default alignment

- Fixed an embedded-client drift where `DeerFlowClient` still defaulted `max_concurrent_subagents` to `3` while the lead agent prompt, middleware, and executor default were already `6`.
- Added a regression assertion so the embedded client passes the unified `MAX_CONCURRENT_SUBAGENTS` value into the prompt by default.
- Updated `backend/AGENTS.md` to say `MAX_CONCURRENT_SUBAGENTS = 6` and clarify that six is a maximum capability release, not a requirement to always create six subtasks.
- Validation: `cd backend && uv run pytest tests/test_client.py::TestEnsureAgent::test_creates_agent tests/test_client.py::TestEnsureAgent::test_command_room_client_uses_agent_config_boundaries tests/test_subagent_limit_middleware.py tests/test_lead_agent_model_resolution.py::test_make_lead_agent_reads_runtime_options_from_context -q` passed with 18 tests; targeted `ruff check` passed; precise search found no remaining `MAX_CONCURRENT_SUBAGENTS = 3`, client default `3`, or hardcoded "执行 3 个子任务" strings in runtime/frontend docs under the checked paths.
- Note: a broader `test_client.py` run still has an unrelated existing artifact-path test failure around absolute `.deer-flow/...` paths versus `/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/319f6c6b-538d-4624-9713-25f2b9466a4f/user-data/...` virtual paths.

## 2026-07-03 — Chair code reading and thinking budget landing

- Added Chair Code Reading Policy: Chair may sample decisive code refs for truth, boundary, or acceptance, but broad exploration belongs to Evidence, Boundary, Capability Governor, or Executor, then returns to envelope and Chair decision flow.
- Added Visible Thinking Budget: visible thinking/status should stay short and action-oriented; do not narrate long private deliberation.
- Lowered Command Room frontend default reasoning effort from `xhigh` to `high` when no explicit user choice is set; explicit `xhigh` is still preserved.
- Synced the rule into the Command Room prompt, Chair skill, Naxus Round skill, run/control docs, root/backend `AGENTS.md`, and SkillOpt config/tasks/rubrics.
- Validation: watched new backend prompt/skill tests and frontend reasoning default test fail first; then `cd backend && uv run pytest tests/test_subagent_skills_config.py::test_command_room_chair_skill_bounds_code_reading_and_visible_thinking tests/test_lead_agent_prompt.py::test_command_room_subagent_prompt_allows_single_sub_ai_delegation -q` passed; `cd frontend && pnpm exec rstest run tests/unit/core/threads/hooks.test.ts` passed with 11 tests; SkillOpt JSON parsed cleanly.

## 2026-07-03 — Recorder governance account persistence landing

- Tightened the Recorder role skill so Goal / Boundary / Decision / Evidence / Debt / Learning account changes are persisted only from Chair-accepted `Account Update Proposal` results.
- Clarified that Chair decides `adopt / revise / defer / reject`; Recorder persists only adopted or revised changes to the named `Recorder Target`.
- Added a Recorder state watchpoint for Chair-accepted governance account updates.
- Synced the governance account update rule into root/backend `AGENTS.md` so new sessions see the same durable rule.
- Preserved the existing boundary: program logic may record refs/permissions, but must not auto-update accounts, promote temporary signals, or decide that evidence passes.
- Validation: watched the new Recorder skill regression fail first; then `cd backend && uv run pytest tests/test_subagent_skills_config.py::test_command_room_recorder_skill_requires_chair_accepted_account_updates -q` passed; follow-up sync check found Account Update Proposal coverage in root/backend `AGENTS.md`.

## 2026-07-03 — Loop scenario library landing

- Added `docs/command-room/loop-scenarios.md` as the compact selection map for Command Room loops: New Task Startup, Plan, Development, Evidence, Capability, Conflict, Debt, Learning, and Six-Lane Audit.
- Clarified that loops are AI judgment loops, every material loop must return to Chair, and six lanes are an audit option/concurrency budget, not the default for every task.
- Linked the scenario library from `docs/command-room/ai-control-protocol.md` and `skills/custom/naxus-round/SKILL.md`.
- Added a SkillOpt probe for loop scenario selection: small factual tasks should stay Direct/Single Sub-AI, while broad protocol/release/refactor/governance audits may choose Six-Lane Audit when independent angles buy signal.
- Validation: watched the new loop-scenario regression test fail first because `docs/command-room/loop-scenarios.md` was missing; then `cd backend && uv run pytest tests/test_subagent_skills_config.py::test_command_room_loop_scenario_library_is_linked_and_bounded -q` passed; full `cd backend && uv run pytest tests/test_subagent_skills_config.py -q` passed with 50 tests; targeted `ruff check` passed; SkillOpt JSON parsed successfully; `make command-room-contract-check` passed; `bash scripts/skillopt-probe.sh` returned train/val/test hard=1.0 with test n=20, with `gate.accepted=false` because candidate and baseline tied at 1.0 and no auto-edits were applied; `git diff --check` passed.
- Deliberately skipped: no UI, no program-side loop router, no automatic quality scorer, and no rule that every task must dispatch six subagents.

## 2026-07-03 — Task event redaction and Chair handoff return landing

- Changed `task` stream events to default-redacted payloads: no raw `prompt`, raw `message`, raw `result`, or `action_result` is sent through SSE/task event persistence; events now carry `summary`, bounded `result_preview`/`error_preview`, `redacted: true`, usage, and artifact refs.
- Changed explicit `Target Role` handling so completed subagent output returns to Chair by default instead of automatically starting the next role. `Target Role` remains an AI-authored next-role recommendation for Chair to decide.
- Tightened frontend task event handling so visible task events require `task_id`, `thread_id`, and `run_id`, and use `result_preview` / `error_preview` while keeping old fields as compatibility fallback.
- Synced the new handoff boundary into run/control docs, core invariants, root/backend `AGENTS.md`, Naxus Round skill, and SkillOpt assets.
- Validation: watched backend tests fail first on raw event fields and automatic handoff chains; watched frontend tests fail first on missing `result_preview` support and missing run identity rejection; then `cd backend && uv run pytest tests/test_task_tool_core_logic.py -q` passed with 38 tests; `cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_handoff_audit.py tests/test_subagent_skills_config.py -q` passed with 101 tests; `cd frontend && pnpm exec rstest run tests/unit/core/threads/hooks.test.ts` passed with 13 tests; targeted `ruff check` passed; SkillOpt JSON parsed successfully; `make command-room-contract-check` passed; `bash scripts/skillopt-probe.sh` returned train/val/test hard=1.0 with test n=20, with `gate.accepted=false` because candidate and baseline tied at 1.0 and no auto-edits were applied; frontend Prettier check passed; `git diff --check` passed.
- Deliberately skipped: no auto-handoff config switch, no program-side Chair decision router, and no broad owner/checkpoint/run-manager/uploads changes in this P0 slice.

## 2026-07-03 — Deployment exposure hardening parallel patch observed

- Observed parallel workspace changes to `scripts/serve.sh`, `docker/docker-compose.yaml`, and `docker/nginx/nginx.conf` matching the read-only diagnosis priorities: local gateway bind by default, safe dotenv parsing instead of `source .env`, Docker nginx localhost bind by default, and opt-in exposure for docs/OpenAPI/sandbox API.
- Validation: `bash -n scripts/serve.sh` passed; `docker compose -f docker/docker-compose.yaml config` needs required env paths and fails with empty local env as expected, then passed when supplied dummy `DEER_FLOW_REPO_ROOT`, `DEER_FLOW_HOME`, `DEER_FLOW_CONFIG_PATH`, `DEER_FLOW_EXTENSIONS_CONFIG_PATH`, `BETTER_AUTH_SECRET`, and `DEER_FLOW_INTERNAL_AUTH_TOKEN`.
- Still open from the diagnosis: trusted forwarded-header policy, owner/checkpoint/run resolver consistency, uploads/artifacts user-id consistency, and RunManager persistence/concurrency hardening.

## 2026-07-03 — Run owner and thread-run resolver consistency landing

- Fixed regular authenticated run startup so `start_run()` persists the run, thread metadata, and background task context under the authenticated user id; trusted internal owner headers still take precedence for channel workers.
- Tightened `RunManager.get(..., user_id=...)` and `RunManager.list_by_thread(..., user_id=...)` so active in-memory runs honor the same owner filter as store-only persisted runs.
- Added `resolve_thread_run()` as the shared thread-scoped run resolver and moved `get/cancel/join/stream/messages/events` routes through it before reading event streams or mutating a run.
- The resolver rejects mismatched `thread_id/run_id` pairs and rejects runs with a foreign explicit `user_id`; `user_id=None` legacy rows remain readable behind the existing thread owner check.
- Validation: watched new RunManager user-filter tests, authenticated `start_run` persistence test, and mismatched thread/run route test fail first; then `cd backend && uv run pytest tests/test_run_manager.py tests/test_gateway_services.py tests/test_thread_run_messages_pagination.py tests/test_cancel_run_idempotent.py tests/test_stateless_runs_owner_isolation.py tests/test_runs_api_endpoints.py -q` passed with 153 tests.
- Still open from the diagnosis: trusted forwarded-header policy, uploads/artifacts user-id consistency, and deeper RunManager persistence/concurrency hardening.

## 2026-07-03 — Uploads/artifacts request storage user consistency landing

- Added request-scoped storage user resolution for filesystem paths: trusted internal owner header first, then `request.state.user.id`, then contextvar fallback.
- Updated artifact path resolution to pass the resolved storage user into `resolve_thread_virtual_path()` for both regular artifacts and `.skill` archive previews.
- Updated uploads list/upload/delete flows to use the same request storage user when resolving host upload directories and sandbox-visible paths.
- Added regressions proving internal channel requests with an owner header read the owner user's uploads/artifacts bucket instead of the synthetic/default user bucket.
- Validation: watched the new owner-header uploads/artifacts tests fail first; then `cd backend && uv run pytest tests/test_uploads_router.py tests/test_artifacts_router.py tests/blocking_io/test_artifacts_router.py tests/test_uploads_manager.py tests/test_memory_upload_filtering.py -q` passed with 107 tests.
- Still open from the diagnosis: trusted forwarded-header policy and deeper RunManager persistence/concurrency hardening.

## 2026-07-03 — Trusted forwarded-header policy landing

- Tightened CSRF origin and secure-cookie scheme detection so `Forwarded` / `X-Forwarded-*` headers are honored only when the TCP peer is inside `AUTH_TRUSTED_PROXIES`.
- Kept direct same-origin behavior unchanged and preserved trusted reverse-proxy deployments by requiring operators to configure the proxy CIDR/IP allowlist.
- Added a regression proving spoofed forwarded same-origin headers are rejected when no trusted proxy is configured, while trusted proxy requests still pass and set secure cookies for HTTPS forwarded requests.
- Validation: watched the untrusted forwarded CSRF test fail first; then `cd backend && uv run pytest tests/test_csrf_middleware.py -q` passed with 17 tests, and `cd backend && uv run pytest tests/test_auth_type_system.py -q -k "csrf or https or secure"` passed with 19 selected tests.
- Still open from the diagnosis: deeper RunManager persistence/concurrency hardening.

## 2026-07-03 — Deployment exposure defaults completion

- Closed the remaining dev-compose exposure gap: Docker dev nginx now binds `127.0.0.1` by default, with `DEER_FLOW_BIND_HOST` as the explicit opt-in for broader exposure.
- Made Docker dev generate the same nginx exposure flag file as production and run `nginx -t` before starting, so docs/OpenAPI and sandbox provisioner routes stay closed unless explicitly enabled.
- Changed Gateway config's bare default host to `127.0.0.1`; direct operators can still opt into `GATEWAY_HOST=0.0.0.0`.
- Documented the default-local bind and docs/sandbox opt-in flags in README and root `AGENTS.md`.
- Validation: added `backend/tests/test_deployment_exposure_defaults.py` and watched it fail first on dev compose and Gateway config defaults before the fix.

## 2026-07-03 — Task return and audit raw-leak hardening

- Preserved AI-AI natural-language handoff semantics while redacting obvious secret-like strings from the parent-facing `task()` terminal return and terminal error logs.
- Tightened subagent handoff audit records so `action_result.summary` and `action_result.error` are stored only as hashes and character counts; safe action metadata such as status, output refs, evidence refs, risks, conflicts, and open questions remains.
- Kept event-stream behavior unchanged: task events stay redacted and frontend consumers continue using `result_preview` / `error_preview`.
- Added `schema_version: deerflow.task-event/v1` to task events and the frontend event type so live SSE/history replay have an explicit contract marker.
- Changed live frontend subtask notifications to use a React functional state update, so concurrent task events merge against the latest task map instead of a stale closure; silent history-derived updates keep their no-render behavior.
- Validation: watched new task return and audit leak tests fail first; then `cd backend && uv run pytest tests/test_subagent_handoff_audit.py::test_record_subagent_handoff_omits_raw_payloads tests/test_task_tool_core_logic.py::test_task_tool_redacts_secret_like_text_in_parent_return -q` passed; `cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_handoff_audit.py -q` passed with 52 tests; frontend task event contract tests passed with 63 tests.
- Deliberately skipped: no schema parser, no evidence scorer, and no truncation of non-secret AI handoff text.

## 2026-07-03 — RunManager persistence and concurrent owner recheck hardening

- Reused the existing bounded persistence retry helper for `update_run_progress()`, so running token/message snapshots survive short SQLite lock pressure instead of being dropped after the first transient failure.
- Fixed a concurrent `get(run_id, user_id=...)` owner-filter race: if a store lookup awaited while the same run appeared in memory, the post-await in-memory recheck now applies the same `user_id` filter as the first check.
- Added failing-first regressions for both cases: transient progress-store lock retry and post-store-await owner filtering.
- Validation: `cd backend && uv run pytest tests/test_run_manager.py -q` passed with 56 tests; `cd backend && uv run pytest tests/test_gateway_services.py tests/test_thread_run_messages_pagination.py tests/test_cancel_run_idempotent.py tests/test_stateless_runs_owner_isolation.py tests/test_runs_api_endpoints.py -q` passed with 99 tests; targeted `ruff check` passed.
- Deliberately skipped: no new cross-process lock manager, no queue/reservation rewrite for run creation, and no Redis/shared stream bridge.

## 2026-07-03 — Audit follow-up fail-closed and event contract hardening

- 2026-07-05: Added configured Skill Catalog sources (`skillCatalogSources`) with read-only list/preview endpoints and admin-only catalog install that reuses the existing `.skill` scanner/installer, records source/hash history, and blocks high-risk entries with an approval-required response instead of mutating local skills.

- Fixed the frontend subtask context type so functional `setTasks(current => next)` updates pass `pnpm typecheck` and concurrent live task notifications keep merging against the latest state.
- Changed backend `make dev` / `make dev-reload` / `make gateway` to bind Gateway to `127.0.0.1` by default, with `DEER_FLOW_BIND_HOST` / `DEER_FLOW_GATEWAY_HOST` as explicit opt-ins.
- Added route-level `runs:create` permission to stateless `/api/runs/stream` and `/api/runs/wait`; body `thread_id` owner checks remain in `start_run()`.
- Tightened both `/wait` routes so checkpoint state is returned only when the final run status is `success`; terminal `error` / `interrupted` / other non-success states return `{status, error}` instead of looking successful.
- Encoded frontend upload `threadId` and `filename` path segments for upload/list/delete calls.
- Added compact redacted `action_result` to terminal task events and made task event emission recursively sanitize descriptions, artifact refs, and nested dict/list fields before SSE/journal persistence.
- Added route-level auth to Memory API routes so a bare-mounted router fails closed instead of falling back to a default memory owner.
- Validation: `cd frontend && pnpm typecheck` passed; upload API unit tests passed; targeted frontend Prettier passed; `cd backend && uv run pytest tests/test_deployment_exposure_defaults.py tests/test_stateless_runs_owner_isolation.py tests/test_task_tool_core_logic.py tests/test_command_room_round_context.py tests/test_command_room_task_action_result.py tests/test_memory_router_auth.py -q` passed in targeted runs; targeted backend `ruff check` passed.
- Deliberately skipped: no Skills API admin/user-tenancy change, no Target Role auto-chain implementation, no cross-process active-run lease, no run creation lock rewrite, and no rollback state-machine redesign in this mechanical follow-up slice.

## 2026-07-03 — Skills API admin-only governance landing

- Short-term model landed: global skills are admin-managed resources. `skills/custom`, `extensions_config.json` skill enablement, and the skills prompt cache remain global in this round.
- Made Gateway global skill management fail closed: install from thread artifact, custom skill list/raw content/edit/delete/history/rollback, and enable/disable all require admin.
- Kept authenticated user reads limited to safe skills summaries through `GET /api/skills` and `GET /api/skills/{name}`; raw custom skill content and history are not ordinary-user readable.
- Scoped install-from-thread artifact resolution through the request storage user after the admin check, avoiding default-user/anonymous source path fallback.
- Added regressions for ordinary-user 403, bare-mounted unauthenticated 401, admin lifecycle behavior, admin install source path resolution, and safe summary reads.
- Validation: `cd backend && uv run pytest tests/test_skills_custom_router.py -q` passed with 29 tests; `cd backend && uv run ruff check app/gateway/routers/skills.py tests/test_skills_custom_router.py` passed; `git diff --check` passed.
- Deliberately skipped: no per-user skills tenancy, no skills storage migration, no Target Role auto-chain change, no RunManager active-run lease/rollback state-machine work, and no frontend UI changes.
- Future SaaS/multi-tenant design must tenant-scope skills storage, `extensions_config`, prompt cache, MCP/skills activation, install source ownership, and audit/history together instead of adding a route-only `user_id`.

## 2026-07-03 — Command Room Target Role advisory hardening

- Tightened `task()` completion semantics: worker `Target Role` output is returned as `Suggested next receiver (advisory only)` for Chair/main AI, not resolved into an available subagent route.
- Removed built-in Command Room role prompts' fixed Planner -> Boundary -> Evidence -> Opposition -> Chair next-role defaults; roles now suggest a next receiver only when Chair/main AI asks or another AI angle is truly needed.
- Added regressions proving `Target Role: planner/boundary/evidence/opposition/chair` does not create a second task event, does not redispatch, and does not use routed/dispatched wording.
- Validation: `cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_skills_config.py -q` passed with 94 tests; targeted `ruff check` passed.
- Deliberately skipped: no automatic Target Role handoff, no fixed PM/Dev/QA/Reviewer pipeline, no Gateway permission changes, no RunManager/run store/stream/checkpoint/rollback/lease changes, and no skill loading policy work.

## 2026-07-04 — Task event/action_result contract convergence

- Pinned `deerflow.task-event/v1` around task terminal events and compact `action_result` metadata so completed, failed, cancelled, and timed_out preserve both status and terminal_reason across backend and frontend.
- Marked `task_running.message` optional/reserved because the current backend emits only message indexes/counts, not raw message payloads.
- Marked `boundary_blocked` as a reserved future task terminal reason; current task terminal emissions do not use it.
- Added backend/frontend regressions for schema v1 terminal cases, unknown future terminal states failing safe instead of staying in progress, cancelled staying `user_cancelled`, and timed_out staying `timed_out`.
- Validation: targeted backend pytest, frontend hooks/subtask-result tests, `pnpm typecheck`, targeted ruff/prettier, and `git diff --check` passed.
- Deliberately skipped: no RunManager/run store/stream persistence/checkpoint/rollback/lease/permission-model/Target Role auto-chain/skill-loading-policy changes.

- 2026-07-04: 整理 artifact URL encoding/base URL fallback 测试，合并重复 artifacts-utils 覆盖；验证 `npm test -- tests/unit/core/artifacts/utils.test.ts` 9/9 通过，`pnpm exec prettier --check tests/unit/core/artifacts/utils.test.ts src/core/artifacts/utils.ts` 通过。

## P1 handoff evidence ledger minimal landing

- Added native task-lane list fields for evidence/artifact/output refs while retaining legacy single `evidence_ref`/`result_ref` compatibility.
- Clarified round-state read boundary: API quality context now prefers native round-state rows and uses `RunRecord.metadata.round_context` only as fallback/bootstrap.
- Updated command-room operating rules in AGENTS/skill docs: parallelize independent work, AI-first discovery, DeerFlow edits in isolated worktrees, durable evidence, advisory-only signals, no automatic PASS/FAIL/dispatch/rework.

## 2026-07-05 - Capability Boundary Center

- Added `capability_center` to the AI-readable capability snapshot with current release facts, normalized stop-before boundaries, permission facts, stable evidence refs, and explicit advisory-only/non-decision flags.
- Locked regressions for field presence, legacy compatibility, stop-before reuse, non-secret exposure, and no automatic authorize/reject/PASS/FAIL/dispatch/rework decisions.

## 2026-07-10 - Command Room execution capability recovery

- Root cause was not one weak model response. Historical audit data showed 1,744 completed subagent tasks with zero `action_result.evidence_refs`, while handoff normalization produced far more REDLINE than SUPPORTED signals (527 versus 42). Runtime-observed edits and tests were being reduced to worker prose, then broad redline matching and mandatory governance language sent the Chair back into review loops.
- Added a runtime evidence bridge from paired `AIMessage.tool_calls` and actual `ToolMessage` results into `SubagentResult`, terminal `ActionResult`, task events/audit, and the parent-facing `task()` result. Evidence includes bounded/redacted commands, exit codes, paths, statuses, and output hashes. Orphan tool calls are ignored; commands containing credential markers are replaced with a command hash instead of exposing command text.
- Kept model-claimed `evidence_refs` untrusted. Runtime-observed refs override worker formatting, while worker prose remains a claim. Natural implementation output no longer becomes REDLINE merely because it mentions ordinary writes, and missing structured `RecommendedDecision` no longer invalidates an otherwise grounded handoff.
- Changed successful Command Room runs to close the native round. The previous AI answer is no longer copied into `next_action`, raw `next_action` is not treated as accepted user intent, and a follow-up starts a child round from the new user message. Command Room graph input also clears stale Todo state inherited from another assistant.
- Repaired Skill loading for this installation: ignored local `config.yaml` now uses `skills.path: skills` and `/mnt/skills`; a real loader check found 36 enabled skills including `naxus-round` and all core Command Room role skills. Role-specific subagents receive their declared role Skill, while a general implementation subagent keeps only the parent allowlist and does not inherit the whole governance-role set.
- Reframed Planner, Boundary, Evidence, Opposition, and Recorder as risk-triggered capabilities rather than mandatory ordinary-development lanes. Ordinary local work now defaults to one implementation lane plus focused acceptance verification, stops when the evidence standard is met, and preserves chronology so later unsupported criticism cannot erase observed edits or passing commands.
- Removed the offline contract's universal `PASS requires opposition or oppositionExemption` rule. A PASS still requires concrete non-self-attested evidence and remains invalid when an invoked Opposition supplies a blocking signal, but runtime evidence can now close an ordinary round without manufacturing an Opposition task or exemption form.
- Updated root/backend `AGENTS.md`, Command Room protocol/state docs, README/README_zh, the tracked Chair Skill, the ignored local `naxus-round` Skill, and SkillOpt assets. Added a SkillOpt scenario for stopping after observed implementation instead of dispatching review/evidence/opposition/commit lanes to complete a sequence.
- Validation: failing-first regressions covered runtime evidence promotion, sensitive-command redaction, clean child rounds, stale Todo clearing, role-Skill routing, general-agent Skill isolation, and PASS without Opposition. The changed-behavior regression group passed 418 tests; the final full backend suite passed 5,806 tests with 20 conditional skips; final contract/round-record tests passed 21 tests. Targeted Ruff passed, all changed JSON parsed, `git diff --check` passed, `make command-room-contract-check` passed with `pass_with_runtime_evidence_without_opposition`, and `quick_validate.py` accepted `naxus-round`.
- SkillOpt: train/val/test hard and soft scores are all 1.0 with 21 test tasks. `gate.accepted=false` is the expected no-op tie because the baseline already satisfies the updated rules and no automatic edit was needed.
- No MCP server was installed or promoted, no live/production system was accessed or mutated, no secret was exposed, and no commit/deploy was performed. Existing unrelated frontend worktree changes were preserved untouched.

## 2026-07-10 - Full-stack stability audit and repair closure

- Closed the remaining execution-loop gaps: subagents now have real loop detection, model-call limits, and structural recursion limits instead of review-language heuristics. Successful work can stop on observed implementation evidence, while boundary confirmation remains reserved for actual production, credential, financial, or public-impact expansion.
- Repaired durable runtime continuity across process and page boundaries: SSE accepts `Last-Event-ID: -1` and replays persisted events, runtime paths normalize under `backend/.deer-flow`, `doctor` reports split legacy roots without deleting or migrating them, and lead-agent memory uses the correct agent identity.
- Completed TaskLane and subtask UI convergence. Task lanes persist display metadata through migration `0009_task_lane_display_metadata`; terminal cards survive reload; historical tool calls without a round id claim exactly one unambiguous terminal lane; authenticated artifact/Skill failures remain visible instead of silently degrading; round close gates are opt-in.
- Added Command Room capability observability from the real Gateway snapshot through the frontend header. The panel exposes resolved model, direct tools, loaded/missing Skills, MCP configuration/cache state, delegated tool count, and whether MCP is direct or delegated. A non-empty Skill allowlist automatically retains the Chair Skill. No MCP server is configured in the verified local runtime, so the panel correctly reports `Not configured` and `Delegated only` rather than implying MCP is active.
- Fixed frontend stream/history ownership races: a thread id without a run id no longer prematurely commits a visible stream start; terminal completion safely commits SDK thread-id-only streams; delayed events remain scoped to their owning thread; empty runtime-snapshot `runs` now falls back to queried runs so historical Mermaid and paged messages load correctly.
- Isolated every Playwright build from the developer `.next` directory (`.next-e2e-mock`, `.next-e2e-real`, `.next-e2e-record`). Real-backend tests now assert the replay Gateway's `scenario-model` before running, preventing a false green against the daily Gateway or shared database. ESLint, Prettier, Git, and TypeScript ignore/include rules were aligned with those deterministic build roots.
- Hardened sandbox isolation end to end: each local/containerized sandbox receives a scoped execution key distinct from the Provisioner control token; readiness probes and AioSandbox clients use that key; unsecured legacy sandboxes are rejected instead of silently adopted; Provisioner APIs require the internal control token and user-data mounts remain user/thread scoped.
- Made destructive thread cleanup fail closed. A deleting tombstone blocks new runs, active runs are cancelled and drained, short checkpoint writes register through a per-thread condition barrier, and run creation binds native round state only after durable active-slot acquisition. Store failures and slot conflicts no longer leave ghost rounds or overwrite the active round.
- Final verification: backend full suite `5929 passed, 20 skipped`; persistence migration subset `30 passed`; frontend unit suite `681/681`; mock Playwright `77/77`; isolated real-backend Playwright `6/6`; production build generated all 81 routes; `pnpm check`, `pnpm format`, full backend/scripts Ruff, `git diff --check`, and the Command Room behavior SkillOpt probe passed. Static SkillOpt remained at baseline/candidate 1.0 and correctly rejected a no-gain rewrite.
- Visual verification on the isolated replay stack covered 1440x900 and 390x844. The capability menu stayed inside the viewport with no horizontal overflow or clipped text. Screenshots are in `frontend/test-results/command-room-capabilities/desktop.jpg` and `mobile.jpg`.
- No external MCP was configured, no live/production state was accessed or mutated, no secret was exposed, and no commit or deployment was performed. The replay verification stack is local and authentication-disabled by design.

## 2026-07-11 - NextOS frontend architecture 2.0 implementation

- Landed the owner-scoped frontend architecture without adding a duplicate global state store: route/UI scope, runtime execution ownership, and persisted thread/run/round identity remain separate, while LangGraph live state and TanStack Query stay authoritative.
- Isolated Artifact state at the chat subtree and moved Composer scope tracking from a module singleton into React context. The workspace prompt controller remains at workspace scope because page-level chat-mode hooks consume it; browser testing caught and closed the attempted over-scoping regression before completion.
- Extracted message/history merge and pagination, run recovery policy, task-event normalization/projection, effect policy, run status, query keys, and Command Room round/run/lane projection from the thread hook monolith while preserving compatibility re-exports. Central query keys now drive thread/run/snapshot/artifact/upload caches and deletion cleanup.
- Command Room now projects authoritative runtime snapshot facts; legacy no-round lanes remain compatibility-only and cannot replace strong thread/run/round records. No frontend governance decision or automatic dispatch logic was added.
- Five-round validation closed one P1 Provider placement defect and left P0/P1/P2 at zero. Final evidence: frontend unit `692/692`; focused browser ownership suite `56/56`; full browser suite `77/77` with the local worker cap that avoids cold-start server starvation; real Gateway replay `4/4`; production build generated 81 routes; ESLint, TypeScript, Prettier, and SkillOpt behavior gate passed. The first unbounded local Playwright run reproduced an infrastructure-only navigation timeout; the failed subset then passed `10/10`, and the full controlled rerun passed `77/77`.
- No production/live system was mutated, no secret was exposed, and no commit or deployment was performed. Existing unrelated dirty-worktree changes were preserved.

## 2026-07-12 - Command Room goal-first correction

- Removed the default Command Room workflow that made ordinary work revolve around Round Cards, evidence-strength labels, PASS/NEEDS_MORE verdicts, opposition, and a standing Chair/Planner/Boundary/Evidence/Opposition/Recorder roster. The lead prompt now starts from the user goal and completes ordinary safe work directly; delegation and focused review remain optional tools when they add concrete value.
- Reduced model-visible round context to current intent and objective action facts. Historical audit, capability, quality, role, handoff, account, and synthetic next-action state remains available for compatibility/recovery where needed, but no longer re-enters the lead prompt as a soft workflow gate.
- Made the persisted command-room record fact-only. Legacy `next_action` can remain historical input, but memory/SQL round state and RunManager no longer reinterpret it as an accepted, safe, or authorized next action. Task dispatch now preserves the AI-authored prompt without adding a handoff envelope or next-receiver suggestion.
- Rewrote the Command Room contract, documentation, custom skills, probes, and public subagent documentation around goal-first direct execution. Hard boundaries remain for production/public behavior, credentials, money, customer data, deletion/migration, irreversible actions, and scope or permission expansion.
- Validation: focused Command Room/runtime regressions passed `444`; the final backend suite passed `6270` with `20` conditional skips; the contract checker, targeted Ruff, JSON/bytecode checks, frontend `pnpm check`, and `git diff --check` passed. The local SkillOpt static probe scored `1.0` on train/val/test (`1/1/4`) and the model-backed behavior probe passed all three direct/finish/stop scenarios. After commit, the target local stack was restarted only after active run/task-lane counts were both zero; Gateway, Frontend, and Nginx returned on `8001`/`3000`/`2026`, and the proxy health check passed. No database migration, config change, container cleanup, live-data mutation, or secret exposure occurred.

## 2026-07-13 - Bash subagent Skill isolation

- Fixed a concrete timeout cause without changing the 30-minute governance ceiling, Loop, or role roster: a `bash` executor inherited the Command Room's own operating Skill while changing that Skill, expanded a bounded rename into 20 tool rounds, and never reached a final response before the 1800-second cap.
- `bash` now has an explicit empty Skill allowlist. It retains its model and command tools; role subagents retain their explicitly declared Skills.
- Verification: regression test first failed with the leaked `naxus-round`/Chair Skills, then passed after the isolation change. Focused backend tests passed `250`; the full backend suite passed `6347` with `20` conditional skips; Ruff, Command Room contract check, and static/model-backed SkillOpt gates passed.

## 2026-07-13 - Complete AI-AI context text observability

- Corrected the Command Room context model: lead-to-subagent prompts and subagent-to-lead results are text contracts, while a one-shot subagent's internal token spend is not the lead AI's next-round context. The 1800-second governance ceiling, roles, and Loop behavior remain unchanged.
- Exempted `task` results from generic tool-output preview/truncation so the complete sub-AI result reaches the next lead model call. `RunJournal` now records the complete messages and tool schemas seen at each real model-call boundary in owner-scoped, non-truncated `context` events.
- Added an owner-scoped detail endpoint and a top-right Context inspector that uses character count as the primary measure, lists individual lead/subagent/middleware calls, and lazily renders each selected call's complete payload without head-tail shortening. Older metric-only snapshots remain compatible and are marked as lacking stored full text.
- Validation: failing-first backend tests covered complete journal persistence, exact owner-scoped detail retrieval, and invariant `task` exemption even with an old explicit config; failing-first frontend tests covered character-based header display, separation from cumulative token spend, exact detail fetch, and rendering a 20,000-character payload without shortening. Focused regressions passed backend `288` and frontend `74`; the full backend suite passed `6349` with `20` conditional skips, and the full frontend suite passed `770/770`. Full Ruff, `pnpm check`, Prettier, `git diff --check`, and the Command Room contract check passed. The required static SkillOpt probe stayed at hard/soft `1.0` on train/val/test with no applied edits; the model-backed probe was not rerun because this slice changed the runtime/UI text contract rather than the Commander Skill behavior. A read-only database check found zero active runs/task lanes before the local dev stack was restarted with `--skip-install`; Gateway, Frontend, Nginx, and the registered detail route then passed health/route checks. No live/production business state was mutated, no secret was exposed, and no commit or deployment was performed.

## 2026-07-13 - Self-contained one-shot subagent handoffs

- The Command Room lead prompt now states the real context boundary: a one-shot sub-AI does not inherit the lead conversation. A useful handoff carries the goal, relevant confirmed context and starting points, ordinary in-scope authority, scope/stop boundaries, an observable definition of done, and the natural result to return without prescribing tool-by-tool procedure or a fixed form.
- Failing-first coverage locked that behavior before the prompt changed; the complete lead-prompt test file then passed `27` tests.
- Three authenticated local probes exercised the actual Gateway and configured model against isolated `/tmp` fixtures. A simple rename stayed with the lead AI and completed in `52.72s` with zero `task()` calls. A bounded implementation used one `general-purpose` sub-AI, whose task completed in `65.019s`; the full lead run completed in `147.38s`, returned a `2,157`-character task result, and the lead reran the passing three-test unittest check. A read-only code-path investigation used one sub-AI, completed the task in `580.139s` and the lead run in `670.52s`, and returned all `19,170` result characters to the lead.
- The slow investigation isolated two separate causes rather than a weak handoff: one provider read blocked for about five minutes before `The read operation timed out` and a successful retry; the same sub-AI also issued roughly sixty read/search tool calls, reached thirty `read_file` calls, and received a program-injected `[LOOP DETECTED]` instruction. This single-AI `LoopDetectionMiddleware` is not the intended Command Room AI-AI Loop; the user confirmed its removal from subagent runtimes in the follow-up change below.
- The context snapshots also verified the exact text path: `task_tool` passes the AI-authored prompt to `SubagentExecutor`; the model-boundary `InputSanitizationMiddleware` then adds the security-only `BEGIN/END USER INPUT` wrapper without replacing the task semantics. No second reviewer/audit sub-AI was launched in either delegated probe.
- Final validation: focused Command Room prompt/behavior tests passed `35`; targeted Ruff, the Command Room contract check, `git diff --check`, the simple-rename fixture check, and the three-test unittest fixture passed. Static SkillOpt remained hard/soft `1.0` on train/val/test with no applied edit, and the model-backed behavior probe passed all three finish/direct/stop scenarios. Validation used only the local development stack and a dedicated local test account/workspace; final read-only status showed no active run or task lane. No production or external business state was mutated, no secret content was printed, and no commit or deployment was performed.

## 2026-07-13 - Restore Command Room Loop to AI-AI collaboration

- Corrected the Loop boundary confirmed by the user: Command Room Loop means one AI completes and returns before the lead decides whether another AI should execute, verify, oppose, or review. It is not a programmatic tool-frequency limit inside one one-shot sub-AI.
- Removed `LoopDetectionMiddleware` from `build_subagent_runtime_middlewares`. Lead-agent loop detection remains available through `loop_detection.enabled`; subagent 1800-second timeout, cancellation, `ModelCallLimitMiddleware`, structural recursion limit, provider safety termination, tool errors, and guardrails remain unchanged.
- TDD reproduced the old behavior with `loop_detection.enabled=true`: the subagent middleware stack contained `LoopDetectionMiddleware`. After deleting the sole six-line attachment block, the focused middleware suite passed `14` tests.
- Final validation: the relevant runtime regression group passed `317`; full Ruff, the Command Room contract check, `git diff --check`, static SkillOpt at hard/soft `1.0` on train/val/test, and the model-backed finish/direct/stop behavior probe passed. After a zero-active-run/task check, the local stack was restarted and runtime inspection confirmed `LoopDetectionMiddleware` absent, `ModelCallLimitMiddleware` and provider safety termination present, and the effective subagent timeout still `1800`. A separately launched full backend run completed, but its orchestration lost the final exit status, so it is deliberately not claimed as passing evidence. No production state was changed, no secret was exposed, and no commit or deployment was performed.

## 2026-07-13 - Collapse duplicate Codex retry layers

- The five-minute event was not a total model-call limit: `httpx.Client(timeout=300)` is a per-read idle watchdog, and historical journals include successful active streams lasting over 420 seconds. The affected subagent call stayed silent for `312.8s`; its same-payload retry returned quickly, matching a 300-second idle-stream recovery rather than normal Terra reasoning.
- The amplification bug was nested retry ownership. `CodexChatModel` could spend three transport attempts, then `LLMErrorHandlingMiddleware` treated the final raw timeout as fresh and could replay that whole three-attempt budget up to three times. The provider now raises `CodexRetryExhaustedError` after its own budget is spent, and the existing middleware exception-budget mechanism caps that terminal signal at one outer attempt. The provider's 300-second idle watchdog, three internal attempts, and the subagent's 1800-second governance timeout remain unchanged.
- TDD first reproduced both missing behaviors: the provider leaked raw `ReadTimeout`, and the outer middleware classified the exhausted signal as generic. The focused provider/error-middleware suite then passed `59` tests.
- Final validation: the broader provider/model/subagent regression group passed `314`; full backend Ruff and format checks, the Command Room contract, `git diff --check`, static SkillOpt at hard/soft `1.0`, and the model-backed finish/direct/stop probe passed. A combined runtime simulation confirmed three Provider attempts produce exactly one outer middleware attempt. After a zero-active-run/task check, the local stack was restarted and Gateway, Frontend, and Nginx all returned HTTP 200. No timeout default, production state, secret, commit, or deployment was changed.
