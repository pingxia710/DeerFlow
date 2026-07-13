import { expect, test, type Route } from "@playwright/test";

import {
  handleRunStream,
  mockLangGraphAPI,
  MOCK_THREAD_ID,
  MOCK_THREAD_ID_2,
} from "./utils/mock-api";

const MOCK_AGENTS = [
  {
    name: "test-agent",
    description: "A test agent for E2E tests",
    system_prompt: "You are a test agent.",
  },
];
const DEMO_THREAD_ID = "7cfa5f8f-a2f8-47ad-acbd-da7137baf990";

test.describe("Agent chat", () => {
  test("agent gallery page loads and shows agents", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents");

    // The agent card should appear with the agent name
    await expect(page.getByText("test-agent")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("agent chat page loads with input box", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/test-agent/chats/new");

    // The prompt input textarea should be visible
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
  });

  test("deleting a Command Room chat stops capability requests", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      agents: [{ name: "command-room", model: "mock-model" }],
    });
    await page.route(/\/api\/threads\/([^/]+)\/capabilities$/, (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ command_room_runtime: null }),
      });
    });

    let localDeleteStarted = false;
    let releaseLocalDelete: () => void = () => undefined;
    const localDeletePending = new Promise<void>((resolve) => {
      releaseLocalDelete = resolve;
    });
    const staleRequests: string[] = [];

    page.on("request", (request) => {
      if (
        localDeleteStarted &&
        request.url().includes(`/api/threads/${MOCK_THREAD_ID}/`)
      ) {
        staleRequests.push(`${request.method()} ${request.url()}`);
      }
    });

    await page.route(/\/api\/threads\/[^/]+$/, async (route) => {
      if (route.request().method() !== "DELETE") {
        return route.fallback();
      }
      localDeleteStarted = true;
      await localDeletePending;
      return route.fulfill({ status: 204 });
    });

    await page.goto("/workspace/agents/command-room/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill("Delete this Command Room chat");
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
    await recentThreadItem.hover();
    await recentThreadItem.getByRole("button", { name: /more/i }).click();
    await page.getByRole("menuitem", { name: /delete/i }).click();

    try {
      await expect.poll(() => localDeleteStarted).toBe(true);
      await page.waitForTimeout(300);
      expect(staleRequests).toEqual([]);
    } finally {
      releaseLocalDelete();
    }
  });

  test("agent model beats the globally saved model until this thread chooses", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      localStorage.setItem(
        "deerflow.local-settings",
        JSON.stringify({ context: { model_name: "gpt-5.5" } }),
      );
    });
    mockLangGraphAPI(page, {
      agents: [
        {
          name: "command-room",
          description: "Command Room",
          model: "gpt-5.6",
        },
      ],
      models: [
        {
          id: "gpt-5.5",
          name: "gpt-5.5",
          provider: "OpenAI",
          model: "gpt-5.5",
          display_name: "GPT-5.5",
          supports_thinking: true,
          supports_reasoning_effort: true,
        },
        {
          id: "gpt-5.6",
          name: "gpt-5.6",
          provider: "Codex CLI",
          model: "gpt-5.6-sol",
          display_name: "GPT-5.6 Sol",
          supports_thinking: true,
        },
      ],
    });
    let submittedModel: string | undefined;
    await page.route("**/runs/stream", (route) => {
      const body = route.request().postDataJSON() as {
        context?: { model_name?: string };
      };
      submittedModel = body.context?.model_name;
      return handleRunStream(route);
    });

    const agentResponsePromise = page.waitForResponse((response) =>
      response.url().endsWith("/api/agents/command-room"),
    );
    await page.goto("/workspace/agents/command-room/chats/new");
    await expect(page).toHaveURL(
      /\/workspace\/agents\/command-room\/chats\/new$/,
    );
    const agentResponse = await agentResponsePromise;
    await expect(agentResponse.json()).resolves.toMatchObject({
      model: "gpt-5.6",
    });
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: /5.6 Sol/i })).toBeVisible();
    await textarea.fill("Verify the configured model");
    await textarea.press("Enter");

    await expect
      .poll(() => submittedModel, { timeout: 10_000 })
      .toBe("gpt-5.6");
  });

  test("agent chat page shows agent badge", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/test-agent/chats/new");

    // The agent badge should display in the header (scoped to header to avoid
    // matching the welcome area which also shows the agent name)
    await expect(
      page.locator("header span", { hasText: "test-agent" }),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("agent mock chat keeps the prompt read-only", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto(
      `/workspace/agents/test-agent/chats/${DEMO_THREAD_ID}?mock=true`,
    );

    await expect(page.locator("textarea[name='message']")).toBeDisabled({
      timeout: 15_000,
    });
  });

  test("agent chat exposes regenerate on assistant turns", async ({ page }) => {
    mockLangGraphAPI(page, {
      agents: MOCK_AGENTS,
      threads: [
        {
          thread_id: MOCK_THREAD_ID_2,
          title: "Agent answer to regenerate",
          updated_at: "2025-06-04T12:00:00Z",
          agent_name: "test-agent",
          messages: [
            {
              type: "human",
              id: "msg-human-agent-regenerate",
              content: [{ type: "text", text: "Draft a launch plan" }],
            },
            {
              type: "ai",
              id: "msg-ai-agent-regenerate",
              content: "Agent response eligible for regeneration",
            },
          ],
        },
      ],
    });

    await page.goto(`/workspace/agents/test-agent/chats/${MOCK_THREAD_ID_2}`);
    await expect(
      page.getByText("Agent response eligible for regeneration"),
    ).toBeVisible({ timeout: 15_000 });

    await expect(page.getByRole("button", { name: "Regenerate" })).toHaveCount(
      1,
    );
  });

  test("agent new chat button resets while already on the new chat route", async ({
    page,
  }) => {
    const marker = "AGENT-NEW-CHAT-SHOULD-CLEAR-THIS";
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });
    let releaseStream: () => void = () => undefined;
    const pendingStream = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    const markerStream = async (route: Route) => {
      await pendingStream;
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "event: end\ndata: {}\n\n",
      });
    };
    await page.route("**/api/langgraph/runs/stream", markerStream);
    await page.route("**/api/langgraph/threads/*/runs/stream", markerStream);

    await page.goto("/workspace/agents/test-agent/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    try {
      await textarea.fill(marker);
      await textarea.press("Enter");

      await expect(page).toHaveURL(
        /\/workspace\/agents\/test-agent\/chats\/new$/,
      );
      await expect(page.getByText(marker).first()).toBeVisible();
      await page.getByRole("button", { name: /new chat/i }).click();
      await expect(page).toHaveURL(
        /\/workspace\/agents\/test-agent\/chats\/new$/,
      );
      await expect(page.getByText(marker)).toHaveCount(0);
    } finally {
      releaseStream();
    }
  });

  test("switching agent chats ignores a delayed stream from the previous chat", async ({
    page,
  }) => {
    const delayedMarker = "AGENT-LEAK-DELAYED-STREAM-SHOULD-STAY-OUT";
    let resolveStream!: () => void;
    const streamDone = new Promise<void>((resolve) => {
      resolveStream = resolve;
    });

    mockLangGraphAPI(page, {
      agents: MOCK_AGENTS,
      threads: [
        {
          thread_id: MOCK_THREAD_ID_2,
          title: "Destination agent conversation",
          updated_at: "2025-06-04T12:00:00Z",
          agent_name: "test-agent",
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
                id: "msg-human-agent-delayed-stream",
                content: [{ type: "text", text: delayedMarker }],
              },
              {
                type: "ai",
                id: "msg-ai-agent-delayed-stream",
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

    await page.goto("/workspace/agents/test-agent/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill(delayedMarker);
    await textarea.press("Enter");
    await expect(page.getByText(delayedMarker, { exact: true })).toBeVisible();

    await page.getByText("Destination agent conversation").click();
    await page.waitForURL(
      `**/workspace/agents/test-agent/chats/${MOCK_THREAD_ID_2}`,
    );

    await streamDone;

    await expect(page).toHaveURL(new RegExp(MOCK_THREAD_ID_2));
    await expect(page.getByText(delayedMarker)).toHaveCount(0);
    await expect(
      page.getByText("Response in thread Destination agent conversation"),
    ).toBeVisible();
  });

  test("agent chat history failure shows retry UI instead of a blank message list", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });
    await page.route(
      /\/api\/threads\/nonexistent\/runtime-snapshot(?:\?.*)?$/,
      (route) => {
        if (route.request().method() === "GET") {
          return route.fulfill({
            status: 404,
            contentType: "application/json",
            body: JSON.stringify({ detail: "Thread not found" }),
          });
        }
        return route.fallback();
      },
    );
    await page.route("**/api/langgraph/threads/nonexistent/history", (route) =>
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Thread not found" }),
      }),
    );
    await page.route(
      /\/api\/langgraph\/threads\/nonexistent\/runs(\?|$)/,
      (route) =>
        route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Thread not found" }),
        }),
    );

    await page.goto("/workspace/agents/test-agent/chats/nonexistent");

    const notice = page.getByTestId("run-recovery-notice");
    await expect(notice).toBeVisible({ timeout: 15_000 });
    await expect(notice.getByText("Recovery failed")).toBeVisible();
    await expect(notice.getByText("Thread not found")).toBeVisible();
    await expect(
      notice.getByRole("button", { name: "Retry recovery" }),
    ).toBeVisible();
  });
});
