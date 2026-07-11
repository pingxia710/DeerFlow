import type { Run } from "@langchain/langgraph-sdk";

import { isRunStreamRecoveryRequiredError } from "../api";

import { isTerminalRunStatus } from "./run-status";

const PUBLIC_PROVIDER_TRANSIENT_ERROR_MESSAGE =
  "The configured LLM provider is temporarily unavailable after multiple retries. Please wait a moment and continue the conversation.";
const PROVIDER_TRANSIENT_ERROR_MARKERS = [
  "codex api stream ended without response.completed event",
  "codexstreamincompleteerror",
  "response.completed",
];
const BACKGROUND_RUN_PROBE_DELAY_MS = 5000;
const BACKGROUND_RUN_PROBE_MAX_DELAY_MS = 30000;
const BACKGROUND_RUN_PROBE_MAX_ATTEMPTS = 12;
const BACKGROUND_RUN_PROBE_STOP_STATUS_CODES = new Set([401, 403, 404]);

export type ThreadStreamFinishMeta = {
  threadId: string | null;
  runId: string | null;
};

export type ThreadStreamOwnerSnapshot = {
  threadId: string | null;
  runId: string | null;
  runtimeOwnerId?: string | null;
  displayThreadId?: string | null;
};

export type StreamErrorRecoveryRun = {
  threadId: string;
  runId: string;
  runtimeOwnerId?: string | null;
};

export function resolveRunStreamRecoveryErrorOwner(
  error: unknown,
  fallbackThreadId: string | null | undefined,
  fallbackRunId: string | null | undefined,
): StreamErrorRecoveryRun | null {
  const threadId = isRunStreamRecoveryRequiredError(error)
    ? (error.threadId ?? fallbackThreadId)
    : fallbackThreadId;
  const runId = isRunStreamRecoveryRequiredError(error)
    ? error.runId
    : fallbackRunId;
  return threadId && runId ? { threadId, runId } : null;
}

export function shouldCommitStreamStart({
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

export const shouldCommitStreamStartFromError = shouldCommitStreamStart;

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

export function getHttpStatus(error: unknown): number | undefined {
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
