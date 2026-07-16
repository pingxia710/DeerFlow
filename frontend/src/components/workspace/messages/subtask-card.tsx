import { useQuery } from "@tanstack/react-query";
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
import { Button } from "@/components/ui/button";
import {
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  fetch as fetchWithAuth,
} from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { hasToolCalls } from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { streamdownPluginsWithWordAnimation } from "@/core/streamdown";
import { SafeStreamdown } from "@/core/streamdown/components";
import { useSubtask } from "@/core/tasks/context";
import { formatElapsedMinutesSeconds } from "@/core/tasks/elapsed";
import type { Subtask } from "@/core/tasks/types";
import {
  buildRunMessagesUrl,
  readRunMessagesPageResponse,
} from "@/core/threads/hooks";
import { queryKeys } from "@/core/threads/query-keys";
import { terminalTaskToolResult } from "@/core/threads/task-events";
import { explainLastToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";

import { MarkdownContent } from "./markdown-content";

const MS_IN_SECOND = 1000;

export function getSubtaskAnchorId({
  id,
  roundId,
  runId,
}: Pick<Subtask, "id" | "roundId" | "runId">) {
  return `command-room-task:${JSON.stringify([
    runId ?? "",
    roundId ?? "",
    id,
  ])}`;
}

function taskDisplayName(task: Subtask, unnamedTask: string) {
  const description = task.description?.trim();
  if (description && description !== task.id) {
    return description;
  }
  if (task.subagent_type && task.subagent_type !== "task") {
    return task.subagent_type;
  }
  return unnamedTask;
}

function taskScopeLabel(task: Subtask, t: ReturnType<typeof useI18n>["t"]) {
  const container = task.commandRoomContainer;
  if (!container) {
    return null;
  }
  if (container === "context") {
    return t.chats.trajectory.context;
  }
  if (container === "planning") {
    return t.chats.trajectory.planResearch;
  }
  if (container === "technical-design") {
    return t.chats.trajectory.technicalDesign;
  }
  if (container === "execution" || container === "review") {
    const label =
      container === "execution"
        ? t.chats.trajectory.execution
        : t.chats.trajectory.review;
    return task.deliveryCycleIndex === undefined
      ? label
      : t.chats.trajectory.cycle(label, task.deliveryCycleIndex);
  }
  if (
    container === "project-steward" ||
    container === "debt-curation" ||
    container === "learning-curation"
  ) {
    return t.chats.trajectory.informationDeposit;
  }
  return container === "evaluation"
    ? t.chats.trajectory.evaluation
    : t.chats.trajectory.otherProcess;
}

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
  runId,
  roundId,
  taskId,
  threadId,
  isLoading,
}: {
  className?: string;
  runId: string;
  roundId?: string | null;
  taskId: string;
  threadId: string;
  isLoading: boolean;
}) {
  const { t } = useI18n();
  const storedTask = useSubtask({ id: taskId, threadId, runId, roundId });
  const task =
    storedTask ??
    (isLoading
      ? ({
          id: taskId,
          threadId,
          runId,
          ...(roundId ? { roundId } : {}),
          status: "in_progress",
          subagent_type: "",
          description: t.subtasks.in_progress,
          prompt: "",
        } satisfies Subtask)
      : null);

  if (!task) {
    return null;
  }

  return (
    <SubtaskCardBody className={className} isLoading={isLoading} task={task} />
  );
}

function SubtaskCardBody({
  className,
  isLoading,
  task,
}: {
  className?: string;
  isLoading: boolean;
  task: Subtask;
}) {
  const { t } = useI18n();
  const { scrollRef, stopScroll } = useStickToBottomContext();
  const [collapsed, setCollapsed] = useState(true);
  const isOpen = !collapsed;
  const canLoadFullResult = Boolean(task.threadId && task.runId);
  const fullResult = useQuery({
    queryKey: queryKeys.thread.taskResult(
      task.threadId ?? "",
      task.runId ?? "",
      task.id,
    ),
    queryFn: async () => {
      if (!task.threadId || !task.runId) {
        return null;
      }
      const response = await fetchWithAuth(
        buildRunMessagesUrl(getBackendBaseURL(), task.threadId, task.runId),
        {
          method: "GET",
          timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
        },
      );
      const page = await readRunMessagesPageResponse(response);
      return terminalTaskToolResult(page.data, task.id) ?? null;
    },
    enabled: isOpen && canLoadFullResult && task.status !== "in_progress",
    retry: false,
    staleTime: Infinity,
  });
  const completedResult = fullResult.data ?? task.result;
  const resultPreview = useMemo(
    () => completedResult?.replace(/\s+/g, " ").trim() ?? "",
    [completedResult],
  );
  const hasCompletedResult =
    task.status === "completed" && resultPreview.length > 0;
  const startedAtRef = useRef<number | null>(task.startedAt ?? null);
  const [elapsedSeconds, setElapsedSeconds] = useState<number | null>(() => {
    if (task.durationMs !== undefined) {
      return Math.floor(task.durationMs / MS_IN_SECOND);
    }
    if (task.status === "in_progress") {
      const startedAt = task.startedAt ?? startedAtRef.current;
      return startedAt === null
        ? null
        : Math.floor((Date.now() - startedAt) / MS_IN_SECOND);
    }
    if (task.startedAt !== undefined && task.finishedAt !== undefined) {
      return Math.floor((task.finishedAt - task.startedAt) / MS_IN_SECOND);
    }
    return null;
  });
  const elapsedText =
    elapsedSeconds === null
      ? null
      : formatElapsedMinutesSeconds(elapsedSeconds);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const name = taskDisplayName(task, t.chats.trajectory.unnamedTask);
  const scope = taskScopeLabel(task, t);
  const role =
    task.subagent_type && task.subagent_type !== "task"
      ? task.subagent_type
      : null;
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
    if (task.durationMs !== undefined) {
      setElapsedSeconds(Math.floor(task.durationMs / MS_IN_SECOND));
      if (task.status !== "in_progress") {
        startedAtRef.current = null;
      }
      return;
    }

    if (task.status !== "in_progress") {
      const startedAt = task.startedAt ?? startedAtRef.current;
      const finishedAt = task.finishedAt;
      if (
        startedAt !== null &&
        startedAt !== undefined &&
        finishedAt !== undefined
      ) {
        setElapsedSeconds(Math.floor((finishedAt - startedAt) / MS_IN_SECOND));
      } else {
        setElapsedSeconds(null);
      }
      startedAtRef.current = null;
      return;
    }

    if (
      task.startedAt !== undefined &&
      (startedAtRef.current === null || task.startedAt < startedAtRef.current)
    ) {
      startedAtRef.current = task.startedAt;
    }

    const startedAt = startedAtRef.current;
    if (startedAt === null) {
      setElapsedSeconds(null);
      return;
    }
    const updateElapsed = () => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / MS_IN_SECOND));
    };

    updateElapsed();
    const interval = window.setInterval(updateElapsed, MS_IN_SECOND);

    return () => window.clearInterval(interval);
  }, [task.durationMs, task.finishedAt, task.startedAt, task.status]);

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
      className={cn(
        "bg-background w-full scroll-mt-20 gap-2 rounded-md border py-0 transition-shadow",
        className,
      )}
      data-command-room-task
      id={getSubtaskAnchorId(task)}
      open={isOpen}
    >
      <div className="flex w-full flex-col">
        <div className="flex w-full items-center justify-between p-0.5">
          <Button
            className="h-auto min-h-9 w-full items-start justify-start text-left"
            variant="ghost"
            onClick={handleHeaderToggle}
          >
            <div className="flex w-full flex-col items-stretch gap-1 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 flex-1">
                <ChainOfThoughtStep
                  className="font-normal"
                  label={name}
                  icon={<ClipboardListIcon />}
                ></ChainOfThoughtStep>
                {(role ?? scope) && (
                  <p className="text-muted-foreground truncate px-6 pb-1 text-xs">
                    {[role, scope].filter(Boolean).join(" · ")}
                  </p>
                )}
                {!isOpen && hasCompletedResult && (
                  <p className="text-muted-foreground line-clamp-2 px-6 pb-1 text-xs leading-5 whitespace-normal">
                    {resultPreview}
                  </p>
                )}
              </div>
              <div className="flex items-center justify-end gap-1 sm:justify-start">
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
                  fullResult.isFetching ? (
                    <span className="text-muted-foreground flex items-center gap-2">
                      <Loader2Icon className="size-4 animate-spin" />
                      {t.common.loading}
                    </span>
                  ) : completedResult ? (
                    <MarkdownContent
                      content={completedResult}
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
