import { expect, test, type Route } from "@playwright/test";

import {
  mockLangGraphAPI,
  MOCK_RUN_ID,
  MOCK_THREAD_ID,
  MOCK_THREAD_ID_2,
} from "./utils/mock-api";

test("skill-mode navigation is not replaced by delayed chat metadata", async ({
  page,
}) => {
  const oldPrompt = "OLD-CHAT-MUST-NOT-RECLAIM-SKILL-MODE";
  let markStreamRequested!: () => void;
  let releaseStream!: () => void;
  let markStreamFulfilled!: () => void;
  let markSkillStreamFulfilled!: () => void;
  const streamRequested = new Promise<void>((resolve) => {
    markStreamRequested = resolve;
  });
  const streamRelease = new Promise<void>((resolve) => {
    releaseStream = resolve;
  });
  const streamFulfilled = new Promise<void>((resolve) => {
    markStreamFulfilled = resolve;
  });
  const skillStreamFulfilled = new Promise<void>((resolve) => {
    markSkillStreamFulfilled = resolve;
  });
  let streamRequestCount = 0;

  mockLangGraphAPI(page, {
    createdThreadIds: [MOCK_THREAD_ID, MOCK_THREAD_ID_2],
  });

  const delayedStream = async (route: Route) => {
    streamRequestCount += 1;
    const isOldStream = streamRequestCount === 1;
    if (isOldStream) {
      markStreamRequested();
      await streamRelease;
    }
    const threadId = isOldStream ? MOCK_THREAD_ID : MOCK_THREAD_ID_2;
    const body = [
      {
        event: "metadata",
        data: { run_id: MOCK_RUN_ID, thread_id: threadId },
      },
      {
        event: "values",
        data: {
          messages: [
            {
              type: "human",
              id: `human-${threadId}`,
              content: [{ type: "text", text: "Stream prompt" }],
            },
            {
              type: "ai",
              id: `ai-${threadId}`,
              content: "Stream response",
            },
          ],
        },
      },
      { event: "end", data: {} },
    ]
      .map(
        (event) =>
          `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`,
      )
      .join("");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: {
        "Content-Location": `/threads/${threadId}/runs/${MOCK_RUN_ID}`,
      },
      body,
    });
    if (isOldStream) {
      markStreamFulfilled();
    } else {
      markSkillStreamFulfilled();
    }
  };
  await page.route("**/api/langgraph/runs/stream", delayedStream);
  await page.route("**/api/langgraph/threads/*/runs/stream", delayedStream);

  await page.goto("/workspace/chats/new");
  const textarea = page.getByPlaceholder(/how can i assist you/i);
  await expect(textarea).toBeVisible({ timeout: 15_000 });
  await textarea.fill(oldPrompt);
  await textarea.press("Enter");
  await streamRequested;

  await page.keyboard.press("Control+k");
  await page.getByRole("option", { name: "Settings" }).click();
  const settings = page.getByRole("dialog");
  await settings.getByRole("button", { name: "Skills", exact: true }).click();
  await settings.getByRole("button", { name: "Create skill" }).click();

  await expect(page).toHaveURL(/\/workspace\/chats\/new\?mode=skill$/);

  releaseStream();
  await streamFulfilled;

  await expect(page).toHaveURL(/\/workspace\/chats\/new\?mode=skill$/);
  await expect(
    page.getByText("✨ Create Your Own Skill ✨", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(oldPrompt)).toHaveCount(0);

  await textarea.fill("NEW-SKILL-CHAT-MUST-STILL-COMMIT");
  await textarea.press("Enter");
  await skillStreamFulfilled;

  await expect(page).toHaveURL(
    new RegExp(`/workspace/chats/${MOCK_THREAD_ID_2}$`),
  );
});
