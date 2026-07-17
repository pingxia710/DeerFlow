import {
  createContext,
  useCallback,
  useContext,
  useMemo,
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
  runId?: string | null;
  roundId?: string | null;
  notify?: boolean;
};

export type RunTerminalSubtaskUpdate = {
  threadId: string;
  runId: string;
  roundId?: string | null;
  status: string;
  terminalReason?: string;
};

export type SubtaskStorageKeyInput = {
  id: string;
  threadId?: string | null;
  runId?: string | null;
  roundId?: string | null;
};

export const SUBTASK_NO_ROUND_ID = "__no_round__";

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

export function normalizeSubtaskRoundId(roundId?: string | null) {
  return typeof roundId === "string" && roundId.length > 0
    ? roundId
    : SUBTASK_NO_ROUND_ID;
}

function getLegacyRunSubtaskStorageKey(input: SubtaskStorageKeyInput) {
  return JSON.stringify(["subtask", input.threadId, input.runId, input.id]);
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
    normalizeSubtaskRoundId(keyInput.roundId),
    keyInput.id,
  ]);
}

export function getSubtaskLookupKeys(input: SubtaskStorageKeyInput): string[] {
  const storageKey = getSubtaskStorageKey(input);
  if (input.threadId && input.runId) {
    const noRoundKey = input.roundId
      ? getSubtaskStorageKey({ ...input, roundId: undefined })
      : undefined;
    return [
      storageKey,
      noRoundKey,
      getLegacyRunSubtaskStorageKey(input),
      getLegacySubtaskStorageKey(input.id, input.threadId),
      input.id,
    ].filter(
      (key, index, keys): key is string =>
        typeof key === "string" && keys.indexOf(key) === index,
    );
  }
  const legacyKey = getLegacySubtaskStorageKey(input.id, input.threadId);
  return [storageKey, legacyKey, input.id].filter(
    (key, index, keys) => keys.indexOf(key) === index,
  );
}

function parseSubtaskStorageKey(storageKey: string): SubtaskStorageKeyInput {
  try {
    const parts = JSON.parse(storageKey) as unknown;
    if (!Array.isArray(parts)) {
      return { id: storageKey };
    }
    if (parts[0] === "subtask") {
      const hasRoundId = typeof parts[4] === "string";
      return {
        threadId: typeof parts[1] === "string" ? parts[1] : undefined,
        runId: typeof parts[2] === "string" ? parts[2] : undefined,
        roundId:
          hasRoundId && typeof parts[3] === "string" ? parts[3] : undefined,
        id: hasRoundId
          ? parts[4]
          : typeof parts[3] === "string"
            ? parts[3]
            : storageKey,
      };
    }
    return {
      threadId: typeof parts[0] === "string" ? parts[0] : undefined,
      id: typeof parts[1] === "string" ? parts[1] : storageKey,
    };
  } catch {
    return { id: storageKey };
  }
}

function subtaskStorageKeyThreadId(storageKey: string) {
  return parseSubtaskStorageKey(storageKey).threadId;
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
    if (task && subtaskMatchesInput(task, keyInput)) {
      return task;
    }
  }
  return undefined;
}

function subtaskIdentity(task: Subtask) {
  return JSON.stringify([
    task.threadId ?? "",
    task.runId ?? "",
    normalizeSubtaskRoundId(task.roundId),
    task.id,
  ]);
}

function dedupeSubtasks(tasks: Subtask[]) {
  const byIdentity = new Map<string, Subtask>();
  for (const task of tasks) {
    byIdentity.set(subtaskIdentity(task), task);
  }
  return [...byIdentity.values()];
}

function subtaskMatchesInput(task: Subtask, input: SubtaskStorageKeyInput) {
  if (task.id !== input.id) {
    return false;
  }
  if (input.threadId && task.threadId && task.threadId !== input.threadId) {
    return false;
  }
  if (input.runId && task.runId && task.runId !== input.runId) {
    return false;
  }
  if (
    input.threadId &&
    input.runId &&
    task.roundId &&
    normalizeSubtaskRoundId(task.roundId) !==
      normalizeSubtaskRoundId(input.roundId)
  ) {
    return false;
  }
  return true;
}

export function selectSubtasksForRun(
  tasks: Record<string, Subtask>,
  threadId?: string | null,
  runId?: string | null,
  roundId?: string | null,
): Subtask[] {
  if (!threadId || !runId) {
    return [];
  }
  const hasRoundFilter = roundId !== undefined;
  const normalizedRoundId = normalizeSubtaskRoundId(roundId);
  return dedupeSubtasks(
    Object.values(tasks).filter(
      (task) =>
        task.threadId === threadId &&
        task.runId === runId &&
        (!hasRoundFilter ||
          normalizeSubtaskRoundId(task.roundId) === normalizedRoundId),
    ),
  );
}

export function useSubtasksForRun(
  threadId?: string | null,
  runId?: string | null,
  roundId?: string | null,
): Subtask[] {
  const { tasks } = useSubtaskContext();
  return useMemo(
    () => selectSubtasksForRun(tasks, threadId, runId, roundId),
    [tasks, threadId, runId, roundId],
  );
}

export function selectSubtasksForThread(
  tasks: Record<string, Subtask>,
  threadId?: string | null,
): Subtask[] {
  if (!threadId) {
    return [];
  }
  return dedupeSubtasks(
    Object.values(tasks).filter((task) => task.threadId === threadId),
  );
}

export function useSubtasksForThread(threadId?: string | null): Subtask[] {
  const { tasks } = useSubtaskContext();
  return useMemo(
    () => selectSubtasksForThread(tasks, threadId),
    [tasks, threadId],
  );
}

export function mergeSubtaskUpdate(
  previous: Subtask | undefined,
  task: SubtaskUpdate,
  now?: number,
) {
  const previousStatus = previous?.status;
  const isTerminalStatus =
    task.status === "completed" ||
    task.status === "failed" ||
    task.status === "unknown";
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
  const isRecoveryStatusUnknown =
    previousStatus === "unknown" &&
    previous?.terminalReason === "recovery_exhausted";
  const shouldKeepTerminalStatus =
    task.status === "in_progress" &&
    (isTerminalCompleted || isTerminalFailure || isRecoveryStatusUnknown);
  const shouldKeepExistingMetadata =
    previous !== undefined &&
    (task.description === undefined ||
      task.description === previous.description ||
      task.description === `${previous.subagent_type} task` ||
      task.description === `${task.subagent_type} task` ||
      task.description === task.id);
  if (shouldKeepExistingMetadata) {
    delete taskPatch.description;
    delete taskPatch.prompt;
    delete taskPatch.subagent_type;
  }
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
  if (task.threadId && task.runId) {
    return getSubtaskStorageKey({
      id: task.id,
      threadId: task.threadId,
      runId: task.runId,
      roundId: task.roundId,
    });
  }

  if (task.threadId && !task.runId) {
    const matchingStrongKeys = Object.keys(tasks).filter((storageKey) => {
      const parsed = parseSubtaskStorageKey(storageKey);
      return (
        parsed.threadId === task.threadId &&
        Boolean(parsed.runId) &&
        parsed.id === task.id
      );
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
  const previousKey =
    getSubtaskLookupKeys({
      id: task.id,
      threadId: task.threadId,
      runId: task.runId,
      roundId: task.roundId,
    }).find((key) => {
      const previousTask = tasks[key];
      return previousTask && subtaskMatchesInput(previousTask, task);
    }) ?? storageKey;
  const previous = tasks[previousKey];
  const next = mergeSubtaskUpdate(previous, task);

  if (previousKey === storageKey && !didSubtaskChange(previous, next)) {
    return tasks;
  }

  const nextTasks = { ...tasks, [storageKey]: next };
  if (previousKey !== storageKey) {
    delete nextTasks[previousKey];
  }
  return nextTasks;
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
      task.status !== "in_progress" ||
      task.backgroundTask === true ||
      (terminal.roundId &&
        task.roundId &&
        normalizeSubtaskRoundId(task.roundId) !==
          normalizeSubtaskRoundId(terminal.roundId))
    ) {
      continue;
    }

    const recoveryExhausted = terminal.status === "recovery_failed";
    const next = mergeSubtaskUpdate(task, {
      id: task.id,
      threadId: terminal.threadId,
      runId: terminal.runId,
      ...(task.roundId ? { roundId: task.roundId } : {}),
      status: recoveryExhausted ? "unknown" : "failed",
      ...(recoveryExhausted
        ? {}
        : {
            error: runTerminalSubtaskError(
              terminal.status,
              terminal.terminalReason,
            ),
          }),
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
