import {
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  fetch,
} from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { CapabilitySnapshot } from "./types";

export class CapabilityRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "CapabilityRequestError";
    this.status = status;
  }
}

async function errorDetail(response: Response) {
  const body = (await response.json().catch(() => ({}))) as {
    detail?: unknown;
  };
  return typeof body.detail === "string"
    ? body.detail
    : `Failed to load capability snapshot: ${response.status}`;
}

export async function loadCapabilitySnapshot(threadId?: string) {
  const path = threadId
    ? `/api/threads/${encodeURIComponent(threadId)}/capabilities`
    : "/api/capabilities";
  const response = await fetch(`${getBackendBaseURL()}${path}`, {
    timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  });
  if (!response.ok) {
    throw new CapabilityRequestError(
      response.status,
      await errorDetail(response),
    );
  }
  return (await response.json()) as CapabilitySnapshot;
}
