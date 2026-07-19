import { expect, test, type BrowserContext } from "@playwright/test";

const gateway = process.env.E3_GATEWAY_URL!;
const scenario = process.env.E3_SCENARIO;

async function register(context: BrowserContext) {
  const response = await context.request.post(
    `${gateway}/api/v1/auth/register`,
    {
      data: {
        email: `e3-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`,
        password: "e3-temporary-local-password",
      },
    },
  );
  expect(response.status(), await response.text()).toBe(201);
}

async function csrfHeaders(context: BrowserContext) {
  const token = (await context.cookies(gateway)).find(
    (cookie) => cookie.name === "csrf_token",
  )?.value;
  expect(token).toBeTruthy();
  return { "X-CSRF-Token": token! };
}

async function waitFor<T>(
  read: () => Promise<T>,
  ready: (value: T) => boolean,
): Promise<T> {
  let last: T | undefined;
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const value = await read();
    last = value;
    if (ready(value)) return value;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`E3 condition did not become ready: ${JSON.stringify(last)}`);
}

test("WF exposes only completed-child plus failed-wake public facts", async ({
  browser,
  context,
  page,
}) => {
  test.skip(scenario !== "wf", "controller selected E3-R");
  await register(context);
  const created = await context.request.post(
    `${gateway}/api/test-only/e3/wf/terminal-job`,
    {
      headers: await csrfHeaders(context),
    },
  );
  expect(created.status(), await created.text()).toBe(200);
  const fixture = await created.json();
  const status = await waitFor(
    async () => {
      const response = await context.request.get(
        `${gateway}/api/test-only/e3/wf/status/${fixture.nonce}`,
      );
      expect(response.status(), await response.text()).toBe(200);
      return response.json();
    },
    (value) =>
      value.wake_failed === true &&
      value.wake_attempts === 3 &&
      value.wake_start_calls === 3 &&
      typeof value.round_id === "string",
  );
  const factsUrl = `${gateway}/api/threads/${fixture.thread_id}/command-room/wake-facts?run_id=${fixture.run_id}&round_id=${status.round_id}`;
  const facts = await context.request.get(factsUrl);
  expect(facts.status(), await facts.text()).toBe(200);
  const payload = await facts.json();
  expect(payload.items).toHaveLength(1);
  expect(payload.items[0]).toMatchObject({
    task_id: fixture.task_id,
    source_run_id: fixture.run_id,
    child_status: "completed",
    wake_state: "failed",
    wake_attempts: 3,
    wake_failure_reason: "retry_exhausted",
  });
  expect(JSON.stringify(payload)).not.toContain("http_503");
  expect(JSON.stringify(payload)).not.toMatch(
    /handoff|last_status|prompt|result|error|claim_id/i,
  );

  const snapshot = page.waitForResponse(
    (response) =>
      response
        .url()
        .includes(`/api/threads/${fixture.thread_id}/runtime-snapshot`) &&
      response.status() === 200,
  );
  await page.goto(`/workspace/chats/${fixture.thread_id}`);
  await snapshot;
  await page.reload();
  await page.getByLabel("Open activity").click();
  const alert = page
    .getByRole("complementary", { name: "Activity" })
    .getByRole("alert");
  await expect(alert).toContainText(/child task completed/i);
  await expect(alert).toContainText(/3 attempts/i);
  await expect(alert).toContainText(/does not mean.*project.*complete/i);
  await expect(page.locator("body")).not.toContainText("http_503");
  await expect(page.getByRole("button", { name: /retry/i })).toHaveCount(0);

  const invalid = await context.request.get(
    `${gateway}/api/threads/${fixture.thread_id}/command-room/wake-facts?run_id=${fixture.run_id}`,
  );
  expect(invalid.status(), await invalid.text()).toBe(200);
  expect((await invalid.json()).items).toEqual([]);
  await context.request.post(
    `${gateway}/api/test-only/e3/wf/arm-zero-write/${fixture.nonce}`,
    {
      headers: await csrfHeaders(context),
    },
  );
  expect((await context.request.get(factsUrl)).status()).toBe(200);
  const zeroWrite = await context.request.get(
    `${gateway}/api/test-only/e3/wf/zero-write/${fixture.nonce}`,
  );
  expect(await zeroWrite.json()).toEqual({
    mutator_calls: 0,
    lane_digest_unchanged: true,
  });

  const other = await browser.newContext();
  await register(other);
  const foreign = await other.request.get(factsUrl);
  expect(foreign.status(), await foreign.text()).toBe(404);
  await other.close();
});

test("R reloads the single recovered wake from persisted state", async ({
  page,
}) => {
  test.skip(scenario !== "r", "controller selected E3-WF");
  const threadId = process.env.E3_R_THREAD_ID;
  const nonce = process.env.DEERFLOW_E3_NONCE;
  expect(threadId).toBeTruthy();
  expect(nonce).toBeTruthy();
  const snapshot = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/threads/${threadId}/runtime-snapshot`) &&
      response.status() === 200,
  );
  await page.goto(`/workspace/chats/${threadId}`);
  await snapshot;
  await expect(
    page.getByText(`E3_WAKE_ACK_${nonce}`, { exact: false }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText(`E3_WAKE_ACK_${nonce}`, { exact: false }),
  ).toBeVisible();
});
