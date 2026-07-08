import { isUnauthorizedError } from "@/core/api/fetcher";

const DEFAULT_COMPOSER_SESSION_ID = "__default__";

export function getPromptInputComposerKey({
  threadId,
  composerSessionId,
}: {
  threadId: string;
  composerSessionId?: string | null;
}) {
  return JSON.stringify([
    "prompt-input",
    threadId,
    composerSessionId ?? DEFAULT_COMPOSER_SESSION_ID,
  ]);
}

export function shouldApplyPromptInputSubmitContinuation(
  currentComposerKey: string,
  submitComposerKey: string,
) {
  return currentComposerKey === submitComposerKey;
}

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
