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

export type SubtaskStorageKeyInput = {
  id: string;
  threadId?: string | null;
  runId?: string | null;
  roundId?: string | null;
};

export function getLegacySubtaskStorageKey(
  id: string,
  threadId?: string | null,
) {
  if (!threadId) {
    return id;
  }
  return JSON.stringify([threadId, id]);
}

function normalizeSubtaskStorageKeyInput(
  input: SubtaskStorageKeyInput | string,
  threadId?: string | null,
): SubtaskStorageKeyInput {
  return typeof input === "string" ? { id: input, threadId } : input;
}

export function getSubtaskStorageKey(input: SubtaskStorageKeyInput): string;
export function getSubtaskStorageKey(
  id: string,
  threadId?: string | null,
): string;
export function getSubtaskStorageKey(
  input: SubtaskStorageKeyInput | string,
  threadId?: string | null,
) {
  const keyInput = normalizeSubtaskStorageKeyInput(input, threadId);
  if (!keyInput.threadId || !keyInput.runId) {
    return getLegacySubtaskStorageKey(keyInput.id, keyInput.threadId);
  }
  return JSON.stringify([
    "subtask",
    keyInput.threadId,
    keyInput.runId,
    keyInput.id,
  ]);
}

export function getSubtaskLookupKeys(input: SubtaskStorageKeyInput): string[] {
  const storageKey = getSubtaskStorageKey(input);
  if (input.threadId && input.runId) {
    return [storageKey];
  }
  const legacyKey = getLegacySubtaskStorageKey(input.id, input.threadId);
  return storageKey === legacyKey ? [storageKey] : [storageKey, legacyKey];
}

function subtaskStorageKeyThreadId(storageKey: string) {
  try {
    const parts = JSON.parse(storageKey) as unknown;
    if (!Array.isArray(parts)) {
      return undefined;
    }
    if (parts[0] === "subtask") {
      return typeof parts[1] === "string" ? parts[1] : undefined;
    }
    return typeof parts[0] === "string" ? parts[0] : undefined;
  } catch {
    return undefined;
  }
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

export function useSubtask(input: SubtaskStorageKeyInput): Subtask | undefined;
export function useSubtask(
  id: string,
  threadId?: string | null,
): Subtask | undefined;
export function useSubtask(
  input: SubtaskStorageKeyInput | string,
  threadId?: string | null,
) {
  const { tasks } = useSubtaskContext();
  const keyInput = normalizeSubtaskStorageKeyInput(input, threadId);
  for (const storageKey of getSubtaskLookupKeys(keyInput)) {
    const task = tasks[storageKey];
    if (task) {
      return task;
    }
  }
  return undefined;
}

export function mergeSubtaskUpdate(
  previous: Subtask | undefined,
  task: SubtaskUpdate,
  now?: number,
) {
  const previousStatus = previous?.status;
  const isTerminalStatus =
    task.status === "completed" || task.status === "failed";
  const { roundId, runId, threadId } = task;
  const hasStrongerTiming =
    task.startedAt !== undefined ||
    task.finishedAt !== undefined ||
    task.durationMs !== undefined;
  const taskPatch = { ...task };
  if (previous?.startedAt !== undefined && task.startedAt === undefined) {
    delete taskPatch.startedAt;
  }
  if (
    previous?.finishedAt !== undefined &&
    (task.finishedAt === undefined || task.finishedAt < previous.finishedAt)
  ) {
    delete taskPatch.finishedAt;
  }
  if (previous?.durationMs !== undefined && task.durationMs === undefined) {
    delete taskPatch.durationMs;
  }
  delete taskPatch.threadId;
  delete taskPatch.runId;
  delete taskPatch.roundId;
  delete taskPatch.notify;
  // Completed and task-event terminal failures are definitive. Older UI code
  // also inferred failed from the parent turn ending; allow later running
  // signals to recover that stale local state.
  const isTerminalFailure =
    previousStatus === "failed" &&
    (previous?.actionResultStatus !== undefined ||
      previous?.terminalReason !== undefined);
  const isTerminalCompleted = previousStatus === "completed";
  const shouldKeepTerminalStatus =
    task.status === "in_progress" && (isTerminalCompleted || isTerminalFailure);
  return {
    ...previous,
    ...taskPatch,
    ...(threadId ? { threadId } : {}),
    ...(runId ? { runId } : {}),
    ...(roundId ? { roundId } : {}),
    ...(task.status === "in_progress" && previousStatus !== "completed"
      ? { error: undefined, result: undefined }
      : {}),
    ...(task.startedAt !== undefined &&
    (previous?.startedAt === undefined || task.startedAt < previous.startedAt)
      ? { startedAt: task.startedAt }
      : {}),
    ...(task.status === "in_progress" &&
    previous?.startedAt === undefined &&
    (task.startedAt !== undefined || now !== undefined)
      ? { startedAt: task.startedAt ?? now }
      : {}),
    ...(isTerminalStatus &&
    previous?.finishedAt === undefined &&
    hasStrongerTiming
      ? { finishedAt: task.finishedAt ?? now ?? Date.now() }
      : {}),
    ...(task.finishedAt !== undefined &&
    (previous?.finishedAt === undefined ||
      task.finishedAt > previous.finishedAt)
      ? { finishedAt: task.finishedAt }
      : {}),
    ...(task.durationMs !== undefined &&
    (previous?.durationMs === undefined ||
      task.durationMs > previous.durationMs)
      ? { durationMs: task.durationMs }
      : {}),
    ...(shouldKeepTerminalStatus
      ? {
          status: previousStatus,
          ...(isTerminalCompleted ? { result: previous?.result } : {}),
          ...(isTerminalFailure ? { error: previous?.error } : {}),
        }
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

function getSubtaskUpdateStorageKey(
  tasks: Record<string, Subtask>,
  task: SubtaskUpdate,
) {
  if (task.threadId && !task.runId) {
    const matchingStrongKeys = Object.keys(tasks).filter((storageKey) => {
      try {
        const parts = JSON.parse(storageKey) as unknown;
        return (
          Array.isArray(parts) &&
          parts[0] === "subtask" &&
          parts[1] === task.threadId &&
          parts[3] === task.id
        );
      } catch {
        return false;
      }
    });

    if (matchingStrongKeys.length === 1) {
      return matchingStrongKeys[0]!;
    }
  }

  return getSubtaskStorageKey({
    id: task.id,
    threadId: task.threadId,
    runId: task.runId,
    roundId: task.roundId,
  });
}

export function applySubtaskUpdateInState(
  tasks: Record<string, Subtask>,
  task: SubtaskUpdate,
) {
  const storageKey = getSubtaskUpdateStorageKey(tasks, task);
  const previous = tasks[storageKey];
  const next = mergeSubtaskUpdate(previous, task);

  if (!didSubtaskChange(previous, next)) {
    return tasks;
  }

  return { ...tasks, [storageKey]: next };
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
  const now = Date.now();
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
      finishedAt: now,
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

export function clearSubtasksForThreadInState(
  tasks: Record<string, Subtask>,
  threadId: string,
) {
  let changed = false;
  const nextTasks: Record<string, Subtask> = {};
  for (const [storageKey, task] of Object.entries(tasks)) {
    if (
      task.threadId === threadId ||
      subtaskStorageKeyThreadId(storageKey) === threadId
    ) {
      changed = true;
      continue;
    }
    nextTasks[storageKey] = task;
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

export function useClearSubtasksForThread() {
  const { setTasks } = useSubtaskContext();

  return useCallback(
    (threadId: string) => {
      setTasks((currentTasks) =>
        clearSubtasksForThreadInState(currentTasks, threadId),
      );
    },
    [setTasks],
  );
}

export function useUpdateSubtask() {
  const { setTasks } = useSubtaskContext();

  const updateSubtask = useCallback(
    (task: SubtaskUpdate) => {
      setTasks((currentTasks) => applySubtaskUpdateInState(currentTasks, task));
    },
    [setTasks],
  );

  return updateSubtask;
}
