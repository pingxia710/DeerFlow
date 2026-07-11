import type { Run } from "@langchain/langgraph-sdk";
import { afterEach, expect, rs, test } from "@rstest/core";

type ThreadRunsInfiniteOptions = {
  queryFn: (context: {
    pageParam: string | null;
    signal: AbortSignal;
  }) => Promise<Run[]>;
  getNextPageParam: (lastPage: Run[]) => string | undefined;
  select: (data: { pages: Run[][]; pageParams: Array<string | null> }) => Run[];
};

afterEach(() => {
  rs.restoreAllMocks();
  rs.doUnmock("@tanstack/react-query");
  rs.doUnmock("@/core/api");
  rs.doUnmock("@/core/api/fetcher");
  rs.doUnmock("@/core/config");
  rs.resetModules();
});

test("useThreadRuns loads runs beyond the 100-run snapshot with a before cursor", async () => {
  const firstPage = Array.from(
    { length: 100 },
    (_, index) => ({ run_id: `run-${index + 1}` }) as Run,
  );
  const olderPage = [{ run_id: "run-101" } as Run];
  const listRuns = rs.fn(async () => firstPage);
  const fetchRuns = rs.fn(
    async () =>
      new Response(JSON.stringify(olderPage), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
  );
  const useInfiniteQuery = rs.fn((options: ThreadRunsInfiniteOptions) => ({
    data: undefined,
    options,
  }));

  rs.resetModules();
  rs.doMock("@tanstack/react-query", () => ({
    useInfiniteQuery,
    useMutation: rs.fn(),
    useQuery: rs.fn(),
    useQueryClient: rs.fn(),
  }));
  rs.doMock("@/core/api", () => ({
    clearReconnectRun: rs.fn(),
    getAPIClient: () => ({ runs: { list: listRuns } }),
  }));
  rs.doMock("@/core/api/fetcher", () => ({
    DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS: 30_000,
    fetch: fetchRuns,
  }));
  rs.doMock("@/core/config", () => ({
    getBackendBaseURL: () => "",
  }));

  const { useThreadRuns } = await import("@/core/threads/hooks");
  useThreadRuns("thread-101");

  expect(useInfiniteQuery).toHaveBeenCalledTimes(1);
  const options = useInfiniteQuery.mock.calls[0]?.[0];
  expect(options).toBeDefined();
  const signal = new AbortController().signal;

  await expect(options!.queryFn({ pageParam: null, signal })).resolves.toEqual(
    firstPage,
  );
  expect(listRuns).toHaveBeenCalledWith("thread-101", {
    limit: 100,
    signal,
  });

  const before = options!.getNextPageParam(firstPage);
  expect(before).toBe("run-100");
  await expect(
    options!.queryFn({ pageParam: before!, signal }),
  ).resolves.toEqual(olderPage);
  expect(fetchRuns).toHaveBeenCalledWith(
    "/api/threads/thread-101/runs?limit=100&before=run-100",
    expect.objectContaining({
      credentials: "include",
      method: "GET",
      signal,
    }),
  );

  expect(
    options!.select({
      pages: [firstPage, olderPage],
      pageParams: [null, before!],
    }),
  ).toHaveLength(101);
});

test("cached next-page errors retry the failed cursor before refetching the first page", async () => {
  const hooks = await import("@/core/threads/hooks");
  const resolveLoadAction = Reflect.get(
    hooks,
    "resolveThreadRunsLoadAction",
  ) as unknown;

  expect(resolveLoadAction).toBeTypeOf("function");
  const resolve = resolveLoadAction as (input: {
    hasNextRunPage: boolean;
    hasRunsData: boolean;
    hasUnloadedRuns: boolean;
    nextPageIsError: boolean;
    runsIsError: boolean;
  }) => string;
  expect(
    resolve({
      hasNextRunPage: true,
      hasRunsData: true,
      hasUnloadedRuns: true,
      nextPageIsError: true,
      runsIsError: true,
    }),
  ).toBe("fetch-next-page");
  expect(
    resolve({
      hasNextRunPage: false,
      hasRunsData: false,
      hasUnloadedRuns: false,
      nextPageIsError: false,
      runsIsError: true,
    }),
  ).toBe("refetch-runs");
});

test("history requests reject stale thread generation and tombstoned results", async () => {
  const hooks = await import("@/core/threads/hooks");
  const isCurrentRequest = Reflect.get(
    hooks,
    "isCurrentThreadHistoryRequest",
  ) as unknown;

  expect(isCurrentRequest).toBeTypeOf("function");
  const isCurrent = isCurrentRequest as (input: {
    currentGeneration: number;
    currentThreadId: string;
    requestGeneration: number;
    requestThreadId: string;
    tombstoned: boolean;
  }) => boolean;
  const current = {
    currentGeneration: 4,
    currentThreadId: "thread-a",
    requestGeneration: 4,
    requestThreadId: "thread-a",
    tombstoned: false,
  };

  expect(isCurrent(current)).toBe(true);
  expect(isCurrent({ ...current, currentThreadId: "thread-b" })).toBe(false);
  expect(isCurrent({ ...current, currentGeneration: 5 })).toBe(false);
  expect(isCurrent({ ...current, tombstoned: true })).toBe(false);
});
