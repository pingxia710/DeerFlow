"use client";

import {
  HistoryIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  XIcon,
} from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useI18n } from "@/core/i18n/hooks";
import { getCommandRoomStepMessages } from "@/core/messages/utils";
import { useSubtasksForThread } from "@/core/tasks/context";
import {
  useThreadRuntimeSnapshot,
  useThreadTimeline,
} from "@/core/threads/hooks";
import { isActiveRunStatus } from "@/core/threads/run-status";
import { mergeTaskLaneSubtasks } from "@/core/threads/task-events";
import {
  isTaskTimelineRecord,
  isWorkRecordFact,
  type ThreadTimelineRecord,
} from "@/core/threads/thread-timeline";
import { formatDateTime } from "@/core/utils/datetime";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { CommandRoomTrajectory } from "../messages/command-room-trajectory";
import { useThread } from "../messages/context";
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

function WorkRecordBody({
  threadId,
  enabled,
  mobile,
  onClose,
}: {
  threadId: string;
  enabled: boolean;
  mobile: boolean;
  onClose: () => void;
}) {
  const { locale, t } = useI18n();
  const { thread } = useThread();
  const activity = useWorkActivity(threadId, enabled);
  const snapshot = useThreadRuntimeSnapshot(threadId, { enabled });
  const timeline = useThreadTimeline(threadId, { enabled });
  const contextSubtasks = useSubtasksForThread(threadId);
  const tasks = useMemo(
    () =>
      mergeTaskLaneSubtasks(snapshot.data?.task_lanes ?? [], contextSubtasks),
    [contextSubtasks, snapshot.data?.task_lanes],
  );
  const chairMessages = useMemo(
    () => getCommandRoomStepMessages(thread.messages),
    [thread.messages],
  );
  const hasTaskOverview = tasks.length > 0;
  const records = (timeline.data?.records.filter(isWorkRecordFact) ?? [])
    .filter((record) => !hasTaskOverview || !isTaskTimelineRecord(record))
    .sort((left, right) => right.seq - left.seq);
  const navigateToChat = (anchorId: string) => {
    if (mobile) {
      onClose();
    }
    window.requestAnimationFrame(() => {
      const target = document.getElementById(anchorId);
      if (!target) {
        return;
      }
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.animate(
        [
          {
            boxShadow:
              "0 0 0 2px color-mix(in oklab, var(--primary) 45%, transparent)",
          },
          { boxShadow: "0 0 0 2px transparent" },
        ],
        { duration: 1200, easing: "ease-out" },
      );
    });
  };

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
              disabled={!enabled || timeline.isFetching}
              size="icon-sm"
              type="button"
              variant="ghost"
              onClick={() => {
                void timeline.refetch();
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
        {hasTaskOverview && (
          <CommandRoomTrajectory
            chairMessages={chairMessages}
            tasks={tasks}
            onNavigate={navigateToChat}
          />
        )}
        {timeline.isLoading ? (
          <p className="text-muted-foreground px-4 py-6 text-sm">
            {t.chats.workRecord.loading}
          </p>
        ) : timeline.isError ? (
          <p className="text-muted-foreground px-4 py-6 text-sm" role="alert">
            {t.chats.workRecord.unavailable}
          </p>
        ) : records.length === 0 && !hasTaskOverview ? (
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
            mobile
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
      <WorkRecordBody
        enabled={enabled}
        mobile={false}
        threadId={threadId}
        onClose={close}
      />
    </aside>
  );
}
