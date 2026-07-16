import { expect, test } from "@rstest/core";

import {
  getActiveTurnSubtaskScope,
  findMatchingTerminalSubtaskForTask,
  getMessageGroupKey,
  getSubtaskCardKey,
  hasMatchingTerminalSubtaskForTask,
  hasTerminalSubtaskForTask,
  isInferredRunningSubtaskVisible,
  isRuntimeOnlySubtaskForActiveTurn,
  shouldKeepInferredSubtask,
} from "@/components/workspace/messages/message-list";

type TestMessageGroup = Parameters<typeof getActiveTurnSubtaskScope>[0];

test("getMessageGroupKey uses stable type-id key without index when id exists", () => {
  expect(getMessageGroupKey({ type: "assistant", id: "group-1" }, 42)).toBe(
    "assistant-group-1",
  );
});

test("getMessageGroupKey falls back to index and type when id is missing", () => {
  expect(getMessageGroupKey({ type: "assistant:subagent" }, 3)).toBe(
    "fallback-3-assistant:subagent",
  );
});

test("getSubtaskCardKey separates same taskId across different runIds", () => {
  expect(getSubtaskCardKey("task-1", "run-1")).not.toBe(
    getSubtaskCardKey("task-1", "run-2"),
  );
});

test("getSubtaskCardKey separates same run and taskId across different roundIds", () => {
  expect(getSubtaskCardKey("task-1", "run-1", "round-1")).not.toBe(
    getSubtaskCardKey("task-1", "run-1", "round-2"),
  );
});

test("getSubtaskCardKey is stable for same taskId and same runId", () => {
  expect(getSubtaskCardKey("task-1", "run-1")).toBe(
    getSubtaskCardKey("task-1", "run-1"),
  );
});

test("getSubtaskCardKey uses stable no-round fallback for missing roundId", () => {
  expect(getSubtaskCardKey("task-1", "run-1")).toBe(
    getSubtaskCardKey("task-1", "run-1", null),
  );
});

test("getSubtaskCardKey keeps legacy key when runId is missing", () => {
  expect(getSubtaskCardKey("task-1")).toBe("task-group-task-1");
  expect(getSubtaskCardKey("task-1", null)).toBe("task-group-task-1");
});

test("terminal task suppresses stale inferred running replay from another run", () => {
  expect(
    hasTerminalSubtaskForTask(
      [
        {
          id: "task-1",
          threadId: "thread-1",
          runId: "run-completed",
          status: "completed",
          subagent_type: "test",
          description: "done",
          prompt: "do it",
        },
      ],
      {
        threadId: "thread-1",
        runId: "run-replay",
        taskId: "task-1",
      },
    ),
  ).toBe(true);
});

test("terminal task does not suppress same-run or different-round cards", () => {
  const terminal = {
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
    status: "completed" as const,
    subagent_type: "test",
    description: "done",
    prompt: "do it",
  };

  expect(
    hasTerminalSubtaskForTask([terminal], {
      threadId: "thread-1",
      runId: "run-1",
      taskId: "task-1",
      roundId: "round-1",
    }),
  ).toBe(false);
  expect(
    hasTerminalSubtaskForTask([terminal], {
      threadId: "thread-1",
      runId: "run-2",
      taskId: "task-1",
      roundId: "round-2",
    }),
  ).toBe(false);
});

test("matching terminal lane keeps its historical task card renderable", () => {
  const terminal = {
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
    status: "completed" as const,
    subagent_type: "test",
    description: "done",
    prompt: "do it",
  };

  expect(
    hasMatchingTerminalSubtaskForTask([terminal], {
      threadId: "thread-1",
      runId: "run-1",
      taskId: "task-1",
      roundId: "round-1",
    }),
  ).toBe(true);
  expect(
    hasMatchingTerminalSubtaskForTask([terminal], {
      threadId: "thread-1",
      runId: "run-2",
      taskId: "task-1",
      roundId: "round-1",
    }),
  ).toBe(false);
});

test("missing historical roundId resolves one matching terminal lane without guessing", () => {
  const terminal = {
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
    status: "completed" as const,
    subagent_type: "evidence",
    description: "Evidence complete",
    prompt: "Collect evidence",
  };

  expect(
    findMatchingTerminalSubtaskForTask([terminal], {
      threadId: "thread-1",
      runId: "run-1",
      taskId: "task-1",
    }),
  ).toEqual(terminal);
  expect(
    findMatchingTerminalSubtaskForTask(
      [terminal, { ...terminal, roundId: "round-2" }],
      {
        threadId: "thread-1",
        runId: "run-1",
        taskId: "task-1",
      },
    ),
  ).toBeUndefined();
});

test("inferred task policy keeps matching terminal metadata updates and drops stale replays", () => {
  expect(
    shouldKeepInferredSubtask({
      status: "in_progress",
      hasMatchingTerminal: true,
      hasTerminalInOtherRun: false,
      isVisibleRunning: false,
    }),
  ).toBe(true);
  expect(
    shouldKeepInferredSubtask({
      status: "in_progress",
      hasMatchingTerminal: false,
      hasTerminalInOtherRun: true,
      isVisibleRunning: true,
    }),
  ).toBe(false);
});

test("runtime-only subtask filter keeps stale tasks out of the active turn", () => {
  const activeRunIds = new Set(["run-new"]);
  const activeRoundIdsByRunId = new Map<string, ReadonlySet<string>>([
    ["run-new", new Set(["round-2"])],
  ]);

  expect(
    isRuntimeOnlySubtaskForActiveTurn(
      { runId: "run-old", startedAt: 1_000 },
      activeRunIds,
      activeRoundIdsByRunId,
      10_000,
    ),
  ).toBe(false);
  expect(
    isRuntimeOnlySubtaskForActiveTurn(
      { runId: "run-new", roundId: "round-2", startedAt: 11_000 },
      activeRunIds,
      activeRoundIdsByRunId,
      10_000,
    ),
  ).toBe(true);
  expect(
    isRuntimeOnlySubtaskForActiveTurn(
      { runId: "run-new", roundId: "round-1", startedAt: 11_000 },
      activeRunIds,
      activeRoundIdsByRunId,
      10_000,
    ),
  ).toBe(false);
});

test("runtime-only subtask filter falls back to current loading start time", () => {
  expect(
    isRuntimeOnlySubtaskForActiveTurn(
      { runId: "run-old", startedAt: 1_000 },
      new Set(),
      new Map(),
      10_000,
    ),
  ).toBe(false);
  expect(
    isRuntimeOnlySubtaskForActiveTurn(
      { runId: "run-new", startedAt: 11_000 },
      new Set(),
      new Map(),
      10_000,
    ),
  ).toBe(true);
});

test("inferred running subtask filter keeps active history runs visible", () => {
  expect(
    isInferredRunningSubtaskVisible({
      runId: "run-active",
      startedAt: 1_000,
      groupIsLoading: false,
      activeRunIds: new Set(["run-active"]),
      turnStartTime: 10_000,
    }),
  ).toBe(true);
});

test("inferred running subtask filter keeps an explicitly active background lane visible", () => {
  expect(
    isInferredRunningSubtaskVisible({
      runId: "run-finished",
      startedAt: 1_000,
      groupIsLoading: false,
      activeRunIds: new Set(),
      turnStartTime: 10_000,
      hasMatchingActiveTask: true,
    }),
  ).toBe(true);
});

test("inferred running subtask filter hides stale historical runs", () => {
  expect(
    isInferredRunningSubtaskVisible({
      runId: "run-old",
      startedAt: 1_000,
      groupIsLoading: false,
      activeRunIds: new Set(["run-active"]),
      turnStartTime: 10_000,
    }),
  ).toBe(false);
});

test("inferred running subtask filter requires current live turn when run is not active", () => {
  expect(
    isInferredRunningSubtaskVisible({
      runId: "run-old",
      startedAt: 1_000,
      groupIsLoading: true,
      activeRunIds: new Set(),
      turnStartTime: 10_000,
    }),
  ).toBe(false);
  expect(
    isInferredRunningSubtaskVisible({
      runId: "run-live",
      groupIsLoading: true,
      activeRunIds: new Set(),
      turnStartTime: 10_000,
    }),
  ).toBe(true);
  expect(
    isInferredRunningSubtaskVisible({
      runId: "run-live",
      startedAt: 11_000,
      groupIsLoading: true,
      activeRunIds: new Set(),
      turnStartTime: 10_000,
    }),
  ).toBe(true);
});

test("getActiveTurnSubtaskScope collects multiple runIds and roundIds after latest human boundary", () => {
  const scope = getActiveTurnSubtaskScope([
    { type: "human", messages: [{ type: "human" }] },
    {
      type: "assistant",
      messages: [
        {
          type: "ai",
          additional_kwargs: {
            deerflow_run_id: "run-1",
            deerflow_round_id: "round-1",
          },
        },
      ],
    },
    {
      type: "tool",
      messages: [
        {
          type: "tool",
          additional_kwargs: {
            deerflow_run_id: "run-2",
            deerflow_round_id: "round-2",
          },
        },
      ],
    },
  ] as TestMessageGroup);

  expect([...scope.runIds].sort()).toEqual(["run-1", "run-2"]);
  expect([...(scope.roundIdsByRunId.get("run-1") ?? [])]).toEqual(["round-1"]);
  expect([...(scope.roundIdsByRunId.get("run-2") ?? [])]).toEqual(["round-2"]);
});

test("getActiveTurnSubtaskScope does not include runIds before latest human boundary", () => {
  const scope = getActiveTurnSubtaskScope([
    {
      type: "assistant",
      messages: [
        {
          type: "ai",
          additional_kwargs: {
            deerflow_run_id: "run-old",
            deerflow_round_id: "round-old",
          },
        },
      ],
    },
    { type: "human", messages: [{ type: "human" }] },
    {
      type: "assistant",
      messages: [
        {
          type: "ai",
          additional_kwargs: {
            deerflow_run_id: "run-new",
            deerflow_round_id: "round-new",
          },
        },
      ],
    },
  ] as TestMessageGroup);

  expect(scope.runIds.has("run-old")).toBe(false);
  expect(scope.runIds.has("run-new")).toBe(true);
});

test("getActiveTurnSubtaskScope adds runId without round filter when roundId is missing", () => {
  const scope = getActiveTurnSubtaskScope([
    { type: "human", messages: [{ type: "human" }] },
    {
      type: "assistant",
      messages: [
        { type: "ai", additional_kwargs: { deerflow_run_id: "run-no-round" } },
      ],
    },
  ] as TestMessageGroup);

  expect(scope.runIds.has("run-no-round")).toBe(true);
  expect(scope.roundIdsByRunId.has("run-no-round")).toBe(false);
});
