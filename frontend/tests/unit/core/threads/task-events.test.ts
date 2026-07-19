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

test("task events preserve factual identity and timing", () => {
  const updates: SubtaskUpdate[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        type: "task_started",
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        round_id: "round-1",
        description: "Inspect the current implementation",
        subagent_type: "fact-finder",
        started_at: "2026-07-17T00:00:00Z",
      },
      (update) => updates.push(update),
    ),
  ).toBe(true);

  expect(updates).toEqual([
    {
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-1",
      status: "in_progress",
      startedAt: Date.parse("2026-07-17T00:00:00Z"),
      notify: true,
      description: "Inspect the current implementation",
      subagent_type: "fact-finder",
    },
  ]);
});

test("task events preserve explicit artifact references without parsing prose", () => {
  const updates: SubtaskUpdate[] = [];

  applyTaskEventToSubtask(
    {
      type: "task_completed",
      task_id: "task-1",
      thread_id: "thread-1",
      run_id: "run-1",
      artifact_refs: ["/mnt/user-data/outputs/report.md"],
      result_preview: "Report completed at /mnt/user-data/outputs/report.md",
    },
    (update) => updates.push(update),
  );

  expect(updates[0]?.metadata).toEqual({
    refs: { artifact_refs: ["/mnt/user-data/outputs/report.md"] },
  });
  expect(updates[0]?.result).toBe(
    "Report completed at /mnt/user-data/outputs/report.md",
  );
});

test("terminal task ToolMessages restore the complete natural result", () => {
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
          content: "Complete natural-language result from the child AI.",
          additional_kwargs: {
            subagent_status: "completed",
            round_id: "round-1",
          },
        },
        created_at: "2026-07-17T00:00:01Z",
      } as unknown as RunMessage,
    ],
    update,
    "thread-1",
  );

  expect(Object.values(tasks)).toEqual([
    expect.objectContaining({
      id: "task-1",
      roundId: "round-1",
      status: "completed",
      result: "Complete natural-language result from the child AI.",
    }),
  ]);
});

test("terminal task ToolMessages restore transport failure details", () => {
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
        created_at: "2026-07-17T00:00:01Z",
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

test("task lane facts merge with a complete live result", () => {
  const tasks = mergeTaskLaneSubtasks(
    [
      {
        thread_id: "thread-1",
        run_id: "run-1",
        round_id: "round-1",
        task_id: "task-1",
        role: "executor",
        status: "completed",
        result: "preview only",
        started_at: "2026-07-17T01:00:00.000Z",
      },
    ],
    [
      {
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        roundId: "round-1",
        status: "completed",
        subagent_type: "executor",
        description: "Execute the work",
        prompt: "",
        result: "complete natural-language result",
        startedAt: Date.parse("2026-07-17T01:00:00.000Z"),
      },
    ],
  );

  expect(tasks).toHaveLength(1);
  expect(tasks[0]).toMatchObject({
    id: "task-1",
    result: "complete natural-language result",
  });
});

test("queued task events and snapshots remain distinct from running tasks", () => {
  const updates: SubtaskUpdate[] = [];
  let tasks: Record<string, Subtask> = {};
  const update = (task: SubtaskUpdate) => {
    updates.push(task);
    tasks = applySubtaskUpdateInState(tasks, task);
  };
  const pendingEvent = {
    type: "task_started",
    task_id: "task-queued",
    thread_id: "thread-1",
    run_id: "run-1",
    status: "pending",
    description: "Wait for a worker",
  };

  expect(applyTaskEventToSubtask(pendingEvent, update)).toBe(true);
  expect(updates[0]).toMatchObject({
    id: "task-queued",
    status: "queued",
    description: "Wait for a worker",
  });
  expect(updates[0]).not.toHaveProperty("startedAt");
  expect(
    mergeTaskLaneSubtasks(
      [
        {
          thread_id: "thread-1",
          run_id: "run-1",
          task_id: "task-queued",
          status: "pending",
        },
      ],
      Object.values(tasks),
    )[0]?.status,
  ).toBe("queued");

  expect(
    mergeTaskLaneSubtasks(
      [
        {
          thread_id: "thread-1",
          run_id: "run-1",
          task_id: "task-queued",
          status: "pending",
        },
        {
          thread_id: "thread-1",
          run_id: "run-1",
          task_id: "task-running",
          status: "running",
          started_at: "2026-07-17T01:00:00.000Z",
        },
      ],
      [],
    ).map((task) => [task.id, task.status]),
  ).toEqual([
    ["task-running", "in_progress"],
    ["task-queued", "queued"],
  ]);

  expect(
    applyTaskEventToSubtask(
      { ...pendingEvent, type: "task_running", status: "in_progress" },
      update,
    ),
  ).toBe(true);
  expect(updates[1]).toMatchObject({
    id: "task-queued",
    status: "in_progress",
  });
  expect(
    mergeTaskLaneSubtasks(
      [
        {
          thread_id: "thread-1",
          run_id: "run-1",
          task_id: "task-queued",
          status: "pending",
        },
      ],
      Object.values(tasks),
    )[0]?.status,
  ).toBe("in_progress");
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
