"use client";

import type { BaseStream } from "@langchain/langgraph-sdk/react";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from "react";

import type { LocalSettings } from "@/core/settings";

import {
  useThreadStream,
  type ThreadStreamOptions,
  type ToolEndEvent,
} from "./hooks";
import {
  THREAD_RUNTIME_DELETED_EVENT,
  type ThreadRuntimeDeletedDetail,
} from "./runtime-events";
import type { AgentThreadState } from "./types";

export type ThreadRuntimeSnapshot = ReturnType<typeof useThreadStream>;

type ThreadRuntimeCallbacks = Pick<
  ThreadStreamOptions,
  "onSend" | "onStart" | "onFinish" | "onToolEnd"
>;

export type ThreadRuntimeRegistration = ThreadRuntimeCallbacks & {
  runtimeKey: string;
  threadId?: string | null;
  displayThreadId?: string | null;
  context: LocalSettings["context"];
  isMock?: boolean;
};

type ThreadRuntimeSlotConfig = {
  slotId: string;
  threadId?: string | null;
  displayThreadId?: string | null;
  context: LocalSettings["context"];
  isMock?: boolean;
};

type ThreadRuntimeEntry = ThreadRuntimeSlotConfig & {
  keys: Set<string>;
  callbacks: ThreadRuntimeCallbacks;
  snapshot: ThreadRuntimeSnapshot | null;
  idleSnapshot: ThreadRuntimeSnapshot | null;
  subscribers: number;
  pendingInvocations: Array<(snapshot: ThreadRuntimeSnapshot) => void>;
  createdAt: number;
  lastUsedAt: number;
};

type ThreadRuntimeKeyScope = "runtime" | "thread" | "display";

type SendMessageArgs = Parameters<ThreadRuntimeSnapshot["sendMessage"]>;
type RegenerateMessageArgs = Parameters<
  ThreadRuntimeSnapshot["regenerateMessage"]
>;

const RuntimeContext = createContext(false);

const listeners = new Set<() => void>();
const entriesBySlotId = new Map<string, ThreadRuntimeEntry>();
const slotIdByKey = new Map<string, string>();
const emptySlots: ThreadRuntimeSlotConfig[] = [];
const emptyMessages: AgentThreadState["messages"] = [];
const emptyHistory: BaseStream<AgentThreadState>["history"] = [];
const noop = () => undefined;
const asyncNoop = () => Promise.resolve(undefined);
const THREAD_RUNTIME_IDLE_GC_DELAY_MS = 60_000;

let nextSlotIndex = 1;
let slotsSnapshot: ThreadRuntimeSlotConfig[] = emptySlots;
let runtimeChangeScheduled = false;
const runtimeGcTimers = new Map<string, ReturnType<typeof setTimeout>>();

export type ThreadRuntimeSlotGcState = {
  subscribers: number;
  pendingInvocationCount: number;
  isLoading?: boolean;
  isUploading?: boolean;
  recoveryState?: string | null;
};

export function shouldCollectThreadRuntimeSlot({
  subscribers,
  pendingInvocationCount,
  isLoading,
  isUploading,
  recoveryState,
}: ThreadRuntimeSlotGcState) {
  return (
    subscribers === 0 &&
    pendingInvocationCount === 0 &&
    !isLoading &&
    !isUploading &&
    recoveryState !== "repairing"
  );
}

export function shouldResetThreadRuntimeSlot(state: ThreadRuntimeSlotGcState) {
  return shouldCollectThreadRuntimeSlot(state);
}

export function normalizeThreadRuntimeKey(key: string | null | undefined) {
  const normalized = key?.trim();
  if (!normalized) {
    return null;
  }
  return normalized;
}

export function getThreadRuntimeSlotKeys({
  runtimeKey,
  threadId,
  displayThreadId,
}: Pick<
  ThreadRuntimeRegistration,
  "runtimeKey" | "threadId" | "displayThreadId"
>) {
  return [
    ...new Set(
      [
        scopedThreadRuntimeKey("runtime", runtimeKey),
        scopedThreadRuntimeKey("thread", threadId),
        scopedThreadRuntimeKey("display", displayThreadId),
      ].filter((key): key is string => Boolean(key)),
    ),
  ];
}

function scopedThreadRuntimeKey(
  scope: ThreadRuntimeKeyScope,
  key: string | null | undefined,
) {
  const normalized = normalizeThreadRuntimeKey(key);
  return normalized ? `${scope}:${normalized}` : null;
}

export function resolveThreadRuntimeSlotId(
  slotIdByRuntimeKey: ReadonlyMap<string, string>,
  ...keys: Array<string | null | undefined>
) {
  for (const key of keys) {
    const normalized = normalizeThreadRuntimeKey(key);
    if (!normalized) {
      continue;
    }
    const slotId = slotIdByRuntimeKey.get(normalized);
    if (slotId) {
      return slotId;
    }
  }
  return null;
}

function emitRuntimeChange() {
  runtimeChangeScheduled = false;
  slotsSnapshot = [...entriesBySlotId.values()]
    .sort((a, b) => a.createdAt - b.createdAt)
    .map(
      ({
        slotId,
        threadId,
        displayThreadId,
        context,
        isMock,
      }): ThreadRuntimeSlotConfig => ({
        slotId,
        threadId,
        displayThreadId,
        context,
        isMock,
      }),
    );
  for (const listener of listeners) {
    listener();
  }
}

function scheduleRuntimeChange() {
  if (runtimeChangeScheduled) {
    return;
  }
  runtimeChangeScheduled = true;
  queueMicrotask(emitRuntimeChange);
}

function subscribeRuntime(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getRuntimeSlotsSnapshot() {
  return slotsSnapshot;
}

function resolveSlotId(...keys: Array<string | null | undefined>) {
  return resolveThreadRuntimeSlotId(slotIdByKey, ...keys);
}

function addRuntimeKey(
  entry: ThreadRuntimeEntry,
  key: string | null | undefined,
) {
  const normalized = normalizeThreadRuntimeKey(key);
  if (!normalized) {
    return;
  }
  const existingSlotId = slotIdByKey.get(normalized);
  if (existingSlotId && existingSlotId !== entry.slotId) {
    entriesBySlotId.get(existingSlotId)?.keys.delete(normalized);
  }
  entry.keys.add(normalized);
  slotIdByKey.set(normalized, entry.slotId);
}

function deleteRuntimeEntry(slotId: string) {
  const entry = entriesBySlotId.get(slotId);
  if (!entry) {
    return;
  }
  cancelRuntimeSlotGc(slotId);
  entriesBySlotId.delete(slotId);
  for (const key of entry.keys) {
    if (slotIdByKey.get(key) === slotId) {
      slotIdByKey.delete(key);
    }
  }
  emitRuntimeChange();
}

function ensureRuntimeEntry(
  registration: Omit<ThreadRuntimeRegistration, keyof ThreadRuntimeCallbacks> &
    Partial<ThreadRuntimeCallbacks>,
) {
  const slotId =
    resolveThreadRuntimeSlotId(
      slotIdByKey,
      ...getThreadRuntimeSlotKeys(registration),
    ) ?? `thread-runtime-${nextSlotIndex++}`;
  const now = Date.now();
  let entry = entriesBySlotId.get(slotId);
  let changed = false;
  if (!entry) {
    entry = {
      slotId,
      threadId: registration.threadId,
      displayThreadId: registration.displayThreadId,
      context: registration.context,
      isMock: registration.isMock,
      keys: new Set(),
      callbacks: {},
      snapshot: null,
      idleSnapshot: null,
      subscribers: 0,
      pendingInvocations: [],
      createdAt: now,
      lastUsedAt: now,
    };
    entriesBySlotId.set(slotId, entry);
    changed = true;
  }

  if (entry.threadId !== registration.threadId) {
    entry.threadId = registration.threadId;
    entry.idleSnapshot = null;
    changed = true;
  }
  if (entry.displayThreadId !== registration.displayThreadId) {
    entry.displayThreadId = registration.displayThreadId;
    changed = true;
  }
  if (entry.context !== registration.context) {
    entry.context = registration.context;
    changed = true;
  }
  if (entry.isMock !== registration.isMock) {
    entry.isMock = registration.isMock;
    changed = true;
  }
  entry.lastUsedAt = now;
  for (const key of getThreadRuntimeSlotKeys(registration)) {
    addRuntimeKey(entry, key);
  }
  if (changed) {
    scheduleRuntimeChange();
  }
  return entry;
}

function runtimeSlotGcState(
  entry: ThreadRuntimeEntry,
): ThreadRuntimeSlotGcState {
  return {
    subscribers: entry.subscribers,
    pendingInvocationCount: entry.pendingInvocations.length,
    isLoading: entry.snapshot?.thread.isLoading,
    isUploading: entry.snapshot?.isUploading,
    recoveryState: entry.snapshot?.recoveryStatus?.state ?? null,
  };
}

function cancelRuntimeSlotGc(slotId: string) {
  const timer = runtimeGcTimers.get(slotId);
  if (!timer) {
    return;
  }
  clearTimeout(timer);
  runtimeGcTimers.delete(slotId);
}

function scheduleRuntimeSlotGc(slotId: string) {
  if (runtimeGcTimers.has(slotId)) {
    return;
  }
  const timer = setTimeout(() => {
    runtimeGcTimers.delete(slotId);
    const entry = entriesBySlotId.get(slotId);
    if (!entry || !shouldCollectThreadRuntimeSlot(runtimeSlotGcState(entry))) {
      return;
    }
    deleteRuntimeEntry(slotId);
  }, THREAD_RUNTIME_IDLE_GC_DELAY_MS);
  runtimeGcTimers.set(slotId, timer);
}

function registerThreadRuntime(registration: ThreadRuntimeRegistration) {
  const entry = ensureRuntimeEntry(registration);
  cancelRuntimeSlotGc(entry.slotId);
  entry.callbacks = {
    onSend: registration.onSend,
    onStart: registration.onStart,
    onFinish: registration.onFinish,
    onToolEnd: registration.onToolEnd,
  };
  entry.subscribers += 1;
  emitRuntimeChange();
  return () => {
    const current = entriesBySlotId.get(entry.slotId);
    if (!current) {
      return;
    }
    current.subscribers = Math.max(0, current.subscribers - 1);
    current.lastUsedAt = Date.now();
    if (current.subscribers === 0) {
      current.callbacks = {};
      scheduleRuntimeSlotGc(current.slotId);
    }
    emitRuntimeChange();
  };
}

function claimRuntimeThreadId(slotId: string, threadId: string) {
  const entry = entriesBySlotId.get(slotId);
  if (!entry) {
    return;
  }
  const threadSlotKey = scopedThreadRuntimeKey("thread", threadId);
  const existingSlotId = threadSlotKey
    ? slotIdByKey.get(threadSlotKey)
    : undefined;
  if (existingSlotId && existingSlotId !== slotId) {
    deleteRuntimeEntry(existingSlotId);
  }
  entry.threadId = threadId;
  addRuntimeKey(entry, threadSlotKey);
  emitRuntimeChange();
}

function publishRuntimeSnapshot(
  slotId: string,
  snapshot: ThreadRuntimeSnapshot,
) {
  const entry = entriesBySlotId.get(slotId);
  if (!entry) {
    return;
  }
  const previous = entry.snapshot;
  entry.snapshot = snapshot;
  const pending = entry.pendingInvocations;
  entry.pendingInvocations = [];
  for (const invoke of pending) {
    invoke(snapshot);
  }
  if (shouldCollectThreadRuntimeSlot(runtimeSlotGcState(entry))) {
    scheduleRuntimeSlotGc(slotId);
  }
  if (shouldEmitRuntimeSnapshot(previous, snapshot)) {
    emitRuntimeChange();
  }
}

function shouldEmitRuntimeSnapshot(
  previous: ThreadRuntimeSnapshot | null,
  next: ThreadRuntimeSnapshot,
) {
  if (!previous) {
    return true;
  }
  const previousThread = previous.thread;
  const nextThread = next.thread;
  return (
    !areSameThreadValues(previousThread.values, nextThread.values) ||
    !areSameRuntimeMessages(previousThread.messages, nextThread.messages) ||
    previousThread.error !== nextThread.error ||
    previousThread.isLoading !== nextThread.isLoading ||
    previousThread.isThreadLoading !== nextThread.isThreadLoading ||
    previousThread.interrupt !== nextThread.interrupt ||
    previousThread.branch !== nextThread.branch ||
    !areSameRuntimeMessages(
      previous.pendingUsageMessages,
      next.pendingUsageMessages,
    ) ||
    previous.isUploading !== next.isUploading ||
    previous.isHistoryLoading !== next.isHistoryLoading ||
    previous.historyError !== next.historyError ||
    !areSameTerminalNotice(previous.terminalNotice, next.terminalNotice) ||
    !areSameRecoveryStatus(previous.recoveryStatus, next.recoveryStatus) ||
    previous.hasMoreHistory !== next.hasMoreHistory
  );
}

function areSameThreadValues(left: AgentThreadState, right: AgentThreadState) {
  return (
    left === right ||
    (left.title === right.title &&
      areSameArrayItems(left.artifacts, right.artifacts) &&
      areSameArrayItems(left.todos ?? [], right.todos ?? []))
  );
}

function areSameRuntimeMessages(
  left: ThreadRuntimeSnapshot["thread"]["messages"],
  right: ThreadRuntimeSnapshot["thread"]["messages"],
) {
  if (left === right) {
    return true;
  }
  if (left.length !== right.length) {
    return false;
  }
  return left.every((message, index) =>
    areSameRuntimeMessage(message, right[index]),
  );
}

function areSameRuntimeMessage<
  T extends ThreadRuntimeSnapshot["thread"]["messages"][number],
>(left: T, right: T | undefined) {
  if (left === right) {
    return true;
  }
  if (!right || left.id !== right.id || left.type !== right.type) {
    return false;
  }
  return (
    stableStringify(left.content) === stableStringify(right.content) &&
    stableStringify(left.additional_kwargs) ===
      stableStringify(right.additional_kwargs)
  );
}

function areSameTerminalNotice(
  left: ThreadRuntimeSnapshot["terminalNotice"],
  right: ThreadRuntimeSnapshot["terminalNotice"],
) {
  return (
    left === right ||
    (Boolean(left) &&
      Boolean(right) &&
      left?.runId === right?.runId &&
      left?.status === right?.status &&
      left?.terminalReason === right?.terminalReason &&
      left?.error === right?.error)
  );
}

function areSameRecoveryStatus(
  left: ThreadRuntimeSnapshot["recoveryStatus"],
  right: ThreadRuntimeSnapshot["recoveryStatus"],
) {
  return left === right || stableStringify(left) === stableStringify(right);
}

function areSameArrayItems<T>(
  left: readonly T[] | undefined = [],
  right: readonly T[] | undefined = [],
) {
  if (left === right) {
    return true;
  }
  if (left.length !== right.length) {
    return false;
  }
  return left.every((item, index) => item === right[index]);
}

function stableStringify(value: unknown) {
  if (value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "[unserializable]";
  }
}

function getEntryForRuntimeKey(runtimeKey: string) {
  const slotId = resolveSlotId(scopedThreadRuntimeKey("runtime", runtimeKey));
  return slotId ? entriesBySlotId.get(slotId) : undefined;
}

export function clearThreadRuntime(threadId: string) {
  const slotId = resolveSlotId(scopedThreadRuntimeKey("thread", threadId));
  if (!slotId) {
    return;
  }
  deleteRuntimeEntry(slotId);
}

export function resetThreadRuntimeSlot(runtimeKey: string) {
  const slotId = resolveSlotId(scopedThreadRuntimeKey("runtime", runtimeKey));
  if (!slotId) {
    return;
  }
  const entry = entriesBySlotId.get(slotId);
  if (!entry || !shouldResetThreadRuntimeSlot(runtimeSlotGcState(entry))) {
    return;
  }
  deleteRuntimeEntry(slotId);
}

function createIdleThread(
  isHistoryLoading: boolean,
): BaseStream<AgentThreadState> {
  return {
    values: {
      title: "",
      messages: emptyMessages,
      artifacts: [],
    },
    error: undefined,
    isLoading: false,
    isThreadLoading: isHistoryLoading,
    messages: emptyMessages,
    interrupt: undefined,
    stop: asyncNoop,
    submit: asyncNoop,
    branch: "",
    setBranch: noop,
    history: emptyHistory,
    experimental_branchTree:
      {} as BaseStream<AgentThreadState>["experimental_branchTree"],
    getMessagesMetadata: () => undefined,
    client: {} as BaseStream<AgentThreadState>["client"],
    assistantId: "lead_agent",
    joinStream: asyncNoop,
  };
}

function createIdleRuntimeSnapshot(
  entry: ThreadRuntimeEntry | undefined,
): ThreadRuntimeSnapshot {
  const hasSavedThread = Boolean(entry?.threadId);
  const thread = createIdleThread(hasSavedThread);
  return {
    thread,
    pendingUsageMessages: emptyMessages,
    sendMessage: asyncNoop,
    regenerateMessage: asyncNoop,
    isUploading: false,
    isHistoryLoading: hasSavedThread,
    historyError: null,
    terminalNotice: null,
    recoveryStatus: null,
    retryRecovery: asyncNoop,
    hasMoreHistory: false,
    loadMoreHistory: asyncNoop,
  };
}

const missingRuntimeSnapshot = createIdleRuntimeSnapshot(undefined);

function getRuntimeSnapshot(runtimeKey: string) {
  const entry = getEntryForRuntimeKey(runtimeKey);
  if (!entry) {
    return missingRuntimeSnapshot;
  }
  if (entry.snapshot) {
    return entry.snapshot;
  }
  entry.idleSnapshot ??= createIdleRuntimeSnapshot(entry);
  return entry.idleSnapshot;
}

function invokeRuntime<K extends "sendMessage" | "regenerateMessage">(
  runtimeKey: string,
  method: K,
  args: K extends "sendMessage" ? SendMessageArgs : RegenerateMessageArgs,
) {
  const entry = getEntryForRuntimeKey(runtimeKey);
  if (!entry) {
    return Promise.reject(new Error("Thread runtime is not registered."));
  }
  if (entry.snapshot) {
    return Promise.resolve(
      (entry.snapshot[method] as (...methodArgs: typeof args) => unknown)(
        ...args,
      ),
    ).then(() => undefined);
  }
  return new Promise<void>((resolve, reject) => {
    entry.pendingInvocations.push((snapshot) => {
      Promise.resolve(
        (snapshot[method] as (...methodArgs: typeof args) => unknown)(...args),
      ).then(() => resolve(), reject);
    });
  });
}

function callRuntimeCallback<K extends keyof ThreadRuntimeCallbacks>(
  slotId: string,
  callbackName: K,
  ...args: Parameters<NonNullable<ThreadRuntimeCallbacks[K]>>
) {
  const callback = entriesBySlotId.get(slotId)?.callbacks[callbackName];
  if (callback) {
    (callback as (...callbackArgs: typeof args) => void)(...args);
  }
}

function ThreadRuntimeSlot({ slot }: { slot: ThreadRuntimeSlotConfig }) {
  const { slotId } = slot;
  const latestSlotIdRef = useRef(slotId);
  latestSlotIdRef.current = slotId;

  const runtime = useThreadStream({
    threadId: slot.threadId,
    displayThreadId: slot.displayThreadId,
    runtimeOwnerId: slotId,
    context: slot.context,
    isMock: slot.isMock,
    onSend(threadId) {
      callRuntimeCallback(latestSlotIdRef.current, "onSend", threadId);
    },
    onStart(threadId, runId) {
      claimRuntimeThreadId(latestSlotIdRef.current, threadId);
      callRuntimeCallback(latestSlotIdRef.current, "onStart", threadId, runId);
    },
    onFinish(state, meta) {
      callRuntimeCallback(latestSlotIdRef.current, "onFinish", state, meta);
    },
    onToolEnd(event: ToolEndEvent) {
      callRuntimeCallback(latestSlotIdRef.current, "onToolEnd", event);
    },
  });

  useEffect(() => {
    publishRuntimeSnapshot(slotId, runtime);
  }, [runtime, slotId]);

  return null;
}

export function ThreadRuntimeProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const slots = useSyncExternalStore(
    subscribeRuntime,
    getRuntimeSlotsSnapshot,
    getRuntimeSlotsSnapshot,
  );

  useEffect(() => {
    const handleDeletedThread = (event: Event) => {
      const detail = (event as CustomEvent<ThreadRuntimeDeletedDetail>).detail;
      if (detail?.threadId) {
        clearThreadRuntime(detail.threadId);
      }
    };
    window.addEventListener(THREAD_RUNTIME_DELETED_EVENT, handleDeletedThread);
    return () =>
      window.removeEventListener(
        THREAD_RUNTIME_DELETED_EVENT,
        handleDeletedThread,
      );
  }, []);

  return (
    <RuntimeContext.Provider value>
      {children}
      {slots.map((slot) => (
        <ThreadRuntimeSlot key={slot.slotId} slot={slot} />
      ))}
    </RuntimeContext.Provider>
  );
}

export function useThreadRuntime(registration: ThreadRuntimeRegistration) {
  const hasProvider = useContext(RuntimeContext);
  if (!hasProvider) {
    throw new Error(
      "useThreadRuntime must be used within ThreadRuntimeProvider",
    );
  }

  const runtimeKey = registration.runtimeKey;
  const snapshot = useSyncExternalStore(
    subscribeRuntime,
    () => getRuntimeSnapshot(runtimeKey),
    () => getRuntimeSnapshot(runtimeKey),
  );

  useEffect(() => registerThreadRuntime(registration), [registration]);

  const sendMessage = useMemo(
    () =>
      (...args: SendMessageArgs) =>
        invokeRuntime(runtimeKey, "sendMessage", args),
    [runtimeKey],
  );
  const regenerateMessage = useMemo(
    () =>
      (...args: RegenerateMessageArgs) =>
        invokeRuntime(runtimeKey, "regenerateMessage", args),
    [runtimeKey],
  );

  return {
    ...snapshot,
    sendMessage,
    regenerateMessage,
  } as ThreadRuntimeSnapshot;
}
