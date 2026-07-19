"use client";

import {
  GitBranchIcon,
  HistoryIcon,
  InboxIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  TargetIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { Locale } from "@/core/i18n";
import { useI18n } from "@/core/i18n/hooks";
import {
  goalCellRows,
  type GoalWorkspaceHistoryEvent,
  type GoalWorkspaceRecord,
  type GoalWorkspaceResult,
} from "@/core/threads/goal-workspace";
import {
  useThreadGoalTree,
  useThreadGoalWorkspace,
  useThreadGoalWorkspaceHistory,
  useThreadRuntimeSnapshot,
  useThreadTimeline,
  useThreadWakeFacts,
} from "@/core/threads/hooks";
import { isActiveRunStatus } from "@/core/threads/run-status";
import {
  isWorkRecordFact,
  type ThreadTimelineRecord,
} from "@/core/threads/thread-timeline";
import { pathOfThread } from "@/core/threads/utils";
import { formatDateTime, formatDuration } from "@/core/utils/datetime";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { Tooltip } from "../tooltip";

type WorkRecordToggleProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

const ACTIVE_TASK_LANE_STATUSES = new Set([
  "in_progress",
  "running",
  "pending",
  "executing",
]);
const RUNNING_TASK_LANE_STATUSES = new Set([
  "in_progress",
  "running",
  "executing",
]);

function useWorkActivity(threadId: string, enabled: boolean) {
  const { t } = useI18n();
  const snapshot = useThreadRuntimeSnapshot(threadId, { enabled });
  const activeTaskCount =
    snapshot.data?.task_lanes?.filter((lane) =>
      ACTIVE_TASK_LANE_STATUSES.has(lane.status),
    ).length ?? 0;

  if (activeTaskCount > 0) {
    return t.chats.workRecord.tasksRunning(activeTaskCount);
  }
  return snapshot.data?.runs?.some((run) => isActiveRunStatus(run.status))
    ? t.chats.workRecord.runRunning
    : null;
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function recordLabel(
  record: ThreadTimelineRecord,
  t: ReturnType<typeof useI18n>["t"],
) {
  switch (record.eventType) {
    case "task_started":
      return t.chats.workRecord.taskStarted;
    case "task_completed":
      return t.chats.workRecord.taskCompleted;
    case "task_failed":
      return t.chats.workRecord.taskFailed;
    case "task_cancelled":
      return t.chats.workRecord.taskCancelled;
    case "task_timed_out":
      return t.chats.workRecord.taskTimedOut;
    default:
      return record.category === "artifact"
        ? t.chats.workRecord.artifactRecorded
        : t.chats.workRecord.runLifecycle;
  }
}

function detailForRecord(
  record: ThreadTimelineRecord,
  t: ReturnType<typeof useI18n>["t"],
) {
  if (isObjectRecord(record.content)) {
    for (const field of ["description", "subagent_type", "role", "status"]) {
      const value = record.content[field];
      if (typeof value === "string" && value.length > 0) {
        return value;
      }
    }
    const taskId = record.content.task_id;
    if (typeof taskId === "string" && taskId.length > 0) {
      return t.subtasks.subtask;
    }
  }
  return record.runId;
}

function WorkspaceRecord({
  empty,
  label,
  record,
  open = false,
}: {
  empty: string;
  label: string;
  record: GoalWorkspaceRecord | null | undefined;
  open?: boolean;
}) {
  const { t } = useI18n();
  if (!record) {
    return <p className="text-muted-foreground text-xs">{empty}</p>;
  }
  return (
    <details className="group bg-background/70 rounded-md border" open={open}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-xs font-medium">
        <span>{label}</span>
        <span className="text-muted-foreground font-mono text-[10px]">
          {t.chats.workRecord.revision(record.revision)}
        </span>
      </summary>
      <div className="border-t px-3 py-3">
        <p className="max-h-80 overflow-y-auto text-sm leading-6 break-words whitespace-pre-wrap">
          {record.body}
        </p>
      </div>
    </details>
  );
}

function resultHeading(result: GoalWorkspaceResult, fallback: string) {
  const role = result.metadata.role;
  const description = result.metadata.description;
  if (typeof description === "string" && description.trim()) {
    return description;
  }
  return typeof role === "string" && role.trim() ? role : fallback;
}

function historyFactDetails(event: GoalWorkspaceHistoryEvent, locale: Locale) {
  const facts = [
    event.eventType,
    event.authorRunId ? `author_run_id: ${event.authorRunId}` : null,
    formatDateTime(event.createdAt, locale) ?? event.createdAt,
  ];
  const metadata = JSON.stringify(event.metadata);
  if (metadata !== "{}") {
    facts.push(`metadata: ${metadata}`);
  }
  return facts.filter((fact): fact is string => Boolean(fact)).join(" · ");
}

function WorkRecordBody({
  threadId,
  enabled,
  onClose,
}: {
  threadId: string;
  enabled: boolean;
  onClose: () => void;
}) {
  const { locale, t } = useI18n();
  const [historyOpen, setHistoryOpen] = useState(false);
  const activity = useWorkActivity(threadId, enabled);
  const goalWorkspace = useThreadGoalWorkspace(threadId, { enabled });
  const workspaceHistory = useThreadGoalWorkspaceHistory(threadId, {
    enabled: enabled && historyOpen,
  });
  const goalTree = useThreadGoalTree(threadId, { enabled });
  const snapshot = useThreadRuntimeSnapshot(threadId, { enabled });
  const runningTaskLanes =
    snapshot.data?.task_lanes?.filter((lane) =>
      RUNNING_TASK_LANE_STATUSES.has(lane.status),
    ) ?? [];
  const hasRunningTaskLanes = runningTaskLanes.length > 0;
  const [now, setNow] = useState(0);
  const timeline = useThreadTimeline(threadId, { enabled });
  const wakeScope = useMemo(() => {
    const runId = snapshot.data?.runs?.[0]?.run_id;
    const roundId = snapshot.data?.rounds?.find(
      (round) => round.current_run_id === runId,
    )?.round_id;
    return { runId, roundId };
  }, [snapshot.data?.rounds, snapshot.data?.runs]);
  const wakeFacts = useThreadWakeFacts(threadId, {
    ...wakeScope,
    enabled,
    poll: true,
  });
  const backgroundWakeFailures = wakeFacts.data?.items ?? [];
  const records = (timeline.data?.records.filter(isWorkRecordFact) ?? []).sort(
    (left, right) => right.seq - left.seq,
  );
  const cellRows = useMemo(
    () => (goalTree.data ? goalCellRows(goalTree.data) : []),
    [goalTree.data],
  );
  const workspace = goalWorkspace.data;
  const historyEvents = useMemo(
    () => workspaceHistory.data?.pages.flatMap((page) => page.events) ?? [],
    [workspaceHistory.data],
  );

  useEffect(() => {
    if (!hasRunningTaskLanes) {
      return;
    }
    const updateNow = () => setNow(Date.now());
    updateNow();
    const interval = window.setInterval(updateNow, 1_000);
    return () => window.clearInterval(interval);
  }, [hasRunningTaskLanes]);

  return (
    <>
      <header className="flex shrink-0 items-start justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{t.chats.workRecord.title}</h2>
          {activity && (
            <p className="text-muted-foreground mt-1 text-xs" role="status">
              {activity}
            </p>
          )}
          {timeline.data?.truncated && (
            <p className="text-muted-foreground mt-1 text-xs">
              {t.chats.workRecord.truncated}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Tooltip content={t.chats.workRecord.retry}>
            <Button
              aria-label={t.chats.workRecord.retry}
              disabled={
                !enabled ||
                timeline.isFetching ||
                goalWorkspace.isFetching ||
                (historyOpen && workspaceHistory.isFetching) ||
                goalTree.isFetching
              }
              size="icon-sm"
              type="button"
              variant="ghost"
              onClick={() => {
                void Promise.all([
                  timeline.refetch(),
                  goalWorkspace.refetch(),
                  goalTree.refetch(),
                  ...(historyOpen ? [workspaceHistory.refetch()] : []),
                ]);
              }}
            >
              <RefreshCwIcon className="size-4" />
            </Button>
          </Tooltip>
          <Tooltip content={t.chats.workRecord.close}>
            <Button
              aria-label={t.chats.workRecord.close}
              size="icon-sm"
              type="button"
              variant="ghost"
              onClick={onClose}
            >
              <XIcon className="size-4" />
            </Button>
          </Tooltip>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto pb-4">
        {hasRunningTaskLanes && (
          <section className="space-y-3 border-b px-4 py-4">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <LoaderCircleIcon
                  aria-hidden
                  className="size-3.5 animate-spin text-blue-600 motion-reduce:animate-none dark:text-blue-400"
                />
                <h3 className="text-xs font-semibold tracking-wide uppercase">
                  {t.chats.workRecord.runningSubtasks}
                </h3>
              </div>
              <span className="bg-muted rounded-full px-2 py-0.5 font-mono text-[10px]">
                {runningTaskLanes.length}
              </span>
            </div>
            <ol className="space-y-2">
              {runningTaskLanes.map((lane) => {
                const startedAt = Date.parse(lane.started_at ?? "");
                const elapsed =
                  formatDuration(
                    Number.isFinite(startedAt) && now > 0
                      ? Math.max(0, now - startedAt)
                      : 0,
                  ) ?? "00:00";
                const name =
                  [lane.description, lane.role, lane.subagent_type]
                    .find((value) => value?.trim())
                    ?.trim() ?? lane.task_id;
                return (
                  <li
                    className="flex items-center justify-between gap-3 rounded-md border border-blue-500/25 bg-blue-500/[0.04] px-3 py-2.5"
                    key={`${lane.run_id}:${lane.round_id ?? ""}:${lane.task_id}`}
                  >
                    <span className="min-w-0 truncate text-xs font-medium">
                      {name}
                    </span>
                    <time
                      aria-label={t.chats.workRecord.executionTime(elapsed)}
                      className="text-muted-foreground shrink-0 font-mono text-xs tabular-nums"
                    >
                      {elapsed}
                    </time>
                  </li>
                );
              })}
            </ol>
          </section>
        )}
        <section className="space-y-3 border-b px-4 py-4">
          <div className="flex items-center gap-2">
            <TargetIcon className="size-3.5 text-blue-600 dark:text-blue-400" />
            <h3 className="text-xs font-semibold tracking-wide uppercase">
              {t.chats.workRecord.goalWorkspace}
            </h3>
          </div>
          {goalWorkspace.isLoading ? (
            <p className="text-muted-foreground text-xs">
              {t.chats.workRecord.loadingGoalWorkspace}
            </p>
          ) : goalWorkspace.isError ? (
            <p className="text-muted-foreground text-xs" role="alert">
              {t.chats.workRecord.goalWorkspaceUnavailable}
            </p>
          ) : (
            <div className="space-y-2">
              <WorkspaceRecord
                empty={t.chats.workRecord.noMandate}
                label={t.chats.workRecord.goalMandate}
                record={workspace?.goalMandate}
              />
              <WorkspaceRecord
                empty={t.chats.workRecord.noOperatingBrief}
                label={t.chats.workRecord.operatingBrief}
                record={workspace?.operatingBrief}
                open
              />
              <WorkspaceRecord
                empty={t.chats.workRecord.noOrganizationMap}
                label={t.chats.workRecord.organizationMap}
                record={workspace?.organizationMap}
              />
            </div>
          )}
        </section>

        <section className="space-y-3 border-b px-4 py-4">
          <div className="flex items-center gap-2">
            <HistoryIcon className="size-3.5 text-slate-600 dark:text-slate-400" />
            <h3 className="text-xs font-semibold tracking-wide uppercase">
              {t.chats.workRecord.workspaceHistory}
            </h3>
          </div>
          <details
            className="group rounded-md border"
            onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
          >
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-xs font-medium">
              <span>{t.chats.workRecord.workspaceHistory}</span>
              <span className="text-muted-foreground text-[10px]">
                {t.chats.workRecord.workspaceHistoryOnDemand}
              </span>
            </summary>
            <div className="space-y-2 border-t px-3 py-3">
              {workspaceHistory.isLoading ? (
                <p className="text-muted-foreground text-xs">
                  {t.chats.workRecord.loadingWorkspaceHistory}
                </p>
              ) : workspaceHistory.isError ? (
                <p className="text-muted-foreground text-xs" role="alert">
                  {t.chats.workRecord.workspaceHistoryUnavailable}
                </p>
              ) : historyEvents.length === 0 ? (
                <p className="text-muted-foreground text-xs">
                  {t.chats.workRecord.noWorkspaceHistory}
                </p>
              ) : (
                <div className="space-y-2">
                  {historyEvents.map((event) => (
                    <details
                      className="bg-background/70 rounded-md border"
                      key={event.revision}
                    >
                      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-xs">
                        <span className="truncate font-medium">
                          {event.eventType}
                        </span>
                        <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
                          #{event.revision}
                        </span>
                      </summary>
                      <div className="border-t px-3 py-3">
                        <p className="text-muted-foreground mb-2 font-mono text-[10px] break-words">
                          {historyFactDetails(event, locale)}
                        </p>
                        <p className="max-h-96 overflow-y-auto text-sm leading-6 break-words whitespace-pre-wrap">
                          {event.body}
                        </p>
                      </div>
                    </details>
                  ))}
                </div>
              )}
              {workspaceHistory.hasNextPage && (
                <Button
                  className="w-full justify-center text-xs"
                  disabled={workspaceHistory.isFetchingNextPage}
                  size="sm"
                  type="button"
                  variant="ghost"
                  onClick={() => void workspaceHistory.fetchNextPage()}
                >
                  {workspaceHistory.isFetchingNextPage
                    ? t.chats.workRecord.loadingOlderWorkspaceFacts
                    : t.chats.workRecord.loadOlderWorkspaceFacts}
                </Button>
              )}
            </div>
          </details>
        </section>

        <section className="space-y-3 border-b px-4 py-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <InboxIcon className="size-3.5 text-amber-600 dark:text-amber-400" />
              <h3 className="text-xs font-semibold tracking-wide uppercase">
                {t.chats.workRecord.resultInbox}
              </h3>
            </div>
            <span className="bg-muted rounded-full px-2 py-0.5 font-mono text-[10px]">
              {workspace?.results.length ?? 0}
            </span>
          </div>
          {workspace && (
            <p className="text-muted-foreground font-mono text-[10px]">
              {t.chats.workRecord.inboxFacts(
                workspace.notifiedThroughSeq,
                workspace.acknowledgedThroughSeq,
              )}
            </p>
          )}
          {goalWorkspace.isLoading ? (
            <p className="text-muted-foreground text-xs">
              {t.chats.workRecord.loadingGoalWorkspace}
            </p>
          ) : goalWorkspace.isError ? (
            <p className="text-muted-foreground text-xs" role="alert">
              {t.chats.workRecord.goalWorkspaceUnavailable}
            </p>
          ) : (workspace?.results.length ?? 0) === 0 ? (
            <p className="text-muted-foreground text-xs">
              {t.chats.workRecord.noResults}
            </p>
          ) : (
            <div className="space-y-2">
              {workspace?.results.map((result) => (
                <details
                  className="rounded-md border border-amber-500/25 bg-amber-500/[0.04]"
                  key={result.revision}
                >
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-xs">
                    <span className="truncate font-medium">
                      {resultHeading(result, t.chats.workRecord.result)}
                    </span>
                    <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
                      #{result.revision}
                    </span>
                  </summary>
                  <div className="border-t border-amber-500/20 px-3 py-3">
                    <p className="max-h-96 overflow-y-auto text-sm leading-6 break-words whitespace-pre-wrap">
                      {result.body}
                    </p>
                  </div>
                </details>
              ))}
            </div>
          )}
        </section>

        <section className="space-y-3 border-b px-4 py-4">
          <div className="flex items-center gap-2">
            <GitBranchIcon className="size-3.5 text-violet-600 dark:text-violet-400" />
            <h3 className="text-xs font-semibold tracking-wide uppercase">
              {t.chats.workRecord.goalTree}
            </h3>
          </div>
          {goalTree.isLoading ? (
            <p className="text-muted-foreground text-xs">
              {t.chats.workRecord.loadingGoalWorkspace}
            </p>
          ) : goalTree.isError ? (
            <p className="text-muted-foreground text-xs" role="alert">
              {t.chats.workRecord.goalTreeUnavailable}
            </p>
          ) : cellRows.length === 0 ? (
            <p className="text-muted-foreground text-xs">
              {t.chats.workRecord.noGoalCells}
            </p>
          ) : (
            <ol className="space-y-1.5 overflow-x-auto pb-1">
              {cellRows.map((cell) => (
                <li
                  className="relative min-w-max border-l border-violet-500/25 py-1 pl-3"
                  key={cell.threadId}
                  style={{ marginInlineStart: `${cell.depth * 14}px` }}
                >
                  <span className="bg-background absolute top-3 -left-1 size-2 rounded-full border border-violet-500/50" />
                  <Link
                    className="hover:bg-muted/60 focus-visible:ring-ring block rounded-sm px-2 py-1 outline-none focus-visible:ring-2"
                    href={pathOfThread(cell.threadId)}
                  >
                    <span className="block max-w-64 truncate text-xs font-medium">
                      {cell.displayName ?? t.chats.workRecord.goalCell}
                    </span>
                    <span className="text-muted-foreground mt-0.5 block font-mono text-[10px]">
                      {t.chats.workRecord.runtimeStatus(cell.runtimeStatus)}
                    </span>
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </section>

        {backgroundWakeFailures.length > 0 && (
          <div className="space-y-2 border-b px-4 py-3">
            {backgroundWakeFailures.map((task) => (
              <p
                className="rounded-sm border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-950 dark:text-amber-100"
                key={`${task.task_id}:${task.source_run_id}:${wakeFacts.data?.round_id}`}
                role="alert"
              >
                {t.chats.workRecord.backgroundWakeFailed(
                  task.task_id,
                  task.source_run_id,
                  wakeFacts.data?.round_id ?? "",
                  task.wake_attempts,
                )}
              </p>
            ))}
          </div>
        )}
        {timeline.isLoading ? (
          <p className="text-muted-foreground px-4 py-6 text-sm">
            {t.chats.workRecord.loading}
          </p>
        ) : timeline.isError ? (
          <p className="text-muted-foreground px-4 py-6 text-sm" role="alert">
            {t.chats.workRecord.unavailable}
          </p>
        ) : records.length === 0 ? (
          <p className="text-muted-foreground px-4 py-6 text-sm">
            {t.chats.workRecord.empty}
          </p>
        ) : records.length > 0 ? (
          <details className="border-border/60 border-t">
            <summary className="text-muted-foreground flex min-h-10 cursor-pointer list-none items-center justify-between gap-3 px-4 py-2 text-xs font-medium">
              <span>{t.chats.workRecord.eventHistory}</span>
              <span>{records.length}</span>
            </summary>
            <ol className="divide-border/70 border-t px-4">
              {records.map((record) => {
                const time = formatDateTime(record.createdAt, locale);
                return (
                  <li
                    className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 py-3"
                    key={record.eventId}
                  >
                    <span className="bg-muted-foreground/40 mt-2 size-1.5 rounded-full" />
                    <div className="min-w-0">
                      <div className="flex min-w-0 items-center justify-between gap-3">
                        <span className="truncate text-sm">
                          {recordLabel(record, t)}
                        </span>
                        {time && (
                          <time
                            className="text-muted-foreground shrink-0 text-xs"
                            dateTime={record.createdAt}
                          >
                            {time}
                          </time>
                        )}
                      </div>
                      <p className="text-muted-foreground mt-0.5 truncate text-xs">
                        {detailForRecord(record, t)}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ol>
          </details>
        ) : null}
      </div>
    </>
  );
}

export function ThreadWorkRecordTrigger({
  open,
  onOpenChange,
}: WorkRecordToggleProps) {
  const { t } = useI18n();

  if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true") {
    return null;
  }
  return (
    <Tooltip content={t.chats.workRecord.open}>
      <Button
        aria-label={t.chats.workRecord.open}
        aria-pressed={open}
        size="icon-sm"
        type="button"
        variant="ghost"
        onClick={() => onOpenChange(!open)}
      >
        <HistoryIcon className="size-4" />
      </Button>
    </Tooltip>
  );
}

export function ThreadWorkActivityTrigger({
  enabled,
  threadId,
  onOpen,
}: {
  enabled: boolean;
  threadId: string;
  onOpen: () => void;
}) {
  const activity = useWorkActivity(threadId, enabled);

  if (!activity) {
    return null;
  }
  return (
    <Button
      aria-label={activity}
      className="h-7 max-w-40 gap-1.5 px-2 text-xs"
      size="sm"
      type="button"
      variant="outline"
      onClick={onOpen}
    >
      <LoaderCircleIcon
        aria-hidden
        className="size-3.5 shrink-0 animate-spin"
      />
      <span className="truncate">{activity}</span>
    </Button>
  );
}

export function ThreadWorkRecordPanel({
  enabled,
  threadId,
  mobile,
  open,
  onOpenChange,
}: {
  enabled: boolean;
  threadId: string;
  mobile: boolean;
} & WorkRecordToggleProps) {
  const { t } = useI18n();
  const close = () => onOpenChange(false);

  if (mobile) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          aria-describedby={undefined}
          className="h-[min(80dvh,42rem)] gap-0 rounded-t-md p-0 [&>button]:hidden"
          side="bottom"
        >
          <SheetHeader className="sr-only">
            <SheetTitle>{t.chats.workRecord.title}</SheetTitle>
          </SheetHeader>
          <WorkRecordBody
            enabled={enabled}
            threadId={threadId}
            onClose={close}
          />
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <aside
      aria-label={t.chats.workRecord.title}
      className={cn("flex size-full min-w-0 flex-col", !open && "invisible")}
    >
      <WorkRecordBody enabled={enabled} threadId={threadId} onClose={close} />
    </aside>
  );
}
