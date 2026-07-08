import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import {
  AlertCircleIcon,
  BrainCircuitIcon,
  ChevronUpIcon,
  Loader2Icon,
  RefreshCcwIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
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
import { HISTORY_CREATED_AT_KEY } from "@/core/threads/hooks";
import type {
  ThreadRunTerminalNotice,
  useThreadStream,
} from "@/core/threads/hooks";
import type { ThreadContextUsageSnapshot } from "@/core/threads/types";
import { cn } from "@/lib/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";
import { CopyButton } from "../copy-button";
import { Tooltip } from "../tooltip";

import { MarkdownContent } from "./markdown-content";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";
import {
  MessageTokenUsageDebugList,
  MessageTokenUsageList,
} from "./message-token-usage";
import { MessageListSkeleton } from "./skeleton";
import { SubtaskCard } from "./subtask-card";

export const MESSAGE_LIST_DEFAULT_PADDING_BOTTOM = 24;

const LOAD_MORE_HISTORY_THROTTLE_MS = 1200;

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

function getMessageRoundId(message: Message) {
  const value =
    message.additional_kwargs?.deerflow_round_id ??
    message.additional_kwargs?.round_id ??
    message.additional_kwargs?.roundId;
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function isTerminalSubtask(task: Subtask) {
  return task.status === "completed" || task.status === "failed";
}

export function hasTerminalSubtaskForTask(
  subtasks: Subtask[],
  {
    threadId,
    runId,
    taskId,
    roundId,
  }: {
    threadId: string;
    runId?: string | null;
    taskId: string;
    roundId?: string | null;
  },
) {
  const normalizedRoundId = normalizeSubtaskRoundId(roundId);
  return subtasks.some(
    (task) =>
      task.threadId === threadId &&
      task.id === taskId &&
      task.runId !== runId &&
      normalizeSubtaskRoundId(task.roundId) === normalizedRoundId &&
      isTerminalSubtask(task),
  );
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

function LoadMoreHistoryIndicator({
  isLoading,
  hasMore,
  loadMore,
}: {
  isLoading?: boolean;
  hasMore?: boolean;
  loadMore?: () => void;
}) {
  const { t } = useI18n();
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoadRef = useRef(0);

  const throttledLoadMore = useCallback(() => {
    if (!hasMore || isLoading) {
      return;
    }

    const now = Date.now();
    const remaining =
      LOAD_MORE_HISTORY_THROTTLE_MS - (now - lastLoadRef.current);

    if (remaining <= 0) {
      lastLoadRef.current = now;
      loadMore?.();
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
      loadMore?.();
    }, remaining);
  }, [hasMore, isLoading, loadMore]);

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

function RunRecoveryNotice({
  status,
  onRetry,
}: {
  status: NonNullable<ThreadRecoveryStatus>;
  onRetry?: () => void;
}) {
  const isRepairing = status.state === "repairing";
  const isFailed = status.state === "failed";
  const title = isRepairing
    ? "正在恢复运行状态"
    : isFailed
      ? "恢复失败"
      : "运行已终止";
  const description = isRepairing
    ? "正在从中断连接恢复运行状态，当前不会标记为业务成功。"
    : isFailed
      ? status.reason
      : `终止原因：${status.reason ?? "unknown"}`;
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
          重试恢复
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
  hideThinkingUi = false,
  contextSnapshot,
  hasMoreHistory,
  loadMoreHistory,
  isHistoryLoading,
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
  contextSnapshot?: ThreadContextUsageSnapshot | null;
  hasMoreHistory?: boolean;
  loadMoreHistory?: () => void;
  isHistoryLoading?: boolean;
  terminalNotice?: ThreadRunTerminalNotice | null;
  recoveryStatus?: ThreadRecoveryStatus;
  onRetryRecovery?: () => void;
  onRegenerateMessage?: (
    messageId: string,
    supersededMessageIds: string[],
  ) => void | Promise<void>;
  canRegenerate?: boolean;
}) {
  const { t } = useI18n();
  const [turnStartTime, setTurnStartTime] = useState<number | null>(null);
  const prevIsLoading = useRef(thread.isLoading);

  useEffect(() => {
    if (thread.isLoading && !prevIsLoading.current) {
      setTurnStartTime(Date.now());
    }
    prevIsLoading.current = thread.isLoading;
  }, [thread.isLoading]);
  const messages = thread.messages;
  const groupedMessages = useMemo(() => getMessageGroups(messages), [messages]);
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
  const shouldShowTerminalNotice = Boolean(
    terminalNotice && !thread.isLoading && !hasActiveAssistantText,
  );
  const rehypePlugins = useRehypeSplitWordsIntoSpans(thread.isLoading);
  const updateSubtask = useUpdateSubtask();
  const contextSubtasks = useSubtasksForThread(threadId);
  const lastGroupIndex = groupedMessages.length - 1;
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
            const status = derivePendingSubtaskStatus(
              toolCall.id,
              group.messages,
              groupIsLoading &&
                messages.some((message) => getMessageRunId(message) === runId),
            );
            if (
              status === "in_progress" &&
              hasTerminalSubtaskForTask(contextSubtasks, {
                threadId,
                runId,
                taskId: toolCall.id,
                roundId,
              })
            ) {
              continue;
            }
            const startedAt = getMessageHistoryTime(message);
            updates.push({
              id: toolCall.id,
              threadId,
              runId,
              roundId,
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
        Boolean(task.runId) &&
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
  }, [anchoredSubtaskKeys, contextSubtasks, thread.isLoading, threadId]);
  const activeTurnStartTime = useMemo(() => {
    return (
      getMessagesHistoryStartTime(
        groupedMessages[lastGroupIndex]?.messages ?? [],
      ) ?? turnStartTime
    );
  }, [groupedMessages, lastGroupIndex, turnStartTime]);
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
      const turnElement = event.currentTarget.closest("[data-assistant-turn]");
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
            <div className="flex gap-1 opacity-0 transition-opacity delay-200 duration-300 group-hover/assistant-turn:opacity-100">
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
                contextSnapshotForTurn.estimated_tokens,
              )}`}
            >
              <BrainCircuitIcon className="size-3.5" />
              <span className="hidden sm:inline">{t.contextUsage.label}</span>
              <span className="font-mono">
                {formatContextCount(contextSnapshotForTurn.estimated_tokens)}
              </span>
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
            看一下
          </Button>
        </div>
      );
    },
    [
      canRegenerate,
      handleReviewTurnClick,
      onRegenerateMessage,
      regeneratingMessageId,
      t.contextUsage.label,
      t.common.regenerate,
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
      className={cn("flex size-full flex-col justify-center", className)}
      resize={thread.isLoading ? "smooth" : undefined}
    >
      <ConversationContent className="mx-auto w-full max-w-(--container-width-md) gap-8 pt-8">
        <LoadMoreHistoryIndicator
          isLoading={isHistoryLoading}
          hasMore={hasMoreHistory}
          loadMore={loadMoreHistory}
        />
        {groupedMessages.map((group, groupIndex) => {
          const turnUsageMessages = turnUsageMessagesByGroupIndex[groupIndex];
          const groupIsLoading =
            thread.isLoading && groupIndex === lastGroupIndex;
          const groupKey = getMessageGroupKey(group, groupIndex);

          if (group.type === "human" || group.type === "assistant") {
            return (
              <div
                key={groupKey}
                data-assistant-turn={
                  group.type === "assistant" ? true : undefined
                }
                className={cn(
                  "w-full",
                  group.type === "assistant" && "group/assistant-turn",
                )}
              >
                {group.messages.map((msg) => {
                  return (
                    <MessageListItem
                      key={`${group.id}/${msg.id}`}
                      message={msg}
                      isLoading={
                        thread.isLoading &&
                        groupIndex === groupedMessages.length - 1
                      }
                      threadId={threadId}
                      showCopyButton={group.type !== "assistant"}
                      hideThinkingUi={hideThinkingUi}
                      turnStartTime={
                        groupIndex === groupedMessages.length - 1
                          ? activeTurnStartTime
                          : null
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
                <div key={groupKey} className="w-full">
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
                const presentFiles = extractPresentFilesFromMessage(message);
                files.push(...presentFiles);
              }
            }
            return (
              <div className="w-full" key={groupKey}>
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
                    const status = derivePendingSubtaskStatus(
                      taskId,
                      group.messages,
                      groupIsLoading,
                    );
                    if (
                      status === "in_progress" &&
                      hasTerminalSubtaskForTask(contextSubtasks, {
                        threadId,
                        runId,
                        taskId,
                        roundId,
                      })
                    ) {
                      continue;
                    }
                    const startedAt = getMessageHistoryTime(message);
                    const task: Subtask = {
                      id: taskId,
                      threadId,
                      runId,
                      roundId,
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
              if (!hideProtocolUi && !hideThinkingUi && hasReasoning(message)) {
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
                toolCall.name === "task" && toolCall.id ? [toolCall.id] : [],
              );
              const runId = getMessageRunId(message);
              const roundId = getMessageRoundId(message);
              if (runId) {
                for (const taskId of taskIds ?? []) {
                  if (
                    hasTerminalSubtaskForTask(contextSubtasks, {
                      threadId,
                      runId,
                      taskId,
                      roundId,
                    })
                  ) {
                    continue;
                  }
                  results.push(
                    <SubtaskCard
                      key={getSubtaskCardKey(taskId, runId, roundId)}
                      runId={runId}
                      roundId={roundId}
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
                results.push(
                  <MessageListItem
                    key={`subagent-final-${message.id}`}
                    message={message}
                    isLoading={groupIsLoading}
                    threadId={threadId}
                    showCopyButton={false}
                    hideThinkingUi={hideThinkingUi}
                    turnStartTime={
                      groupIndex === lastGroupIndex ? activeTurnStartTime : null
                    }
                  />,
                );
              }
            }
            return (
              <div
                key={"subtask-group-" + groupKey}
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
            <div key={"group-" + groupKey} className="w-full">
              <MessageGroup
                messages={group.messages}
                isLoading={thread.isLoading}
                hideThinkingUi={hideThinkingUi}
                tokenDebugSteps={tokenDebugSteps.filter((step) =>
                  group.messages.some(
                    (message) => message.id === step.messageId,
                  ),
                )}
                showTokenDebugSummaries={tokenUsageInlineMode === "step_debug"}
              />
              {renderTokenUsage({
                messages: group.messages,
                turnUsageMessages,
                inlineDebug: false,
              })}
            </div>
          );
        })}
        {runtimeOnlySubtasks.length > 0 && (
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
        {recoveryStatus && (
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
    </Conversation>
  );
}
