type WelcomeModeInput = {
  committedPathname: string;
  hasMessages: boolean;
  hasPendingUsageMessages: boolean;
  isHistoryLoading: boolean;
  isNewThread: boolean;
  isStreamingOrLoading: boolean;
  pendingStartThreadId: string | null;
};

export function shouldShowWelcomeMode({
  committedPathname,
  hasMessages,
  hasPendingUsageMessages,
  isHistoryLoading,
  isNewThread,
  isStreamingOrLoading,
  pendingStartThreadId,
}: WelcomeModeInput) {
  return (
    isNewThread &&
    committedPathname.endsWith("/new") &&
    !hasMessages &&
    !hasPendingUsageMessages &&
    !isStreamingOrLoading &&
    !isHistoryLoading &&
    pendingStartThreadId === null
  );
}
