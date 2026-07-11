import type { SubtaskUpdate } from "../tasks/context";

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
  prompt?: unknown;
  message?: unknown;
  result?: unknown;
  error?: unknown;
  action_result?: unknown;
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

export function stringValue(value: unknown) {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function objectStringValue(value: unknown, field: string) {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  return stringValue((value as Record<string, unknown>)[field]);
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
    ...(roundId ? { roundId } : {}),
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
