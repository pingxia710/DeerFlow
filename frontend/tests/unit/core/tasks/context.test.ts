import { expect, test } from "@rstest/core";

import {
  applySubtaskUpdateInState,
  getLegacySubtaskStorageKey,
  getSubtaskLookupKeys,
  getSubtaskStorageKey,
  mergeSubtaskUpdate,
  type SubtaskUpdate,
} from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

function subtaskFixture(
  overrides: Partial<Subtask> & Pick<Subtask, "id" | "status">,
): Subtask {
  return {
    subagent_type: "test-subagent",
    description: "Test subtask",
    prompt: "Test prompt",
    ...overrides,
  };
}

function subtaskUpdateFixture(
  overrides: SubtaskUpdate & Pick<SubtaskUpdate, "id" | "status">,
): SubtaskUpdate {
  return {
    subagent_type: "test-subagent",
    description: "Test subtask",
    prompt: "Test prompt",
    ...overrides,
  };
}

test("strong subtask lookup with runId does not fall back to legacy state", () => {
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  const strongKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-2",
  });
  const tasks = {
    [legacyKey]: subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      status: "completed",
    }),
  };

  expect(
    getSubtaskLookupKeys({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-2",
    }),
  ).toEqual([strongKey]);
  expect(
    tasks[
      getSubtaskLookupKeys({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-2",
      })[0]!
    ],
  ).toBeUndefined();
});

test("legacy subtask lookup without runId still uses legacy key", () => {
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  expect(getSubtaskLookupKeys({ id: "task-1", threadId: "thread-1" })).toEqual([
    legacyKey,
  ]);
});

test("subtask updates with runId are stored under strong run-scoped key", () => {
  const tasks = applySubtaskUpdateInState(
    {},
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-2",
      status: "in_progress",
    }),
  );

  expect(Object.keys(tasks)).toEqual([
    getSubtaskStorageKey({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-2",
    }),
  ]);
});

test("legacy update without runId updates unique existing run-scoped task without creating legacy duplicate", () => {
  const strongKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
  });
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  const tasks = applySubtaskUpdateInState(
    {
      [strongKey]: subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "in_progress",
      }),
    },
    subtaskUpdateFixture({
      id: "task-1",
      threadId: "thread-1",
      status: "completed",
    }),
  );

  expect(tasks[strongKey]).toMatchObject({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    status: "completed",
  });
  expect(tasks[legacyKey]).toBeUndefined();
});

test("legacy update without runId does not choose among multiple matching run-scoped tasks", () => {
  const strongKey1 = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
  });
  const strongKey2 = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-2",
  });
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  const tasks = applySubtaskUpdateInState(
    {
      [strongKey1]: subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "in_progress",
      }),
      [strongKey2]: subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-2",
        status: "in_progress",
      }),
    },
    subtaskUpdateFixture({
      id: "task-1",
      threadId: "thread-1",
      status: "completed",
    }),
  );

  expect(tasks[strongKey1]?.status).toBe("in_progress");
  expect(tasks[strongKey2]?.status).toBe("in_progress");
  expect(tasks[legacyKey]).toMatchObject({
    id: "task-1",
    threadId: "thread-1",
    status: "completed",
  });
});

test("legacy-only update still uses legacy key", () => {
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  const tasks = applySubtaskUpdateInState(
    {},
    subtaskUpdateFixture({
      id: "task-1",
      threadId: "thread-1",
      status: "in_progress",
    }),
  );

  expect(Object.keys(tasks)).toEqual([legacyKey]);
  expect(tasks[legacyKey]).toMatchObject({
    id: "task-1",
    threadId: "thread-1",
    status: "in_progress",
  });
});

test("later running update does not overwrite completed or terminal failed subtask", () => {
  expect(
    mergeSubtaskUpdate(
      subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "completed",
      }),
      subtaskUpdateFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "in_progress",
      }),
    ).status,
  ).toBe("completed");

  expect(
    mergeSubtaskUpdate(
      subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "failed",
        terminalReason: "error",
      }),
      subtaskUpdateFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "in_progress",
      }),
    ).status,
  ).toBe("failed");
});
