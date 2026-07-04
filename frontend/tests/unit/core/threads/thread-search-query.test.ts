import { expect, test, rs } from "@rstest/core";

import {
  buildThreadsSearchQueryOptions,
  DEFAULT_THREAD_SEARCH_PARAMS,
} from "@/core/threads/thread-search-query";

test("thread search query does not poll or refetch on window focus", () => {
  const search = rs.fn();
  const options = buildThreadsSearchQueryOptions(
    { threads: { search } },
    DEFAULT_THREAD_SEARCH_PARAMS,
  );

  expect(options).not.toHaveProperty("refetchInterval");
  expect(options).not.toHaveProperty("refetchIntervalInBackground");
  expect(options.refetchOnWindowFocus).toBe(false);
});
