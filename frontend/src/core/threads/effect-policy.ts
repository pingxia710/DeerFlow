import { isSameThreadViewScope, type ThreadViewScope } from "./owner-scope";
import {
  createThreadRuntimeOwnerSnapshot,
  isCurrentThreadRuntimeOwnerEvent,
  type ThreadRuntimeOwnerSnapshot,
} from "./runtime-owner";

export function shouldApplyVisibleViewEffect({
  effectView,
  currentView,
}: {
  effectView: ThreadViewScope | null | undefined;
  currentView: ThreadViewScope | null | undefined;
}) {
  return isSameThreadViewScope(effectView, currentView);
}

export function shouldApplyOwnerThreadEffect({
  effectThreadId,
  ownerThreadId,
}: {
  effectThreadId: string | null | undefined;
  ownerThreadId: string | null | undefined;
}) {
  return Boolean(
    effectThreadId && ownerThreadId && effectThreadId === ownerThreadId,
  );
}

export function shouldApplyVisibleThreadEffect({
  effectThreadId,
  visibleThreadId,
  committedThreadId,
}: {
  effectThreadId: string | null | undefined;
  visibleThreadId: string | null | undefined;
  committedThreadId: string | null | undefined;
}) {
  return Boolean(
    effectThreadId &&
    (effectThreadId === visibleThreadId ||
      effectThreadId === committedThreadId),
  );
}

type StreamOwnershipState = {
  eventThreadId?: string | null;
  eventRunId?: string | null;
  eventRuntimeOwnerId?: string | null;
  streamThreadId?: string | null;
  streamRunId?: string | null;
  runtimeOwnerId?: string | null;
  viewThreadId?: string | null;
  displayThreadId?: string | null;
  liveMessagesThreadId?: string | null;
  optimisticThreadId?: string | null;
};

export function resolveStreamErrorRecoveryRuntimeOwnerId({
  eventThreadId,
  eventRunId,
  streamOwner,
  currentOwner,
  errorOwnsCurrentUi,
}: {
  eventThreadId: string | null | undefined;
  eventRunId: string | null | undefined;
  streamOwner?: ThreadRuntimeOwnerSnapshot | null;
  currentOwner?: ThreadRuntimeOwnerSnapshot | null;
  errorOwnsCurrentUi: boolean;
}) {
  if (!eventThreadId || !eventRunId) {
    return null;
  }

  const streamOwnerMatchesRecoveryEvent = isCurrentThreadRuntimeOwnerEvent({
    eventThreadId,
    eventRunId,
    currentOwner: streamOwner,
    requireEventThreadId: true,
    requireEventRunId: Boolean(streamOwner?.runId),
    allowMissingCurrentRunId: true,
  });
  if (streamOwnerMatchesRecoveryEvent) {
    return streamOwner?.runtimeOwnerId ?? null;
  }

  const currentOwnerMatchesRecoveryEvent = isCurrentThreadRuntimeOwnerEvent({
    eventThreadId,
    eventRunId,
    currentOwner,
    requireEventThreadId: true,
    requireEventRunId: Boolean(currentOwner?.runId),
    allowMissingCurrentRunId: true,
  });
  if (errorOwnsCurrentUi && currentOwnerMatchesRecoveryEvent) {
    return currentOwner?.runtimeOwnerId ?? null;
  }

  return null;
}

export function shouldTreatTerminalEventAsCurrentStream(
  eventThreadId: string | null | undefined,
  eventRunId: string | null | undefined,
  streamThreadId: string | null | undefined,
  streamRunId: string | null | undefined,
) {
  return isCurrentThreadRuntimeOwnerEvent({
    eventThreadId,
    eventRunId,
    currentOwner: createThreadRuntimeOwnerSnapshot({
      threadId: streamThreadId,
      runId: streamRunId,
      displayThreadId: streamThreadId,
    }),
    requireEventThreadId: true,
    requireEventRunId: true,
  });
}

export function shouldTreatStreamFinishAsCurrentStream(
  finishThreadId: string | null | undefined,
  finishRunId: string | null | undefined,
  streamThreadId: string | null | undefined,
  streamRunId: string | null | undefined,
) {
  const streamHasRunOwner = Boolean(streamRunId);
  return isCurrentThreadRuntimeOwnerEvent({
    eventThreadId: finishThreadId,
    eventRunId: finishRunId,
    currentOwner: createThreadRuntimeOwnerSnapshot({
      threadId: streamThreadId,
      runId: streamRunId,
      displayThreadId: streamThreadId,
    }),
    requireEventThreadId: true,
    requireEventRunId: streamHasRunOwner,
    allowMissingEventRunId: !streamHasRunOwner,
    allowMissingCurrentRunId: true,
  });
}

export function shouldRunCurrentStreamFinishSideEffects({
  eventThreadId,
  eventRunId,
  eventRuntimeOwnerId,
  streamThreadId,
  streamRunId,
  runtimeOwnerId,
  displayThreadId,
}: StreamOwnershipState) {
  const streamHasRunOwner = Boolean(streamRunId);
  return isCurrentThreadRuntimeOwnerEvent({
    eventThreadId,
    eventRunId,
    eventRuntimeOwnerId,
    currentOwner: createThreadRuntimeOwnerSnapshot({
      threadId: streamThreadId,
      runId: streamRunId,
      runtimeOwnerId,
      displayThreadId: displayThreadId ?? streamThreadId,
    }),
    requireEventThreadId: true,
    requireEventRunId: streamHasRunOwner,
    allowMissingEventRunId: !streamHasRunOwner,
    allowMissingCurrentRunId: true,
  });
}

export function shouldApplyStreamTitleUpdate({
  eventThreadId,
  eventRunId,
  eventRuntimeOwnerId,
  streamThreadId,
  streamRunId,
  runtimeOwnerId,
  displayThreadId,
}: StreamOwnershipState) {
  const streamHasRunOwner = Boolean(streamRunId);
  return isCurrentThreadRuntimeOwnerEvent({
    eventThreadId,
    eventRunId,
    eventRuntimeOwnerId,
    currentOwner: createThreadRuntimeOwnerSnapshot({
      threadId: streamThreadId,
      runId: streamRunId,
      runtimeOwnerId,
      displayThreadId: displayThreadId ?? streamThreadId,
    }),
    requireEventThreadId: streamHasRunOwner,
    requireEventRunId: streamHasRunOwner,
    allowMissingEventRunId: !streamHasRunOwner,
  });
}
