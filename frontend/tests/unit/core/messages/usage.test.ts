import type { Message } from "@langchain/langgraph-sdk";
import { expect, test } from "@rstest/core";

import {
  accumulateUsage,
  formatContextCount,
  formatTokenCount,
  selectHeaderTokenUsage,
} from "@/core/messages/usage";
import {
  getAssistantTurnUsageMessages,
  getMessageGroups,
} from "@/core/messages/utils";

test("accumulates each AI message usage only once by message id", () => {
  const aiMessage = {
    id: "ai-1",
    type: "ai",
    content: "Answer",
    usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
  } as Message;

  expect(accumulateUsage([aiMessage, aiMessage])).toEqual({
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
  });
});

test("counts later usage-bearing snapshots for the same AI message id", () => {
  const earlySnapshot = {
    id: "ai-1",
    type: "ai",
    content: "Streaming...",
  } as Message;
  const completedSnapshot = {
    id: "ai-1",
    type: "ai",
    content: "Complete answer",
    usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
  } as Message;

  expect(accumulateUsage([earlySnapshot, completedSnapshot])).toEqual({
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
  });
});

test("keeps header and per-turn aggregation consistent for duplicated UI groups", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Explain this",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "<think>checking context</think>Final answer",
      usage_metadata: { input_tokens: 20, output_tokens: 7, total_tokens: 27 },
    },
  ] as Message[];

  const groups = getMessageGroups(messages);
  const usageMessagesByGroupIndex = getAssistantTurnUsageMessages(groups);
  const turnUsageMessages = usageMessagesByGroupIndex.at(-1);

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
    "assistant",
  ]);
  expect(turnUsageMessages?.map((message) => message.id)).toEqual([
    "ai-1",
    "ai-1",
  ]);
  expect(accumulateUsage(messages)).toEqual(
    accumulateUsage(turnUsageMessages!),
  );
  expect(accumulateUsage(turnUsageMessages!)).toEqual({
    inputTokens: 20,
    outputTokens: 7,
    totalTokens: 27,
  });
});

test("prefers backend thread usage for header totals", () => {
  const messages = [
    {
      id: "ai-visible",
      type: "ai",
      content: "Visible answer",
      usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    },
  ] as Message[];

  expect(
    selectHeaderTokenUsage({
      backendUsage: { inputTokens: 100, outputTokens: 50, totalTokens: 150 },
      messages,
    }),
  ).toEqual({
    inputTokens: 100,
    outputTokens: 50,
    totalTokens: 150,
  });
});

test("adds current in-flight message usage to backend header totals", () => {
  const completedMessages = [
    {
      id: "ai-completed",
      type: "ai",
      content: "Completed answer",
      usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    },
    {
      id: "ai-pending",
      type: "ai",
      content: "Streaming answer",
      usage_metadata: { input_tokens: 4, output_tokens: 6, total_tokens: 10 },
    },
  ] as Message[];

  expect(
    selectHeaderTokenUsage({
      backendUsage: { inputTokens: 100, outputTokens: 50, totalTokens: 150 },
      messages: completedMessages,
      pendingMessages: [completedMessages[1]!],
    }),
  ).toEqual({
    inputTokens: 104,
    outputTokens: 56,
    totalTokens: 160,
  });
});

test("falls back to visible messages when backend usage is unavailable or zero", () => {
  const messages = [
    {
      id: "ai-visible",
      type: "ai",
      content: "Visible answer",
      usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    },
  ] as Message[];

  expect(
    selectHeaderTokenUsage({
      backendUsage: null,
      messages,
    }),
  ).toEqual({
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
  });
  expect(
    selectHeaderTokenUsage({
      backendUsage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
      messages,
    }),
  ).toEqual({
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
  });
});

test("formats large token counts without decimal places", () => {
  expect(formatTokenCount(1234)).toBe("1234");
  expect(formatTokenCount(12_345)).toBe("1万");
  expect(formatTokenCount(1_234_567)).toBe("123万");
});

test("formats context counts with one decimal place", () => {
  expect(formatContextCount(1234)).toBe("1234");
  expect(formatContextCount(12_345)).toBe("1.2万");
  expect(formatContextCount(24_999)).toBe("2.5万");
});
