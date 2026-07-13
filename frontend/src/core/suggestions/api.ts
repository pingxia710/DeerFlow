import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import { isStaticWebsiteOnly } from "@/core/static-mode";

export interface SuggestionsConfigResponse {
  enabled: boolean;
}

export async function loadSuggestionsConfig(): Promise<SuggestionsConfigResponse> {
  if (isStaticWebsiteOnly()) {
    return { enabled: false };
  }

  const response = await fetch(`${getBackendBaseURL()}/api/suggestions/config`);
  if (!response.ok) {
    if (response.status === 404) {
      // Fallback to true if the backend is older
      return { enabled: true };
    }
    throw new Error(
      `Failed to load suggestions config: ${response.statusText}`,
    );
  }
  return response.json() as Promise<SuggestionsConfigResponse>;
}
