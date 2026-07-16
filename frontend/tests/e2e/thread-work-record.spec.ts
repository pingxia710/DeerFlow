import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

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

test("active background tasks are visible before work facts arrive", async ({
  page,
}) => {
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
            { task_id: "task-1", status: "in_progress" },
            { task_id: "task-2", status: "in_progress" },
          ],
        },
      },
    ],
  });

  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await expect(
    page.getByRole("button", { name: "2 tasks running" }),
  ).toBeVisible();
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
