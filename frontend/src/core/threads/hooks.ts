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
  useClearSubtasksForThread,
  useSettleRunningSubtasksForRun,
  useUpdateSubtask,
} from "../tasks/context";
import type { UploadedFileInfo, UploadResponse } from "../uploads";
import {
  isStaleThreadUploadError,
  promptInputFilePartToFile,
  uploadFiles,
  uploadListQueryKey,
} from "../uploads";

import { fetchThreadContextUsage, fetchThreadTokenUsage } from "./api";
import {
  applyNativeRoundsToSnapshotRuns,
  buildCommandRoomReadModel,
  latestRoundIdFromSnapshot,
  mergeRunsWithTerminalPrecedence,
  resolveThreadHistoryReset,
  roundIdOfRun,
  type RuntimeRoundSnapshot,
  type TaskLaneSnapshot,
} from "./command-room-read-model";
import {
  clearDeletedThreadTombstones,
  isDeletedThreadTombstoned,
  tombstoneDeletedThread,
} from "./deleted-thread-tombstones";
import {
  resolveStreamErrorRecoveryRuntimeOwnerId,
  shouldApplyStreamTitleUpdate,
  shouldRunCurrentStreamFinishSideEffects,
} from "./effect-policy";
import {
  applySnapshotRunMessagePageState,
  buildVisibleHistoryMessages,
  completeOptimisticUploadMessages,
  dedupeMessagesByIdentity,
  findLatestUnloadedRunIndex,
  getMessagesAfterBaseline,
  getNextRunMessagesBeforeSeq,
  getSupersededRunIds,
  getSummarizationMiddlewareMessages,
  getThreadMessagesWithLiveSnapshot,
  getVisibleOptimisticMessages,
  isAbortError,
  isNonEmptyString,
  isVisibleHistoryRunMessage,
  mergeFetchedRunMessages,
  mergeMessages,
  mergeSnapshotRunMessages,
  messageIdentity,
  partitionKnownRunIds,
  removeSetItems,
  resetLoadedRunStateForRefresh,
  shouldAutoContinueRunHistory,
  shouldAutoLoadLatestRun,
  shouldShowLiveThreadState,
  shouldShowThreadHistory,
  type LiveMessagesSnapshot,
  type RunMessagesPageResponse,
} from "./message-history";
import { isThreadScopedQueryKey, queryKeys } from "./query-keys";
import {
  getBackgroundRunProbeDelay,
  getHttpStatus,
  getStreamErrorMessage,
  getVisibleThreadError,
  hasTerminalStreamErrorRecoveryRun,
  isSameStreamErrorRecoveryRun,
  resolveRunStreamRecoveryErrorOwner,
  resolveThreadStreamFinishMeta,
  shouldCommitStreamStart,
  shouldRefreshRunHistoryForThread,
  shouldShowStreamErrorToast,
  shouldStopBackgroundRunProbe,
  type StreamErrorRecoveryRun,
  type ThreadStreamFinishMeta,
} from "./run-recovery";
import {
  getTerminalTransitionRunIds,
  isActiveRunStatus,
  isTerminalRunStatus,
} from "./run-status";
import { notifyThreadRuntimeDeleted } from "./runtime-events";
import {
  createThreadRuntimeOwnerSnapshot,
  isCurrentThreadRuntimeOwnerEvent,
  shouldClaimThreadRuntimeOwner,
  shouldPreserveRuntimeOwnerOnRouteSwitch,
  shouldReleaseQueuedRuntimeOwner,
  type ThreadRuntimeOwnerSnapshot,
} from "./runtime-owner";
import {
  applyTaskEventRunMessages,
  applyTaskEventToSubtask,
  asRunTerminalEvent,
  asTaskEvent,
  resolveVisibleTaskRunningThreadId,
  stringValue,
  taskLaneSubtaskUpdate,
  taskEventType,
} from "./task-events";
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

export {
  createThreadRuntimeOwnerSnapshot,
  isCurrentThreadRuntimeOwnerEvent,
  shouldClaimThreadRuntimeOwner,
  shouldPreserveRuntimeOwnerOnRouteSwitch,
};
export {
  resolveStreamErrorRecoveryRuntimeOwnerId,
  shouldApplyStreamTitleUpdate,
  shouldRunCurrentStreamFinishSideEffects,
  shouldTreatStreamFinishAsCurrentStream,
  shouldTreatTerminalEventAsCurrentStream,
} from "./effect-policy";
export {
  getBackgroundRunProbeDelay,
  getStreamErrorMessage,
  getThreadHistoryLoadErrorKind,
  getVisibleThreadError,
  hasTerminalStreamErrorRecoveryRun,
  isSameStreamErrorRecoveryRun,
  isThreadRecoveringFromStreamError,
  resolveRunStreamRecoveryErrorOwner,
  resolveThreadStreamFinishMeta,
  shouldCommitStreamStart,
  shouldCommitStreamStartFromError,
  shouldRefreshRunHistoryForThread,
  shouldShowStreamErrorToast,
  shouldStopBackgroundRunProbe,
} from "./run-recovery";
export type {
  StreamErrorRecoveryRun,
  ThreadHistoryLoadErrorKind,
  ThreadStreamFinishMeta,
  ThreadStreamOwnerSnapshot,
} from "./run-recovery";

export type ThreadStreamOptions = {
  threadId?: string | null | undefined;
  displayThreadId?: string | null | undefined;
  runtimeOwnerId?: string | null | undefined;
  context: LocalSettings["context"];
  isMock?: boolean;
  onSend?: (threadId: string) => void;
  onStart?: (threadId: string, runId: string | null) => void;
  onFinish?: (state: AgentThreadState, meta: ThreadStreamFinishMeta) => void;
  onToolEnd?: (event: ToolEndEvent) => void;
};

type SendMessageOptions = {
  additionalKwargs?: Record<string, unknown>;
  source?: "direct" | "queued";
};

type QueuedThreadMessage = {
  ownerId: string;
  threadId: string;
  message: PromptInputMessage;
  extraContext?: Record<string, unknown>;
  options?: SendMessageOptions;
};

export type SendRequestOwnership = {
  requestId: string;
  threadId: string;
  displayThreadId?: string | null;
  runtimeOwnerId?: string | null;
};

type ThreadActivitySnapshot = {
  running: ReadonlySet<string>;
  finished: ReadonlySet<string>;
};

type ThreadActivityOwnerScope = {
  runId?: string | null;
  runtimeOwnerId?: string | null;
};

type StreamErrorRecoveryOwner = {
  threadId: string;
  runId?: string | null;
  runtimeOwnerId?: string | null;
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
const backgroundRunProbeTimers = new Map<string, number>();
const backgroundRunProbeAttempts = new Map<string, number>();

type QueuedMessageReleaseState = {
  streamFinished: boolean;
  sendInFlight: boolean;
  recovering: boolean;
  paused?: boolean;
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

const threadActivityListeners = new Set<() => void>();
let threadActivitySnapshot: ThreadActivitySnapshot = {
  running: new Set(),
  finished: new Set(),
};
const LEGACY_THREAD_ACTIVITY_OWNER = "thread";
const threadActivityOwnersByThread = new Map<string, Set<string>>();
const manualThreadTitleLocks = new Map<string, string>();

export { isDeletedThreadTombstoned } from "./deleted-thread-tombstones";

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

function threadActivityOwnerKey(scope?: ThreadActivityOwnerScope) {
  if (scope?.runId) {
    return `run:${scope.runId}`;
  }
  if (scope?.runtimeOwnerId) {
    return `owner:${scope.runtimeOwnerId}`;
  }
  return LEGACY_THREAD_ACTIVITY_OWNER;
}

function isScopedThreadActivityOwner(scope?: ThreadActivityOwnerScope) {
  return Boolean(scope?.runId ?? scope?.runtimeOwnerId);
}

function setThreadActivityOwners(threadId: string, owners: Set<string>) {
  if (owners.size === 0) {
    threadActivityOwnersByThread.delete(threadId);
    return;
  }
  threadActivityOwnersByThread.set(threadId, owners);
}

function hasActiveThreadActivity(threadId: string) {
  return Boolean(threadActivityOwnersByThread.get(threadId)?.size);
}

function hasActiveThreadActivityOwner(
  threadId: string,
  scope?: ThreadActivityOwnerScope,
) {
  if (!isScopedThreadActivityOwner(scope)) {
    return hasActiveThreadActivity(threadId);
  }
  const owners = threadActivityOwnersByThread.get(threadId);
  if (!owners) {
    return false;
  }
  if (scope?.runId) {
    return owners.has(threadActivityOwnerKey({ runId: scope.runId }));
  }
  return Boolean(
    scope?.runtimeOwnerId &&
    owners.has(
      threadActivityOwnerKey({ runtimeOwnerId: scope.runtimeOwnerId }),
    ),
  );
}

function updateThreadActivitySnapshot(
  threadId: string,
  { finished }: { finished: boolean },
) {
  const running = new Set(threadActivitySnapshot.running);
  const finishedThreads = new Set(threadActivitySnapshot.finished);
  if (hasActiveThreadActivity(threadId)) {
    running.add(threadId);
    finishedThreads.delete(threadId);
  } else {
    running.delete(threadId);
    if (finished) {
      finishedThreads.add(threadId);
    } else {
      finishedThreads.delete(threadId);
    }
  }
  threadActivitySnapshot = {
    running,
    finished: finishedThreads,
  };
}

function markThreadActivityRunning(
  threadId: string,
  scope?: ThreadActivityOwnerScope,
) {
  const owners = new Set(threadActivityOwnersByThread.get(threadId));
  if (isScopedThreadActivityOwner(scope)) {
    if (scope?.runtimeOwnerId) {
      owners.delete(LEGACY_THREAD_ACTIVITY_OWNER);
      owners.delete(
        threadActivityOwnerKey({ runtimeOwnerId: scope.runtimeOwnerId }),
      );
    }
  }
  owners.add(threadActivityOwnerKey(scope));
  setThreadActivityOwners(threadId, owners);
  updateThreadActivitySnapshot(threadId, { finished: false });
  emitThreadActivity();
}

export function markThreadFinished(
  threadId: string,
  scope?: ThreadActivityOwnerScope,
) {
  if (isDeletedThreadTombstoned(threadId)) {
    return;
  }
  if (isScopedThreadActivityOwner(scope)) {
    const owners = new Set(threadActivityOwnersByThread.get(threadId));
    owners.delete(threadActivityOwnerKey(scope));
    if (scope?.runId && scope.runtimeOwnerId) {
      owners.delete(
        threadActivityOwnerKey({ runtimeOwnerId: scope.runtimeOwnerId }),
      );
    }
    setThreadActivityOwners(threadId, owners);
  } else {
    threadActivityOwnersByThread.delete(threadId);
  }
  updateThreadActivitySnapshot(threadId, { finished: true });
  emitThreadActivity();
}

export function clearThreadActivity(
  threadId: string,
  scope?: ThreadActivityOwnerScope,
) {
  if (isScopedThreadActivityOwner(scope)) {
    const owners = new Set(threadActivityOwnersByThread.get(threadId));
    owners.delete(threadActivityOwnerKey(scope));
    if (scope?.runId && scope.runtimeOwnerId) {
      owners.delete(
        threadActivityOwnerKey({ runtimeOwnerId: scope.runtimeOwnerId }),
      );
    }
    setThreadActivityOwners(threadId, owners);
  } else {
    threadActivityOwnersByThread.delete(threadId);
  }
  updateThreadActivitySnapshot(threadId, { finished: false });
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
  paused = false,
  queuedOwnerId,
  currentOwnerId,
  queuedThreadId,
  currentViewThreadId,
}: QueuedMessageReleaseState) {
  const currentOwner = createThreadRuntimeOwnerSnapshot({
    runtimeOwnerId: currentOwnerId,
    threadId: currentViewThreadId,
    displayThreadId: currentViewThreadId,
  });
  const ownedByCurrentRuntime = shouldReleaseQueuedRuntimeOwner({
    queuedOwnerId,
    currentOwner,
    queuedThreadId,
    currentViewThreadId,
  });
  return (
    streamFinished &&
    !sendInFlight &&
    !recovering &&
    !paused &&
    ownedByCurrentRuntime
  );
}

export function settleQueuedThreadMessageAttempt<T>(
  queue: readonly T[],
  attempted: T,
  succeeded: boolean,
) {
  if (queue[0] !== attempted) {
    return { queue: [...queue], failed: null, paused: false };
  }
  return {
    queue: queue.slice(1),
    failed: succeeded ? null : attempted,
    paused: !succeeded,
  };
}

export function shouldQueueThreadMessage({
  isLoading,
  streamFinished,
  recovering,
  sendInFlight,
}: QueuedMessageAdmissionState) {
  return (isLoading && !streamFinished) || recovering || sendInFlight;
}

export function resolveThreadRunsLoadAction({
  hasNextRunPage,
  hasRunsData,
  hasUnloadedRuns,
  nextPageIsError,
  runsIsError,
}: {
  hasNextRunPage: boolean;
  hasRunsData: boolean;
  hasUnloadedRuns: boolean;
  nextPageIsError: boolean;
  runsIsError: boolean;
}) {
  if (hasNextRunPage && (!hasUnloadedRuns || nextPageIsError)) {
    return "fetch-next-page" as const;
  }
  if (runsIsError && !hasRunsData) {
    return "refetch-runs" as const;
  }
  return "load-messages" as const;
}

export function isCurrentThreadHistoryRequest({
  currentGeneration,
  currentThreadId,
  requestGeneration,
  requestThreadId,
  tombstoned,
}: {
  currentGeneration: number;
  currentThreadId: string;
  requestGeneration: number;
  requestThreadId: string;
  tombstoned: boolean;
}) {
  return (
    currentGeneration === requestGeneration &&
    currentThreadId === requestThreadId &&
    !tombstoned
  );
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
    current.requestId === request.requestId &&
    (!current.runtimeOwnerId ||
      !request.runtimeOwnerId ||
      current.runtimeOwnerId === request.runtimeOwnerId)
  );
}

export function shouldApplyUploadContinuation({
  activeRequest,
  request,
  currentViewThreadId,
  isDeletedThread,
  visibleOnly = false,
}: {
  activeRequest: SendRequestOwnership | null;
  request: SendRequestOwnership;
  currentViewThreadId?: string | null;
  isDeletedThread?: boolean;
  visibleOnly?: boolean;
}) {
  if (isDeletedThread || !isSameSendRequest(activeRequest, request)) {
    return false;
  }
  if (!visibleOnly) {
    return true;
  }
  return Boolean(
    currentViewThreadId &&
    (currentViewThreadId === request.threadId ||
      currentViewThreadId === request.displayThreadId),
  );
}

type CreateThreadForUpload = (
  requestedThreadId: string,
) => Promise<string | { thread_id?: unknown } | null | undefined>;

function createdThreadId(
  result: Awaited<ReturnType<CreateThreadForUpload>>,
  fallbackThreadId: string,
) {
  if (typeof result === "string" && result.length > 0) {
    return result;
  }
  if (
    result &&
    typeof result === "object" &&
    typeof result.thread_id === "string" &&
    result.thread_id.length > 0
  ) {
    return result.thread_id;
  }
  return fallbackThreadId;
}

export async function uploadPromptFilesForThreadSend({
  threadId,
  backendThreadId,
  fileParts,
  createThread,
  upload,
  shouldContinue = () => true,
}: {
  threadId: string | null | undefined;
  backendThreadId?: string | null;
  fileParts: PromptInputMessage["files"];
  createThread: CreateThreadForUpload;
  upload: (threadId: string, files: File[]) => Promise<UploadResponse>;
  shouldContinue?: () => boolean;
}): Promise<{ threadId: string; files: UploadedFileInfo[] } | null> {
  if (!threadId || threadId === "new") {
    throw new Error("Thread is not ready for file upload.");
  }

  const conversionResults = await Promise.all(
    fileParts.map((fileUIPart) => promptInputFilePartToFile(fileUIPart)),
  );
  const files = conversionResults.filter((file): file is File => file !== null);
  const failedConversions = conversionResults.length - files.length;

  if (failedConversions > 0) {
    throw new Error(
      `Failed to prepare ${failedConversions} attachment(s) for upload. Please retry.`,
    );
  }

  if (!shouldContinue()) {
    return null;
  }

  const uploadThreadId =
    backendThreadId === threadId
      ? threadId
      : createdThreadId(await createThread(threadId), threadId);

  if (!shouldContinue()) {
    return null;
  }

  const uploadResponse = await upload(uploadThreadId, files);
  if (!shouldContinue()) {
    return null;
  }
  if (!uploadResponse.success || uploadResponse.skipped_files.length > 0) {
    throw new Error(uploadResponse.message || "Failed to upload files.");
  }

  return { threadId: uploadThreadId, files: uploadResponse.files };
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

export async function renameThreadRemote({
  threadId,
  title,
  apiClient,
}: {
  threadId: string;
  title: string;
  apiClient: ReturnType<typeof getAPIClient>;
}) {
  await apiClient.threads.updateState(threadId, {
    values: { title },
  });
  setManualThreadTitleLock(threadId, title);
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

export {
  applyTaskEventRunMessages,
  applyTaskEventToSubtask,
  asRunTerminalEvent,
  asTaskEvent,
  isTaskEventRunMessage,
  isTaskEventRunMessageForRequest,
  resolveVisibleTaskRunningThreadId,
  taskEventRunMessageKey,
  taskLaneSubtaskUpdate,
  TASK_EVENT_SCHEMA_VERSION,
} from "./task-events";
export {
  applySnapshotRunMessagePageState,
  buildVisibleHistoryMessages,
  completeOptimisticUploadMessages,
  findLatestUnloadedRunIndex,
  getNextRunMessagesBeforeSeq,
  getOldestRunMessageSeq,
  getSupersededRunIds,
  getSummarizationMiddlewareMessages,
  getThreadMessagesWithLiveSnapshot,
  getVisibleOptimisticMessages,
  HISTORY_CREATED_AT_KEY,
  isAbortError,
  isVisibleHistoryRunMessage,
  MAX_CONSECUTIVE_EMPTY_RUN_LOADS,
  mergeFetchedRunMessages,
  mergeMessages,
  mergeSnapshotRunMessages,
  partitionKnownRunIds,
  removeSetItems,
  resetLoadedRunStateForRefresh,
  runMessagesPageHasMore,
  shouldAutoContinueOnEmptyRun,
  shouldAutoContinueRunHistory,
  shouldAutoLoadLatestRun,
  shouldShowLiveThreadState,
  shouldShowThreadHistory,
} from "./message-history";

export {
  getTerminalTransitionRunIds,
  isActiveRunStatus,
  isTerminalRunStatus,
} from "./run-status";

type RunWithTerminalFields = Run & {
  error?: unknown;
  terminal_reason?: unknown;
};

type SettleRunSubtasks = (terminal: {
  threadId: string;
  runId: string;
  roundId?: string | null;
  status: string;
  terminalReason?: string;
}) => void;

type TerminalRunSettlementOptions = {
  roundId?: string | null;
  terminalReason?: string;
  settleRunSubtasks?: SettleRunSubtasks;
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
    roundId: roundIdOfRun(run),
    status,
    terminalReason: stringValue(runWithTerminal.terminal_reason) ?? status,
  });
}

export type {
  RuntimeRoundSnapshot,
  TaskLaneSnapshot,
} from "./command-room-read-model";

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

export function threadRuntimeSnapshotQueryKey(threadId?: string | null) {
  return queryKeys.thread.runtimeSnapshot(threadId);
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

const THREAD_RUNS_PAGE_SIZE = 100;

function buildThreadRunsUrl(baseUrl: string, threadId: string, before: string) {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");
  const path = `/api/threads/${encodeURIComponent(threadId)}/runs`;
  const url = new URL(
    `${normalizedBaseUrl}${path}`,
    typeof window !== "undefined" ? window.location.origin : "http://localhost",
  );
  url.searchParams.set("limit", String(THREAD_RUNS_PAGE_SIZE));
  url.searchParams.set("before", before);
  return normalizedBaseUrl ? url.toString() : `${url.pathname}${url.search}`;
}

async function readThreadRunsPageResponse(response: Response): Promise<Run[]> {
  const fallback = "Failed to load older thread runs.";
  if (!response.ok) {
    const error = new Error(await readResponseErrorMessage(response, fallback));
    Object.defineProperty(error, "status", {
      value: response.status,
      enumerable: true,
    });
    throw error;
  }
  try {
    return (await response.json()) as Run[];
  } catch {
    throw new Error(fallback);
  }
}

export function buildThreadRunCancelUrl(
  baseUrl: string,
  threadId: string,
  runId: string,
  {
    action = "interrupt",
    wait = false,
  }: { action?: "interrupt" | "rollback"; wait?: boolean } = {},
) {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");
  const path = `/api/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(
    runId,
  )}/cancel`;
  const url = new URL(
    `${normalizedBaseUrl}${path}`,
    typeof window !== "undefined" ? window.location.origin : "http://localhost",
  );
  url.searchParams.set("action", action);
  url.searchParams.set("wait", wait ? "1" : "0");
  return normalizedBaseUrl ? url.toString() : `${url.pathname}${url.search}`;
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

export function upsertThreadInSearchCache(
  queryClient: QueryClient,
  thread: AgentThread,
) {
  if (isDeletedThreadTombstoned(thread.thread_id)) {
    return;
  }
  queryClient.setQueriesData(
    {
      queryKey: queryKeys.threads.search(),
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
  scope?: ThreadActivityOwnerScope,
) {
  if (isDeletedThreadTombstoned(threadId)) {
    return;
  }
  markThreadActivityRunning(threadId, scope);
  queryClient.setQueriesData(
    {
      queryKey: queryKeys.threads.search(),
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
  if (
    INACTIVE_THREAD_STATUSES.has(status) &&
    hasActiveThreadActivity(threadId)
  ) {
    return;
  }
  const nextStatus = status as AgentThread["status"];
  queryClient.setQueriesData(
    {
      queryKey: queryKeys.threads.search(),
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
  const roundId =
    typeof options.roundId === "string" && options.roundId.length > 0
      ? options.roundId
      : undefined;
  options.settleRunSubtasks?.({
    threadId,
    runId,
    ...(roundId ? { roundId } : {}),
    status: terminalStatus,
    terminalReason: options.terminalReason ?? terminalStatus,
  });
  clearReconnectRun(threadId, runId);
  const scope = { runId };
  if (status === "success") {
    markThreadFinished(threadId, scope);
  } else {
    clearThreadActivity(threadId, scope);
  }
  setThreadStatusInCaches(queryClient, threadId, threadStatus);
  invalidateTerminalRunQueries(queryClient, threadId);
  return true;
}

export function markThreadCancellingInCaches(
  queryClient: QueryClient,
  threadId: string,
  scope?: ThreadActivityOwnerScope,
) {
  markThreadBusyInCaches(queryClient, threadId, scope);
  setThreadStatusInCaches(queryClient, threadId, "cancelling");
  void queryClient.invalidateQueries({
    queryKey: threadRunsQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadRuntimeSnapshotQueryKey(threadId),
  });
}

export function beginLocalRunCancellation({
  queryClient,
  threadId,
  runId,
}: {
  queryClient: QueryClient;
  threadId: string | null | undefined;
  runId: string | null | undefined;
}): StreamErrorRecoveryRun | null {
  if (!threadId || !runId) {
    if (threadId) {
      clearThreadActivity(threadId);
    }
    return null;
  }
  markThreadCancellingInCaches(queryClient, threadId, { runId });
  return { threadId, runId };
}

export async function requestThreadRunCancel({
  threadId,
  runId,
  action = "interrupt",
  wait = false,
}: {
  threadId: string;
  runId: string;
  action?: "interrupt" | "rollback";
  wait?: boolean;
}): Promise<{ status: number; detail?: string }> {
  const response = await fetch(
    buildThreadRunCancelUrl(getBackendBaseURL(), threadId, runId, {
      action,
      wait,
    }),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
    },
  );
  if (response.status === 202 || response.status === 204) {
    return { status: response.status };
  }
  if (response.status === 409) {
    return {
      status: response.status,
      detail: await readResponseErrorMessage(
        response,
        "Run cancellation requires recovery.",
      ),
    };
  }
  if (!response.ok) {
    const error = new Error(
      await readResponseErrorMessage(response, "Failed to cancel run."),
    );
    Object.defineProperty(error, "status", {
      value: response.status,
      enumerable: true,
    });
    throw error;
  }
  return { status: response.status };
}

export function finishLocalRunCancellation({
  queryClient,
  threadId,
  runId,
  settleRunSubtasks,
}: {
  queryClient: QueryClient;
  threadId: string | null | undefined;
  runId: string | null | undefined;
  settleRunSubtasks?: SettleRunSubtasks;
}): StreamErrorRecoveryRun | null {
  if (!threadId || !runId) {
    if (threadId) {
      clearThreadActivity(threadId);
    }
    return null;
  }
  stopBackgroundRunProbe(threadId, runId);
  applyBackgroundRunProbeResult(queryClient, threadId, runId, "interrupted", {
    settleRunSubtasks,
    terminalReason: "user_cancelled",
  });
  return { threadId, runId };
}

function runStatus(run: unknown) {
  return typeof run === "object" && run !== null
    ? Reflect.get(run, "status")
    : undefined;
}

function runTerminalReason(run: unknown) {
  if (typeof run !== "object" || run === null) {
    return undefined;
  }
  const reason = Reflect.get(run, "terminal_reason");
  return typeof reason === "string" ? reason : undefined;
}

export function reconcileRunCancellationAuthority({
  queryClient,
  threadId,
  runId,
  run,
  isMock,
  settleRunSubtasks,
}: {
  queryClient: QueryClient;
  threadId: string | null | undefined;
  runId: string | null | undefined;
  run: unknown;
  isMock?: boolean;
  settleRunSubtasks?: SettleRunSubtasks;
}): "terminal" | "active" | "unknown" {
  if (!threadId || !runId) {
    return "unknown";
  }
  if (isDeletedThreadTombstoned(threadId)) {
    stopBackgroundRunProbe(threadId, runId);
    return "terminal";
  }
  const status = runStatus(run);
  if (isTerminalRunStatus(status)) {
    stopBackgroundRunProbe(threadId, runId);
    applyBackgroundRunProbeResult(queryClient, threadId, runId, status, {
      settleRunSubtasks,
      roundId: roundIdOfRun(run as Run | undefined),
      terminalReason: runTerminalReason(run),
    });
    return "terminal";
  }
  if (isActiveRunStatus(status)) {
    markThreadCancellingInCaches(queryClient, threadId, { runId });
    startBackgroundRunProbe({
      queryClient,
      threadId,
      runId,
      isMock,
      settleRunSubtasks,
    });
    return "active";
  }
  return "unknown";
}

export function keepRunCancellationRecovering({
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
  const cancellation = beginLocalRunCancellation({
    queryClient,
    threadId,
    runId,
  });
  if (!cancellation) {
    return null;
  }
  startBackgroundRunProbe({
    queryClient,
    threadId: cancellation.threadId,
    runId: cancellation.runId,
    isMock,
    settleRunSubtasks,
  });
  return cancellation;
}

export async function reconcileRunCancellationFromAuthority({
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
}): Promise<"terminal" | "active" | "unknown"> {
  try {
    const run = await getAPIClient(isMock).runs.get(threadId, runId);
    const result = reconcileRunCancellationAuthority({
      queryClient,
      threadId,
      runId,
      run,
      isMock,
      settleRunSubtasks,
    });
    if (result !== "unknown") {
      return result;
    }
  } catch {
    // Fall through to runtime snapshot; it is the canonical recovery source.
  }

  try {
    const snapshot = await fetch(
      buildThreadRuntimeSnapshotUrl(getBackendBaseURL(), threadId),
      {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
      },
    ).then(readThreadRuntimeSnapshotResponse);
    queryClient.setQueryData(threadRuntimeSnapshotQueryKey(threadId), snapshot);
    const run = snapshot.runs?.find((item) => item.run_id === runId);
    return reconcileRunCancellationAuthority({
      queryClient,
      threadId,
      runId,
      run,
      isMock,
      settleRunSubtasks,
    });
  } catch {
    return "unknown";
  }
}

export function applyStreamErrorRecovery({
  queryClient,
  threadId,
  runId,
  runtimeOwnerId,
  isMock,
  settleRunSubtasks,
}: {
  queryClient: QueryClient;
  threadId: string | null | undefined;
  runId: string | null | undefined;
  runtimeOwnerId?: string | null | undefined;
  isMock?: boolean;
  settleRunSubtasks?: SettleRunSubtasks;
}): StreamErrorRecoveryRun | null {
  if (!threadId || !runId) {
    if (threadId) {
      clearThreadActivity(threadId);
    }
    return null;
  }
  markThreadBusyInCaches(queryClient, threadId, { runId, runtimeOwnerId });
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
  return { threadId, runId, ...(runtimeOwnerId ? { runtimeOwnerId } : {}) };
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
  if (isDeletedThreadTombstoned(threadId)) {
    return true;
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
  { applyThreadSideEffects = true }: { applyThreadSideEffects?: boolean } = {},
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
    applyThreadSideEffects &&
    !applyBackgroundRunProbeResult(
      queryClient,
      runTerminalEvent.thread_id,
      runTerminalEvent.run_id,
      runTerminalEvent.status,
      {
        settleRunSubtasks,
        roundId: runTerminalEvent.round_id,
        terminalReason: runTerminalEvent.terminal_reason,
      },
    )
  ) {
    invalidateTerminalRunQueries(queryClient, runTerminalEvent.thread_id);
  }
  if (!applyThreadSideEffects) {
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
  void queryClient.invalidateQueries({
    queryKey: queryKeys.threads.search(),
  });
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

export function stopBackgroundRunProbeRecovery(
  queryClient: QueryClient,
  threadId: string,
  runId?: string | null,
) {
  clearThreadActivity(threadId, { runId });
  void queryClient.invalidateQueries({
    queryKey: threadRunsQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: threadRuntimeSnapshotQueryKey(threadId),
  });
  void queryClient.invalidateQueries({
    queryKey: queryKeys.threads.search(),
  });
  void queryClient.invalidateQueries({
    queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
  });
}

export function shouldKeepStreamErrorRecoveryRun(
  recoveryRun: StreamErrorRecoveryOwner | null,
  activity: ThreadActivitySnapshot,
) {
  if (!recoveryRun) {
    return false;
  }
  if (recoveryRun.runId || recoveryRun.runtimeOwnerId) {
    return hasActiveThreadActivityOwner(recoveryRun.threadId, recoveryRun);
  }
  return activity.running.has(recoveryRun.threadId);
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
    stopBackgroundRunProbeRecovery(queryClient, threadId, runId);
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
          stopBackgroundRunProbeRecovery(queryClient, threadId, runId);
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

function isThreadMissingError(error: unknown): boolean {
  const status = getHttpStatus(error);
  // Treat 403 like 404 here to avoid disclosing whether an inaccessible thread
  // exists; callers render an empty state without changing the browser route.
  return status === 403 || status === 404;
}

export function isThreadDeleteNotFound(error: unknown): boolean {
  return getHttpStatus(error) === 404;
}

export async function deleteThreadRemote({
  threadId,
  apiClient,
  onRemoteDeleted,
}: {
  threadId: string;
  apiClient: ReturnType<typeof getAPIClient>;
  onRemoteDeleted?: () => void;
}) {
  let sdkDeleteConfirmed = false;
  try {
    await apiClient.threads.delete(threadId);
    sdkDeleteConfirmed = true;
  } catch (error) {
    if (!isThreadDeleteNotFound(error)) {
      throw error;
    }
    sdkDeleteConfirmed = true;
  }
  onRemoteDeleted?.();

  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}`,
    {
      method: "DELETE",
    },
  );

  if (response.ok || (response.status === 404 && sdkDeleteConfirmed)) {
    return;
  }

  const error = await response
    .json()
    .catch(() => ({ detail: "Failed to delete local thread data." }));
  throw new Error(error.detail ?? "Failed to delete local thread data.");
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
  const [locallySettledRun, setLocallySettledRunState] =
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
  const streamOwnerSnapshotRef = useRef<ThreadRuntimeOwnerSnapshot | null>(
    createThreadRuntimeOwnerSnapshot({
      threadId: threadId ?? null,
      runId: null,
      runtimeOwnerId: currentRuntimeOwnerId,
      displayThreadId: currentViewThreadId,
    }),
  );
  const streamClientThreadIdLockedRef = useRef(false);
  const liveMessagesSnapshotRef = useRef<LiveMessagesSnapshot | null>(null);
  const streamErrorRecoveryRunRef = useRef<StreamErrorRecoveryRun | null>(null);
  const locallySettledRunRef = useRef<StreamErrorRecoveryRun | null>(null);
  const startedRef = useRef(false);
  const activeSendRequestRef = useRef<SendRequestOwnership | null>(null);
  const queuedReleasePausedRef = useRef(false);
  const queuedReleaseInFlightRef = useRef(false);
  const pendingUsageBaselineMessageIdsRef = useRef<Set<string>>(new Set());
  const listeners = useRef({
    onSend,
    onStart,
    onFinish,
    onToolEnd,
  });

  const getCurrentRuntimeOwner = useCallback(
    () =>
      streamOwnerSnapshotRef.current ??
      createThreadRuntimeOwnerSnapshot({
        threadId: streamThreadIdRef.current,
        runId: streamRunIdRef.current,
        runtimeOwnerId: currentRuntimeOwnerIdRef.current,
        displayThreadId: currentViewThreadIdRef.current,
      }),
    [],
  );

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
  const setLocallySettledRun = useCallback(
    (next: StreamErrorRecoveryRun | null) => {
      locallySettledRunRef.current = next;
      setLocallySettledRunState(next);
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

  const commitVisibleStreamStart = useCallback(
    (startedThreadId: string, startedRunId: string | null) => {
      const currentView = currentViewThreadIdRef.current;
      const streamStillOwnsVisibleChat =
        currentView === startedThreadId ||
        optimisticThreadIdRef.current === currentView ||
        liveMessagesThreadIdRef.current === currentView;
      if (!startedRef.current && streamStillOwnsVisibleChat) {
        listeners.current.onStart?.(startedThreadId, startedRunId);
        startedRef.current = true;
      }
    },
    [],
  );

  useEffect(() => {
    const normalizedThreadId = threadId ?? null;
    const preserveRuntimeOwner = shouldPreserveRuntimeOwnerOnRouteSwitch({
      currentOwner: getCurrentRuntimeOwner(),
      nextDisplayThreadId: currentViewThreadIdRef.current,
      streamFinished: streamFinishedRef.current,
      sendInFlight: sendInFlightRef.current,
    });
    if (!normalizedThreadId) {
      // Reset when the UI moves back to a brand new unsaved thread.
      if (!preserveRuntimeOwner) {
        startedRef.current = false;
      }
      setOnStreamThreadId(normalizedThreadId);
    } else {
      setOnStreamThreadId(normalizedThreadId);
    }
    if (!streamClientThreadIdLockedRef.current) {
      setStreamClientThreadId(normalizedThreadId);
    }
    if (!preserveRuntimeOwner) {
      streamThreadIdRef.current = normalizedThreadId;
      streamOwnerSnapshotRef.current = createThreadRuntimeOwnerSnapshot({
        threadId: normalizedThreadId,
        runId: null,
        runtimeOwnerId: currentRuntimeOwnerIdRef.current,
        displayThreadId: currentViewThreadIdRef.current,
      });
    }
  }, [getCurrentRuntimeOwner, threadId]);

  const handleStreamStart = useCallback(
    (_threadId: string, _runId: string | null) => {
      streamThreadIdRef.current = _threadId;
      streamRunIdRef.current = _runId;
      streamOwnerSnapshotRef.current = createThreadRuntimeOwnerSnapshot({
        threadId: _threadId,
        runId: _runId,
        runtimeOwnerId: currentRuntimeOwnerIdRef.current,
        displayThreadId: currentViewThreadIdRef.current,
      });
      setStreamErrorRecoveryRun(null);
      setLocallySettledRun(null);
      setOptimisticThreadId((currentOptimisticThreadId) => {
        const currentView = currentViewThreadIdRef.current;
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
        const currentView = currentViewThreadIdRef.current;
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
      commitVisibleStreamStart(_threadId, _runId);
      setOnStreamThreadId(_threadId);
    },
    [commitVisibleStreamStart, setLocallySettledRun, setStreamErrorRecoveryRun],
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
      const currentOwner = getCurrentRuntimeOwner();
      if (
        isDeletedThreadTombstoned(createdThreadId) ||
        !shouldClaimThreadRuntimeOwner({
          eventThreadId: createdThreadId,
          eventRunId: streamRunIdRef.current,
          currentOwner,
        })
      ) {
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
      streamOwnerSnapshotRef.current = createThreadRuntimeOwnerSnapshot({
        ...currentOwner,
        threadId: createdThreadId,
        runId: streamRunIdRef.current,
      });
      setOnStreamThreadId(createdThreadId);
      for (const previousId of previousIds) {
        clearThreadActivity(previousId);
      }
      markThreadBusyInCaches(queryClient, createdThreadId, {
        runId: streamRunIdRef.current,
        runtimeOwnerId: currentRuntimeOwnerIdRef.current,
      });
    },
    onCreated(meta) {
      if (
        isDeletedThreadTombstoned(meta.thread_id) ||
        !shouldClaimThreadRuntimeOwner({
          eventThreadId: meta.thread_id,
          eventRunId: meta.run_id,
          currentOwner: getCurrentRuntimeOwner(),
        })
      ) {
        return;
      }
      handleStreamStart(meta.thread_id, meta.run_id);
      markThreadBusyInCaches(queryClient, meta.thread_id, {
        runId: meta.run_id,
        runtimeOwnerId: currentRuntimeOwnerIdRef.current,
      });
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
        if (isDeletedThreadTombstoned(getCurrentRuntimeOwner().threadId)) {
          return;
        }
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
            isDeletedThreadTombstoned(eventThreadId ?? streamThreadId) ||
            !shouldApplyStreamTitleUpdate({
              eventThreadId,
              eventRunId,
              streamThreadId,
              streamRunId: streamRunIdRef.current,
              runtimeOwnerId: currentRuntimeOwnerIdRef.current,
              displayThreadId: currentViewThreadIdRef.current,
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
              queryKey: queryKeys.threads.search(),
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
        const eventThreadId = stringValue(taskEvent.thread_id) ?? null;
        const eventRunId = stringValue(taskEvent.run_id) ?? null;
        if (
          isDeletedThreadTombstoned(eventThreadId) ||
          !isCurrentThreadRuntimeOwnerEvent({
            eventThreadId,
            eventRunId,
            currentOwner: getCurrentRuntimeOwner(),
            requireEventThreadId: true,
            requireEventRunId: true,
            allowMissingCurrentRunId: true,
          })
        ) {
          return;
        }
        const taskThreadId = resolveVisibleTaskRunningThreadId({
          eventThreadId,
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
      const terminalOwnsCurrentStream = terminalEvent
        ? isCurrentThreadRuntimeOwnerEvent({
            eventThreadId: terminalEvent.thread_id,
            eventRunId: terminalEvent.run_id,
            currentOwner: getCurrentRuntimeOwner(),
            requireEventThreadId: true,
            requireEventRunId: true,
          })
        : false;
      if (
        terminalEvent &&
        !isDeletedThreadTombstoned(terminalEvent.thread_id) &&
        reconcileTerminalRunHistory(
          queryClient,
          terminalEvent,
          refreshHistoryRuns,
          terminalOwnsCurrentStream ? settleRunSubtasks : undefined,
          { applyThreadSideEffects: terminalOwnsCurrentStream },
        )
      ) {
        if (terminalOwnsCurrentStream) {
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
        if (
          isSameStreamErrorRecoveryRun(
            locallySettledRunRef.current,
            terminalEvent.thread_id,
            terminalEvent.run_id,
          )
        ) {
          setLocallySettledRun(null);
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
      const recoveryOwner = resolveRunStreamRecoveryErrorOwner(
        error,
        run?.thread_id ?? streamThreadIdRef.current,
        run?.run_id ?? streamRunIdRef.current,
      );
      const streamThreadId =
        recoveryOwner?.threadId ?? run?.thread_id ?? streamThreadIdRef.current;
      const streamRunId =
        recoveryOwner?.runId ?? run?.run_id ?? streamRunIdRef.current;
      if (isDeletedThreadTombstoned(streamThreadId)) {
        return;
      }
      const currentOwner = getCurrentRuntimeOwner();
      const errorOwnsCurrentUi = isCurrentThreadRuntimeOwnerEvent({
        eventThreadId: streamThreadId,
        eventRunId: streamRunId,
        currentOwner,
        requireEventThreadId: true,
        allowMissingEventRunId: true,
        allowMissingCurrentRunId: true,
      });
      if (errorOwnsCurrentUi) {
        releaseStreamClientThreadId(streamThreadId);
      }
      if (
        errorOwnsCurrentUi &&
        shouldCommitStreamStart({
          started: startedRef.current,
          threadId: streamThreadId,
          runId: streamRunId,
        })
      ) {
        handleStreamStart(streamThreadId!, streamRunId);
      }
      const recoveryRuntimeOwnerId = resolveStreamErrorRecoveryRuntimeOwnerId({
        eventThreadId: streamThreadId,
        eventRunId: streamRunId,
        streamOwner: streamOwnerSnapshotRef.current,
        currentOwner,
        errorOwnsCurrentUi,
      });
      const recoveryRun = applyStreamErrorRecovery({
        queryClient,
        threadId: streamThreadId,
        runId: streamRunId,
        runtimeOwnerId: recoveryRuntimeOwnerId,
        isMock,
        settleRunSubtasks,
      });
      if (
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
        setLocallySettledRun(null);
        setOptimisticMessages([]);
        setOptimisticThreadTarget(null);
        setLiveMessagesThreadTarget(null);
        setPendingSupersededRunIds(new Set());
        setPendingSupersededMessageIds(new Set());
      }
      if (errorOwnsCurrentUi && shouldShowStreamErrorToast(recoveryRun)) {
        toast.error(getStreamErrorMessage(error));
      }
      if (errorOwnsCurrentUi) {
        pendingUsageBaselineMessageIdsRef.current = new Set(
          messagesRef.current
            .map(messageIdentity)
            .filter((id): id is string => Boolean(id)),
        );
      }
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
      const finishEventThreadId = run?.thread_id ?? null;
      const finishEventRunId = run?.run_id ?? null;
      const finishMeta = resolveThreadStreamFinishMeta({
        run,
        streamOwner: streamOwnerSnapshotRef.current,
      });
      const streamThreadId = finishMeta.threadId;
      const streamRunId = finishMeta.runId;
      if (isDeletedThreadTombstoned(streamThreadId)) {
        return;
      }
      const finishOwnsCurrentStream = shouldRunCurrentStreamFinishSideEffects({
        eventThreadId: finishEventThreadId ?? streamThreadId,
        eventRunId: finishEventRunId ?? streamRunId,
        streamThreadId: streamThreadIdRef.current,
        streamRunId: streamRunIdRef.current,
        runtimeOwnerId: currentRuntimeOwnerIdRef.current,
        displayThreadId: currentViewThreadIdRef.current,
      });
      if (finishOwnsCurrentStream && !startedRef.current && streamThreadId) {
        handleStreamStart(streamThreadId, streamRunId);
      }
      const finishSideEffectThreadId =
        finishEventThreadId ??
        (finishOwnsCurrentStream ? streamThreadId : null);
      const finishSideEffectRunId =
        finishEventRunId ?? (finishOwnsCurrentStream ? streamRunId : null);
      if (finishOwnsCurrentStream) {
        releaseStreamClientThreadId(streamThreadId);
      }
      if (finishSideEffectThreadId && finishSideEffectRunId) {
        clearReconnectRun(finishSideEffectThreadId, finishSideEffectRunId);
        stopBackgroundRunProbe(finishSideEffectThreadId, finishSideEffectRunId);
      }
      if (
        (finishOwnsCurrentStream && !streamErrorRecoveryRunRef.current) ||
        isSameStreamErrorRecoveryRun(
          streamErrorRecoveryRunRef.current,
          finishSideEffectThreadId,
          finishSideEffectRunId,
        )
      ) {
        setStreamErrorRecoveryRun(null);
      }
      if (
        isSameStreamErrorRecoveryRun(
          locallySettledRunRef.current,
          finishSideEffectThreadId,
          finishSideEffectRunId,
        )
      ) {
        setLocallySettledRun(null);
      }
      if (streamThreadId && finishOwnsCurrentStream) {
        markThreadFinished(streamThreadId, {
          runId: streamRunId,
          runtimeOwnerId: currentRuntimeOwnerIdRef.current,
        });
        setThreadStatusInCaches(queryClient, streamThreadId, "idle");
      }
      if (finishOwnsCurrentStream) {
        streamFinishedRef.current = true;
        setQueueReleaseVersion((version) => version + 1);
        listeners.current.onFinish?.(state.values, finishMeta);
        pendingUsageBaselineMessageIdsRef.current = new Set(
          messagesRef.current
            .map(messageIdentity)
            .filter((id): id is string => Boolean(id)),
        );
      }
      if (finishSideEffectThreadId) {
        invalidateTerminalRunQueries(queryClient, finishSideEffectThreadId);
      }
    },
  });

  useEffect(() => {
    const streamThreadId = streamThreadIdRef.current;
    if (!thread.isLoading || !streamThreadId) {
      return;
    }
    markThreadBusyInCaches(queryClient, streamThreadId, {
      runId: streamRunIdRef.current,
      runtimeOwnerId: currentRuntimeOwnerIdRef.current,
    });
  }, [queryClient, thread.isLoading]);

  const hasVisibleStreamState = shouldShowLiveThreadState(
    currentViewThreadId,
    onStreamThreadId ?? null,
    liveMessagesThreadId,
  );
  const hasLocallySettledCurrentStream = isSameStreamErrorRecoveryRun(
    locallySettledRun,
    streamThreadIdRef.current,
    streamRunIdRef.current,
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
    !hasLocallySettledCurrentStream &&
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
        liveRunSettled: hasLocallySettledCurrentStream,
      }),
    [
      currentThreadMessages,
      currentViewThreadId,
      hasLocallySettledCurrentStream,
      pendingSupersededMessageIds,
    ],
  );
  const terminalRunHasVisibleAi =
    streamRunIdRef.current === terminalNotice?.runId &&
    persistedMessages.some(
      (message) => message.type === "ai" && !isHiddenFromUIMessage(message),
    );
  const visibleTerminalNotice = terminalRunHasVisibleAi ? null : terminalNotice;
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
    const preserveRuntimeOwner = shouldPreserveRuntimeOwnerOnRouteSwitch({
      currentOwner: getCurrentRuntimeOwner(),
      nextDisplayThreadId: currentViewThreadIdRef.current,
      streamFinished: streamFinishedRef.current,
      sendInFlight: sendInFlightRef.current,
    });
    if (!preserveRuntimeOwner) {
      startedRef.current = false;
      streamRunIdRef.current = null;
      activeSendRequestRef.current = null;
      sendInFlightRef.current = false;
      streamFinishedRef.current = true;
      queuedReleasePausedRef.current = false;
      queuedReleaseInFlightRef.current = false;
      queuedMessagesRef.current = [];
      setLocallySettledRun(null);
      messagesRef.current = [];
      summarizedRef.current = new Set<string>();
      pendingUsageBaselineMessageIdsRef.current = new Set();
      setPendingSupersededRunIds(new Set());
      setPendingSupersededMessageIds(new Set());
      setStreamErrorRecoveryRun(null);
      setIsUploading(false);
    }
    prevHumanMsgCountRef.current =
      latestMessageCountsRef.current.humanMessageCount;
  }, [
    getCurrentRuntimeOwner,
    setLocallySettledRun,
    setStreamErrorRecoveryRun,
    threadId,
  ]);

  useEffect(() => {
    if (
      !streamErrorRecoveryRun ||
      !hasTerminalStreamErrorRecoveryRun(streamErrorRecoveryRun, historyRuns)
    ) {
      return;
    }
    setLocallySettledRun(streamErrorRecoveryRun);
    if (
      liveMessagesSnapshotRef.current?.threadId ===
        streamErrorRecoveryRun.threadId &&
      liveMessagesSnapshotRef.current.runId === streamErrorRecoveryRun.runId
    ) {
      liveMessagesSnapshotRef.current = null;
    }
    streamFinishedRef.current = true;
    setQueueReleaseVersion((version) => version + 1);
    setStreamErrorRecoveryRun(null);
  }, [
    historyRuns,
    setLocallySettledRun,
    setStreamErrorRecoveryRun,
    streamErrorRecoveryRun,
  ]);

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

  const isCurrentStreamLocallySettled = useCallback(
    () =>
      isSameStreamErrorRecoveryRun(
        locallySettledRunRef.current,
        streamThreadIdRef.current,
        streamRunIdRef.current,
      ),
    [],
  );

  const prepareStreamOwnerForSend = useCallback((targetThreadId: string) => {
    const ownerThreadId =
      streamThreadIdRef.current === targetThreadId ? targetThreadId : null;
    startedRef.current = false;
    streamRunIdRef.current = null;
    streamOwnerSnapshotRef.current = createThreadRuntimeOwnerSnapshot({
      threadId: ownerThreadId,
      runId: null,
      runtimeOwnerId: currentRuntimeOwnerIdRef.current,
      displayThreadId: targetThreadId,
    });
  }, []);

  const sendMessage = useCallback(
    async (
      targetThreadId: string,
      message: PromptInputMessage,
      extraContext?: Record<string, unknown>,
      options?: SendMessageOptions,
    ) => {
      const text = message.text.trim();
      if (targetThreadId === "new" && message.files.length > 0) {
        toast.error("Please start the chat before adding attachments.");
        return Promise.reject(
          new Error("Attachments require a saved thread before upload."),
        );
      }
      const currentOwner = getCurrentRuntimeOwner();
      const targetThreadRecovering = Boolean(
        streamErrorRecoveryRunRef.current &&
        (streamErrorRecoveryRunRef.current.threadId === targetThreadId ||
          currentOwner.displayThreadId === targetThreadId) &&
        isCurrentThreadRuntimeOwnerEvent({
          eventThreadId: streamErrorRecoveryRunRef.current.threadId,
          eventRunId: streamErrorRecoveryRunRef.current.runId,
          currentOwner,
          requireEventThreadId: true,
          requireEventRunId: true,
        }),
      );
      if (
        shouldQueueThreadMessage({
          isLoading: thread.isLoading && !isCurrentStreamLocallySettled(),
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
            ownerId: currentRuntimeOwnerIdRef.current ?? targetThreadId,
            threadId: targetThreadId,
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
        setOptimisticThreadTarget(targetThreadId);
        setLiveMessagesThreadTarget(targetThreadId);
        markThreadBusyInCaches(queryClient, targetThreadId, {
          runId: streamRunIdRef.current,
          runtimeOwnerId: currentRuntimeOwnerIdRef.current,
        });
        listeners.current.onSend?.(targetThreadId);
        return;
      }

      if (sendInFlightRef.current) {
        return;
      }
      sendInFlightRef.current = true;
      streamClientThreadIdLockedRef.current = true;
      const sendRequest = {
        requestId: createOptimisticMessageId("send"),
        threadId: targetThreadId,
        displayThreadId: currentViewThreadIdRef.current,
        runtimeOwnerId: currentRuntimeOwnerIdRef.current,
      };
      activeSendRequestRef.current = sendRequest;
      const ownsSendRequest = () =>
        shouldApplyUploadContinuation({
          activeRequest: activeSendRequestRef.current,
          request: sendRequest,
          isDeletedThread: isDeletedThreadTombstoned(sendRequest.threadId),
        });
      const ownsVisibleSendRequest = () =>
        shouldApplyUploadContinuation({
          activeRequest: activeSendRequestRef.current,
          request: sendRequest,
          currentViewThreadId: currentViewThreadIdRef.current,
          isDeletedThread: isDeletedThreadTombstoned(sendRequest.threadId),
          visibleOnly: true,
        });

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
      setOptimisticThreadTarget(targetThreadId);
      setLiveMessagesThreadTarget(targetThreadId);
      setOptimisticMessages(newOptimistic);
      setLocallySettledRun(null);
      prepareStreamOwnerForSend(targetThreadId);
      streamFinishedRef.current = false;
      markThreadBusyInCaches(queryClient, targetThreadId, {
        runtimeOwnerId: currentRuntimeOwnerIdRef.current,
      });

      listeners.current.onSend?.(targetThreadId);

      let uploadedFileInfo: UploadedFileInfo[] = [];
      let activeThreadId = targetThreadId;
      let streamSubmitStarted = false;

      try {
        // Upload files first if any
        if (message.files && message.files.length > 0) {
          setIsUploading(true);
          try {
            const uploadResult = await uploadPromptFilesForThreadSend({
              threadId: activeThreadId,
              backendThreadId: threadId ?? null,
              fileParts: message.files,
              createThread: async (requestedThreadId) =>
                getAPIClient(isMock).threads.create({
                  threadId: requestedThreadId,
                  metadata: context.agent_name
                    ? { agent_name: context.agent_name }
                    : undefined,
                }),
              upload: uploadFiles,
              shouldContinue: ownsSendRequest,
            });
            if (!uploadResult) {
              return;
            }
            activeThreadId = uploadResult.threadId;
            sendRequest.threadId = activeThreadId;
            uploadedFileInfo = uploadResult.files;
            void queryClient.invalidateQueries({
              queryKey: uploadListQueryKey(activeThreadId),
            });
            if (activeThreadId !== targetThreadId) {
              markThreadBusyInCaches(queryClient, activeThreadId, {
                runtimeOwnerId: currentRuntimeOwnerIdRef.current,
              });
            }

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
              return completeOptimisticUploadMessages(messages, uploadedFiles);
            });
          } catch (error) {
            if (!ownsSendRequest()) {
              return;
            }
            const errorMessage =
              error instanceof Error
                ? error.message
                : "Failed to upload files.";
            if (ownsVisibleSendRequest()) {
              toast.error(errorMessage);
            }
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

        streamSubmitStarted = true;
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
            threadId: activeThreadId,
            streamSubgraphs: true,
            streamResumable: true,
            onDisconnect: "continue",
            config: {
              recursion_limit: 1000,
            },
            context: buildThreadRunContext(
              context,
              activeThreadId,
              extraContext,
            ),
          },
        );
        void queryClient.invalidateQueries({
          queryKey: queryKeys.threads.search(),
        });
        void queryClient.invalidateQueries({
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
        });
      } catch (error) {
        if (!ownsSendRequest()) {
          return;
        }
        releaseStreamClientThreadId(streamThreadIdRef.current);
        if (!streamSubmitStarted || isStaleThreadUploadError(error)) {
          streamFinishedRef.current = true;
        }
        setOptimisticMessages([]);
        setOptimisticThreadTarget(null);
        setLiveMessagesThreadTarget(null);
        setIsUploading(false);
        clearThreadActivity(targetThreadId, {
          runtimeOwnerId: currentRuntimeOwnerIdRef.current,
        });
        if (activeThreadId !== targetThreadId) {
          clearThreadActivity(activeThreadId, {
            runtimeOwnerId: currentRuntimeOwnerIdRef.current,
          });
        }
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
      threadId,
      t.inputBox.waitForCurrentResponse,
      t.uploads.uploadingFiles,
      context,
      isMock,
      queryClient,
      humanMessageCount,
      getCurrentRuntimeOwner,
      isCurrentStreamLocallySettled,
      persistedMessages,
      prepareStreamOwnerForSend,
      releaseStreamClientThreadId,
      setLocallySettledRun,
      setLiveMessagesThreadTarget,
      setOptimisticThreadTarget,
    ],
  );

  useEffect(() => {
    const next = queuedMessagesRef.current[0];
    const currentOwner = getCurrentRuntimeOwner();
    const currentThreadRecovering = Boolean(
      streamErrorRecoveryRun &&
      currentViewThreadId &&
      (streamErrorRecoveryRun.threadId === currentViewThreadId ||
        currentOwner.displayThreadId === currentViewThreadId) &&
      isCurrentThreadRuntimeOwnerEvent({
        eventThreadId: streamErrorRecoveryRun.threadId,
        eventRunId: streamErrorRecoveryRun.runId,
        currentOwner,
        requireEventThreadId: true,
        requireEventRunId: true,
      }),
    );
    if (
      !shouldReleaseQueuedThreadMessage({
        streamFinished: streamFinishedRef.current,
        sendInFlight: sendInFlightRef.current,
        recovering: currentThreadRecovering,
        queuedOwnerId: next?.ownerId,
        currentOwnerId: currentOwner.runtimeOwnerId,
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
    getCurrentRuntimeOwner,
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
      setLocallySettledRun(null);
      markThreadBusyInCaches(queryClient, threadId, {
        runtimeOwnerId: currentRuntimeOwnerIdRef.current,
      });
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

        prepareStreamOwnerForSend(threadId);
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
        void queryClient.invalidateQueries({
          queryKey: queryKeys.threads.search(),
        });
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
        clearThreadActivity(threadId, {
          runtimeOwnerId: currentRuntimeOwnerIdRef.current,
        });
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
      prepareStreamOwnerForSend,
      queryClient,
      releaseStreamClientThreadId,
      setLocallySettledRun,
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
  const currentOwnerForRecovery = getCurrentRuntimeOwner();
  const hasVisibleStreamErrorRecovery = Boolean(
    streamErrorRecoveryRun &&
    currentViewThreadId &&
    (streamErrorRecoveryRun.threadId === currentViewThreadId ||
      currentOwnerForRecovery.displayThreadId === currentViewThreadId) &&
    isCurrentThreadRuntimeOwnerEvent({
      eventThreadId: streamErrorRecoveryRun.threadId,
      eventRunId: streamErrorRecoveryRun.runId,
      currentOwner: currentOwnerForRecovery,
      requireEventThreadId: true,
      requireEventRunId: true,
    }),
  );
  const effectiveThreadIsLoading =
    thread.isLoading && !hasLocallySettledCurrentStream;

  const mergedMessages = mergeMessages(
    visibleHistory,
    persistedMessages,
    visibleOptimisticMessages,
  );
  const pendingUsageMessages = effectiveThreadIsLoading
    ? getMessagesAfterBaseline(
        persistedMessages,
        pendingUsageBaselineMessageIdsRef.current,
      )
    : [];

  const stopCurrentStream = useCallback(async () => {
    const owner = getCurrentRuntimeOwner();
    const stopThreadId = owner?.threadId ?? streamThreadIdRef.current;
    const stopRunId = owner?.runId ?? streamRunIdRef.current;
    if (isDeletedThreadTombstoned(stopThreadId)) {
      return;
    }
    const cancellation = beginLocalRunCancellation({
      queryClient,
      threadId: stopThreadId,
      runId: stopRunId,
    });
    const ownsCurrentStream = isCurrentThreadRuntimeOwnerEvent({
      eventThreadId: stopThreadId,
      eventRunId: stopRunId,
      currentOwner: owner,
      requireEventThreadId: true,
      requireEventRunId: true,
    });
    const ownsCancellationNow = () =>
      Boolean(
        cancellation &&
        isCurrentThreadRuntimeOwnerEvent({
          eventThreadId: cancellation.threadId,
          eventRunId: cancellation.runId,
          eventRuntimeOwnerId: owner?.runtimeOwnerId,
          currentOwner: getCurrentRuntimeOwner(),
          requireEventThreadId: true,
          requireEventRunId: true,
        }),
      );

    if (cancellation && ownsCurrentStream) {
      activeSendRequestRef.current = null;
      sendInFlightRef.current = false;
      setStreamErrorRecoveryRun(cancellation);
      setLocallySettledRun(null);
      releaseStreamClientThreadId(cancellation.threadId);
    }

    if (cancellation) {
      clearReconnectRun(cancellation.threadId, cancellation.runId);
    }

    try {
      await thread.stop();
    } catch (error) {
      if (!cancellation) {
        throw error;
      }
    }

    if (!cancellation) {
      return;
    }

    let cancelResult: { status: number; detail?: string } | null = null;
    try {
      cancelResult = await requestThreadRunCancel({
        threadId: cancellation.threadId,
        runId: cancellation.runId,
      });
    } catch (error) {
      if (ownsCancellationNow()) {
        keepRunCancellationRecovering({
          queryClient,
          threadId: cancellation.threadId,
          runId: cancellation.runId,
          isMock,
          settleRunSubtasks,
        });
        setStreamErrorRecoveryRun(cancellation);
        toast.error(getStreamErrorMessage(error));
      }
      return;
    }

    if (!ownsCancellationNow()) {
      return;
    }

    if (cancelResult.status === 204 || cancelResult.status === 409) {
      const reconciliation = await reconcileRunCancellationFromAuthority({
        queryClient,
        threadId: cancellation.threadId,
        runId: cancellation.runId,
        isMock,
        settleRunSubtasks,
      });
      if (!ownsCancellationNow()) {
        return;
      }
      if (reconciliation === "terminal") {
        streamFinishedRef.current = true;
        setStreamErrorRecoveryRun(null);
        setLocallySettledRun(null);
        setQueueReleaseVersion((version) => version + 1);
      } else {
        keepRunCancellationRecovering({
          queryClient,
          threadId: cancellation.threadId,
          runId: cancellation.runId,
          isMock,
          settleRunSubtasks,
        });
        setStreamErrorRecoveryRun(cancellation);
        if (cancelResult.status === 409 && reconciliation === "unknown") {
          toast.error(
            cancelResult.detail ?? "Run cancellation requires recovery.",
          );
        }
      }
    } else {
      keepRunCancellationRecovering({
        queryClient,
        threadId: cancellation.threadId,
        runId: cancellation.runId,
        isMock,
        settleRunSubtasks,
      });
      setStreamErrorRecoveryRun(cancellation);
    }

    refreshHistoryRuns({
      threadId: cancellation.threadId,
      runIds: [cancellation.runId],
    });
  }, [
    getCurrentRuntimeOwner,
    isMock,
    queryClient,
    refreshHistoryRuns,
    releaseStreamClientThreadId,
    setLocallySettledRun,
    setStreamErrorRecoveryRun,
    settleRunSubtasks,
    thread,
  ]);

  // Merge history, live stream, and optimistic messages for display
  // History messages may overlap with thread.messages; thread.messages take precedence
  const mergedThread = {
    ...thread,
    stop: stopCurrentStream,
    isLoading: effectiveThreadIsLoading || hasVisibleStreamErrorRecovery,
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
      : visibleTerminalNotice
        ? ({
            state: "terminal",
            reason:
              visibleTerminalNotice.terminalReason ??
              visibleTerminalNotice.error ??
              visibleTerminalNotice.status,
          } as const)
        : streamErrorRecoveryRun === null &&
            !effectiveThreadIsLoading &&
            thread.error === undefined
          ? null
          : null;

  return {
    thread: mergedThread,
    historyRuns,
    pendingUsageMessages,
    sendMessage,
    regenerateMessage,
    isUploading,
    isHistoryLoading,
    historyError,
    terminalNotice: visibleTerminalNotice,
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

function runsForHistoryScope(runs: Run[], runId: string | null) {
  return runId ? runs.filter((run) => run.run_id === runId) : runs;
}

export {
  applyNativeRoundsToSnapshotRuns,
  buildCommandRoomReadModel,
  latestRoundIdFromSnapshot,
  mergeRunsWithTerminalPrecedence,
  resolveThreadHistoryReset,
  roundIdOfRun,
  taskLanesForLatestRound,
} from "./command-room-read-model";

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
    enabled:
      enabled && Boolean(threadId) && !isDeletedThreadTombstoned(threadId),
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
  const snapshotRoundIdRef = useRef<string | null>(null);
  const historyScopeRunIdRef = useRef<string | null>(null);
  const updateSubtask = useUpdateSubtask();
  const settleRunSubtasks = useSettleRunningSubtasksForRun();
  const updateSubtaskRef = useRef(updateSubtask);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [messageRows, setMessageRows] = useState<RunMessage[]>([]);
  const [appendedMessages, setAppendedMessages] = useState<Message[]>([]);
  const {
    fetchNextPage: fetchNextRunPage,
    hasNextPage: hasNextRunPage,
    isError: runsIsError,
    isFetchNextPageError: nextRunPageIsError,
    isFetchingNextPage: isFetchingNextRunPage,
    refetch: refetchRuns,
  } = runs;
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
  const commandRoomReadModel = useMemo(
    () =>
      buildCommandRoomReadModel({
        threadId,
        runs: runsData,
        rounds: snapshot.data?.rounds,
        taskLanes: snapshot.data?.task_lanes,
      }),
    [runsData, snapshot.data?.rounds, snapshot.data?.task_lanes, threadId],
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
        if (isDeletedThreadTombstoned(requestThreadId)) {
          return;
        }
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
          !isCurrentThreadHistoryRequest({
            currentGeneration: loadGenerationRef.current,
            currentThreadId: threadIdRef.current,
            requestGeneration: loadGeneration,
            requestThreadId,
            tombstoned: isDeletedThreadTombstoned(requestThreadId),
          }) ||
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
        const visibleMessageCount = result.data.filter(
          isVisibleHistoryRunMessage,
        ).length;
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
              visibleMessageCount,
              consecutiveEmptyLoads,
            })
          ) {
            consecutiveEmptyLoads =
              visibleMessageCount === 0 ? consecutiveEmptyLoads + 1 : 0;
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
    if (
      !enabled ||
      data?.thread_id !== threadId ||
      isDeletedThreadTombstoned(threadId)
    ) {
      return;
    }

    const snapshotThreadChanged = threadIdRef.current !== threadId;
    loadGenerationRef.current += 1;
    historyLoadAbortRef.current?.abort();
    historyLoadAbortRef.current = null;
    threadIdRef.current = threadId;
    const snapshotRuns =
      applyNativeRoundsToSnapshotRuns(data.runs, data.rounds) ?? data.runs;
    const snapshotCommandRoomReadModel = buildCommandRoomReadModel({
      threadId,
      runs: snapshotRuns,
      rounds: data.rounds,
      taskLanes: data.task_lanes,
    });
    const latestRoundId =
      snapshotCommandRoomReadModel.activeRound?.round_id ??
      latestRoundIdFromSnapshot(snapshotRuns, data.rounds);
    const snapshotRoundChanged =
      !snapshotThreadChanged &&
      snapshotRoundIdRef.current !== null &&
      latestRoundId !== null &&
      snapshotRoundIdRef.current !== latestRoundId;
    const resetSnapshotHistory = snapshotThreadChanged || snapshotRoundChanged;
    if (snapshotThreadChanged) {
      historyScopeRunIdRef.current = null;
    } else if (snapshotRoundChanged) {
      historyScopeRunIdRef.current = snapshotRuns[0]?.run_id ?? null;
    }
    const snapshotPages = historyScopeRunIdRef.current
      ? data.run_messages.filter(
          (page) => page.run_id === historyScopeRunIdRef.current,
        )
      : data.run_messages;
    const snapshotHistoryRuns = runsForHistoryScope(
      snapshotRuns,
      historyScopeRunIdRef.current,
    );
    runsRef.current = snapshotHistoryRuns;
    indexRef.current = -1;
    pendingLoadRef.current = false;
    loadingRunIdRef.current = null;
    if (resetSnapshotHistory) {
      loadedRunIdsRef.current = new Set();
      runBeforeSeqRef.current = new Map();
    }
    activeRunIdsRef.current = new Set(
      snapshotRuns
        .filter((run) => isActiveRunStatus(run.status))
        .map((run) => run.run_id),
    );
    appliedTaskEventKeysRef.current = new Set();

    const rows = snapshotPages.flatMap((page) => page.data);
    for (const run of snapshotRuns) {
      settleTerminalRunSubtasksForThread(settleRunSubtasks, threadId, run);
    }
    applyTaskEventRunMessages(
      rows,
      updateSubtaskRef.current,
      threadId,
      appliedTaskEventKeysRef.current,
    );
    for (const lane of [
      ...snapshotCommandRoomReadModel.taskLanes,
      ...snapshotCommandRoomReadModel.legacyTaskLanes,
    ]) {
      updateSubtaskRef.current(taskLaneSubtaskUpdate(lane));
    }
    for (const page of snapshotPages) {
      pendingRefreshRunIdsRef.current.delete(page.run_id);
    }
    applySnapshotRunMessagePageState(
      snapshotPages,
      loadedRunIdsRef.current,
      runBeforeSeqRef.current,
    );
    snapshotRoundIdRef.current = latestRoundId;
    latestRoundIdRef.current = latestRoundId;
    autoLoadedLatestRunIdRef.current = snapshotRuns[0]?.run_id ?? null;
    indexRef.current = findLatestUnloadedRunIndex(
      snapshotHistoryRuns,
      loadedRunIdsRef.current,
    );
    loadingRef.current = false;
    setError(null);
    setLoading(false);
    setMessageRows((previous) =>
      resetSnapshotHistory
        ? rows
        : mergeSnapshotRunMessages(previous, snapshotPages),
    );
    if (resetSnapshotHistory) {
      setAppendedMessages([]);
    }
  }, [enabled, settleRunSubtasks, snapshot.data, threadId]);

  useEffect(() => {
    const threadChanged = threadIdRef.current !== threadId;
    const roundSourceRuns =
      snapshot.data?.runs && snapshot.data.runs.length > 0
        ? snapshot.data.runs
        : runsData;
    const authoritativeRuns =
      runsData && runsData.length > 0 ? runsData : roundSourceRuns;
    const latestRoundId = latestRoundIdFromSnapshot(
      roundSourceRuns,
      snapshot.data?.rounds,
    );
    const roundChanged =
      latestRoundIdRef.current !== null &&
      latestRoundId !== null &&
      latestRoundIdRef.current !== latestRoundId;
    const resetToCurrentRun = enabled && !threadChanged && roundChanged;
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
        historyScopeRunIdRef.current = resetToCurrentRun
          ? (authoritativeRuns?.[0]?.run_id ?? null)
          : null;
        if (!resetToCurrentRun) {
          snapshotRoundIdRef.current = null;
        }
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

    if (authoritativeRuns && authoritativeRuns.length > 0) {
      const scopedRuns = runsForHistoryScope(
        authoritativeRuns,
        historyScopeRunIdRef.current,
      );
      runsRef.current = scopedRuns;
      indexRef.current = findLatestUnloadedRunIndex(
        scopedRuns,
        loadedRunIdsRef.current,
      );
    }
    const latestRunId =
      historyScopeRunIdRef.current ?? authoritativeRuns?.[0]?.run_id ?? null;
    if (
      shouldAutoLoadLatestRun(latestRunId, autoLoadedLatestRunIdRef.current)
    ) {
      autoLoadedLatestRunIdRef.current = latestRunId;
      loadMessages().catch(() => {
        toast.error("Failed to load thread history.");
      });
    }
  }, [
    enabled,
    threadId,
    runsData,
    snapshot.data?.rounds,
    snapshot.data?.runs,
    loadMessages,
  ]);

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
    const hasUnloadedRuns =
      findLatestUnloadedRunIndex(runsRef.current, loadedRunIdsRef.current) !==
      -1;
    const action = resolveThreadRunsLoadAction({
      hasNextRunPage: Boolean(hasNextRunPage),
      hasRunsData: Boolean(runsData?.length),
      hasUnloadedRuns,
      nextPageIsError: nextRunPageIsError,
      runsIsError,
    });
    if (action === "refetch-runs") {
      const result = await refetchRuns();
      if (result.error) {
        setError(result.error);
      }
      return;
    }
    if (action === "fetch-next-page") {
      const result = await fetchNextRunPage();
      if (result.error) {
        setError(result.error);
        return;
      }
      if (
        threadIdRef.current !== threadId ||
        isDeletedThreadTombstoned(threadId)
      ) {
        return;
      }
      const nextRuns = mergeRunsWithTerminalPrecedence({
        snapshotRuns: snapshot.data?.runs,
        queriedRuns: result.data,
        rounds: snapshot.data?.rounds,
      });
      if (nextRuns && nextRuns.length > 0) {
        runsRef.current = nextRuns;
        indexRef.current = findLatestUnloadedRunIndex(
          nextRuns,
          loadedRunIdsRef.current,
        );
      }
    }
    await loadMessages();
  }, [
    fetchNextRunPage,
    hasNextRunPage,
    loadMessages,
    nextRunPageIsError,
    refetchRuns,
    runsData?.length,
    runsIsError,
    snapshot.data?.rounds,
    snapshot.data?.runs,
    threadId,
  ]);

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
    enabled &&
    hasThreadId &&
    (indexRef.current >= 0 || hasUnloadedRuns || Boolean(hasNextRunPage));
  return {
    runs: runsData,
    commandRoomReadModel,
    messages,
    terminalNotice,
    loading:
      loading || isRunsLoading || isRunsUnresolved || isFetchingNextRunPage,
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
  ...queryKeys.threads.infinite(),
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
  tombstoneDeletedThread(threadId);
  clearThreadActivity(threadId);
  manualThreadTitleLocks.delete(threadId);
  stopBackgroundRunProbesForThread(threadId);
}

export function clearAllThreadSingletonState() {
  if (typeof window !== "undefined") {
    for (const timer of backgroundRunProbeTimers.values()) {
      window.clearTimeout(timer);
    }
  }
  backgroundRunProbeTimers.clear();
  backgroundRunProbeAttempts.clear();
  threadActivityOwnersByThread.clear();
  manualThreadTitleLocks.clear();
  clearDeletedThreadTombstones();
  threadActivitySnapshot = { running: new Set(), finished: new Set() };
  emitThreadActivity();
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
    predicate: (query) => isThreadScopedQueryKey(query.queryKey, threadId),
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
  return queryKeys.thread.runs(threadId);
}

export function useThreadRuns(
  threadId?: string,
  { enabled = true }: { enabled?: boolean } = {},
) {
  const apiClient = getAPIClient();
  return useInfiniteQuery({
    queryKey: threadRunsQueryKey(threadId),
    queryFn: async ({ pageParam, signal }) => {
      if (!threadId) {
        return [];
      }
      if (pageParam === null) {
        return apiClient.runs.list(threadId, {
          limit: THREAD_RUNS_PAGE_SIZE,
          signal,
        });
      }
      const url = buildThreadRunsUrl(getBackendBaseURL(), threadId, pageParam);
      return fetch(url, {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
        signal,
      }).then(readThreadRunsPageResponse);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) =>
      lastPage.length === THREAD_RUNS_PAGE_SIZE
        ? lastPage.at(-1)?.run_id
        : undefined,
    select: (data) => data.pages.flat(),
    enabled:
      enabled && Boolean(threadId) && !isDeletedThreadTombstoned(threadId),
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
    queryKey: queryKeys.thread.metadata(threadId, isMock),
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
    enabled:
      enabled && Boolean(threadId) && !isDeletedThreadTombstoned(threadId),
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
    enabled:
      enabled && Boolean(threadId) && !isDeletedThreadTombstoned(threadId),
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
    enabled:
      enabled && Boolean(threadId) && !isDeletedThreadTombstoned(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useRunDetail(threadId: string, runId: string) {
  const apiClient = getAPIClient();
  return useQuery<Run>({
    queryKey: queryKeys.thread.run(threadId, runId),
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
      await deleteThreadRemote({
        threadId,
        apiClient,
        onRemoteDeleted: () => {
          clearDeletedThreadClientState(queryClient, threadId, {
            clearSubtasksForThread,
          });
          onRemoteDeleted?.();
        },
      });
    },
    onSuccess(_, { threadId }) {
      queryClient.setQueriesData(
        {
          queryKey: queryKeys.threads.search(),
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
      void queryClient.invalidateQueries({
        queryKey: queryKeys.threads.search(),
      });
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
      await renameThreadRemote({ threadId, title, apiClient });
    },
    onSuccess(_, { threadId, title }) {
      queryClient.setQueriesData(
        {
          queryKey: queryKeys.threads.search(),
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
