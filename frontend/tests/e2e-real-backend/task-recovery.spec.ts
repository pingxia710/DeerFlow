import { expect, test } from "@playwright/test";

const GATEWAY = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_BACKEND_GATEWAY_PORT ?? "8011"}`;
const APP = `http://localhost:${process.env.PLAYWRIGHT_REAL_BACKEND_FRONTEND_PORT ?? "3100"}`;

const TASK_PROMPT = "E2E_TASK_SCENARIO: write deterministic runtime evidence.";
const TASK_DESCRIPTION = "E2E task writes runtime evidence";
const TASK_RESULT = "E2E subagent evidence written.";
const FINAL_ANSWER = "E2E task scenario final answer.";
const ISOLATED_THREAD_MARKER = "E2E-ISOLATED-THREAD-MESSAGE-4ad21c";

type RuntimeSnapshot = {
  task_lanes: Array<{
    task_id: string;
    status: string;
    result?: string | null;
  }>;
  run_messages: Array<{
    data: Array<{
      content?: {
        type?: string;
        name?: string;
        content?: string;
        additional_kwargs?: Record<string, unknown>;
      };
    }>;
  }>;
};

test.describe("task execution restores after a thread switch (real backend)", () => {
  test("persists a completed AI-to-AI task across another conversation and reload", async ({
    page,
    context,
  }) => {
    const modelsResponse = await context.request.get(`${APP}/api/models`);
    expect(modelsResponse.status(), await modelsResponse.text()).toBe(200);
    const models = (await modelsResponse.json()) as {
      models: Array<{ name: string }>;
    };
    expect(models.models.map((model) => model.name)).toContain(
      "task-scenario-model",
    );

    await page.addInitScript(() => {
      window.localStorage.setItem(
        "deerflow.local-settings",
        JSON.stringify({
          context: {
            mode: "ultra",
            model_name: "task-scenario-model",
          },
        }),
      );
    });
    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 30_000 });
    await textarea.fill(TASK_PROMPT);
    await textarea.press("Enter");

    await expect(page.getByText(FINAL_ANSWER, { exact: false })).toBeVisible({
      timeout: 60_000,
    });
    await expect(page).toHaveURL(
      /\/workspace\/chats\/(?!new(?:[/?#]|$))[^/?#]+/,
    );
    const sourceThreadId = new URL(page.url()).pathname.split("/").at(-1)!;

    const isolatedThreadId = `e2e-isolated-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
    const seed = await context.request.post(
      `${GATEWAY}/api/test-only/seed-runs`,
      {
        data: {
          thread_id: isolatedThreadId,
          runs: [
            {
              run_id: `${isolatedThreadId}-run`,
              created_at: "2026-01-01T00:00:00+00:00",
              messages: [
                {
                  role: "human",
                  content: ISOLATED_THREAD_MARKER,
                  id: `${isolatedThreadId}-human`,
                },
                {
                  role: "ai",
                  content: "Isolated thread reply.",
                  id: `${isolatedThreadId}-ai`,
                },
              ],
            },
          ],
        },
      },
    );
    expect(seed.status(), await seed.text()).toBe(200);

    await page.goto(`/workspace/chats/${isolatedThreadId}`);
    await expect(
      page.getByText(ISOLATED_THREAD_MARKER, { exact: false }),
    ).toBeVisible({ timeout: 30_000 });

    await page.goto(`/workspace/chats/${sourceThreadId}`);
    await expect(page.getByText(FINAL_ANSWER, { exact: false })).toBeVisible({
      timeout: 30_000,
    });
    const snapshotResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/api/threads/${sourceThreadId}/runtime-snapshot`) &&
        response.status() === 200,
    );
    await page.reload();
    const snapshot = (await (await snapshotResponse).json()) as RuntimeSnapshot;
    const taskMessage = snapshot.run_messages
      .flatMap((page) => page.data)
      .map((message) => message.content)
      .find((message) => message?.type === "tool" && message.name === "task");

    expect(taskMessage?.additional_kwargs?.subagent_status).toBe("completed");
    expect(taskMessage?.content).toContain(TASK_RESULT);
    expect(snapshot.task_lanes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          task_id: "task-scenario-parent-call",
          status: "completed",
          result: TASK_RESULT,
        }),
      ]),
    );
    await expect(
      page.getByText(TASK_DESCRIPTION, { exact: false }),
    ).toBeVisible();
    await expect(page.getByText("Subtask completed")).toBeVisible();
    await page.getByRole("button", { name: /Subtask completed/ }).click();
    await expect(page.getByText(TASK_RESULT, { exact: false })).toBeVisible();
    await expect(page.getByText(FINAL_ANSWER, { exact: false })).toBeVisible();
  });
});
