import type { BaseStream } from "@langchain/langgraph-sdk";
import { useEffect } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { FlipDisplay } from "./flip-display";

export function ThreadTitle({
  threadId,
  thread,
  isNewThread,
  className,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  isNewThread: boolean;
}) {
  const { t } = useI18n();
  useEffect(() => {
    let _title = t.pages.untitled;

    if (thread.values?.title) {
      _title = thread.values.title;
    } else if (isNewThread) {
      _title = t.pages.newChat;
    }
    if (thread.isThreadLoading) {
      document.title = `Loading... - ${t.pages.appName}`;
    } else {
      document.title = `${_title} - ${t.pages.appName}`;
    }
  }, [
    isNewThread,
    t.pages.newChat,
    t.pages.untitled,
    t.pages.appName,
    thread.isThreadLoading,
    thread.values,
  ]);

  if (!thread.values?.title) {
    return null;
  }
  return (
    <FlipDisplay
      className={cn("max-w-full min-w-0 [&>div]:truncate", className)}
      uniqueKey={threadId}
    >
      {thread.values.title ?? "Untitled"}
    </FlipDisplay>
  );
}
