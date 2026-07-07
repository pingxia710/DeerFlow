import { expect, test, type Route } from "@playwright/test";

import {
  mockLangGraphAPI,
  MOCK_THREAD_ID,
  MOCK_THREAD_ID_2,
} from "./utils/mock-api";

const THREADS = [
  {
    thread_id: MOCK_THREAD_ID,
    title: "First conversation",
    updated_at: "2025-06-01T12:00:00Z",
  },
  {
    thread_id: MOCK_THREAD_ID_2,
    title: "Second conversation",
    updated_at: "2025-06-02T12:00:00Z",
  },
];
const DEMO_THREAD_ID = "7cfa5f8f-a2f8-47ad-acbd-da7137baf990";
const SVG_PROMPT_THREAD_ID = "00000000-0000-0000-0000-000000000777";
const SVG_PROMPT_MARKER = "LEAK-STRICT-SVG-PROMPT-SHOULD-DISAPPEAR";
const OPTIMISTIC_PROMPT_MARKER = "LEAK-OPTIMISTIC-SVG-PROMPT-SHOULD-DISAPPEAR";

test.describe("Thread history", () => {
  test("sidebar shows existing threads", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto("/workspace/chats/new");

    // Both thread titles should appear in the sidebar
    await expect(page.getByText("First conversation")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("Second conversation")).toBeVisible();
  });

  test("clicking a thread in sidebar navigates to it", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto("/workspace/chats/new");

    // Wait for sidebar to populate
    const firstThread = page.getByText("First conversation");
    await expect(firstThread).toBeVisible({ timeout: 15_000 });

    // Click on the first thread
    await firstThread.click();

    // Should navigate to that thread's URL
    await page.waitForURL(`**/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page).toHaveURL(new RegExp(MOCK_THREAD_ID));
  });

  test("clicking blank space in a sidebar thread row navigates to it", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto("/workspace/chats/new");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    const firstThreadItem = sidebar
      .locator("[data-sidebar='menu-item']")
      .filter({ hasText: "First conversation" })
      .first();
    await expect(firstThreadItem).toBeVisible({ timeout: 15_000 });

    const firstThreadLink = firstThreadItem.getByRole("link");
    await expect(firstThreadLink).toBeVisible();

    const box = await firstThreadLink.boundingBox();
    expect(box).not.toBeNull();
    if (!box) {
      return;
    }

    await firstThreadLink.click({ position: { x: 4, y: box.height / 2 } });

    await page.waitForURL(`**/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page).toHaveURL(new RegExp(MOCK_THREAD_ID));
  });

  test("existing thread loads historical messages", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    // Navigate directly to an existing thread
    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);

    // The historical AI response should be displayed
    await expect(
      page.getByText("Response in thread First conversation"),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("switching existing chats and reloading keeps histories isolated", async ({
    page,
  }) => {
    const markerA = "THREAD-A-HISTORY-MARKER";
    const markerB = "THREAD-B-HISTORY-MARKER";

    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "First conversation",
          updated_at: "2025-06-01T12:00:00Z",
          messages: [
            {
              type: "human",
              id: "thread-a-human",
              content: [{ type: "text", text: markerA }],
            },
            {
              type: "ai",
              id: "thread-a-ai",
              content: "Answer for thread A",
            },
          ],
        },
        {
          thread_id: MOCK_THREAD_ID_2,
          title: "Second conversation",
          updated_at: "2025-06-02T12:00:00Z",
          messages: [
            {
              type: "human",
              id: "thread-b-human",
              content: [{ type: "text", text: markerB }],
            },
            {
              type: "ai",
              id: "thread-b-ai",
              content: "Answer for thread B",
            },
          ],
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText(markerA)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(markerB)).toHaveCount(0);

    await page.getByText("Second conversation").click();
    await page.waitForURL(`**/workspace/chats/${MOCK_THREAD_ID_2}`);
    await expect(page.getByText(markerB)).toBeVisible();
    await expect(page.getByText(markerA)).toHaveCount(0);

    await page.reload();
    await expect(page.getByText(markerB)).toBeVisible();
    await expect(page.getByText(markerA)).toHaveCount(0);

    await page.getByText("First conversation").click();
    await page.waitForURL(`**/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText(markerA)).toBeVisible();
    await expect(page.getByText(markerB)).toHaveCount(0);
  });

  test("input box recalls previous prompts with arrow keys", async ({
    page,
  }) => {
    const firstPrompt = "Summarize the latest quarterly report";
    const secondPrompt = "Turn the summary into an action plan";

    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Prompt history conversation",
          updated_at: "2025-06-03T12:00:00Z",
          messages: [
            {
              type: "human",
              id: "msg-human-prompt-history-1",
              content: [{ type: "text", text: firstPrompt }],
            },
            {
              type: "ai",
              id: "msg-ai-prompt-history-1",
              content: "First answer",
            },
            {
              type: "human",
              id: "msg-human-prompt-history-2",
              content: [{ type: "text", text: secondPrompt }],
            },
            {
              type: "ai",
              id: "msg-ai-prompt-history-2",
              content: "Second answer",
            },
          ],
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("Second answer")).toBeVisible({
      timeout: 15_000,
    });

    const textarea = page.locator("textarea[name='message']");
    await expect(textarea).toBeVisible();

    await textarea.focus();
    await textarea.press("ArrowUp");
    await expect(textarea).toHaveValue(secondPrompt);

    await textarea.press("ArrowUp");
    await expect(textarea).toHaveValue(firstPrompt);

    await textarea.press("ArrowDown");
    await expect(textarea).toHaveValue(secondPrompt);

    await textarea.press("ArrowDown");
    await expect(textarea).toHaveValue("");

    await textarea.fill("draft should not be overwritten");
    await textarea.press("ArrowUp");
    await expect(textarea).toHaveValue("draft should not be overwritten");
  });

  test("switching chats clears the input draft", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);

    const textarea = page.locator("textarea[name='message']");
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill("draft must not leak to the next chat");

    await page.getByText("Second conversation").click();
    await page.waitForURL(`**/workspace/chats/${MOCK_THREAD_ID_2}`);

    await expect(textarea).toHaveValue("");
  });

  test("deleting an inactive chat keeps the current chat open", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(
      page.getByText("Response in thread First conversation"),
    ).toBeVisible({ timeout: 15_000 });

    const sidebar = page.locator("[data-sidebar='sidebar']");
    const inactiveThreadItem = sidebar
      .locator("[data-sidebar='menu-item']")
      .filter({
        has: page.getByRole("button", { name: /more/i }),
        hasText: "Second conversation",
      })
      .first();
    await expect(inactiveThreadItem).toBeVisible();
    await inactiveThreadItem.hover();
    await inactiveThreadItem.getByRole("button", { name: /more/i }).click();
    await page.getByRole("menuitem", { name: /delete/i }).click();

    await expect(page).toHaveURL(new RegExp(MOCK_THREAD_ID));
    await expect(
      page.getByText("Response in thread First conversation"),
    ).toBeVisible();
    await expect(sidebar.getByText("Second conversation")).toHaveCount(0);
  });

  test("deleting the active existing chat removes it and prevents stale history from returning", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(
      page.getByText("Response in thread First conversation"),
    ).toBeVisible({ timeout: 15_000 });

    const sidebar = page.locator("[data-sidebar='sidebar']");
    const activeThreadItem = sidebar
      .locator("[data-sidebar='menu-item']")
      .filter({
        has: page.getByRole("button", { name: /more/i }),
        hasText: "First conversation",
      })
      .first();
    await expect(activeThreadItem).toBeVisible();
    await activeThreadItem.hover();
    await activeThreadItem.getByRole("button", { name: /more/i }).click();
    await page.getByRole("menuitem", { name: /delete/i }).click();

    await page.waitForURL("**/workspace/chats/new");
    await expect(sidebar.getByText("First conversation")).toHaveCount(0);
    await expect(
      page.getByText("Response in thread First conversation"),
    ).toHaveCount(0);
    await expect(page.getByPlaceholder(/how can i assist you/i)).toBeVisible();

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);

    await expect(
      page.getByText("Response in thread First conversation"),
    ).toHaveCount(0);
    await expect(sidebar.getByText("First conversation")).toHaveCount(0);
    await expect(page.getByPlaceholder(/how can i assist you/i)).toBeVisible();
  });

  test("new chat does not show previous thread messages after client-side navigation", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: SVG_PROMPT_THREAD_ID,
          title: "SVG artifact prompt",
          updated_at: "2025-06-03T12:00:00Z",
          messages: [
            {
              type: "human",
              id: "msg-human-svg-prompt",
              content: [
                {
                  type: "text",
                  text: `请严格执行：\n1. 使用 write_file 创建 /mnt/user-data/outputs/shared.svg，内容包含 ${SVG_PROMPT_MARKER}\n2. 最终回复只输出 Markdown 图片。`,
                },
              ],
            },
            {
              type: "ai",
              id: "msg-ai-svg-prompt",
              content: "![shared artifact](/mnt/user-data/outputs/shared.svg)",
            },
          ],
        },
      ],
    });

    await page.goto(`/workspace/chats/${SVG_PROMPT_THREAD_ID}`);
    await expect(page.getByText(SVG_PROMPT_MARKER)).toBeVisible({
      timeout: 15_000,
    });

    await page
      .locator("[data-sidebar='sidebar'] a[href='/workspace/chats/new']")
      .click();
    await page.waitForURL("**/workspace/chats/new");

    await expect(page.getByText(SVG_PROMPT_MARKER)).toBeHidden();
    await expect(page.getByPlaceholder(/how can i assist you/i)).toBeVisible();
  });

  test("new chat does not show previous optimistic user message after client-side navigation", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID_2,
          title: "Destination conversation",
          updated_at: "2025-06-04T12:00:00Z",
        },
      ],
    });

    const metadataOnlyStream = async (route: Route) => {
      const body = [
        {
          event: "metadata",
          data: {
            run_id: "00000000-0000-0000-0000-000000000778",
            thread_id: MOCK_THREAD_ID,
          },
        },
        { event: "end", data: {} },
      ]
        .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
        .join("");

      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body,
      });
    };

    await page.route("**/api/langgraph/runs/stream", metadataOnlyStream);
    await page.route(
      "**/api/langgraph/threads/*/runs/stream",
      metadataOnlyStream,
    );

    await page.goto("/workspace/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill(
      `请严格执行：使用 write_file 创建 shared.svg，内容包含 ${OPTIMISTIC_PROMPT_MARKER}。`,
    );
    await textarea.press("Enter");

    await expect(page.getByText(OPTIMISTIC_PROMPT_MARKER)).toBeVisible();

    await page.getByText("Destination conversation").click();
    await page.waitForURL(`**/workspace/chats/${MOCK_THREAD_ID_2}`);
    await expect(page.getByText(OPTIMISTIC_PROMPT_MARKER)).toHaveCount(0);

    await page
      .locator("[data-sidebar='sidebar'] a[href='/workspace/chats/new']")
      .click();
    await page.waitForURL("**/workspace/chats/new");

    await expect(page.getByText(OPTIMISTIC_PROMPT_MARKER)).toHaveCount(0);
    await expect(page.getByPlaceholder(/how can i assist you/i)).toBeVisible();
  });

  test("switching chats ignores a delayed stream from the previous chat", async ({
    page,
  }) => {
    const delayedMarker = "LEAK-DELAYED-STREAM-SHOULD-STAY-OUT";
    let resolveStream!: () => void;
    const streamDone = new Promise<void>((resolve) => {
      resolveStream = resolve;
    });

    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID_2,
          title: "Destination conversation",
          updated_at: "2025-06-04T12:00:00Z",
        },
      ],
    });

    const delayedPreviousStream = async (route: Route) => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      const body = [
        {
          event: "metadata",
          data: {
            run_id: "00000000-0000-0000-0000-000000000779",
            thread_id: MOCK_THREAD_ID,
          },
        },
        {
          event: "values",
          data: {
            messages: [
              {
                type: "human",
                id: "msg-human-delayed-stream",
                content: [{ type: "text", text: delayedMarker }],
              },
              {
                type: "ai",
                id: "msg-ai-delayed-stream",
                content: `Delayed answer containing ${delayedMarker}`,
              },
            ],
          },
        },
        { event: "end", data: {} },
      ]
        .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
        .join("");

      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body,
      });
      resolveStream();
    };

    await page.route("**/api/langgraph/runs/stream", delayedPreviousStream);
    await page.route(
      "**/api/langgraph/threads/*/runs/stream",
      delayedPreviousStream,
    );

    await page.goto("/workspace/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill(delayedMarker);
    await textarea.press("Enter");
    await expect(page.getByText(delayedMarker)).toBeVisible();

    await page.getByText("Destination conversation").click();
    await page.waitForURL(`**/workspace/chats/${MOCK_THREAD_ID_2}`);

    await streamDone;

    await expect(page).toHaveURL(new RegExp(MOCK_THREAD_ID_2));
    await expect(page.getByText(delayedMarker)).toHaveCount(0);
    await expect(
      page.getByText("Response in thread Destination conversation"),
    ).toBeVisible();
  });

  test("switching chats keeps simultaneous streams scoped to their threads", async ({
    page,
  }) => {
    const markerA = "THREAD-A-LATE-STREAM-MUST-STAY-IN-A";
    const markerB = "THREAD-B-OWN-STREAM-MUST-STAY-IN-B";
    let releaseThreadA!: () => void;
    const threadAReleased = new Promise<void>((resolve) => {
      releaseThreadA = resolve;
    });
    let resolveThreadAFulfilled!: () => void;
    const threadAFulfilled = new Promise<void>((resolve) => {
      resolveThreadAFulfilled = resolve;
    });

    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID_2,
          title: "Destination conversation",
          updated_at: "2025-06-04T12:00:00Z",
        },
      ],
    });

    const fulfillStream = (
      route: Route,
      {
        threadId,
        runId,
        marker,
      }: { threadId: string; runId: string; marker: string },
    ) => {
      const body = [
        {
          event: "metadata",
          data: {
            run_id: runId,
            thread_id: threadId,
          },
        },
        {
          event: "values",
          data: {
            messages: [
              {
                type: "human",
                id: `msg-human-${runId}`,
                content: [{ type: "text", text: marker }],
              },
              {
                type: "ai",
                id: `msg-ai-${runId}`,
                content: `Answer for ${marker}`,
              },
            ],
          },
        },
        { event: "end", data: {} },
      ]
        .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
        .join("");

      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body,
      });
    };

    await page.route("**/api/langgraph/runs/stream", async (route) => {
      await threadAReleased;
      await fulfillStream(route, {
        threadId: MOCK_THREAD_ID,
        runId: "run-thread-a-late",
        marker: markerA,
      });
      resolveThreadAFulfilled();
    });
    await page.route(
      "**/api/langgraph/threads/*/runs/stream",
      async (route) => {
        const url = route.request().url();
        if (url.includes(MOCK_THREAD_ID_2)) {
          await fulfillStream(route, {
            threadId: MOCK_THREAD_ID_2,
            runId: "run-thread-b",
            marker: markerB,
          });
          return;
        }
        await threadAReleased;
        await fulfillStream(route, {
          threadId: MOCK_THREAD_ID,
          runId: "run-thread-a-late",
          marker: markerA,
        });
        resolveThreadAFulfilled();
      },
    );

    await page.goto("/workspace/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill(markerA);
    await textarea.press("Enter");
    await expect(page.getByText(markerA)).toBeVisible();

    await page.getByText("Destination conversation").click();
    await page.waitForURL(`**/workspace/chats/${MOCK_THREAD_ID_2}`);
    await expect(page.getByText(markerA)).toHaveCount(0);

    await textarea.fill(markerB);
    await textarea.press("Enter");
    await expect(page.getByText(`Answer for ${markerB}`)).toBeVisible({
      timeout: 15_000,
    });

    releaseThreadA();
    await threadAFulfilled;

    await expect(page.getByText(`Answer for ${markerA}`)).toHaveCount(0);
    await expect(
      page.locator(
        `[data-sidebar='sidebar'] a[href='/workspace/chats/${MOCK_THREAD_ID}']`,
      ),
    ).toHaveAttribute("aria-label", /Finished/, { timeout: 15_000 });
    await expect(page.getByText(markerB, { exact: true })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(MOCK_THREAD_ID_2));
  });

  test("separate browser tabs keep simultaneous streams scoped", async ({
    page,
    context,
  }) => {
    const pageA = page;
    const pageB = await context.newPage();
    const markerA = "TAB-A-LATE-STREAM-MUST-STAY-IN-A";
    const markerB = "TAB-B-OWN-STREAM-MUST-STAY-IN-B";
    let releaseThreadA!: () => void;
    const threadAReleased = new Promise<void>((resolve) => {
      releaseThreadA = resolve;
    });

    mockLangGraphAPI(pageA, { threads: THREADS });
    mockLangGraphAPI(pageB, { threads: THREADS });

    const fulfillStream = (
      route: Route,
      {
        threadId,
        runId,
        marker,
      }: { threadId: string; runId: string; marker: string },
    ) => {
      const body = [
        {
          event: "metadata",
          data: {
            run_id: runId,
            thread_id: threadId,
          },
        },
        {
          event: "values",
          data: {
            messages: [
              {
                type: "human",
                id: `msg-human-${runId}`,
                content: [{ type: "text", text: marker }],
              },
              {
                type: "ai",
                id: `msg-ai-${runId}`,
                content: `Answer for ${marker}`,
              },
            ],
          },
        },
        { event: "end", data: {} },
      ]
        .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
        .join("");

      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body,
      });
    };

    await pageA.route(
      "**/api/langgraph/threads/*/runs/stream",
      async (route) => {
        await threadAReleased;
        await fulfillStream(route, {
          threadId: MOCK_THREAD_ID,
          runId: "run-tab-a",
          marker: markerA,
        });
      },
    );
    await pageB.route(
      "**/api/langgraph/threads/*/runs/stream",
      async (route) => {
        await fulfillStream(route, {
          threadId: MOCK_THREAD_ID_2,
          runId: "run-tab-b",
          marker: markerB,
        });
      },
    );

    await pageA.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await pageB.goto(`/workspace/chats/${MOCK_THREAD_ID_2}`);
    const textareaA = pageA.getByPlaceholder(/how can i assist you/i);
    const textareaB = pageB.getByPlaceholder(/how can i assist you/i);
    await expect(textareaA).toBeVisible({ timeout: 15_000 });
    await expect(textareaB).toBeVisible({ timeout: 15_000 });

    await textareaA.fill(markerA);
    await textareaA.press("Enter");
    await expect(pageA.getByText(markerA)).toBeVisible();

    await textareaB.fill(markerB);
    await textareaB.press("Enter");
    await expect(pageB.getByText(`Answer for ${markerB}`)).toBeVisible({
      timeout: 15_000,
    });
    await expect(pageB.getByText(markerA)).toHaveCount(0);

    releaseThreadA();

    await expect(pageA.getByText(`Answer for ${markerA}`)).toBeVisible({
      timeout: 15_000,
    });
    await expect(pageA.getByText(markerB)).toHaveCount(0);
    await expect(pageB.getByText(markerA)).toHaveCount(0);
    await pageB.close();
  });

  test("queued follow-up stays on its original thread after switching chats", async ({
    page,
  }) => {
    const firstMarker = "THREAD-A-FIRST-HELD-STREAM";
    const queuedMarker = "THREAD-A-QUEUED-FOLLOW-UP";
    let releaseFirstStream!: () => void;
    const firstStreamReleased = new Promise<void>((resolve) => {
      releaseFirstStream = resolve;
    });
    let resolveQueuedSubmit!: () => void;
    const queuedSubmitSeen = new Promise<void>((resolve) => {
      resolveQueuedSubmit = resolve;
    });
    let threadAStreamCount = 0;
    let threadBStreamCount = 0;
    const streamRequests: string[] = [];

    mockLangGraphAPI(page, { threads: THREADS });

    const fulfillStream = (
      route: Route,
      {
        threadId,
        runId,
        marker,
      }: { threadId: string; runId: string; marker: string },
    ) => {
      const body = [
        {
          event: "metadata",
          data: {
            run_id: runId,
            thread_id: threadId,
          },
        },
        {
          event: "values",
          data: {
            messages: [
              {
                type: "human",
                id: `msg-human-${runId}`,
                content: [{ type: "text", text: marker }],
              },
              {
                type: "ai",
                id: `msg-ai-${runId}`,
                content: `Answer for ${marker}`,
              },
            ],
          },
        },
        { event: "end", data: {} },
      ]
        .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
        .join("");

      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body,
      });
    };

    const handleStreamRoute = async (route: Route) => {
      const url = route.request().url();
      streamRequests.push(url);
      if (url.includes(MOCK_THREAD_ID_2)) {
        threadBStreamCount += 1;
        await fulfillStream(route, {
          threadId: MOCK_THREAD_ID_2,
          runId: "run-thread-b-unexpected",
          marker: "THREAD-B-UNEXPECTED-STREAM",
        });
        return;
      }

      threadAStreamCount += 1;
      if (threadAStreamCount === 1) {
        await firstStreamReleased;
        await fulfillStream(route, {
          threadId: MOCK_THREAD_ID,
          runId: "run-thread-a-first",
          marker: firstMarker,
        });
        return;
      }

      await fulfillStream(route, {
        threadId: MOCK_THREAD_ID,
        runId: "run-thread-a-queued",
        marker: queuedMarker,
      });
      resolveQueuedSubmit();
    };

    await page.route("**/api/langgraph/runs/stream", handleStreamRoute);
    await page.route(
      "**/api/langgraph/threads/*/runs/stream",
      handleStreamRoute,
    );

    const waitForQueuedSubmit = () =>
      Promise.race([
        queuedSubmitSeen,
        new Promise<never>((_, reject) =>
          setTimeout(
            () =>
              reject(
                new Error(
                  `queued submit was not released: A=${threadAStreamCount}, B=${threadBStreamCount}, urls=${streamRequests.join(",")}`,
                ),
              ),
            10_000,
          ),
        ),
      ]);

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill(firstMarker);
    await textarea.press("Enter");
    await expect(page.getByText(firstMarker)).toBeVisible();

    await textarea.fill(queuedMarker);
    await textarea.press("Enter");
    await expect(textarea).toHaveValue("");
    await expect(page.getByText(queuedMarker)).toBeVisible();

    await page.getByText("Second conversation").click();
    await page.waitForURL(`**/workspace/chats/${MOCK_THREAD_ID_2}`);
    await expect(page.getByText(firstMarker)).toHaveCount(0);
    await expect(page.getByText(queuedMarker)).toHaveCount(0);

    releaseFirstStream();
    await waitForQueuedSubmit();

    expect(threadAStreamCount).toBe(2);
    expect(threadBStreamCount).toBe(0);
    await expect(page).toHaveURL(new RegExp(MOCK_THREAD_ID_2));
    await expect(page.getByText(firstMarker)).toHaveCount(0);
    await expect(page.getByText(queuedMarker)).toHaveCount(0);
    await expect(
      page.getByText("Response in thread Second conversation"),
    ).toBeVisible();
  });

  test("runtime snapshot terminal wins over stale active runs", async ({
    page,
  }) => {
    const runId = "snapshot-terminal-run";
    const marker = "SNAPSHOT-TERMINAL-HUMAN";
    let staleRunQueries = 0;
    let activeRunMessageLoads = 0;

    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Snapshot terminal conversation",
          updated_at: "2025-06-05T12:00:00Z",
          runtimeSnapshot: {
            runs: [
              {
                run_id: runId,
                thread_id: MOCK_THREAD_ID,
                assistant_id: "lead_agent",
                status: "success",
                terminal_reason: "success",
                metadata: {},
                kwargs: {},
                created_at: "2025-06-05T12:00:00Z",
                updated_at: "2025-06-05T12:00:01Z",
              },
            ],
            run_messages: [
              {
                run_id: runId,
                data: [
                  {
                    run_id: runId,
                    content: {
                      type: "human",
                      id: "snapshot-terminal-human",
                      content: [{ type: "text", text: marker }],
                    },
                    metadata: { caller: "lead_agent" },
                    created_at: "2025-06-05T12:00:00Z",
                  },
                ],
                hasMore: false,
              },
            ],
          },
        },
      ],
    });

    await page.route(
      /\/api\/langgraph\/threads\/[^/]+\/runs(\?|$)/,
      (route) => {
        if (
          route.request().method() === "GET" &&
          route.request().url().includes(MOCK_THREAD_ID)
        ) {
          staleRunQueries += 1;
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify([
              {
                run_id: runId,
                thread_id: MOCK_THREAD_ID,
                assistant_id: "lead_agent",
                status: "running",
                metadata: {},
                kwargs: {},
                created_at: "2025-06-05T12:00:00Z",
                updated_at: "2025-06-05T12:00:02Z",
              },
            ]),
          });
        }
        return route.fallback();
      },
    );
    await page.route(
      new RegExp(`/api/threads/${MOCK_THREAD_ID}/runs/${runId}/messages`),
      (route) => {
        activeRunMessageLoads += 1;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: [], hasMore: false }),
        });
      },
    );

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText(marker)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("运行已终止")).toBeVisible();
    await expect(page.getByText("终止原因：success")).toBeVisible();
    await expect
      .poll(() => staleRunQueries, { timeout: 15_000 })
      .toBeGreaterThan(0);
    expect(activeRunMessageLoads).toBe(0);
  });

  test("new chat resets immediately after a history-only thread URL update", async ({
    page,
  }) => {
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill("Message that must disappear in the next new chat");
    await textarea.press("Enter");
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 15_000,
    });

    // A newly created chat changes the URL with history.replaceState so the
    // active stream is not remounted. Reproduce that history-only transition:
    // the canonical pathname becomes the UUID while useParams can stay "new".
    await page.evaluate((threadId) => {
      history.replaceState(null, "", `/workspace/chats/${threadId}`);
    }, MOCK_THREAD_ID);

    const newChatLink = page.locator(
      "[data-sidebar='sidebar'] a[href='/workspace/chats/new']",
    );
    await expect(page).toHaveURL(
      new RegExp(`/workspace/chats/${MOCK_THREAD_ID}$`),
    );
    await expect(newChatLink).toHaveAttribute("data-active", "false");

    // One click must reset the chat without a second click or unrelated UI
    // interaction forcing another render.
    await newChatLink.click();
    await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    await expect(page.getByText("Hello from DeerFlow!")).toHaveCount(0);
    await expect(textarea).toBeVisible();
  });

  test("deleting the active newly created chat returns to the new chat screen", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await page.route(/\/api\/threads\/[^/]+$/, (route) => {
      if (route.request().method() === "DELETE") {
        return route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Local cleanup failed" }),
        });
      }
      return route.fallback();
    });

    await page.goto("/workspace/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill("What should disappear after deletion?");
    await textarea.press("Enter");

    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 15_000,
    });

    const sidebar = page.locator("[data-sidebar='sidebar']");
    const recentThreadItem = sidebar
      .locator("[data-sidebar='menu-item']")
      .filter({
        has: page.getByRole("button", { name: /more/i }),
        hasText: "New Chat",
      })
      .first();
    await expect(recentThreadItem).toBeVisible();
    await recentThreadItem.hover();
    await recentThreadItem.getByRole("button", { name: /more/i }).click();
    await page.getByRole("menuitem", { name: /delete/i }).click();

    await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    await expect(page.getByText("Previous question")).toHaveCount(0);
    await expect(page.getByText("Hello from DeerFlow!")).toHaveCount(0);
    await expect(page.getByPlaceholder(/how can i assist you/i)).toBeVisible();

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page).toHaveURL(
      new RegExp(`/workspace/chats/${MOCK_THREAD_ID}$`),
    );
    await expect(page.getByText("Hello from DeerFlow!")).toHaveCount(0);
    await expect(page.getByPlaceholder(/how can i assist you/i)).toBeVisible();
  });

  test("mock thread does not load real backend run history", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: DEMO_THREAD_ID,
          title: "Forecasting 2026 Trends and Opportunities",
          updated_at: "2025-06-01T12:00:00Z",
          messages: [
            {
              type: "human",
              id: `run-human-${DEMO_THREAD_ID}`,
              content: [
                {
                  type: "text",
                  text: "This run-message endpoint should not be called.",
                },
              ],
            },
          ],
        },
      ],
    });
    const backendRunHistoryUrls: string[] = [];
    await page.route(
      /\/api\/langgraph\/threads\/[^/]+\/runs(?:\?|$)/,
      (route) => {
        if (
          route.request().method() === "GET" &&
          route
            .request()
            .url()
            .includes(`/api/langgraph/threads/${DEMO_THREAD_ID}/runs`)
        ) {
          backendRunHistoryUrls.push(route.request().url());
          return route.fulfill({
            status: 500,
            contentType: "application/json",
            body: JSON.stringify({
              error: "mock=true must not load real runs",
            }),
          });
        }
        return route.fallback();
      },
    );
    await page.route(
      /\/api\/threads\/[^/]+\/runs\/[^/]+\/messages(?:\?|$)/,
      (route) => {
        if (
          route.request().method() === "GET" &&
          route.request().url().includes(`/api/threads/${DEMO_THREAD_ID}/runs/`)
        ) {
          backendRunHistoryUrls.push(route.request().url());
          return route.fulfill({
            status: 500,
            contentType: "application/json",
            body: JSON.stringify({
              error: "mock=true must not load real run messages",
            }),
          });
        }
        return route.fallback();
      },
    );

    await page.goto(`/workspace/chats/${DEMO_THREAD_ID}?mock=true`);

    await expect(
      page.getByText("What might be the trends and opportunities in 2026?"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByText("I've created a modern, minimalist website"),
    ).toBeVisible();
    expect(backendRunHistoryUrls).toEqual([]);
  });

  test("chats list page shows all threads", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto("/workspace/chats");

    // Both threads should be listed in the main content area
    const main = page.locator("main");
    await expect(main.getByText("First conversation")).toBeVisible({
      timeout: 15_000,
    });
    await expect(main.getByText("Second conversation")).toBeVisible();
  });

  test("IM channel threads show their source in thread lists", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Feishu conversation",
          updated_at: "2025-06-03T12:00:00Z",
          metadata: {
            channel_source: {
              type: "im_channel",
              provider: "feishu",
              chat_id: "oc_mock",
            },
          },
        },
      ],
    });

    await page.goto("/workspace/chats/new");

    const sidebarThread = page.locator(
      `a[href='/workspace/chats/${MOCK_THREAD_ID}']`,
    );
    await expect(sidebarThread).toBeVisible({ timeout: 15_000 });
    await expect(sidebarThread.getByLabel("Feishu channel")).toBeVisible();

    await page.goto("/workspace/chats");

    const mainThread = page
      .locator("main")
      .locator(`a[href='/workspace/chats/${MOCK_THREAD_ID}']`);
    await expect(mainThread.getByText("Feishu conversation")).toBeVisible({
      timeout: 15_000,
    });
    await expect(mainThread.getByText("Feishu", { exact: true })).toBeVisible();
  });
});
