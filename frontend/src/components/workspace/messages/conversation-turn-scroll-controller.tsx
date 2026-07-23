import { ArrowDownIcon } from "lucide-react";
import { useLayoutEffect, type MutableRefObject } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";

import type { HistoryPrependRunner } from "./message-list-utils";
import { useConversationTurnScroll } from "./use-conversation-turn-scroll";

export function ConversationTurnScrollController({
  historyPrependRef,
  threadId,
  activeTurnId,
  isStreaming,
}: {
  historyPrependRef: MutableRefObject<HistoryPrependRunner | null>;
  threadId: string;
  activeTurnId?: string;
  isStreaming: boolean;
}) {
  const { t } = useI18n();
  const {
    runWithHistoryAnchor,
    returnToCurrentReply,
    shouldShowReturnToCurrentReply,
  } = useConversationTurnScroll({ threadId, activeTurnId, isStreaming });

  useLayoutEffect(() => {
    historyPrependRef.current = runWithHistoryAnchor;
    return () => {
      if (historyPrependRef.current === runWithHistoryAnchor) {
        historyPrependRef.current = null;
      }
    };
  }, [historyPrependRef, runWithHistoryAnchor]);

  if (!shouldShowReturnToCurrentReply) {
    return null;
  }

  return (
    <Button
      aria-label={t.conversation.returnToCurrentReply}
      className="absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full shadow-sm"
      onClick={returnToCurrentReply}
      size="sm"
      type="button"
      variant="outline"
    >
      <ArrowDownIcon className="mr-1 size-3.5" />
      {t.conversation.returnToCurrentReply}
    </Button>
  );
}
