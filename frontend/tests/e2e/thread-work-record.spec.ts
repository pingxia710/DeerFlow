import { expect, test } from "@playwright/test";

import {
  MOCK_THREAD_ID,
  MOCK_THREAD_ID_2,
  mockLangGraphAPI,
} from "./utils/mock-api";

function timelineResponse() {
  return {
    thread_id: MOCK_THREAD_ID,
    records: [
      {
        event_id: `${MOCK_THREAD_ID}:1`,
        seq: 1,
        run_id: "run-1",
        event_type: "llm.human.input",
        category: "message",
        content: { type: "human", content: "This must stay in chat only." },
        metadata: {},
        created_at: "2026-07-15T10:00:00Z",
      },
      {
        event_id: `${MOCK_THREAD_ID}:2`,
        seq: 2,
        run_id: "run-1",
        event_type: "task_started",
        category: "message",
        content: { task_id: "task-1" },
        metadata: {},
        created_at: "2026-07-15T10:00:01Z",
      },
      {
        event_id: `${MOCK_THREAD_ID}:3`,
        seq: 3,
        run_id: "run-1",
        event_type: "artifact.presented",
        category: "artifact",
        content: { artifact_refs: ["report.md"] },
        metadata: {},
        created_at: "2026-07-15T10:00:02Z",
      },
      {
        event_id: `${MOCK_THREAD_ID}:4`,
        seq: 4,
        run_id: "run-1",
        event_type: "task_completed",
        category: "message",
        content: { task_id: "task-1" },
        metadata: {},
        created_at: "2026-07-15T10:00:03Z",
      },
      {
        event_id: `${MOCK_THREAD_ID}:5`,
        seq: 5,
        run_id: "run-1",
        event_type: "run.terminal",
        category: "lifecycle",
        content: { status: "success" },
        metadata: {},
        created_at: "2026-07-15T10:00:04Z",
      },
    ],
    after_seq: 0,
    watermark_seq: 5,
    cursor: "timeline-cursor-5",
    has_more: false,
    truncated: false,
  };
}

async function mockWorkRecord(page: Parameters<typeof mockLangGraphAPI>[0]) {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Work record test",
        messages: [
          { type: "human", id: "human-1", content: "Existing chat message" },
          { type: "ai", id: "ai-1", content: "Existing assistant message" },
        ],
      },
    ],
  });
  await page.route(`**/api/threads/${MOCK_THREAD_ID}/timeline**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(timelineResponse()),
    }),
  );
}

test("work record renders a factual desktop side panel", async ({
  page,
}, testInfo) => {
  await mockWorkRecord(page);
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await expect(page.getByText("Existing assistant message")).toBeVisible();

  await page.getByLabel("Open activity").click();
  const panel = page.getByRole("complementary", { name: "Activity" });
  await expect(panel).toBeVisible();
  await panel.getByText("Event history").click();
  await expect(panel.getByText("Task started")).toBeVisible();
  await expect(panel.getByText("Task completed")).toBeVisible();
  await expect(panel.getByText("Artifact recorded")).toBeVisible();
  await expect(panel.getByText("Run lifecycle")).toBeVisible();
  await expect(panel.getByText("This must stay in chat only.")).toHaveCount(0);
  const eventRows = panel.locator("ol > li");
  await expect(eventRows.nth(0)).toContainText("Run lifecycle");
  await expect(eventRows.nth(1)).toContainText("Task completed");
  await expect(eventRows.nth(2)).toContainText("Artifact recorded");
  await expect(eventRows.nth(3)).toContainText("Task started");

  await page.screenshot({
    path: testInfo.outputPath("work-record-desktop.png"),
  });
});

test("work record exposes the AI organization without approval controls", async ({
  page,
}, testInfo) => {
  await mockWorkRecord(page);
  let historyRequests = 0;
  await page.route(`**/api/threads/${MOCK_THREAD_ID}/goal-workspace`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        thread_id: MOCK_THREAD_ID,
        goal_mandate: {
          revision: 1,
          body: "Explore a durable AI-native enterprise.",
          content_hash: "mandate-hash",
          author_run_id: "run-owner",
          created_at: "2026-07-19T10:00:00Z",
        },
        operating_brief: {
          revision: 2,
          body: "Organize the V3 foundation and preserve every AI return.",
          content_hash: "brief-hash",
          author_run_id: "run-chair",
          created_at: "2026-07-19T10:01:00Z",
        },
        organization_map: {
          revision: 3,
          body: "Research Cell returns facts to the Chair for the next decision.",
          content_hash: "organization-hash",
          author_run_id: "run-chair",
          created_at: "2026-07-19T10:01:30Z",
        },
        acknowledged_through_seq: 7,
        notified_through_seq: 9,
        results: [
          {
            revision: 9,
            body: "Complete research findings with source-level detail.",
            content_hash: "result-hash",
            author_run_id: "run-cell",
            created_at: "2026-07-19T10:02:00Z",
            metadata: {
              role: "fact-finder",
              description: "Research Cell return",
              task_id: "task-research-a",
              source_run_id: "run-research-a",
            },
          },
          {
            revision: 10,
            body: "Complete research findings with source-level detail.",
            content_hash: "result-hash-b",
            author_run_id: "run-cell-b",
            created_at: "2026-07-19T10:02:01Z",
            metadata: {
              role: "fact-finder",
              description: "Research Cell return",
              task_id: "task-research-b",
              source_run_id: "run-research-b",
            },
          },
          {
            revision: 11,
            body: "Result without source fields remains readable.",
            content_hash: "result-hash-c",
            author_run_id: "run-cell-c",
            created_at: "2026-07-19T10:02:02Z",
            metadata: {},
          },
        ],
      }),
    }),
  );
  await page.route(
    `**/api/threads/${MOCK_THREAD_ID}/goal-workspace/history**`,
    (route) => {
      historyRequests += 1;
      const beforeRevision = new URL(route.request().url()).searchParams.get(
        "before_revision",
      );
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          beforeRevision === "6"
            ? {
                thread_id: MOCK_THREAD_ID,
                events: [
                  {
                    revision: 1,
                    event_type: "goal.mandate.revised",
                    body: "Original mandate remains an unchanged fact.",
                    content_hash: "older-mandate-hash",
                    author_run_id: "run-owner",
                    created_at: "2026-07-19T10:00:00Z",
                    metadata: { source: "human" },
                  },
                ],
                next_before_revision: null,
              }
            : {
                thread_id: MOCK_THREAD_ID,
                events: [
                  {
                    revision: 7,
                    event_type: "result.inbox.acknowledged",
                    body: "The Chair explicitly acknowledged result inbox through sequence 6.",
                    content_hash: "acknowledgement-hash",
                    author_run_id: "run-chair",
                    created_at: "2026-07-19T10:03:00Z",
                    metadata: { through_seq: 6 },
                  },
                  {
                    revision: 6,
                    event_type: "result.received",
                    body: "Complete previously acknowledged result stays available in History.",
                    content_hash: "acknowledged-result-hash",
                    author_run_id: "run-cell",
                    created_at: "2026-07-19T10:02:00Z",
                    metadata: { task_id: "task-older", role: "fact-finder" },
                  },
                ],
                next_before_revision: 6,
              },
        ),
      });
    },
  );
  await page.route(`**/api/threads/${MOCK_THREAD_ID}/goal-tree`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        root_thread_id: MOCK_THREAD_ID,
        cells: [
          {
            thread_id: MOCK_THREAD_ID_2,
            parent_thread_id: MOCK_THREAD_ID,
            parent_run_id: "run-chair",
            display_name: "Research Cell",
            runtime_status: "running",
            capability_refs: ["web-research"],
            workspace_ref: null,
            created_at: "2026-07-19T10:01:30Z",
            updated_at: "2026-07-19T10:02:30Z",
          },
        ],
      }),
    }),
  );

  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await page.getByLabel("Open activity").click();

  const panel = page.getByRole("complementary", { name: "Activity" });
  await expect(panel.getByText("Goal Mandate")).toBeVisible();
  expect(historyRequests).toBe(0);
  await panel.getByText("Goal Mandate").click();
  await expect(
    panel.getByText("Explore a durable AI-native enterprise."),
  ).toBeVisible();
  await expect(
    panel.getByText("Organize the V3 foundation and preserve every AI return."),
  ).toBeVisible();
  await expect(panel.getByText("Current Organization Map")).toBeVisible();
  await panel.getByText("Current Organization Map").click();
  await expect(
    panel.getByText(
      "Research Cell returns facts to the Chair for the next decision.",
    ),
  ).toBeVisible();
  await panel.getByText("Read on demand", { exact: true }).click();
  await expect(
    panel.getByText("result.inbox.acknowledged", { exact: true }),
  ).toBeVisible();
  expect(historyRequests).toBe(1);
  await panel.getByText("result.received", { exact: true }).click();
  await expect(
    panel.getByText(
      "Complete previously acknowledged result stays available in History.",
    ),
  ).toBeVisible();
  await panel.getByText("Load older facts", { exact: true }).click();
  await expect(
    panel.getByText("goal.mandate.revised", { exact: true }),
  ).toBeVisible();
  await panel.getByText("goal.mandate.revised", { exact: true }).click();
  await expect(
    panel.getByText("Original mandate remains an unchanged fact."),
  ).toBeVisible();
  await expect(panel.getByText("notified through 9")).toBeVisible();
  const resultInbox = panel.locator("section").filter({
    hasText: "Result inbox",
  });
  const resultCards = resultInbox
    .locator("details")
    .filter({ hasText: "Research Cell return" });
  await expect(resultCards).toHaveCount(2);
  await resultCards.nth(0).locator("summary").click();
  await expect(
    resultCards
      .nth(0)
      .getByText("Complete research findings with source-level detail."),
  ).toBeVisible();
  await expect(resultCards.nth(0)).toContainText("Role");
  await expect(resultCards.nth(0)).toContainText("fact-finder");
  await expect(resultCards.nth(0)).toContainText("Task ID");
  await expect(resultCards.nth(0)).toContainText("task-research-a");
  await expect(resultCards.nth(0)).toContainText("Source run ID");
  await expect(resultCards.nth(0)).toContainText("run-research-a");
  await expect(resultCards.nth(0).locator("a")).toHaveCount(0);
  await resultCards.nth(1).locator("summary").click();
  await expect(resultCards.nth(1)).toContainText("task-research-b");
  await expect(resultCards.nth(1)).toContainText("run-research-b");
  const fallbackResult = resultInbox
    .locator("details")
    .filter({ hasText: "Result without source fields remains readable." });
  await fallbackResult.locator("summary").click();
  await expect(fallbackResult).toContainText(
    "Result without source fields remains readable.",
  );
  await expect(fallbackResult.locator("dl")).toHaveCount(0);
  await expect(panel.getByText("Research Cell", { exact: true })).toBeVisible();
  await expect(panel.getByText("runtime: running")).toBeVisible();
  await expect(
    panel.getByRole("button", { name: /approve|accept|acknowledge/i }),
  ).toHaveCount(0);

  await page.screenshot({
    path: testInfo.outputPath("ai-organization-work-record.png"),
  });
});

test("active background tasks are visible before work facts arrive", async ({
  page,
}) => {
  const startedAt = new Date(Date.now() - 5_000).toISOString();
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Active work",
        messages: [
          { type: "human", id: "human-1", content: "Existing chat message" },
        ],
        runtimeSnapshot: {
          runs: [{ run_id: "run-1", status: "success" }],
          task_lanes: [
            {
              run_id: "run-1",
              task_id: "task-1",
              status: "in_progress",
              description: "Research current behavior",
              started_at: startedAt,
            },
            {
              run_id: "run-1",
              task_id: "task-2",
              status: "in_progress",
              description: "Check implementation details",
              started_at: startedAt,
            },
          ],
        },
      },
    ],
  });

  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await expect(
    page.getByRole("button", { name: "2 tasks running" }),
  ).toBeVisible();

  await page.getByLabel("Open activity").click();
  const panel = page.getByRole("complementary", { name: "Activity" });
  const sections = panel.locator("section");
  await expect(sections.nth(0)).toContainText("Running subtasks");
  await expect(sections.nth(0)).toContainText("Research current behavior");
  await expect(sections.nth(0)).toContainText("Check implementation details");
  await expect(sections.nth(0).locator("time")).toHaveCount(2);
  await expect(sections.nth(0).locator("time").first()).toHaveText(
    /^\d{2}:\d{2}$/,
  );
  await expect(sections.nth(1)).toContainText("Goal workspace");
});

test("work record calls queued task lanes queued, not running", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Queued work",
        runtimeSnapshot: {
          runs: [{ run_id: "run-queued", status: "success" }],
          task_lanes: [
            { task_id: "task-1", status: "pending" },
            { task_id: "task-2", status: "pending" },
          ],
        },
      },
    ],
  });

  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await expect(
    page.getByRole("button", { name: "2 tasks queued" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /running/i })).toHaveCount(0);
});

test("work record distinguishes queued and running task lanes", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Mixed work",
        runtimeSnapshot: {
          runs: [{ run_id: "run-mixed", status: "success" }],
          task_lanes: [
            { task_id: "task-queued", status: "pending" },
            { task_id: "task-running", status: "running" },
          ],
        },
      },
    ],
  });

  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await expect(
    page.getByRole("button", { name: "1 task queued · 1 task running" }),
  ).toBeVisible();
});

test("work record keeps a failed Chair wake with its task, source run, and round", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Wake fact work record",
        runtimeSnapshot: {
          runs: [{ run_id: "run-wake", status: "success" }],
          rounds: [
            {
              round_id: "round-wake",
              thread_id: MOCK_THREAD_ID,
              current_run_id: "run-wake",
              state: "closed",
            },
          ],
          task_lanes: [
            {
              thread_id: MOCK_THREAD_ID,
              run_id: "run-wake",
              round_id: "round-wake",
              task_id: "task-wake",
              status: "completed",
            },
          ],
        },
      },
    ],
  });
  await page.route(
    new RegExp(
      `/api/threads/${MOCK_THREAD_ID}/command-room/wake-facts(?:\\?.*)?$`,
    ),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          thread_id: MOCK_THREAD_ID,
          run_id: "run-wake",
          round_id: "round-wake",
          items: [
            {
              task_id: "task-wake",
              source_run_id: "run-wake",
              child_status: "completed",
              child_completed_at: "2026-07-17T00:00:01Z",
              wake_state: "failed",
              wake_attempts: 3,
              wake_failure_reason: "retry_exhausted",
              updated_at: "2026-07-17T00:00:04Z",
            },
          ],
        }),
      }),
  );
  await page.route(`**/api/threads/${MOCK_THREAD_ID}/timeline**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(timelineResponse()),
    }),
  );

  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await page.getByLabel("Open activity").click();

  const panel = page.getByRole("complementary", { name: "Activity" });
  const notice = panel.getByRole("alert");
  await expect(notice).toContainText("Task task-wake");
  await expect(notice).toContainText("source run run-wake");
  await expect(notice).toContainText("round round-wake");
  await expect(notice).toContainText("does not mean the project is complete");
  await expect(notice).not.toContainText("http_503");
  await expect(notice).not.toContainText("Retry");
});

test("work record uses a bottom sheet at 360px and returns to the chat", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await mockWorkRecord(page);
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await expect(page.getByText("Existing assistant message")).toBeVisible();

  await page.getByLabel("Open activity").click();
  const sheet = page.locator('[data-slot="sheet-content"]');
  await expect(sheet).toBeVisible();
  await sheet.getByText("Event history").click();
  await expect(sheet.getByText("Task completed")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("work-record-mobile.png"),
  });

  await page.getByLabel("Close activity").click();
  await expect(sheet).toBeHidden();
  await expect(page.getByText("Existing assistant message")).toBeVisible();
});

test("work record stays empty for an unpersisted chat", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await mockWorkRecord(page);
  const consoleErrors: string[] = [];
  const timelineRequests: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("request", (request) => {
    if (/\/api\/threads\/[^/]+\/timeline/.test(request.url())) {
      timelineRequests.push(request.url());
    }
  });

  await page.goto("/workspace/chats/new");
  await page.getByLabel("Open activity").click();

  await expect(page.getByText("No work facts recorded yet.")).toBeVisible();
  await expect(page.getByLabel("Refresh activity")).toBeDisabled();
  expect(timelineRequests).toHaveLength(0);
  expect(consoleErrors).toEqual([]);
});
