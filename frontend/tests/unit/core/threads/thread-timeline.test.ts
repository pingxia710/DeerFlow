import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

import {
  THREAD_TIMELINE_CATEGORIES,
  ThreadTimelineConflictError,
  mergeThreadTimelinePage,
  parseThreadTimelinePage,
  shouldPollThreadTimeline,
  type ThreadTimelinePage,
} from "@/core/threads/thread-timeline";

type ThreadTimelineContract = {
  categories: string[];
  method: string;
  record_required_fields: string[];
  response_required_fields: string[];
  route: string;
};

const THREAD_TIMELINE_CONTRACT = JSON.parse(
  readFileSync(
    resolve(
      __dirname,
      "../../../../../contracts/thread_timeline_contract.json",
    ),
    "utf-8",
  ),
) as ThreadTimelineContract;

function page(
  records: Array<Record<string, unknown>>,
  overrides: Partial<Record<string, unknown>> = {},
): ThreadTimelinePage {
  return parseThreadTimelinePage(
    {
      thread_id: "thread-1",
      records,
      after_seq: 0,
      watermark_seq: 4,
      cursor: "cursor-1",
      has_more: false,
      truncated: false,
      ...overrides,
    },
    "thread-1",
  );
}

function record(seq: number, eventType = "task_started") {
  return {
    event_id: `thread-1:${seq}`,
    seq,
    run_id: "run-1",
    event_type: eventType,
    category: "message",
    content: { task_id: "task-1" },
    metadata: {},
    created_at: "2026-07-15T00:00:00Z",
  };
}

test("timeline parser stays aligned with the shared API contract", () => {
  expect(THREAD_TIMELINE_CONTRACT.route).toBe(
    "/api/threads/{thread_id}/timeline",
  );
  expect(THREAD_TIMELINE_CONTRACT.method).toBe("GET");
  expect(THREAD_TIMELINE_CONTRACT.categories).toEqual(
    THREAD_TIMELINE_CATEGORIES,
  );
  expect(THREAD_TIMELINE_CONTRACT.record_required_fields).toEqual([
    "event_id",
    "seq",
    "run_id",
    "event_type",
    "category",
    "content",
    "metadata",
    "created_at",
  ]);
  expect(THREAD_TIMELINE_CONTRACT.response_required_fields).toContain("cursor");
});

test("timeline projection is stable across duplicate and out-of-order pages", () => {
  const initial = mergeThreadTimelinePage(
    undefined,
    page([record(2), record(1)], { watermark_seq: 2, cursor: "cursor-2" }),
    "snapshot",
  );
  const merged = mergeThreadTimelinePage(
    initial,
    page([record(2), record(3, "task_completed")], {
      after_seq: 2,
      watermark_seq: 3,
      cursor: "cursor-3",
    }),
    "incremental",
  );

  expect(merged.records.map(({ seq }) => seq)).toEqual([1, 2, 3]);
  expect(merged.confirmedWatermarkSeq).toBe(3);
  expect(shouldPollThreadTimeline(merged, false)).toBe(false);
});

test("timeline conflicts and stale snapshots do not replace confirmed facts", () => {
  const initial = mergeThreadTimelinePage(
    undefined,
    page([record(3)], { watermark_seq: 3, cursor: "cursor-3" }),
    "snapshot",
  );

  expect(() =>
    mergeThreadTimelinePage(
      initial,
      page([{ ...record(3), content: { task_id: "other-task" } }], {
        after_seq: 3,
        watermark_seq: 3,
        cursor: "cursor-conflict",
      }),
      "incremental",
    ),
  ).toThrow(ThreadTimelineConflictError);

  const stale = mergeThreadTimelinePage(
    initial,
    page([record(1)], { watermark_seq: 2, cursor: "cursor-stale" }),
    "snapshot",
  );
  expect(stale).toBe(initial);
});

test("a cursor recovery snapshot replaces the prior bounded projection", () => {
  const staleProjection = mergeThreadTimelinePage(
    undefined,
    page([record(1)], { watermark_seq: 1, cursor: "expired-cursor" }),
    "snapshot",
  );
  const recovered = mergeThreadTimelinePage(
    staleProjection,
    page([record(3), record(4, "task_completed")], {
      watermark_seq: 4,
      cursor: "fresh-cursor",
      truncated: true,
    }),
    "snapshot",
  );

  expect(recovered.records.map(({ seq }) => seq)).toEqual([3, 4]);
  expect(recovered.cursor).toBe("fresh-cursor");
  expect(recovered.truncated).toBe(true);
});

test("timeline only polls for a factual active task, runtime activity, or catch-up page", () => {
  const active = mergeThreadTimelinePage(
    undefined,
    page([record(1)], { watermark_seq: 1, cursor: "cursor-start" }),
    "snapshot",
  );
  const terminal = mergeThreadTimelinePage(
    active,
    page([record(2, "task_completed")], {
      after_seq: 1,
      watermark_seq: 2,
      cursor: "cursor-terminal",
    }),
    "incremental",
  );
  const catchUp = { ...terminal, hasMore: true };

  expect(shouldPollThreadTimeline(active, false)).toBe(true);
  expect(shouldPollThreadTimeline(terminal, false)).toBe(false);
  expect(shouldPollThreadTimeline(terminal, true)).toBe(true);
  expect(shouldPollThreadTimeline(catchUp, false)).toBe(true);
});

test("timeline parser rejects a record outside the response thread identity", () => {
  expect(() => page([{ ...record(1), event_id: "other-thread:1" }])).toThrow(
    "Invalid timeline record.",
  );
});
