"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircleIcon,
  ChevronsUpDownIcon,
  CircleAlertIcon,
  CircleDashedIcon,
  LoaderCircleIcon,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  fetch as fetchWithAuth,
} from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { extractContentFromMessage } from "@/core/messages/utils";
import type { Subtask } from "@/core/tasks";
import {
  groupCommandRoomTrajectoryByWorkPackage,
  splitCommandRoomTrajectory,
  type CommandRoomTrajectoryStep,
} from "@/core/threads/command-room-read-model";
import {
  buildRunMessagesUrl,
  readRunMessagesPageResponse,
} from "@/core/threads/hooks";
import { HISTORY_CREATED_AT_KEY } from "@/core/threads/message-history";
import { queryKeys } from "@/core/threads/query-keys";
import { terminalTaskToolResult } from "@/core/threads/task-events";
import { formatDateTime, formatDuration } from "@/core/utils/datetime";
import { cn } from "@/lib/utils";

import { MarkdownContent } from "./markdown-content";

function messageTime(message: Message) {
  const value = message.additional_kwargs?.[HISTORY_CREATED_AT_KEY];
  const timestamp = typeof value === "string" ? Date.parse(value) : Number.NaN;
  return Number.isFinite(timestamp) ? timestamp : undefined;
}

function resultLabel(
  container: CommandRoomTrajectoryStep["container"] | undefined,
  t: ReturnType<typeof useI18n>["t"],
) {
  if (container === "execution") {
    return t.chats.trajectory.executionResult;
  }
  if (container === "review") {
    return t.chats.trajectory.reviewResult;
  }
  return t.chats.trajectory.result;
}

function stepLabel(
  step: CommandRoomTrajectoryStep,
  t: ReturnType<typeof useI18n>["t"],
) {
  const artifactKinds = new Set(
    step.tasks.map((task) => task.containerArtifactKind),
  );
  if (artifactKinds.has("spec")) {
    return t.chats.trajectory.planProposal;
  }
  if (artifactKinds.has("technical-plan")) {
    return t.chats.trajectory.technicalPlan;
  }
  if (step.container === "planning") {
    return t.chats.trajectory.planResearch;
  }
  const label =
    step.container === "technical-design"
      ? t.chats.trajectory.technicalDesign
      : step.container === "execution"
        ? t.chats.trajectory.execution
        : step.container === "review"
          ? t.chats.trajectory.review
          : step.container === "evaluation"
            ? t.chats.trajectory.evaluation
            : step.container;
  return step.deliveryCycleIndex === undefined
    ? label
    : t.chats.trajectory.cycle(label, step.deliveryCycleIndex);
}

function stepStatus(
  status: CommandRoomTrajectoryStep["status"] | Subtask["status"],
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
  status: CommandRoomTrajectoryStep["status"] | Subtask["status"];
}) {
  if (status === "completed") {
    return <CheckCircleIcon aria-hidden className="size-4 text-emerald-600" />;
  }
  if (status === "failed") {
    return <CircleAlertIcon aria-hidden className="size-4 text-red-600" />;
  }
  return <CircleDashedIcon aria-hidden className="size-4 animate-spin" />;
}

function Timing({
  startedAt,
  finishedAt,
  durationMs,
}: {
  startedAt?: number;
  finishedAt?: number;
  durationMs?: number;
}) {
  const { locale, t } = useI18n();
  const started =
    startedAt === undefined ? null : formatDateTime(startedAt, locale);
  const finished =
    finishedAt === undefined ? null : formatDateTime(finishedAt, locale);
  const duration = durationMs === undefined ? null : formatDuration(durationMs);
  if (!started && !finished && !duration) {
    return null;
  }
  return (
    <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {started && (
        <span>
          {t.chats.trajectory.startedAt}:{" "}
          <time dateTime={new Date(startedAt!).toISOString()}>{started}</time>
        </span>
      )}
      {finished && (
        <span>
          {t.chats.trajectory.finishedAt}:{" "}
          <time dateTime={new Date(finishedAt!).toISOString()}>{finished}</time>
        </span>
      )}
      {duration && (
        <span>
          {t.chats.trajectory.elapsed}: {duration}
        </span>
      )}
    </div>
  );
}

function stepTiming(step: CommandRoomTrajectoryStep, now: number) {
  const startedAt = Math.min(
    ...step.tasks.map((task) => task.startedAt ?? Number.MAX_SAFE_INTEGER),
  );
  const finishedAt = Math.max(
    ...step.tasks.map((task) => task.finishedAt ?? Number.NEGATIVE_INFINITY),
  );
  const validStartedAt =
    startedAt === Number.MAX_SAFE_INTEGER ? undefined : startedAt;
  const validFinishedAt =
    finishedAt === Number.NEGATIVE_INFINITY ? undefined : finishedAt;
  return {
    startedAt: validStartedAt,
    finishedAt: validFinishedAt,
    durationMs:
      validStartedAt === undefined
        ? undefined
        : Math.max(0, (validFinishedAt ?? now) - validStartedAt),
  };
}

function taskTiming(task: Subtask, now: number) {
  return {
    startedAt: task.startedAt,
    finishedAt: task.finishedAt,
    durationMs:
      task.durationMs ??
      (task.status === "in_progress" && task.startedAt !== undefined
        ? Math.max(0, now - task.startedAt)
        : undefined),
  };
}

function TaskResult({
  task,
  enabled,
  label,
}: {
  task: Subtask;
  enabled: boolean;
  label: string;
}) {
  const { t } = useI18n();
  const threadId = task.threadId;
  const runId = task.runId;
  const canLoad = Boolean(threadId && runId);
  const result = useQuery({
    queryKey: queryKeys.thread.taskResult(threadId ?? "", runId ?? "", task.id),
    queryFn: async () => {
      if (!threadId || !runId) {
        return null;
      }
      const response = await fetchWithAuth(
        buildRunMessagesUrl(getBackendBaseURL(), threadId, runId),
        {
          method: "GET",
          timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
        },
      );
      const page = await readRunMessagesPageResponse(response);
      return terminalTaskToolResult(page.data, task.id) ?? null;
    },
    enabled: enabled && canLoad && task.status !== "in_progress",
    retry: false,
    staleTime: Infinity,
  });

  if (!enabled || task.status === "in_progress") {
    return null;
  }
  if (!canLoad) {
    const content = task.result ?? task.error;
    return content ? (
      <div>
        <p className="text-muted-foreground mb-2 text-xs font-medium">
          {label}
        </p>
        <MarkdownContent
          className={cn(task.status === "failed" && "text-red-600")}
          content={content}
          isLoading={false}
        />
      </div>
    ) : (
      <p className="text-muted-foreground text-sm">
        {t.chats.trajectory.resultUnavailable}
      </p>
    );
  }
  if (result.isPending) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm">
        <LoaderCircleIcon aria-hidden className="size-4 animate-spin" />
        {t.common.loading}
      </p>
    );
  }
  const content = result.data;
  if (!content || result.isError) {
    return (
      <p className="text-muted-foreground text-sm">
        {t.chats.trajectory.resultUnavailable}
      </p>
    );
  }
  return (
    <div>
      <p className="text-muted-foreground mb-2 text-xs font-medium">{label}</p>
      <MarkdownContent
        className={cn(task.status === "failed" && "text-red-600")}
        content={content}
        isLoading={false}
      />
    </div>
  );
}

function PlanArtifact({ enabled, task }: { enabled: boolean; task: Subtask }) {
  const { t } = useI18n();
  const threadId = task.threadId;
  const runId = task.runId;
  const canLoad = Boolean(threadId && runId);
  const artifact = useQuery({
    queryKey: queryKeys.thread.commandRoomPlanArtifact(
      threadId ?? "",
      runId ?? "",
      task.id,
    ),
    queryFn: async () => {
      if (!threadId || !runId) {
        return null;
      }
      const response = await fetchWithAuth(
        `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/command-room/tasks/${encodeURIComponent(runId)}/${encodeURIComponent(task.id)}/plan-artifact`,
        {
          method: "GET",
          timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
        },
      );
      if (!response.ok) {
        throw new Error("Plan artifact is unavailable.");
      }
      return response.text();
    },
    enabled: enabled && canLoad && task.status === "completed",
    retry: false,
    staleTime: Infinity,
  });
  const unavailableMessage =
    task.containerArtifactWritten === false
      ? t.chats.trajectory.planArtifactNotWritten
      : t.chats.trajectory.planArtifactUnavailable;

  if (task.status === "in_progress") {
    return (
      <p className="text-muted-foreground text-sm">
        {t.chats.trajectory.planForming}
      </p>
    );
  }
  if (artifact.isPending) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm">
        <LoaderCircleIcon aria-hidden className="size-4 animate-spin" />
        {t.common.loading}
      </p>
    );
  }
  if (!canLoad || artifact.isError || !artifact.data) {
    return (
      <p className="text-muted-foreground text-sm">
        {task.containerArtifactWritten === false
          ? unavailableMessage
          : t.chats.trajectory.resultUnavailable}
      </p>
    );
  }
  return <MarkdownContent content={artifact.data} isLoading={false} />;
}

function TaskTraceRow({ task, now }: { task: Subtask; now: number }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const timing = taskTiming(task, now);
  return (
    <li>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <Button
            aria-label={`${task.description}: ${stepStatus(task.status, t)}`}
            className="h-auto min-h-10 w-full justify-between px-3 py-2 text-left"
            size="sm"
            type="button"
            variant="ghost"
          >
            <span className="flex min-w-0 items-center gap-2">
              <StatusIcon status={task.status} />
              <span className="min-w-0">
                <span className="block truncate">{task.description}</span>
                <span className="text-muted-foreground block truncate text-xs font-normal">
                  {task.subagent_type}
                </span>
                <Timing {...timing} />
              </span>
            </span>
            <ChevronsUpDownIcon aria-hidden className="size-4 shrink-0" />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="border-border/60 border-t px-3 py-3">
          <TaskResult
            enabled={open}
            label={t.chats.trajectory.result}
            task={task}
          />
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

function TrajectoryStepRow({
  now,
  step,
}: {
  now: number;
  step: CommandRoomTrajectoryStep;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const timing = stepTiming(step, now);
  const terminalTasks = step.tasks.filter(
    (task) => task.status !== "in_progress",
  );
  return (
    <li>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <Button
            aria-label={`${stepLabel(step, t)}: ${stepStatus(step.status, t)}`}
            className="h-auto min-h-10 w-full justify-between px-3 py-2 text-left"
            size="sm"
            type="button"
            variant="ghost"
          >
            <span className="flex min-w-0 items-center gap-2">
              <StatusIcon status={step.status} />
              <span className="min-w-0">
                <span className="block truncate">{stepLabel(step, t)}</span>
                <Timing {...timing} />
              </span>
            </span>
            <span className="text-muted-foreground flex shrink-0 items-center gap-2 text-xs font-normal">
              <span>{t.chats.trajectory.tasks(step.tasks.length)}</span>
              <span>{stepStatus(step.status, t)}</span>
              <ChevronsUpDownIcon aria-hidden className="size-4" />
            </span>
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="border-border/60 border-t px-3 py-3">
          {terminalTasks.length > 0 && (
            <ol className="space-y-4">
              {terminalTasks.map((task) => (
                <li key={task.id}>
                  <TaskResult
                    enabled={open}
                    label={resultLabel(step.container, t)}
                    task={task}
                  />
                </li>
              ))}
            </ol>
          )}
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

function ChairDecision({ messages }: { messages: Message[] }) {
  const { locale, t } = useI18n();
  const [historyOpen, setHistoryOpen] = useState(false);
  const current = messages.at(-1);
  if (!current) {
    return null;
  }
  const timestamp = messageTime(current);
  const history = messages.slice(0, -1);
  return (
    <section
      className="border-border/60 border-t px-3 py-4"
      data-command-room-decision
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="text-sm font-medium">
          {t.chats.trajectory.chairOutput}
        </h2>
        {timestamp !== undefined && (
          <p className="text-muted-foreground text-xs">
            {t.chats.trajectory.recordedAt}: {formatDateTime(timestamp, locale)}
          </p>
        )}
      </div>
      <MarkdownContent
        content={extractContentFromMessage(current)}
        isLoading={false}
      />
      {history.length > 0 && (
        <Collapsible open={historyOpen} onOpenChange={setHistoryOpen}>
          <CollapsibleTrigger asChild>
            <Button
              aria-label={t.chats.trajectory.previousOutputs(history.length)}
              className="mt-3 h-auto px-0 py-1 text-xs"
              size="sm"
              type="button"
              variant="ghost"
            >
              {t.chats.trajectory.previousOutputs(history.length)}
              <ChevronsUpDownIcon aria-hidden className="ml-1 size-3" />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="border-border/60 mt-2 space-y-4 border-t pt-3">
            {history.map((message, index) => (
              <MarkdownContent
                content={extractContentFromMessage(message)}
                isLoading={false}
                key={message.id ?? `chair-history-${index}`}
              />
            ))}
          </CollapsibleContent>
        </Collapsible>
      )}
    </section>
  );
}

function PlanProposalRow({ task }: { task: Subtask }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  const timing = taskTiming(task, Date.now());
  const title =
    task.containerArtifactKind === "technical-plan"
      ? t.chats.trajectory.technicalPlan
      : t.chats.trajectory.planProposal;
  return (
    <div className="border-border/60 border-t" data-command-room-plan>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <Button
            aria-label={`${title}: ${stepStatus(task.status, t)}`}
            className="h-auto min-h-10 w-full justify-between px-3 py-2 text-left"
            size="sm"
            type="button"
            variant="ghost"
          >
            <span className="flex min-w-0 items-center gap-2">
              <StatusIcon status={task.status} />
              <span className="min-w-0">
                <span className="block truncate">{title}</span>
                <Timing {...timing} />
              </span>
            </span>
            <span className="text-muted-foreground flex shrink-0 items-center gap-2 text-xs font-normal">
              <span>{stepStatus(task.status, t)}</span>
              <ChevronsUpDownIcon aria-hidden className="size-4" />
            </span>
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="border-border/60 border-t px-3 py-3">
          <PlanArtifact enabled={open} task={task} />
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

function ProcessSection({
  defaultOpen = false,
  steps,
  title,
}: {
  defaultOpen?: boolean;
  steps: CommandRoomTrajectoryStep[];
  title: string;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  if (steps.length === 0) {
    return null;
  }
  return (
    <section className="border-border/60 border-t" data-command-room-process>
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
            <span className="text-muted-foreground flex shrink-0 items-center gap-2 text-xs font-normal">
              <span>{t.chats.trajectory.tasks(steps.length)}</span>
              <ChevronsUpDownIcon aria-hidden className="size-4" />
            </span>
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="border-border/60 border-t">
          <ol className="divide-border/60 divide-y">
            {steps.map((step) => (
              <TrajectoryStepRow key={step.id} now={Date.now()} step={step} />
            ))}
          </ol>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}

function WorkPackageTrajectory({
  showPackageHeader,
  steps,
  workPackageId,
}: {
  showPackageHeader: boolean;
  steps: CommandRoomTrajectoryStep[];
  workPackageId?: string;
}) {
  const { t } = useI18n();
  const { context, delivery, other, planProposals, planResearch } =
    splitCommandRoomTrajectory(steps);
  const planTasks = planProposals.flatMap((step) =>
    step.tasks.filter(
      (task) =>
        task.containerArtifactKind === "spec" ||
        task.containerArtifactKind === "technical-plan",
    ),
  );
  return (
    <section data-command-room-work-package>
      {showPackageHeader && workPackageId && (
        <header className="text-muted-foreground border-border/60 border-t px-3 py-2 text-xs font-medium">
          {t.chats.trajectory.workPackage(workPackageId)}
        </header>
      )}
      <ProcessSection steps={context} title={t.chats.trajectory.context} />
      {planTasks.length > 0 && (
        <section
          aria-label={t.chats.trajectory.plan}
          className="border-border/60 border-t"
        >
          <header className="text-muted-foreground px-3 py-2 text-xs font-medium">
            {t.chats.trajectory.plan}
          </header>
          {planTasks.map((task) => (
            <PlanProposalRow
              key={`${task.runId ?? ""}-${task.id}`}
              task={task}
            />
          ))}
          <ProcessSection
            steps={planResearch}
            title={t.chats.trajectory.planResearch}
          />
        </section>
      )}
      {planTasks.length === 0 && (
        <ProcessSection
          steps={planResearch}
          title={t.chats.trajectory.planResearch}
        />
      )}
      <ProcessSection steps={delivery} title={t.chats.trajectory.delivery} />
      <ProcessSection steps={other} title={t.chats.trajectory.otherProcess} />
    </section>
  );
}

export function CommandRoomTrajectory({
  chairMessages,
  steps,
  unstagedTasks,
}: {
  chairMessages: Message[];
  steps: CommandRoomTrajectoryStep[];
  unstagedTasks: Subtask[];
}) {
  const { t } = useI18n();
  if (
    steps.length === 0 &&
    unstagedTasks.length === 0 &&
    chairMessages.length === 0
  ) {
    return null;
  }
  const packages = groupCommandRoomTrajectoryByWorkPackage(steps);

  return (
    <section
      aria-label={t.chats.trajectory.title}
      className="border-border/60 w-full border-y"
      data-command-room-trajectory
    >
      <header className="text-muted-foreground px-3 py-2 text-xs font-medium">
        {t.chats.trajectory.title}
      </header>
      <div>
        <ChairDecision messages={chairMessages} />
        {packages.map((workPackage) => (
          <WorkPackageTrajectory
            key={workPackage.workPackageId ?? "__legacy__"}
            showPackageHeader={Boolean(workPackage.workPackageId)}
            steps={workPackage.steps}
            workPackageId={workPackage.workPackageId}
          />
        ))}
        {unstagedTasks.length > 0 && (
          <section className="border-border/60 border-t py-2">
            <p className="text-muted-foreground px-3 text-xs font-medium">
              {t.chats.trajectory.subtasks}
            </p>
            <ol className="divide-border/60 mt-2 border-y">
              {unstagedTasks.map((task) => (
                <TaskTraceRow
                  key={`${task.runId ?? ""}-${task.id}`}
                  now={Date.now()}
                  task={task}
                />
              ))}
            </ol>
          </section>
        )}
      </div>
    </section>
  );
}
