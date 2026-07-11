"use client";

import { useParams, usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { shouldApplyVisibleThreadEffect } from "@/core/threads/effect-policy";
import { uuid } from "@/core/utils/uuid";

export const THREAD_CHAT_RESET_EVENT = "deer-flow:thread-chat-reset";

let pendingThreadChatPathname: string | null = null;

export function markThreadChatNavigationIntent(pathname: string) {
  pendingThreadChatPathname = pathname;
}

export function clearThreadChatNavigationIntent(pathname: string) {
  if (pendingThreadChatPathname === pathname) {
    pendingThreadChatPathname = null;
  }
}

export function resetThreadChatNavigationIntent() {
  pendingThreadChatPathname = null;
}

export function threadIdFromPendingNavigationIntent() {
  return pendingThreadChatPathname
    ? threadIdFromCommittedPathname(pendingThreadChatPathname)
    : null;
}

export function pendingNavigationAllowsThreadStart(createdThreadId: string) {
  return (
    pendingThreadChatPathname === null ||
    threadIdFromCommittedPathname(pendingThreadChatPathname) === createdThreadId
  );
}

type ThreadChatResetDetail = {
  deletedThreadId?: string;
  nextPath: string;
  force?: boolean;
};

type ThreadChatRouteSyncInput = {
  committedPathname: string;
  threadIdFromPath: string;
  currentThreadId: string;
  newThreadId: string | null;
  createThreadId: () => string;
};

type ThreadChatRouteSyncState = {
  threadId: string;
  isNewThread: boolean;
  newThreadId: string | null;
};

export function threadIdFromCommittedPathname(pathname: string) {
  const segments = pathname.split("/").filter(Boolean);
  const chatsIndex = segments.lastIndexOf("chats");
  const threadId = chatsIndex >= 0 ? segments[chatsIndex + 1] : undefined;
  if (!threadId || threadId === "new") {
    return null;
  }
  try {
    return decodeURIComponent(threadId);
  } catch {
    return threadId;
  }
}

export function isThreadFinishForVisibleChat({
  finishThreadId,
  visibleThreadId,
  committedPathname,
}: {
  finishThreadId: string | null | undefined;
  visibleThreadId: string | null | undefined;
  committedPathname: string;
}) {
  return shouldApplyVisibleThreadEffect({
    effectThreadId: finishThreadId,
    visibleThreadId,
    committedThreadId: threadIdFromCommittedPathname(committedPathname),
  });
}

export function resolveThreadChatRouteSync({
  committedPathname,
  threadIdFromPath,
  currentThreadId,
  newThreadId,
  createThreadId,
}: ThreadChatRouteSyncInput): ThreadChatRouteSyncState {
  if (committedPathname.endsWith("/new")) {
    const nextThreadId = newThreadId ?? createThreadId();
    return {
      threadId: nextThreadId,
      isNewThread: true,
      newThreadId: nextThreadId,
    };
  }

  return {
    threadId:
      threadIdFromPath === "new"
        ? (threadIdFromCommittedPathname(committedPathname) ?? currentThreadId)
        : threadIdFromPath,
    isNewThread: false,
    newThreadId: null,
  };
}

export function resetThreadChatAfterDelete(detail: ThreadChatResetDetail) {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<ThreadChatResetDetail>(THREAD_CHAT_RESET_EVENT, {
      detail,
    }),
  );
}

export function resetThreadChatToNew(nextPath: string) {
  resetThreadChatAfterDelete({ nextPath, force: true });
}

export function useThreadChat() {
  const { thread_id: threadIdFromPath } = useParams<{ thread_id: string }>();
  const pathname = usePathname();
  // Render-time values use the committed browser URL. The sync effect below
  // intentionally watches the reactive pathname so client navigation still
  // schedules a reset when window.location is stale during render.
  const actualPathname =
    typeof window === "undefined" ? pathname : window.location.pathname;
  const isNewPath = actualPathname.endsWith("/new");
  const newThreadIdRef = useRef<string | null>(
    threadIdFromPath === "new" ? uuid() : null,
  );

  if (isNewPath && !newThreadIdRef.current) {
    newThreadIdRef.current = uuid();
  }

  const searchParams = useSearchParams();
  const [threadId, setThreadIdState] = useState(() => {
    return isNewPath && threadIdFromPath === "new"
      ? (newThreadIdRef.current ?? uuid())
      : (threadIdFromCommittedPathname(actualPathname) ?? threadIdFromPath);
  });

  const [isNewThreadState, setIsNewThreadState] = useState(() => isNewPath);

  const resetToNewThread = useCallback(() => {
    const nextThreadId = uuid();
    newThreadIdRef.current = nextThreadId;
    setIsNewThreadState(true);
    setThreadIdState(nextThreadId);
  }, []);

  useEffect(() => {
    const committedPathname =
      typeof window === "undefined" ? pathname : window.location.pathname;
    clearThreadChatNavigationIntent(committedPathname);
    const nextState = resolveThreadChatRouteSync({
      committedPathname,
      threadIdFromPath,
      currentThreadId: threadId,
      newThreadId: newThreadIdRef.current,
      createThreadId: uuid,
    });
    newThreadIdRef.current = nextState.newThreadId;
    setIsNewThreadState(nextState.isNewThread);
    setThreadIdState(nextState.threadId);
  }, [pathname, threadId, threadIdFromPath]);

  useEffect(() => {
    const handleReset = (event: Event) => {
      const detail = (event as CustomEvent<ThreadChatResetDetail>).detail;
      if (!detail?.nextPath) {
        return;
      }

      const currentPathname = window.location.pathname;
      const isDeletingCurrentThread =
        detail.force === true ||
        detail.deletedThreadId === threadId ||
        detail.deletedThreadId === threadIdFromPath ||
        currentPathname.endsWith(`/${detail.deletedThreadId}`);

      if (!isDeletingCurrentThread) {
        return;
      }

      // URL replacement is owned by the caller's Next router action; this hook
      // only resets local chat state so the router state and browser URL stay
      // in sync.
      resetToNewThread();
    };

    window.addEventListener(THREAD_CHAT_RESET_EVENT, handleReset);
    return () =>
      window.removeEventListener(THREAD_CHAT_RESET_EVENT, handleReset);
  }, [resetToNewThread, threadId, threadIdFromPath]);

  const setThreadId = useCallback((nextThreadId: string) => {
    newThreadIdRef.current = null;
    setThreadIdState(nextThreadId);
  }, []);

  const setIsNewThread = useCallback((nextIsNewThread: boolean) => {
    if (!nextIsNewThread) {
      newThreadIdRef.current = null;
    }
    setIsNewThreadState(nextIsNewThread);
  }, []);

  const isMock = searchParams.get("mock") === "true";
  return {
    threadId: isNewPath ? (newThreadIdRef.current ?? threadId) : threadId,
    setThreadId,
    isNewThread: isNewPath ? true : isNewThreadState,
    setIsNewThread,
    resetToNewThread,
    isMock,
  };
}
