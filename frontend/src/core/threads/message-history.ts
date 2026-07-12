import type { Message, Run } from "@langchain/langgraph-sdk";

import { isHiddenFromUIMessage } from "../messages/utils";
import type { FileInMessage } from "../messages/utils";

import { isTaskEventRunMessage } from "./task-events";
import type { RunMessage } from "./types";

export type RunMessagesPageResponse = {
  data: RunMessage[];
  has_more?: boolean;
  hasMore?: boolean;
};

type RuntimeSnapshotRunMessagesPage = RunMessagesPageResponse & {
  run_id: string;
};

export const HISTORY_CREATED_AT_KEY = "history_created_at";

export function isNonEmptyString(value: string | undefined): value is string {
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

export function messageIdentity(message: Message): string | undefined {
  const historyIdentity = (message as MessageWithHistoryIdentity)[
    HISTORY_IDENTITY_SYMBOL
  ];
  if (typeof historyIdentity === "string" && historyIdentity.length > 0) {
    return `history:${historyIdentity}`;
  }
  return runScopedBaseMessageIdentity(message) ?? baseMessageIdentity(message);
}

export function dedupeMessagesByIdentity(messages: Message[]): Message[] {
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

export function mergeSnapshotRunMessages(
  previous: RunMessage[],
  pages: Array<{
    run_id: string;
    data: RunMessage[];
    has_more?: boolean;
    hasMore?: boolean;
  }>,
) {
  return pages.reduce(
    (rows, page) => mergeFetchedRunMessages(rows, page.data, page.run_id, true),
    previous,
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

export type LiveMessagesSnapshot = {
  threadId: string;
  runId: string | null;
  messages: Message[];
};

export function getThreadMessagesWithLiveSnapshot({
  viewThreadId,
  threadMessages,
  liveSnapshot,
  pendingSupersededMessageIds,
  liveRunSettled = false,
}: {
  viewThreadId: string | null;
  threadMessages: Message[];
  liveSnapshot: LiveMessagesSnapshot | null;
  pendingSupersededMessageIds: ReadonlySet<string>;
  liveRunSettled?: boolean;
}): Message[] {
  if (liveRunSettled) {
    return [];
  }
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
  runs?: Run[],
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
  const runIds = new Set(
    runs
      ? [...runs].reverse().map((run) => run.run_id)
      : messageRows.map((message) => message.run_id),
  );
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
      loadedRunIds.delete(page.run_id);
      runBeforeSeq.set(page.run_id, nextBeforeSeq);
    } else if (nextBeforeSeq === null) {
      loadedRunIds.add(page.run_id);
      runBeforeSeq.delete(page.run_id);
    } else {
      loadedRunIds.delete(page.run_id);
      runBeforeSeq.delete(page.run_id);
    }
  }
}

export function getSnapshotHistoryContinuationState(
  runs: Run[],
  pages: RuntimeSnapshotRunMessagesPage[],
) {
  const pagesByRunId = new Map(pages.map((page) => [page.run_id, page]));
  let consecutiveEmptyLoads = 0;
  let visibleMessageCount = 0;

  for (const run of runs) {
    const page = pagesByRunId.get(run.run_id);
    if (!page || getNextRunMessagesBeforeSeq(page) !== null) {
      break;
    }
    visibleMessageCount = page.data.filter(isVisibleHistoryRunMessage).length;
    consecutiveEmptyLoads =
      visibleMessageCount === 0 ? consecutiveEmptyLoads + 1 : 0;
  }

  return { consecutiveEmptyLoads, visibleMessageCount };
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
      .map(runScopedBaseMessageIdentity)
      .filter(isNonEmptyString),
  );
  const liveBaseMessageIds = new Set(
    allowUnscopedLiveOverlap
      ? visibleThreadMessages.map(baseMessageIdentity).filter(isNonEmptyString)
      : [],
  );
  const liveMessageByRunScopedIdentity = new Map<string, Message>();
  const liveMessageByBaseIdentity = new Map<string, Message>();
  for (const message of visibleThreadMessages) {
    const identity = runScopedBaseMessageIdentity(message);
    if (identity) {
      liveMessageByRunScopedIdentity.set(identity, message);
    }
    const baseIdentity = baseMessageIdentity(message);
    if (allowUnscopedLiveOverlap && baseIdentity) {
      liveMessageByBaseIdentity.set(baseIdentity, message);
    }
  }
  const latestHistoryIndexByLiveBaseIdentity = new Map<string, number>();
  if (allowUnscopedLiveOverlap) {
    historyMessages.forEach((message, index) => {
      const baseIdentity = baseMessageIdentity(message);
      if (baseIdentity && liveBaseMessageIds.has(baseIdentity)) {
        latestHistoryIndexByLiveBaseIdentity.set(baseIdentity, index);
      }
    });
  }

  // The overlap is a contiguous suffix of historyMessages (newest history == oldest thread).
  // Scan from the end: shrink cutoff while messages are already in thread, stop as soon as
  // we hit one that isn't — everything before that point is non-overlapping.
  let cutoff = historyMessages.length;
  const matchedBaseMessageIds = new Set<string>();
  for (let i = historyMessages.length - 1; i >= 0; i--) {
    const msg = historyMessages[i];
    if (!msg) {
      continue;
    }
    const runScopedIdentity = runScopedBaseMessageIdentity(msg);
    const baseIdentity = baseMessageIdentity(msg);
    const matchesRunScope =
      runScopedIdentity !== undefined &&
      liveRunScopedMessageIds.has(runScopedIdentity);
    const matchesLatestUnscoped =
      baseIdentity !== undefined &&
      liveBaseMessageIds.has(baseIdentity) &&
      !matchedBaseMessageIds.has(baseIdentity);
    if (matchesRunScope || matchesLatestUnscoped) {
      cutoff = i;
      if (baseIdentity) {
        matchedBaseMessageIds.add(baseIdentity);
      }
    } else {
      break;
    }
  }

  const persistedMetadataByLiveBaseIdentity = new Map<
    string,
    Record<string, unknown>
  >();
  if (allowUnscopedLiveOverlap) {
    for (const message of historyMessages.slice(cutoff)) {
      const baseIdentity = baseMessageIdentity(message);
      if (
        !baseIdentity ||
        !liveBaseMessageIds.has(baseIdentity) ||
        !message.additional_kwargs
      ) {
        continue;
      }
      persistedMetadataByLiveBaseIdentity.set(
        baseIdentity,
        message.additional_kwargs,
      );
    }
  }

  const consumedLiveMessages = new Set<Message>();
  const historyWithRunScopedLiveReplacements = historyMessages
    .slice(0, cutoff)
    .map((message, index) => {
      const runScopedIdentity = runScopedBaseMessageIdentity(message);
      const runScopedLiveMessage = runScopedIdentity
        ? liveMessageByRunScopedIdentity.get(runScopedIdentity)
        : undefined;
      const baseIdentity = baseMessageIdentity(message);
      const liveMessage =
        runScopedLiveMessage ??
        (baseIdentity &&
        latestHistoryIndexByLiveBaseIdentity.get(baseIdentity) === index
          ? liveMessageByBaseIdentity.get(baseIdentity)
          : undefined);
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
    const additionalKwargs = message.additional_kwargs ?? {};
    const persistedMetadata = persistedMetadataByLiveBaseIdentity.get(
      baseMessageIdentity(message) ?? "",
    );
    const hasRunIdentity =
      typeof additionalKwargs.deerflow_run_id === "string" ||
      typeof additionalKwargs.run_id === "string";
    const hasRoundIdentity =
      typeof additionalKwargs.deerflow_round_id === "string" ||
      typeof additionalKwargs.round_id === "string" ||
      typeof additionalKwargs.roundId === "string";
    const persistedRunId =
      typeof persistedMetadata?.deerflow_run_id === "string"
        ? persistedMetadata.deerflow_run_id
        : typeof persistedMetadata?.run_id === "string"
          ? persistedMetadata.run_id
          : undefined;
    const persistedRoundId =
      typeof persistedMetadata?.deerflow_round_id === "string"
        ? persistedMetadata.deerflow_round_id
        : typeof persistedMetadata?.round_id === "string"
          ? persistedMetadata.round_id
          : typeof persistedMetadata?.roundId === "string"
            ? persistedMetadata.roundId
            : undefined;
    const missingPersistedRunIdentity =
      !hasRunIdentity && persistedRunId !== undefined;
    const missingPersistedRoundIdentity =
      !hasRoundIdentity && persistedRoundId !== undefined;
    const missingTurnDuration =
      identity &&
      savedTurnDurations.has(identity) &&
      additionalKwargs.turn_duration === undefined;
    const missingHistoryCreatedAt =
      identity &&
      savedHistoryCreatedAt.has(identity) &&
      additionalKwargs[HISTORY_CREATED_AT_KEY] === undefined;
    const missingPersistedHistoryCreatedAt =
      additionalKwargs[HISTORY_CREATED_AT_KEY] === undefined &&
      persistedMetadata?.[HISTORY_CREATED_AT_KEY] !== undefined;
    if (
      missingPersistedRunIdentity ||
      missingPersistedRoundIdentity ||
      (identity &&
        (missingTurnDuration ||
          missingHistoryCreatedAt ||
          missingPersistedHistoryCreatedAt))
    ) {
      return {
        ...message,
        additional_kwargs: {
          ...additionalKwargs,
          ...(missingPersistedRunIdentity
            ? { deerflow_run_id: persistedRunId }
            : {}),
          ...(missingPersistedRoundIdentity
            ? { deerflow_round_id: persistedRoundId }
            : {}),
          ...(missingTurnDuration
            ? { turn_duration: savedTurnDurations.get(identity) }
            : {}),
          ...(missingHistoryCreatedAt
            ? {
                [HISTORY_CREATED_AT_KEY]: savedHistoryCreatedAt.get(identity),
              }
            : missingPersistedHistoryCreatedAt
              ? {
                  [HISTORY_CREATED_AT_KEY]:
                    persistedMetadata[HISTORY_CREATED_AT_KEY],
                }
              : {}),
        },
      } as Message;
    }
    return message;
  });
}

export function getMessagesAfterBaseline(
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
