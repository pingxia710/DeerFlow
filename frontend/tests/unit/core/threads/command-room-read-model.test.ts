import { expect, test } from "@rstest/core";

import {
  buildCommandRoomReadModel,
  parseWakeFactsProjection,
} from "@/core/threads/command-room-read-model";

test("command-room read model separates strong current-round lanes from legacy lanes", () => {
  const model = buildCommandRoomReadModel({
    threadId: "thread-1",
    runs: [
      { run_id: "run-2", status: "running", round_id: "round-2" },
      { run_id: "run-1", status: "success", round_id: "round-1" },
    ],
    rounds: [
      {
        round_id: "round-2",
        thread_id: "thread-1",
        current_run_id: "run-2",
      },
    ],
    taskLanes: [
      {
        thread_id: "thread-1",
        run_id: "run-2",
        round_id: "round-2",
        task_id: "task-current",
        status: "running",
      },
      {
        thread_id: "thread-1",
        run_id: "run-2",
        task_id: "task-legacy",
        status: "running",
      },
      {
        thread_id: "thread-1",
        run_id: "run-1",
        round_id: "round-1",
        task_id: "task-old",
        status: "completed",
      },
    ],
  });

  expect(model.activeRun?.run_id).toBe("run-2");
  expect(model.activeRound?.round_id).toBe("round-2");
  expect(model.taskLanes.map((lane) => lane.task_id)).toEqual(["task-current"]);
  expect(model.legacyTaskLanes.map((lane) => lane.task_id)).toEqual([
    "task-legacy",
  ]);
});

test("command-room read model never admits another thread into the projection", () => {
  const model = buildCommandRoomReadModel({
    threadId: "thread-1",
    runs: [{ run_id: "run-1", status: "running", round_id: "round-1" }],
    rounds: [
      {
        round_id: "round-1",
        thread_id: "thread-1",
        current_run_id: "run-1",
      },
    ],
    taskLanes: [
      {
        thread_id: "thread-2",
        run_id: "run-1",
        round_id: "round-1",
        task_id: "foreign-task",
        status: "running",
      },
    ],
  });

  expect(model.taskLanes).toEqual([]);
  expect(model.legacyTaskLanes).toEqual([]);
});

test("wake-facts parser accepts only the narrow, identity-bound public contract", () => {
  const scope = { threadId: "thread-1", runId: "run-1", roundId: "round-1" };
  const projection = parseWakeFactsProjection(
    {
      thread_id: "thread-1",
      run_id: "run-1",
      round_id: "round-1",
      items: [
        {
          task_id: "task-1",
          source_run_id: "run-1",
          child_status: "completed",
          child_completed_at: "2026-07-17T00:00:01Z",
          wake_state: "failed",
          wake_attempts: 3,
          wake_failure_reason: "retry_exhausted",
          updated_at: "2026-07-17T00:00:04Z",
          handoff: { result: "must not reach the UI" },
          error: "must not reach the UI",
        },
        {
          task_id: "task-other-run",
          source_run_id: "run-other",
          child_status: "completed",
          child_completed_at: null,
          wake_state: "failed",
          wake_attempts: 1,
          updated_at: "2026-07-17T00:00:04Z",
        },
      ],
    },
    scope,
  );

  expect(projection).toEqual({
    thread_id: "thread-1",
    run_id: "run-1",
    round_id: "round-1",
    items: [
      {
        task_id: "task-1",
        source_run_id: "run-1",
        child_status: "completed",
        child_completed_at: "2026-07-17T00:00:01Z",
        wake_state: "failed",
        wake_attempts: 3,
        wake_failure_reason: "retry_exhausted",
        updated_at: "2026-07-17T00:00:04Z",
      },
    ],
  });
  expect(
    parseWakeFactsProjection({ ...projection, round_id: "round-other" }, scope)
      .items,
  ).toEqual([]);
});
