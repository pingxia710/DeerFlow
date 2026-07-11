import { useQuery } from "@tanstack/react-query";

import { isDeletedThreadTombstoned } from "@/core/threads/deleted-thread-tombstones";
import { queryKeys } from "@/core/threads/query-keys";

import { CapabilityRequestError, loadCapabilitySnapshot } from "./api";

export function useCapabilitySnapshot(
  threadId?: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: queryKeys.thread.capabilitySnapshot(threadId),
    queryFn: () => loadCapabilitySnapshot(threadId),
    enabled: (options.enabled ?? true) && !isDeletedThreadTombstoned(threadId),
    staleTime: 30_000,
    retry: (count, error) =>
      !(error instanceof CapabilityRequestError) && count < 2,
  });
}
