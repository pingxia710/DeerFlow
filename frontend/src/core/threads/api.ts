import {
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  fetch as fetchWithAuth,
} from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import { isStaticWebsiteOnly } from "@/core/static-mode";

import {
  parseGoalTree,
  parseGoalWorkspace,
  parseGoalWorkspaceHistory,
  type GoalTree,
  type GoalWorkspace,
  type GoalWorkspaceHistoryPage,
} from "./goal-workspace";
import {
  parseThreadTimelinePage,
  type ThreadTimelinePage,
} from "./thread-timeline";
import type {
  ThreadContextDetail,
  ThreadContextUsageResponse,
  ThreadTokenUsageResponse,
} from "./types";

export class ThreadTimelineRequestError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ThreadTimelineRequestError";
  }
}

export async function fetchGoalWorkspace(
  threadId: string,
): Promise<GoalWorkspace> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL().replace(/\/$/, "")}/api/threads/${encodeURIComponent(threadId)}/goal-workspace`,
    {
      method: "GET",
      timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
    },
  );
  if (!response.ok) {
    throw new Error("Failed to load Goal Workspace.");
  }
  return parseGoalWorkspace(await response.json(), threadId);
}

export async function fetchGoalWorkspaceHistory(
  threadId: string,
  {
    beforeRevision,
    limit = 20,
    signal,
  }: {
    beforeRevision?: number | null;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<GoalWorkspaceHistoryPage> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (typeof beforeRevision === "number") {
    query.set("before_revision", String(beforeRevision));
  }
  const response = await fetchWithAuth(
    `${getBackendBaseURL().replace(/\/$/, "")}/api/threads/${encodeURIComponent(threadId)}/goal-workspace/history?${query}`,
    {
      method: "GET",
      timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
      signal,
    },
  );
  if (!response.ok) {
    throw new Error("Failed to load Goal Workspace history.");
  }
  return parseGoalWorkspaceHistory(await response.json(), threadId);
}

export async function fetchGoalTree(threadId: string): Promise<GoalTree> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL().replace(/\/$/, "")}/api/threads/${encodeURIComponent(threadId)}/goal-tree`,
    {
      method: "GET",
      timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
    },
  );
  if (!response.ok) {
    throw new Error("Failed to load Goal tree.");
  }
  return parseGoalTree(await response.json());
}

export async function fetchThreadTimeline(
  threadId: string,
  cursor?: string,
): Promise<ThreadTimelinePage> {
  const query = cursor ? `?${new URLSearchParams({ cursor }).toString()}` : "";
  const response = await fetchWithAuth(
    `${getBackendBaseURL().replace(/\/$/, "")}/api/threads/${encodeURIComponent(threadId)}/timeline${query}`,
    {
      method: "GET",
      timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
    },
  );
  if (!response.ok) {
    throw new ThreadTimelineRequestError(
      response.status,
      "Failed to load thread timeline.",
    );
  }
  return parseThreadTimelinePage(await response.json(), threadId);
}

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
