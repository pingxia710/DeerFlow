import {
  CheckCircleIcon,
  ChevronUp,
  ClipboardListIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useStickToBottomContext } from "use-stick-to-bottom";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Button } from "@/components/ui/button";
import { ShineBorder } from "@/components/ui/shine-border";
import { useI18n } from "@/core/i18n/hooks";
import { hasToolCalls } from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { streamdownPluginsWithWordAnimation } from "@/core/streamdown";
import { SafeStreamdown } from "@/core/streamdown/components";
import { useSubtask } from "@/core/tasks/context";
import { formatElapsedMinutesSeconds } from "@/core/tasks/elapsed";
import { explainLastToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";

import { MarkdownContent } from "./markdown-content";

const MS_IN_SECOND = 1000;

function restoreAnchorTop(
  anchor: HTMLElement,
  initialTop: number,
  scrollElement: HTMLElement | null,
) {
  let frames = 0;

  const keepAnchorStable = () => {
    if (!anchor.isConnected) {
      return;
    }

    const delta = anchor.getBoundingClientRect().top - initialTop;
    if (Math.abs(delta) >= 1) {
      if (scrollElement) {
        scrollElement.scrollTop += delta;
      } else {
        window.scrollBy(0, delta);
      }
    }

    frames += 1;
    if (frames < 3) {
      requestAnimationFrame(keepAnchorStable);
    }
  };

  requestAnimationFrame(keepAnchorStable);
}

export function SubtaskCard({
  className,
  taskId,
  isLoading,
}: {
  className?: string;
  taskId: string;
  isLoading: boolean;
}) {
  const { t } = useI18n();
  const { scrollRef, stopScroll } = useStickToBottomContext();
  const task = useSubtask(taskId)!;
  const resultPreview = useMemo(
    () => task.result?.replace(/\s+/g, " ").trim() ?? "",
    [task.result],
  );
  const hasCompletedResult =
    task.status === "completed" && resultPreview.length > 0;
  const [collapsed, setCollapsed] = useState(true);
  const startedAtRef = useRef<number | null>(
    task.status === "in_progress" ? Date.now() : null,
  );
  const [elapsedSeconds, setElapsedSeconds] = useState<number | null>(
    task.status === "in_progress" ? 0 : null,
  );
  const isOpen = !collapsed;
  const elapsedText =
    elapsedSeconds === null
      ? null
      : formatElapsedMinutesSeconds(elapsedSeconds);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const icon = useMemo(() => {
    if (task.status === "completed") {
      return <CheckCircleIcon className="size-3" />;
    } else if (task.status === "failed") {
      return <XCircleIcon className="size-3 text-red-500" />;
    } else if (task.status === "in_progress") {
      return <Loader2Icon className="size-3 animate-spin" />;
    }
  }, [task.status]);

  useEffect(() => {
    if (task.status !== "in_progress") {
      if (startedAtRef.current !== null) {
        setElapsedSeconds(
          Math.floor((Date.now() - startedAtRef.current) / MS_IN_SECOND),
        );
        startedAtRef.current = null;
      }
      return;
    }

    startedAtRef.current ??= Date.now();

    const startedAt = startedAtRef.current;
    const updateElapsed = () => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / MS_IN_SECOND));
    };

    updateElapsed();
    const interval = window.setInterval(updateElapsed, MS_IN_SECOND);

    return () => window.clearInterval(interval);
  }, [task.status]);

  const handleHeaderToggle = (event: MouseEvent<HTMLButtonElement>) => {
    const anchor = event.currentTarget;
    const initialTop = anchor.getBoundingClientRect().top;
    const scrollElement = scrollRef.current;

    stopScroll();
    setCollapsed((current) => !current);
    restoreAnchorTop(anchor, initialTop, scrollElement);
  };

  return (
    <ChainOfThought
      className={cn("relative w-full gap-2 rounded-lg border py-0", className)}
      open={isOpen}
    >
      <div
        className={cn(
          "ambilight z-[-1]",
          task.status === "in_progress" ? "enabled" : "",
        )}
      ></div>
      {task.status === "in_progress" && (
        <>
          <ShineBorder
            borderWidth={1.5}
            shineColor={["#A07CFE", "#FE8FB5", "#FFBE7B"]}
          />
        </>
      )}
      <div className="bg-background/95 flex w-full flex-col rounded-lg">
        <div className="flex w-full items-center justify-between p-0.5">
          <Button
            className="h-auto min-h-9 w-full items-start justify-start text-left"
            variant="ghost"
            onClick={handleHeaderToggle}
          >
            <div className="flex w-full items-center justify-between">
              <div className="min-w-0 flex-1">
                <ChainOfThoughtStep
                  className="font-normal"
                  label={
                    task.status === "in_progress" ? (
                      <Shimmer duration={3} spread={3}>
                        {task.description}
                      </Shimmer>
                    ) : (
                      task.description
                    )
                  }
                  icon={<ClipboardListIcon />}
                ></ChainOfThoughtStep>
                {!isOpen && hasCompletedResult && (
                  <p className="text-muted-foreground line-clamp-2 px-6 pb-1 text-xs leading-5 whitespace-normal">
                    {resultPreview}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-1">
                {elapsedText && (
                  <span className="text-muted-foreground/80 min-w-[4ch] text-right font-mono text-xs leading-none tabular-nums">
                    {elapsedText}
                  </span>
                )}
                {!isOpen && (
                  <div
                    className={cn(
                      "text-muted-foreground flex items-center gap-1 text-xs font-normal",
                      task.status === "failed" ? "text-red-500 opacity-67" : "",
                    )}
                  >
                    {icon}
                    <FlipDisplay
                      className="max-w-[420px] truncate pb-1"
                      uniqueKey={task.latestMessage?.id ?? ""}
                    >
                      {task.status === "in_progress" &&
                      task.latestMessage &&
                      hasToolCalls(task.latestMessage)
                        ? explainLastToolCall(task.latestMessage, t)
                        : t.subtasks[task.status]}
                    </FlipDisplay>
                  </div>
                )}
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    isOpen ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
        </div>
        <ChainOfThoughtContent className="px-4 pb-4">
          {task.prompt && (
            <ChainOfThoughtStep
              label={
                <SafeStreamdown
                  {...streamdownPluginsWithWordAnimation}
                  components={{ a: CitationLink }}
                >
                  {task.prompt}
                </SafeStreamdown>
              }
            ></ChainOfThoughtStep>
          )}
          {task.status === "in_progress" &&
            task.latestMessage &&
            hasToolCalls(task.latestMessage) && (
              <ChainOfThoughtStep
                label={t.subtasks.in_progress}
                icon={<Loader2Icon className="size-4 animate-spin" />}
              >
                {explainLastToolCall(task.latestMessage, t)}
              </ChainOfThoughtStep>
            )}
          {task.status === "completed" && (
            <>
              <ChainOfThoughtStep
                label={t.subtasks.completed}
                icon={<CheckCircleIcon className="size-4" />}
              ></ChainOfThoughtStep>
              <ChainOfThoughtStep
                label={
                  task.result ? (
                    <MarkdownContent
                      content={task.result}
                      isLoading={false}
                      rehypePlugins={rehypePlugins}
                    />
                  ) : null
                }
              ></ChainOfThoughtStep>
            </>
          )}
          {task.status === "failed" && (
            <ChainOfThoughtStep
              label={<div className="text-red-500">{task.error}</div>}
              icon={<XCircleIcon className="size-4 text-red-500" />}
            ></ChainOfThoughtStep>
          )}
          <div className="flex justify-end pt-1">
            <Button
              aria-label={t.toolCalls.lessSteps}
              className="h-8 gap-1 text-xs"
              onClick={() => setCollapsed(true)}
              size="sm"
              type="button"
              variant="ghost"
            >
              <ChevronUp className="size-3" />
              {t.toolCalls.lessSteps}
            </Button>
          </div>
        </ChainOfThoughtContent>
      </div>
    </ChainOfThought>
  );
}
