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
const NEW_TURN_VIEWPORT_OFFSET_PX = 24;

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
  const { contentRef, scrollRef, scrollToBottom } = useStickToBottomContext();
  const [isFollowing, setIsFollowing] = useState(true);
  const [currentTurnId, setCurrentTurnId] = useState<string | null>(null);
  const followingRef = useRef(true);
  const isStreamingRef = useRef(isStreaming);
  const previousActiveTurnIdRef = useRef<string | undefined>(undefined);
  const previousIsStreamingRef = useRef(isStreaming);
  const readerAnchorRef = useRef<ReaderAnchor | null>(null);
  const programmaticScrollRef = useRef(false);

  isStreamingRef.current = isStreaming;

  const setFollowingState = useCallback((next: boolean) => {
    followingRef.current = next;
    setIsFollowing(next);
  }, []);

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
      scrollRoot.scrollTop += delta;
    }
  }, [scrollRef]);

  const runWithHistoryAnchor = useCallback(
    async (loadMore: LoadMoreHistory) => {
      captureReaderAnchor();
      await Promise.resolve(loadMore());
      requestAnimationFrame(restoreReaderAnchor);
    },
    [captureReaderAnchor, restoreReaderAnchor],
  );

  const anchorActiveTurn = useCallback(
    (turnId: string) => {
      const content = contentRef.current;
      const scrollRoot = scrollRef.current;
      if (!content || !scrollRoot) {
        return;
      }

      const turnElement = Array.from(
        content.querySelectorAll<HTMLElement>("[data-conversation-turn]"),
      ).find((element) => element.dataset.conversationTurnId === turnId);
      if (!turnElement) {
        return;
      }

      const rootRect = scrollRoot.getBoundingClientRect();
      const target =
        scrollRoot.scrollTop +
        turnElement.getBoundingClientRect().top -
        rootRect.top -
        NEW_TURN_VIEWPORT_OFFSET_PX;
      const maxScrollTop = Math.max(
        0,
        scrollRoot.scrollHeight - scrollRoot.clientHeight,
      );

      programmaticScrollRef.current = true;
      scrollRoot.scrollTop = Math.min(Math.max(target, 0), maxScrollTop);
      captureReaderAnchor();
      requestAnimationFrame(() => {
        programmaticScrollRef.current = false;
      });
    },
    [captureReaderAnchor, contentRef, scrollRef],
  );

  useEffect(() => {
    const scrollRoot = scrollRef.current;
    if (!scrollRoot) {
      return;
    }

    const handleScroll = () => {
      captureReaderAnchor();
      if (programmaticScrollRef.current) {
        return;
      }
      setFollowingState(
        distanceFromLiveEdge(scrollRoot) <= FOLLOW_RELEASE_DISTANCE_PX,
      );
    };

    scrollRoot.addEventListener("scroll", handleScroll, { passive: true });
    return () => scrollRoot.removeEventListener("scroll", handleScroll);
  }, [captureReaderAnchor, scrollRef, setFollowingState]);

  useEffect(() => {
    const content = contentRef.current;
    const scrollRoot = scrollRef.current;
    if (!content || !scrollRoot) {
      return;
    }

    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (followingRef.current && isStreamingRef.current) {
          scrollRoot.scrollTop = scrollRoot.scrollHeight;
          return;
        }
        restoreReaderAnchor();
      });
    });
    observer.observe(content);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [contentRef, restoreReaderAnchor, scrollRef]);

  useLayoutEffect(() => {
    const startedStreaming = isStreaming && !previousIsStreamingRef.current;
    const changedTurn = activeTurnId !== previousActiveTurnIdRef.current;
    if (activeTurnId && (startedStreaming || (changedTurn && isStreaming))) {
      setCurrentTurnId(activeTurnId);
      requestAnimationFrame(() => anchorActiveTurn(activeTurnId));
    }
    previousActiveTurnIdRef.current = activeTurnId;
    previousIsStreamingRef.current = isStreaming;
  }, [activeTurnId, anchorActiveTurn, isStreaming]);

  useEffect(() => {
    previousActiveTurnIdRef.current = undefined;
    previousIsStreamingRef.current = false;
    readerAnchorRef.current = null;
    setCurrentTurnId(null);
    setFollowingState(true);
  }, [setFollowingState, threadId]);

  const returnToCurrentReply = useCallback(() => {
    setFollowingState(true);
    void scrollToBottom({ animation: "smooth" });
  }, [scrollToBottom, setFollowingState]);

  return {
    runWithHistoryAnchor,
    shouldShowReturnToCurrentReply: currentTurnId !== null && !isFollowing,
    returnToCurrentReply,
  };
}
