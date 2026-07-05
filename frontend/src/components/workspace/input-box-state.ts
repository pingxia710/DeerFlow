import { isUnauthorizedError } from "@/core/api/fetcher";

export function shouldClearPromptInputForThreadChange(
  previousThreadId: string,
  nextThreadId: string,
) {
  return previousThreadId !== nextThreadId;
}

export type FollowupSuggestionsErrorAction = "clear" | "refresh-auth";

export function getFollowupSuggestionsErrorAction(
  error: unknown,
): FollowupSuggestionsErrorAction {
  return isUnauthorizedError(error) ? "refresh-auth" : "clear";
}
