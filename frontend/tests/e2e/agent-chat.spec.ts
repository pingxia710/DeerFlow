import { expect, test, type Route } from "@playwright/test";

import {
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
    await expect(page.getByText(delayedMarker)).toBeVisible();

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
});
