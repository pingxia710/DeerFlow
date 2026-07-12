# Single-Node Reliability Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing one-Gateway DeerFlow deployment reliably preserve a lead-agent → subagent → durable task-state → multi-conversation recovery loop, while producing a sanitized local value report.

**Architecture:** Keep the current one-process Gateway and per-thread run ownership model. Terminal task state becomes a `ToolMessage.additional_kwargs` value at the task tool boundary; legacy text parsing remains only for history compatibility. Runtime snapshot becomes a bounded, read-only projection: startup and run lifecycle transitions remain responsible for durable recovery, and snapshot message pages load under a small concurrency cap. A second local-only deterministic model drives one real frontend/Gateway task scenario without adding a service or a paid model.

**Tech Stack:** Python 3.12, FastAPI, LangChain/LangGraph, SQLite, React/Next.js, TypeScript, Playwright, pytest, rstest.

## Global Constraints

- Deployment stays **one Gateway worker and one node**; do not add Redis, multiple workers, shared stream infrastructure, or distributed scheduling.
- Do not add a database migration, external dependency, paid model, or live production validation.
- Preserve current auth/owner semantics and the ViewScope/execution-owner/persisted-record frontend model.
- Keep automatic subagent execution goal-first and one-hop; do not add multi-hop role routing.
- `GET /runtime-snapshot` must not write recovery or repair state in its normal path.
- Value-report output must contain aggregates only: no prompts, responses, user identifiers, credentials, artifact paths, or artifact content.
- Use the target worktree `/Users/pingxia/projects/deer-flow-arch2-readiness-20260711`; leave the pre-existing untracked model-reasoning plan untouched.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `backend/packages/harness/deerflow/tools/builtins/task_tool.py` | Construct terminal `ToolMessage` objects with structured task status at the producing tool boundary. |
| `backend/packages/harness/deerflow/subagents/status_contract.py` | Keep the legacy textual fallback table compatible with every terminal task result shape. |
| `contracts/subagent_status_contract.json` | Cross-language fixture including runtime-observed-evidence success. |
| `frontend/src/core/tasks/subtask-result.ts` | Parse legacy evidence-bearing success rows when history has no structured status. |
| `backend/packages/harness/deerflow/runtime/runs/manager.py` | Allow callers that only need a projection to skip read-time recovery/retry writes. |
| `backend/app/gateway/routers/thread_runs.py` | Build a read-only, bounded-concurrency runtime snapshot and stop advertising `enqueue`. |
| `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` | Do not force an unsupported Command Room reasoning effort on every render. |
| `frontend/src/components/workspace/messages/message-list-item.tsx` | Keep hidden message actions from capturing clicks meant for subtask cards. |
| `backend/tests/replay_provider.py` | Provide a deterministic local task-scenario model used only by replay test infrastructure. |
| `backend/tests/_replay_fixture.py` | Register the test-only scenario model alongside the existing fixture replay model. |
| `frontend/tests/e2e-real-backend/task-recovery.spec.ts` | Exercise actual task dispatch, conversation switch, reload, and durable task recovery through the real Gateway/frontend pair. |
| `backend/scripts/runtime_value_report.py` | Read a local SQLite database in read-only mode and emit sanitized operational aggregates. |
| `backend/tests/test_runtime_value_report.py` | Pin report aggregation, absent-table handling, and no-identifier output. |
| `docs/runtime/value-report.md` | Document the command and the operational interpretation limits of its metrics. |
| `Progress.md` | Record the completed closure and its verification evidence. |

### Task 1: Produce terminal task status as a structured ToolMessage

**Files:**
- Modify: `backend/packages/harness/deerflow/tools/builtins/task_tool.py:16-29,264-269,441-950`
- Modify: `backend/packages/harness/deerflow/subagents/status_contract.py:52-63`
- Modify: `contracts/subagent_status_contract.json:5-100`
- Modify: `frontend/src/core/tasks/subtask-result.ts:60-160`
- Modify: `backend/tests/test_task_tool_core_logic.py:76-420`
- Modify: `backend/tests/test_subagent_status_contract.py:47-57`
- Modify: `backend/tests/test_tool_error_handling_subagent_stamp.py:45-115`
- Modify: `frontend/tests/unit/core/tasks/subtask-result.test.ts:35-150,327-365`

**Interfaces:**
- Consumes: `tool_call_id: Annotated[str, InjectedToolCallId]` already supplied to `task_tool` and `make_subagent_additional_kwargs(status, error=None)` from the existing contract module.
- Produces: `task_tool(...) -> ToolMessage` for every normal terminal outcome, with `name="task"`, the original `tool_call_id`, and `additional_kwargs["subagent_status"]` in the fixed status vocabulary.
- Produces: `parseSubtaskResult(text, additionalKwargs?)` remains compatible with historical string-only task ToolMessages, including the evidence format.

- [x] **Step 1: Write the failing producer and fallback tests**

  Update the test helper to return a `ToolMessage`, then assert its content and structured status instead of comparing the wrapper object to a string. Add this direct producer assertion beside the current completed-event test:

  ```python
  assert isinstance(output, ToolMessage)
  assert output.content == (
      "Task Succeeded. Runtime-observed evidence:\n"
      "- command: pytest backend/tests/test_example.py -q; exit code: 0\n"
      "Result: all done"
  )
  assert output.additional_kwargs[SUBAGENT_STATUS_KEY] == "completed"
  ```

  Add this cross-language fixture case to `contracts/subagent_status_contract.json` immediately after `succeeded`:

  ```json
  {
    "name": "succeeded_with_runtime_observed_evidence",
    "origin": "task_tool.py completed path with runtime-observed tool evidence",
    "content": "Task Succeeded. Runtime-observed evidence:\n- tool: write_file; path: /mnt/user-data/outputs/evidence.txt; status: success; output_sha256: abc\nResult: wrote evidence",
    "expected_status": "completed",
    "expected_error_contains": "wrote evidence"
  }
  ```

  Add a frontend unit test which proves the history fallback displays the final result rather than leaving the task in progress:

  ```ts
  it("recognises runtime-observed-evidence success and extracts its result", () => {
    const parsed = parseSubtaskResult(
      "Task Succeeded. Runtime-observed evidence:\n- tool: write_file; status: success\nResult: wrote evidence",
    );
    expect(parsed).toEqual({ status: "completed", result: "wrote evidence" });
  });
  ```

- [x] **Step 2: Run the narrow tests and verify the expected failure**

  Run:

  ```bash
  cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_status_contract.py tests/test_tool_error_handling_subagent_stamp.py -q
  cd ../frontend && pnpm rstest tests/unit/core/tasks/subtask-result.test.ts
  ```

  Expected: the task-tool producer assertion fails because `task_tool` returns a string, and the shared/front-end evidence case is unrecognised or remains `in_progress`.

- [x] **Step 3: Implement the minimal producer boundary**

  In `task_tool.py`, import `ToolMessage`, `SubagentStatusValue`, and the existing contract helpers. Add one small helper; do not introduce a second status enum:

  ```python
  def _terminal_task_message(
      content: str,
      *,
      tool_call_id: str,
      status: SubagentStatusValue,
      error: str | None = None,
  ) -> ToolMessage:
      return ToolMessage(
          content=content,
          name="task",
          tool_call_id=tool_call_id,
          additional_kwargs=make_subagent_additional_kwargs(status, error=error),
      )
  ```

  Change the return annotation of `task_tool` to `ToolMessage`. Wrap each of its eight terminal branches with that helper using the explicit status below; preserve the existing visible `content` exactly.

  ```python
  # unknown subagent / bash disabled / disappeared
  return _terminal_task_message(content, tool_call_id=tool_call_id, status="failed")

  # completed
  return _terminal_task_message(
      _format_task_success(result_text, observed_evidence_refs=observed_evidence_refs),
      tool_call_id=tool_call_id,
      status="completed",
  )

  # failed / cancelled / timed out / polling timed out
  return _terminal_task_message(content, tool_call_id=tool_call_id, status="failed")
  return _terminal_task_message(content, tool_call_id=tool_call_id, status="cancelled")
  return _terminal_task_message(content, tool_call_id=tool_call_id, status="timed_out")
  return _terminal_task_message(content, tool_call_id=tool_call_id, status="polling_timed_out")
  ```

  Keep `ToolErrorHandlingMiddleware` as the compatibility safety net for an exception before a ToolMessage is produced; its successful-result stamping becomes idempotent because `dict.update()` writes the same status.

  Add `("Task Succeeded. Runtime-observed evidence:", "completed")` to `_PREFIX_TO_STATUS`. In the frontend add `SUCCESS_WITH_RUNTIME_OBSERVED_EVIDENCE_PREFIX`, detect it before `SUCCESS_PREFIX`, and use the existing `Result:` marker extraction:

  ```ts
  if (trimmed.startsWith(SUCCESS_WITH_RUNTIME_OBSERVED_EVIDENCE_PREFIX)) {
    const resultIndex = trimmed.indexOf("Result:");
    return {
      status: "completed",
      result: resultIndex >= 0 ? trimmed.slice(resultIndex + "Result:".length).trim() : "",
    };
  }
  ```

- [x] **Step 4: Run the contract and producer tests**

  Run:

  ```bash
  cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_status_contract.py tests/test_tool_error_handling_subagent_stamp.py -q
  cd ../frontend && pnpm rstest tests/unit/core/tasks/subtask-result.test.ts
  ```

  Expected: all selected tests pass; the middleware tests continue to prove exception-path compatibility, while the task tool test proves the source stamp.

- [x] **Step 5: Commit the independently testable contract slice**

  ```bash
  git add backend/packages/harness/deerflow/tools/builtins/task_tool.py backend/packages/harness/deerflow/subagents/status_contract.py contracts/subagent_status_contract.json backend/tests/test_task_tool_core_logic.py backend/tests/test_subagent_status_contract.py backend/tests/test_tool_error_handling_subagent_stamp.py frontend/src/core/tasks/subtask-result.ts frontend/tests/unit/core/tasks/subtask-result.test.ts
  git commit -m "fix(tasks): stamp terminal subagent status at source"
  ```

### Task 2: Make multi-conversation snapshot reads bounded and side-effect free

**Files:**
- Modify: `backend/packages/harness/deerflow/runtime/runs/manager.py:1090-1140`
- Modify: `backend/app/gateway/routers/thread_runs.py:966-1013,2151-2260`
- Modify: `backend/tests/test_thread_run_messages_pagination.py:228-480`
- Modify: `backend/tests/test_thread_runtime_snapshot_close_gate.py:74-170`
- Modify: `backend/tests/test_run_manager.py:1090-1140`

**Interfaces:**
- Consumes: `RunManager.list_by_thread(thread_id, user_id=None, limit=100, before=None)`.
- Produces: `RunManager.list_by_thread(..., recover_stale=False)` that returns the same newest-first projection without calling stale-run recovery or terminal projection retry.
- Produces: `_list_runtime_snapshot_run_messages(records, thread_id, request, user_id, limit)` with original run ordering and at most eight active per-run message reads.

- [x] **Step 1: Write the failing snapshot purity, concurrency, and API-schema tests**

  Add a `TrackingRunManager`/`ReadOnlyRoundStore` fixture to the snapshot tests. Give it a terminal record and mutator methods that fail if called:

  ```python
  class _ReadOnlyTerminalRoundStore(_ReadOnlyRoundStore):
      async def set_run_state(self, *args, **kwargs):
          raise AssertionError("runtime snapshot must not repair round state")

      async def record_task_events(self, events):
          raise AssertionError("runtime snapshot must not repair task lanes")
  ```

  Assert a snapshot returns `200`, leaves both mutator call lists empty, and calls `list_by_thread(..., recover_stale=False)`.

  Add a delayed `list_messages_by_run` fake that records active calls. Request at least three runs and assert:

  ```python
  assert max_active > 1
  assert max_active <= thread_runs._RUNTIME_SNAPSHOT_MESSAGE_CONCURRENCY
  assert [page["run_id"] for page in response.json()["run_messages"]] == [record.run_id for record in records]
  ```

  Add a public-schema assertion:

  ```python
  schema = thread_runs.RunCreateRequest.model_json_schema()
  assert "enqueue" not in json.dumps(schema)
  with pytest.raises(ValidationError):
      thread_runs.RunCreateRequest(multitask_strategy="enqueue")
  ```

- [x] **Step 2: Run the narrow tests and verify the expected failure**

  Run:

  ```bash
  cd backend && uv run pytest tests/test_thread_run_messages_pagination.py tests/test_thread_runtime_snapshot_close_gate.py tests/test_run_manager.py -q
  ```

  Expected: the schema still contains `enqueue`; snapshot calls its repair helpers; and per-run message loading reaches only one active request.

- [x] **Step 3: Implement the read-only projection and bounded fan-out**

  Change the model field to expose only implemented policies:

  ```python
  multitask_strategy: Literal["reject", "rollback", "interrupt"] = Field(
      default="reject", description="Concurrency strategy"
  )
  ```

  Add an opt-out parameter to `RunManager.list_by_thread`, preserving the current behavior by default for legacy callers:

  ```python
  async def list_by_thread(..., before: str | None = None, recover_stale: bool = True) -> list[RunRecord]:
      if recover_stale:
          await self.recover_stale_inflight_runs(thread_id=thread_id, user_id=user_id)
          await self._retry_terminal_round_projections(thread_id=thread_id)
      # existing hydration and newest-first ordering unchanged
  ```

  In `get_thread_runtime_snapshot`, remove the explicit `recover_stale_inflight_runs`, `_sync_thread_error_for_latest_worker_lost`, `_repair_task_event_projection_from_store`, and `_repair_terminal_runtime_snapshot_rows` calls. Load runs with `recover_stale=False`; startup reconciliation and the existing `RunManager.set_status` lifecycle projection remain the write boundaries.

  Rewrite the three router tests that currently expect snapshot repair (`test_runtime_snapshot_repairs_terminal_run_with_open_round_and_task_lane`, `test_runtime_snapshot_recovers_stale_store_only_inflight_run`, and `test_runtime_snapshot_repair_keeps_new_active_round_and_lane_isolated`) into read-only assertions. Their terminal/open fixtures must return the stored state unchanged, `recovery` must be absent, and `set_run_state_calls` / `record_task_events_calls` must remain empty. Keep the direct helper tests in `test_native_round_state.py` and the `RunManager.set_status` / startup-reconciliation tests as the lifecycle-write coverage.

  Replace the serial message loop with this bounded helper near `_run_messages_page`:

  ```python
  _RUNTIME_SNAPSHOT_MESSAGE_CONCURRENCY = 8

  async def _list_runtime_snapshot_run_messages(...):
      semaphore = asyncio.Semaphore(_RUNTIME_SNAPSHOT_MESSAGE_CONCURRENCY)

      async def load(record: RunRecord) -> RuntimeSnapshotRunMessages:
          async with semaphore:
              page = await _run_messages_page(thread_id, record.run_id, record, request, user_id=user_id, limit=limit)
          return RuntimeSnapshotRunMessages(run_id=record.run_id, **page)

      return list(await asyncio.gather(*(load(record) for record in records)))
  ```

  Keep close-gate queries opt-in and read-only. Keep existing repair helper functions intact for their focused compatibility tests; do not call them from a normal GET route.

- [x] **Step 4: Run the snapshot regression suite**

  Run:

  ```bash
  cd backend && uv run pytest tests/test_thread_run_messages_pagination.py tests/test_thread_runtime_snapshot_close_gate.py tests/test_run_manager.py tests/test_native_round_state.py -q
  ```

  Expected: all selected tests pass; terminal state is still projected by lifecycle tests, while snapshot is proven read-only and concurrently bounded.

- [x] **Step 5: Commit the backend multi-conversation slice**

  ```bash
  git add backend/packages/harness/deerflow/runtime/runs/manager.py backend/app/gateway/routers/thread_runs.py backend/tests/test_thread_run_messages_pagination.py backend/tests/test_thread_runtime_snapshot_close_gate.py backend/tests/test_run_manager.py
  git commit -m "fix(runtime): make snapshots read-only and bounded"
  ```

### Task 3: Fix the two current frontend interaction regressions at their sources

**Files:**
- Modify: `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx:119-134`
- Modify: `frontend/src/components/workspace/messages/message-list-item.tsx:155-175`
- Test: `frontend/tests/e2e/agent-chat.spec.ts:110-170`
- Test: `frontend/tests/e2e/subtask-card.spec.ts:145-180`

**Interfaces:**
- Consumes: `settings.context.reasoning_effort`, a model's `supports_reasoning_effort`, and the existing InputBox normalisation effect.
- Produces: Command Room context may leave `reasoning_effort` undefined so InputBox can resolve it only when the selected model supports it; backend still applies its existing Command Room default when sending a run.
- Produces: Message actions are clickable only while their conversation message is hovered and never intercept a subtask-card click while hidden.

- [x] **Step 1: Run the two existing regression tests and preserve their failure evidence**

  Run:

  ```bash
  cd frontend && pnpm playwright test tests/e2e/agent-chat.spec.ts --grep "agent model beats" --workers=1
  cd frontend && pnpm playwright test tests/e2e/subtask-card.spec.ts --grep "keeps completed terminal subtask" --workers=1
  ```

  Expected: the agent route renders the generic load error because `max` is reintroduced after InputBox removes unsupported reasoning effort; the subtask card click is intercepted by the invisible action layer.

- [x] **Step 2: Apply the two minimal source fixes**

  In the Command Room context object, stop manufacturing `"max"` in the page component. Keep the user-selected setting, which lets InputBox erase unsupported effort and lets `buildThreadRunContext` retain its server-side Command Room default:

  ```tsx
  reasoning_effort: settings.context.reasoning_effort,
  ```

  In `MessageListItem`, make the hidden toolbar and its action group non-interactive until its named conversation group is hovered:

  ```tsx
  "pointer-events-none z-20 opacity-0 transition-opacity delay-200 duration-300 group-hover/conversation-message:opacity-100",
  // inner action container
  "pointer-events-none flex gap-1 group-hover/conversation-message:pointer-events-auto",
  ```

  Do not add a global z-index rule or a second frontend state store.

- [x] **Step 3: Run the focused browser tests**

  Run:

  ```bash
  cd frontend && pnpm playwright test tests/e2e/agent-chat.spec.ts --grep "agent model beats" --workers=1
  cd frontend && pnpm playwright test tests/e2e/subtask-card.spec.ts --grep "keeps completed terminal subtask" --workers=1
  ```

  Expected: both tests pass, including the submitted agent-specific DeepSeek model assertion and the completed subtask card opening action.

- [x] **Step 4: Commit the frontend regression slice**

  ```bash
  git add frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx frontend/src/components/workspace/messages/message-list-item.tsx
  git commit -m "fix(frontend): preserve task-card clicks and model selection"
  ```

### Task 4: Add the deterministic real Gateway/frontend AI-AI golden path

**Files:**
- Modify: `backend/tests/replay_provider.py:1-420`
- Modify: `backend/tests/_replay_fixture.py:35-90`
- Create: `frontend/tests/e2e-real-backend/task-recovery.spec.ts`
- Test: `frontend/playwright.real-backend.config.ts:1-76` (use without changing its server topology)

**Interfaces:**
- Consumes: the existing replay Gateway, `TaskScenarioChatModel` selected by `context.model_name="task-scenario-model"`, and the test-only `POST /api/test-only/seed-runs` endpoint for an isolated comparison conversation.
- Produces: a local model that deterministically makes `lead_agent` call `task`, makes the one-hop `subagent:general-purpose` write one evidence file, then returns a final lead response.
- Produces: a browser test that asserts persisted `task` ToolMessage structured status, task-lane completion, A → B → A isolation, and reload recovery.

- [x] **Step 1: Write the failing Playwright golden test**

  Create `task-recovery.spec.ts` with a unique task marker and use a named model selected in local storage:

  ```ts
  const TASK_PROMPT = "E2E_TASK_SCENARIO: create one observed evidence file";
  const FINAL = "Task scenario final answer";

  await page.addInitScript(() => {
    localStorage.setItem(
      "deerflow.local-settings",
      JSON.stringify({ context: { mode: "ultra", model_name: "task-scenario-model" } }),
    );
  });
  ```

  Submit `TASK_PROMPT` through `/workspace/chats/new`; wait for `FINAL` and `Subtask completed`; take the generated A thread id from the URL. Seed B through the already test-only endpoint with the literal marker `B-ISOLATED-MESSAGE`. Navigate A → B → A, reload A, and assert:

  ```ts
  await expect(page.getByText("Task scenario evidence task")).toBeVisible();
  await expect(page.getByText("Subtask completed")).toBeVisible();
  await expect(page.getByText(FINAL)).toBeVisible();
  expect(await page.getByText("B-ISOLATED-MESSAGE", { exact: false }).count()).toBe(0);
  ```

  Read A's snapshot through the same-origin frontend proxy after reload and assert at least one persisted ToolMessage has `name === "task"` and `content.additional_kwargs.subagent_status === "completed"`.

- [x] **Step 2: Run it and verify it fails because the named scenario model does not exist**

  Run:

  ```bash
  cd frontend && pnpm playwright test --config playwright.real-backend.config.ts tests/e2e-real-backend/task-recovery.spec.ts --workers=1
  ```

  Expected: the Gateway reports an unknown `task-scenario-model`, or the task/final assertions time out.

- [x] **Step 3: Add the smallest local-only scenario model**

  In `backend/tests/replay_provider.py`, add `TaskScenarioChatModel(BaseChatModel)`. It must return by inspecting caller tags and its own message history, not a process-global turn counter:

  ```python
  if caller.startswith("subagent:"):
      if any(isinstance(message, ToolMessage) and message.name == "write_file" for message in messages):
          return AIMessage(content="Subagent completed the observed evidence write.")
      return AIMessage(content="", tool_calls=[{
          "name": "write_file",
          "id": "task-scenario-evidence-call",
          "type": "tool_call",
          "args": {
              "description": "Write deterministic task evidence",
              "path": "/mnt/user-data/outputs/task-scenario-evidence.txt",
              "content": "deterministic evidence",
          },
      }])
  if any(isinstance(message, ToolMessage) and message.name == "task" for message in messages):
      return AIMessage(content="Task scenario final answer")
  return AIMessage(content="", tool_calls=[{
      "name": "task",
      "id": "task-scenario-parent-call",
      "type": "tool_call",
      "args": {
          "description": "Task scenario evidence task",
          "prompt": "E2E_TASK_SCENARIO: write deterministic evidence",
          "subagent_type": "general-purpose",
      },
  }])
  ```

  Implement `_generate`, `_stream`, and no-op `bind_tools` with the same `BaseChatModel` conventions used by `ReplayChatModel`. Add a `TASK_SCENARIO_MODEL_BLOCK` to `_replay_fixture.py` and append it after `REPLAY_MODEL_BLOCK` in `build_config_yaml`; the existing default model remains first and fixture tests keep using it.

  In `ReplayChatModel._match`, if a middleware/title/suggestion call contains the task marker, return a plain local `AIMessage(content="Task scenario")` so those auxiliary calls do not attempt to consume a fixture entry. Leave normal fixture matching untouched.

- [x] **Step 4: Run the golden test, then the existing replay suite**

  Run:

  ```bash
  cd frontend && pnpm playwright test --config playwright.real-backend.config.ts tests/e2e-real-backend/task-recovery.spec.ts --workers=1
  cd frontend && pnpm playwright test --config playwright.real-backend.config.ts --workers=1
  cd ../backend && uv run pytest tests/test_replay_golden.py -q
  ```

  Expected: the new browser path proves a real task tool call and durable recovery; existing replay fixture tests remain green because their model and fixture matching are unchanged.

- [x] **Step 5: Commit the full-stack golden slice**

  ```bash
  git add backend/tests/replay_provider.py backend/tests/_replay_fixture.py frontend/tests/e2e-real-backend/task-recovery.spec.ts
  git commit -m "test(e2e): cover durable subagent task recovery"
  ```

### Task 5: Add a sanitized, read-only DeerFlow value report

**Files:**
- Create: `backend/scripts/runtime_value_report.py`
- Create: `backend/tests/test_runtime_value_report.py`
- Create: `docs/runtime/value-report.md`

**Interfaces:**
- Consumes: a SQLite path supplied with `--db`; tables `runs`, `task_lanes`, and `artifact_provenance` when present.
- Produces: `build_report(database_path: Path) -> dict[str, object]` and a CLI `uv run python scripts/runtime_value_report.py --db <path> --format json`.
- Produces: only aggregate fields for outcomes, terminal duration quantiles, token distribution/subagent share, task-lane outcomes, and artifact coverage.

- [x] **Step 1: Write the failing report tests with a minimal local SQLite fixture**

  In `test_runtime_value_report.py`, load the script with `importlib.util.spec_from_file_location`, create only the needed schema in `tmp_path / "deerflow.db"`, and insert two runs, two task lanes, and one artifact row. Assert exact safe aggregates:

  ```python
  report = module.build_report(db_path)
  assert report["runs"]["total"] == 2
  assert report["runs"]["outcomes"] == {"error": 1, "success": 1}
  assert report["tokens"]["total"] == 150
  assert report["tokens"]["subagent_share"] == 0.4
  assert report["task_lanes"]["outcomes"] == {"completed": 1, "failed": 1}
  assert report["artifacts"]["coverage"] == 0.5
  assert "thread-1" not in json.dumps(report)
  assert "user-1" not in json.dumps(report)
  ```

  Also create a database containing only `runs`; assert `task_lanes["available"] is False` and `artifacts["available"] is False`, rather than failing or creating tables.

- [x] **Step 2: Run the report test and verify the expected failure**

  Run:

  ```bash
  cd backend && uv run pytest tests/test_runtime_value_report.py -q
  ```

  Expected: collection fails because `scripts/runtime_value_report.py` does not exist.

- [x] **Step 3: Implement a read-only standard-library report**

  Use `sqlite3` only. Open the supplied database with an immutable read-only URI and never issue DDL/DML:

  ```python
  def _open_read_only(database_path: Path) -> sqlite3.Connection:
      resolved = database_path.expanduser().resolve()
      return sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
  ```

  Build only aggregate SQL queries. Use `SELECT status, COUNT(*) ... GROUP BY status` for outcomes, `SELECT total_tokens, subagent_tokens ...` for token totals, `SELECT duration_ms FROM task_lanes WHERE duration_ms IS NOT NULL` for task duration values, and `COUNT(DISTINCT run_id)` for artifact coverage. Compute p50/p95 with a local nearest-rank helper returning `None` for an empty list:

  ```python
  def _percentile(values: list[float], percentile: float) -> float | None:
      if not values:
          return None
      ordered = sorted(values)
      index = max(0, math.ceil(percentile * len(ordered)) - 1)
      return ordered[index]
  ```

  Emit a stable structure with `database`, `runs`, `tokens`, `task_lanes`, and `artifacts`; `database` contains only the resolved basename and schema availability, never its full path. Offer `--format json` and `--format text`; JSON is the automation-safe format.

  In `docs/runtime/value-report.md`, document the exact command, the output fields, and the limits: a high completion rate does not establish user value; compare completion, p95 duration, subagent token share, and artifact coverage across the same task cohort before treating delegation as valuable.

- [x] **Step 4: Run the report tests and CLI against a temporary fixture**

  Run:

  ```bash
  cd backend && uv run pytest tests/test_runtime_value_report.py -q
  cd backend && uv run python scripts/runtime_value_report.py --help
  ```

  Expected: tests pass; CLI help shows only `--db` and `--format` options, and no code path can alter the supplied database.

- [x] **Step 5: Commit the operational-value slice**

  ```bash
  git add backend/scripts/runtime_value_report.py backend/tests/test_runtime_value_report.py docs/runtime/value-report.md
  git commit -m "feat(ops): add read-only runtime value report"
  ```

### Task 6: Integrate, document, and verify the single-node candidate

**Files:**
- Modify: `Progress.md` (append one dated closure entry only)
- Verify: all files changed by Tasks 1-5

**Interfaces:**
- Consumes: the five independently passing slices above.
- Produces: a clean, reviewable candidate with reproducible backend/frontend/browser checks and a documented non-goal boundary.

- [x] **Step 1: Run format, type, and focused complete suites**

  Run:

  ```bash
  cd backend && uv run ruff check app packages tests scripts
  cd backend && uv run ruff format --check app packages tests scripts
  cd backend && uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_status_contract.py tests/test_tool_error_handling_subagent_stamp.py tests/test_thread_run_messages_pagination.py tests/test_thread_runtime_snapshot_close_gate.py tests/test_native_round_state.py tests/test_runtime_value_report.py tests/test_replay_golden.py -q
  cd frontend && pnpm check
  cd frontend && pnpm exec prettier --check src tests
  cd frontend && pnpm rstest
  cd frontend && pnpm playwright test --workers=1
  cd frontend && pnpm playwright test --config playwright.real-backend.config.ts --workers=1
  ```

  Expected: all commands pass. If a broad suite exposes an unrelated existing failure, isolate it with the exact failing command before deciding whether it overlaps this diff; do not edit unrelated code to make the suite green.

- [x] **Step 2: Update the project record**

  Append a concise `2026-07-12` entry to `Progress.md` stating:

  ```markdown
  - Closed the single-node reliability pass: task terminal status is source-stamped, runtime snapshots are read-only/bounded, two browser regressions are covered, deterministic task recovery spans Gateway+frontend, and `runtime_value_report.py` provides sanitized local aggregates.
  - Kept the explicit boundary: one Gateway/one node; no Redis, multi-worker runtime, migration, or automatic multi-hop routing.
  ```

- [x] **Step 3: Inspect the final diff for scope and accidental sensitive output**

  Run:

  ```bash
  git diff --check
  git status --short
  git diff --stat 2b5aea97..HEAD
  rg -n "api[_-]?key|password|authorization|token" backend/scripts/runtime_value_report.py docs/runtime/value-report.md frontend/tests/e2e-real-backend/task-recovery.spec.ts
  ```

  Expected: no whitespace errors; the pre-existing untracked `docs/superpowers/plans/2026-07-11-model-reasoning-capability-alignment.md` remains untouched; report/docs/tests contain only synthetic values and no credentials.

- [x] **Step 4: Commit verification record**

  ```bash
  git add Progress.md
  git commit -m "docs: record single-node reliability closure"
  ```

## Acceptance Coverage Review

- Runtime-observed-evidence success is completed on both backend and frontend fallback paths: **Task 1**.
- Unsupported `enqueue` disappears from the public request schema: **Task 2**.
- Snapshot is read-only and message recovery is bounded/non-serial: **Task 2**.
- Agent-specific model and completed subtask-card browser regressions pass: **Task 3**.
- Real Gateway/frontend task dispatch survives A → B → A and reload: **Task 4**.
- Sanitized local outcomes, latency, token, and artifact metrics are available: **Task 5**.
- Candidate validation and no-distributed-architecture boundary are recorded: **Task 6**.

## Plan Self-Review

- Spec coverage: every one of the five approved vertical slices has a dedicated independently testable task; no multi-worker, migration, paid model, auth, or routing expansion appears in the plan.
- Placeholder scan: no deferred implementation markers are used; each task names exact files, commands, interfaces, expected test result, and minimal code shape.
- Type consistency: `SubagentStatusValue` remains the single backend terminal vocabulary; `ToolMessage.additional_kwargs["subagent_status"]` is the producer and wire contract; `RunManager.list_by_thread(..., recover_stale=False)` is the sole new snapshot projection parameter; `build_report(Path) -> dict[str, object]` is the value-report test interface.
