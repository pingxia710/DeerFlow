import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import {
  AlertCircleIcon,
  ArrowDownIcon,
  BrainCircuitIcon,
  ChevronUpIcon,
  Loader2Icon,
  RefreshCcwIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
  type MouseEvent,
} from "react";

import {
  Conversation,
  ConversationContent,
} from "@/components/ai-elements/conversation";
import {
  Reasoning,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { formatContextCount } from "@/core/messages/usage";
import {
  buildTokenDebugSteps,
  type TokenUsageInlineMode,
} from "@/core/messages/usage-model";
import {
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  extractTextFromMessage,
  getAssistantTurnCopyData,
  getAssistantTurnUsageMessages,
  getConversationTurnTiming,
  getCommandRoomStepMessages,
  getConversationTurns,
  getMessageGroups,
  getStreamingMessageLookup,
  hasContent,
  hasPresentFiles,
  hasReasoning,
  isAssistantMessageGroupStreaming,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import type { Subtask } from "@/core/tasks";
import {
  normalizeSubtaskRoundId,
  type SubtaskUpdate,
  useSubtasksForThread,
  useUpdateSubtask,
} from "@/core/tasks/context";
import {
  derivePendingSubtaskStatus,
  parseSubtaskResult,
} from "@/core/tasks/subtask-result";
import type { AgentThreadState } from "@/core/threads";
import { buildCommandRoomTrajectory } from "@/core/threads/command-room-read-model";
import {
  HISTORY_CREATED_AT_KEY,
  isActiveRunStatus,
  useThreadRuntimeSnapshot,
  type ThreadRunTerminalNotice,
  type useThreadStream,
} from "@/core/threads/hooks";
import { mergeTaskLaneSubtasks } from "@/core/threads/task-events";
import type { ThreadContextUsageSnapshot } from "@/core/threads/types";
import { cn } from "@/lib/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";
import { CopyButton } from "../copy-button";
import { Tooltip } from "../tooltip";

import { CommandRoomTrajectory } from "./command-room-trajectory";
import { MarkdownContent } from "./markdown-content";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";
import {
  MessageTokenUsageDebugList,
  MessageTokenUsageList,
} from "./message-token-usage";
import { MessageListSkeleton } from "./skeleton";
import { SubtaskCard } from "./subtask-card";
import { useConversationTurnScroll } from "./use-conversation-turn-scroll";

export const MESSAGE_LIST_DEFAULT_PADDING_BOTTOM = 24;

const LOAD_MORE_HISTORY_THROTTLE_MS = 1200;

type HistoryPrependRunner = (
  loadMore: () => Promise<void> | void,
) => Promise<void>;

type LocalTurnTiming = {
  startedAt: number;
  completedAt?: number;
};

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

type ThreadRecoveryStatus = ReturnType<
  typeof useThreadStream
>["recoveryStatus"];
type MessageListRun = {
  run_id?: string | null;
  status?: unknown;
};

function getMessageHistoryTime(message: Message) {
  const value = message.additional_kwargs?.[HISTORY_CREATED_AT_KEY];
  if (typeof value !== "string") {
    return undefined;
  }
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : undefined;
}

function getMessageRunId(message: Message) {
  const value =
    message.additional_kwargs?.deerflow_run_id ??
    message.additional_kwargs?.run_id;
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function isMessageActivelyStreaming(
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

function getMessageRoundId(message: Message) {
  const value =
    message.additional_kwargs?.deerflow_round_id ??
    message.additional_kwargs?.round_id ??
    message.additional_kwargs?.roundId;
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function isCommandRoomBackgroundSubtaskGroup(messages: Message[]) {
  return messages.some(
    (message) =>
      message.type === "tool" &&
      message.name === "task" &&
      message.additional_kwargs?.background_task === true,
  );
}

function isTerminalSubtask(task: Subtask) {
  return task.status === "completed" || task.status === "failed";
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

export function findMatchingActiveSubtaskForTask(
  subtasks: Subtask[],
  { threadId, runId, taskId, roundId }: SubtaskTaskLookup,
) {
  const candidates = subtasks.filter(
    (task) =>
      task.threadId === threadId &&
      task.id === taskId &&
      task.runId === runId &&
      task.status === "in_progress",
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
  if (status !== "in_progress") {
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

function getMessagesHistoryStartTime(messages: Message[]) {
  let startTime: number | undefined;
  for (const message of messages) {
    const time = getMessageHistoryTime(message);
    if (time !== undefined) {
      startTime = startTime === undefined ? time : Math.min(startTime, time);
    }
  }
  return startTime;
}

function formatConversationTime(timestamp: number, locale: string) {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

function LoadMoreHistoryIndicator({
  historyPrependRef,
  isLoading,
  hasMore,
  loadMore,
}: {
  historyPrependRef: MutableRefObject<HistoryPrependRunner | null>;
  isLoading?: boolean;
  hasMore?: boolean;
  loadMore?: () => Promise<void> | void;
}) {
  const { t } = useI18n();
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoadRef = useRef(0);

  const runLoadMore = useCallback(() => {
    if (!loadMore) {
      return;
    }
    const runWithHistoryAnchor = historyPrependRef.current;
    if (runWithHistoryAnchor) {
      void runWithHistoryAnchor(loadMore);
      return;
    }
    void loadMore();
  }, [historyPrependRef, loadMore]);

  const throttledLoadMore = useCallback(() => {
    if (!hasMore || isLoading) {
      return;
    }

    const now = Date.now();
    const remaining =
      LOAD_MORE_HISTORY_THROTTLE_MS - (now - lastLoadRef.current);

    if (remaining <= 0) {
      lastLoadRef.current = now;
      runLoadMore();
      return;
    }

    if (timeoutRef.current) {
      return;
    }

    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      if (!hasMore || isLoading) {
        return;
      }
      lastLoadRef.current = Date.now();
      runLoadMore();
    }, remaining);
  }, [hasMore, isLoading, runLoadMore]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  if (!hasMore && !isLoading) {
    return null;
  }

  return (
    <div className="flex w-full justify-center">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="text-muted-foreground hover:text-foreground rounded-full px-3"
        disabled={(isLoading ?? false) || !hasMore}
        onClick={throttledLoadMore}
      >
        {isLoading ? (
          <>
            <Loader2Icon className="mr-2 size-4 animate-spin" />
            {t.common.loading}
          </>
        ) : (
          <>
            <ChevronUpIcon className="mr-2 size-4" />
            {t.common.loadMore}
          </>
        )}
      </Button>
    </div>
  );
}

function ConversationTurnScrollController({
  historyPrependRef,
  threadId,
  activeTurnId,
  isStreaming,
}: {
  historyPrependRef: MutableRefObject<HistoryPrependRunner | null>;
  threadId: string;
  activeTurnId?: string;
  isStreaming: boolean;
}) {
  const { t } = useI18n();
  const {
    runWithHistoryAnchor,
    returnToCurrentReply,
    shouldShowReturnToCurrentReply,
  } = useConversationTurnScroll({ threadId, activeTurnId, isStreaming });

  useLayoutEffect(() => {
    historyPrependRef.current = runWithHistoryAnchor;
    return () => {
      if (historyPrependRef.current === runWithHistoryAnchor) {
        historyPrependRef.current = null;
      }
    };
  }, [historyPrependRef, runWithHistoryAnchor]);

  if (!shouldShowReturnToCurrentReply) {
    return null;
  }

  return (
    <Button
      aria-label={t.conversation.returnToCurrentReply}
      className="absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full shadow-sm"
      onClick={returnToCurrentReply}
      size="sm"
      type="button"
      variant="outline"
    >
      <ArrowDownIcon className="mr-1 size-3.5" />
      {t.conversation.returnToCurrentReply}
    </Button>
  );
}

function RunRecoveryNotice({
  status,
  onRetry,
}: {
  status: NonNullable<ThreadRecoveryStatus>;
  onRetry?: () => void;
}) {
  const { t } = useI18n();
  const isRepairing = status.state === "repairing";
  const isFailed = status.state === "failed";
  const title = isRepairing
    ? t.chats.runRecoveryRepairingTitle
    : isFailed
      ? t.chats.runRecoveryFailedTitle
      : t.chats.runRecoveryTerminalTitle;
  const description = isRepairing
    ? t.chats.runRecoveryRepairingDescription
    : isFailed
      ? status.reason
      : t.chats.runRecoveryTerminalDescription(status.reason);
  return (
    <div
      role="status"
      data-testid="run-recovery-notice"
      className={cn(
        "flex w-full items-start gap-2 rounded-md border px-3 py-2 text-sm",
        isRepairing
          ? "border-amber-300/70 bg-amber-50 text-amber-900 dark:border-amber-700/70 dark:bg-amber-950/30 dark:text-amber-100"
          : isFailed
            ? "border-destructive/40 bg-destructive/10 text-destructive"
            : "border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-200",
      )}
    >
      {isRepairing ? (
        <Loader2Icon className="mt-0.5 size-4 shrink-0 animate-spin" />
      ) : (
        <AlertCircleIcon className="mt-0.5 size-4 shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title}</div>
        <div className="mt-0.5 break-words">{description}</div>
      </div>
      {isFailed && onRetry ? (
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          <RefreshCcwIcon className="mr-1 size-3.5" />
          {t.chats.retryRecovery}
        </Button>
      ) : null}
    </div>
  );
}

function RunTerminalNotice({ notice }: { notice: ThreadRunTerminalNotice }) {
  const { t } = useI18n();
  return (
    <div
      role="status"
      data-testid="run-terminal-notice"
      className="border-border/70 bg-muted/40 text-muted-foreground flex w-full items-start gap-2 rounded-md border px-3 py-2 text-sm"
    >
      <AlertCircleIcon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        <div className="text-foreground font-medium">
          {t.chats.runTerminalNoticeTitle}
        </div>
        <div className="mt-0.5 break-words">
          {t.chats.runTerminalNoticeDescription(
            notice.status,
            notice.terminalReason ?? notice.error,
          )}
        </div>
      </div>
    </div>
  );
}

export function MessageList({
  className,
  threadId,
  thread,
  paddingBottom = MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
  tokenUsageInlineMode = "off",
  hideProtocolUi = false,
  hideThinkingUi = true,
  isCommandRoom = false,
  contextSnapshot,
  hasMoreHistory,
  loadMoreHistory,
  isHistoryLoading,
  historyRuns = [],
  terminalNotice,
  recoveryStatus,
  onRetryRecovery,
  onRegenerateMessage,
  canRegenerate = false,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  paddingBottom?: number;
  tokenUsageInlineMode?: TokenUsageInlineMode;
  hideProtocolUi?: boolean;
  hideThinkingUi?: boolean;
  isCommandRoom?: boolean;
  contextSnapshot?: ThreadContextUsageSnapshot | null;
  hasMoreHistory?: boolean;
  loadMoreHistory?: () => Promise<void> | void;
  isHistoryLoading?: boolean;
  historyRuns?: ReadonlyArray<MessageListRun>;
  terminalNotice?: ThreadRunTerminalNotice | null;
  recoveryStatus?: ThreadRecoveryStatus;
  onRetryRecovery?: () => void;
  onRegenerateMessage?: (
    messageId: string,
    supersededMessageIds: string[],
  ) => void | Promise<void>;
  canRegenerate?: boolean;
}) {
  const { locale, t } = useI18n();
  const [turnStartTime, setTurnStartTime] = useState<number | null>(null);
  const prevIsLoading = useRef(thread.isLoading);
  const messages = thread.messages;
  const commandRoomStepMessages = useMemo(
    () => getCommandRoomStepMessages(messages),
    [messages],
  );
  const groupedMessages = useMemo(() => getMessageGroups(messages), [messages]);
  const conversationTurns = useMemo(
    () => getConversationTurns(groupedMessages),
    [groupedMessages],
  );
  const groupIndexes = useMemo(
    () => new Map(groupedMessages.map((group, index) => [group, index])),
    [groupedMessages],
  );
  const turnNumbers = useMemo(() => {
    const numbers = new Map<string, number>();
    let turnCount = 0;
    for (const turn of conversationTurns) {
      if (turn.hasHumanMessage) {
        numbers.set(turn.id, ++turnCount);
      }
    }
    return numbers;
  }, [conversationTurns]);
  const activeConversationTurn = useMemo(
    () => [...conversationTurns].reverse().find((turn) => turn.hasHumanMessage),
    [conversationTurns],
  );
  const [localTurnTimings, setLocalTurnTimings] = useState<
    Record<string, LocalTurnTiming>
  >({});
  const [liveElapsedSeconds, setLiveElapsedSeconds] = useState<number | null>(
    null,
  );
  const activeStreamingTurnIdRef = useRef<string | null>(null);
  const historyPrependRef = useRef<HistoryPrependRunner | null>(null);

  useEffect(() => {
    activeStreamingTurnIdRef.current = null;
    prevIsLoading.current = false;
    setLocalTurnTimings({});
    setLiveElapsedSeconds(null);
    setTurnStartTime(null);
  }, [threadId]);

  useEffect(() => {
    const wasLoading = prevIsLoading.current;
    if (thread.isLoading && !wasLoading) {
      const startedAt = activeConversationTurn
        ? (getConversationTurnTiming(activeConversationTurn).startedAt ??
          Date.now())
        : Date.now();
      setTurnStartTime(startedAt);
      if (activeConversationTurn) {
        activeStreamingTurnIdRef.current = activeConversationTurn.id;
        setLocalTurnTimings((current) => ({
          ...current,
          [activeConversationTurn.id]: { startedAt },
        }));
      }
    } else if (!thread.isLoading && wasLoading) {
      const turnId = activeStreamingTurnIdRef.current;
      if (turnId) {
        setLocalTurnTimings((current) => {
          const timing = current[turnId];
          return timing
            ? {
                ...current,
                [turnId]: { ...timing, completedAt: Date.now() },
              }
            : current;
        });
      }
    }
    prevIsLoading.current = thread.isLoading;
  }, [activeConversationTurn, thread.isLoading]);
  const activeTurnId = activeConversationTurn?.id;
  const activeTurnStartedAt = activeConversationTurn
    ? (getConversationTurnTiming(activeConversationTurn).startedAt ??
      localTurnTimings[activeConversationTurn.id]?.startedAt ??
      turnStartTime)
    : null;

  useEffect(() => {
    if (!thread.isLoading || activeTurnStartedAt === null) {
      setLiveElapsedSeconds(null);
      return;
    }

    const updateElapsed = () => {
      setLiveElapsedSeconds(
        Math.max(0, Math.floor((Date.now() - activeTurnStartedAt) / 1000)),
      );
    };

    updateElapsed();
    const interval = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(interval);
  }, [activeTurnId, activeTurnStartedAt, thread.isLoading]);
  const [regeneratingMessageId, setRegeneratingMessageId] = useState<
    string | null
  >(null);
  const hasActiveAssistantText = useMemo(() => {
    let lastHumanIndex = -1;
    for (let i = groupedMessages.length - 1; i >= 0; i--) {
      if (groupedMessages[i]?.type === "human") {
        lastHumanIndex = i;
        break;
      }
    }
    if (lastHumanIndex === -1) return false;
    return groupedMessages
      .slice(lastHumanIndex)
      .some((g) => g.type === "assistant");
  }, [groupedMessages]);
  const hasTerminalRunMessages = Boolean(
    terminalNotice &&
    messages.some(
      (message) => getMessageRunId(message) === terminalNotice.runId,
    ),
  );
  const shouldShowTerminalNotice = Boolean(
    terminalNotice &&
    !thread.isLoading &&
    (!hasActiveAssistantText || hasTerminalRunMessages),
  );
  const rehypePlugins = useRehypeSplitWordsIntoSpans(thread.isLoading);
  const updateSubtask = useUpdateSubtask();
  const contextSubtasks = useSubtasksForThread(threadId);
  const runtimeSnapshot = useThreadRuntimeSnapshot(threadId, {
    enabled: isCommandRoom,
  });
  const trajectoryTasks = useMemo(
    () =>
      isCommandRoom
        ? mergeTaskLaneSubtasks(
            runtimeSnapshot.data?.task_lanes ?? [],
            contextSubtasks,
          )
        : contextSubtasks,
    [contextSubtasks, isCommandRoom, runtimeSnapshot.data?.task_lanes],
  );
  const commandRoomTrajectory = useMemo(
    () => buildCommandRoomTrajectory(trajectoryTasks),
    [trajectoryTasks],
  );
  const unstagedCommandRoomTasks = useMemo(
    () =>
      isCommandRoom
        ? trajectoryTasks.filter((task) => !task.commandRoomContainer)
        : [],
    [isCommandRoom, trajectoryTasks],
  );
  const lastGroupIndex = groupedMessages.length - 1;
  const activeHistoryRunIds = useMemo(() => {
    return new Set(
      historyRuns
        .filter((run) => isActiveRunStatus(run.status))
        .map((run) => run.run_id)
        .filter((runId): runId is string => Boolean(runId)),
    );
  }, [historyRuns]);
  const activeTurnSubtaskScope = useMemo(
    () => getActiveTurnSubtaskScope(groupedMessages),
    [groupedMessages],
  );
  const activeTurnStartTime = useMemo(() => {
    return (
      (activeConversationTurn
        ? getConversationTurnTiming(activeConversationTurn).startedAt
        : undefined) ??
      (activeConversationTurn
        ? localTurnTimings[activeConversationTurn.id]?.startedAt
        : undefined) ??
      getMessagesHistoryStartTime(
        groupedMessages[lastGroupIndex]?.messages ?? [],
      ) ??
      turnStartTime
    );
  }, [
    activeConversationTurn,
    groupedMessages,
    lastGroupIndex,
    localTurnTimings,
    turnStartTime,
  ]);
  const subtaskUpdates = useMemo<SubtaskUpdate[]>(() => {
    const updates: SubtaskUpdate[] = [];
    for (const [groupIndex, group] of groupedMessages.entries()) {
      if (group.type !== "assistant:subagent") {
        continue;
      }
      const groupIsLoading = thread.isLoading && groupIndex === lastGroupIndex;
      for (const message of group.messages) {
        if (message.type === "ai") {
          for (const toolCall of message.tool_calls ?? []) {
            if (toolCall.name !== "task" || !toolCall.id) {
              continue;
            }
            const runId = getMessageRunId(message);
            if (!runId) {
              continue;
            }
            const roundId = getMessageRoundId(message);
            const matchingTerminal = findMatchingTerminalSubtaskForTask(
              contextSubtasks,
              {
                threadId,
                runId,
                taskId: toolCall.id,
                roundId,
              },
            );
            const matchingActive = findMatchingActiveSubtaskForTask(
              contextSubtasks,
              {
                threadId,
                runId,
                taskId: toolCall.id,
                roundId,
              },
            );
            const effectiveRoundId =
              roundId ?? matchingTerminal?.roundId ?? matchingActive?.roundId;
            const status = derivePendingSubtaskStatus(
              toolCall.id,
              group.messages,
              groupIsLoading &&
                messages.some((message) => getMessageRunId(message) === runId),
            );
            const startedAt = getMessageHistoryTime(message);
            if (
              !shouldKeepInferredSubtask({
                status,
                hasMatchingTerminal: Boolean(matchingTerminal),
                hasTerminalInOtherRun: hasTerminalSubtaskForTask(
                  contextSubtasks,
                  {
                    threadId,
                    runId,
                    taskId: toolCall.id,
                    roundId: effectiveRoundId,
                  },
                ),
                isVisibleRunning: isInferredRunningSubtaskVisible({
                  runId,
                  startedAt,
                  groupIsLoading,
                  activeRunIds: activeHistoryRunIds,
                  turnStartTime: activeTurnStartTime,
                  hasMatchingActiveTask: Boolean(matchingActive),
                }),
              })
            ) {
              continue;
            }
            updates.push({
              id: toolCall.id,
              threadId,
              runId,
              roundId: effectiveRoundId,
              subagent_type: toolCall.args.subagent_type,
              description: toolCall.args.description,
              prompt: toolCall.args.prompt,
              status,
              ...(startedAt !== undefined ? { startedAt } : {}),
              ...(status === "failed" ? { error: t.subtasks.failed } : {}),
            });
          }
        } else if (message.type === "tool" && message.tool_call_id) {
          const runId = getMessageRunId(message);
          if (!runId) {
            continue;
          }
          const roundId = getMessageRoundId(message);
          updates.push({
            id: message.tool_call_id,
            threadId,
            runId,
            roundId,
            ...parseSubtaskResult(
              extractTextFromMessage(message),
              message.additional_kwargs,
            ),
          });
        }
      }
    }
    return updates;
  }, [
    activeHistoryRunIds,
    activeTurnStartTime,
    groupedMessages,
    contextSubtasks,
    lastGroupIndex,
    messages,
    t.subtasks.failed,
    thread.isLoading,
    threadId,
  ]);
  useEffect(() => {
    for (const update of subtaskUpdates) {
      updateSubtask(update);
    }
  }, [subtaskUpdates, updateSubtask]);
  const anchoredSubtaskKeys = useMemo(() => {
    return new Set(
      subtaskUpdates
        .filter((task) => task.runId)
        .map((task) =>
          JSON.stringify([
            task.runId,
            normalizeSubtaskRoundId(task.roundId),
            task.id,
          ]),
        ),
    );
  }, [subtaskUpdates]);
  const runtimeOnlySubtasks = useMemo(() => {
    if (!thread.isLoading) {
      return [];
    }

    return contextSubtasks.filter(
      (task) =>
        task.status === "in_progress" &&
        !task.commandRoomContainer &&
        Boolean(task.runId) &&
        isRuntimeOnlySubtaskForActiveTurn(
          task,
          activeTurnSubtaskScope.runIds,
          activeTurnSubtaskScope.roundIdsByRunId,
          activeTurnStartTime,
        ) &&
        !hasTerminalSubtaskForTask(contextSubtasks, {
          threadId,
          runId: task.runId,
          taskId: task.id,
          roundId: task.roundId,
        }) &&
        !anchoredSubtaskKeys.has(
          JSON.stringify([
            task.runId,
            normalizeSubtaskRoundId(task.roundId),
            task.id,
          ]),
        ),
    );
  }, [
    activeTurnStartTime,
    activeTurnSubtaskScope,
    anchoredSubtaskKeys,
    contextSubtasks,
    thread.isLoading,
    threadId,
  ]);
  const turnUsageMessagesByGroupIndex =
    getAssistantTurnUsageMessages(groupedMessages);
  const tokenDebugSteps = useMemo(
    () => buildTokenDebugSteps(messages, t),
    [messages, t],
  );
  const streamingMessages = useMemo(
    () =>
      getStreamingMessageLookup(
        messages,
        thread.isLoading,
        thread.getMessagesMetadata,
      ),
    [messages, thread.getMessagesMetadata, thread.isLoading],
  );

  const latestAssistantGroupId = useMemo(() => {
    if (thread.isLoading) {
      return null;
    }
    for (let i = groupedMessages.length - 1; i >= 0; i -= 1) {
      const group = groupedMessages[i];
      if (group?.type === "assistant") {
        return group.id;
      }
    }
    return null;
  }, [groupedMessages, thread.isLoading]);

  const handleReviewTurnClick = useCallback(
    (event: MouseEvent<HTMLButtonElement>) => {
      const turnElement = event.currentTarget.closest(
        "[data-conversation-turn]",
      );
      if (turnElement instanceof HTMLElement) {
        turnElement.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    },
    [],
  );

  const renderAssistantActions = useCallback(
    (
      messages: Message[],
      isStreaming: boolean,
      enableRegenerateForTurn: boolean,
      contextSnapshotForTurn?: ThreadContextUsageSnapshot | null,
    ) => {
      const clipboardData = getAssistantTurnCopyData(messages, { isStreaming });
      const regenerateTarget = [...messages]
        .reverse()
        .find((message) => message.type === "ai" && message.id);
      const supersededMessageIds = messages
        .filter((message) => message.type === "ai" && message.id)
        .map((message) => message.id)
        .filter((id): id is string => typeof id === "string");
      const hasRegenerateAction =
        enableRegenerateForTurn &&
        !!regenerateTarget?.id &&
        !!onRegenerateMessage;
      const hasHoverActions = !!clipboardData || hasRegenerateAction;

      return (
        <div className="mt-2 flex items-center justify-end gap-1">
          {hasHoverActions && (
            <div className="flex gap-1 opacity-0 transition-opacity delay-200 duration-300 group-hover/assistant-turn:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100">
              {clipboardData && <CopyButton clipboardData={clipboardData} />}
              {hasRegenerateAction && (
                <Tooltip content={t.common.regenerate}>
                  <Button
                    aria-label={t.common.regenerate}
                    size="icon-sm"
                    type="button"
                    variant="ghost"
                    disabled={
                      !canRegenerate ||
                      regeneratingMessageId === regenerateTarget.id
                    }
                    onClick={() => {
                      const targetId = regenerateTarget.id;
                      if (!targetId) {
                        return;
                      }
                      setRegeneratingMessageId(targetId);
                      void Promise.resolve(
                        onRegenerateMessage?.(targetId, supersededMessageIds),
                      ).finally(() => {
                        setRegeneratingMessageId(null);
                      });
                    }}
                  >
                    <RefreshCcwIcon
                      className={cn(
                        "size-3",
                        regeneratingMessageId === regenerateTarget.id &&
                          "animate-spin",
                      )}
                    />
                  </Button>
                </Tooltip>
              )}
            </div>
          )}
          {contextSnapshotForTurn && (
            <div
              className="text-muted-foreground bg-background/70 flex h-8 items-center gap-1.5 rounded-full border px-2 text-xs"
              title={`${t.contextUsage.label} ${formatContextCount(
                contextSnapshotForTurn.char_count,
              )} ${t.contextUsage.charUnit}`}
            >
              <BrainCircuitIcon className="size-3.5" />
              <span className="hidden sm:inline">{t.contextUsage.label}</span>
              <span className="font-mono">
                {formatContextCount(contextSnapshotForTurn.char_count)}
              </span>
              <span>{t.contextUsage.charUnit}</span>
            </div>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground rounded-full px-3"
            onClick={handleReviewTurnClick}
          >
            <ChevronUpIcon className="mr-1 size-3.5" />
            {t.chats.reviewTurn}
          </Button>
        </div>
      );
    },
    [
      canRegenerate,
      handleReviewTurnClick,
      onRegenerateMessage,
      regeneratingMessageId,
      t.contextUsage.charUnit,
      t.contextUsage.label,
      t.common.regenerate,
      t.chats.reviewTurn,
    ],
  );

  const renderTokenUsage = useCallback(
    ({
      messages,
      turnUsageMessages,
      inlineDebug = true,
      debugMessageIds,
    }: {
      messages: Message[];
      turnUsageMessages?: Message[] | null;
      inlineDebug?: boolean;
      debugMessageIds?: string[];
    }) => {
      if (hideProtocolUi) {
        return null;
      }

      if (tokenUsageInlineMode === "per_turn") {
        return (
          <MessageTokenUsageList
            enabled={true}
            isLoading={thread.isLoading}
            messages={turnUsageMessages ?? []}
          />
        );
      }

      if (tokenUsageInlineMode === "step_debug" && inlineDebug) {
        const messageIds = new Set(
          debugMessageIds ??
            messages
              .filter((message) => message.type === "ai")
              .map((message) => message.id)
              .filter((id): id is string => typeof id === "string"),
        );
        return (
          <MessageTokenUsageDebugList
            enabled={true}
            isLoading={thread.isLoading}
            steps={tokenDebugSteps.filter(
              (step) =>
                messageIds.has(step.messageId) &&
                !(
                  hideThinkingUi &&
                  step.label === t.common.thinking &&
                  step.secondaryLabels.length === 0
                ),
            )}
          />
        );
      }

      return null;
    },
    [
      hideProtocolUi,
      hideThinkingUi,
      t.common.thinking,
      thread.isLoading,
      tokenDebugSteps,
      tokenUsageInlineMode,
    ],
  );

  if (thread.isThreadLoading && messages.length === 0) {
    return <MessageListSkeleton />;
  }

  return (
    <Conversation
      className={cn("flex size-full flex-col", className)}
      resize={thread.isLoading ? "smooth" : undefined}
    >
      <ConversationContent
        data-conversation-content
        className="mx-auto w-full max-w-(--container-width-md) gap-8 pt-8"
      >
        <LoadMoreHistoryIndicator
          historyPrependRef={historyPrependRef}
          isLoading={isHistoryLoading}
          hasMore={hasMoreHistory}
          loadMore={loadMoreHistory}
        />
        {conversationTurns.map((turn) => {
          const turnTiming = getConversationTurnTiming(turn);
          const isActiveTurn = turn.id === activeConversationTurn?.id;
          const localTurnTiming = localTurnTimings[turn.id];
          const displayTurnStartTime =
            turnTiming.startedAt ?? localTurnTiming?.startedAt;
          const completedAt =
            turnTiming.completedAt ?? localTurnTiming?.completedAt;
          const isStreamingTurn = isActiveTurn && thread.isLoading;
          const localDurationSeconds = isStreamingTurn
            ? (liveElapsedSeconds ?? undefined)
            : displayTurnStartTime !== undefined && completedAt !== undefined
              ? Math.max(
                  0,
                  Math.floor((completedAt - displayTurnStartTime) / 1000),
                )
              : undefined;
          const durationSeconds =
            turnTiming.durationSeconds ?? localDurationSeconds;
          const turnNumber = turnNumbers.get(turn.id);
          const hasTerminalAssistantOutput = turn.groups.some(
            (group) =>
              group.type === "assistant" ||
              group.type === "assistant:clarification" ||
              group.type === "assistant:present-files",
          );
          const isFailedTurn =
            isActiveTurn && !thread.isLoading && Boolean(thread.error);
          const footerParts: string[] = [];
          if (isStreamingTurn) {
            footerParts.push(t.conversation.inProgress);
          } else {
            if (isFailedTurn) {
              footerParts.push(t.conversation.failed);
            }
            if (
              completedAt !== undefined &&
              (hasTerminalAssistantOutput || isFailedTurn)
            ) {
              footerParts.push(
                t.conversation.completedAt(
                  formatConversationTime(completedAt, locale),
                ),
              );
            }
          }
          if (
            durationSeconds !== undefined &&
            (isStreamingTurn || isFailedTurn || hasTerminalAssistantOutput)
          ) {
            const minutes = Math.floor(durationSeconds / 60);
            const seconds = Math.floor(durationSeconds % 60);
            footerParts.push(t.conversation.elapsed(minutes, seconds));
          }

          return (
            <section
              key={turn.id}
              data-conversation-turn
              data-conversation-turn-id={turn.id}
              className="border-border/60 border-t pt-6 first:border-t-0"
            >
              {turn.hasHumanMessage && turnNumber !== undefined && (
                <div
                  data-conversation-scroll-anchor
                  className="text-muted-foreground mb-3 text-xs font-medium"
                >
                  {t.conversation.turn(turnNumber)}
                  {displayTurnStartTime !== undefined &&
                    ` · ${formatConversationTime(displayTurnStartTime, locale)}`}
                </div>
              )}
              {turn.groups.map((group, turnGroupIndex) => {
                const groupIndex = groupIndexes.get(group);
                if (groupIndex === undefined) {
                  return null;
                }
                const turnUsageMessages =
                  turnUsageMessagesByGroupIndex[groupIndex];
                const groupIsLoading =
                  thread.isLoading && groupIndex === lastGroupIndex;
                const groupKey = group.id
                  ? getMessageGroupKey(group, groupIndex)
                  : `${group.type}-${turn.id}-${turnGroupIndex}`;

                if (group.type === "human" || group.type === "assistant") {
                  return (
                    <div
                      key={groupKey}
                      data-conversation-scroll-anchor
                      data-assistant-turn={
                        group.type === "assistant" ? true : undefined
                      }
                      className={cn(
                        "w-full",
                        group.type === "assistant" && "group/assistant-turn",
                      )}
                    >
                      {group.messages.map((msg) => {
                        const messageIsLoading = isMessageActivelyStreaming(
                          msg,
                          groupIsLoading,
                          activeHistoryRunIds,
                        );
                        return (
                          <MessageListItem
                            key={`${group.id}/${msg.id}`}
                            message={msg}
                            isLoading={messageIsLoading}
                            threadId={threadId}
                            showCopyButton={group.type !== "assistant"}
                            hideThinkingUi={hideThinkingUi}
                            turnStartTime={
                              messageIsLoading ? activeTurnStartTime : null
                            }
                          />
                        );
                      })}
                      {renderTokenUsage({
                        messages: group.messages,
                        turnUsageMessages,
                      })}
                      {group.type === "assistant" &&
                        renderAssistantActions(
                          group.messages,
                          isAssistantMessageGroupStreaming(
                            group.messages,
                            streamingMessages,
                          ),
                          group.id === latestAssistantGroupId,
                          group.id === latestAssistantGroupId
                            ? contextSnapshot
                            : null,
                        )}
                    </div>
                  );
                } else if (group.type === "assistant:clarification") {
                  const message = group.messages[0];
                  if (message && hasContent(message)) {
                    return (
                      <div
                        key={groupKey}
                        data-conversation-scroll-anchor
                        className="w-full"
                      >
                        <MarkdownContent
                          content={extractContentFromMessage(message)}
                          isLoading={thread.isLoading}
                          rehypePlugins={rehypePlugins}
                        />
                        {renderTokenUsage({
                          messages: group.messages,
                          turnUsageMessages,
                        })}
                      </div>
                    );
                  }
                  return null;
                } else if (group.type === "assistant:present-files") {
                  const files: string[] = [];
                  for (const message of group.messages) {
                    if (hasPresentFiles(message)) {
                      const presentFiles =
                        extractPresentFilesFromMessage(message);
                      files.push(...presentFiles);
                    }
                  }
                  return (
                    <div
                      className="w-full"
                      data-conversation-scroll-anchor
                      key={groupKey}
                    >
                      {group.messages[0] && hasContent(group.messages[0]) && (
                        <MarkdownContent
                          content={extractContentFromMessage(group.messages[0])}
                          isLoading={thread.isLoading}
                          rehypePlugins={rehypePlugins}
                          className="mb-4"
                        />
                      )}
                      <ArtifactFileList files={files} threadId={threadId} />
                      {renderTokenUsage({
                        messages: group.messages,
                        turnUsageMessages,
                      })}
                    </div>
                  );
                } else if (group.type === "assistant:subagent") {
                  if (isCommandRoomBackgroundSubtaskGroup(group.messages)) {
                    return null;
                  }
                  const tasks = new Set<Subtask>();
                  for (const message of group.messages) {
                    if (message.type === "ai") {
                      for (const toolCall of message.tool_calls ?? []) {
                        if (toolCall.name === "task") {
                          const taskId = toolCall.id;
                          if (!taskId) {
                            continue;
                          }
                          const runId = getMessageRunId(message);
                          if (!runId) {
                            continue;
                          }
                          const roundId = getMessageRoundId(message);
                          const matchingTerminal =
                            findMatchingTerminalSubtaskForTask(
                              contextSubtasks,
                              {
                                threadId,
                                runId,
                                taskId,
                                roundId,
                              },
                            );
                          const matchingActive =
                            findMatchingActiveSubtaskForTask(contextSubtasks, {
                              threadId,
                              runId,
                              taskId,
                              roundId,
                            });
                          const effectiveRoundId =
                            roundId ??
                            matchingTerminal?.roundId ??
                            matchingActive?.roundId;
                          const status = derivePendingSubtaskStatus(
                            taskId,
                            group.messages,
                            groupIsLoading,
                          );
                          const startedAt = getMessageHistoryTime(message);
                          if (
                            !shouldKeepInferredSubtask({
                              status,
                              hasMatchingTerminal: Boolean(matchingTerminal),
                              hasTerminalInOtherRun: hasTerminalSubtaskForTask(
                                contextSubtasks,
                                {
                                  threadId,
                                  runId,
                                  taskId,
                                  roundId: effectiveRoundId,
                                },
                              ),
                              isVisibleRunning: isInferredRunningSubtaskVisible(
                                {
                                  runId,
                                  startedAt,
                                  groupIsLoading,
                                  activeRunIds: activeHistoryRunIds,
                                  turnStartTime: activeTurnStartTime,
                                  hasMatchingActiveTask:
                                    Boolean(matchingActive),
                                },
                              ),
                            })
                          ) {
                            continue;
                          }
                          const task: Subtask = {
                            id: taskId,
                            threadId,
                            runId,
                            roundId: effectiveRoundId,
                            subagent_type: toolCall.args.subagent_type,
                            description: toolCall.args.description,
                            prompt: toolCall.args.prompt,
                            status,
                            ...(startedAt !== undefined ? { startedAt } : {}),
                            ...(status === "failed"
                              ? { error: t.subtasks.failed }
                              : {}),
                          };
                          tasks.add(task);
                        }
                      }
                    }
                  }

                  const results: React.ReactNode[] = [];
                  const subagentDebugMessageIds: string[] = [];
                  if (!hideProtocolUi && groupIsLoading && tasks.size > 0) {
                    results.push(
                      <div
                        key="subtask-count"
                        className="text-muted-foreground pt-2 text-sm font-normal"
                      >
                        {t.subtasks.executing(tasks.size)}
                      </div>,
                    );
                  }
                  for (const message of group.messages.filter(
                    (message) => message.type === "ai",
                  )) {
                    if (
                      !hideProtocolUi &&
                      !hideThinkingUi &&
                      hasReasoning(message)
                    ) {
                      results.push(
                        <MessageGroup
                          key={"thinking-group-" + message.id}
                          messages={[message]}
                          isLoading={groupIsLoading}
                          tokenDebugSteps={tokenDebugSteps.filter(
                            (step) => step.messageId === message.id,
                          )}
                          showTokenDebugSummaries={
                            tokenUsageInlineMode === "step_debug"
                          }
                        />,
                      );
                    } else if (message.id) {
                      subagentDebugMessageIds.push(message.id);
                    }
                    const taskIds = message.tool_calls?.flatMap((toolCall) =>
                      toolCall.name === "task" && toolCall.id
                        ? [toolCall.id]
                        : [],
                    );
                    const runId = getMessageRunId(message);
                    const roundId = getMessageRoundId(message);
                    if (runId) {
                      for (const taskId of taskIds ?? []) {
                        const matchingTerminal =
                          findMatchingTerminalSubtaskForTask(contextSubtasks, {
                            threadId,
                            runId,
                            taskId,
                            roundId,
                          });
                        const matchingActive = findMatchingActiveSubtaskForTask(
                          contextSubtasks,
                          {
                            threadId,
                            runId,
                            taskId,
                            roundId,
                          },
                        );
                        const effectiveRoundId =
                          roundId ??
                          matchingTerminal?.roundId ??
                          matchingActive?.roundId;
                        const status = derivePendingSubtaskStatus(
                          taskId,
                          group.messages,
                          groupIsLoading,
                        );
                        const startedAt = getMessageHistoryTime(message);
                        if (
                          !shouldKeepInferredSubtask({
                            status,
                            hasMatchingTerminal: Boolean(matchingTerminal),
                            hasTerminalInOtherRun: hasTerminalSubtaskForTask(
                              contextSubtasks,
                              {
                                threadId,
                                runId,
                                taskId,
                                roundId: effectiveRoundId,
                              },
                            ),
                            isVisibleRunning: isInferredRunningSubtaskVisible({
                              runId,
                              startedAt,
                              groupIsLoading,
                              activeRunIds: activeHistoryRunIds,
                              turnStartTime: activeTurnStartTime,
                              hasMatchingActiveTask: Boolean(matchingActive),
                            }),
                          })
                        ) {
                          continue;
                        }
                        results.push(
                          <SubtaskCard
                            key={getSubtaskCardKey(
                              taskId,
                              runId,
                              effectiveRoundId,
                            )}
                            runId={runId}
                            roundId={effectiveRoundId}
                            taskId={taskId}
                            threadId={threadId}
                            isLoading={
                              groupIsLoading &&
                              messages.some(
                                (message) => getMessageRunId(message) === runId,
                              )
                            }
                          />,
                        );
                      }
                    }
                    if (hasContent(message)) {
                      const messageIsLoading = isMessageActivelyStreaming(
                        message,
                        groupIsLoading,
                        activeHistoryRunIds,
                      );
                      results.push(
                        <MessageListItem
                          key={`subagent-final-${message.id}`}
                          message={message}
                          isLoading={messageIsLoading}
                          threadId={threadId}
                          showCopyButton={false}
                          hideThinkingUi={hideThinkingUi}
                          turnStartTime={
                            messageIsLoading ? activeTurnStartTime : null
                          }
                        />,
                      );
                    }
                  }
                  return (
                    <div
                      key={"subtask-group-" + groupKey}
                      data-conversation-scroll-anchor
                      className="relative z-1 flex flex-col gap-2"
                    >
                      {results}
                      {renderTokenUsage({
                        messages: group.messages,
                        turnUsageMessages,
                        debugMessageIds: subagentDebugMessageIds,
                      })}
                    </div>
                  );
                }
                return (
                  <div
                    key={"group-" + groupKey}
                    data-conversation-scroll-anchor
                    className="w-full"
                  >
                    <MessageGroup
                      messages={group.messages}
                      isLoading={thread.isLoading}
                      hideThinkingUi={hideThinkingUi}
                      tokenDebugSteps={tokenDebugSteps.filter((step) =>
                        group.messages.some(
                          (message) => message.id === step.messageId,
                        ),
                      )}
                      showTokenDebugSummaries={
                        tokenUsageInlineMode === "step_debug"
                      }
                    />
                    {renderTokenUsage({
                      messages: group.messages,
                      turnUsageMessages,
                      inlineDebug: false,
                    })}
                  </div>
                );
              })}
              {isActiveTurn && runtimeOnlySubtasks.length > 0 && (
                <div className="relative z-1 flex flex-col gap-2">
                  {!hideProtocolUi && (
                    <div className="text-muted-foreground pt-2 text-sm font-normal">
                      {t.subtasks.executing(runtimeOnlySubtasks.length)}
                    </div>
                  )}
                  {runtimeOnlySubtasks.map((task) => (
                    <SubtaskCard
                      key={getSubtaskCardKey(task.id, task.runId, task.roundId)}
                      runId={task.runId!}
                      roundId={task.roundId}
                      taskId={task.id}
                      threadId={threadId}
                      isLoading={true}
                    />
                  ))}
                </div>
              )}
              {footerParts.length > 0 && (
                <div className="text-muted-foreground mt-3 text-xs">
                  {footerParts.join(" · ")}
                </div>
              )}
            </section>
          );
        })}
        {thread.isLoading &&
          !hideProtocolUi &&
          !hideThinkingUi &&
          !hasActiveAssistantText && (
            <div className="w-full">
              <Reasoning isStreaming={true} startTimeProp={activeTurnStartTime}>
                <ReasoningTrigger hasContent={false} />
              </Reasoning>
            </div>
          )}
        <CommandRoomTrajectory
          chairMessages={commandRoomStepMessages}
          steps={commandRoomTrajectory}
          unstagedTasks={unstagedCommandRoomTasks}
        />
        {recoveryStatus &&
          (recoveryStatus.state !== "terminal" || shouldShowTerminalNotice) && (
            <RunRecoveryNotice
              status={recoveryStatus}
              onRetry={onRetryRecovery}
            />
          )}
        {!recoveryStatus && shouldShowTerminalNotice && terminalNotice && (
          <RunTerminalNotice notice={terminalNotice} />
        )}
        <div style={{ height: `${paddingBottom}px` }} />
      </ConversationContent>
      <ConversationTurnScrollController
        historyPrependRef={historyPrependRef}
        threadId={threadId}
        activeTurnId={activeConversationTurn?.id}
        isStreaming={thread.isLoading}
      />
    </Conversation>
  );
}
