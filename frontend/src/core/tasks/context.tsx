import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import type { Subtask } from "./types";

function isTerminalSubtaskStatus(status: Subtask["status"] | undefined) {
  return status === "completed" || status === "failed";
}

export interface SubtaskContextValue {
  tasks: Record<string, Subtask>;
  setTasks: (tasks: Record<string, Subtask>) => void;
}

export type SubtaskUpdate = Partial<Subtask> & {
  id: string;
  threadId?: string | null;
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

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
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
  now = Date.now(),
) {
  const previousStatus = previous?.status;
  const { threadId, ...taskPatch } = task;
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
    ...(task.status === "in_progress" && previous?.startedAt === undefined
      ? { startedAt: task.startedAt ?? now }
      : {}),
    ...(task.status === "in_progress" && previousStatus === "completed"
      ? { status: previousStatus }
      : {}),
  } as Subtask;
}

export function useUpdateSubtask() {
  const { tasks, setTasks } = useSubtaskContext();
  const shouldNotifyAfterRenderRef = useRef(false);
  // No deps: must run after every render to check the ref set during render.
  useEffect(() => {
    if (!shouldNotifyAfterRenderRef.current) {
      return;
    }
    shouldNotifyAfterRenderRef.current = false;
    setTasks({ ...tasks });
  });

  const updateSubtask = useCallback(
    (task: SubtaskUpdate) => {
      const storageKey = getSubtaskStorageKey(task.id, task.threadId);
      const previous = tasks[storageKey];
      const previousStatus = previous?.status;
      const next = mergeSubtaskUpdate(previous, task);

      const becameTerminal =
        isTerminalSubtaskStatus(next.status) && previousStatus !== next.status;

      tasks[storageKey] = next;

      if (task.latestMessage) {
        setTasks({ ...tasks });
      } else if (becameTerminal) {
        shouldNotifyAfterRenderRef.current = true;
      }
    },
    [tasks, setTasks],
  );

  return updateSubtask;
}
