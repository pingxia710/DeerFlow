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
      new RegExp(`/api/threads/${MOCK_THREAD_ID}/runtime-snapshot(?:\\?.*)?$`),
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

const COMPLETED_RUN_ID = `run-completed-${MOCK_THREAD_ID}`;
const REPLAY_RUN_ID = `run-replay-${MOCK_THREAD_ID}`;
const COMPLETED_TASK_ID = "call-completed-subtask";
const COMPLETED_TASK_DESCRIPTION = "Completed subtask render smoke";
const COMPLETED_TASK_PROMPT = "Complete this subtask and preserve the result.";
const COMPLETED_TASK_RESULT = "Completed terminal result should stay visible.";

const completedSubtaskMessages = [
  {
    type: "human",
    id: "msg-human-completed-subtask",
    content: [{ type: "text", text: "Run a subtask to completion." }],
  },
  {
    type: "ai",
    id: "msg-ai-completed-subtask",
    content: "I'll run that subtask.",
    additional_kwargs: { run_id: COMPLETED_RUN_ID },
    response_metadata: {},
    tool_calls: [
      {
        id: COMPLETED_TASK_ID,
        name: "task",
        args: {
          subagent_type: "general-purpose",
          description: COMPLETED_TASK_DESCRIPTION,
          prompt: COMPLETED_TASK_PROMPT,
        },
        type: "tool_call",
      },
    ],
    invalid_tool_calls: [],
  },
];

test.describe("Subtask card render smoke", () => {
  test("keeps completed terminal subtask visible over stale running replay", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Completed subtask smoke",
          updated_at: "2026-06-18T13:00:00Z",
          messages: completedSubtaskMessages,
          runtimeSnapshot: {
            runs: [
              {
                run_id: COMPLETED_RUN_ID,
                thread_id: MOCK_THREAD_ID,
                assistant_id: "lead_agent",
                status: "success",
                metadata: {},
                kwargs: {},
                created_at: "2026-06-18T13:00:00Z",
                updated_at: "2026-06-18T13:01:00Z",
              },
              {
                run_id: REPLAY_RUN_ID,
                thread_id: MOCK_THREAD_ID,
                assistant_id: "lead_agent",
                status: "running",
                metadata: {},
                kwargs: {},
                created_at: "2026-06-18T13:02:00Z",
                updated_at: "2026-06-18T13:02:30Z",
              },
            ],
            run_messages: [
              {
                run_id: COMPLETED_RUN_ID,
                data: completedSubtaskMessages.map((message, index) => ({
                  run_id: COMPLETED_RUN_ID,
                  seq: index + 1,
                  content: message,
                  metadata: { caller: "lead_agent" },
                  created_at: `2026-06-18T13:00:0${index}Z`,
                })),
                has_more: false,
              },
              {
                run_id: REPLAY_RUN_ID,
                data: [
                  {
                    run_id: REPLAY_RUN_ID,
                    seq: 1,
                    content: {
                      ...completedSubtaskMessages[1],
                      id: "msg-ai-stale-running-replay",
                      additional_kwargs: { run_id: REPLAY_RUN_ID },
                    },
                    metadata: { caller: "lead_agent" },
                    created_at: "2026-06-18T13:02:00Z",
                  },
                ],
                has_more: false,
              },
            ],
            task_lanes: [
              {
                thread_id: MOCK_THREAD_ID,
                run_id: COMPLETED_RUN_ID,
                task_id: COMPLETED_TASK_ID,
                status: "completed",
                subagent_type: "general-purpose",
                description: COMPLETED_TASK_DESCRIPTION,
                prompt: COMPLETED_TASK_PROMPT,
                result: COMPLETED_TASK_RESULT,
                created_at: "2026-06-18T13:00:01Z",
                updated_at: "2026-06-18T13:01:00Z",
              },
            ],
          },
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);

    await expect(page.getByText("Subtask completed")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /Subtask completed/ }).click();
    await expect(page.getByText(COMPLETED_TASK_RESULT)).toBeVisible();
    await expect(page.getByText("Running subtask")).toHaveCount(0);
  });
});
