import { ChevronUpIcon, Loader2Icon } from "lucide-react";
import { useCallback, useEffect, useRef, type MutableRefObject } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";

import type { HistoryPrependRunner } from "./message-list-utils";

const LOAD_MORE_HISTORY_THROTTLE_MS = 1200;

export function LoadMoreHistoryIndicator({
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
