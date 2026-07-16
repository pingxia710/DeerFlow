import {
  getSubtaskStorageKey,
  mergeSubtaskUpdate,
  type SubtaskUpdate,
} from "../tasks/context";
import { parseSubtaskResult } from "../tasks/subtask-result";
import type {
  CommandRoomArtifactKind,
  CommandRoomContainer,
  Subtask,
} from "../tasks/types";

import type { TaskLaneSnapshot } from "./command-room-read-model";
import type { RunMessage } from "./types";

const TASK_EVENT_CALLER = "task_event";
export const TASK_EVENT_SCHEMA_VERSION = "deerflow.task-event/v1";
const TASK_EVENT_TYPES = new Set([
  "task_started",
  "task_running",
  "task_completed",
  "task_failed",
  "task_cancelled",
  "task_timed_out",
]);
const RUN_TERMINAL_EVENT_TYPE = "run.terminal";

type PersistedTaskEvent = {
  type?: unknown;
  event_type?: unknown;
  schema_version?: unknown;
  task_id?: unknown;
  thread_id?: unknown;
  run_id?: unknown;
  round_id?: unknown;
  roundId?: unknown;
  status?: unknown;
  summary?: unknown;
  result_preview?: unknown;
  error_preview?: unknown;
  redacted?: unknown;
  artifact_refs?: unknown;
  created_at?: unknown;
  started_at?: unknown;
  updated_at?: unknown;
  completed_at?: unknown;
  finished_at?: unknown;
  duration_ms?: unknown;
  description?: unknown;
  subagent_type?: unknown;
  background_task?: unknown;
  prompt?: unknown;
  message?: unknown;
  result?: unknown;
  error?: unknown;
  action_result?: unknown;
  command_room_container?: unknown;
  work_package_id?: unknown;
  delivery_cycle_index?: unknown;
  collaboration_round_index?: unknown;
  container_artifact_path?: unknown;
  container_artifact_written?: unknown;
  container_artifact_kind?: unknown;
  metadata?: unknown;
  content?: unknown;
};

export type RunTerminalEvent = {
  type: typeof RUN_TERMINAL_EVENT_TYPE;
  event_type: typeof RUN_TERMINAL_EVENT_TYPE;
  thread_id: string;
  run_id: string;
  round_id?: string | null;
  status: string;
  terminal_reason: string;
};

type TaskEventUpdateSubtask = (task: SubtaskUpdate) => void;

function taskToolMessageText(message: Record<string, unknown>) {
  const content = message.content;
  if (typeof content === "string") {
    return content;
  }
  if (!Array.isArray(content)) {
    return "";
  }
  return content
    .map((part) => {
      if (typeof part === "string") {
        return part;
      }
      if (
        typeof part === "object" &&
        part !== null &&
        (part as Record<string, unknown>).type === "text"
      ) {
        const text = (part as Record<string, unknown>).text;
        return typeof text === "string" ? text : "";
      }
      return "";
    })
    .join("\n");
}

export function applyTaskToolResultRunMessages(
  messages: RunMessage[],
  updateSubtask: TaskEventUpdateSubtask,
  fallbackThreadId?: string | null,
) {
  const taskRoundIds = new Map<string, string>();
  for (const row of messages) {
    const event = asTaskEvent(row.content);
    const taskId = stringValue(event?.task_id);
    const roundId = taskEventRoundId(event);
    if (taskId && roundId) {
      taskRoundIds.set(taskId, roundId);
    }
  }

  for (const row of messages) {
    if (typeof row.content !== "object" || row.content === null) {
      continue;
    }
    const message = row.content as Record<string, unknown>;
    if (message.type !== "tool" || message.name !== "task") {
      continue;
    }
    const taskId = stringValue(message.tool_call_id);
    if (!taskId) {
      continue;
    }
    const additionalKwargs =
      typeof message.additional_kwargs === "object" &&
      message.additional_kwargs !== null
        ? (message.additional_kwargs as Record<string, unknown>)
        : undefined;
    const parsed = parseSubtaskResult(
      taskToolMessageText(message),
      additionalKwargs,
    );
    if (parsed.status === "in_progress") {
      continue;
    }
    updateSubtask({
      id: taskId,
      threadId: fallbackThreadId ?? undefined,
      runId: row.run_id,
      roundId:
        objectStringValue(additionalKwargs, "round_id") ??
        objectStringValue(row.metadata, "round_id") ??
        taskRoundIds.get(taskId),
      ...parsed,
      ...commandRoomContainerFacts(additionalKwargs),
      notify: false,
    });
  }
}

export function terminalTaskToolResult(messages: RunMessage[], taskId: string) {
  for (const row of [...messages].reverse()) {
    if (typeof row.content !== "object" || row.content === null) {
      continue;
    }
    const message = row.content as Record<string, unknown>;
    if (
      message.type !== "tool" ||
      message.name !== "task" ||
      message.tool_call_id !== taskId
    ) {
      continue;
    }
    const additionalKwargs =
      typeof message.additional_kwargs === "object" &&
      message.additional_kwargs !== null
        ? (message.additional_kwargs as Record<string, unknown>)
        : undefined;
    const content = taskToolMessageText(message);
    if (
      parseSubtaskResult(content, additionalKwargs).status !== "in_progress"
    ) {
      return content;
    }
  }
  return undefined;
}

export function stringValue(value: unknown) {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function objectStringValue(value: unknown, field: string) {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  return stringValue((value as Record<string, unknown>)[field]);
}

function objectNumberValue(value: unknown, field: string) {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const fieldValue = (value as Record<string, unknown>)[field];
  return typeof fieldValue === "number" && Number.isFinite(fieldValue)
    ? fieldValue
    : undefined;
}

function objectBooleanValue(value: unknown, field: string) {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const fieldValue = (value as Record<string, unknown>)[field];
  return typeof fieldValue === "boolean" ? fieldValue : undefined;
}

function firstDefinedValue<T>(
  values: unknown[],
  read: (value: unknown) => T | undefined,
) {
  for (const value of values) {
    const parsed = read(value);
    if (parsed !== undefined) {
      return parsed;
    }
  }
  return undefined;
}

function commandRoomContainerValue(
  value: unknown,
): CommandRoomContainer | undefined {
  return value === "planning" ||
    value === "context" ||
    value === "technical-design" ||
    value === "execution" ||
    value === "review" ||
    value === "project-steward" ||
    value === "debt-curation" ||
    value === "learning-curation" ||
    value === "collaboration" ||
    value === "evaluation"
    ? value
    : undefined;
}

function commandRoomArtifactKindValue(
  value: unknown,
): CommandRoomArtifactKind | undefined {
  return value === "spec" ||
    value === "context-discovery" ||
    value === "context" ||
    value === "planning-forward" ||
    value === "planning-opposition" ||
    value === "technical-forward" ||
    value === "technical-opposition" ||
    value === "technical-plan" ||
    value === "execution" ||
    value === "findings" ||
    value === "project-status" ||
    value === "debt" ||
    value === "learning" ||
    value === "round-note" ||
    value === "evaluation" ||
    value === "chair-decision"
    ? value
    : undefined;
}

function commandRoomContainerFacts(
  ...sources: unknown[]
): Pick<
  SubtaskUpdate,
  | "commandRoomContainer"
  | "workPackageId"
  | "deliveryCycleIndex"
  | "collaborationRoundIndex"
  | "containerArtifactPath"
  | "containerArtifactWritten"
  | "containerArtifactKind"
> {
  const container = firstDefinedValue(sources, (source) =>
    commandRoomContainerValue(
      typeof source === "object" && source !== null
        ? (source as Record<string, unknown>).command_room_container
        : undefined,
    ),
  );
  const deliveryCycleIndex = firstDefinedValue(sources, (source) =>
    objectNumberValue(source, "delivery_cycle_index"),
  );
  const workPackageId = firstDefinedValue(sources, (source) =>
    objectStringValue(source, "work_package_id"),
  );
  const collaborationRoundIndex = firstDefinedValue(sources, (source) =>
    objectNumberValue(source, "collaboration_round_index"),
  );
  const containerArtifactPath = firstDefinedValue(sources, (source) =>
    objectStringValue(source, "container_artifact_path"),
  );
  const containerArtifactWritten = firstDefinedValue(sources, (source) =>
    objectBooleanValue(source, "container_artifact_written"),
  );
  const containerArtifactKind = firstDefinedValue(sources, (source) =>
    commandRoomArtifactKindValue(
      typeof source === "object" && source !== null
        ? (source as Record<string, unknown>).container_artifact_kind
        : undefined,
    ),
  );

  return {
    ...(container ? { commandRoomContainer: container } : {}),
    ...(workPackageId ? { workPackageId } : {}),
    ...(deliveryCycleIndex !== undefined ? { deliveryCycleIndex } : {}),
    ...(collaborationRoundIndex !== undefined
      ? { collaborationRoundIndex }
      : {}),
    ...(containerArtifactPath ? { containerArtifactPath } : {}),
    ...(containerArtifactWritten !== undefined
      ? { containerArtifactWritten }
      : {}),
    ...(containerArtifactKind ? { containerArtifactKind } : {}),
  };
}

export function taskEventType(event: PersistedTaskEvent | null | undefined) {
  return stringValue(event?.event_type) ?? stringValue(event?.type);
}

function taskEventRoundId(event: PersistedTaskEvent | null | undefined) {
  if (!event) {
    return undefined;
  }
  return (
    objectStringValue(event.metadata, "round_id") ??
    objectStringValue(event.content, "round_id") ??
    stringValue(event.round_id) ??
    stringValue(event.roundId)
  );
}

function taskEventTimestamp(value?: unknown) {
  const stringTimestamp = stringValue(value);
  if (!stringTimestamp) {
    return undefined;
  }
  const time = Date.parse(stringTimestamp);
  return Number.isFinite(time) ? time : undefined;
}

function taskEventStartedAt(value?: unknown) {
  return taskEventTimestamp(value);
}

function taskEventDurationMs(value?: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

function taskEventFinishedAt(event: PersistedTaskEvent) {
  return (
    taskEventTimestamp(event.finished_at) ??
    taskEventTimestamp(event.completed_at) ??
    taskEventTimestamp(event.updated_at)
  );
}

export function resolveVisibleTaskRunningThreadId({
  eventThreadId,
  streamThreadId,
}: {
  eventThreadId?: string | null;
  streamThreadId?: string | null;
  viewThreadId: string | null;
  liveMessagesThreadId: string | null;
}) {
  if (eventThreadId) {
    return eventThreadId;
  }
  return streamThreadId ?? null;
}

export function asTaskEvent(value: unknown): PersistedTaskEvent | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const event = value as PersistedTaskEvent;
  const eventType = taskEventType(event);
  const schemaVersion = stringValue(event.schema_version);
  if (!eventType || !TASK_EVENT_TYPES.has(eventType)) {
    return null;
  }
  if (schemaVersion && schemaVersion !== TASK_EVENT_SCHEMA_VERSION) {
    return null;
  }
  if (
    !stringValue(event.task_id) ||
    !stringValue(event.thread_id) ||
    !stringValue(event.run_id)
  ) {
    return null;
  }
  return { ...event, type: eventType, event_type: eventType };
}

export function asRunTerminalEvent(value: unknown): RunTerminalEvent | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const event = value as Record<string, unknown>;
  const eventType = stringValue(event.event_type) ?? stringValue(event.type);
  const threadId = stringValue(event.thread_id);
  const runId = stringValue(event.run_id);
  const roundId = stringValue(event.round_id) ?? stringValue(event.roundId);
  const status = stringValue(event.status);
  const terminalReason = stringValue(event.terminal_reason);
  if (
    eventType !== RUN_TERMINAL_EVENT_TYPE ||
    !threadId ||
    !runId ||
    !status ||
    !terminalReason
  ) {
    return null;
  }
  return {
    type: RUN_TERMINAL_EVENT_TYPE,
    event_type: RUN_TERMINAL_EVENT_TYPE,
    thread_id: threadId,
    run_id: runId,
    ...(roundId ? { round_id: roundId } : {}),
    status,
    terminal_reason: terminalReason,
  };
}

function taskLaneStatus(status: string): NonNullable<SubtaskUpdate["status"]> {
  if (status === "completed") {
    return "completed";
  }
  if (
    status === "in_progress" ||
    status === "running" ||
    status === "pending"
  ) {
    return "in_progress";
  }
  return "failed";
}

export function taskLaneSubtaskUpdate(lane: TaskLaneSnapshot): SubtaskUpdate {
  const status = taskLaneStatus(lane.status);
  const fallbackDescription = lane.role ? `${lane.role} task` : lane.task_id;
  const description = lane.description ?? fallbackDescription;
  const prompt = lane.prompt ?? lane.description ?? fallbackDescription;
  const result =
    lane.result ?? lane.result_ref ?? lane.evidence_ref ?? undefined;
  const update: SubtaskUpdate = {
    id: lane.task_id,
    threadId: lane.thread_id,
    runId: lane.run_id,
    ...(lane.round_id ? { roundId: lane.round_id } : {}),
    status,
    subagent_type: lane.subagent_type ?? lane.role ?? "task",
    description,
    prompt,
    actionResultStatus: lane.status,
    notify: true,
    ...commandRoomContainerFacts(lane.handoff, lane.metadata, lane.details),
  };
  const refs: Record<string, unknown> = {};
  for (const key of [
    "result_ref",
    "evidence_ref",
    "evidence_refs",
    "artifact_refs",
    "output_refs",
    "handoff",
  ] as const) {
    const value = lane[key];
    if (value !== undefined && value !== null) {
      refs[key] = value;
    }
  }
  update.metadata = {
    ...(lane.metadata ?? {}),
    ...(Object.keys(refs).length > 0 ? { refs } : {}),
  };
  update.details = {
    ...(lane.details ?? {}),
    ...(Object.keys(refs).length > 0 ? { refs } : {}),
  };
  const startedAt = taskEventStartedAt(lane.started_at);
  if (startedAt !== undefined) {
    update.startedAt = startedAt;
  }
  const durationMs = taskEventDurationMs(lane.duration_ms);
  if (durationMs !== undefined) {
    update.durationMs = durationMs;
  }
  if (status !== "in_progress") {
    const finishedAt =
      taskEventTimestamp(lane.finished_at) ??
      taskEventTimestamp(lane.completed_at);
    if (finishedAt !== undefined) {
      update.finishedAt = finishedAt;
    }
  }
  if (status === "completed" && result) {
    update.result = result;
  }
  if (status === "failed") {
    update.error = lane.error ?? lane.status;
    update.terminalReason = lane.status;
  }
  return update;
}

export function mergeTaskLaneSubtasks(
  lanes: TaskLaneSnapshot[],
  liveTasks: Subtask[],
) {
  const tasks = new Map<string, Subtask>();
  for (const lane of lanes) {
    const task = mergeSubtaskUpdate(undefined, taskLaneSubtaskUpdate(lane));
    tasks.set(getSubtaskStorageKey(task), task);
  }
  for (const task of liveTasks) {
    const key = getSubtaskStorageKey(task);
    tasks.set(key, mergeSubtaskUpdate(tasks.get(key), task));
  }
  return [...tasks.values()].sort(
    (left, right) =>
      (left.startedAt ?? Number.MAX_SAFE_INTEGER) -
        (right.startedAt ?? Number.MAX_SAFE_INTEGER) ||
      left.id.localeCompare(right.id),
  );
}

function actionResultString(event: PersistedTaskEvent, field: string) {
  if (
    typeof event.action_result !== "object" ||
    event.action_result === null ||
    !(field in event.action_result)
  ) {
    return undefined;
  }
  return stringValue((event.action_result as Record<string, unknown>)[field]);
}

function isRedactedTaskEvent(event: PersistedTaskEvent) {
  return event.redacted === true;
}

function applyActionResultMetadata(
  event: PersistedTaskEvent,
  update: SubtaskUpdate,
) {
  const status = actionResultString(event, "status");
  const terminalReason = actionResultString(event, "terminal_reason");
  if (status) {
    update.actionResultStatus = status;
  }
  if (terminalReason) {
    update.terminalReason = terminalReason;
  }
}

export function isTaskEventRunMessage(message: RunMessage) {
  if (message.display?.message_type) {
    return message.display.message_type === "task_event";
  }
  return (
    message.metadata?.caller === TASK_EVENT_CALLER ||
    asTaskEvent(message.content) !== null
  );
}

export function taskEventRunMessageKey(message: RunMessage) {
  if (!isTaskEventRunMessage(message)) {
    return null;
  }
  if (typeof message.seq === "number") {
    return `${message.run_id}:${message.seq}`;
  }
  const taskEvent = asTaskEvent(message.content);
  const taskId = stringValue(taskEvent?.task_id);
  const eventType = taskEventType(taskEvent);
  const eventRunId = stringValue(taskEvent?.run_id) ?? message.run_id;
  const eventThreadId = stringValue(taskEvent?.thread_id) ?? "";
  const eventRoundId = taskEventRoundId(taskEvent);
  if (!taskId || !eventType || !eventRunId || !message.created_at) {
    return null;
  }
  const roundKeySegment = eventRoundId ? `${eventRoundId}:` : "";
  return `${eventRunId}:${eventThreadId}:${roundKeySegment}${taskId}:${eventType}:${message.created_at}`;
}

export function isTaskEventRunMessageForRequest(
  message: RunMessage,
  fallbackThreadId?: string | null,
) {
  const taskEvent = asTaskEvent(message.content);
  const eventRunId = stringValue(taskEvent?.run_id);
  const eventThreadId = stringValue(taskEvent?.thread_id);
  if (!taskEvent || !eventRunId || eventRunId !== message.run_id) {
    return false;
  }
  return !fallbackThreadId || eventThreadId === fallbackThreadId;
}

export function applyTaskEventToSubtask(
  event: unknown,
  updateSubtask: TaskEventUpdateSubtask,
  fallbackThreadId?: string | null,
  startedAt?: number,
) {
  const taskEvent = asTaskEvent(event);
  const taskId = stringValue(taskEvent?.task_id);
  if (!taskEvent || !taskId) {
    return false;
  }
  const eventType = taskEventType(taskEvent);
  if (!eventType) {
    return false;
  }
  const eventStatus = stringValue(taskEvent.status);
  const threadId =
    stringValue(taskEvent.thread_id) ?? fallbackThreadId ?? undefined;
  const runId = stringValue(taskEvent.run_id);
  const roundId = taskEventRoundId(taskEvent);
  const durationMs = taskEventDurationMs(taskEvent.duration_ms);
  const base: SubtaskUpdate = {
    id: taskId,
    threadId,
    runId,
    ...(taskEvent.background_task === true ? { backgroundTask: true } : {}),
    ...(roundId ? { roundId } : {}),
    ...commandRoomContainerFacts(
      taskEvent,
      taskEvent.metadata,
      taskEvent.content,
    ),
    notify: true,
    ...(durationMs !== undefined ? { durationMs } : {}),
  };

  if (eventType === "task_started") {
    const update: SubtaskUpdate = { ...base, status: "in_progress" };
    const eventStartedAt =
      taskEventStartedAt(taskEvent.started_at) ??
      taskEventStartedAt(taskEvent.created_at) ??
      startedAt;
    if (eventStartedAt !== undefined) {
      update.startedAt = eventStartedAt;
    }
    const description =
      stringValue(taskEvent.description) ?? stringValue(taskEvent.summary);
    if (description) {
      update.description = description;
    }
    const subagentType = stringValue(taskEvent.subagent_type);
    if (subagentType) {
      update.subagent_type = subagentType;
    }
    const prompt = stringValue(taskEvent.prompt);
    if (prompt) {
      update.prompt = prompt;
    }
    updateSubtask(update);
    return true;
  }

  if (eventType === "task_running" || eventStatus === "in_progress") {
    const eventStartedAt =
      taskEventStartedAt(taskEvent.started_at) ??
      taskEventStartedAt(taskEvent.created_at) ??
      startedAt;
    updateSubtask({
      ...base,
      status: "in_progress",
      ...(eventStartedAt !== undefined ? { startedAt: eventStartedAt } : {}),
    });
    return true;
  }

  if (eventType === "task_completed" || eventStatus === "completed") {
    const update: SubtaskUpdate = { ...base, status: "completed" };
    const eventStartedAt = taskEventStartedAt(taskEvent.started_at);
    const eventFinishedAt = taskEventFinishedAt(taskEvent);
    if (eventStartedAt !== undefined) {
      update.startedAt = eventStartedAt;
    }
    if (eventFinishedAt !== undefined) {
      update.finishedAt = eventFinishedAt;
    }
    applyActionResultMetadata(taskEvent, update);
    const result = isRedactedTaskEvent(taskEvent)
      ? stringValue(taskEvent.result_preview)
      : (stringValue(taskEvent.result_preview) ??
        stringValue(taskEvent.result) ??
        actionResultString(taskEvent, "summary"));
    if (result) {
      update.result = result;
    }
    updateSubtask(update);
    return true;
  }

  const update: SubtaskUpdate = { ...base, status: "failed" };
  const eventStartedAt = taskEventStartedAt(taskEvent.started_at);
  const eventFinishedAt = taskEventFinishedAt(taskEvent);
  if (eventStartedAt !== undefined) {
    update.startedAt = eventStartedAt;
  }
  if (eventFinishedAt !== undefined) {
    update.finishedAt = eventFinishedAt;
  }
  applyActionResultMetadata(taskEvent, update);
  const error = isRedactedTaskEvent(taskEvent)
    ? stringValue(taskEvent.error_preview)
    : (stringValue(taskEvent.error_preview) ??
      stringValue(taskEvent.error) ??
      actionResultString(taskEvent, "error") ??
      actionResultString(taskEvent, "terminal_reason") ??
      (TASK_EVENT_TYPES.has(eventType)
        ? undefined
        : `Unknown task event terminal status: ${eventStatus ?? eventType}`));
  if (error) {
    update.error = error;
  }
  updateSubtask(update);
  return true;
}

export function applyTaskEventRunMessages(
  messages: RunMessage[],
  updateSubtask: TaskEventUpdateSubtask,
  fallbackThreadId?: string | null,
  appliedEventKeys?: Set<string>,
) {
  for (const message of messages) {
    if (!isTaskEventRunMessageForRequest(message, fallbackThreadId)) {
      continue;
    }
    const eventKey = taskEventRunMessageKey(message);
    if (eventKey && appliedEventKeys?.has(eventKey)) {
      continue;
    }
    const applied = applyTaskEventToSubtask(
      message.content,
      updateSubtask,
      fallbackThreadId,
      taskEventStartedAt(message.created_at),
    );
    if (applied && eventKey) {
      appliedEventKeys?.add(eventKey);
    }
  }
}
