import type { TokenUsage } from "@/core/messages/usage";

import type { ThreadTokenUsageResponse } from "./types";

export function getCallerTokenUsageRows(
  callerUsage: ThreadTokenUsageResponse["by_caller"] | null | undefined,
) {
  if (!callerUsage) {
    return [];
  }
  return [
    { key: "lead_agent" as const, tokens: callerUsage.lead_agent ?? 0 },
    { key: "subagent" as const, tokens: callerUsage.subagent ?? 0 },
    { key: "middleware" as const, tokens: callerUsage.middleware ?? 0 },
  ].filter((row) => row.tokens > 0);
}

export function getLeadAgentTokenUsage(
  callerUsage: ThreadTokenUsageResponse["by_caller"] | null | undefined,
) {
  const tokens = callerUsage?.lead_agent ?? 0;
  return tokens > 0 ? tokens : null;
}

export function threadTokenUsageQueryKey(threadId?: string | null) {
  return ["thread-token-usage", threadId] as const;
}

export function threadContextUsageQueryKey(threadId?: string | null) {
  return ["thread-context-usage", threadId] as const;
}

export function threadTokenUsageToTokenUsage(
  usage: ThreadTokenUsageResponse | null | undefined,
): TokenUsage | null {
  if (!usage) {
    return null;
  }
  return {
    inputTokens: usage.total_input_tokens ?? 0,
    outputTokens: usage.total_output_tokens ?? 0,
    totalTokens: usage.total_tokens ?? 0,
  };
}
