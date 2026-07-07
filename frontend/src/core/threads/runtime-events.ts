export const THREAD_RUNTIME_DELETED_EVENT = "deer-flow:thread-runtime-deleted";

export type ThreadRuntimeDeletedDetail = {
  threadId: string;
};

export function notifyThreadRuntimeDeleted(threadId: string) {
  if (
    typeof window === "undefined" ||
    typeof window.dispatchEvent !== "function"
  ) {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<ThreadRuntimeDeletedDetail>(THREAD_RUNTIME_DELETED_EVENT, {
      detail: { threadId },
    }),
  );
}
