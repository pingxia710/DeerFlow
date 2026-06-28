import { expect, test } from "@rstest/core";

import {
  getCallerTokenUsageRows,
  getLeadAgentTokenUsage,
  threadTokenUsageToTokenUsage,
} from "@/core/threads/token-usage";
import type { ThreadTokenUsageResponse } from "@/core/threads/types";

test("maps backend thread token usage to UI token usage", () => {
  const response: ThreadTokenUsageResponse = {
    thread_id: "thread-1",
    total_input_tokens: 90,
    total_output_tokens: 60,
    total_tokens: 150,
    total_runs: 2,
    by_model: { unknown: { tokens: 150, runs: 2 } },
    by_caller: {
      lead_agent: 120,
      subagent: 25,
      middleware: 5,
    },
  };

  expect(threadTokenUsageToTokenUsage(response)).toEqual({
    inputTokens: 90,
    outputTokens: 60,
    totalTokens: 150,
  });
});

test("returns null when backend thread token usage is unavailable", () => {
  expect(threadTokenUsageToTokenUsage(null)).toBeNull();
  expect(threadTokenUsageToTokenUsage(undefined)).toBeNull();
});

test("keeps caller token usage rows in display order and skips empty buckets", () => {
  expect(
    getCallerTokenUsageRows({
      lead_agent: 120,
      subagent: 25,
      middleware: 0,
    }),
  ).toEqual([
    { key: "lead_agent", tokens: 120 },
    { key: "subagent", tokens: 25 },
  ]);
  expect(getCallerTokenUsageRows(null)).toEqual([]);
});

test("returns lead agent token usage for the header when available", () => {
  expect(
    getLeadAgentTokenUsage({
      lead_agent: 120,
      subagent: 25,
      middleware: 0,
    }),
  ).toBe(120);
  expect(
    getLeadAgentTokenUsage({
      lead_agent: 0,
      subagent: 25,
      middleware: 0,
    }),
  ).toBeNull();
});
