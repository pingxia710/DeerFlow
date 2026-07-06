import { expect, test } from "@playwright/test";

import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

const STOPPED_RUN_ID = `run-${MOCK_THREAD_ID}`;
const STOPPED_TASK_ID = "call-stopped-subtask";
const STOPPED_TASK_DESCRIPTION = "Research stopped reload regression";
const STOPPED_TASK_PROMPT =
  "Investigate why the stopped subtask card should not remain running after reload.";

const stoppedSubtaskMessages = [
  {
    type: "human",
    id: "msg-human-stopped-subtask",
    content: [
      {
        type: "text",
        text: "Start a subtask and then stop before the task tool returns.",
      },
    ],
  },
  {
    type: "ai",
    id: "msg-ai-stopped-subtask",
    content: "I'll start that subtask and keep you posted.",
    additional_kwargs: {},
    response_metadata: {},
    tool_calls: [
      {
        id: STOPPED_TASK_ID,
        name: "task",
        args: {
          subagent_type: "general-purpose",
          description: STOPPED_TASK_DESCRIPTION,
          prompt: STOPPED_TASK_PROMPT,
        },
        type: "tool_call",
      },
    ],
    invalid_tool_calls: [],
  },
];

test.describe("Subtask card", () => {
  test("shows failed after a stopped task thread is reloaded", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Stopped subtask",
          updated_at: "2026-06-18T12:00:00Z",
          messages: [],
        },
      ],
    });
    await page.route(
      `**/api/threads/${MOCK_THREAD_ID}/runtime-snapshot`,
      async (route) => {
        if (route.request().method() !== "GET") {
          return route.fallback();
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            thread_id: MOCK_THREAD_ID,
            runs: [
              {
                run_id: STOPPED_RUN_ID,
                thread_id: MOCK_THREAD_ID,
                assistant_id: "lead_agent",
                status: "interrupted",
                metadata: {},
                kwargs: {},
                created_at: "2026-06-18T12:00:00Z",
                updated_at: "2026-06-18T12:01:00Z",
              },
            ],
            run_messages: [
              {
                run_id: STOPPED_RUN_ID,
                data: stoppedSubtaskMessages.map((message, index) => ({
                  run_id: STOPPED_RUN_ID,
                  seq: index + 1,
                  content: message,
                  metadata: { caller: "lead_agent" },
                  created_at: `2026-06-18T12:00:0${index}Z`,
                })),
                has_more: false,
              },
            ],
            task_lanes: [
              {
                thread_id: MOCK_THREAD_ID,
                run_id: STOPPED_RUN_ID,
                task_id: STOPPED_TASK_ID,
                status: "cancelled",
                error: "Parent run stopped before this task lane completed.",
                created_at: "2026-06-18T12:00:01Z",
              },
            ],
          }),
        });
      },
    );

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await page.reload();

    await expect(
      page.getByText("I'll start that subtask and keep you posted."),
    ).toBeVisible();
    await expect(page.getByText(STOPPED_TASK_DESCRIPTION)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("Subtask failed")).toBeVisible();
    await expect(page.getByText("Running subtask")).toHaveCount(0);
  });
});
