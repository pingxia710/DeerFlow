export type GoalWorkspaceRecord = {
  revision: number;
  body: string;
  contentHash: string;
  authorRunId: string | null;
  createdAt: string;
};

export type GoalWorkspaceResult = GoalWorkspaceRecord & {
  metadata: Record<string, unknown>;
};

export type GoalWorkspaceHistoryEvent = GoalWorkspaceResult & {
  eventType: string;
};

export type GoalWorkspaceHistoryPage = {
  threadId: string;
  events: GoalWorkspaceHistoryEvent[];
  nextBeforeRevision: number | null;
};

export type GoalWorkspace = {
  threadId: string;
  goalMandate: GoalWorkspaceRecord | null;
  operatingBrief: GoalWorkspaceRecord | null;
  organizationMap: GoalWorkspaceRecord | null;
  acknowledgedThroughSeq: number;
  notifiedThroughSeq: number;
  results: GoalWorkspaceResult[];
};

export type GoalCellNode = {
  threadId: string;
  parentThreadId: string;
  parentRunId: string;
  displayName: string | null;
  runtimeStatus: string;
  capabilityRefs: string[];
  workspaceRef: string | null;
  createdAt: string;
  updatedAt: string;
};

export type GoalTree = {
  rootThreadId: string;
  cells: GoalCellNode[];
};

export type GoalCellRow = GoalCellNode & { depth: number };

function objectRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function nonNegativeInteger(value: unknown) {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : 0;
}

function stringOrNull(value: unknown) {
  return typeof value === "string" ? value : null;
}

function positiveIntegerOrNull(value: unknown) {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? value
    : null;
}

function parseRecord(value: unknown): GoalWorkspaceRecord | null {
  const row = objectRecord(value);
  if (
    !row ||
    typeof row.revision !== "number" ||
    !Number.isInteger(row.revision) ||
    row.revision < 0 ||
    typeof row.body !== "string" ||
    typeof row.content_hash !== "string" ||
    typeof row.created_at !== "string"
  ) {
    return null;
  }
  return {
    revision: row.revision,
    body: row.body,
    contentHash: row.content_hash,
    authorRunId: stringOrNull(row.author_run_id),
    createdAt: row.created_at,
  };
}

export function parseGoalWorkspace(
  value: unknown,
  threadId: string,
): GoalWorkspace {
  const payload = objectRecord(value);
  if (payload?.thread_id !== threadId) {
    throw new Error(
      "Goal Workspace response did not match the requested thread.",
    );
  }
  const results: GoalWorkspaceResult[] = [];
  if (Array.isArray(payload.results)) {
    for (const value of payload.results) {
      const record = parseRecord(value);
      const raw = objectRecord(value);
      if (!record || !raw) continue;
      results.push({
        ...record,
        metadata: objectRecord(raw.metadata) ?? {},
      });
    }
  }
  return {
    threadId,
    goalMandate: parseRecord(payload.goal_mandate),
    operatingBrief: parseRecord(payload.operating_brief),
    organizationMap: parseRecord(payload.organization_map),
    acknowledgedThroughSeq: nonNegativeInteger(
      payload.acknowledged_through_seq,
    ),
    notifiedThroughSeq: nonNegativeInteger(payload.notified_through_seq),
    results,
  };
}

export function parseGoalWorkspaceHistory(
  value: unknown,
  threadId: string,
): GoalWorkspaceHistoryPage {
  const payload = objectRecord(value);
  if (payload?.thread_id !== threadId) {
    throw new Error(
      "Goal Workspace history response did not match the requested thread.",
    );
  }
  const events: GoalWorkspaceHistoryEvent[] = [];
  if (Array.isArray(payload.events)) {
    for (const value of payload.events) {
      const record = parseRecord(value);
      const raw = objectRecord(value);
      const eventType = raw?.event_type;
      if (!record || !raw || typeof eventType !== "string" || !eventType) {
        continue;
      }
      events.push({
        ...record,
        eventType,
        metadata: objectRecord(raw.metadata) ?? {},
      });
    }
  }
  return {
    threadId,
    events,
    nextBeforeRevision: positiveIntegerOrNull(payload.next_before_revision),
  };
}

export function parseGoalTree(value: unknown): GoalTree {
  const payload = objectRecord(value);
  if (!payload || typeof payload.root_thread_id !== "string") {
    throw new Error("Goal tree response is invalid.");
  }
  const cells: GoalCellNode[] = [];
  if (Array.isArray(payload.cells)) {
    for (const value of payload.cells) {
      const row = objectRecord(value);
      if (
        !row ||
        typeof row.thread_id !== "string" ||
        typeof row.parent_thread_id !== "string" ||
        typeof row.parent_run_id !== "string" ||
        typeof row.runtime_status !== "string" ||
        typeof row.created_at !== "string" ||
        typeof row.updated_at !== "string"
      ) {
        continue;
      }
      cells.push({
        threadId: row.thread_id,
        parentThreadId: row.parent_thread_id,
        parentRunId: row.parent_run_id,
        displayName: stringOrNull(row.display_name),
        runtimeStatus: row.runtime_status,
        capabilityRefs: Array.isArray(row.capability_refs)
          ? row.capability_refs.filter(
              (item): item is string => typeof item === "string",
            )
          : [],
        workspaceRef: stringOrNull(row.workspace_ref),
        createdAt: row.created_at,
        updatedAt: row.updated_at,
      });
    }
  }
  return { rootThreadId: payload.root_thread_id, cells };
}

export function goalCellRows(tree: GoalTree): GoalCellRow[] {
  const children = new Map<string, GoalCellNode[]>();
  for (const cell of tree.cells) {
    const siblings = children.get(cell.parentThreadId) ?? [];
    siblings.push(cell);
    children.set(cell.parentThreadId, siblings);
  }
  for (const siblings of children.values()) {
    siblings.sort(
      (left, right) =>
        left.createdAt.localeCompare(right.createdAt) ||
        left.threadId.localeCompare(right.threadId),
    );
  }

  const rows: GoalCellRow[] = [];
  const visited = new Set<string>();
  const visit = (parentThreadId: string, depth: number) => {
    for (const cell of children.get(parentThreadId) ?? []) {
      if (visited.has(cell.threadId)) continue;
      visited.add(cell.threadId);
      rows.push({ ...cell, depth });
      visit(cell.threadId, depth + 1);
    }
  };
  visit(tree.rootThreadId, 0);
  for (const cell of tree.cells) {
    if (!visited.has(cell.threadId)) {
      visited.add(cell.threadId);
      rows.push({ ...cell, depth: 0 });
      visit(cell.threadId, 1);
    }
  }
  return rows;
}
