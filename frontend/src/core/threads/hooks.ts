import type { Message, Run } from "@langchain/langgraph-sdk";
import type { ThreadsClient } from "@langchain/langgraph-sdk/client";
import { useStream } from "@langchain/langgraph-sdk/react";
import {
  type QueryClient,
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { toast } from "sonner";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";

import { clearReconnectRun, getAPIClient } from "../api";
import {
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  fetch,
} from "../api/fetcher";
import { getBackendBaseURL } from "../config";
import { useI18n } from "../i18n/hooks";
import { isHiddenFromUIMessage } from "../messages/utils";
import type { FileInMessage } from "../messages/utils";
import type { LocalSettings } from "../settings";
import { clearThreadModelName } from "../settings/store";
import {
  type SubtaskUpdate,
  useClearSubtasksForThread,
  useSettleRunningSubtasksForRun,
  useUpdateSubtask,
} from "../tasks/context";
import type { UploadedFileInfo } from "../uploads";
import { promptInputFilePartToFile, uploadFiles } from "../uploads";

import { fetchThreadContextUsage, fetchThreadTokenUsage } from "./api";
import { notifyThreadRuntimeDeleted } from "./runtime-events";
import {
  buildThreadsSearchQueryOptions,
  DEFAULT_THREAD_SEARCH_PARAMS,
  type ThreadSearchParams,
} from "./thread-search-query";
import {
  threadContextUsageQueryKey,
  threadTokenUsageQueryKey,
} from "./token-usage";
import type {
  AgentThread,
  AgentThreadContext,
  AgentThreadState,
  RunMessage,
  ThreadContextUsageResponse,
  ThreadTokenUsageResponse,
} from "./types";

export type ToolEndEvent = {
  name: string;
  data: unknown;
  threadId: string;
  runId: string;
};

export type ThreadStreamFinishMeta = {
  threadId: string | null;
  runId: string | null;
};

type ThreadStreamOwnerSnapshot = {
  threadId: string | null;
  runId: string | null;
};

export type ThreadStreamOptions = {
  threadId?: string | null | undefined;
  displayThreadId?: string | null | undefined;
  runtimeOwnerId?: string | null | undefined;
  context: LocalSettings["context"];
  isMock?: boolean;
  onSend?: (threadId: string) => void;
  onStart?: (threadId: string, runId: string) => void;
  onFinish?: (state: AgentThreadState, meta: ThreadStreamFinishMeta) => void;
  onToolEnd?: (event: ToolEndEvent) => void;
};

type SendMessageOptions = {
  additionalKwargs?: Record<string, unknown>;
};

type QueuedThreadMessage = {
  ownerId: string;
  threadId: string;
  message: PromptInputMessage;
  extraContext?: Record<string, unknown>;
  options?: SendMessageOptions;
};

type SendRequestOwnership = {
  requestId: string;
  threadId: string;
};

type ThreadActivitySnapshot = {
  running: ReadonlySet<string>;
  finished: ReadonlySet<string>;
};

const PUBLIC_PROVIDER_TRANSIENT_ERROR_MESSAGE =
  "The configured LLM provider is temporarily unavailable after multiple retries. Please wait a moment and continue the conversation.";
const PROVIDER_TRANSIENT_ERROR_MARKERS = [
  "codex api stream ended without response.completed event",
  "codexstreamincompleteerror",
  "response.completed",
];

export type StreamErrorRecoveryRun = {
  threadId: string;
  runId: string;
};

const ACTIVE_THREAD_STATUSES = new Set([
  "busy",
  "pending",
  "running",
  "cancelling",
  "rolling_back",
]);
const INACTIVE_THREAD_STATUSES = new Set([
  "idle",
  "error",
  "timeout",
  "interrupted",
  "cancelled",
  "timed_out",
  "boundary_stopped",
  "worker_lost",
  "rolled_back",
  "rollback_failed",
]);
const BACKGROUND_RUN_PROBE_DELAY_MS = 5000;
const BACKGROUND_RUN_PROBE_MAX_DELAY_MS = 30000;
const BACKGROUND_RUN_PROBE_MAX_ATTEMPTS = 12;
const BACKGROUND_RUN_PROBE_STOP_STATUS_CODES = new Set([401, 403, 404]);
const backgroundRunProbeTimers = new Map<string, number>();
const backgroundRunProbeAttempts = new Map<string, number>();

type QueuedMessageReleaseState = {
  streamFinished: boolean;
  sendInFlight: boolean;
  recovering: boolean;
  queuedOwnerId?: string | null;
  currentOwnerId?: string | null;
  queuedThreadId?: string | null;
  currentViewThreadId?: string | null;
};
type QueuedMessageAdmissionState = {
  isLoading: boolean;
  streamFinished: boolean;
  recovering: boolean;
  sendInFlight: boolean;
};

type StreamOwnershipState = {
  eventThreadId?: string | null;
  eventRunId?: string | null;
  streamThreadId?: string | null;
  streamRunId?: string | null;
  viewThreadId?: string | null;
  liveMessagesThreadId?: string | null;
  optimisticThreadId?: string | null;
};

const threadActivityListeners = new Set<() => void>();
let threadActivitySnapshot: ThreadActivitySnapshot = {
  running: new Set(),
  finished: new Set(),
};
const manualThreadTitleLocks = new Map<string, string>();
const deletedThreadTombstones = new Set<string>();

export function isDeletedThreadTombstoned(threadId: string | null | undefined) {
  return Boolean(threadId && deletedThreadTombstones.has(threadId));
}

function emitThreadActivity() {
  for (const listener of threadActivityListeners) {
    listener();
  }
}

export function getThreadActivitySnapshot() {
  return threadActivitySnapshot;
}

export function useThreadActivity() {
  return useSyncExternalStore(
    (listener) => {
      threadActivityListeners.add(listener);
      return () => threadActivityListeners.delete(listener);
    },
    getThreadActivitySnapshot,
    getThreadActivitySnapshot,
  );
}

export function markThreadFinished(threadId: string) {
  if (isDeletedThreadTombstoned(threadId)) {
    return;
  }
  threadActivitySnapshot = {
    running: new Set(
      [...threadActivitySnapshot.running].filter((id) => id !== threadId),
    ),
    finished: new Set([...threadActivitySnapshot.finished, threadId]),
  };
  emitThreadActivity();
}

export function clearThreadActivity(threadId: string) {
  threadActivitySnapshot = {
    running: new Set(
      [...threadActivitySnapshot.running].filter((id) => id !== threadId),
    ),
    finished: new Set(
      [...threadActivitySnapshot.finished].filter((id) => id !== threadId),
    ),
  };
  emitThreadActivity();
}

export function clearThreadFinishedActivity(threadId: string) {
  if (!threadActivitySnapshot.finished.has(threadId)) {
    return;
  }
  threadActivitySnapshot = {
    running: threadActivitySnapshot.running,
    finished: new Set(
      [...threadActivitySnapshot.finished].filter((id) => id !== threadId),
    ),
  };
  emitThreadActivity();
}

export function shouldShowThreadRunningStatus(
  status: unknown,
  locallyRunning: boolean,
) {
  if (typeof status === "string") {
    if (ACTIVE_THREAD_STATUSES.has(status)) {
      return true;
    }
    if (INACTIVE_THREAD_STATUSES.has(status)) {
      return false;
    }
  }
  return locallyRunning;
}

export function shouldReleaseQueuedThreadMessage({
  streamFinished,
  sendInFlight,
  recovering,
  queuedOwnerId,
  currentOwnerId,
  queuedThreadId,
  currentViewThreadId,
}: QueuedMessageReleaseState) {
  const hasOwner = Boolean(queuedOwnerId && currentOwnerId);
  const ownedByCurrentRuntime = hasOwner
    ? queuedOwnerId === currentOwnerId
    : Boolean(
        queuedThreadId &&
        currentViewThreadId &&
        queuedThreadId === currentViewThreadId,
      );
  return (
    streamFinished && !sendInFlight && !recovering && ownedByCurrentRuntime
  );
}

export function shouldQueueThreadMessage({
  isLoading,
  streamFinished,
  recovering,
  sendInFlight,
}: QueuedMessageAdmissionState) {
  return (isLoading && !streamFinished) || recovering || sendInFlight;
}

export function createOptimisticMessageId(prefix: string) {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

export function isSameSendRequest(
  current: SendRequestOwnership | null,
  request: SendRequestOwnership,
) {
  return (
    current?.threadId === request.threadId &&
    current.requestId === request.requestId
  );
}

export function getScopedToolEndEvent(
  event: { name?: unknown; data?: unknown },
  threadId: string | null | undefined,
  runId: string | null | undefined,
): ToolEndEvent | null {
  if (typeof event.name !== "string" || !threadId || !runId) {
    return null;
  }
  return {
    name: event.name,
    data: event.data,
    threadId,
    runId,
  };
}

export function setManualThreadTitleLock(threadId: string, title: string) {
  manualThreadTitleLocks.set(threadId, title);
}

export function getManualThreadTitleLock(threadId: string) {
  return manualThreadTitleLocks.get(threadId);
}

function shouldAcceptStreamTitle(threadId: string | null, title: string) {
  if (!threadId) {
    return false;
  }
  const manualTitle = manualThreadTitleLocks.get(threadId);
  return !manualTitle || manualTitle === title;
}

export function resolveAssistantId(agentName: unknown): string {
  return typeof agentName === "string" && agentName.length > 0
    ? agentName
    : "lead_agent";
}

export function buildThreadRunContext(
  baseContext: LocalSettings["context"],
  threadId: string,
  extraContext?: Record<string, unknown>,
): AgentThreadContext {
  const mergedContext = {
    ...extraContext,
    ...baseContext,
  };
  const isCommandRoom = mergedContext.agent_name === "command-room";
  const mode = isCommandRoom ? "ultra" : mergedContext.mode;
  const requestedReasoningEffort =
    mergedContext.reasoning_effort === "minimal" ||
    mergedContext.reasoning_effort === "low"
      ? isCommandRoom
        ? "high"
        : "xhigh"
      : mergedContext.reasoning_effort;

  return {
    ...mergedContext,
    model_name: mergedContext.model_name,
    mode,
    thinking_enabled: mode !== "flash",
    is_plan_mode: mode === "pro" || mode === "ultra",
    subagent_enabled: mode === "ultra",
    reasoning_effort: isCommandRoom
      ? (requestedReasoningEffort ?? "high")
      : (requestedReasoningEffort ??
        (mode === "ultra"
          ? "xhigh"
          : mode === "pro" || mode === "thinking"
            ? "xhigh"
            : undefined)),
    reasoning_summary: mergedContext.reasoning_summary,
    text_verbosity: mergedContext.text_verbosity,
    thread_id: threadId,
  };
}

type RegeneratePrepareResponse = {
  input: Partial<AgentThreadState>;
  checkpoint: {
    checkpoint_ns: string;
    checkpoint_id: string;
    checkpoint_map: Record<string, unknown> | null;
  };
  metadata: Record<string, unknown>;
  target_run_id: string;
};

const EMPTY_THREAD_VALUES: AgentThreadState = {
  title: "",
  messages: [],
  artifacts: [],
  todos: [],
};
export const HISTORY_CREATED_AT_KEY = "history_created_at";

function isNonEmptyString(value: string | undefined): value is string {
  return typeof value === "string" && value.length > 0;
}

const SUMMARIZATION_MIDDLEWARE_UPDATE_KEYS = new Set([
  "SummarizationMiddleware.before_model",
  "DeerFlowSummarizationMiddleware.before_model",
]);

function baseMessageIdentity(message: Message): string | undefined {
  if (
    "tool_call_id" in message &&
    typeof message.tool_call_id === "string" &&
    message.tool_call_id.length > 0
  ) {
    return `tool:${message.tool_call_id}`;
  }
  if (typeof message.id === "string" && message.id.length > 0) {
    return `message:${message.id}`;
  }
  return undefined;
}

function messageRunId(message: Message): string | undefined {
  const runId =
    message.additional_kwargs?.deerflow_run_id ??
    message.additional_kwargs?.run_id;
  return typeof runId === "string" && runId.length > 0 ? runId : undefined;
}

function runScopedBaseMessageIdentity(message: Message): string | undefined {
  const identity = baseMessageIdentity(message);
  const runId = messageRunId(message);
  return identity && runId ? `${runId}:${identity}` : undefined;
}

function liveOverlapMessageIdentities(
  message: Message,
  includeBaseIdentity: boolean,
): string[] {
  const runScopedIdentity = runScopedBaseMessageIdentity(message);
  const baseIdentity = baseMessageIdentity(message);
  return [
    runScopedIdentity,
    includeBaseIdentity ? baseIdentity : undefined,
  ].filter(isNonEmptyString);
}

function messageIdentity(message: Message): string | undefined {
  const historyIdentity = (message as MessageWithHistoryIdentity)[
    HISTORY_IDENTITY_SYMBOL
  ];
  if (typeof historyIdentity === "string" && historyIdentity.length > 0) {
    return `history:${historyIdentity}`;
  }
  return runScopedBaseMessageIdentity(message) ?? baseMessageIdentity(message);
}

function dedupeMessagesByIdentity(messages: Message[]): Message[] {
  const lastIndexByIdentity = new Map<string, number>();
  const lastVisibleIndexByIdentity = new Map<string, number>();

  // This is a UI-display dedupe rule, not a general LangChain message-stream
  // contract. Hidden messages that share an identity with a visible message are
  // treated as control messages for this merged view; hidden messages carrying
  // independent tracing/task semantics should use a distinct id or a custom
  // stream/state channel instead of relying on message dedupe preservation.
  const preservedTurnDurations = new Map<string, number>();
  const preservedHistoryCreatedAt = new Map<string, unknown>();
  messages.forEach((message, index) => {
    const identity = messageIdentity(message);
    if (identity) {
      lastIndexByIdentity.set(identity, index);
      if (!isHiddenFromUIMessage(message)) {
        lastVisibleIndexByIdentity.set(identity, index);
      }
      if (message.additional_kwargs?.turn_duration !== undefined) {
        preservedTurnDurations.set(
          identity,
          message.additional_kwargs.turn_duration as number,
        );
      }
      if (message.additional_kwargs?.[HISTORY_CREATED_AT_KEY] !== undefined) {
        preservedHistoryCreatedAt.set(
          identity,
          message.additional_kwargs[HISTORY_CREATED_AT_KEY],
        );
      }
    }
  });

  return messages
    .filter((message, index) => {
      const identity = messageIdentity(message);
      if (!identity) {
        return true;
      }
      const visibleIndex = lastVisibleIndexByIdentity.get(identity);
      if (visibleIndex !== undefined) {
        return visibleIndex === index;
      }
      return lastIndexByIdentity.get(identity) === index;
    })
    .map((message) => {
      const identity = messageIdentity(message);
      if (
        identity &&
        preservedTurnDurations.has(identity) &&
        message.additional_kwargs?.turn_duration === undefined
      ) {
        return {
          ...message,
          additional_kwargs: {
            ...message.additional_kwargs,
            turn_duration: preservedTurnDurations.get(identity),
            ...(message.additional_kwargs?.[HISTORY_CREATED_AT_KEY] ===
              undefined && preservedHistoryCreatedAt.has(identity)
              ? {
                  [HISTORY_CREATED_AT_KEY]:
                    preservedHistoryCreatedAt.get(identity),
                }
              : {}),
          },
        } as Message;
      }
      if (
        identity &&
        preservedHistoryCreatedAt.has(identity) &&
        message.additional_kwargs?.[HISTORY_CREATED_AT_KEY] === undefined
      ) {
        return {
          ...message,
          additional_kwargs: {
            ...message.additional_kwargs,
            [HISTORY_CREATED_AT_KEY]: preservedHistoryCreatedAt.get(identity),
          },
        } as Message;
      }
      return message;
    });
}

const HISTORY_IDENTITY_SYMBOL = Symbol("deerflow.historyIdentity");

type MessageWithHistoryIdentity = Message & {
  [HISTORY_IDENTITY_SYMBOL]?: string;
};

type VisibleRunMessage = RunMessage & { content: Message };

function isMessageContent(value: RunMessage["content"]): value is Message {
  if (typeof value !== "object" || value === null || !("type" in value)) {
    return false;
  }
  const type = (value as { type?: unknown }).type;
  return (
    type === "human" ||
    type === "ai" ||
    type === "system" ||
    type === "tool" ||
    type === "remove"
  );
}

function runMessageIdentity(message: RunMessage): string | undefined {
  return isMessageContent(message.content)
    ? baseMessageIdentity(message.content)
    : undefined;
}

function historyMessageFromRunMessage(message: VisibleRunMessage): Message {
  const identity = baseMessageIdentity(message.content);
  const result = {
    ...message.content,
    additional_kwargs: {
      ...message.content.additional_kwargs,
      deerflow_run_id: message.run_id,
      ...(typeof message.seq === "number"
        ? { deerflow_run_seq: message.seq }
        : {}),
      ...(typeof message.metadata?.caller === "string" &&
      message.metadata.caller.length > 0
        ? { deerflow_caller: message.metadata.caller }
        : {}),
      [HISTORY_CREATED_AT_KEY]: message.created_at,
    },
  } as MessageWithHistoryIdentity;
  if (identity) {
    Object.defineProperty(result, HISTORY_IDENTITY_SYMBOL, {
      value: `${message.run_id}:${identity}`,
      enumerable: false,
    });
  }
  return result;
}

function dedupeRunMessagesByIdentity(messages: RunMessage[]): RunMessage[] {
  const lastIndexByIdentity = new Map<string, number>();
  messages.forEach((message, index) => {
    const identity = runMessageIdentity(message);
    if (identity) {
      lastIndexByIdentity.set(`${message.run_id}:${identity}`, index);
    }
  });

  return messages.filter((message, index) => {
    const identity = runMessageIdentity(message);
    if (!identity) {
      return true;
    }
    return lastIndexByIdentity.get(`${message.run_id}:${identity}`) === index;
  });
}

export function mergeFetchedRunMessages(
  previous: RunMessage[],
  fetched: RunMessage[],
  runId: string,
  replaceRun: boolean,
) {
  let base = previous;
  if (replaceRun) {
    const fetchedSeqs = fetched
      .map((message) => message.seq)
      .filter((seq): seq is number => typeof seq === "number");
    if (fetchedSeqs.length > 0) {
      const minFetchedSeq = Math.min(...fetchedSeqs);
      base = previous.filter(
        (message) =>
          message.run_id !== runId ||
          typeof message.seq !== "number" ||
          message.seq < minFetchedSeq,
      );
    }
  }
  return dedupeRunMessagesByIdentity(
    replaceRun ? [...base, ...fetched] : [...fetched, ...base],
  );
}

export function partitionKnownRunIds(runIds: Iterable<string>, runs: Run[]) {
  const knownRunIds = new Set(runs.map((run) => run.run_id));
  const known: string[] = [];
  const pending: string[] = [];
  for (const runId of runIds) {
    if (knownRunIds.has(runId)) {
      known.push(runId);
    } else {
      pending.push(runId);
    }
  }
  return { known, pending };
}

export function resetLoadedRunStateForRefresh(
  runIds: Iterable<string>,
  runs: Run[],
  loadedRunIds: Set<string>,
  runBeforeSeq: Map<string, number>,
) {
  const result = partitionKnownRunIds(runIds, runs);
  for (const runId of result.known) {
    loadedRunIds.delete(runId);
    runBeforeSeq.delete(runId);
  }
  return result;
}

export function getSupersededRunIds(
  runs: Run[] | undefined,
  pendingSupersededRunIds?: ReadonlySet<string>,
) {
  const ids = new Set(pendingSupersededRunIds ?? []);
  for (const run of runs ?? []) {
    if (run.status !== "success") {
      continue;
    }
    const metadata = run.metadata;
    if (metadata && typeof metadata === "object") {
      const fromRunId = Reflect.get(metadata, "regenerate_from_run_id");
      if (typeof fromRunId === "string" && fromRunId) {
        ids.add(fromRunId);
      }
    }
  }
  return ids;
}

export function removeSetItems<T>(
  values: ReadonlySet<T>,
  itemsToRemove: Iterable<T>,
) {
  const next = new Set(values);
  for (const item of itemsToRemove) {
    next.delete(item);
  }
  return next;
}

export function shouldShowLiveThreadState(
  viewThreadId: string | null,
  streamThreadId: string | null,
  liveMessagesThreadId: string | null,
) {
  if (!viewThreadId) {
    return false;
  }
  return (
    streamThreadId === viewThreadId || liveMessagesThreadId === viewThreadId
  );
}

export function shouldShowThreadHistory(
  viewThreadId: string | null,
  historyThreadId: string | null,
) {
  return Boolean(viewThreadId) && historyThreadId === viewThreadId;
}

type LiveMessagesSnapshot = {
  threadId: string;
  runId: string | null;
  messages: Message[];
};

export function getThreadMessagesWithLiveSnapshot({
  viewThreadId,
  threadMessages,
  liveSnapshot,
  pendingSupersededMessageIds,
}: {
  viewThreadId: string | null;
  threadMessages: Message[];
  liveSnapshot: LiveMessagesSnapshot | null;
  pendingSupersededMessageIds: ReadonlySet<string>;
}): Message[] {
  if (
    !viewThreadId ||
    liveSnapshot?.threadId !== viewThreadId ||
    liveSnapshot.messages.length === 0
  ) {
    return threadMessages;
  }

  const snapshotMessages = liveSnapshot.messages.filter(
    (message) => !message.id || !pendingSupersededMessageIds.has(message.id),
  );
  if (snapshotMessages.length === 0) {
    return threadMessages;
  }

  const snapshotByIdentity = new Map<string, Message>();
  for (const message of snapshotMessages) {
    const identity = messageIdentity(message);
    if (identity) {
      snapshotByIdentity.set(identity, message);
    }
  }

  const seen = new Set<string>();
  const replacedThreadMessages = threadMessages.map((message) => {
    const identity = messageIdentity(message);
    if (!identity) {
      return message;
    }
    seen.add(identity);
    return snapshotByIdentity.get(identity) ?? message;
  });

  const missingSnapshotMessages = snapshotMessages.filter((message) => {
    const identity = messageIdentity(message);
    return !identity || !seen.has(identity);
  });

  if (missingSnapshotMessages.length === 0) {
    return replacedThreadMessages;
  }

  return dedupeMessagesByIdentity([
    ...replacedThreadMessages,
    ...missingSnapshotMessages,
  ]);
}

export function resolveVisibleTaskRunningThreadId({
  eventThreadId,
  streamThreadId,
  viewThreadId,
  liveMessagesThreadId,
}: {
  eventThreadId?: string | null;
  streamThreadId?: string | null;
  viewThreadId: string | null;
  liveMessagesThreadId: string | null;
}) {
  if (eventThreadId) {
    return eventThreadId;
  }
  if (streamThreadId) {
    return streamThreadId;
  }
  if (!viewThreadId || !liveMessagesThreadId) {
    return null;
  }
  return liveMessagesThreadId === viewThreadId ? viewThreadId : null;
}

const TASK_EVENT_CALLER = "task_event";
export const TASK_EVENT_SCHEMA_VERSION = "deerflow.task-event/v1";
const TASK_EVENT_TYPES = new Set([
  "task_started",
  "task_running",
  "task_completed",
  "task_failed",
  "task_cancelled",
  "task_timed_out",
]);
const RUN_TERMINAL_EVENT_TYPE = "run.terminal";

type PersistedTaskEvent = {
  type?: unknown;
  event_type?: unknown;
  schema_version?: unknown;
  task_id?: unknown;
  thread_id?: unknown;
  run_id?: unknown;
  status?: unknown;
  summary?: unknown;
  result_preview?: unknown;
  error_preview?: unknown;
  redacted?: unknown;
  artifact_refs?: unknown;
  created_at?: unknown;
  started_at?: unknown;
  updated_at?: unknown;
  completed_at?: unknown;
  finished_at?: unknown;
  description?: unknown;
  subagent_type?: unknown;
  prompt?: unknown;
  message?: unknown;
  result?: unknown;
  error?: unknown;
  action_result?: unknown;
};

type RunTerminalEvent = {
  type: typeof RUN_TERMINAL_EVENT_TYPE;
  event_type: typeof RUN_TERMINAL_EVENT_TYPE;
  thread_id: string;
  run_id: string;
  status: string;
  terminal_reason: string;
};

type TaskEventUpdateSubtask = (task: SubtaskUpdate) => void;
type SettleRunSubtasks = (terminal: {
  threadId: string;
  runId: string;
  status: string;
  terminalReason?: string;
}) => void;
type TerminalRunSettlementOptions = {
  terminalReason?: string;
  settleRunSubtasks?: SettleRunSubtasks;
};

export function asTaskEvent(value: unknown): PersistedTaskEvent | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const event = value as PersistedTaskEvent;
  const eventType = taskEventType(event);
  const schemaVersion = stringValue(event.schema_version);
  if (!eventType || !TASK_EVENT_TYPES.has(eventType)) {
    return null;
  }
  // Missing schema_version is accepted for legacy persisted task events, but
  // unknown future schemas are rejected so replay cannot reinterpret payloads.
  if (schemaVersion && schemaVersion !== TASK_EVENT_SCHEMA_VERSION) {
    return null;
  }
  if (
    !stringValue(event.task_id) ||
    !stringValue(event.thread_id) ||
    !stringValue(event.run_id)
  ) {
    return null;
  }
  return { ...event, type: eventType, event_type: eventType };
}

function stringValue(value: unknown) {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function taskEventType(event: PersistedTaskEvent | null | undefined) {
  return stringValue(event?.event_type) ?? stringValue(event?.type);
}

export function asRunTerminalEvent(value: unknown): RunTerminalEvent | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const event = value as Record<string, unknown>;
  const eventType = stringValue(event.event_type) ?? stringValue(event.type);
  const threadId = stringValue(event.thread_id);
  const runId = stringValue(event.run_id);
  const status = stringValue(event.status);
  const terminalReason = stringValue(event.terminal_reason);
  if (
    eventType !== RUN_TERMINAL_EVENT_TYPE ||
    !threadId ||
    !runId ||
    !status ||
    !terminalReason
  ) {
    return null;
  }
  return {
    type: RUN_TERMINAL_EVENT_TYPE,
    event_type: RUN_TERMINAL_EVENT_TYPE,
    thread_id: threadId,
    run_id: runId,
    status,
    terminal_reason: terminalReason,
  };
}

function taskLaneStatus(status: string): NonNullable<SubtaskUpdate["status"]> {
  if (status === "completed") {
    return "completed";
  }
  if (
    status === "in_progress" ||
    status === "running" ||
    status === "pending"
  ) {
    return "in_progress";
  }
  return "failed";
}

export function taskLaneSubtaskUpdate(lane: TaskLaneSnapshot): SubtaskUpdate {
  const status = taskLaneStatus(lane.status);
  const description = lane.role ? `${lane.role} task` : lane.task_id;
  const result = lane.result_ref ?? lane.evidence_ref ?? undefined;
  const update: SubtaskUpdate = {
    id: lane.task_id,
    threadId: lane.thread_id,
    runId: lane.run_id,
    ...(lane.round_id ? { roundId: lane.round_id } : {}),
    status,
    subagent_type: lane.role ?? "task",
    description,
    prompt: description,
    actionResultStatus: lane.status,
    notify: true,
  };
  const refs: Record<string, unknown> = {};
  for (const key of [
    "result_ref",
    "evidence_ref",
    "evidence_refs",
    "artifact_refs",
    "output_refs",
    "handoff",
  ] as const) {
    const value = lane[key];
    if (value !== undefined && value !== null) {
      refs[key] = value;
    }
  }
  update.metadata = {
    ...(lane.metadata ?? {}),
    ...(Object.keys(refs).length > 0 ? { refs } : {}),
  };
  update.details = {
    ...(lane.details ?? {}),
    ...(Object.keys(refs).length > 0 ? { refs } : {}),
  };
  update.startedAt =
    taskEventStartedAt(lane.started_at) ?? taskEventStartedAt(lane.created_at);
  if (status !== "in_progress") {
    update.finishedAt =
      taskEventTimestamp(lane.finished_at) ??
      taskEventTimestamp(lane.completed_at) ??
      taskEventTimestamp(lane.updated_at);
  }
  if (status === "completed" && result) {
    update.result = result;
  }
  if (status === "failed") {
    update.error = lane.error ?? lane.status;
    update.terminalReason = lane.status;
  }
  return update;
}

function actionResultString(event: PersistedTaskEvent, field: string) {
  if (
    typeof event.action_result !== "object" ||
    event.action_result === null ||
    !(field in event.action_result)
  ) {
    return undefined;
  }
  return stringValue((event.action_result as Record<string, unknown>)[field]);
}

function isRedactedTaskEvent(event: PersistedTaskEvent) {
  return event.redacted === true;
}

function applyActionResultMetadata(
  event: PersistedTaskEvent,
  update: SubtaskUpdate,
) {
  const status = actionResultString(event, "status");
  const terminalReason = actionResultString(event, "terminal_reason");
  if (status) {
    update.actionResultStatus = status;
  }
  if (terminalReason) {
    update.terminalReason = terminalReason;
  }
}

function taskEventTimestamp(value?: unknown) {
  const stringTimestamp = stringValue(value);
  if (!stringTimestamp) {
    return undefined;
  }
  const time = Date.parse(stringTimestamp);
  return Number.isFinite(time) ? time : undefined;
}

function taskEventStartedAt(value?: unknown) {
  return taskEventTimestamp(value);
}

function taskEventFinishedAt(event: PersistedTaskEvent) {
  return (
    taskEventTimestamp(event.finished_at) ??
    taskEventTimestamp(event.completed_at) ??
    taskEventTimestamp(event.updated_at)
  );
}

export function isTaskEventRunMessage(message: RunMessage) {
  return (
    message.metadata?.caller === TASK_EVENT_CALLER ||
    asTaskEvent(message.content) !== null
  );
}

export function taskEventRunMessageKey(message: RunMessage) {
  if (!isTaskEventRunMessage(message)) {
    return null;
  }
  if (typeof message.seq === "number") {
    return `${message.run_id}:${message.seq}`;
  }
  const taskEvent = asTaskEvent(message.content);
  const taskId = stringValue(taskEvent?.task_id);
  const eventType = taskEventType(taskEvent);
  const eventRunId = stringValue(taskEvent?.run_id) ?? message.run_id;
  const eventThreadId = stringValue(taskEvent?.thread_id) ?? "";
  if (!taskId || !eventType || !eventRunId || !message.created_at) {
    return null;
  }
  return `${eventRunId}:${eventThreadId}:${taskId}:${eventType}:${message.created_at}`;
}

export function isTaskEventRunMessageForRequest(
  message: RunMessage,
  fallbackThreadId?: string | null,
) {
  const taskEvent = asTaskEvent(message.content);
  const eventRunId = stringValue(taskEvent?.run_id);
  const eventThreadId = stringValue(taskEvent?.thread_id);
  if (!taskEvent || !eventRunId || eventRunId !== message.run_id) {
    return false;
  }
  return !fallbackThreadId || eventThreadId === fallbackThreadId;
}

export function applyTaskEventToSubtask(
  event: unknown,
  updateSubtask: TaskEventUpdateSubtask,
  fallbackThreadId?: string | null,
  startedAt?: number,
) {
  const taskEvent = asTaskEvent(event);
  const taskId = stringValue(taskEvent?.task_id);
  if (!taskEvent || !taskId) {
    return false;
  }
  const eventType = taskEventType(taskEvent);
  if (!eventType) {
    return false;
  }
  const eventStatus = stringValue(taskEvent.status);

  const threadId =
    stringValue(taskEvent.thread_id) ?? fallbackThreadId ?? undefined;
  const runId = stringValue(taskEvent.run_id);
  const base: SubtaskUpdate = { id: taskId, threadId, runId, notify: true };

  if (eventType === "task_started") {
    const update: SubtaskUpdate = { ...base, status: "in_progress" };
    const eventStartedAt =
      taskEventStartedAt(taskEvent.started_at) ??
      taskEventStartedAt(taskEvent.created_at) ??
      startedAt;
    if (eventStartedAt !== undefined) {
      update.startedAt = eventStartedAt;
    }
    const description =
      stringValue(taskEvent.description) ?? stringValue(taskEvent.summary);
    if (description) {
      update.description = description;
    }
    const subagentType = stringValue(taskEvent.subagent_type);
    if (subagentType) {
      update.subagent_type = subagentType;
    }
    const prompt = stringValue(taskEvent.prompt);
    if (prompt) {
      update.prompt = prompt;
    }
    updateSubtask(update);
    return true;
  }

  if (eventType === "task_running" || eventStatus === "in_progress") {
    const eventStartedAt =
      taskEventStartedAt(taskEvent.started_at) ??
      taskEventStartedAt(taskEvent.created_at) ??
      startedAt;
    updateSubtask({
      ...base,
      status: "in_progress",
      ...(eventStartedAt !== undefined ? { startedAt: eventStartedAt } : {}),
    });
    return true;
  }

  if (eventType === "task_completed" || eventStatus === "completed") {
    const update: SubtaskUpdate = { ...base, status: "completed" };
    const eventStartedAt = taskEventStartedAt(taskEvent.started_at);
    const eventFinishedAt = taskEventFinishedAt(taskEvent) ?? startedAt;
    if (eventStartedAt !== undefined) {
      update.startedAt = eventStartedAt;
    }
    if (eventFinishedAt !== undefined) {
      update.finishedAt = eventFinishedAt;
    }
    applyActionResultMetadata(taskEvent, update);
    const result = isRedactedTaskEvent(taskEvent)
      ? stringValue(taskEvent.result_preview)
      : (stringValue(taskEvent.result_preview) ??
        stringValue(taskEvent.result) ??
        actionResultString(taskEvent, "summary"));
    if (result) {
      update.result = result;
    }
    updateSubtask(update);
    return true;
  }

  const update: SubtaskUpdate = { ...base, status: "failed" };
  const eventStartedAt = taskEventStartedAt(taskEvent.started_at);
  const eventFinishedAt = taskEventFinishedAt(taskEvent) ?? startedAt;
  if (eventStartedAt !== undefined) {
    update.startedAt = eventStartedAt;
  }
  if (eventFinishedAt !== undefined) {
    update.finishedAt = eventFinishedAt;
  }
  applyActionResultMetadata(taskEvent, update);
  const error = isRedactedTaskEvent(taskEvent)
    ? stringValue(taskEvent.error_preview)
    : (stringValue(taskEvent.error_preview) ??
      stringValue(taskEvent.error) ??
      actionResultString(taskEvent, "error") ??
      actionResultString(taskEvent, "terminal_reason") ??
      (TASK_EVENT_TYPES.has(eventType)
        ? undefined
        : `Unknown task event terminal status: ${eventStatus ?? eventType}`));
  if (error) {
    update.error = error;
  }
  updateSubtask(update);
  return true;
}

export function applyTaskEventRunMessages(
  messages: RunMessage[],
  updateSubtask: TaskEventUpdateSubtask,
  fallbackThreadId?: string | null,
  appliedEventKeys?: Set<string>,
) {
  for (const message of messages) {
    if (!isTaskEventRunMessageForRequest(message, fallbackThreadId)) {
      continue;
    }
    const eventKey = taskEventRunMessageKey(message);
    if (eventKey && appliedEventKeys?.has(eventKey)) {
      continue;
    }
    const applied = applyTaskEventToSubtask(
      message.content,
      updateSubtask,
      fallbackThreadId,
      taskEventStartedAt(message.created_at),
    );
    if (applied && eventKey) {
      appliedEventKeys?.add(eventKey);
    }
  }
}

export function isVisibleHistoryRunMessage(
  message: RunMessage,
): message is VisibleRunMessage {
  if (!isMessageContent(message.content)) {
    return false;
  }
  if (typeof message.display?.visible_in_chat === "boolean") {
    return message.display.visible_in_chat;
  }
  const caller =
    typeof message.metadata?.caller === "string" ? message.metadata.caller : "";
  return (
    (message.content.type === "human" || message.content.type === "ai") &&
    !isTaskEventRunMessage(message) &&
    !caller.startsWith("middleware:") &&
    !caller.startsWith("subagent:") &&
    !isHiddenFromUIMessage(message.content)
  );
}

export function buildVisibleHistoryMessages(
  messageRows: RunMessage[],
  supersededRunIds: ReadonlySet<string>,
  appendedMessages: Message[],
  runs: Run[] = [],
) {
  const visibleRowsByRunId = new Map<string, VisibleRunMessage[]>();
  for (const message of messageRows) {
    if (
      supersededRunIds.has(message.run_id) ||
      !isVisibleHistoryRunMessage(message)
    ) {
      continue;
    }
    const rows = visibleRowsByRunId.get(message.run_id) ?? [];
    rows.push(message);
    visibleRowsByRunId.set(message.run_id, rows);
  }
  const runIds = new Set([...runs].reverse().map((run) => run.run_id));
  for (const message of messageRows) {
    runIds.add(message.run_id);
  }
  const visibleRows = [...runIds].flatMap((runId) => {
    const rows = visibleRowsByRunId.get(runId) ?? [];
    // seq is run-local; never use it to order rows across different runs.
    return [...rows].sort((a, b) => {
      if (typeof a.seq !== "number" || typeof b.seq !== "number") {
        return 0;
      }
      return a.seq - b.seq;
    });
  });
  return dedupeMessagesByIdentity([
    ...visibleRows.map(historyMessageFromRunMessage),
    ...appendedMessages,
  ]);
}

export function findLatestUnloadedRunIndex(
  runs: Run[],
  loadedRunIds: ReadonlySet<string>,
): number {
  for (let i = 0; i < runs.length; i++) {
    const run = runs[i];
    if (run && !loadedRunIds.has(run.run_id)) {
      return i;
    }
  }
  return -1;
}

const ACTIVE_RUN_REVALIDATION_STATUSES = new Set([
  "pending",
  "running",
  "cancelling",
  "rolling_back",
]);
const TERMINAL_RUN_REVALIDATION_STATUSES = new Set([
  "success",
  "error",
  "timeout",
  "interrupted",
  "cancelled",
  "timed_out",
  "boundary_stopped",
  "worker_lost",
  "rolled_back",
  "rollback_failed",
]);

export function isActiveRunStatus(status: unknown) {
  return (
    typeof status === "string" && ACTIVE_RUN_REVALIDATION_STATUSES.has(status)
  );
}

export function isTerminalRunStatus(status: unknown) {
  return (
    typeof status === "string" && TERMINAL_RUN_REVALIDATION_STATUSES.has(status)
  );
}

export function getTerminalTransitionRunIds(
  previousActiveRunIds: ReadonlySet<string>,
  runs: Run[],
) {
  return runs
    .filter(
      (run) =>
        previousActiveRunIds.has(run.run_id) && isTerminalRunStatus(run.status),
    )
    .map((run) => run.run_id);
}

type RunWithTerminalFields = Run & {
  error?: unknown;
  terminal_reason?: unknown;
};

export function getLatestRunTerminalNotice(
  runs: Run[] | undefined,
  messageRows: RunMessage[],
): ThreadRunTerminalNotice | null {
  const latestRun = runs?.[0] as RunWithTerminalFields | undefined;
  const runId = typeof latestRun?.run_id === "string" ? latestRun.run_id : null;
  const status = stringValue(latestRun?.status);
  if (!runId || !status || !isTerminalRunStatus(status)) {
    return null;
  }
  const hasVisibleAiForRun = messageRows.some(
    (message) =>
      message.run_id === runId &&
      isVisibleHistoryRunMessage(message) &&
      message.content.type === "ai",
  );
  if (hasVisibleAiForRun) {
    return null;
  }
  return {
    runId,
    status,
    terminalReason: stringValue(latestRun?.terminal_reason),
    error: stringValue(latestRun?.error),
  };
}

function settleTerminalRunSubtasksForThread(
  settleRunSubtasks: SettleRunSubtasks,
  threadId: string,
  run: Run,
) {
  const runWithTerminal = run as RunWithTerminalFields;
  const status = stringValue(runWithTerminal.status);
  if (!status || !isTerminalRunStatus(status)) {
    return;
  }
  settleRunSubtasks({
    threadId,
    runId: run.run_id,
    status,
    terminalReason: stringValue(runWithTerminal.terminal_reason) ?? status,
  });
}

export const MAX_CONSECUTIVE_EMPTY_RUN_LOADS = 5;

export function shouldAutoContinueOnEmptyRun(
  fetchedMessageCount: number,
  consecutiveEmptyLoads: number,
  maxConsecutiveEmptyLoads: number = MAX_CONSECUTIVE_EMPTY_RUN_LOADS,
): boolean {
  return (
    fetchedMessageCount === 0 &&
    consecutiveEmptyLoads < maxConsecutiveEmptyLoads
  );
}

export function shouldAutoContinueRunHistory({
  hasMoreUnloadedRuns,
  visibleMessageCount,
  consecutiveEmptyLoads,
}: {
  hasMoreUnloadedRuns: boolean;
  visibleMessageCount: number;
  consecutiveEmptyLoads: number;
}): boolean {
  if (!hasMoreUnloadedRuns) {
    return false;
  }
  if (visibleMessageCount > 0) {
    return true;
  }
  return shouldAutoContinueOnEmptyRun(
    visibleMessageCount,
    consecutiveEmptyLoads,
  );
}

export function shouldAutoLoadLatestRun(
  latestRunId: string | null | undefined,
  autoLoadedLatestRunId: string | null | undefined,
) {
  return Boolean(latestRunId) && latestRunId !== autoLoadedLatestRunId;
}

type RunMessagesPageResponse = {
  data: RunMessage[];
  has_more?: boolean;
  hasMore?: boolean;
};

export type TaskLaneSnapshot = {
  thread_id: string;
  run_id: string;
  round_id?: string | null;
  task_id: string;
  role?: string | null;
  status: string;
  result_ref?: string | null;
  evidence_ref?: string | null;
  evidence_refs?: unknown;
  artifact_refs?: unknown;
  output_refs?: unknown;
  handoff?: unknown;
  metadata?: Record<string, unknown> | null;
  details?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string;
  started_at?: string;
  updated_at?: string;
  completed_at?: string;
  finished_at?: string;
};

export type RuntimeRoundSnapshot = {
  round_id: string;
  thread_id: string;
  state: string;
  current_run_id?: string | null;
  created_at?: string;
  updated_at?: string;
  closed_at?: string | null;
};

type RuntimeSnapshotRecovery = {
  stale_inflight?: {
    recovered?: boolean;
    recovered_count?: number;
    run_ids?: string[];
    terminal_reason?: string | null;
    runs?: Array<{ run_id: string; terminal_reason?: string | null }>;
  } | null;
  snapshot_self_heal?: {
    repaired?: boolean;
    round_count?: number;
    task_lane_count?: number;
    rounds?: Array<{ run_id: string; round_id: string; state: string }>;
    task_lanes?: Array<{
      run_id: string;
      round_id: string;
      task_id: string;
      status: string;
    }>;
  } | null;
};

export type ThreadRunTerminalNotice = {
  runId: string;
  status: string;
  terminalReason?: string;
  error?: string;
};

type ThreadRuntimeSnapshotResponse = {
  thread_id: string;
  runs: Run[];
  rounds?: RuntimeRoundSnapshot[];
  run_messages: Array<RunMessagesPageResponse & { run_id: string }>;
  task_lanes?: TaskLaneSnapshot[];
  recovery?: RuntimeSnapshotRecovery | null;
};

type RuntimeSnapshotRunMessagesPage =
  ThreadRuntimeSnapshotResponse["run_messages"][number];

export function threadRuntimeSnapshotQueryKey(threadId?: string | null) {
  return ["thread", threadId, "runtime-snapshot"] as const;
}

export function buildThreadRuntimeSnapshotUrl(
  baseUrl: string,
  threadId: string,
) {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");
  const path = `/api/threads/${encodeURIComponent(threadId)}/runtime-snapshot`;
  const url = new URL(
    `${normalizedBaseUrl}${path}`,
    typeof window !== "undefined" ? window.location.origin : "http://localhost",
  );
  return normalizedBaseUrl ? url.toString() : url.pathname;
}

export async function readThreadRuntimeSnapshotResponse(
  response: Response,
): Promise<ThreadRuntimeSnapshotResponse> {
  const fallback = "Failed to load thread runtime snapshot.";
  if (!response.ok) {
    const error = new Error(await readResponseErrorMessage(response, fallback));
    Object.defineProperty(error, "status", {
      value: response.status,
      enumerable: true,
    });
    throw error;
  }
  try {
    return (await response.json()) as ThreadRuntimeSnapshotResponse;
  } catch {
    throw new Error(fallback);
  }
}

export function runMessagesPageHasMore(result: RunMessagesPageResponse) {
  return result.has_more ?? result.hasMore ?? false;
}

export function getOldestRunMessageSeq(messages: RunMessage[]) {
  let oldestSeq: number | null = null;
  for (const message of messages) {
    if (typeof message.seq !== "number") {
      continue;
    }
    oldestSeq =
      oldestSeq === null ? message.seq : Math.min(oldestSeq, message.seq);
  }
  return oldestSeq;
}

export function getNextRunMessagesBeforeSeq(
  result: RunMessagesPageResponse,
): number | null | undefined {
  if (!runMessagesPageHasMore(result)) {
    return null;
  }
  return getOldestRunMessageSeq(result.data) ?? undefined;
}

export function applySnapshotRunMessagePageState(
  pages: RuntimeSnapshotRunMessagesPage[],
  loadedRunIds: Set<string>,
  runBeforeSeq: Map<string, number>,
) {
  for (const page of pages) {
    const nextBeforeSeq = getNextRunMessagesBeforeSeq(page);
    if (typeof nextBeforeSeq === "number") {
      runBeforeSeq.set(page.run_id, nextBeforeSeq);
    } else if (nextBeforeSeq === null) {
      loadedRunIds.add(page.run_id);
    }
  }
}

export function buildRunMessagesUrl(
  baseUrl: string,
  threadId: string,
  runId: string,
  beforeSeq?: number,
) {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");
  const path = `/api/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/messages`;
  const url = new URL(
    `${normalizedBaseUrl}${path}`,
    typeof window !== "undefined" ? window.location.origin : "http://localhost",
  );
  if (beforeSeq !== undefined) {
    url.searchParams.set("before_seq", String(beforeSeq));
  }
  return normalizedBaseUrl ? url.toString() : `${url.pathname}${url.search}`;
}

export async function readRunMessagesPageResponse(
  response: Response,
): Promise<RunMessagesPageResponse> {
  const fallback = "Failed to load thread history.";
  if (!response.ok) {
    const error = new Error(await readResponseErrorMessage(response, fallback));
    Object.defineProperty(error, "status", {
      value: response.status,
      enumerable: true,
    });
    throw error;
  }
  try {
    return (await response.json()) as RunMessagesPageResponse;
  } catch {
    throw new Error(response.statusText || fallback);
  }
}

export function isAbortError(error: unknown) {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

export function mergeMessages(
  historyMessages: Message[],
  threadMessages: Message[],
  optimisticMessages: Message[],
): Message[] {
  // Only visible live messages should trim overlapping history. Hidden messages
  // are UI control messages in this path, not observability records; any hidden
  // message that must survive as task/tracing data should use custom events or a
  // separate state channel instead of participating in this overlap heuristic.

  const savedTurnDurations = new Map<string, number>();
  const savedHistoryCreatedAt = new Map<string, unknown>();
  for (const msg of historyMessages) {
    const identity = messageIdentity(msg);
    if (identity && msg.additional_kwargs?.turn_duration !== undefined) {
      savedTurnDurations.set(
        identity,
        msg.additional_kwargs.turn_duration as number,
      );
    }
    if (
      identity &&
      msg.additional_kwargs?.[HISTORY_CREATED_AT_KEY] !== undefined
    ) {
      savedHistoryCreatedAt.set(
        identity,
        msg.additional_kwargs[HISTORY_CREATED_AT_KEY],
      );
    }
  }

  const visibleThreadMessages = threadMessages.filter(
    (message) => !isHiddenFromUIMessage(message),
  );
  const hasVisibleThreadHuman = visibleThreadMessages.some(
    (message) => message.type === "human",
  );
  const optimisticMessagesBeforeThread =
    !hasVisibleThreadHuman &&
    optimisticMessages.some((message) => message.type === "human")
      ? optimisticMessages
      : [];
  const optimisticMessagesAfterThread =
    optimisticMessagesBeforeThread.length > 0 ? [] : optimisticMessages;
  const allowUnscopedLiveOverlap = visibleThreadMessages[0]?.type === "human";
  const liveRunScopedMessageIds = new Set(
    visibleThreadMessages
      .flatMap((message) =>
        liveOverlapMessageIdentities(message, allowUnscopedLiveOverlap),
      )
      .filter(isNonEmptyString),
  );
  const liveMessageByRunScopedIdentity = new Map<string, Message>();
  for (const message of visibleThreadMessages) {
    for (const identity of liveOverlapMessageIdentities(
      message,
      allowUnscopedLiveOverlap,
    )) {
      liveMessageByRunScopedIdentity.set(identity, message);
    }
  }

  // The overlap is a contiguous suffix of historyMessages (newest history == oldest thread).
  // Scan from the end: shrink cutoff while messages are already in thread, stop as soon as
  // we hit one that isn't — everything before that point is non-overlapping.
  let cutoff = historyMessages.length;
  for (let i = historyMessages.length - 1; i >= 0; i--) {
    const msg = historyMessages[i];
    if (!msg) {
      continue;
    }
    const identities = liveOverlapMessageIdentities(
      msg,
      allowUnscopedLiveOverlap,
    );
    if (identities.some((identity) => liveRunScopedMessageIds.has(identity))) {
      cutoff = i;
    } else {
      break;
    }
  }

  const consumedLiveMessages = new Set<Message>();
  const historyWithRunScopedLiveReplacements = historyMessages
    .slice(0, cutoff)
    .map((message) => {
      const liveMessage = liveOverlapMessageIdentities(
        message,
        allowUnscopedLiveOverlap,
      )
        .map((identity) => liveMessageByRunScopedIdentity.get(identity))
        .find(Boolean);
      if (!liveMessage) {
        return message;
      }
      consumedLiveMessages.add(liveMessage);
      return {
        ...liveMessage,
        additional_kwargs: {
          ...liveMessage.additional_kwargs,
          ...(message.additional_kwargs?.turn_duration !== undefined &&
          liveMessage.additional_kwargs?.turn_duration === undefined
            ? { turn_duration: message.additional_kwargs.turn_duration }
            : {}),
          ...(message.additional_kwargs?.[HISTORY_CREATED_AT_KEY] !==
            undefined &&
          liveMessage.additional_kwargs?.[HISTORY_CREATED_AT_KEY] === undefined
            ? {
                [HISTORY_CREATED_AT_KEY]:
                  message.additional_kwargs[HISTORY_CREATED_AT_KEY],
              }
            : {}),
        },
      } as Message;
    });

  const merged = dedupeMessagesByIdentity([
    ...historyWithRunScopedLiveReplacements,
    ...optimisticMessagesBeforeThread,
    ...threadMessages.filter((message) => !consumedLiveMessages.has(message)),
    ...optimisticMessagesAfterThread,
  ]);

  return merged.map((message) => {
    const identity = messageIdentity(message);
    const missingTurnDuration =
      identity &&
      savedTurnDurations.has(identity) &&
      message.additional_kwargs?.turn_duration === undefined;
    const missingHistoryCreatedAt =
      identity &&
      savedHistoryCreatedAt.has(identity) &&
      message.additional_kwargs?.[HISTORY_CREATED_AT_KEY] === undefined;
    if (identity && (missingTurnDuration || missingHistoryCreatedAt)) {
      return {
        ...message,
        additional_kwargs: {
          ...message.additional_kwargs,
          ...(missingTurnDuration
            ? { turn_duration: savedTurnDurations.get(identity) }
            : {}),
          ...(missingHistoryCreatedAt
            ? {
                [HISTORY_CREATED_AT_KEY]: savedHistoryCreatedAt.get(identity),
              }
            : {}),
        },
      } as Message;
    }
    return message;
  });
}

function getMessagesAfterBaseline(
  messages: Message[],
  baselineMessageIds: ReadonlySet<string>,
): Message[] {
  return messages.filter((message) => {
    const id = messageIdentity(message);
    return !id || !baselineMessageIds.has(id);
  });
}

function isOptimisticUploadStatusMessage(message: Message): boolean {
  const additionalKwargs = message.additional_kwargs as
    | Record<string, unknown>
    | undefined;
  return (
    message.type === "ai" &&
    additionalKwargs?.element === "task" &&
    additionalKwargs?.upload_status === "uploading"
  );
}

export function completeOptimisticUploadMessages(
  optimisticMessages: Message[],
  uploadedFiles: FileInMessage[],
): Message[] {
  return optimisticMessages
    .map((message) => {
      if (message.type !== "human") {
        return message;
      }
      return {
        ...message,
        additional_kwargs: {
          ...(message.additional_kwargs ?? {}),
          files: uploadedFiles,
        },
      } as Message;
    })
    .filter((message) => !isOptimisticUploadStatusMessage(message));
}

export function getVisibleOptimisticMessages(
  optimisticMessages: Message[],
  previousHumanMessageCount: number,
  currentHumanMessageCount: number,
): Message[] {
  if (
    optimisticMessages.some((message) => message.type === "human") &&
    currentHumanMessageCount > previousHumanMessageCount
  ) {
    return [];
  }
  return optimisticMessages;
}

export function getSummarizationMiddlewareMessages(
  data: unknown,
): Message[] | undefined {
  if (typeof data !== "object" || data === null) {
    return undefined;
  }

  for (const [key, update] of Object.entries(data)) {
    if (!SUMMARIZATION_MIDDLEWARE_UPDATE_KEYS.has(key)) {
      continue;
    }
    if (typeof update !== "object" || update === null) {
      continue;
    }

    const messages = Reflect.get(update, "messages");
    if (Array.isArray(messages)) {
      return [...messages] as Message[];
    }
  }

  return undefined;
}

export function upsertThreadInSearchCache(
  queryClient: QueryClient,
  thread: AgentThread,
) {
  if (isDeletedThreadTombstoned(thread.thread_id)) {
    return;
  }
  queryClient.setQueriesData(
    {
      queryKey: ["threads", "search"],
      exact: false,
    },
    (oldData: Array<AgentThread> | undefined) => {
      if (!oldData) {
        return [thread];
      }

      const existingIndex = oldData.findIndex(
        (t) => t.thread_id === thread.thread_id,
      );
      if (existingIndex === -1) {
        return [thread, ...oldData];
      }

      return oldData.map((t, index) => {
        if (index !== existingIndex) {
          return t;
        }
        return {
          ...t,
          ...thread,
          metadata: {
            ...(t.metadata ?? {}),
            ...(thread.metadata ?? {}),
          },
          values: {
            ...t.values,
            ...thread.values,
          },
        };
      });
    },
  );
}

export function upsertThreadInInfiniteCache(
  queryClient: QueryClient,
  thread: AgentThread,
) {
  if (isDeletedThreadTombstoned(thread.thread_id)) {
    return;
  }
  queryClient.setQueriesData(
    {
      queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      exact: false,
    },
    (oldData: InfiniteData<AgentThread[]> | undefined) => {
      if (!oldData) {
        return oldData;
      }

      const merged = oldData.pages.map((page) =>
        page.map((t) =>
          t.thread_id === thread.thread_id
            ? {
                ...t,
                ...thread,
                metadata: {
                  ...(t.metadata ?? {}),
                  ...(thread.metadata ?? {}),
                },
                values: {
                  ...t.values,
                  ...thread.values,
                },
              }
            : t,
        ),
      );

      const exists = merged.some((page) =>
        page.some((t) => t.thread_id === thread.thread_id),
      );
      if (exists) {
        return { ...oldData, pages: merged };
      }

      const firstPage = merged[0] ?? [];
      const restPages = merged.slice(1);
      return {
        ...oldData,
        pages: [[thread, ...firstPage], ...restPages],
      };
    },
  );
}

export function markThreadBusyInCaches(
  queryClient: QueryClient,
  threadId: string,
) {
  if (isDeletedThreadTombstoned(threadId)) {
    return;
  }
  threadActivitySnapshot = {
    running: new Set([...threadActivitySnapshot.running, threadId]),
    finished: new Set(
      [...threadActivitySnapshot.finished].filter((id) => id !== threadId),
    ),
  };
  emitThreadActivity();
  queryClient.setQueriesData(
    {
      queryKey: ["threads", "search"],
      exact: false,
    },
    (oldData: Array<AgentThread> | undefined) =>
      oldData?.map((t) =>
        t.thread_id === threadId ? { ...t, status: "busy" } : t,
      ),
  );
  queryClient.setQueriesData(
    {
      queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      exact: false,
    },
    (oldData: InfiniteData<AgentThread[]> | undefined) =>
      mapInfiniteThreadsCache(oldData, (t) =>
        t.thread_id === threadId ? { ...t, status: "busy" } : t,
      ),
  );
}

function setThreadStatusInCaches(
  queryClient: QueryClient,
  threadId: string,
  status: string,
) {
  if (isDeletedThreadTombstoned(threadId)) {
    return;
  }
  const nextStatus = status as AgentThread["status"];
  queryClient.setQueriesData(
    {
      queryKey: ["threads", "search"],
      exact: false,
    },
    (oldData: Array<AgentThread> | undefined) =>
      oldData?.map((t) =>
        t.thread_id === threadId ? { ...t, status: nextStatus } : t,
      ),
  );
  queryClient.setQueriesData(
    {
      queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      exact: false,
    },
    (oldData: InfiniteData<AgentThread[]> | undefined) =>
      mapInfiniteThreadsCache(oldData, (t) =>
        t.thread_id === threadId ? { ...t, status: nextStatus } : t,
      ),
  );
}

export function applyBackgroundRunProbeResult(
  queryClient: QueryClient,
  threadId: string,
  runId: string,
  status: unknown,
  options: TerminalRunSettlementOptions = {},
) {
  if (isDeletedThreadTombstoned(threadId)) {
    stopBackgroundRunProbe(threadId, runId);
    return true;
  }
  if (!isTerminalRunStatus(status)) {
    return false;
  }
  const terminalStatus = String(status);
  const threadStatus = status === "success" ? "idle" : terminalStatus;
  options.settleRunSubtasks?.({
    threadId,
    runId,
    status: terminalStatus,
    terminalReason: options.terminalReason ?? terminalStatus,
  });
  clearReconnectRun(threadId, runId);
  if (status === "success") {
    markThreadFinished(threadId);
  } else {
    clearThreadActivity(threadId);
  }
  setThreadStatusInCaches(queryClient, threadId, threadStatus);
  invalidateTerminalRunQueries(queryClient, threadId);
  return true;
}

export function applyStreamErrorRecovery({
  queryClient,
  threadId,
  runId,
  isMock,
  settleRunSubtasks,
}: {
  queryClient: QueryClient;
  threadId: string | null | undefined;
  runId: string | null | undefined;
  isMock?: boolean;
  settleRunSubtasks?: SettleRunSubtasks;
}): StreamErrorRecoveryRun | null {
  if (!threadId || !runId) {
    if (threadId) {
      clearThreadActivity(threadId);
    }
    return null;
  }
  markThreadBusyInCaches(queryClient, threadId);
  startBackgroundRunProbe({
    queryClient,
    threadId,
    runId,
    isMock,
    settleRunSubtasks,
  });
  void queryClient.invalidateQueries({
    queryKey: threadRunsQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadRuntimeSnapshotQueryKey(threadId),
  });
  return { threadId, runId };
}

export function shouldCommitStreamStartFromError({
  started,
  threadId,
  runId,
}: {
  started: boolean;
  threadId: string | null | undefined;
  runId: string | null | undefined;
}) {
  return !started && Boolean(threadId && runId);
}

export function isThreadRecoveringFromStreamError(
  recoveryRun: StreamErrorRecoveryRun | null,
  threadId: string | null | undefined,
) {
  return Boolean(threadId && recoveryRun?.threadId === threadId);
}

export function isSameStreamErrorRecoveryRun(
  recoveryRun: StreamErrorRecoveryRun | null,
  threadId: string | null | undefined,
  runId: string | null | undefined,
) {
  return Boolean(
    recoveryRun &&
    threadId &&
    runId &&
    recoveryRun.threadId === threadId &&
    recoveryRun.runId === runId,
  );
}

export function hasTerminalStreamErrorRecoveryRun(
  recoveryRun: StreamErrorRecoveryRun | null,
  runs: Run[] | undefined,
) {
  return Boolean(
    recoveryRun &&
    runs?.some(
      (run) =>
        run.run_id === recoveryRun.runId && isTerminalRunStatus(run.status),
    ),
  );
}

export function getVisibleThreadError<T>(
  error: T,
  isStreamErrorRecovering: boolean,
): T | undefined {
  return isStreamErrorRecovering ? undefined : error;
}

export function shouldShowStreamErrorToast(
  recoveryRun: StreamErrorRecoveryRun | null,
) {
  return recoveryRun === null;
}

export function shouldRefreshRunHistoryForThread(
  requestThreadId: string | null | undefined,
  currentThreadId: string | null | undefined,
) {
  return !requestThreadId || requestThreadId === currentThreadId;
}

export function resolveThreadStreamFinishMeta({
  run,
  streamOwner,
}: {
  run?: { thread_id?: string | null; run_id?: string | null } | null;
  streamOwner?: ThreadStreamOwnerSnapshot | null;
}): ThreadStreamFinishMeta {
  const hasRunMetadata = Boolean(run?.thread_id ?? run?.run_id);
  return {
    threadId: hasRunMetadata
      ? (run?.thread_id ?? null)
      : (streamOwner?.threadId ?? null),
    runId: hasRunMetadata
      ? (run?.run_id ?? null)
      : (streamOwner?.runId ?? null),
  };
}

export function shouldTreatTerminalEventAsCurrentStream(
  eventThreadId: string | null | undefined,
  eventRunId: string | null | undefined,
  streamThreadId: string | null | undefined,
  streamRunId: string | null | undefined,
) {
  return Boolean(
    eventThreadId &&
    eventRunId &&
    streamThreadId &&
    streamRunId &&
    eventThreadId === streamThreadId &&
    eventRunId === streamRunId,
  );
}

export function shouldTreatStreamFinishAsCurrentStream(
  finishThreadId: string | null | undefined,
  finishRunId: string | null | undefined,
  streamThreadId: string | null | undefined,
  streamRunId: string | null | undefined,
) {
  return Boolean(
    finishThreadId &&
    streamThreadId &&
    finishThreadId === streamThreadId &&
    (!finishRunId || !streamRunId || finishRunId === streamRunId),
  );
}

export function shouldRunCurrentStreamFinishSideEffects({
  eventThreadId,
  eventRunId,
  streamThreadId,
  streamRunId,
}: StreamOwnershipState) {
  return shouldTreatStreamFinishAsCurrentStream(
    eventThreadId,
    eventRunId,
    streamThreadId,
    streamRunId,
  );
}

export function shouldApplyStreamTitleUpdate({
  eventThreadId,
  eventRunId,
  streamThreadId,
  streamRunId,
  viewThreadId,
  liveMessagesThreadId,
  optimisticThreadId,
}: StreamOwnershipState) {
  if (!streamThreadId) {
    return false;
  }
  if (eventThreadId && eventThreadId !== streamThreadId) {
    return false;
  }
  if (eventRunId && streamRunId && eventRunId !== streamRunId) {
    return false;
  }
  if (eventThreadId || eventRunId) {
    return Boolean(eventThreadId && eventThreadId === streamThreadId);
  }
  return Boolean(
    viewThreadId &&
    (viewThreadId === streamThreadId ||
      liveMessagesThreadId === viewThreadId ||
      optimisticThreadId === viewThreadId),
  );
}

export type RefreshRunsParams = {
  threadId?: string | null;
  runIds?: Iterable<string>;
};

type RefreshRuns = (params?: RefreshRunsParams) => void;

function isLegacyRefreshRunsParams(value: unknown): value is Iterable<string> {
  return (
    typeof value === "object" &&
    value !== null &&
    Symbol.iterator in value &&
    !("threadId" in value) &&
    !("runIds" in value)
  );
}

export function reconcileTaskEventRunHistory(
  queryClient: QueryClient,
  event: unknown,
  refreshRuns: RefreshRuns,
) {
  const taskEvent = asTaskEvent(event);
  const eventType = taskEventType(taskEvent);
  if (
    !taskEvent ||
    (eventType !== "task_completed" &&
      eventType !== "task_failed" &&
      eventType !== "task_cancelled" &&
      eventType !== "task_timed_out")
  ) {
    return false;
  }
  const threadId = stringValue(taskEvent.thread_id);
  const runId = stringValue(taskEvent.run_id);
  if (!threadId || !runId) {
    return false;
  }
  void queryClient.invalidateQueries({
    queryKey: threadRunsQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadRuntimeSnapshotQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadTokenUsageQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadContextUsageQueryKey(threadId),
  });
  refreshRuns({ threadId, runIds: [runId] });
  return true;
}

export function reconcileTerminalRunHistory(
  queryClient: QueryClient,
  event: unknown,
  refreshRuns: RefreshRuns,
  settleRunSubtasks?: SettleRunSubtasks,
) {
  const runTerminalEvent = asRunTerminalEvent(event);
  if (!runTerminalEvent) {
    return false;
  }
  stopBackgroundRunProbe(runTerminalEvent.thread_id, runTerminalEvent.run_id);
  if (isDeletedThreadTombstoned(runTerminalEvent.thread_id)) {
    return true;
  }
  if (
    !applyBackgroundRunProbeResult(
      queryClient,
      runTerminalEvent.thread_id,
      runTerminalEvent.run_id,
      runTerminalEvent.status,
      {
        settleRunSubtasks,
        terminalReason: runTerminalEvent.terminal_reason,
      },
    )
  ) {
    invalidateTerminalRunQueries(queryClient, runTerminalEvent.thread_id);
  }
  refreshRuns({
    threadId: runTerminalEvent.thread_id,
    runIds: [runTerminalEvent.run_id],
  });
  return true;
}

export function invalidateTerminalRunQueries(
  queryClient: QueryClient,
  threadId: string,
) {
  if (isDeletedThreadTombstoned(threadId)) {
    return;
  }
  void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
  void queryClient.invalidateQueries({
    queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
  });
  void queryClient.invalidateQueries({
    queryKey: threadRunsQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadRuntimeSnapshotQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadTokenUsageQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadContextUsageQueryKey(threadId),
  });
}

function backgroundRunProbeKey(threadId: string, runId: string) {
  return `${threadId}:${runId}`;
}

function getErrorStatusCode(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null) {
    return undefined;
  }
  const status =
    Reflect.get(error, "status") ?? Reflect.get(error, "statusCode");
  return typeof status === "number" ? status : undefined;
}

export function shouldStopBackgroundRunProbe(
  attempt: number,
  error?: unknown,
): boolean {
  const status = getErrorStatusCode(error);
  return (
    attempt >= BACKGROUND_RUN_PROBE_MAX_ATTEMPTS ||
    (status !== undefined && BACKGROUND_RUN_PROBE_STOP_STATUS_CODES.has(status))
  );
}

export function getBackgroundRunProbeDelay(attempt: number): number {
  return Math.min(
    BACKGROUND_RUN_PROBE_DELAY_MS * 2 ** Math.max(0, attempt - 1),
    BACKGROUND_RUN_PROBE_MAX_DELAY_MS,
  );
}

export function stopBackgroundRunProbeRecovery(
  queryClient: QueryClient,
  threadId: string,
) {
  clearThreadActivity(threadId);
  void queryClient.invalidateQueries({
    queryKey: threadRunsQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadRuntimeSnapshotQueryKey(threadId),
  });
  void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
  void queryClient.invalidateQueries({
    queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
  });
}

export function shouldKeepStreamErrorRecoveryRun(
  recoveryRun: StreamErrorRecoveryRun | null,
  activity: ThreadActivitySnapshot,
) {
  return Boolean(recoveryRun && activity.running.has(recoveryRun.threadId));
}

export function stopBackgroundRunProbe(
  threadId: string | null | undefined,
  runId: string | null | undefined,
) {
  if (!threadId || !runId) {
    return;
  }
  const key = backgroundRunProbeKey(threadId, runId);
  const timer = backgroundRunProbeTimers.get(key);
  if (timer !== undefined && typeof window !== "undefined") {
    window.clearTimeout(timer);
  }
  backgroundRunProbeTimers.delete(key);
  backgroundRunProbeAttempts.delete(key);
}

export function stopBackgroundRunProbesForThread(threadId: string) {
  const prefix = `${threadId}:`;
  for (const [key, timer] of backgroundRunProbeTimers) {
    if (!key.startsWith(prefix)) {
      continue;
    }
    if (typeof window !== "undefined") {
      window.clearTimeout(timer);
    }
    backgroundRunProbeTimers.delete(key);
  }
  for (const key of backgroundRunProbeAttempts.keys()) {
    if (key.startsWith(prefix)) {
      backgroundRunProbeAttempts.delete(key);
    }
  }
}

export function startBackgroundRunProbe({
  queryClient,
  threadId,
  runId,
  isMock,
  settleRunSubtasks,
}: {
  queryClient: QueryClient;
  threadId: string;
  runId: string;
  isMock?: boolean;
  settleRunSubtasks?: SettleRunSubtasks;
}) {
  if (typeof window === "undefined" || isMock) {
    return;
  }
  const key = backgroundRunProbeKey(threadId, runId);
  if (backgroundRunProbeTimers.has(key)) {
    return;
  }
  const attempt = (backgroundRunProbeAttempts.get(key) ?? 0) + 1;
  if (shouldStopBackgroundRunProbe(attempt)) {
    backgroundRunProbeAttempts.delete(key);
    stopBackgroundRunProbeRecovery(queryClient, threadId);
    return;
  }
  backgroundRunProbeAttempts.set(key, attempt);

  const timer = window.setTimeout(() => {
    void (async () => {
      try {
        const run = await getAPIClient().runs.get(threadId, runId);
        if (backgroundRunProbeTimers.get(key) !== timer) {
          return;
        }
        backgroundRunProbeTimers.delete(key);
        if (
          applyBackgroundRunProbeResult(
            queryClient,
            threadId,
            runId,
            run.status,
            {
              terminalReason: stringValue(
                (run as { terminal_reason?: unknown }).terminal_reason,
              ),
              settleRunSubtasks,
            },
          )
        ) {
          backgroundRunProbeAttempts.delete(key);
          return;
        }
      } catch (error) {
        if (backgroundRunProbeTimers.get(key) !== timer) {
          return;
        }
        backgroundRunProbeTimers.delete(key);
        if (shouldStopBackgroundRunProbe(attempt, error)) {
          backgroundRunProbeAttempts.delete(key);
          stopBackgroundRunProbeRecovery(queryClient, threadId);
          return;
        }
      }
      startBackgroundRunProbe({
        queryClient,
        threadId,
        runId,
        isMock,
        settleRunSubtasks,
      });
    })();
  }, getBackgroundRunProbeDelay(attempt));

  backgroundRunProbeTimers.set(key, timer);
}

export function getStreamErrorMessage(error: unknown): string {
  const sanitize = (message: string) =>
    isProviderTransientErrorMessage(message)
      ? PUBLIC_PROVIDER_TRANSIENT_ERROR_MESSAGE
      : message;
  if (typeof error === "string" && error.trim()) {
    return sanitize(error);
  }
  if (error instanceof Error && error.message.trim()) {
    return sanitize(error.message);
  }
  if (typeof error === "object" && error !== null) {
    const message = Reflect.get(error, "message");
    if (typeof message === "string" && message.trim()) {
      return sanitize(message);
    }
    const nestedError = Reflect.get(error, "error");
    if (nestedError instanceof Error && nestedError.message.trim()) {
      return sanitize(nestedError.message);
    }
    if (typeof nestedError === "string" && nestedError.trim()) {
      return sanitize(nestedError);
    }
  }
  return "Request failed.";
}

function isProviderTransientErrorMessage(message: string) {
  const lowered = message.toLowerCase();
  return PROVIDER_TRANSIENT_ERROR_MARKERS.some((marker) =>
    lowered.includes(marker),
  );
}

async function readResponseErrorMessage(
  response: Response,
  fallback = "Request failed.",
) {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string" && data.detail.trim()) {
      return data.detail;
    }
  } catch {
    // Use the fallback below when the response body is not JSON.
  }
  return response.statusText || fallback;
}

function getHttpStatus(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null) {
    return undefined;
  }

  const status = Reflect.get(error, "status");
  if (typeof status === "number") {
    return status;
  }

  const response = Reflect.get(error, "response");
  if (typeof response === "object" && response !== null) {
    const responseStatus = Reflect.get(response, "status");
    if (typeof responseStatus === "number") {
      return responseStatus;
    }
  }

  return undefined;
}

export type ThreadHistoryLoadErrorKind = "forbidden" | "not-found" | "failed";

export function getThreadHistoryLoadErrorKind(
  error: unknown,
): ThreadHistoryLoadErrorKind {
  const status = getHttpStatus(error);
  if (status === 403) {
    return "forbidden";
  }
  if (status === 404) {
    return "not-found";
  }
  return "failed";
}

function isThreadMissingError(error: unknown): boolean {
  const status = getHttpStatus(error);
  // Treat 403 like 404 here to avoid disclosing whether an inaccessible thread
  // exists; callers render an empty state without changing the browser route.
  return status === 403 || status === 404;
}

export function useThreadStream({
  threadId,
  displayThreadId,
  runtimeOwnerId,
  context,
  isMock,
  onSend,
  onStart,
  onFinish,
  onToolEnd,
}: ThreadStreamOptions) {
  const { t } = useI18n();
  const threadActivity = useThreadActivity();
  const currentViewThreadId = displayThreadId ?? threadId ?? null;
  const currentRuntimeOwnerId =
    runtimeOwnerId ?? currentViewThreadId ?? threadId ?? null;
  const currentViewThreadIdRef = useRef(currentViewThreadId);
  const currentRuntimeOwnerIdRef = useRef(currentRuntimeOwnerId);
  currentViewThreadIdRef.current = currentViewThreadId;
  currentRuntimeOwnerIdRef.current = currentRuntimeOwnerId;
  // Optimistic messages shown before the server stream responds.
  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);
  const [optimisticThreadId, setOptimisticThreadId] = useState<string | null>(
    null,
  );
  const optimisticThreadIdRef = useRef(optimisticThreadId);
  const [liveMessagesThreadId, setLiveMessagesThreadId] = useState<
    string | null
  >(null);
  const liveMessagesThreadIdRef = useRef(liveMessagesThreadId);
  optimisticThreadIdRef.current = optimisticThreadId;
  liveMessagesThreadIdRef.current = liveMessagesThreadId;
  const [pendingSupersededRunIds, setPendingSupersededRunIds] = useState<
    ReadonlySet<string>
  >(() => new Set());
  const [pendingSupersededMessageIds, setPendingSupersededMessageIds] =
    useState<ReadonlySet<string>>(() => new Set());
  const [streamErrorRecoveryRun, setStreamErrorRecoveryRunState] =
    useState<StreamErrorRecoveryRun | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [queueReleaseVersion, setQueueReleaseVersion] = useState(0);
  // Track the thread ID that is currently streaming to handle thread changes during streaming
  const [onStreamThreadId, setOnStreamThreadId] = useState(() => threadId);
  const [streamClientThreadId, setStreamClientThreadId] = useState<
    string | null
  >(() => threadId ?? null);
  // Ref to track the stream target across async callbacks without causing re-renders.
  // Do not use the visible route id here; users can switch away while a run
  // continues in the background.
  const streamThreadIdRef = useRef<string | null>(threadId ?? null);
  const streamRunIdRef = useRef<string | null>(null);
  const streamOwnerSnapshotRef = useRef<ThreadStreamOwnerSnapshot | null>(
    threadId ? { threadId, runId: null } : null,
  );
  const streamClientThreadIdLockedRef = useRef(false);
  const liveMessagesSnapshotRef = useRef<LiveMessagesSnapshot | null>(null);
  const streamErrorRecoveryRunRef = useRef<StreamErrorRecoveryRun | null>(null);
  const startedRef = useRef(false);
  const activeSendRequestRef = useRef<SendRequestOwnership | null>(null);
  const pendingUsageBaselineMessageIdsRef = useRef<Set<string>>(new Set());
  const listeners = useRef({
    onSend,
    onStart,
    onFinish,
    onToolEnd,
  });

  const {
    runs: historyRuns,
    messages: history,
    hasMore: hasMoreHistory,
    loadMore: loadMoreHistory,
    loading: isHistoryLoading,
    error: historyError,
    terminalNotice,
    appendMessages,
    refreshRuns: refreshHistoryRuns,
  } = useThreadHistory(onStreamThreadId ?? "", {
    enabled: !isMock,
    pendingSupersededRunIds,
  });

  const setStreamErrorRecoveryRun = useCallback(
    (next: StreamErrorRecoveryRun | null) => {
      streamErrorRecoveryRunRef.current = next;
      setStreamErrorRecoveryRunState(next);
    },
    [],
  );

  // Keep listeners ref updated with latest callbacks
  useEffect(() => {
    listeners.current = { onSend, onStart, onFinish, onToolEnd };
  }, [onSend, onStart, onFinish, onToolEnd]);

  const setOptimisticThreadTarget = useCallback(
    (nextThreadId: string | null) => {
      optimisticThreadIdRef.current = nextThreadId;
      setOptimisticThreadId(nextThreadId);
    },
    [],
  );

  const setLiveMessagesThreadTarget = useCallback(
    (nextThreadId: string | null) => {
      liveMessagesThreadIdRef.current = nextThreadId;
      setLiveMessagesThreadId(nextThreadId);
    },
    [],
  );

  const releaseStreamClientThreadId = useCallback(
    (nextThreadId?: string | null) => {
      streamClientThreadIdLockedRef.current = false;
      setStreamClientThreadId(nextThreadId ?? threadId ?? null);
    },
    [threadId],
  );

  useEffect(() => {
    const normalizedThreadId = threadId ?? null;
    if (!normalizedThreadId) {
      // Reset when the UI moves back to a brand new unsaved thread.
      startedRef.current = false;
      setOnStreamThreadId(normalizedThreadId);
    } else {
      setOnStreamThreadId(normalizedThreadId);
    }
    if (!streamClientThreadIdLockedRef.current) {
      setStreamClientThreadId(normalizedThreadId);
    }
    streamThreadIdRef.current = normalizedThreadId;
    streamOwnerSnapshotRef.current = normalizedThreadId
      ? { threadId: normalizedThreadId, runId: null }
      : null;
  }, [threadId]);

  const handleStreamStart = useCallback(
    (_threadId: string, _runId: string) => {
      streamThreadIdRef.current = _threadId;
      streamRunIdRef.current = _runId;
      streamOwnerSnapshotRef.current = { threadId: _threadId, runId: _runId };
      setStreamErrorRecoveryRun(null);
      const currentView = currentViewThreadIdRef.current;
      const streamStillOwnsVisibleChat =
        currentView === _threadId ||
        optimisticThreadIdRef.current === currentView ||
        liveMessagesThreadIdRef.current === currentView;
      setOptimisticThreadId((currentOptimisticThreadId) => {
        if (
          currentOptimisticThreadId &&
          (currentOptimisticThreadId === currentView ||
            currentOptimisticThreadId === _threadId)
        ) {
          optimisticThreadIdRef.current = _threadId;
          return _threadId;
        }
        optimisticThreadIdRef.current = currentOptimisticThreadId;
        return currentOptimisticThreadId;
      });
      setLiveMessagesThreadId((currentLiveMessagesThreadId) => {
        if (
          currentLiveMessagesThreadId &&
          (currentLiveMessagesThreadId === currentView ||
            currentLiveMessagesThreadId === _threadId)
        ) {
          liveMessagesThreadIdRef.current = _threadId;
          return _threadId;
        }
        liveMessagesThreadIdRef.current = currentLiveMessagesThreadId;
        return currentLiveMessagesThreadId;
      });
      if (!startedRef.current && streamStillOwnsVisibleChat) {
        listeners.current.onStart?.(_threadId, _runId);
        startedRef.current = true;
      }
      setOnStreamThreadId(_threadId);
    },
    [setStreamErrorRecoveryRun],
  );

  const queryClient = useQueryClient();
  const updateSubtask = useUpdateSubtask();
  const settleRunSubtasks = useSettleRunningSubtasksForRun();
  const assistantId = resolveAssistantId(context.agent_name);
  const reconnectOnMount =
    !onStreamThreadId || !threadActivitySnapshot.finished.has(onStreamThreadId);
  const controlledStreamThreadId = streamClientThreadId
    ? { threadId: streamClientThreadId }
    : {};

  const thread = useStream<AgentThreadState>({
    client: getAPIClient(isMock),
    assistantId,
    ...controlledStreamThreadId,
    reconnectOnMount,
    fetchStateHistory: { limit: 1 },
    onThreadId(createdThreadId) {
      if (isDeletedThreadTombstoned(createdThreadId)) {
        return;
      }
      const previousIds = new Set(
        [
          streamThreadIdRef.current,
          optimisticThreadIdRef.current,
          liveMessagesThreadIdRef.current,
        ].filter((id): id is string => Boolean(id && id !== createdThreadId)),
      );
      queuedMessagesRef.current = queuedMessagesRef.current.map((queued) =>
        previousIds.has(queued.threadId)
          ? { ...queued, threadId: createdThreadId }
          : queued,
      );
      streamThreadIdRef.current = createdThreadId;
      streamOwnerSnapshotRef.current = {
        threadId: createdThreadId,
        runId: streamRunIdRef.current,
      };
      setOnStreamThreadId(createdThreadId);
      for (const previousId of previousIds) {
        clearThreadActivity(previousId);
      }
      markThreadBusyInCaches(queryClient, createdThreadId);
    },
    onCreated(meta) {
      if (isDeletedThreadTombstoned(meta.thread_id)) {
        return;
      }
      handleStreamStart(meta.thread_id, meta.run_id);
      markThreadBusyInCaches(queryClient, meta.thread_id);
      startBackgroundRunProbe({
        queryClient,
        threadId: meta.thread_id,
        runId: meta.run_id,
        isMock,
        settleRunSubtasks,
      });
      void queryClient.invalidateQueries({
        queryKey: threadRunsQueryKey(meta.thread_id),
      });
      const now = new Date().toISOString();
      upsertThreadInSearchCache(queryClient, {
        thread_id: meta.thread_id,
        created_at: now,
        updated_at: now,
        metadata: context.agent_name ? { agent_name: context.agent_name } : {},
        status: "busy",
        values: {
          title: t.pages.newChat,
          messages: [],
          artifacts: [],
        },
        interrupts: {},
      });
      upsertThreadInInfiniteCache(queryClient, {
        thread_id: meta.thread_id,
        created_at: now,
        updated_at: now,
        metadata: context.agent_name ? { agent_name: context.agent_name } : {},
        status: "busy",
        values: {
          title: t.pages.newChat,
          messages: [],
          artifacts: [],
        },
        interrupts: {},
      });
      if (context.agent_name && !isMock) {
        void getAPIClient()
          .threads.update(meta.thread_id, {
            metadata: { agent_name: context.agent_name },
          })
          .catch(() => ({}));
      }
    },
    onLangChainEvent(event) {
      if (event.event === "on_tool_end") {
        const toolEndEvent = getScopedToolEndEvent(
          event,
          streamThreadIdRef.current,
          streamRunIdRef.current,
        );
        if (toolEndEvent) {
          listeners.current.onToolEnd?.(toolEndEvent);
        }
      }
    },
    onUpdateEvent(data) {
      const _messages = getSummarizationMiddlewareMessages(data);
      if (_messages && _messages.length >= 2) {
        for (const m of _messages) {
          if (m.name === "summary" && m.type === "human") {
            summarizedRef.current?.add(m.id ?? "");
          }
        }
        const firstRetainedVisibleIdentity = _messages
          .filter((message) => message.type !== "remove")
          .filter((message) => !isHiddenFromUIMessage(message))
          .map(messageIdentity)
          .find(isNonEmptyString);
        const _currentMessages = [...messagesRef.current];
        const _movedMessages: Message[] = [];
        for (const m of _currentMessages) {
          if (
            firstRetainedVisibleIdentity &&
            messageIdentity(m) === firstRetainedVisibleIdentity
          ) {
            break;
          }
          if (!summarizedRef.current?.has(m.id ?? "")) {
            _movedMessages.push(m);
          }
        }
        appendMessages(_movedMessages);
        messagesRef.current = [];
      }

      const updates: Array<Partial<AgentThreadState> | null> = Object.values(
        data || {},
      );
      for (const update of updates) {
        if (update && "title" in update && update.title) {
          const streamThreadId = streamThreadIdRef.current;
          const updateRecord = update as Record<string, unknown>;
          const eventThreadId =
            typeof updateRecord.thread_id === "string"
              ? updateRecord.thread_id
              : typeof updateRecord.threadId === "string"
                ? updateRecord.threadId
                : null;
          const eventRunId =
            typeof updateRecord.run_id === "string"
              ? updateRecord.run_id
              : typeof updateRecord.runId === "string"
                ? updateRecord.runId
                : null;
          if (
            !shouldApplyStreamTitleUpdate({
              eventThreadId,
              eventRunId,
              streamThreadId,
              streamRunId: streamRunIdRef.current,
              viewThreadId: currentViewThreadIdRef.current,
              liveMessagesThreadId: liveMessagesThreadIdRef.current,
              optimisticThreadId: optimisticThreadIdRef.current,
            }) ||
            !shouldAcceptStreamTitle(streamThreadId, update.title)
          ) {
            continue;
          }
          void queryClient.setQueriesData(
            {
              queryKey: ["threads", "search"],
              exact: false,
            },
            (oldData: Array<AgentThread> | undefined) => {
              return oldData?.map((t) => {
                if (t.thread_id === streamThreadId) {
                  return {
                    ...t,
                    values: {
                      ...t.values,
                      title: update.title,
                    },
                  };
                }
                return t;
              });
            },
          );
          const nextTitle: string = update.title;
          void queryClient.setQueriesData(
            {
              queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
              exact: false,
            },
            (oldData: InfiniteData<AgentThread[]> | undefined) =>
              mapInfiniteThreadsCache(
                oldData,
                (t): AgentThread =>
                  t.thread_id === streamThreadId
                    ? {
                        ...t,
                        values: {
                          ...t.values,
                          title: nextTitle,
                        },
                      }
                    : t,
              ),
          );
        }
      }
    },
    onCustomEvent(event: unknown) {
      const taskEvent = asTaskEvent(event);
      if (taskEvent) {
        const taskThreadId = resolveVisibleTaskRunningThreadId({
          eventThreadId: stringValue(taskEvent.thread_id) ?? null,
          streamThreadId: streamThreadIdRef.current,
          viewThreadId: currentViewThreadIdRef.current,
          liveMessagesThreadId: liveMessagesThreadIdRef.current,
        });
        if (!taskThreadId) {
          return;
        }
        applyTaskEventToSubtask(taskEvent, updateSubtask, taskThreadId);
        reconcileTaskEventRunHistory(
          queryClient,
          taskEvent,
          refreshHistoryRuns,
        );
        return;
      }

      const terminalEvent = asRunTerminalEvent(event);
      if (
        terminalEvent &&
        reconcileTerminalRunHistory(
          queryClient,
          terminalEvent,
          refreshHistoryRuns,
          settleRunSubtasks,
        )
      ) {
        if (
          shouldTreatTerminalEventAsCurrentStream(
            terminalEvent.thread_id,
            terminalEvent.run_id,
            streamThreadIdRef.current,
            streamRunIdRef.current,
          )
        ) {
          streamFinishedRef.current = true;
          setQueueReleaseVersion((version) => version + 1);
        }
        if (
          isSameStreamErrorRecoveryRun(
            streamErrorRecoveryRunRef.current,
            terminalEvent.thread_id,
            terminalEvent.run_id,
          )
        ) {
          setStreamErrorRecoveryRun(null);
        }
        return;
      }

      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "llm_retry" &&
        "message" in event &&
        typeof event.message === "string" &&
        event.message.trim()
      ) {
        const e = event as { type: "llm_retry"; message: string };
        toast(e.message);
      }
    },
    onError(error, run) {
      const streamThreadId = run?.thread_id ?? streamThreadIdRef.current;
      const streamRunId = run?.run_id ?? streamRunIdRef.current;
      releaseStreamClientThreadId(streamThreadId);
      if (
        shouldCommitStreamStartFromError({
          started: startedRef.current,
          threadId: streamThreadId,
          runId: streamRunId,
        })
      ) {
        handleStreamStart(streamThreadId!, streamRunId!);
      }
      const recoveryRun = applyStreamErrorRecovery({
        queryClient,
        threadId: streamThreadId,
        runId: streamRunId,
        isMock,
        settleRunSubtasks,
      });
      const errorOwnsCurrentStream = shouldTreatTerminalEventAsCurrentStream(
        streamThreadId,
        streamRunId,
        streamThreadIdRef.current,
        streamRunIdRef.current,
      );
      const errorOwnsCurrentThread = Boolean(
        streamThreadId && streamThreadId === streamThreadIdRef.current,
      );
      const errorOwnsCurrentUi =
        errorOwnsCurrentStream || (errorOwnsCurrentThread && !streamRunId);
      if (
        !streamErrorRecoveryRunRef.current ||
        errorOwnsCurrentUi ||
        isSameStreamErrorRecoveryRun(
          streamErrorRecoveryRunRef.current,
          streamThreadId,
          streamRunId,
        )
      ) {
        setStreamErrorRecoveryRun(recoveryRun);
      }
      if (errorOwnsCurrentUi) {
        streamFinishedRef.current = recoveryRun === null;
        if (recoveryRun === null) {
          setQueueReleaseVersion((version) => version + 1);
        }
      }
      if (recoveryRun === null && errorOwnsCurrentUi) {
        setOptimisticMessages([]);
        setOptimisticThreadTarget(null);
        setLiveMessagesThreadTarget(null);
        setPendingSupersededRunIds(new Set());
        setPendingSupersededMessageIds(new Set());
      }
      if (errorOwnsCurrentUi && shouldShowStreamErrorToast(recoveryRun)) {
        toast.error(getStreamErrorMessage(error));
      }
      pendingUsageBaselineMessageIdsRef.current = new Set(
        messagesRef.current
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      if (streamThreadId && !isMock) {
        void queryClient.invalidateQueries({
          queryKey: threadTokenUsageQueryKey(streamThreadId),
        });
        void queryClient.invalidateQueries({
          queryKey: threadContextUsageQueryKey(streamThreadId),
        });
      }
    },
    onFinish(state, run) {
      const finishMeta = resolveThreadStreamFinishMeta({
        run,
        streamOwner: streamOwnerSnapshotRef.current,
      });
      const streamThreadId = finishMeta.threadId;
      const streamRunId = finishMeta.runId;
      releaseStreamClientThreadId(streamThreadId);
      if (streamRunId) {
        clearReconnectRun(streamThreadId, streamRunId);
        stopBackgroundRunProbe(streamThreadId, streamRunId);
      }
      if (
        !streamErrorRecoveryRunRef.current ||
        isSameStreamErrorRecoveryRun(
          streamErrorRecoveryRunRef.current,
          streamThreadId,
          streamRunId,
        )
      ) {
        setStreamErrorRecoveryRun(null);
      }
      if (streamThreadId) {
        markThreadFinished(streamThreadId);
        setThreadStatusInCaches(queryClient, streamThreadId, "idle");
      }
      const finishOwnsCurrentStream = shouldRunCurrentStreamFinishSideEffects({
        eventThreadId: streamThreadId,
        eventRunId: streamRunId,
        streamThreadId: streamThreadIdRef.current,
        streamRunId: streamRunIdRef.current,
      });
      if (finishOwnsCurrentStream) {
        streamFinishedRef.current = true;
        setQueueReleaseVersion((version) => version + 1);
        listeners.current.onFinish?.(state.values, finishMeta);
      }
      pendingUsageBaselineMessageIdsRef.current = new Set(
        messagesRef.current
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      if (streamThreadId) {
        invalidateTerminalRunQueries(queryClient, streamThreadId);
      }
    },
  });

  useEffect(() => {
    const streamThreadId = streamThreadIdRef.current;
    if (!thread.isLoading || !streamThreadId) {
      return;
    }
    markThreadBusyInCaches(queryClient, streamThreadId);
  }, [queryClient, thread.isLoading]);

  const hasVisibleStreamState = shouldShowLiveThreadState(
    currentViewThreadId,
    onStreamThreadId ?? null,
    liveMessagesThreadId,
  );
  const currentThreadMessages = useMemo(
    () =>
      hasVisibleStreamState
        ? thread.messages.filter(
            (message) =>
              !message.id || !pendingSupersededMessageIds.has(message.id),
          )
        : [],
    [hasVisibleStreamState, pendingSupersededMessageIds, thread.messages],
  );
  const liveSnapshotThreadId =
    streamThreadIdRef.current ?? onStreamThreadId ?? null;
  if (
    hasVisibleStreamState &&
    thread.isLoading &&
    liveSnapshotThreadId &&
    currentThreadMessages.length > 0
  ) {
    liveMessagesSnapshotRef.current = {
      threadId: liveSnapshotThreadId,
      runId: streamRunIdRef.current,
      messages: currentThreadMessages,
    };
  }
  const persistedMessages = useMemo(
    () =>
      getThreadMessagesWithLiveSnapshot({
        viewThreadId: currentViewThreadId,
        threadMessages: currentThreadMessages,
        liveSnapshot: liveMessagesSnapshotRef.current,
        pendingSupersededMessageIds,
      }),
    [currentThreadMessages, currentViewThreadId, pendingSupersededMessageIds],
  );
  const visibleHistory = useMemo(
    () =>
      shouldShowThreadHistory(currentViewThreadId, onStreamThreadId ?? null)
        ? history
        : [],
    [currentViewThreadId, history, onStreamThreadId],
  );
  const humanMessageCount = persistedMessages.filter(
    (m) => m.type === "human",
  ).length;
  const latestMessageCountsRef = useRef({ humanMessageCount });
  const sendInFlightRef = useRef(false);
  const streamFinishedRef = useRef(true);
  const queuedMessagesRef = useRef<QueuedThreadMessage[]>([]);
  const messagesRef = useRef<Message[]>([]);
  const summarizedRef = useRef<Set<string>>(null);
  // Track human message count before sending to prevent clearing optimistic
  // messages before the server's human message arrives (e.g. when AI messages
  // from "messages-tuple" events arrive before the input human message from
  // "values" events).
  const prevHumanMsgCountRef = useRef(humanMessageCount);

  latestMessageCountsRef.current = { humanMessageCount };
  summarizedRef.current ??= new Set<string>();

  // Reset thread-local pending UI state when switching between threads so
  // optimistic messages and in-flight guards do not leak across chat views.
  useEffect(() => {
    startedRef.current = false;
    streamRunIdRef.current = null;
    activeSendRequestRef.current = null;
    sendInFlightRef.current = false;
    streamFinishedRef.current = true;
    messagesRef.current = [];
    summarizedRef.current = new Set<string>();
    pendingUsageBaselineMessageIdsRef.current = new Set();
    setPendingSupersededRunIds(new Set());
    setPendingSupersededMessageIds(new Set());
    setStreamErrorRecoveryRun(null);
    setIsUploading(false);
    prevHumanMsgCountRef.current =
      latestMessageCountsRef.current.humanMessageCount;
  }, [setStreamErrorRecoveryRun, threadId]);

  useEffect(() => {
    if (
      !hasTerminalStreamErrorRecoveryRun(streamErrorRecoveryRun, historyRuns)
    ) {
      return;
    }
    streamFinishedRef.current = true;
    setQueueReleaseVersion((version) => version + 1);
    setStreamErrorRecoveryRun(null);
  }, [historyRuns, setStreamErrorRecoveryRun, streamErrorRecoveryRun]);

  useEffect(() => {
    if (
      !streamErrorRecoveryRun ||
      shouldKeepStreamErrorRecoveryRun(streamErrorRecoveryRun, threadActivity)
    ) {
      return;
    }
    streamFinishedRef.current = true;
    setQueueReleaseVersion((version) => version + 1);
    setStreamErrorRecoveryRun(null);
  }, [setStreamErrorRecoveryRun, streamErrorRecoveryRun, threadActivity]);

  useEffect(() => {
    if (optimisticThreadId && optimisticThreadId !== currentViewThreadId) {
      setOptimisticMessages([]);
      setOptimisticThreadTarget(null);
    }
    if (liveMessagesThreadId && liveMessagesThreadId !== currentViewThreadId) {
      setLiveMessagesThreadTarget(null);
    }
  }, [
    currentViewThreadId,
    liveMessagesThreadId,
    optimisticThreadId,
    setLiveMessagesThreadTarget,
    setOptimisticThreadTarget,
  ]);

  // When streaming starts without a baseline (e.g. reconnection, run started
  // from another client, or page reload mid-stream), snapshot the current
  // messages so only *new* messages are treated as "pending" for token usage.
  useEffect(() => {
    if (
      thread.isLoading &&
      pendingUsageBaselineMessageIdsRef.current.size === 0
    ) {
      pendingUsageBaselineMessageIdsRef.current = new Set(
        persistedMessages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
    }
  }, [persistedMessages, thread.isLoading]);

  // Clear optimistic when server messages arrive.
  // For messages with a human optimistic message, wait until the server's
  // human message has arrived to avoid clearing before the input message
  // appears in the stream (the input message may arrive via "values" events
  // after individual "messages-tuple" events for AI messages).
  const optimisticMessageCount = optimisticMessages.length;
  const hasHumanOptimistic = optimisticMessages.some((m) => m.type === "human");
  useEffect(() => {
    if (optimisticMessageCount === 0) return;

    const newHumanMsgArrived = humanMessageCount > prevHumanMsgCountRef.current;

    if (!hasHumanOptimistic || newHumanMsgArrived) {
      setOptimisticMessages([]);
      setOptimisticThreadTarget(null);
    }
  }, [
    hasHumanOptimistic,
    humanMessageCount,
    optimisticMessageCount,
    setOptimisticThreadTarget,
  ]);

  const sendMessage = useCallback(
    async (
      threadId: string,
      message: PromptInputMessage,
      extraContext?: Record<string, unknown>,
      options?: SendMessageOptions,
    ) => {
      const text = message.text.trim();
      if (threadId === "new" && message.files.length > 0) {
        toast.error("Please start the chat before adding attachments.");
        return Promise.reject(
          new Error("Attachments require a saved thread before upload."),
        );
      }
      const targetThreadRecovering = isThreadRecoveringFromStreamError(
        streamErrorRecoveryRunRef.current,
        threadId,
      );
      if (
        shouldQueueThreadMessage({
          isLoading: thread.isLoading,
          streamFinished: streamFinishedRef.current,
          recovering: targetThreadRecovering,
          sendInFlight: sendInFlightRef.current,
        })
      ) {
        if (message.files.length > 0) {
          toast(t.inputBox.waitForCurrentResponse);
          return Promise.reject(
            new Error("Current response is still streaming."),
          );
        }
        prevHumanMsgCountRef.current = humanMessageCount;
        queuedMessagesRef.current = [
          ...queuedMessagesRef.current,
          {
            ownerId: currentRuntimeOwnerIdRef.current ?? threadId,
            threadId,
            message: { ...message, text, files: [] },
            extraContext,
            options,
          },
        ];
        const hideFromUI = options?.additionalKwargs?.hide_from_ui === true;
        if (!hideFromUI) {
          const optimisticAdditionalKwargs = {
            ...options?.additionalKwargs,
            pending_while_streaming: true,
          };
          setOptimisticMessages((messages) => [
            ...messages,
            {
              type: "human",
              id: createOptimisticMessageId("opt-queued-human"),
              content: text ? [{ type: "text", text }] : "",
              additional_kwargs: optimisticAdditionalKwargs,
            },
          ]);
        }
        setOptimisticThreadTarget(threadId);
        setLiveMessagesThreadTarget(threadId);
        markThreadBusyInCaches(queryClient, threadId);
        listeners.current.onSend?.(threadId);
        return;
      }

      if (sendInFlightRef.current) {
        return;
      }
      sendInFlightRef.current = true;
      streamClientThreadIdLockedRef.current = true;
      const sendRequest = {
        requestId: createOptimisticMessageId("send"),
        threadId,
      };
      activeSendRequestRef.current = sendRequest;
      const ownsSendRequest = () =>
        isSameSendRequest(activeSendRequestRef.current, sendRequest) &&
        !isDeletedThreadTombstoned(sendRequest.threadId);

      // Capture the current human message count before showing optimistic
      // messages so we can wait for the server's copy of the user input.
      prevHumanMsgCountRef.current = humanMessageCount;
      pendingUsageBaselineMessageIdsRef.current = new Set(
        persistedMessages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );

      // Build optimistic files list with uploading status
      const optimisticFiles: FileInMessage[] = (message.files ?? []).map(
        (f) => ({
          filename: f.filename ?? "",
          size: 0,
          status: "uploading" as const,
        }),
      );

      const hideFromUI = options?.additionalKwargs?.hide_from_ui === true;
      const optimisticAdditionalKwargs = {
        ...options?.additionalKwargs,
        ...(optimisticFiles.length > 0 ? { files: optimisticFiles } : {}),
      };

      const newOptimistic: Message[] = [];
      if (!hideFromUI) {
        newOptimistic.push({
          type: "human",
          id: createOptimisticMessageId("opt-human"),
          content: text ? [{ type: "text", text }] : "",
          additional_kwargs: optimisticAdditionalKwargs,
        });
      }

      if (optimisticFiles.length > 0 && !hideFromUI) {
        // Mock AI message while files are being uploaded
        newOptimistic.push({
          type: "ai",
          id: createOptimisticMessageId("opt-ai"),
          content: t.uploads.uploadingFiles,
          additional_kwargs: {
            element: "task",
            upload_status: "uploading",
          },
        });
      }
      setOptimisticThreadTarget(threadId);
      setLiveMessagesThreadTarget(threadId);
      setOptimisticMessages(newOptimistic);
      streamFinishedRef.current = false;
      markThreadBusyInCaches(queryClient, threadId);

      listeners.current.onSend?.(threadId);

      let uploadedFileInfo: UploadedFileInfo[] = [];

      try {
        // Upload files first if any
        if (message.files && message.files.length > 0) {
          setIsUploading(true);
          try {
            const filePromises = message.files.map((fileUIPart) =>
              promptInputFilePartToFile(fileUIPart),
            );

            const conversionResults = await Promise.all(filePromises);
            const files = conversionResults.filter(
              (file): file is File => file !== null,
            );
            const failedConversions = conversionResults.length - files.length;

            if (failedConversions > 0) {
              throw new Error(
                `Failed to prepare ${failedConversions} attachment(s) for upload. Please retry.`,
              );
            }

            if (!threadId) {
              throw new Error("Thread is not ready for file upload.");
            }

            if (files.length > 0) {
              const uploadResponse = await uploadFiles(threadId, files);
              if (!ownsSendRequest()) {
                return;
              }
              uploadedFileInfo = uploadResponse.files;

              // Update optimistic human message with uploaded status + paths
              const uploadedFiles: FileInMessage[] = uploadedFileInfo.map(
                (info) => ({
                  filename: info.filename,
                  size: info.size,
                  path: info.virtual_path,
                  status: "uploaded" as const,
                }),
              );
              setOptimisticMessages((messages) => {
                return completeOptimisticUploadMessages(
                  messages,
                  uploadedFiles,
                );
              });
            }
          } catch (error) {
            if (!ownsSendRequest()) {
              return;
            }
            const errorMessage =
              error instanceof Error
                ? error.message
                : "Failed to upload files.";
            toast.error(errorMessage);
            setOptimisticMessages([]);
            setOptimisticThreadTarget(null);
            setLiveMessagesThreadTarget(null);
            throw error;
          } finally {
            if (ownsSendRequest()) {
              setIsUploading(false);
            }
          }
        }

        if (!ownsSendRequest()) {
          return;
        }

        // Build files metadata for submission (included in additional_kwargs)
        const filesForSubmit: FileInMessage[] = uploadedFileInfo.map(
          (info) => ({
            filename: info.filename,
            size: info.size,
            path: info.virtual_path,
            status: "uploaded" as const,
          }),
        );

        await thread.submit(
          {
            messages: [
              {
                type: "human",
                content: [
                  {
                    type: "text",
                    text,
                  },
                ],
                additional_kwargs: {
                  ...options?.additionalKwargs,
                  ...(filesForSubmit.length > 0
                    ? { files: filesForSubmit }
                    : {}),
                },
              },
            ],
          },
          {
            threadId: threadId,
            streamSubgraphs: true,
            streamResumable: true,
            onDisconnect: "continue",
            config: {
              recursion_limit: 1000,
            },
            context: buildThreadRunContext(context, threadId, extraContext),
          },
        );
        void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
        void queryClient.invalidateQueries({
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
        });
      } catch (error) {
        if (!ownsSendRequest()) {
          return;
        }
        releaseStreamClientThreadId(streamThreadIdRef.current);
        setOptimisticMessages([]);
        setOptimisticThreadTarget(null);
        setLiveMessagesThreadTarget(null);
        setIsUploading(false);
        clearThreadActivity(threadId);
        throw error;
      } finally {
        if (ownsSendRequest()) {
          activeSendRequestRef.current = null;
          sendInFlightRef.current = false;
          setQueueReleaseVersion((version) => version + 1);
        }
      }
    },
    [
      thread,
      t.inputBox.waitForCurrentResponse,
      t.uploads.uploadingFiles,
      context,
      queryClient,
      humanMessageCount,
      persistedMessages,
      releaseStreamClientThreadId,
      setLiveMessagesThreadTarget,
      setOptimisticThreadTarget,
    ],
  );

  useEffect(() => {
    const next = queuedMessagesRef.current[0];
    const currentThreadRecovering = isThreadRecoveringFromStreamError(
      streamErrorRecoveryRun,
      currentViewThreadId,
    );
    if (
      !shouldReleaseQueuedThreadMessage({
        streamFinished: streamFinishedRef.current,
        sendInFlight: sendInFlightRef.current,
        recovering: currentThreadRecovering,
        queuedOwnerId: next?.ownerId,
        currentOwnerId: currentRuntimeOwnerId,
        queuedThreadId: next?.threadId,
        currentViewThreadId,
      })
    ) {
      return;
    }
    if (!next) {
      return;
    }
    if (isDeletedThreadTombstoned(next.threadId)) {
      queuedMessagesRef.current = queuedMessagesRef.current.slice(1);
      return;
    }
    queuedMessagesRef.current = queuedMessagesRef.current.slice(1);
    void sendMessage(
      next.threadId,
      next.message,
      next.extraContext,
      next.options,
    );
  }, [
    currentViewThreadId,
    currentRuntimeOwnerId,
    sendMessage,
    streamErrorRecoveryRun,
    queueReleaseVersion,
    thread.isLoading,
  ]);

  const regenerateMessage = useCallback(
    async (
      threadId: string,
      messageId: string,
      supersededMessageIds: string[] = [messageId],
    ) => {
      if (sendInFlightRef.current || !threadId || !messageId) {
        return;
      }
      sendInFlightRef.current = true;
      const sendRequest = {
        requestId: createOptimisticMessageId("regen"),
        threadId,
      };
      activeSendRequestRef.current = sendRequest;
      const ownsSendRequest = () =>
        isSameSendRequest(activeSendRequestRef.current, sendRequest) &&
        !isDeletedThreadTombstoned(sendRequest.threadId);
      prevHumanMsgCountRef.current = humanMessageCount;
      pendingUsageBaselineMessageIdsRef.current = new Set(
        persistedMessages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      setLiveMessagesThreadTarget(threadId);
      markThreadBusyInCaches(queryClient, threadId);
      listeners.current.onSend?.(threadId);
      let preparedSupersededRunId: string | null = null;
      let preparedSupersededMessageIds: string[] = [];

      try {
        const response = await fetch(
          `${getBackendBaseURL()}/api/threads/${encodeURIComponent(
            threadId,
          )}/runs/regenerate/prepare`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            credentials: "include",
            body: JSON.stringify({ message_id: messageId }),
          },
        );
        if (!response.ok) {
          throw new Error(await readResponseErrorMessage(response));
        }
        if (!ownsSendRequest()) {
          return;
        }
        const prepared = (await response.json()) as RegeneratePrepareResponse;
        if (!ownsSendRequest()) {
          return;
        }
        preparedSupersededRunId = prepared.target_run_id;
        preparedSupersededMessageIds = supersededMessageIds;
        setPendingSupersededRunIds((current) => {
          const next = new Set(current);
          next.add(prepared.target_run_id);
          return next;
        });
        setPendingSupersededMessageIds((current) => {
          const next = new Set(current);
          for (const id of supersededMessageIds) {
            next.add(id);
          }
          return next;
        });

        streamClientThreadIdLockedRef.current = true;
        await thread.submit(prepared.input, {
          threadId,
          checkpoint: prepared.checkpoint,
          metadata: prepared.metadata,
          streamSubgraphs: true,
          streamResumable: true,
          onDisconnect: "continue",
          config: {
            recursion_limit: 1000,
          },
          context: buildThreadRunContext(context, threadId),
        });
        if (!ownsSendRequest()) {
          return;
        }
        void queryClient.invalidateQueries({
          queryKey: threadRunsQueryKey(threadId),
        });
        void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
        void queryClient.invalidateQueries({
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
        });
        void queryClient.invalidateQueries({
          queryKey: threadTokenUsageQueryKey(threadId),
        });
        void queryClient.invalidateQueries({
          queryKey: threadContextUsageQueryKey(threadId),
        });
      } catch (error) {
        if (!ownsSendRequest()) {
          return;
        }
        releaseStreamClientThreadId(streamThreadIdRef.current);
        setLiveMessagesThreadTarget(null);
        clearThreadActivity(threadId);
        if (preparedSupersededRunId) {
          const supersededRunId = preparedSupersededRunId;
          setPendingSupersededRunIds((current) =>
            removeSetItems(current, [supersededRunId]),
          );
          setPendingSupersededMessageIds((current) =>
            removeSetItems(current, preparedSupersededMessageIds),
          );
        }
        toast.error(getStreamErrorMessage(error));
      } finally {
        if (ownsSendRequest()) {
          activeSendRequestRef.current = null;
          sendInFlightRef.current = false;
          setQueueReleaseVersion((version) => version + 1);
        }
      }
    },
    [
      context,
      humanMessageCount,
      persistedMessages,
      queryClient,
      releaseStreamClientThreadId,
      setLiveMessagesThreadTarget,
      thread,
    ],
  );

  // Cache the latest thread messages in a ref to compare against incoming history messages for deduplication,
  // and to allow access to the full message list in onUpdateEvent without causing re-renders.
  if (persistedMessages.length >= messagesRef.current.length) {
    messagesRef.current = persistedMessages;
  }

  const visibleOptimisticMessages = getVisibleOptimisticMessages(
    optimisticThreadId === currentViewThreadId ? optimisticMessages : [],
    prevHumanMsgCountRef.current,
    humanMessageCount,
  );
  const hasVisibleStreamErrorRecovery = isThreadRecoveringFromStreamError(
    streamErrorRecoveryRun,
    currentViewThreadId,
  );

  const mergedMessages = mergeMessages(
    visibleHistory,
    persistedMessages,
    visibleOptimisticMessages,
  );
  const pendingUsageMessages = thread.isLoading
    ? getMessagesAfterBaseline(
        persistedMessages,
        pendingUsageBaselineMessageIdsRef.current,
      )
    : [];

  // Merge history, live stream, and optimistic messages for display
  // History messages may overlap with thread.messages; thread.messages take precedence
  const mergedThread = {
    ...thread,
    isLoading: thread.isLoading || hasVisibleStreamErrorRecovery,
    error: getVisibleThreadError(thread.error, hasVisibleStreamErrorRecovery),
    values: hasVisibleStreamState ? thread.values : EMPTY_THREAD_VALUES,
    messages: mergedMessages,
  } as typeof thread;

  const recoveryStatus = hasVisibleStreamErrorRecovery
    ? ({
        state: "repairing",
        runId: streamErrorRecoveryRun?.runId ?? null,
      } as const)
    : historyError
      ? ({
          state: "failed",
          reason: getStreamErrorMessage(historyError),
        } as const)
      : terminalNotice
        ? ({
            state: "terminal",
            reason:
              terminalNotice.terminalReason ??
              terminalNotice.error ??
              terminalNotice.status,
          } as const)
        : streamErrorRecoveryRun === null &&
            !thread.isLoading &&
            thread.error === undefined
          ? null
          : null;

  return {
    thread: mergedThread,
    pendingUsageMessages,
    sendMessage,
    regenerateMessage,
    isUploading,
    isHistoryLoading,
    historyError,
    terminalNotice,
    recoveryStatus,
    retryRecovery: loadMoreHistory,
    hasMoreHistory,
    loadMoreHistory,
  } as const;
}

type ThreadHistoryOptions = {
  enabled?: boolean;
  pendingSupersededRunIds?: ReadonlySet<string>;
};

type RunWithRound = Run & {
  metadata?: { round_id?: unknown };
  round_id?: unknown;
  status?: unknown;
  terminal_reason?: unknown;
};

export function roundIdOfRun(run: Run | undefined) {
  const roundId = (run as RunWithRound | undefined)?.round_id;
  if (typeof roundId === "string") {
    return roundId;
  }
  const metadataRoundId = (run as RunWithRound | undefined)?.metadata?.round_id;
  return typeof metadataRoundId === "string" ? metadataRoundId : null;
}

function roundCurrentRunId(round: RuntimeRoundSnapshot) {
  return stringValue(round.current_run_id);
}

function terminalRunPatchForRoundState(state: string):
  | {
      status: string;
      terminalReason: string;
    }
  | undefined {
  if (state === "closed") {
    return undefined;
  }
  if (state === "blocked") {
    return { status: "error", terminalReason: "blocked" };
  }
  if (state === "waiting_user") {
    return { status: "interrupted", terminalReason: "waiting_user" };
  }
  return undefined;
}

export function applyNativeRoundsToSnapshotRuns(
  runs: Run[] | undefined,
  rounds: RuntimeRoundSnapshot[] | undefined,
): Run[] | undefined {
  if (!runs) {
    return undefined;
  }
  if (!rounds || rounds.length === 0) {
    return runs;
  }

  const roundByRunId = new Map<string, RuntimeRoundSnapshot>();
  for (const round of rounds) {
    const runId = roundCurrentRunId(round);
    if (runId) {
      roundByRunId.set(runId, round);
    }
  }

  let changed = false;
  const nextRuns = runs.map((run) => {
    const round = roundByRunId.get(run.run_id);
    if (!round) {
      return run;
    }

    const patch = terminalRunPatchForRoundState(round.state);
    const currentRoundId = roundIdOfRun(run);
    const runWithRound = run as RunWithRound;
    if (!patch) {
      if (currentRoundId === round.round_id) {
        return run;
      }
      changed = true;
      return { ...run, round_id: round.round_id } as Run;
    }

    const nextRun = {
      ...run,
      round_id: round.round_id,
      status: patch.status,
      terminal_reason:
        runWithRound.status === patch.status
          ? (stringValue(runWithRound.terminal_reason) ?? patch.terminalReason)
          : patch.terminalReason,
    } as Run;
    if (
      currentRoundId !== round.round_id ||
      runWithRound.status !== patch.status
    ) {
      changed = true;
    }
    return nextRun;
  });

  return changed ? nextRuns : runs;
}

export function mergeRunsWithTerminalPrecedence({
  snapshotRuns,
  queriedRuns,
  rounds,
}: {
  snapshotRuns?: Run[];
  queriedRuns?: Run[];
  rounds?: RuntimeRoundSnapshot[];
}): Run[] | undefined {
  const roundedSnapshotRuns = applyNativeRoundsToSnapshotRuns(
    snapshotRuns,
    rounds,
  );
  const roundedQueriedRuns = applyNativeRoundsToSnapshotRuns(
    queriedRuns,
    rounds,
  );
  if (!roundedQueriedRuns) {
    return roundedSnapshotRuns;
  }
  if (!roundedSnapshotRuns) {
    return roundedQueriedRuns;
  }

  const snapshotByRunId = new Map(
    roundedSnapshotRuns.map((run) => [run.run_id, run]),
  );
  const queriedRunIds = new Set(roundedQueriedRuns.map((run) => run.run_id));
  const mergedRuns = roundedQueriedRuns.map((queriedRun) => {
    const snapshotRun = snapshotByRunId.get(queriedRun.run_id);
    if (!snapshotRun) {
      return queriedRun;
    }
    if (
      isTerminalRunStatus(snapshotRun.status) &&
      isActiveRunStatus(queriedRun.status)
    ) {
      return snapshotRun;
    }
    if (
      isTerminalRunStatus(queriedRun.status) &&
      isActiveRunStatus(snapshotRun.status)
    ) {
      return queriedRun;
    }
    return queriedRun;
  });

  for (const snapshotRun of roundedSnapshotRuns) {
    if (!queriedRunIds.has(snapshotRun.run_id)) {
      mergedRuns.push(snapshotRun);
    }
  }
  return mergedRuns;
}

export function latestRoundIdFromSnapshot(
  runs: Run[] | undefined,
  rounds: RuntimeRoundSnapshot[] | undefined,
) {
  const latestRun = runs?.[0];
  if (!latestRun) {
    return null;
  }
  const latestRunRound = rounds?.find(
    (round) => roundCurrentRunId(round) === latestRun.run_id,
  );
  return latestRunRound?.round_id ?? roundIdOfRun(latestRun);
}

export function taskLanesForLatestRound(
  lanes: TaskLaneSnapshot[] | undefined,
  latestRoundId: string | null,
) {
  const rows = lanes ?? [];
  if (!latestRoundId) {
    return rows;
  }
  return rows.filter(
    (lane) => !lane.round_id || lane.round_id === latestRoundId,
  );
}

export function resolveThreadHistoryReset({
  enabled,
  threadChanged,
  previousRoundId,
  latestRoundId,
}: {
  enabled: boolean;
  threadChanged: boolean;
  previousRoundId: string | null;
  latestRoundId: string | null;
}) {
  if (!enabled || threadChanged) {
    return "clear";
  }
  if (
    previousRoundId !== null &&
    latestRoundId !== null &&
    previousRoundId !== latestRoundId
  ) {
    return "revalidate";
  }
  return "none";
}

function useThreadRuntimeSnapshot(
  threadId?: string,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery<ThreadRuntimeSnapshotResponse>({
    queryKey: threadRuntimeSnapshotQueryKey(threadId),
    queryFn: async () => {
      if (!threadId) {
        throw new Error("Missing thread id.");
      }
      const url = buildThreadRuntimeSnapshotUrl(getBackendBaseURL(), threadId);
      return fetch(url, {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
      }).then(readThreadRuntimeSnapshotResponse);
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
  });
}

export function useThreadHistory(
  threadId: string,
  { enabled = true, pendingSupersededRunIds }: ThreadHistoryOptions = {},
) {
  const snapshot = useThreadRuntimeSnapshot(threadId, { enabled });
  const runs = useThreadRuns(threadId, {
    enabled: enabled && (!snapshot.isLoading || snapshot.isError),
  });
  const threadIdRef = useRef(threadId);
  const runsRef = useRef(runs.data ?? []);
  const indexRef = useRef(-1);
  const loadingRef = useRef(false);
  const pendingLoadRef = useRef(false);
  const loadingRunIdRef = useRef<string | null>(null);
  const loadedRunIdsRef = useRef<Set<string>>(new Set());
  const runBeforeSeqRef = useRef<Map<string, number>>(new Map());
  const loadGenerationRef = useRef(0);
  const historyLoadAbortRef = useRef<AbortController | null>(null);
  const autoLoadedLatestRunIdRef = useRef<string | null>(null);
  const activeRunIdsRef = useRef<Set<string>>(new Set());
  const pendingRefreshRunIdsRef = useRef<Set<string>>(new Set());
  const appliedTaskEventKeysRef = useRef<Set<string>>(new Set());
  const latestRoundIdRef = useRef<string | null>(null);
  const updateSubtask = useUpdateSubtask();
  const settleRunSubtasks = useSettleRunningSubtasksForRun();
  const updateSubtaskRef = useRef(updateSubtask);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [messageRows, setMessageRows] = useState<RunMessage[]>([]);
  const [appendedMessages, setAppendedMessages] = useState<Message[]>([]);
  const { isError: runsIsError, refetch: refetchRuns } = runs;
  updateSubtaskRef.current = updateSubtask;
  const runsData = useMemo(
    () =>
      mergeRunsWithTerminalPrecedence({
        snapshotRuns: snapshot.data?.runs,
        queriedRuns: runs.data,
        rounds: snapshot.data?.rounds,
      }),
    [runs.data, snapshot.data?.rounds, snapshot.data?.runs],
  );

  const supersededRunIds = useMemo(() => {
    return getSupersededRunIds(runsData, pendingSupersededRunIds);
  }, [pendingSupersededRunIds, runsData]);

  const messages = useMemo(() => {
    return buildVisibleHistoryMessages(
      messageRows,
      supersededRunIds,
      appendedMessages,
      runsData,
    );
  }, [appendedMessages, messageRows, runsData, supersededRunIds]);
  const terminalNotice = useMemo(
    () => getLatestRunTerminalNotice(runsData, messageRows),
    [messageRows, runsData],
  );

  const loadMessages = useCallback(async () => {
    if (!enabled) {
      return;
    }
    const loadGeneration = loadGenerationRef.current;
    if (loadingRef.current) {
      const pendingRunIndex = findLatestUnloadedRunIndex(
        runsRef.current,
        loadedRunIdsRef.current,
      );
      const pendingRun = runsRef.current[pendingRunIndex];
      if (pendingRun && pendingRun.run_id !== loadingRunIdRef.current) {
        pendingLoadRef.current = true;
      }
      return;
    }
    if (runsRef.current.length === 0) {
      return;
    }

    loadingRef.current = true;
    setLoading(true);
    setError(null);
    const abortController = new AbortController();
    historyLoadAbortRef.current?.abort();
    historyLoadAbortRef.current = abortController;

    try {
      let consecutiveEmptyLoads = 0;
      do {
        pendingLoadRef.current = false;
        const queuedRefreshRunIds = [...pendingRefreshRunIdsRef.current];
        if (queuedRefreshRunIds.length > 0) {
          const { known } = resetLoadedRunStateForRefresh(
            queuedRefreshRunIds,
            runsRef.current,
            loadedRunIdsRef.current,
            runBeforeSeqRef.current,
          );
          for (const runId of known) {
            pendingRefreshRunIdsRef.current.delete(runId);
          }
        }

        const nextRunIndex = findLatestUnloadedRunIndex(
          runsRef.current,
          loadedRunIdsRef.current,
        );
        indexRef.current = nextRunIndex;

        const run = runsRef.current[nextRunIndex];
        if (!run) {
          indexRef.current = -1;
          return;
        }

        const requestThreadId = threadIdRef.current;
        loadingRunIdRef.current = run.run_id;
        const beforeSeq = runBeforeSeqRef.current.get(run.run_id);
        const url = buildRunMessagesUrl(
          getBackendBaseURL(),
          requestThreadId,
          run.run_id,
          beforeSeq,
        );
        const result: RunMessagesPageResponse = await fetch(url, {
          method: "GET",
          headers: {
            "Content-Type": "application/json",
          },
          credentials: "include",
          timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
          signal: abortController.signal,
        }).then(readRunMessagesPageResponse);
        if (
          loadGenerationRef.current !== loadGeneration ||
          threadIdRef.current !== requestThreadId ||
          !runsRef.current.some(
            (currentRun) => currentRun.run_id === run.run_id,
          )
        ) {
          return;
        }
        applyTaskEventRunMessages(
          result.data,
          updateSubtaskRef.current,
          requestThreadId,
          appliedTaskEventKeysRef.current,
        );
        const visibleMessages = result.data.filter(isVisibleHistoryRunMessage);
        setMessageRows((prev) =>
          mergeFetchedRunMessages(
            prev,
            result.data,
            run.run_id,
            beforeSeq === undefined,
          ),
        );
        const nextBeforeSeq = getNextRunMessagesBeforeSeq(result);
        if (typeof nextBeforeSeq === "number") {
          runBeforeSeqRef.current.set(run.run_id, nextBeforeSeq);
          pendingLoadRef.current = true;
        } else if (nextBeforeSeq === undefined) {
          console.warn(
            `Run ${run.run_id} returned has_more without message seq values; leaving it pending for retry.`,
          );
        } else {
          runBeforeSeqRef.current.delete(run.run_id);
          loadedRunIdsRef.current.add(run.run_id);
          const hasMoreUnloadedRuns =
            findLatestUnloadedRunIndex(
              runsRef.current,
              loadedRunIdsRef.current,
            ) !== -1;
          if (
            shouldAutoContinueRunHistory({
              hasMoreUnloadedRuns,
              visibleMessageCount: visibleMessages.length,
              consecutiveEmptyLoads,
            })
          ) {
            consecutiveEmptyLoads =
              visibleMessages.length === 0 ? consecutiveEmptyLoads + 1 : 0;
            pendingLoadRef.current = true;
          } else {
            consecutiveEmptyLoads = 0;
          }
        }
        indexRef.current = findLatestUnloadedRunIndex(
          runsRef.current,
          loadedRunIdsRef.current,
        );
      } while (pendingLoadRef.current);
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      console.warn("Failed to load thread history.", err);
      setError(err);
    } finally {
      if (historyLoadAbortRef.current === abortController) {
        historyLoadAbortRef.current = null;
      }
      if (loadGenerationRef.current === loadGeneration) {
        loadingRef.current = false;
        loadingRunIdRef.current = null;
        setLoading(false);
      }
    }
  }, [enabled]);

  useEffect(() => {
    const data = snapshot.data;
    if (!enabled || data?.thread_id !== threadId) {
      return;
    }

    loadGenerationRef.current += 1;
    historyLoadAbortRef.current?.abort();
    historyLoadAbortRef.current = null;
    threadIdRef.current = threadId;
    const snapshotRuns =
      applyNativeRoundsToSnapshotRuns(data.runs, data.rounds) ?? data.runs;
    const latestRoundId = latestRoundIdFromSnapshot(snapshotRuns, data.rounds);
    runsRef.current = snapshotRuns;
    indexRef.current = -1;
    pendingLoadRef.current = false;
    loadingRunIdRef.current = null;
    loadedRunIdsRef.current = new Set();
    runBeforeSeqRef.current = new Map();
    activeRunIdsRef.current = new Set(
      snapshotRuns
        .filter((run) => isActiveRunStatus(run.status))
        .map((run) => run.run_id),
    );
    appliedTaskEventKeysRef.current = new Set();

    const rows = data.run_messages.flatMap((page) => page.data);
    for (const run of snapshotRuns) {
      settleTerminalRunSubtasksForThread(settleRunSubtasks, threadId, run);
    }
    applyTaskEventRunMessages(
      rows,
      updateSubtaskRef.current,
      threadId,
      appliedTaskEventKeysRef.current,
    );
    for (const lane of taskLanesForLatestRound(
      data.task_lanes,
      latestRoundId,
    )) {
      updateSubtaskRef.current(taskLaneSubtaskUpdate(lane));
    }
    for (const page of data.run_messages) {
      pendingRefreshRunIdsRef.current.delete(page.run_id);
    }
    applySnapshotRunMessagePageState(
      data.run_messages,
      loadedRunIdsRef.current,
      runBeforeSeqRef.current,
    );
    latestRoundIdRef.current = latestRoundId;
    autoLoadedLatestRunIdRef.current = snapshotRuns[0]?.run_id ?? null;
    indexRef.current = findLatestUnloadedRunIndex(
      snapshotRuns,
      loadedRunIdsRef.current,
    );
    loadingRef.current = false;
    setError(null);
    setLoading(false);
    setMessageRows(rows);
  }, [enabled, settleRunSubtasks, snapshot.data, threadId]);

  useEffect(() => {
    const threadChanged = threadIdRef.current !== threadId;
    const latestRoundId = latestRoundIdFromSnapshot(
      runsData,
      snapshot.data?.rounds,
    );
    const resetMode = resolveThreadHistoryReset({
      enabled,
      threadChanged,
      previousRoundId: latestRoundIdRef.current,
      latestRoundId,
    });
    threadIdRef.current = threadId;

    if (resetMode !== "none") {
      loadGenerationRef.current += 1;
      historyLoadAbortRef.current?.abort();
      historyLoadAbortRef.current = null;
      indexRef.current = -1;
      pendingLoadRef.current = false;
      loadingRunIdRef.current = null;
      loadedRunIdsRef.current = new Set();
      runBeforeSeqRef.current = new Map();
      autoLoadedLatestRunIdRef.current = null;
      loadingRef.current = false;
      setError(null);
      setLoading(false);
      if (resetMode === "clear") {
        runsRef.current = [];
        activeRunIdsRef.current = new Set();
        pendingRefreshRunIdsRef.current = new Set();
        appliedTaskEventKeysRef.current = new Set();
        latestRoundIdRef.current = null;
        setMessageRows([]);
        setAppendedMessages([]);
      }
    }

    if (!enabled) {
      return;
    }

    if (latestRoundId !== null) {
      latestRoundIdRef.current = latestRoundId;
    }

    if (runsData && runsData.length > 0) {
      runsRef.current = runsData ?? [];
      indexRef.current = findLatestUnloadedRunIndex(
        runsData,
        loadedRunIdsRef.current,
      );
    }
    const latestRunId = runsData?.[0]?.run_id ?? null;
    if (
      shouldAutoLoadLatestRun(latestRunId, autoLoadedLatestRunIdRef.current)
    ) {
      autoLoadedLatestRunIdRef.current = latestRunId;
      loadMessages().catch(() => {
        toast.error("Failed to load thread history.");
      });
    }
  }, [enabled, threadId, runsData, snapshot.data?.rounds, loadMessages]);

  useEffect(() => {
    return () => {
      historyLoadAbortRef.current?.abort();
      historyLoadAbortRef.current = null;
    };
  }, []);

  const appendMessages = useCallback((_messages: Message[]) => {
    setAppendedMessages((prev) => {
      return dedupeMessagesByIdentity([...prev, ..._messages]);
    });
  }, []);

  const refreshRuns = useCallback(
    (params?: RefreshRunsParams) => {
      const legacyRunIds = isLegacyRefreshRunsParams(params)
        ? params
        : undefined;
      const requestThreadId = legacyRunIds ? undefined : params?.threadId;
      if (
        !shouldRefreshRunHistoryForThread(requestThreadId, threadIdRef.current)
      ) {
        return;
      }
      if (!enabled) {
        return;
      }
      const runIds = legacyRunIds ?? params?.runIds;
      const { known: ids, pending } = partitionKnownRunIds(
        runIds ?? [],
        runsRef.current,
      );
      for (const runId of pending) {
        pendingRefreshRunIdsRef.current.add(runId);
      }
      if (ids.length === 0) {
        return;
      }
      if (loadingRef.current) {
        for (const runId of ids) {
          pendingRefreshRunIdsRef.current.add(runId);
        }
        pendingLoadRef.current = true;
        return;
      }
      resetLoadedRunStateForRefresh(
        ids,
        runsRef.current,
        loadedRunIdsRef.current,
        runBeforeSeqRef.current,
      );
      indexRef.current = findLatestUnloadedRunIndex(
        runsRef.current,
        loadedRunIdsRef.current,
      );
      loadMessages().catch(() => {
        toast.error("Failed to load thread history.");
      });
    },
    [enabled, loadMessages],
  );

  const retryLoad = useCallback(async () => {
    setError(null);
    if (runsIsError) {
      const result = await refetchRuns();
      if (result.error) {
        setError(result.error);
      }
      return;
    }
    await loadMessages();
  }, [loadMessages, refetchRuns, runsIsError]);

  useEffect(() => {
    if (!enabled || !runsData) {
      activeRunIdsRef.current = new Set();
      return;
    }
    const terminalTransitionRunIds = getTerminalTransitionRunIds(
      activeRunIdsRef.current,
      runsData,
    );
    for (const runId of terminalTransitionRunIds) {
      const terminalRun = runsData.find((run) => run.run_id === runId);
      if (terminalRun) {
        settleTerminalRunSubtasksForThread(
          settleRunSubtasks,
          threadId,
          terminalRun,
        );
      }
    }
    const { known: pendingRefreshRunIds } = partitionKnownRunIds(
      pendingRefreshRunIdsRef.current,
      runsData,
    );
    for (const runId of pendingRefreshRunIds) {
      pendingRefreshRunIdsRef.current.delete(runId);
    }
    activeRunIdsRef.current = new Set(
      runsData
        .filter((run) => isActiveRunStatus(run.status))
        .map((run) => run.run_id),
    );
    const refreshRunIds = [
      ...terminalTransitionRunIds,
      ...pendingRefreshRunIds,
    ];
    if (refreshRunIds.length > 0) {
      refreshRuns({ threadId, runIds: refreshRunIds });
    }
  }, [enabled, refreshRuns, runsData, settleRunSubtasks, threadId]);

  const hasThreadId = Boolean(threadId);
  const hasUnloadedRuns = Boolean(
    runsData?.some((run) => !loadedRunIdsRef.current.has(run.run_id)),
  );
  const isSnapshotLoading =
    enabled && hasThreadId && snapshot.isLoading && !snapshot.data;
  const isRunsLoading =
    enabled &&
    hasThreadId &&
    (isSnapshotLoading ||
      runs.isLoading ||
      (runs.isFetching && !runs.data && !snapshot.data));
  const isRunsUnresolved =
    enabled &&
    hasThreadId &&
    !runsData &&
    !runs.isError &&
    (!snapshot.isError || runs.isLoading || runs.isFetching);
  const hasMore =
    enabled && hasThreadId && (indexRef.current >= 0 || hasUnloadedRuns);
  return {
    runs: runsData,
    messages,
    terminalNotice,
    loading: loading || isRunsLoading || isRunsUnresolved,
    error: error ?? (runs.isError ? runs.error : null),
    appendMessages,
    refreshRuns,
    hasMore,
    loadMore: retryLoad,
  };
}

export function useThreads(
  params: ThreadSearchParams = DEFAULT_THREAD_SEARCH_PARAMS,
) {
  const apiClient = getAPIClient();
  return useQuery<AgentThread[]>({
    ...buildThreadsSearchQueryOptions(apiClient, params),
  });
}

export const INFINITE_THREADS_PAGE_SIZE = 50;

export const INFINITE_THREADS_QUERY_KEY_PREFIX = [
  "threads",
  "searchInfinite",
] as const;

type InfiniteThreadsParams = Omit<
  Parameters<ThreadsClient["search"]>[0],
  "limit" | "offset"
>;

export function getInfiniteThreadsNextPageParam(
  lastPage: AgentThread[],
  allPages: AgentThread[][],
  pageSize: number = INFINITE_THREADS_PAGE_SIZE,
): number | undefined {
  if (lastPage.length < pageSize) {
    return undefined;
  }
  return allPages.reduce((sum, page) => sum + page.length, 0);
}

export function mapInfiniteThreadsCache(
  oldData: InfiniteData<AgentThread[]> | undefined,
  mapper: (thread: AgentThread) => AgentThread,
): InfiniteData<AgentThread[]> | undefined {
  if (!oldData) {
    return oldData;
  }
  return {
    ...oldData,
    pages: oldData.pages.map((page) => page.map(mapper)),
  };
}

export function filterInfiniteThreadsCache(
  oldData: InfiniteData<AgentThread[]> | undefined,
  predicate: (thread: AgentThread) => boolean,
): InfiniteData<AgentThread[]> | undefined {
  if (!oldData) {
    return oldData;
  }
  return {
    ...oldData,
    pages: oldData.pages.map((page) => page.filter(predicate)),
  };
}

export function clearThreadSingletonState(threadId: string) {
  deletedThreadTombstones.add(threadId);
  clearThreadActivity(threadId);
  manualThreadTitleLocks.delete(threadId);
  stopBackgroundRunProbesForThread(threadId);
}

function isDeletedThreadClientQueryKey(
  queryKey: readonly unknown[],
  threadId: string,
) {
  if (
    queryKey[0] === "thread" &&
    queryKey[1] === threadId &&
    (queryKey[2] === "runs" || queryKey[2] === "runtime-snapshot")
  ) {
    return true;
  }
  if (
    queryKey[0] === "thread" &&
    queryKey[1] === "metadata" &&
    queryKey[2] === threadId
  ) {
    return true;
  }
  if (queryKey[0] === "thread-token-usage" && queryKey[1] === threadId) {
    return true;
  }
  if (queryKey[0] === "thread-context-usage" && queryKey[1] === threadId) {
    return true;
  }
  if (
    queryKey[0] === "uploads" &&
    queryKey[1] === "list" &&
    queryKey[2] === threadId
  ) {
    return true;
  }
  if (
    queryKey[0] === "thread" &&
    queryKey[1] === threadId &&
    queryKey[2] === "run" &&
    typeof queryKey[3] === "string"
  ) {
    return true;
  }
  return (
    queryKey[0] === "artifact" &&
    typeof queryKey[1] === "string" &&
    queryKey[2] === threadId
  );
}

export function clearDeletedThreadClientState(
  queryClient: QueryClient,
  threadId: string,
  {
    clearSubtasksForThread,
  }: { clearSubtasksForThread?: (threadId: string) => void } = {},
) {
  clearThreadSingletonState(threadId);
  notifyThreadRuntimeDeleted(threadId);
  clearThreadModelName(threadId);
  queryClient.removeQueries({
    predicate: (query) =>
      isDeletedThreadClientQueryKey(query.queryKey, threadId),
  });
  clearSubtasksForThread?.(threadId);
}

export function useInfiniteThreads(
  params: InfiniteThreadsParams = {
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "values", "metadata"],
  },
) {
  const apiClient = getAPIClient();
  return useInfiniteQuery<
    AgentThread[],
    Error,
    InfiniteData<AgentThread[]>,
    readonly unknown[],
    number
  >({
    queryKey: [...INFINITE_THREADS_QUERY_KEY_PREFIX, params],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const response = (await apiClient.threads.search<AgentThreadState>({
        ...params,
        limit: INFINITE_THREADS_PAGE_SIZE,
        offset: pageParam,
      })) as AgentThread[];
      return response;
    },
    getNextPageParam: (lastPage, allPages) =>
      getInfiniteThreadsNextPageParam(lastPage, allPages),
    refetchOnWindowFocus: false,
  });
}

export function threadRunsQueryKey(threadId?: string | null) {
  return ["thread", threadId, "runs"] as const;
}

export function useThreadRuns(
  threadId?: string,
  { enabled = true }: { enabled?: boolean } = {},
) {
  const apiClient = getAPIClient();
  return useQuery<Run[]>({
    queryKey: threadRunsQueryKey(threadId),
    queryFn: async () => {
      if (!threadId) {
        return [];
      }
      const response = await apiClient.runs.list(threadId);
      return response;
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
  });
}

export function useThreadMetadata(
  threadId?: string | null,
  {
    enabled = true,
    isMock = false,
  }: { enabled?: boolean; isMock?: boolean } = {},
) {
  const apiClient = getAPIClient(isMock);
  return useQuery<AgentThread | null>({
    queryKey: ["thread", "metadata", threadId, isMock],
    queryFn: async () => {
      if (!threadId) {
        return null;
      }
      try {
        const response = await apiClient.threads.get(threadId);
        return response as AgentThread;
      } catch (error) {
        if (isThreadMissingError(error)) {
          return null;
        }
        throw error;
      }
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useThreadTokenUsage(
  threadId?: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery<ThreadTokenUsageResponse | null>({
    queryKey: threadTokenUsageQueryKey(threadId),
    queryFn: async () => {
      if (!threadId) {
        return null;
      }
      return fetchThreadTokenUsage(threadId);
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useThreadContextUsage(
  threadId?: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery<ThreadContextUsageResponse | null>({
    queryKey: threadContextUsageQueryKey(threadId),
    queryFn: async () => {
      if (!threadId) {
        return null;
      }
      return fetchThreadContextUsage(threadId);
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useRunDetail(threadId: string, runId: string) {
  const apiClient = getAPIClient();
  return useQuery<Run>({
    queryKey: ["thread", threadId, "run", runId],
    queryFn: async () => {
      const response = await apiClient.runs.get(threadId, runId);
      return response;
    },
    refetchOnWindowFocus: false,
  });
}

export function useDeleteThread() {
  const queryClient = useQueryClient();
  const clearSubtasksForThread = useClearSubtasksForThread();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      onRemoteDeleted,
    }: {
      threadId: string;
      onRemoteDeleted?: () => void;
    }) => {
      await apiClient.threads.delete(threadId);
      onRemoteDeleted?.();

      const response = await fetch(
        `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}`,
        {
          method: "DELETE",
        },
      );

      if (!response.ok) {
        const error = await response
          .json()
          .catch(() => ({ detail: "Failed to delete local thread data." }));
        throw new Error(error.detail ?? "Failed to delete local thread data.");
      }
    },
    onSuccess(_, { threadId }) {
      clearDeletedThreadClientState(queryClient, threadId, {
        clearSubtasksForThread,
      });
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread> | undefined) => {
          if (oldData == null) {
            return oldData;
          }
          return oldData.filter((t) => t.thread_id !== threadId);
        },
      );
      queryClient.setQueriesData(
        {
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
          exact: false,
        },
        (oldData: InfiniteData<AgentThread[]> | undefined) =>
          filterInfiniteThreadsCache(oldData, (t) => t.thread_id !== threadId),
      );
    },

    onSettled() {
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      void queryClient.invalidateQueries({
        queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      });
    },
  });
}

export function useRenameThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      title,
    }: {
      threadId: string;
      title: string;
    }) => {
      setManualThreadTitleLock(threadId, title);
      await apiClient.threads.updateState(threadId, {
        values: { title },
      });
    },
    onSuccess(_, { threadId, title }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread>) => {
          return oldData.map((t) => {
            if (t.thread_id === threadId) {
              return {
                ...t,
                values: {
                  ...t.values,
                  title,
                },
              };
            }
            return t;
          });
        },
      );
      queryClient.setQueriesData(
        {
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
          exact: false,
        },
        (oldData: InfiniteData<AgentThread[]> | undefined) =>
          mapInfiniteThreadsCache(oldData, (t) =>
            t.thread_id === threadId
              ? {
                  ...t,
                  values: {
                    ...t.values,
                    title,
                  },
                }
              : t,
          ),
      );
    },
  });
}
