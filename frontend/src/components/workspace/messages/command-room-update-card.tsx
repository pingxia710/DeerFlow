"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { ChevronRightIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { useI18n } from "@/core/i18n/hooks";
import {
  extractContentFromMessage,
  getMessageRoundId,
  getMessageRunId,
} from "@/core/messages/utils";
import { HISTORY_CREATED_AT_KEY } from "@/core/threads/message-history";
import { formatDateTime } from "@/core/utils/datetime";

import { MarkdownContent } from "./markdown-content";

function messageTime(message: Message) {
  const value = message.additional_kwargs?.[HISTORY_CREATED_AT_KEY];
  const timestamp = typeof value === "string" ? Date.parse(value) : Number.NaN;
  return Number.isFinite(timestamp) ? timestamp : undefined;
}

export function getCommandRoomUpdateAnchorId(message: Message) {
  return `command-room-update:${JSON.stringify([
    getMessageRunId(message) ?? "",
    getMessageRoundId(message) ?? "",
    message.id ?? messageTime(message) ?? "",
  ])}`;
}

export function CommandRoomUpdateCard({
  defaultOpen = false,
  message,
  title,
}: {
  defaultOpen?: boolean;
  message: Message;
  title: string;
}) {
  const { locale } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const content = extractContentFromMessage(message);
  const preview = content.replace(/\s+/g, " ").trim();
  const timestamp = messageTime(message);

  useEffect(() => {
    if (defaultOpen) {
      setOpen(true);
    }
  }, [defaultOpen]);

  return (
    <section
      className="border-border/60 scroll-mt-20 border-y transition-shadow"
      data-command-room-update
      id={getCommandRoomUpdateAnchorId(message)}
    >
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <Button
            aria-label={title}
            className="h-auto min-h-12 w-full justify-between gap-3 px-3 py-2 text-left"
            size="sm"
            type="button"
            variant="ghost"
          >
            <span className="min-w-0">
              <span className="block font-medium">{title}</span>
              {preview && (
                <span className="text-muted-foreground block truncate text-xs font-normal">
                  {preview}
                </span>
              )}
            </span>
            <span className="text-muted-foreground flex shrink-0 items-center gap-2 text-xs font-normal">
              {timestamp !== undefined && (
                <time
                  className="hidden sm:inline"
                  dateTime={new Date(timestamp).toISOString()}
                >
                  {formatDateTime(timestamp, locale)}
                </time>
              )}
              <ChevronRightIcon
                aria-hidden
                className={`size-4 transition-transform ${open ? "rotate-90" : ""}`}
              />
            </span>
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="border-border/60 border-t px-3 py-3">
          <MarkdownContent content={content} isLoading={false} />
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}
