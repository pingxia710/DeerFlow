import { useQuery } from "@tanstack/react-query";
import {
  AlertCircleIcon,
  CheckCircleIcon,
  ChevronUp,
  ClipboardListIcon,
  DownloadIcon,
  EyeIcon,
  Loader2Icon,
  RefreshCcwIcon,
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
import { urlOfArtifact } from "@/core/artifacts/utils";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { hasToolCalls } from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { isStaticWebsiteOnly } from "@/core/static-mode";
import { streamdownPluginsWithWordAnimation } from "@/core/streamdown";
import { SafeStreamdown } from "@/core/streamdown/components";
import { useSubtask } from "@/core/tasks/context";
import { formatElapsedMinutesSeconds } from "@/core/tasks/elapsed";
import type { Subtask } from "@/core/tasks/types";
import { wakeFactForTask } from "@/core/threads/command-room-read-model";
import {
  buildRunMessagesUrl,
  readRunMessagesPageResponse,
  useThreadWakeFacts,
} from "@/core/threads/hooks";
import { queryKeys } from "@/core/threads/query-keys";
import { terminalTaskToolResult } from "@/core/threads/task-events";
import { explainLastToolCall } from "@/core/tools/utils";
import { getFileName } from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";

import { MarkdownContent } from "./markdown-content";

const MS_IN_SECOND = 1000;

type RunArtifact = {
  available: boolean;
  taskId?: string;
  virtualPath: string;
};

type SubtaskArtifactSource = Pick<Subtask, "id" | "metadata" | "details">;

function addArtifactReferences(value: unknown, references: Set<string>) {
  if (typeof value === "string") {
    const reference = value.trim();
    if (reference) {
      references.add(reference);
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => addArtifactReferences(item, references));
    return;
  }
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    for (const key of ["virtual_path", "path", "output_ref"] as const) {
      addArtifactReferences(record[key], references);
    }
  }
}

export function subtaskArtifactReferences(task: SubtaskArtifactSource) {
  const references = new Set<string>();
  for (const source of [task.metadata, task.details]) {
    const refs = source?.refs;
    if (typeof refs !== "object" || refs === null) {
      continue;
    }
    const record = refs as Record<string, unknown>;
    addArtifactReferences(record.artifact_refs, references);
    addArtifactReferences(record.output_refs, references);
  }
  return [...references];
}

export function selectSubtaskArtifacts(
  artifacts: RunArtifact[],
  task: SubtaskArtifactSource,
) {
  const references = new Set(subtaskArtifactReferences(task));
  const matched = artifacts.filter(
    (artifact) =>
      artifact.taskId === task.id || references.has(artifact.virtualPath),
  );
  const available = new Map<string, RunArtifact>();
  for (const artifact of matched) {
    if (artifact.available) {
      available.set(artifact.virtualPath, artifact);
    }
  }
  return {
    available: [...available.values()],
    hasUnavailable:
      matched.some((artifact) => !artifact.available) ||
      [...references].some(
        (reference) =>
          !matched.some((artifact) => artifact.virtualPath === reference),
      ),
  };
}

function buildRunArtifactsUrl(
  baseUrl: string,
  threadId: string,
  runId: string,
) {
  const base = baseUrl.replace(/\/$/, "");
  return `${base}/api/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/artifacts`;
}

async function loadRunArtifacts(threadId: string, runId: string) {
  const response = await fetchWithAuth(
    buildRunArtifactsUrl(getBackendBaseURL(), threadId, runId),
    { method: "GET", timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS },
  );
  if (!response.ok) {
    throw new Error("Failed to load run artifacts.");
  }
  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) {
    throw new Error("Invalid run artifacts response.");
  }
  return payload.flatMap((item): RunArtifact[] => {
    if (typeof item !== "object" || item === null) {
      return [];
    }
    const artifact = item as Record<string, unknown>;
    return typeof artifact.virtual_path === "string" && artifact.virtual_path
      ? [
          {
            available: artifact.available === true,
            ...(typeof artifact.task_id === "string"
              ? { taskId: artifact.task_id }
              : {}),
            virtualPath: artifact.virtual_path,
          },
        ]
      : [];
  });
}

function SubtaskArtifactLinks({
  runId,
  task,
  threadId,
}: {
  runId: string;
  task: Subtask;
  threadId: string;
}) {
  const { t } = useI18n();
  const references = useMemo(() => subtaskArtifactReferences(task), [task]);
  const canLoad =
    references.length > 0 &&
    task.status !== "in_progress" &&
    task.status !== "queued" &&
    !isStaticWebsiteOnly();
  const runArtifacts = useQuery({
    queryKey: queryKeys.thread.runArtifacts(threadId, runId),
    queryFn: () => loadRunArtifacts(threadId, runId),
    enabled: canLoad,
    retry: false,
    staleTime: Infinity,
  });

  if (references.length === 0) {
    return null;
  }
  if (task.status === "queued") {
    return (
      <p className="text-muted-foreground px-3 pb-2 text-xs" role="status">
        {t.subtasks.artifactQueued}
      </p>
    );
  }
  if (task.status === "in_progress") {
    return (
      <p className="text-muted-foreground px-3 pb-2 text-xs" role="status">
        {t.subtasks.artifactPending}
      </p>
    );
  }
  if (isStaticWebsiteOnly()) {
    return (
      <p className="text-muted-foreground px-3 pb-2 text-xs" role="status">
        {t.subtasks.artifactUnavailable}
      </p>
    );
  }
  if (runArtifacts.isFetching) {
    return (
      <p
        aria-live="polite"
        className="text-muted-foreground flex items-center gap-2 px-3 pb-2 text-xs"
        role="status"
      >
        <Loader2Icon aria-hidden className="size-3 animate-spin" />
        {t.subtasks.artifactLoading}
      </p>
    );
  }
  if (runArtifacts.isError) {
    return (
      <p className="text-muted-foreground px-3 pb-2 text-xs" role="status">
        {t.subtasks.artifactCheckFailed}
      </p>
    );
  }

  const artifacts = selectSubtaskArtifacts(runArtifacts.data ?? [], task);
  if (artifacts.available.length === 0) {
    return (
      <p className="text-muted-foreground px-3 pb-2 text-xs" role="status">
        {t.subtasks.artifactUnavailable}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2 px-3 pb-2">
      {artifacts.available.map((artifact) => {
        const name = getFileName(artifact.virtualPath);
        return (
          <div
            className="bg-muted/40 flex min-w-0 flex-wrap items-center gap-2 rounded-sm px-2 py-1.5"
            key={artifact.virtualPath}
          >
            <span
              className="min-w-0 flex-1 truncate text-xs"
              title={artifact.virtualPath}
            >
              {name}
            </span>
            <Button
              asChild
              className="h-7 gap-1 text-xs"
              size="sm"
              variant="outline"
            >
              <a
                aria-label={t.subtasks.viewArtifact(name)}
                href={urlOfArtifact({
                  filepath: artifact.virtualPath,
                  threadId,
                })}
                rel="noopener noreferrer"
                target="_blank"
              >
                <EyeIcon aria-hidden className="size-3" />
                {t.subtasks.viewArtifact(name)}
              </a>
            </Button>
            <Button
              asChild
              className="h-7 gap-1 text-xs"
              size="sm"
              variant="outline"
            >
              <a
                aria-label={t.subtasks.downloadArtifact(name)}
                href={urlOfArtifact({
                  filepath: artifact.virtualPath,
                  threadId,
                  download: true,
                })}
                rel="noopener noreferrer"
                target="_blank"
              >
                <DownloadIcon aria-hidden className="size-3" />
                {t.subtasks.downloadArtifact(name)}
              </a>
            </Button>
          </div>
        );
      })}
      {artifacts.hasUnavailable && (
        <p className="text-muted-foreground text-xs" role="status">
          {t.subtasks.artifactUnavailable}
        </p>
      )}
    </div>
  );
}

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
  onRetryRecovery,
}: {
  className?: string;
  runId: string;
  roundId?: string | null;
  taskId: string;
  threadId: string;
  isLoading: boolean;
  onRetryRecovery?: (threadId?: string, runId?: string) => Promise<void> | void;
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
    <SubtaskCardBody
      className={className}
      isLoading={isLoading}
      onRetryRecovery={onRetryRecovery}
      runId={runId}
      task={task}
      threadId={threadId}
    />
  );
}

function SubtaskCardBody({
  className,
  isLoading,
  onRetryRecovery,
  runId,
  task,
  threadId,
}: {
  className?: string;
  isLoading: boolean;
  onRetryRecovery?: (threadId?: string, runId?: string) => Promise<void> | void;
  runId: string;
  task: Subtask;
  threadId: string;
}) {
  const { t } = useI18n();
  const { scrollRef, stopScroll } = useStickToBottomContext();
  const [collapsed, setCollapsed] = useState(true);
  const [isRetryingRecovery, setIsRetryingRecovery] = useState(false);
  const isOpen = !collapsed;
  const wakeFacts = useThreadWakeFacts(threadId, {
    runId,
    roundId: task.roundId,
    enabled: isOpen && Boolean(task.roundId),
  });
  const backgroundWake = wakeFactForTask(wakeFacts.data, task.id);
  const retryRecovery = () => {
    if (!onRetryRecovery || isRetryingRecovery) {
      return;
    }
    setIsRetryingRecovery(true);
    void Promise.resolve()
      .then(() => onRetryRecovery(threadId, runId))
      .catch(() => undefined)
      .finally(() => setIsRetryingRecovery(false));
  };
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
    enabled:
      isOpen &&
      canLoadFullResult &&
      task.status !== "in_progress" &&
      task.status !== "queued",
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
  const name = taskDisplayName(task, t.subtasks.subtask);
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
    } else if (task.status === "queued") {
      return <ClipboardListIcon className="size-3" />;
    } else if (task.status === "unknown") {
      return <AlertCircleIcon className="size-3" />;
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
                {role && (
                  <p className="text-muted-foreground truncate px-6 pb-1 text-xs">
                    {role}
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
        {backgroundWake && (
          <p
            className="mx-3 mb-2 rounded-sm border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-950 dark:text-amber-100"
            role="alert"
          >
            {t.subtasks.backgroundWakeFailed(backgroundWake.wake_attempts)}
          </p>
        )}
        {subtaskArtifactReferences(task).length > 0 && (
          <SubtaskArtifactLinks runId={runId} task={task} threadId={threadId} />
        )}
        {task.status === "unknown" && onRetryRecovery && (
          <div className="px-3 pb-2">
            <Button
              aria-busy={isRetryingRecovery}
              className="h-8 gap-1 text-xs"
              disabled={isRetryingRecovery}
              onClick={retryRecovery}
              size="sm"
              type="button"
              variant="outline"
            >
              {isRetryingRecovery ? (
                <Loader2Icon className="size-3 animate-spin" />
              ) : (
                <RefreshCcwIcon className="size-3" />
              )}
              {t.chats.retryRecovery}
            </Button>
          </div>
        )}
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
          {task.status === "queued" && (
            <ChainOfThoughtStep
              label={t.subtasks.queued}
              icon={<ClipboardListIcon className="size-4" />}
            ></ChainOfThoughtStep>
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
          {task.status === "unknown" && (
            <ChainOfThoughtStep
              label={t.subtasks.recoveryFailedUnknown}
              icon={<AlertCircleIcon className="size-4" />}
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
