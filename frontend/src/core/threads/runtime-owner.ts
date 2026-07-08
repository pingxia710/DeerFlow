export type ThreadRuntimeOwnerSnapshot = {
  threadId: string | null;
  runId: string | null;
  runtimeOwnerId: string | null;
  displayThreadId: string | null;
};

type ThreadRuntimeOwnerInput = Partial<ThreadRuntimeOwnerSnapshot>;

type CurrentRuntimeOwnerEventInput = {
  eventThreadId?: string | null;
  eventRunId?: string | null;
  eventRuntimeOwnerId?: string | null;
  currentOwner?: ThreadRuntimeOwnerSnapshot | null;
  requireEventThreadId?: boolean;
  requireEventRunId?: boolean;
  allowMissingEventRunId?: boolean;
  allowMissingCurrentRunId?: boolean;
};

export function normalizeThreadRuntimeOwnerId(id: string | null | undefined) {
  return typeof id === "string" && id.length > 0 ? id : null;
}

export function createThreadRuntimeOwnerSnapshot({
  threadId,
  runId,
  runtimeOwnerId,
  displayThreadId,
}: ThreadRuntimeOwnerInput): ThreadRuntimeOwnerSnapshot {
  return {
    threadId: normalizeThreadRuntimeOwnerId(threadId),
    runId: normalizeThreadRuntimeOwnerId(runId),
    runtimeOwnerId: normalizeThreadRuntimeOwnerId(runtimeOwnerId),
    displayThreadId: normalizeThreadRuntimeOwnerId(displayThreadId),
  };
}

function ownerThreadIds(owner: ThreadRuntimeOwnerSnapshot | null | undefined) {
  return new Set(
    [owner?.threadId, owner?.displayThreadId].filter((id): id is string =>
      Boolean(id),
    ),
  );
}

export function isCurrentThreadRuntimeOwnerEvent({
  eventThreadId,
  eventRunId,
  eventRuntimeOwnerId,
  currentOwner,
  requireEventThreadId = false,
  requireEventRunId = false,
  allowMissingEventRunId = false,
  allowMissingCurrentRunId = false,
}: CurrentRuntimeOwnerEventInput) {
  if (!currentOwner) {
    return false;
  }

  const normalizedEventThreadId = normalizeThreadRuntimeOwnerId(eventThreadId);
  const normalizedEventRunId = normalizeThreadRuntimeOwnerId(eventRunId);
  const normalizedEventRuntimeOwnerId =
    normalizeThreadRuntimeOwnerId(eventRuntimeOwnerId);

  if (requireEventThreadId && !normalizedEventThreadId) {
    return false;
  }
  if (requireEventRunId && !normalizedEventRunId) {
    return false;
  }

  if (
    normalizedEventRuntimeOwnerId &&
    currentOwner.runtimeOwnerId &&
    normalizedEventRuntimeOwnerId !== currentOwner.runtimeOwnerId
  ) {
    return false;
  }

  if (normalizedEventThreadId) {
    const threadIds = ownerThreadIds(currentOwner);
    if (threadIds.size > 0 && !threadIds.has(normalizedEventThreadId)) {
      return false;
    }
  }

  if (normalizedEventRunId) {
    return currentOwner.runId
      ? normalizedEventRunId === currentOwner.runId
      : allowMissingCurrentRunId;
  }

  if (currentOwner.runId && !allowMissingEventRunId) {
    return false;
  }
  return true;
}

export function shouldClaimThreadRuntimeOwner({
  eventThreadId,
  eventRunId,
  currentOwner,
}: {
  eventThreadId: string | null | undefined;
  eventRunId?: string | null;
  currentOwner?: ThreadRuntimeOwnerSnapshot | null;
}) {
  const normalizedEventThreadId = normalizeThreadRuntimeOwnerId(eventThreadId);
  if (!normalizedEventThreadId || !currentOwner) {
    return false;
  }

  if (currentOwner.runId) {
    return isCurrentThreadRuntimeOwnerEvent({
      eventThreadId: normalizedEventThreadId,
      eventRunId,
      currentOwner,
      requireEventThreadId: true,
      allowMissingEventRunId: true,
    });
  }

  const threadIds = ownerThreadIds(currentOwner);
  if (threadIds.has(normalizedEventThreadId)) {
    return true;
  }

  // Pending /new chats know only their display id until the backend returns
  // the persisted thread id.
  return !currentOwner.threadId;
}

export function shouldPreserveRuntimeOwnerOnRouteSwitch({
  currentOwner,
  nextDisplayThreadId,
  streamFinished,
  sendInFlight,
}: {
  currentOwner?: ThreadRuntimeOwnerSnapshot | null;
  nextDisplayThreadId?: string | null;
  streamFinished: boolean;
  sendInFlight: boolean;
}) {
  const ownerThreadId = currentOwner?.threadId;
  const nextThreadId = normalizeThreadRuntimeOwnerId(nextDisplayThreadId);
  return Boolean(
    ownerThreadId &&
    ownerThreadId !== nextThreadId &&
    (!streamFinished || sendInFlight),
  );
}

export function shouldReleaseQueuedRuntimeOwner({
  queuedOwnerId,
  currentOwner,
  queuedThreadId,
  currentViewThreadId,
}: {
  queuedOwnerId?: string | null;
  currentOwner?: ThreadRuntimeOwnerSnapshot | null;
  queuedThreadId?: string | null;
  currentViewThreadId?: string | null;
}) {
  if (queuedOwnerId && currentOwner?.runtimeOwnerId) {
    return queuedOwnerId === currentOwner.runtimeOwnerId;
  }
  return Boolean(
    queuedThreadId &&
    currentViewThreadId &&
    queuedThreadId === currentViewThreadId,
  );
}
