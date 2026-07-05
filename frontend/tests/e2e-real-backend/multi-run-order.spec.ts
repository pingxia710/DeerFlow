import { expect, test } from "@playwright/test";

/**
 * Layer 2 (cross-stack contract): reproduces upstream issue #3352 — after the
 * checkpoint no longer holds the older messages (post context-compression), the
 * frontend rebuilds thread history from the per-run endpoints, and the order it
 * rebuilds them in must stay chronological.
 *
 * The dangerous class this guards: a BACKEND change to run ordering silently
 * breaks a FRONTEND assumption. Backend `list_by_thread` returns runs
 * NEWEST-FIRST (PR #2932); the pre-#3354 frontend iterated runs from the end and
 * PREPENDED each loaded page (`core/threads/hooks.ts`), which inverts order. A
 * backend-only ordering test was green the whole time #3352 was live, and the
 * frontend regression unit test hardcodes "backend returns newest-first" in a
 * mock — so only a real frontend against a real backend catches the desync.
 *
 * This drives the REAL frontend against a REAL gateway with two seeded runs and
 * NO checkpoint (the seeder forces the per-run reload path to be the sole source
 * of truth), then asserts the first run's message renders ABOVE the second's.
 * No model, no recording, no API key — the runs are seeded via a test-only
 * endpoint mounted only on the replay gateway.
 */
const GATEWAY = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_BACKEND_GATEWAY_PORT ?? "8011"}`;

// Distinctive markers so getByText can't collide with UI chrome.
const ALPHA = "ALPHA-FIRST-QUESTION-7f3a2c";
const OMEGA = "OMEGA-SECOND-QUESTION-9b21d4";
const VISIBLE = "VISIBLE-LEAD-MESSAGE-08bc11";
const MIDDLEWARE = "HIDDEN-MIDDLEWARE-TITLE-21d9fb";
const TOOL = "HIDDEN-TOOL-OUTPUT-46fbc2";
const TERMINAL_PROMPT = "TERMINAL-RUN-PROMPT-66d91e";
const TASK_LANE_DESCRIPTION = "SNAPSHOT-TASK-LANE-DESCRIPTION-1c43";
const TASK_LANE_PROMPT = "SNAPSHOT-TASK-LANE-PROMPT-7a29";

test.describe("multi-run thread renders chronologically (replay, no API key)", () => {
  test("first run renders above second run after history rebuild (#3352)", async ({
    page,
    context,
  }) => {
    const uniq = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
    const threadId = `e2e-multi-run-${uniq}`;

    // Seed two runs in one thread: run-1 (ALPHA) older, run-2 (OMEGA) newer, so
    // the real backend's list_by_thread returns them newest-first. No checkpoint
    // is seeded — that is the #3352 precondition.
    const seed = await context.request.post(
      `${GATEWAY}/api/test-only/seed-runs`,
      {
        data: {
          thread_id: threadId,
          runs: [
            {
              run_id: `${threadId}-r1`,
              created_at: "2026-01-01T00:00:00+00:00",
              messages: [
                { role: "human", content: ALPHA, id: `${threadId}-a-h` },
                { role: "ai", content: "ALPHA reply", id: `${threadId}-a-a` },
              ],
            },
            {
              run_id: `${threadId}-r2`,
              created_at: "2026-01-01T00:01:00+00:00",
              messages: [
                { role: "human", content: OMEGA, id: `${threadId}-o-h` },
                { role: "ai", content: "OMEGA reply", id: `${threadId}-o-a` },
              ],
            },
          ],
        },
      },
    );
    expect(seed.status(), await seed.text()).toBe(200);

    // Load the thread fresh — triggers the runtime snapshot recovery path.
    const snapshot = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/threads/${threadId}/runtime-snapshot`) &&
        response.status() === 200,
    );
    await page.goto(`/workspace/chats/${threadId}`);
    await snapshot;

    const alpha = page.getByText(ALPHA, { exact: false });
    const omega = page.getByText(OMEGA, { exact: false });
    await expect(alpha).toBeVisible({ timeout: 60_000 });
    await expect(omega).toBeVisible({ timeout: 30_000 });
    // Each marker renders exactly once (guards against accidental duplicate matches).
    expect(await alpha.count(), "ALPHA should render exactly once").toBe(1);
    expect(await omega.count(), "OMEGA should render exactly once").toBe(1);

    // The contract: ALPHA (first run) must render ABOVE OMEGA (second run). With
    // the #3352 bug the per-run rebuild inverts this and OMEGA renders first.
    const alphaBox = await alpha.first().boundingBox();
    const omegaBox = await omega.first().boundingBox();
    expect(alphaBox, "ALPHA must have a layout box").toBeTruthy();
    expect(omegaBox, "OMEGA must have a layout box").toBeTruthy();
    expect(
      alphaBox!.y,
      `chronological order broken: ALPHA(first run) rendered at y=${alphaBox!.y}, OMEGA(second run) at y=${omegaBox!.y} — backend list_by_thread ordering and frontend history rebuild are out of sync (#3352)`,
    ).toBeLessThan(omegaBox!.y);
  });

  test("internal run rows stay hidden from chat after history rebuild", async ({
    page,
    context,
  }) => {
    const uniq = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
    const threadId = `e2e-hidden-run-rows-${uniq}`;

    const seed = await context.request.post(
      `${GATEWAY}/api/test-only/seed-runs`,
      {
        data: {
          thread_id: threadId,
          runs: [
            {
              run_id: `${threadId}-r1`,
              created_at: "2026-01-01T00:00:00+00:00",
              messages: [
                {
                  role: "human",
                  content: VISIBLE,
                  id: `${threadId}-visible-human`,
                },
                {
                  role: "ai",
                  content: MIDDLEWARE,
                  id: `${threadId}-middleware-ai`,
                  caller: "middleware:title",
                },
                {
                  role: "tool",
                  content: TOOL,
                  id: `${threadId}-tool`,
                  caller: "task",
                  tool_call_id: `${threadId}-tool-call`,
                },
                {
                  role: "ai",
                  content: "visible reply",
                  id: `${threadId}-visible-ai`,
                },
              ],
            },
          ],
        },
      },
    );
    expect(seed.status(), await seed.text()).toBe(200);

    const snapshot = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/threads/${threadId}/runtime-snapshot`) &&
        response.status() === 200,
    );
    await page.goto(`/workspace/chats/${threadId}`);
    await snapshot;

    await expect(page.getByText(VISIBLE, { exact: false })).toBeVisible({
      timeout: 60_000,
    });
    expect(await page.getByText(MIDDLEWARE, { exact: false }).count()).toBe(0);
    expect(await page.getByText(TOOL, { exact: false }).count()).toBe(0);
  });

  test("terminal run without final reply shows a recovery status", async ({
    page,
    context,
  }) => {
    const uniq = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
    const threadId = `e2e-terminal-run-notice-${uniq}`;

    const seed = await context.request.post(
      `${GATEWAY}/api/test-only/seed-runs`,
      {
        data: {
          thread_id: threadId,
          runs: [
            {
              run_id: `${threadId}-r1`,
              created_at: "2026-01-01T00:00:00+00:00",
              status: "error",
              terminal_reason: "worker_lost",
              messages: [
                {
                  role: "human",
                  content: TERMINAL_PROMPT,
                  id: `${threadId}-terminal-human`,
                },
              ],
            },
          ],
        },
      },
    );
    expect(seed.status(), await seed.text()).toBe(200);

    const snapshot = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/threads/${threadId}/runtime-snapshot`) &&
        response.status() === 200,
    );
    await page.goto(`/workspace/chats/${threadId}`);
    await snapshot;

    await expect(page.getByText(TERMINAL_PROMPT, { exact: false })).toBeVisible(
      {
        timeout: 60_000,
      },
    );
    await expect(page.getByTestId("run-terminal-notice")).toBeVisible();
    await expect(
      page.getByText("Run ended without a visible reply"),
    ).toBeVisible();
    await expect(page.getByText("worker_lost", { exact: false })).toBeVisible();
  });

  test("runtime snapshot task lanes restore subtask terminal state", async ({
    page,
    context,
  }) => {
    const uniq = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
    const threadId = `e2e-task-lane-snapshot-${uniq}`;
    const runId = `${threadId}-r1`;
    const taskId = `${threadId}-task`;

    const seed = await context.request.post(
      `${GATEWAY}/api/test-only/seed-runs`,
      {
        data: {
          thread_id: threadId,
          runs: [
            {
              run_id: runId,
              created_at: "2026-01-01T00:00:00+00:00",
              messages: [
                {
                  role: "human",
                  content: "Start a snapshot-restored task lane.",
                  id: `${threadId}-human`,
                },
                {
                  role: "ai",
                  content: "Starting task lane replay.",
                  id: `${threadId}-ai-task`,
                  tool_calls: [
                    {
                      id: taskId,
                      name: "task",
                      args: {
                        subagent_type: "evidence",
                        description: TASK_LANE_DESCRIPTION,
                        prompt: TASK_LANE_PROMPT,
                      },
                      type: "tool_call",
                    },
                  ],
                },
              ],
            },
          ],
          task_lanes: [
            {
              run_id: runId,
              task_id: taskId,
              role: "evidence",
              status: "completed",
              result_ref: "artifact://snapshot-task-result",
            },
          ],
        },
      },
    );
    expect(seed.status(), await seed.text()).toBe(200);

    const snapshot = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/threads/${threadId}/runtime-snapshot`) &&
        response.status() === 200,
    );
    await page.goto(`/workspace/chats/${threadId}`);
    await snapshot;

    await expect(page.getByText(TASK_LANE_DESCRIPTION)).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByText("Subtask completed")).toBeVisible();
    await expect(page.getByText("Running subtask")).toHaveCount(0);
  });
});
