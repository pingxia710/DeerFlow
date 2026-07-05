import {
  createContext,
  useCallback,
  useContext,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

import type { Subtask } from "./types";

export interface SubtaskContextValue {
  tasks: Record<string, Subtask>;
  setTasks: Dispatch<SetStateAction<Record<string, Subtask>>>;
}

export type SubtaskUpdate = Partial<Subtask> & {
  id: string;
  threadId?: string | null;
  notify?: boolean;
};

export type RunTerminalSubtaskUpdate = {
  threadId: string;
  runId: string;
  status: string;
  terminalReason?: string;
};

export function getSubtaskStorageKey(id: string, threadId?: string | null) {
  if (!threadId) {
    return id;
  }
  return JSON.stringify([threadId, id]);
}

export const SubtaskContext = createContext<SubtaskContextValue>({
  tasks: {},
  setTasks: () => {
    /* noop */
  },
});

export function SubtasksProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({});
  return (
    <SubtaskContext.Provider value={{ tasks, setTasks }}>
      {children}
    </SubtaskContext.Provider>
  );
}

export function useSubtaskContext() {
  const context = useContext(SubtaskContext);
  if (context === undefined) {
    throw new Error(
      "useSubtaskContext must be used within a SubtaskContext.Provider",
    );
  }
  return context;
}

export function useSubtask(id: string, threadId?: string | null) {
  const { tasks } = useSubtaskContext();
  return tasks[getSubtaskStorageKey(id, threadId)];
}

export function mergeSubtaskUpdate(
  previous: Subtask | undefined,
  task: SubtaskUpdate,
  now?: number,
) {
  const previousStatus = previous?.status;
  const { threadId } = task;
  const taskPatch = { ...task };
  delete taskPatch.threadId;
  delete taskPatch.notify;
  // Completed and task-event terminal failures are definitive. Older UI code
  // also inferred failed from the parent turn ending; allow later running
  // signals to recover that stale local state.
  const isTerminalFailure =
    previousStatus === "failed" &&
    (previous?.actionResultStatus !== undefined ||
      previous?.terminalReason !== undefined);
  return {
    ...previous,
    ...taskPatch,
    ...(threadId ? { threadId } : {}),
    ...(task.status === "in_progress" && previousStatus !== "completed"
      ? { error: undefined, result: undefined }
      : {}),
    ...(task.status === "in_progress" &&
    previous?.startedAt === undefined &&
    (task.startedAt !== undefined || now !== undefined)
      ? { startedAt: task.startedAt ?? now }
      : {}),
    ...(task.status === "in_progress" &&
    (previousStatus === "completed" || isTerminalFailure)
      ? { status: previousStatus }
      : {}),
  } as Subtask;
}

export function didSubtaskChange(previous: Subtask | undefined, next: Subtask) {
  if (!previous) {
    return true;
  }
  const keys = new Set([...Object.keys(previous), ...Object.keys(next)]);
  for (const key of keys) {
    const field = key as keyof Subtask;
    if (!Object.is(previous[field], next[field])) {
      return true;
    }
  }
  return false;
}

function runTerminalSubtaskError(status: string, terminalReason?: string) {
  if (status === "timeout") {
    return "Parent run timed out before this subtask completed.";
  }
  if (status === "interrupted") {
    return "Parent run stopped before this subtask completed.";
  }
  if (status === "error") {
    return "Parent run failed before this subtask completed.";
  }
  return `Parent run ended before this subtask completed${terminalReason ? `: ${terminalReason}` : "."}`;
}

export function settleRunningSubtasksForRun(
  tasks: Record<string, Subtask>,
  terminal: RunTerminalSubtaskUpdate,
) {
  if (terminal.status === "success") {
    return tasks;
  }

  let changed = false;
  const nextTasks: Record<string, Subtask> = { ...tasks };
  for (const [storageKey, task] of Object.entries(tasks)) {
    if (
      task.threadId !== terminal.threadId ||
      task.runId !== terminal.runId ||
      task.status !== "in_progress"
    ) {
      continue;
    }

    const next = mergeSubtaskUpdate(task, {
      id: task.id,
      threadId: terminal.threadId,
      runId: terminal.runId,
      status: "failed",
      error: runTerminalSubtaskError(terminal.status, terminal.terminalReason),
      actionResultStatus: terminal.status,
      terminalReason: terminal.terminalReason ?? terminal.status,
      notify: true,
    });
    if (!didSubtaskChange(task, next)) {
      continue;
    }
    nextTasks[storageKey] = next;
    changed = true;
  }

  return changed ? nextTasks : tasks;
}

export function useSettleRunningSubtasksForRun() {
  const { setTasks } = useSubtaskContext();

  return useCallback(
    (terminal: RunTerminalSubtaskUpdate) => {
      setTasks((currentTasks) =>
        settleRunningSubtasksForRun(currentTasks, terminal),
      );
    },
    [setTasks],
  );
}

export function useUpdateSubtask() {
  const { tasks, setTasks } = useSubtaskContext();

  const updateSubtask = useCallback(
    (task: SubtaskUpdate) => {
      if (task.notify || task.latestMessage) {
        setTasks((currentTasks) => {
          const storageKey = getSubtaskStorageKey(task.id, task.threadId);
          const previous = currentTasks[storageKey];
          const next = mergeSubtaskUpdate(previous, task);

          if (!didSubtaskChange(previous, next)) {
            return currentTasks;
          }

          return { ...currentTasks, [storageKey]: next };
        });
        return;
      }

      const storageKey = getSubtaskStorageKey(task.id, task.threadId);
      const previous = tasks[storageKey];
      const next = mergeSubtaskUpdate(previous, task);

      if (!didSubtaskChange(previous, next)) {
        return;
      }

      tasks[storageKey] = next;
    },
    [tasks, setTasks],
  );

  return updateSubtask;
}
