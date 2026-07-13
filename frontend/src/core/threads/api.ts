import {
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  fetch as fetchWithAuth,
} from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import { isStaticWebsiteOnly } from "@/core/static-mode";

import type {
  ThreadContextDetail,
  ThreadContextUsageResponse,
  ThreadTokenUsageResponse,
} from "./types";

export async function fetchThreadTokenUsage(
  threadId: string,
): Promise<ThreadTokenUsageResponse | null> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/token-usage`,
    {
      method: "GET",
      timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
    },
  );

  if (!response.ok) {
    if (response.status === 403 || response.status === 404) {
      return null;
    }
    throw new Error("Failed to load thread token usage.");
  }

  return (await response.json()) as ThreadTokenUsageResponse;
}

export async function fetchThreadContextUsage(
  threadId: string,
): Promise<ThreadContextUsageResponse | null> {
  if (isStaticWebsiteOnly()) {
    return null;
  }

  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/context-usage`,
    {
      method: "GET",
      timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
    },
  );

  if (!response.ok) {
    if (response.status === 403 || response.status === 404) {
      return null;
    }
    throw new Error("Failed to load thread context usage.");
  }

  return (await response.json()) as ThreadContextUsageResponse;
}

export async function fetchThreadContextDetail(
  threadId: string,
  runId: string,
  seq: number,
): Promise<ThreadContextDetail | null> {
  if (isStaticWebsiteOnly()) {
    return null;
  }

  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/context-usage/${encodeURIComponent(runId)}/${seq}`,
    {
      method: "GET",
      timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
    },
  );

  if (!response.ok) {
    if (response.status === 403 || response.status === 404) {
      return null;
    }
    throw new Error("Failed to load complete thread context.");
  }

  return (await response.json()) as ThreadContextDetail;
}
