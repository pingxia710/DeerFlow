import { expect, test } from "@rstest/core";

import {
  buildCommandRoomReadModel,
  buildCommandRoomTrajectory,
  groupCommandRoomDeliveryCycles,
  groupCommandRoomTrajectoryByWorkPackage,
  parseWakeFactsProjection,
  splitCommandRoomTrajectory,
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
        state: "active",
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
        state: "active",
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

test("trajectory groups only explicit containers and preserves delivery cycles", () => {
  const trajectory = buildCommandRoomTrajectory([
    {
      id: "plan-forward",
      threadId: "thread-1",
      runId: "run-plan",
      status: "completed",
      subagent_type: "planner",
      description: "Forward plan",
      prompt: "",
      commandRoomContainer: "planning",
      startedAt: 10,
    },
    {
      id: "plan-opposition",
      threadId: "thread-1",
      runId: "run-plan",
      status: "completed",
      subagent_type: "opposition",
      description: "Opposition plan",
      prompt: "",
      commandRoomContainer: "planning",
      startedAt: 11,
    },
    {
      id: "execution-1",
      threadId: "thread-1",
      runId: "run-execution-1",
      status: "completed",
      subagent_type: "executor",
      description: "Execution",
      prompt: "",
      commandRoomContainer: "execution",
      deliveryCycleIndex: 1,
      result: "Execution result",
      startedAt: 20,
    },
    {
      id: "review-1",
      threadId: "thread-1",
      runId: "run-review-1",
      status: "completed",
      subagent_type: "reviewer",
      description: "Review",
      prompt: "",
      commandRoomContainer: "review",
      deliveryCycleIndex: 1,
      result: "Review result",
      startedAt: 30,
    },
    {
      id: "ordinary-task",
      threadId: "thread-1",
      runId: "run-ordinary",
      status: "completed",
      subagent_type: "general",
      description: "Contains planning words but no declared container",
      prompt: "",
      startedAt: 40,
    },
  ]);

  expect(
    trajectory.map((step) => [
      step.container,
      step.deliveryCycleIndex,
      step.tasks.map((task) => task.id),
    ]),
  ).toEqual([
    ["planning", undefined, ["plan-forward", "plan-opposition"]],
    ["execution", 1, ["execution-1"]],
    ["review", 1, ["review-1"]],
  ]);
});

test("trajectory separates plan research and a recorded plan from delivery", () => {
  const sections = splitCommandRoomTrajectory(
    buildCommandRoomTrajectory([
      {
        id: "forward",
        threadId: "thread-1",
        runId: "run-1",
        status: "completed",
        subagent_type: "planner",
        description: "Forward plan",
        prompt: "",
        commandRoomContainer: "planning",
        containerArtifactKind: "planning-forward",
      },
      {
        id: "spec",
        threadId: "thread-1",
        runId: "run-2",
        status: "completed",
        subagent_type: "recorder",
        description: "Record plan",
        prompt: "",
        commandRoomContainer: "planning",
        containerArtifactKind: "spec",
      },
      {
        id: "execution",
        threadId: "thread-1",
        runId: "run-3",
        status: "in_progress",
        subagent_type: "executor",
        description: "Execution",
        prompt: "",
        commandRoomContainer: "execution",
      },
    ]),
  );

  expect(sections.planResearch.map((step) => step.tasks[0]?.id)).toEqual([
    "forward",
  ]);
  expect(sections.planProposals.map((step) => step.tasks[0]?.id)).toEqual([
    "spec",
  ]);
  expect(sections.delivery.map((step) => step.tasks[0]?.id)).toEqual([
    "execution",
  ]);
});

test("trajectory keeps context and delivery steps in separate work packages", () => {
  const trajectory = buildCommandRoomTrajectory([
    {
      id: "package-a-execution",
      threadId: "thread-1",
      runId: "run-a",
      status: "in_progress",
      subagent_type: "executor",
      description: "Execute package A",
      prompt: "",
      commandRoomContainer: "execution",
      deliveryCycleIndex: 1,
      workPackageId: "package-a",
      startedAt: 10,
    },
    {
      id: "package-b-context",
      threadId: "thread-1",
      runId: "run-b",
      status: "completed",
      subagent_type: "planner",
      description: "Discover package B",
      prompt: "",
      commandRoomContainer: "context",
      containerArtifactKind: "context-discovery",
      workPackageId: "package-b",
      startedAt: 11,
    },
  ]);

  const packages = groupCommandRoomTrajectoryByWorkPackage(trajectory);
  expect(
    packages.map((workPackage) => [
      workPackage.workPackageId,
      workPackage.steps.map((step) => step.container),
    ]),
  ).toEqual([
    ["package-a", ["execution"]],
    ["package-b", ["context"]],
  ]);
  expect(splitCommandRoomTrajectory(packages[1]!.steps).context).toHaveLength(
    1,
  );
});

test("delivery navigation groups only explicit execution and review cycles", () => {
  const cycles = groupCommandRoomDeliveryCycles([
    {
      id: "execute-a",
      status: "completed",
      subagent_type: "executor",
      description: "Execute A",
      prompt: "",
      commandRoomContainer: "execution",
      deliveryCycleIndex: 1,
      workPackageId: "package-a",
      startedAt: 10,
    },
    {
      id: "review-a",
      status: "in_progress",
      subagent_type: "reviewer",
      description: "Review A",
      prompt: "",
      commandRoomContainer: "review",
      deliveryCycleIndex: 1,
      workPackageId: "package-a",
      startedAt: 20,
    },
    {
      id: "free-task",
      status: "completed",
      subagent_type: "general",
      description: "Free task",
      prompt: "",
      commandRoomContainer: "execution",
      startedAt: 30,
    },
  ]);

  expect(cycles).toHaveLength(1);
  expect(cycles[0]).toMatchObject({
    index: 1,
    workPackageId: "package-a",
    status: "in_progress",
  });
  expect(cycles[0]?.tasks.map((task) => task.id)).toEqual([
    "execute-a",
    "review-a",
  ]);
});
