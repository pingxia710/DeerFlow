export const THREAD_TIMELINE_CATEGORIES = [
  "message",
  "lifecycle",
  "artifact",
] as const;

type ThreadTimelineCategory = (typeof THREAD_TIMELINE_CATEGORIES)[number];

export type ThreadTimelineRecord = Readonly<{
  eventId: string;
  seq: number;
  runId: string;
  eventType: string;
  category: ThreadTimelineCategory;
  content: unknown;
  metadata: Record<string, unknown>;
  createdAt: string;
}>;

export type ThreadTimelinePage = Readonly<{
  threadId: string;
  records: readonly ThreadTimelineRecord[];
  afterSeq: number;
  watermarkSeq: number;
  cursor: string;
  hasMore: boolean;
  truncated: boolean;
}>;

export type ThreadTimelineProjection = Readonly<{
  threadId: string;
  records: readonly ThreadTimelineRecord[];
  cursor: string;
  watermarkSeq: number;
  confirmedWatermarkSeq: number;
  hasMore: boolean;
  truncated: boolean;
}>;

export class ThreadTimelineConflictError extends Error {
  constructor(eventId: string) {
    super(`Conflicting timeline payload for ${eventId}.`);
    this.name = "ThreadTimelineConflictError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function isTimelineCategory(value: unknown): value is ThreadTimelineCategory {
  return (
    typeof value === "string" &&
    (THREAD_TIMELINE_CATEGORIES as readonly string[]).includes(value)
  );
}

function parseTimelineRecord(
  value: unknown,
  threadId: string,
): ThreadTimelineRecord {
  if (!isRecord(value)) {
    throw new Error("Invalid timeline record.");
  }
  const {
    event_id,
    seq,
    run_id,
    event_type,
    category,
    content,
    metadata,
    created_at,
  } = value;
  if (
    typeof event_id !== "string" ||
    !isNonNegativeInteger(seq) ||
    event_id !== `${threadId}:${seq}` ||
    typeof run_id !== "string" ||
    run_id.length === 0 ||
    typeof event_type !== "string" ||
    event_type.length === 0 ||
    !isTimelineCategory(category) ||
    !isRecord(metadata) ||
    typeof created_at !== "string" ||
    !("content" in value)
  ) {
    throw new Error("Invalid timeline record.");
  }
  return {
    eventId: event_id,
    seq,
    runId: run_id,
    eventType: event_type,
    category,
    content,
    metadata,
    createdAt: created_at,
  };
}

export function parseThreadTimelinePage(
  value: unknown,
  expectedThreadId: string,
): ThreadTimelinePage {
  if (!isRecord(value)) {
    throw new Error("Invalid thread timeline response.");
  }
  const {
    thread_id,
    records,
    after_seq,
    watermark_seq,
    cursor,
    has_more,
    truncated,
  } = value;
  if (
    thread_id !== expectedThreadId ||
    !Array.isArray(records) ||
    !isNonNegativeInteger(after_seq) ||
    !isNonNegativeInteger(watermark_seq) ||
    typeof cursor !== "string" ||
    cursor.length === 0 ||
    typeof has_more !== "boolean" ||
    typeof truncated !== "boolean"
  ) {
    throw new Error("Invalid thread timeline response.");
  }
  return {
    threadId: thread_id,
    records: records.map((record) => parseTimelineRecord(record, thread_id)),
    afterSeq: after_seq,
    watermarkSeq: watermark_seq,
    cursor,
    hasMore: has_more,
    truncated,
  };
}

function recordsMatch(
  first: ThreadTimelineRecord,
  second: ThreadTimelineRecord,
) {
  return JSON.stringify(first) === JSON.stringify(second);
}

function mergeRecords(
  current: readonly ThreadTimelineRecord[],
  incoming: readonly ThreadTimelineRecord[],
) {
  const recordsByEventId = new Map(
    current.map((record) => [record.eventId, record]),
  );
  for (const record of incoming) {
    const existing = recordsByEventId.get(record.eventId);
    if (existing && !recordsMatch(existing, record)) {
      throw new ThreadTimelineConflictError(record.eventId);
    }
    recordsByEventId.set(record.eventId, existing ?? record);
  }
  return [...recordsByEventId.values()].sort(
    (left, right) => left.seq - right.seq,
  );
}

export function mergeThreadTimelinePage(
  previous: ThreadTimelineProjection | undefined,
  page: ThreadTimelinePage,
  mode: "snapshot" | "incremental",
): ThreadTimelineProjection {
  if (previous && previous.threadId !== page.threadId) {
    throw new Error("Timeline page belongs to a different thread.");
  }
  if (previous && page.watermarkSeq < previous.confirmedWatermarkSeq) {
    return previous;
  }
  if (!previous || mode === "snapshot") {
    return {
      threadId: page.threadId,
      records: mergeRecords([], page.records),
      cursor: page.cursor,
      watermarkSeq: page.watermarkSeq,
      confirmedWatermarkSeq: page.hasMore ? 0 : page.watermarkSeq,
      hasMore: page.hasMore,
      truncated: page.truncated,
    };
  }
  return {
    threadId: previous.threadId,
    records: mergeRecords(previous.records, page.records),
    cursor: page.cursor,
    watermarkSeq: page.watermarkSeq,
    confirmedWatermarkSeq: page.hasMore
      ? previous.confirmedWatermarkSeq
      : Math.max(previous.confirmedWatermarkSeq, page.watermarkSeq),
    hasMore: page.hasMore,
    truncated: previous.truncated || page.truncated,
  };
}

const TASK_STARTED_EVENT = "task_started";
const TASK_TERMINAL_EVENTS = new Set([
  "task_completed",
  "task_failed",
  "task_cancelled",
  "task_timed_out",
]);

function taskIdOf(record: ThreadTimelineRecord) {
  if (!isRecord(record.content)) {
    return null;
  }
  const taskId = record.content.task_id;
  return typeof taskId === "string" && taskId.length > 0 ? taskId : null;
}

export function isTaskTimelineRecord(record: ThreadTimelineRecord) {
  return (
    record.category === "message" &&
    (record.eventType === TASK_STARTED_EVENT ||
      TASK_TERMINAL_EVENTS.has(record.eventType))
  );
}

export function isWorkRecordFact(record: ThreadTimelineRecord) {
  return (
    record.category === "lifecycle" ||
    record.category === "artifact" ||
    isTaskTimelineRecord(record)
  );
}

export function hasActiveTimelineTask(
  records: readonly ThreadTimelineRecord[],
) {
  const activeTaskIds = new Set<string>();
  for (const record of records) {
    const taskId = taskIdOf(record);
    if (!taskId) {
      continue;
    }
    if (record.eventType === TASK_STARTED_EVENT) {
      activeTaskIds.add(taskId);
    } else if (TASK_TERMINAL_EVENTS.has(record.eventType)) {
      activeTaskIds.delete(taskId);
    }
  }
  return activeTaskIds.size > 0;
}

export function shouldPollThreadTimeline(
  projection: ThreadTimelineProjection | undefined,
  hasRuntimeActivity: boolean,
) {
  if (projection?.hasMore || hasRuntimeActivity) {
    return true;
  }
  return projection ? hasActiveTimelineTask(projection.records) : false;
}
