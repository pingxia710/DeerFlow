import {
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  fetch,
} from "@/core/api/fetcher";

import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";

import type { ModelsResponse } from "./types";

const STATIC_MODELS_RESPONSE: ModelsResponse = {
  models: [],
  token_usage: { enabled: false },
};

export async function loadModels(): Promise<ModelsResponse> {
  if (isStaticWebsiteOnly()) {
    return STATIC_MODELS_RESPONSE;
  }

  const res = await fetch(`${getBackendBaseURL()}/api/models`, {
    timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  });
  if (!res.ok) {
    throw new Error(`Failed to load models: ${res.status}`);
  }
  const data = (await res.json()) as Partial<ModelsResponse>;
  return {
    models: data.models ?? [],
    token_usage: data.token_usage ?? { enabled: false },
  };
}
