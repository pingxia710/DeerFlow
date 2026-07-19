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
const COMPLETED_TASK_PREVIEW = "Completed terminal result preview.";
const COMPLETED_TASK_RESULT =
  "Completed terminal result should stay visible without truncation.";
const FAILED_RUN_ID = `run-failed-${MOCK_THREAD_ID}`;
const FAILED_TASK_ID = "call-failed-subtask";
const FAILED_TASK_DESCRIPTION = "Failed subtask recovery smoke";
const FAILED_TASK_PROMPT = "Fail this subtask and preserve the terminal error.";
const FAILED_TASK_ERROR =
  "Task failed. Error: Child transport stopped after retry.";
const WAKE_FAILED_RUN_ID = `run-wake-failed-${MOCK_THREAD_ID}`;
const WAKE_FAILED_ROUND_ID = "round-wake-failed";
const WAKE_FAILED_TASK_ID = "call-wake-failed-subtask";
const WAKE_FAILED_TASK_DESCRIPTION = "Completed child with failed Chair wake";

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
                data: [
                  ...completedSubtaskMessages.map((message, index) => ({
                    run_id: COMPLETED_RUN_ID,
                    seq: index + 1,
                    content: message,
                    metadata: { caller: "lead_agent" },
                    created_at: `2026-06-18T13:00:0${index}Z`,
                  })),
                  {
                    run_id: COMPLETED_RUN_ID,
                    seq: 3,
                    content: {
                      type: "tool",
                      name: "task",
                      tool_call_id: COMPLETED_TASK_ID,
                      content: COMPLETED_TASK_RESULT,
                      additional_kwargs: {
                        subagent_status: "completed",
                      },
                    },
                    metadata: { caller: "lead_agent" },
                    created_at: "2026-06-18T13:01:00Z",
                  },
                ],
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
                result: COMPLETED_TASK_PREVIEW,
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
    await expect(page.getByText(COMPLETED_TASK_RESULT)).toHaveCount(1);
    await expect(page.getByText(COMPLETED_TASK_RESULT)).toBeVisible();
    await expect(page.getByText("Running subtask")).toHaveCount(0);
  });

  test("restores the full failed terminal subtask result after reload", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Failed subtask smoke",
          updated_at: "2026-07-16T10:00:00Z",
          messages: completedSubtaskMessages,
          runtimeSnapshot: {
            runs: [
              {
                run_id: FAILED_RUN_ID,
                thread_id: MOCK_THREAD_ID,
                assistant_id: "lead_agent",
                status: "success",
                metadata: {},
                kwargs: {},
                created_at: "2026-07-16T10:00:00Z",
                updated_at: "2026-07-16T10:01:00Z",
              },
            ],
            run_messages: [
              {
                run_id: FAILED_RUN_ID,
                data: [
                  {
                    run_id: FAILED_RUN_ID,
                    seq: 1,
                    content: {
                      ...completedSubtaskMessages[1],
                      id: "msg-ai-failed-subtask",
                      additional_kwargs: { run_id: FAILED_RUN_ID },
                      tool_calls: [
                        {
                          id: FAILED_TASK_ID,
                          name: "task",
                          args: {
                            subagent_type: "general-purpose",
                            description: FAILED_TASK_DESCRIPTION,
                            prompt: FAILED_TASK_PROMPT,
                          },
                          type: "tool_call",
                        },
                      ],
                    },
                    metadata: { caller: "lead_agent" },
                    created_at: "2026-07-16T10:00:00Z",
                  },
                  {
                    run_id: FAILED_RUN_ID,
                    seq: 2,
                    content: {
                      type: "tool",
                      name: "task",
                      tool_call_id: FAILED_TASK_ID,
                      content: FAILED_TASK_ERROR,
                      additional_kwargs: { subagent_status: "failed" },
                    },
                    metadata: { caller: "lead_agent" },
                    created_at: "2026-07-16T10:01:00Z",
                  },
                ],
                has_more: false,
              },
            ],
            task_lanes: [
              {
                thread_id: MOCK_THREAD_ID,
                run_id: FAILED_RUN_ID,
                task_id: FAILED_TASK_ID,
                status: "failed",
                subagent_type: "general-purpose",
                description: FAILED_TASK_DESCRIPTION,
                prompt: FAILED_TASK_PROMPT,
                error: "Task failed",
                created_at: "2026-07-16T10:00:01Z",
                updated_at: "2026-07-16T10:01:00Z",
              },
            ],
          },
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await page.reload();

    await expect(page.getByText("Subtask failed")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /Subtask failed/ }).click();
    await expect(
      page.getByText("Error: Child transport stopped after retry."),
    ).toHaveCount(1);
    await expect(page.getByText("Running subtask")).toHaveCount(0);
  });

  test("shows a failed Chair wake without changing the completed child result", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Failed Chair wake",
          agent_name: "command-room",
          messages: completedSubtaskMessages,
          runtimeSnapshot: {
            runs: [
              {
                run_id: WAKE_FAILED_RUN_ID,
                thread_id: MOCK_THREAD_ID,
                assistant_id: "command-room",
                status: "success",
                metadata: { round_id: WAKE_FAILED_ROUND_ID },
                kwargs: {},
              },
            ],
            rounds: [
              {
                round_id: WAKE_FAILED_ROUND_ID,
                thread_id: MOCK_THREAD_ID,
                current_run_id: WAKE_FAILED_RUN_ID,
              },
            ],
            run_messages: [
              {
                run_id: WAKE_FAILED_RUN_ID,
                data: [
                  {
                    run_id: WAKE_FAILED_RUN_ID,
                    seq: 1,
                    content: {
                      ...completedSubtaskMessages[1],
                      additional_kwargs: {
                        run_id: WAKE_FAILED_RUN_ID,
                        round_id: WAKE_FAILED_ROUND_ID,
                      },
                      tool_calls: [
                        {
                          id: WAKE_FAILED_TASK_ID,
                          name: "task",
                          args: {
                            subagent_type: "general-purpose",
                            description: WAKE_FAILED_TASK_DESCRIPTION,
                            prompt: COMPLETED_TASK_PROMPT,
                          },
                          type: "tool_call",
                        },
                      ],
                    },
                    metadata: { caller: "lead_agent" },
                  },
                  {
                    run_id: WAKE_FAILED_RUN_ID,
                    seq: 2,
                    content: {
                      type: "tool",
                      name: "task",
                      tool_call_id: WAKE_FAILED_TASK_ID,
                      content: COMPLETED_TASK_RESULT,
                      additional_kwargs: { subagent_status: "completed" },
                    },
                    metadata: { caller: "lead_agent" },
                  },
                ],
                has_more: false,
              },
            ],
            task_lanes: [
              {
                thread_id: MOCK_THREAD_ID,
                run_id: WAKE_FAILED_RUN_ID,
                round_id: WAKE_FAILED_ROUND_ID,
                task_id: WAKE_FAILED_TASK_ID,
                status: "completed",
                description: WAKE_FAILED_TASK_DESCRIPTION,
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
            run_id: WAKE_FAILED_RUN_ID,
            round_id: WAKE_FAILED_ROUND_ID,
            items: [
              {
                task_id: WAKE_FAILED_TASK_ID,
                source_run_id: WAKE_FAILED_RUN_ID,
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
    await page.route(
      new RegExp(
        `/api/threads/${MOCK_THREAD_ID}/runs/${WAKE_FAILED_RUN_ID}/messages(?:\\?.*)?$`,
      ),
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: [
              {
                run_id: WAKE_FAILED_RUN_ID,
                seq: 1,
                content: {
                  type: "tool",
                  name: "task",
                  tool_call_id: WAKE_FAILED_TASK_ID,
                  content: COMPLETED_TASK_RESULT,
                  additional_kwargs: { subagent_status: "completed" },
                },
              },
            ],
            has_more: false,
          }),
        }),
    );

    await page.goto(`/workspace/agents/command-room/chats/${MOCK_THREAD_ID}`);

    const taskCard = page
      .locator("[data-command-room-task]")
      .filter({ hasText: WAKE_FAILED_TASK_DESCRIPTION });
    await expect(taskCard.getByText("Subtask completed")).toBeVisible({
      timeout: 15_000,
    });
    await taskCard.getByRole("button", { name: /Subtask completed/ }).click();
    const notice = taskCard.getByRole("alert");
    await expect(notice).toContainText("Child task completed");
    await expect(notice).toContainText("3 attempts");
    await expect(notice).toContainText("does not mean the project is complete");
    await expect(notice).not.toContainText("http_503");
    await expect(page.getByText("Subtask failed")).toHaveCount(0);
    await expect(taskCard.getByText(COMPLETED_TASK_RESULT)).toBeVisible();
  });
});
