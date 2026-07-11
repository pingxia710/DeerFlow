const deletedThreadTombstones = new Set<string>();

export function tombstoneDeletedThread(threadId: string) {
  deletedThreadTombstones.add(threadId);
}

export function isDeletedThreadTombstoned(threadId: string | null | undefined) {
  return Boolean(threadId && deletedThreadTombstones.has(threadId));
}

export function clearDeletedThreadTombstones() {
  deletedThreadTombstones.clear();
}
