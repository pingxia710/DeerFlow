import { expect, test } from "@rstest/core";

import {
  applySubtaskUpdateInState,
  type SubtaskUpdate,
} from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";
import {
  applyTaskEventToSubtask,
  applyTaskToolResultRunMessages,
  mergeTaskLaneSubtasks,
  terminalTaskToolResult,
} from "@/core/threads/task-events";
import type { RunMessage } from "@/core/threads/types";

test("task events retain explicit delivery-loop facts without reading task prose", () => {
  const updates: SubtaskUpdate[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        type: "task_started",
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        description: "Review the completed work",
        command_room_container: "review",
        work_package_id: "package-a",
        delivery_cycle_index: 2,
        container_artifact_path:
          "/workspace/03-delivery/cycle-02/review/findings.md",
        container_artifact_written: false,
      },
      (update) => updates.push(update),
    ),
  ).toBe(true);

  expect(updates).toEqual([
    {
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      status: "in_progress",
      commandRoomContainer: "review",
      workPackageId: "package-a",
      deliveryCycleIndex: 2,
      containerArtifactPath:
        "/workspace/03-delivery/cycle-02/review/findings.md",
      containerArtifactWritten: false,
      notify: true,
      description: "Review the completed work",
    },
  ]);
});

test("task events leave old tasks neutral instead of inferring a container from text", () => {
  const updates: SubtaskUpdate[] = [];

  applyTaskEventToSubtask(
    {
      type: "task_started",
      task_id: "task-1",
      thread_id: "thread-1",
      run_id: "run-1",
      description: "Plan an evaluation of the collaboration results",
    },
    (update) => updates.push(update),
  );

  expect(updates).toEqual([
    {
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      status: "in_progress",
      notify: true,
      description: "Plan an evaluation of the collaboration results",
    },
  ]);
});

test("task events retain artifact kinds from top-level, metadata, and content fields", () => {
  const updates: SubtaskUpdate[] = [];
  const update = (task: SubtaskUpdate) => updates.push(task);

  for (const event of [
    {
      type: "task_started",
      task_id: "task-top-level",
      thread_id: "thread-1",
      run_id: "run-1",
      container_artifact_kind: "spec",
    },
    {
      type: "task_started",
      task_id: "task-metadata",
      thread_id: "thread-1",
      run_id: "run-1",
      metadata: { container_artifact_kind: "round-note" },
    },
    {
      type: "task_started",
      task_id: "task-content",
      thread_id: "thread-1",
      run_id: "run-1",
      content: { container_artifact_kind: "evaluation" },
    },
    {
      type: "task_started",
      task_id: "task-findings",
      thread_id: "thread-1",
      run_id: "run-1",
      content: { container_artifact_kind: "findings" },
    },
  ]) {
    applyTaskEventToSubtask(event, update);
  }

  expect(
    updates.map(({ id, containerArtifactKind }) => ({
      id,
      containerArtifactKind,
    })),
  ).toEqual([
    { id: "task-top-level", containerArtifactKind: "spec" },
    { id: "task-metadata", containerArtifactKind: "round-note" },
    { id: "task-content", containerArtifactKind: "evaluation" },
    { id: "task-findings", containerArtifactKind: "findings" },
  ]);
});

test("terminal task ToolMessages restore container facts and preserve them across lifecycle updates", () => {
  let tasks: Record<string, Subtask> = {};
  const update = (task: SubtaskUpdate) => {
    tasks = applySubtaskUpdateInState(tasks, task);
  };

  applyTaskEventToSubtask(
    {
      type: "task_started",
      task_id: "task-1",
      thread_id: "thread-1",
      run_id: "run-1",
      command_room_container: "review",
      delivery_cycle_index: 1,
      container_artifact_path:
        "/workspace/03-delivery/cycle-01/review/findings.md",
    },
    update,
  );

  applyTaskToolResultRunMessages(
    [
      {
        run_id: "run-1",
        content: {
          type: "tool",
          name: "task",
          tool_call_id: "task-1",
          content: "Recorder's complete natural-language result",
          additional_kwargs: {
            subagent_status: "completed",
            command_room_container: "review",
            delivery_cycle_index: 1,
            container_artifact_path:
              "/workspace/03-delivery/cycle-01/review/findings.md",
            container_artifact_written: true,
            container_artifact_kind: "findings",
          },
        },
        created_at: "2026-07-14T00:00:00Z",
      } as unknown as RunMessage,
    ],
    update,
    "thread-1",
  );

  expect(Object.values(tasks)).toEqual([
    expect.objectContaining({
      id: "task-1",
      status: "completed",
      commandRoomContainer: "review",
      deliveryCycleIndex: 1,
      containerArtifactPath:
        "/workspace/03-delivery/cycle-01/review/findings.md",
      containerArtifactWritten: true,
      containerArtifactKind: "findings",
      result: "Recorder's complete natural-language result",
    }),
  ]);
});

test("terminal task ToolMessages restore background failure details", () => {
  let tasks: Record<string, Subtask> = {};
  const update = (task: SubtaskUpdate) => {
    tasks = applySubtaskUpdateInState(tasks, task);
  };

  applyTaskEventToSubtask(
    {
      type: "task_started",
      task_id: "task-1",
      thread_id: "thread-1",
      run_id: "run-1",
    },
    update,
  );

  applyTaskToolResultRunMessages(
    [
      {
        run_id: "run-1",
        content: {
          type: "tool",
          name: "task",
          tool_call_id: "task-1",
          content: "Task failed. Error: Child transport stopped after retry.",
          additional_kwargs: { subagent_status: "failed" },
        },
        created_at: "2026-07-16T00:00:00Z",
      } as unknown as RunMessage,
    ],
    update,
    "thread-1",
  );

  expect(Object.values(tasks)).toEqual([
    expect.objectContaining({
      id: "task-1",
      status: "failed",
      error: "Error: Child transport stopped after retry.",
    }),
  ]);
});

test("task lane trajectory keeps unstaged tasks factual and prefers a complete live result", () => {
  const tasks = mergeTaskLaneSubtasks(
    [
      {
        thread_id: "thread-1",
        run_id: "run-1",
        task_id: "task-1",
        role: "executor",
        status: "completed",
        result: "preview only",
        started_at: "2026-07-16T01:00:00.000Z",
      },
    ],
    [
      {
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "completed",
        subagent_type: "executor",
        description: "Execute the work",
        prompt: "",
        result: "complete natural-language result",
        startedAt: Date.parse("2026-07-16T01:00:00.000Z"),
      },
    ],
  );

  expect(tasks).toHaveLength(1);
  expect(tasks[0]).toMatchObject({
    id: "task-1",
    result: "complete natural-language result",
  });
  expect(tasks[0]).not.toHaveProperty("commandRoomContainer");
});

test("terminal task result lookup ignores background receipts", () => {
  const messages = [
    {
      run_id: "run-1",
      content: {
        type: "tool",
        name: "task",
        tool_call_id: "task-1",
        content: "accepted",
        additional_kwargs: { background_task: true },
      },
    },
    {
      run_id: "run-1",
      content: {
        type: "tool",
        name: "task",
        tool_call_id: "task-1",
        content: "complete natural-language result",
        additional_kwargs: { subagent_status: "completed" },
      },
    },
  ] as unknown as RunMessage[];

  expect(terminalTaskToolResult(messages, "task-1")).toBe(
    "complete natural-language result",
  );
});
