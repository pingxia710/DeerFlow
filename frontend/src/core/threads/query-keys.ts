export const queryKeys = {
  threads: {
    search: (params?: unknown) =>
      params === undefined
        ? (["threads", "search"] as const)
        : (["threads", "search", params] as const),
    infinite: (params?: unknown) =>
      params === undefined
        ? (["threads", "searchInfinite"] as const)
        : (["threads", "searchInfinite", params] as const),
  },
  thread: {
    runs: (threadId?: string | null) => ["thread", threadId, "runs"] as const,
    runtimeSnapshot: (threadId?: string | null) =>
      ["thread", threadId, "runtime-snapshot"] as const,
    capabilitySnapshot: (threadId?: string | null) =>
      ["capability-snapshot", threadId ?? "global"] as const,
    metadata: (threadId?: string | null, isMock = false) =>
      ["thread", "metadata", threadId, isMock] as const,
    run: (threadId: string, runId: string) =>
      ["thread", threadId, "run", runId] as const,
    tokenUsage: (threadId?: string | null) =>
      ["thread-token-usage", threadId] as const,
    contextUsage: (threadId?: string | null) =>
      ["thread-context-usage", threadId] as const,
    artifact: (
      threadId: string,
      filepath: string,
      isMock: boolean | undefined,
    ) => ["artifact", filepath, threadId, isMock] as const,
    uploads: (threadId?: string | null) =>
      ["uploads", "list", threadId] as const,
  },
} as const;

export function isThreadScopedQueryKey(
  queryKey: readonly unknown[],
  threadId: string,
) {
  if (queryKey[0] === "capability-snapshot" && queryKey[1] === threadId) {
    return true;
  }
  if (
    queryKey[0] === "thread" &&
    queryKey[1] === threadId &&
    (queryKey[2] === "runs" ||
      queryKey[2] === "runtime-snapshot" ||
      queryKey[2] === "run")
  ) {
    return true;
  }
  if (
    queryKey[0] === "thread" &&
    queryKey[1] === "metadata" &&
    queryKey[2] === threadId
  ) {
    return true;
  }
  if (
    (queryKey[0] === "thread-token-usage" ||
      queryKey[0] === "thread-context-usage") &&
    queryKey[1] === threadId
  ) {
    return true;
  }
  if (
    queryKey[0] === "uploads" &&
    queryKey[1] === "list" &&
    queryKey[2] === threadId
  ) {
    return true;
  }
  return queryKey[0] === "artifact" && queryKey[2] === threadId;
}
