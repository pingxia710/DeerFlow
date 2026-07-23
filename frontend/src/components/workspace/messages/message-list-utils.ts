import type { Message } from "@langchain/langgraph-sdk";

import {
  type getMessageGroups,
  getMessageRoundId,
  getMessageRunId,
} from "@/core/messages/utils";
import type { Subtask } from "@/core/tasks";
import { normalizeSubtaskRoundId } from "@/core/tasks/context";
import { HISTORY_CREATED_AT_KEY } from "@/core/threads/hooks";

export const MESSAGE_LIST_DEFAULT_PADDING_BOTTOM = 24;

export type HistoryPrependRunner = (
  loadMore: () => Promise<void> | void,
) => Promise<void>;

export function getMessageGroupKey(
  group: { type: string; id?: string | null },
  index: number,
) {
  return group.id
    ? `${group.type}-${group.id}`
    : `fallback-${index}-${group.type}`;
}

export function getSubtaskCardKey(
  taskId: string,
  runId?: string | null,
  roundId?: string | null,
) {
  if (!runId) {
    return `task-group-${taskId}`;
  }
  return `task-group-${JSON.stringify([
    runId,
    normalizeSubtaskRoundId(roundId),
    taskId,
  ])}`;
}

export function getRunScopedSubtaskKey(
  taskId: string,
  runId?: string | null,
  roundId?: string | null,
) {
  return JSON.stringify([
    runId ?? "",
    normalizeSubtaskRoundId(roundId),
    taskId,
  ]);
}

export function getMessageHistoryTime(message: Message) {
  const value = message.additional_kwargs?.[HISTORY_CREATED_AT_KEY];
  if (typeof value !== "string") {
    return undefined;
  }
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : undefined;
}

export function isMessageActivelyStreaming(
  message: Message,
  groupIsLoading: boolean,
  activeRunIds: ReadonlySet<string>,
) {
  if (!groupIsLoading) {
    return false;
  }
  if (getMessageHistoryTime(message) === undefined) {
    return true;
  }
  const runId = getMessageRunId(message);
  return runId !== undefined && activeRunIds.has(runId);
}

function isTerminalSubtask(task: Subtask) {
  return (
    task.status === "completed" ||
    task.status === "failed" ||
    task.status === "unknown"
  );
}

type SubtaskTaskLookup = {
  threadId: string;
  runId?: string | null;
  taskId: string;
  roundId?: string | null;
};

function matchesRequestedRound(
  task: Subtask,
  roundId: string | null | undefined,
) {
  return (
    !roundId ||
    normalizeSubtaskRoundId(task.roundId) === normalizeSubtaskRoundId(roundId)
  );
}

export function hasTerminalSubtaskForTask(
  subtasks: Subtask[],
  { threadId, runId, taskId, roundId }: SubtaskTaskLookup,
) {
  return subtasks.some(
    (task) =>
      task.threadId === threadId &&
      task.id === taskId &&
      task.runId !== runId &&
      matchesRequestedRound(task, roundId) &&
      isTerminalSubtask(task),
  );
}

export function findMatchingTerminalSubtaskForTask(
  subtasks: Subtask[],
  { threadId, runId, taskId, roundId }: SubtaskTaskLookup,
) {
  const candidates = subtasks.filter(
    (task) =>
      task.threadId === threadId &&
      task.id === taskId &&
      task.runId === runId &&
      isTerminalSubtask(task),
  );
  if (roundId) {
    return candidates.find((task) => matchesRequestedRound(task, roundId));
  }
  return candidates.length === 1 ? candidates[0] : undefined;
}

export function isActiveSubtaskStatus(status: Subtask["status"]) {
  return status === "queued" || status === "in_progress";
}

export function findMatchingActiveSubtaskForTask(
  subtasks: Subtask[],
  { threadId, runId, taskId, roundId }: SubtaskTaskLookup,
) {
  const candidates = subtasks.filter(
    (task) =>
      task.threadId === threadId &&
      task.id === taskId &&
      task.runId === runId &&
      isActiveSubtaskStatus(task.status),
  );
  if (roundId) {
    return candidates.find((task) => matchesRequestedRound(task, roundId));
  }
  return candidates.length === 1 ? candidates[0] : undefined;
}

export function hasMatchingTerminalSubtaskForTask(
  subtasks: Subtask[],
  lookup: SubtaskTaskLookup,
) {
  return Boolean(findMatchingTerminalSubtaskForTask(subtasks, lookup));
}

export function shouldKeepInferredSubtask({
  status,
  hasMatchingTerminal,
  hasTerminalInOtherRun,
  isVisibleRunning,
}: {
  status: Subtask["status"];
  hasMatchingTerminal: boolean;
  hasTerminalInOtherRun: boolean;
  isVisibleRunning: boolean;
}) {
  if (!isActiveSubtaskStatus(status)) {
    return true;
  }
  if (hasMatchingTerminal) {
    return true;
  }
  if (hasTerminalInOtherRun) {
    return false;
  }
  return isVisibleRunning;
}

export function isRuntimeOnlySubtaskForActiveTurn(
  task: Pick<Subtask, "runId" | "roundId" | "startedAt">,
  activeRunIds: ReadonlySet<string>,
  activeRoundIdsByRunId: ReadonlyMap<string, ReadonlySet<string>>,
  turnStartTime: number | null,
) {
  if (!task.runId) {
    return false;
  }
  if (activeRunIds.size === 0) {
    return (
      turnStartTime !== null &&
      task.startedAt !== undefined &&
      task.startedAt >= turnStartTime
    );
  }
  if (!activeRunIds.has(task.runId)) {
    return false;
  }
  const activeRoundIds = activeRoundIdsByRunId.get(task.runId);
  return (
    !activeRoundIds ||
    activeRoundIds.size === 0 ||
    !task.roundId ||
    activeRoundIds.has(normalizeSubtaskRoundId(task.roundId))
  );
}

export function isInferredRunningSubtaskVisible({
  runId,
  startedAt,
  groupIsLoading,
  activeRunIds,
  turnStartTime,
  hasMatchingActiveTask = false,
}: {
  runId?: string | null;
  startedAt?: number;
  groupIsLoading: boolean;
  activeRunIds: ReadonlySet<string>;
  turnStartTime: number | null;
  hasMatchingActiveTask?: boolean;
}) {
  if (!runId) {
    return false;
  }
  if (hasMatchingActiveTask) {
    return true;
  }
  if (activeRunIds.has(runId)) {
    return true;
  }
  if (!groupIsLoading) {
    return false;
  }
  if (startedAt === undefined) {
    return true;
  }
  return turnStartTime !== null && startedAt >= turnStartTime;
}

export function getActiveTurnSubtaskScope(
  groupedMessages: ReturnType<typeof getMessageGroups>,
) {
  const runIds = new Set<string>();
  const roundIdsByRunId = new Map<string, Set<string>>();

  for (
    let groupIndex = groupedMessages.length - 1;
    groupIndex >= 0;
    groupIndex--
  ) {
    const group = groupedMessages[groupIndex];
    if (!group || group.type === "human") {
      break;
    }
    for (
      let messageIndex = group.messages.length - 1;
      messageIndex >= 0;
      messageIndex--
    ) {
      const message = group.messages[messageIndex];
      if (!message) {
        continue;
      }
      const runId = getMessageRunId(message);
      if (!runId) {
        continue;
      }
      runIds.add(runId);
      const roundId = getMessageRoundId(message);
      if (roundId) {
        let roundIds = roundIdsByRunId.get(runId);
        if (!roundIds) {
          roundIds = new Set<string>();
          roundIdsByRunId.set(runId, roundIds);
        }
        roundIds.add(normalizeSubtaskRoundId(roundId));
      }
    }
  }

  return { runIds, roundIdsByRunId };
}

export function getMessagesHistoryStartTime(messages: Message[]) {
  let startTime: number | undefined;
  for (const message of messages) {
    const time = getMessageHistoryTime(message);
    if (time !== undefined) {
      startTime = startTime === undefined ? time : Math.min(startTime, time);
    }
  }
  return startTime;
}

export function formatConversationTime(timestamp: number, locale: string) {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}
