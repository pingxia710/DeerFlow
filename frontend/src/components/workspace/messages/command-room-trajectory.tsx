"use client";

import type { Message } from "@langchain/langgraph-sdk";
import {
  CheckCircleIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  CircleDashedIcon,
  LoaderCircleIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

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
import type { Subtask } from "@/core/tasks";
import {
  groupCommandRoomDeliveryCycles,
  type CommandRoomDeliveryCycle,
} from "@/core/threads/command-room-read-model";
import { HISTORY_CREATED_AT_KEY } from "@/core/threads/message-history";
import { formatDateTime, formatDuration } from "@/core/utils/datetime";

import { MarkdownContent } from "./markdown-content";
import { getSubtaskAnchorId } from "./subtask-card";

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

function statusLabel(
  status: Subtask["status"] | CommandRoomDeliveryCycle["status"],
  t: ReturnType<typeof useI18n>["t"],
) {
  switch (status) {
    case "completed":
      return t.chats.trajectory.completed;
    case "failed":
      return t.chats.trajectory.failed;
    case "in_progress":
      return t.chats.trajectory.running;
    default:
      return t.chats.trajectory.mixed;
  }
}

function StatusIcon({
  status,
}: {
  status: Subtask["status"] | CommandRoomDeliveryCycle["status"];
}) {
  if (status === "completed") {
    return <CheckCircleIcon aria-hidden className="size-4 text-emerald-600" />;
  }
  if (status === "failed") {
    return <CircleAlertIcon aria-hidden className="size-4 text-red-600" />;
  }
  if (status === "in_progress") {
    return <LoaderCircleIcon aria-hidden className="size-4 animate-spin" />;
  }
  return <CircleDashedIcon aria-hidden className="size-4" />;
}

function taskActivityTime(task: Subtask) {
  return task.finishedAt ?? task.startedAt ?? 0;
}

function taskDuration(task: Subtask) {
  if (task.durationMs !== undefined) {
    return task.durationMs;
  }
  if (task.startedAt === undefined) {
    return undefined;
  }
  return Math.max(0, Date.now() - task.startedAt);
}

function taskCompletionTime(task: Subtask, locale: string) {
  if (task.finishedAt === undefined || !Number.isFinite(task.finishedAt)) {
    return null;
  }
  return new Intl.DateTimeFormat(locale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(task.finishedAt);
}

function isInformationTask(task: Subtask) {
  return (
    task.commandRoomContainer === "context" ||
    task.commandRoomContainer === "project-steward" ||
    task.commandRoomContainer === "debt-curation" ||
    task.commandRoomContainer === "learning-curation"
  );
}

function isPlanTask(task: Subtask) {
  return (
    task.containerArtifactKind === "spec" ||
    task.containerArtifactKind === "technical-plan"
  );
}

function NavigationSection({
  children,
  count,
  defaultOpen = false,
  title,
}: {
  children: ReactNode;
  count: string;
  defaultOpen?: boolean;
  title: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="border-border/60 border-t">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <Button
            aria-label={title}
            className="h-auto min-h-10 w-full justify-between px-3 py-2 text-left"
            size="sm"
            type="button"
            variant="ghost"
          >
            <span className="font-medium">{title}</span>
            <span className="text-muted-foreground flex items-center gap-2 text-xs font-normal">
              {count}
              <ChevronRightIcon
                aria-hidden
                className={`size-4 transition-transform ${open ? "rotate-90" : ""}`}
              />
            </span>
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="border-border/60 border-t">
          <div className="divide-border/60 divide-y">{children}</div>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}

function TaskNavigationRow({
  anchorId,
  label,
  onNavigate,
  task,
}: {
  anchorId?: string;
  label?: string;
  onNavigate: (anchorId: string) => void;
  task: Subtask;
}) {
  const { locale, t } = useI18n();
  const name = label ?? taskDisplayName(task, t.chats.trajectory.unnamedTask);
  const duration =
    task.status === "in_progress" ? taskDuration(task) : undefined;
  const completedAt = taskCompletionTime(task, locale);
  const target = anchorId ?? getSubtaskAnchorId(task);
  const role =
    task.subagent_type && task.subagent_type !== "task"
      ? task.subagent_type
      : null;
  return (
    <Button
      aria-label={`${name}: ${statusLabel(task.status, t)}`}
      className="h-auto min-h-11 w-full justify-between gap-3 px-3 py-2 text-left"
      size="sm"
      type="button"
      variant="ghost"
      onClick={() => onNavigate(target)}
    >
      <span className="flex min-w-0 items-center gap-2">
        <StatusIcon status={task.status} />
        <span className="min-w-0">
          <span className="block truncate">{name}</span>
          {role && (
            <span className="text-muted-foreground block truncate text-xs font-normal">
              {role}
            </span>
          )}
        </span>
      </span>
      <span className="text-muted-foreground flex shrink-0 flex-col items-end text-xs font-normal">
        <span>{statusLabel(task.status, t)}</span>
        {duration !== undefined && <span>{formatDuration(duration)}</span>}
        {completedAt && task.finishedAt !== undefined && (
          <time dateTime={new Date(task.finishedAt).toISOString()}>
            {completedAt}
          </time>
        )}
      </span>
    </Button>
  );
}

function planTarget(task: Subtask, chairMessages: Message[]) {
  const matchingUpdate = [...chairMessages].reverse().find((message) => {
    if (task.roundId) {
      return getMessageRoundId(message) === task.roundId;
    }
    return task.runId && getMessageRunId(message) === task.runId;
  });
  return matchingUpdate
    ? getCommandRoomUpdateAnchorId(matchingUpdate)
    : getSubtaskAnchorId(task);
}

function deliveryTaskLabel(task: Subtask, t: ReturnType<typeof useI18n>["t"]) {
  const phase =
    task.commandRoomContainer === "review"
      ? t.chats.trajectory.review
      : t.chats.trajectory.execution;
  return `${phase} · ${taskDisplayName(task, t.chats.trajectory.unnamedTask)}`;
}

function DeliveryCycleNavigationGroup({
  cycle,
  onNavigate,
}: {
  cycle: CommandRoomDeliveryCycle;
  onNavigate: (anchorId: string) => void;
}) {
  const { t } = useI18n();
  const title = t.chats.trajectory.deliveryCycle(cycle.index);
  return (
    <div className="divide-border/60 divide-y">
      <div className="bg-muted/30 flex min-h-10 items-center justify-between gap-3 px-3 py-2">
        <span className="flex min-w-0 items-center gap-2">
          <StatusIcon status={cycle.status} />
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{title}</span>
            {cycle.workPackageId && (
              <span className="text-muted-foreground block truncate text-xs">
                {t.chats.trajectory.workPackage(cycle.workPackageId)}
              </span>
            )}
          </span>
        </span>
        <span className="text-muted-foreground shrink-0 text-xs">
          {t.chats.trajectory.tasks(cycle.tasks.length)}
        </span>
      </div>
      {cycle.tasks.map((task) => (
        <TaskNavigationRow
          key={getSubtaskAnchorId(task)}
          label={deliveryTaskLabel(task, t)}
          onNavigate={onNavigate}
          task={task}
        />
      ))}
    </div>
  );
}

function isDeliveryTask(task: Subtask) {
  return (
    task.commandRoomContainer === "execution" ||
    task.commandRoomContainer === "review"
  );
}

function deliveryNavigationItems(tasks: Subtask[]) {
  const cycles = groupCommandRoomDeliveryCycles(tasks);
  return [
    ...cycles.map((cycle) => ({
      type: "cycle" as const,
      id: cycle.id,
      timestamp: Math.max(...cycle.tasks.map(taskActivityTime)),
      cycle,
    })),
    ...tasks
      .filter(
        (task) => isDeliveryTask(task) && task.deliveryCycleIndex === undefined,
      )
      .map((task) => ({
        type: "task" as const,
        id: getSubtaskAnchorId(task),
        timestamp: taskActivityTime(task),
        task,
      })),
  ].sort((left, right) => right.timestamp - left.timestamp);
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

export function CommandRoomTrajectory({
  chairMessages,
  onNavigate,
  tasks,
}: {
  chairMessages: Message[];
  onNavigate: (anchorId: string) => void;
  tasks: Subtask[];
}) {
  const { t } = useI18n();
  const orderedTasks = useMemo(
    () =>
      [...tasks].sort(
        (left, right) => taskActivityTime(right) - taskActivityTime(left),
      ),
    [tasks],
  );
  const informationTasks = orderedTasks.filter(isInformationTask);
  const planTasks = orderedTasks.filter(isPlanTask);
  const deliveryTasks = orderedTasks.filter(isDeliveryTask);
  const deliveryItems = deliveryNavigationItems(orderedTasks);
  const activeTasks = orderedTasks.filter(
    (task) => task.status === "in_progress",
  );
  const recentTasks = orderedTasks.slice(0, 12);
  const completedCount = tasks.filter(
    (task) => task.status === "completed",
  ).length;
  const failedCount = tasks.filter((task) => task.status === "failed").length;

  if (tasks.length === 0) {
    return null;
  }

  return (
    <section
      aria-label={t.chats.trajectory.title}
      className="border-border/60 w-full border-y"
      data-command-room-trajectory
    >
      <header className="flex min-h-10 flex-wrap items-center justify-between gap-2 px-3 py-2">
        <h3 className="text-sm font-semibold">{t.chats.trajectory.title}</h3>
        <div className="text-muted-foreground flex flex-wrap items-center justify-end gap-x-3 gap-y-1 text-xs">
          {activeTasks.length > 0 && (
            <span className="text-foreground flex items-center gap-1">
              <LoaderCircleIcon aria-hidden className="size-3 animate-spin" />
              {activeTasks.length} {t.chats.trajectory.running}
            </span>
          )}
          {completedCount > 0 && (
            <span>
              {completedCount} {t.chats.trajectory.completed}
            </span>
          )}
          {failedCount > 0 && (
            <span className="text-red-600">
              {failedCount} {t.chats.trajectory.failed}
            </span>
          )}
        </div>
      </header>

      {activeTasks.length > 0 && (
        <NavigationSection
          count={t.chats.trajectory.tasks(activeTasks.length)}
          defaultOpen
          title={t.chats.trajectory.activeTasks}
        >
          {activeTasks.map((task) => (
            <TaskNavigationRow
              key={getSubtaskAnchorId(task)}
              onNavigate={onNavigate}
              task={task}
            />
          ))}
        </NavigationSection>
      )}

      {informationTasks.length > 0 && (
        <NavigationSection
          count={t.chats.trajectory.tasks(informationTasks.length)}
          title={t.chats.trajectory.context}
        >
          {informationTasks.map((task) => (
            <TaskNavigationRow
              key={getSubtaskAnchorId(task)}
              onNavigate={onNavigate}
              task={task}
            />
          ))}
        </NavigationSection>
      )}

      {planTasks.length > 0 && (
        <NavigationSection
          count={t.chats.trajectory.plans(planTasks.length)}
          defaultOpen
          title={t.chats.trajectory.plan}
        >
          {planTasks.map((task) => (
            <TaskNavigationRow
              anchorId={planTarget(task, chairMessages)}
              key={getSubtaskAnchorId(task)}
              label={
                task.containerArtifactKind === "technical-plan"
                  ? t.chats.trajectory.technicalPlan
                  : t.chats.trajectory.planProposal
              }
              onNavigate={onNavigate}
              task={task}
            />
          ))}
        </NavigationSection>
      )}

      {deliveryItems.length > 0 && (
        <NavigationSection
          count={t.chats.trajectory.tasks(deliveryTasks.length)}
          defaultOpen
          title={t.chats.trajectory.delivery}
        >
          {deliveryItems.map((item) =>
            item.type === "cycle" ? (
              <DeliveryCycleNavigationGroup
                cycle={item.cycle}
                key={item.id}
                onNavigate={onNavigate}
              />
            ) : (
              <TaskNavigationRow
                key={item.id}
                label={deliveryTaskLabel(item.task, t)}
                onNavigate={onNavigate}
                task={item.task}
              />
            ),
          )}
        </NavigationSection>
      )}

      {recentTasks.length > 0 && (
        <NavigationSection
          count={t.chats.trajectory.tasks(recentTasks.length)}
          title={t.chats.trajectory.recentTasks}
        >
          {recentTasks.map((task) => (
            <TaskNavigationRow
              key={getSubtaskAnchorId(task)}
              onNavigate={onNavigate}
              task={task}
            />
          ))}
        </NavigationSection>
      )}
    </section>
  );
}
