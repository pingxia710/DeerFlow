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

export type FailedQueuedMessageDraftAction = "restore" | "acknowledge" | "wait";

export function getFailedQueuedMessageDraftAction({
  failedThreadId,
  currentThreadId,
  failedText,
  currentText,
}: {
  failedThreadId: string;
  currentThreadId: string;
  failedText: string;
  currentText: string;
}): FailedQueuedMessageDraftAction {
  if (failedThreadId !== currentThreadId || failedText.length === 0) {
    return "wait";
  }
  if (currentText === failedText) {
    return "acknowledge";
  }
  return currentText.length === 0 ? "restore" : "wait";
}

export type FollowupSuggestionsErrorAction = "clear" | "refresh-auth";

export function getFollowupSuggestionsErrorAction(
  error: unknown,
): FollowupSuggestionsErrorAction {
  return isUnauthorizedError(error) ? "refresh-auth" : "clear";
}
