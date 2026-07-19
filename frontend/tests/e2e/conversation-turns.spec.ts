import { expect, test } from "@playwright/test";

import {
  handleRunStream,
  mockLangGraphAPI,
  MOCK_THREAD_ID,
} from "./utils/mock-api";

const TURN_MESSAGES = [
  {
    id: "turn-1-human",
    type: "human",
    content: [{ type: "text", text: "First turn question" }],
    additional_kwargs: {
      deerflow_run_id: "run-turn-1",
      history_created_at: "2026-07-12T06:22:00.000Z",
    },
  },
  {
    id: "turn-1-ai",
    type: "ai",
    content: "First turn answer",
    additional_kwargs: {
      deerflow_run_id: "run-turn-1",
      history_created_at: "2026-07-12T06:22:18.000Z",
      turn_duration: 18,
    },
  },
  {
    id: "turn-2-human",
    type: "human",
    content: [{ type: "text", text: "Second turn question" }],
    additional_kwargs: {
      deerflow_run_id: "run-turn-2",
      history_created_at: "2026-07-12T06:25:00.000Z",
    },
  },
  {
    id: "turn-2-ai",
    type: "ai",
    content: "Second turn answer",
    additional_kwargs: {
      deerflow_run_id: "run-turn-2",
      history_created_at: "2026-07-12T06:26:07.000Z",
      turn_duration: 67,
    },
  },
];

const LONG_HISTORY_MESSAGES = [
  {
    id: "history-human-1",
    type: "human",
    content: [{ type: "text", text: "History marker" }],
  },
  {
    id: "history-ai-1",
    type: "ai",
    content: Array.from(
      { length: 80 },
      (_, index) => `History paragraph ${index + 1}.`,
    ).join("\n\n"),
  },
  {
    id: "history-human-2",
    type: "human",
    content: [{ type: "text", text: "Second history question" }],
  },
  {
    id: "history-ai-2",
    type: "ai",
    content: "Second history answer",
  },
];

const CURRENT_RUN_MESSAGES = [
  {
    id: "stream-human-current",
    type: "human",
    content: [{ type: "text", text: "Current request" }],
  },
  {
    id: "stream-ai-current",
    type: "ai",
    content: "Hello from DeerFlow!",
  },
];

test.describe("Conversation turns", () => {
  test("groups each user request with its completed answer", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Turn history",
          messages: TURN_MESSAGES,
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("Second turn answer")).toBeVisible({
      timeout: 15_000,
    });

    const turns = page.locator("[data-conversation-turn]");
    await expect(turns).toHaveCount(2);
    await expect(turns.nth(0)).toContainText(/round 1/i);
    await expect(turns.nth(1)).toContainText(/completed at/i);
    await expect(turns.nth(1)).toContainText(/1m 7s/i);
  });

  test("opens history at the live edge", async ({ page }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Long history",
          messages: LONG_HISTORY_MESSAGES,
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    const conversationContent = page.locator("[data-conversation-content]");
    const scrollRoot = conversationContent.locator("..");
    await expect
      .poll(() =>
        scrollRoot.evaluate(
          (element) => element.scrollHeight > element.clientHeight,
        ),
      )
      .toBeTruthy();
    await expect
      .poll(() =>
        scrollRoot.evaluate(
          (element) =>
            element.scrollHeight - element.clientHeight - element.scrollTop <=
            70,
        ),
      )
      .toBeTruthy();
  });

  test("keeps a reader above the live edge until they return to the reply", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Long history",
          messages: LONG_HISTORY_MESSAGES,
        },
      ],
    });

    let releaseResponse: (() => void) | undefined;
    const responseReady = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });
    await page.route("**/runs/stream", async (route) => {
      await responseReady;
      return handleRunStream(route, { messages: CURRENT_RUN_MESSAGES });
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("Second history answer")).toBeVisible({
      timeout: 15_000,
    });

    const conversationContent = page.locator("[data-conversation-content]");
    const scrollRoot = conversationContent.locator("..");
    await expect
      .poll(() =>
        scrollRoot.evaluate(
          (element) => element.scrollHeight > element.clientHeight,
        ),
      )
      .toBeTruthy();

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await textarea.fill("Current request");
    await textarea.press("Enter");
    await expect(page.getByText("Current request")).toBeVisible();
    await expect.poll(() => Boolean(releaseResponse)).toBeTruthy();
    await expect(page.locator("[data-conversation-turn]").last()).toContainText(
      /in progress.*0m \d+s/i,
    );
    await expect(page.locator("[data-conversation-turn]").last()).toContainText(
      /in progress.*0m [1-9]\d*s/i,
      { timeout: 3_000 },
    );
    await expect
      .poll(() =>
        scrollRoot.evaluate(
          (element) =>
            element.scrollHeight - element.clientHeight - element.scrollTop <=
            1,
        ),
      )
      .toBeTruthy();

    await scrollRoot.evaluate((element) => {
      element.scrollTop = 0;
      element.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
    const anchor = page.getByText("History marker");
    const beforeTop = await anchor.evaluate(
      (element) => element.getBoundingClientRect().top,
    );

    releaseResponse?.();
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    await expect(
      page.getByRole("button", { name: /return to current reply/i }),
    ).toBeVisible();
    await expect
      .poll(async () => {
        const afterTop = await anchor.evaluate(
          (element) => element.getBoundingClientRect().top,
        );
        return Math.abs(afterTop - beforeTop);
      })
      .toBeLessThanOrEqual(2);
    await page
      .getByRole("button", { name: /return to current reply/i })
      .click();
    await expect
      .poll(() =>
        scrollRoot.evaluate(
          (element) =>
            element.scrollHeight - element.clientHeight - element.scrollTop <=
            96,
        ),
      )
      .toBeTruthy();
  });

  test("follows a content observer resize callback and disconnects it on unmount", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      const state = {
        contentObservers: [] as Array<{
          active: boolean;
          callbackTriggers: number;
          disconnects: number;
          installs: number;
        }>,
        maxActiveContentObservers: 0,
        triggerContentResize: () => undefined,
      };
      Object.defineProperty(window, "__conversationResizeObserverState", {
        value: state,
      });

      const recordActiveContentObservers = () => {
        state.maxActiveContentObservers = Math.max(
          state.maxActiveContentObservers,
          state.contentObservers.filter((observer) => observer.active).length,
        );
      };

      class TrackingResizeObserver implements ResizeObserver {
        private readonly callback: ResizeObserverCallback;
        private readonly contentObserver = {
          active: false,
          callbackTriggers: 0,
          disconnects: 0,
          installs: 0,
        };
        private contentTarget: Element | null = null;
        private observesConversationContent = false;

        constructor(callback: ResizeObserverCallback) {
          this.callback = callback;
        }

        disconnect() {
          if (this.observesConversationContent) {
            this.contentObserver.active = false;
            this.contentObserver.disconnects += 1;
            recordActiveContentObservers();
          }
          this.contentTarget = null;
        }

        observe(target: Element) {
          if (target.matches("[data-conversation-content]")) {
            if (!this.observesConversationContent) {
              this.observesConversationContent = true;
              state.contentObservers.push(this.contentObserver);
            }
            this.contentObserver.active = true;
            this.contentObserver.installs += 1;
            this.contentTarget = target;
            recordActiveContentObservers();
            state.triggerContentResize = () => {
              if (!this.contentTarget || !this.contentObserver.active) {
                return;
              }
              this.contentObserver.callbackTriggers += 1;
              this.callback(
                [
                  {
                    target: this.contentTarget,
                    contentRect: this.contentTarget.getBoundingClientRect(),
                  } as ResizeObserverEntry,
                ],
                this,
              );
            };
          }
        }

        takeRecords() {
          return [];
        }

        unobserve(target: Element) {
          if (target === this.contentTarget) {
            this.contentTarget = null;
          }
        }
      }

      window.ResizeObserver = TrackingResizeObserver;
    });
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Observed conversation",
          messages: TURN_MESSAGES,
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("Second turn answer")).toBeVisible({
      timeout: 15_000,
    });
    await expect
      .poll(() =>
        page.evaluate(() => {
          const state = Reflect.get(
            window,
            "__conversationResizeObserverState",
          ) as {
            contentObservers: Array<{ active: boolean; installs: number }>;
            maxActiveContentObservers: number;
          };
          return {
            activeContentObservers: state.contentObservers.filter(
              (observer) => observer.active,
            ).length,
            everyObserverInstalledOnce: state.contentObservers.every(
              (observer) => observer.installs === 1,
            ),
            maxActiveContentObservers: state.maxActiveContentObservers,
          };
        }),
      )
      .toEqual({
        activeContentObservers: 1,
        everyObserverInstalledOnce: true,
        maxActiveContentObservers: 1,
      });

    const conversationContent = page.locator("[data-conversation-content]");
    const scrollRoot = conversationContent.locator("..");
    await page.evaluate(() => {
      const content = document.querySelector<HTMLElement>(
        "[data-conversation-content]",
      );
      const state = Reflect.get(
        window,
        "__conversationResizeObserverState",
      ) as {
        triggerContentResize: () => void;
      };
      if (!content) {
        throw new Error("Conversation content did not mount");
      }

      state.triggerContentResize();
      content.style.minHeight = `${content.getBoundingClientRect().height + 2_000}px`;
      state.triggerContentResize();
    });
    await expect
      .poll(() =>
        page.evaluate(() => {
          const state = Reflect.get(
            window,
            "__conversationResizeObserverState",
          ) as {
            contentObservers: Array<{
              active: boolean;
              callbackTriggers: number;
            }>;
          };
          return {
            activeContentObservers: state.contentObservers.filter(
              (observer) => observer.active,
            ).length,
            callbackTriggers: state.contentObservers.reduce(
              (total, observer) => total + observer.callbackTriggers,
              0,
            ),
          };
        }),
      )
      .toEqual({ activeContentObservers: 1, callbackTriggers: 2 });
    await expect
      .poll(() =>
        scrollRoot.evaluate(
          (element) =>
            element.scrollHeight - element.clientHeight - element.scrollTop <=
            1,
        ),
      )
      .toBeTruthy();
    await expect
      .poll(() =>
        page.evaluate(() => {
          const state = Reflect.get(
            window,
            "__conversationResizeObserverState",
          ) as {
            contentObservers: Array<{ active: boolean }>;
          };
          return state.contentObservers.filter((observer) => observer.active)
            .length;
        }),
      )
      .toBe(1);

    await page
      .locator("[data-sidebar='sidebar'] a[href='/workspace/agents']")
      .click();
    await expect(page).toHaveURL(/\/workspace\/agents/);
    await expect
      .poll(() =>
        page.evaluate(() => {
          const state = Reflect.get(
            window,
            "__conversationResizeObserverState",
          ) as {
            contentObservers: Array<{
              active: boolean;
              disconnects: number;
              installs: number;
            }>;
            maxActiveContentObservers: number;
          };
          return {
            activeContentObservers: state.contentObservers.filter(
              (observer) => observer.active,
            ).length,
            everyObserverDisconnectedOnce: state.contentObservers.every(
              (observer) => observer.disconnects === 1,
            ),
            everyObserverInstalledOnce: state.contentObservers.every(
              (observer) => observer.installs === 1,
            ),
            maxActiveContentObservers: state.maxActiveContentObservers,
          };
        }),
      )
      .toEqual({
        activeContentObservers: 0,
        everyObserverDisconnectedOnce: true,
        everyObserverInstalledOnce: true,
        maxActiveContentObservers: 1,
      });
  });

  test("keeps the visible turn anchored when older run history is prepended", async ({
    page,
  }) => {
    const latestMessages = [
      {
        type: "human",
        content: [{ type: "text", text: "Newest history marker" }],
      },
      {
        type: "ai",
        content: Array.from(
          { length: 80 },
          (_, index) => `Newest history paragraph ${index + 1}.`,
        ).join("\n\n"),
      },
    ];
    const olderMessages = [
      {
        type: "human",
        content: [{ type: "text", text: "Older history marker" }],
      },
      {
        type: "ai",
        content: "Older history answer",
      },
    ];

    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Paged history",
          messages: [],
        },
      ],
    });

    await page.route(
      /\/api\/langgraph\/threads\/[^/]+\/runs(\?|$)/,
      (route) => {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              run_id: "run-latest",
              thread_id: MOCK_THREAD_ID,
              assistant_id: "lead_agent",
              status: "success",
              metadata: {},
              kwargs: {},
              created_at: "2026-07-12T06:00:00Z",
              updated_at: "2026-07-12T06:01:00Z",
            },
            {
              run_id: "run-older",
              thread_id: MOCK_THREAD_ID,
              assistant_id: "lead_agent",
              status: "success",
              metadata: {},
              kwargs: {},
              created_at: "2026-07-12T05:00:00Z",
              updated_at: "2026-07-12T05:01:00Z",
            },
          ]),
        });
      },
    );
    await page.route(
      /\/api\/threads\/([^/]+)\/runs\/([^/]+)\/messages/,
      (route) => {
        const requestUrl = new URL(route.request().url());
        const runId = requestUrl.pathname.split("/").at(-2);
        const isLatestRunContinuation =
          runId === "run-latest" && requestUrl.searchParams.has("before_seq");
        const messages = isLatestRunContinuation
          ? []
          : runId === "run-latest"
            ? latestMessages
            : olderMessages;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: messages.map((content, index) => ({
              run_id: runId,
              content,
              metadata: { caller: "lead_agent" },
              created_at: `2026-07-12T0${runId === "run-latest" ? 6 : 5}:00:${String(index).padStart(2, "0")}Z`,
              seq: index + 1,
            })),
            hasMore: runId === "run-latest" && !isLatestRunContinuation,
          }),
        });
      },
    );

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    const anchor = page.getByText("Newest history marker");
    await expect(anchor).toBeVisible({ timeout: 15_000 });
    const scrollRoot = page
      .locator("[data-conversation-content]")
      .locator("..");
    const loadMoreButton = page.getByRole("button", { name: /load more/i });
    await expect(loadMoreButton).toBeVisible({
      timeout: 15_000,
    });

    await scrollRoot.evaluate((element) => {
      element.scrollTop = 200;
      element.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
    const beforeTop = await anchor.evaluate(
      (element) => element.getBoundingClientRect().top,
    );

    await loadMoreButton.evaluate((button) => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await expect(page.getByText("Older history marker")).toBeVisible();
    await expect
      .poll(async () => {
        const afterTop = await anchor.evaluate(
          (element) => element.getBoundingClientRect().top,
        );
        return Math.abs(afterTop - beforeTop);
      })
      .toBeLessThanOrEqual(2);
  });
});
