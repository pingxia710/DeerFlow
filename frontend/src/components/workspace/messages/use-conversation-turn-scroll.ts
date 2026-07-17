"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useStickToBottomContext } from "use-stick-to-bottom";

const FOLLOW_RELEASE_DISTANCE_PX = 96;

export function getFollowingState({
  current,
  distanceFromBottom,
  isProgrammatic,
}: {
  current: boolean;
  distanceFromBottom: number;
  isProgrammatic: boolean;
}) {
  if (isProgrammatic) {
    return current;
  }
  return distanceFromBottom <= FOLLOW_RELEASE_DISTANCE_PX;
}

type ConversationTurnScrollOptions = {
  threadId: string;
  activeTurnId?: string;
  isStreaming: boolean;
};

type LoadMoreHistory = () => Promise<void> | void;

type ReaderAnchor = {
  element: HTMLElement;
  top: number;
};

function distanceFromLiveEdge(scrollRoot: HTMLElement) {
  return Math.max(
    0,
    scrollRoot.scrollHeight - scrollRoot.clientHeight - scrollRoot.scrollTop,
  );
}

export function useConversationTurnScroll({
  threadId,
  activeTurnId,
  isStreaming,
}: ConversationTurnScrollOptions) {
  const { contentRef, scrollRef, scrollToBottom, stopScroll } =
    useStickToBottomContext();
  const [isFollowing, setIsFollowing] = useState(true);
  const followingRef = useRef(true);
  const previousActiveTurnIdRef = useRef<string | undefined>(undefined);
  const previousIsStreamingRef = useRef(isStreaming);
  const readerAnchorRef = useRef<ReaderAnchor | null>(null);
  const programmaticScrollTopRef = useRef<number | null>(null);

  const setFollowingState = useCallback((next: boolean) => {
    followingRef.current = next;
    setIsFollowing(next);
  }, []);

  const setScrollTop = useCallback(
    (scrollRoot: HTMLElement, nextScrollTop: number) => {
      const maxScrollTop = Math.max(
        0,
        scrollRoot.scrollHeight - scrollRoot.clientHeight,
      );
      const target = Math.min(Math.max(nextScrollTop, 0), maxScrollTop);
      programmaticScrollTopRef.current = target;
      scrollRoot.scrollTop = target;
    },
    [],
  );

  const captureReaderAnchor = useCallback(() => {
    const content = contentRef.current;
    const scrollRoot = scrollRef.current;
    if (!content || !scrollRoot) {
      return;
    }

    const rootRect = scrollRoot.getBoundingClientRect();
    const element = Array.from(
      content.querySelectorAll<HTMLElement>(
        "[data-conversation-scroll-anchor]",
      ),
    ).find((candidate) => {
      const rect = candidate.getBoundingClientRect();
      return rect.bottom > rootRect.top && rect.top < rootRect.bottom;
    });
    if (element) {
      readerAnchorRef.current = {
        element,
        top: element.getBoundingClientRect().top,
      };
    }
  }, [contentRef, scrollRef]);

  const restoreReaderAnchor = useCallback(() => {
    const scrollRoot = scrollRef.current;
    const anchor = readerAnchorRef.current;
    if (!scrollRoot || !anchor?.element.isConnected) {
      return;
    }

    const delta = anchor.element.getBoundingClientRect().top - anchor.top;
    if (Math.abs(delta) >= 1) {
      setScrollTop(scrollRoot, scrollRoot.scrollTop + delta);
    }
  }, [scrollRef, setScrollTop]);

  const runWithHistoryAnchor = useCallback(
    async (loadMore: LoadMoreHistory) => {
      captureReaderAnchor();
      try {
        await Promise.resolve(loadMore());
      } finally {
        requestAnimationFrame(() => {
          restoreReaderAnchor();
          requestAnimationFrame(() => {
            restoreReaderAnchor();
          });
        });
      }
    },
    [captureReaderAnchor, restoreReaderAnchor],
  );

  useEffect(() => {
    const scrollRoot = scrollRef.current;
    if (!scrollRoot) {
      return;
    }

    const handleScroll = () => {
      captureReaderAnchor();
      const isProgrammatic =
        programmaticScrollTopRef.current !== null &&
        Math.abs(scrollRoot.scrollTop - programmaticScrollTopRef.current) < 1;
      programmaticScrollTopRef.current = null;
      const distanceFromBottom = distanceFromLiveEdge(scrollRoot);
      if (!isProgrammatic && distanceFromBottom > FOLLOW_RELEASE_DISTANCE_PX) {
        stopScroll();
      }
      setFollowingState(
        getFollowingState({
          current: followingRef.current,
          distanceFromBottom,
          isProgrammatic,
        }),
      );
    };

    scrollRoot.addEventListener("scroll", handleScroll, { passive: true });
    return () => scrollRoot.removeEventListener("scroll", handleScroll);
  }, [captureReaderAnchor, scrollRef, setFollowingState, stopScroll]);

  useLayoutEffect(() => {
    previousActiveTurnIdRef.current = undefined;
    previousIsStreamingRef.current = false;
    readerAnchorRef.current = null;
    setFollowingState(true);
  }, [setFollowingState, threadId]);

  useLayoutEffect(() => {
    const startedStreaming = isStreaming && !previousIsStreamingRef.current;
    const changedTurn = activeTurnId !== previousActiveTurnIdRef.current;
    if (activeTurnId && (startedStreaming || (changedTurn && isStreaming))) {
      setFollowingState(true);
      void scrollToBottom({ animation: "instant" });
    }
    previousActiveTurnIdRef.current = activeTurnId;
    previousIsStreamingRef.current = isStreaming;
  }, [activeTurnId, isStreaming, scrollToBottom, setFollowingState]);

  const returnToCurrentReply = useCallback(() => {
    setFollowingState(true);
    void scrollToBottom({ animation: "instant" });
  }, [scrollToBottom, setFollowingState]);

  return {
    runWithHistoryAnchor,
    shouldShowReturnToCurrentReply: !isFollowing,
    returnToCurrentReply,
  };
}
