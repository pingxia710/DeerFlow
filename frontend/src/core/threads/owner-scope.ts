export type ThreadViewScope = {
  runtimeScope: string;
  runtimeKey: string;
  displayThreadId: string;
};

export type ThreadRecordIdentity = {
  threadId: string;
  runId?: string | null;
  roundId?: string | null;
  taskId?: string | null;
  messageId?: string | null;
  toolCallId?: string | null;
};

export function createThreadViewScope(scope: ThreadViewScope): ThreadViewScope {
  return scope;
}

export function isSameThreadViewScope(
  left: ThreadViewScope | null | undefined,
  right: ThreadViewScope | null | undefined,
) {
  if (!left || !right) {
    return false;
  }
  return (
    left.runtimeScope === right.runtimeScope &&
    left.runtimeKey === right.runtimeKey &&
    left.displayThreadId === right.displayThreadId
  );
}

export function hasStrongCommandRoomIdentity(identity: ThreadRecordIdentity) {
  return Boolean(identity.threadId && identity.runId && identity.roundId);
}
