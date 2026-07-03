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
  // Completed is definitive. A failed status can be explicit, but older UI
  // code also inferred it from the parent turn ending; allow later running
  // signals to recover that stale local state. Explicit ToolMessage failures
  // are applied again after the pending AI tool call is scanned.
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
    ...(task.status === "in_progress" && previousStatus === "completed"
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
