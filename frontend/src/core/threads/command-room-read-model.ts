import type { Run } from "@langchain/langgraph-sdk";

import { hasStrongCommandRoomIdentity } from "./owner-scope";
import { isActiveRunStatus, isTerminalRunStatus } from "./run-status";

export type WakeFactsProjectionItem = {
  task_id: string;
  source_run_id: string;
  child_status: "completed";
  child_completed_at: string | null;
  wake_state: "failed";
  wake_attempts: number;
  wake_failure_reason?: "retry_exhausted" | "wake_unavailable";
  updated_at: string;
};

export type WakeFactsProjectionResponse = {
  thread_id: string;
  run_id: string;
  round_id: string;
  items: WakeFactsProjectionItem[];
};

export type TaskLaneSnapshot = {
  thread_id: string;
  run_id: string;
  round_id?: string | null;
  task_id: string;
  role?: string | null;
  description?: string | null;
  prompt?: string | null;
  subagent_type?: string | null;
  status: string;
  result?: string | null;
  result_ref?: string | null;
  evidence_ref?: string | null;
  duration_ms?: unknown;
  evidence_refs?: unknown;
  artifact_refs?: unknown;
  output_refs?: unknown;
  handoff?: unknown;
  metadata?: Record<string, unknown> | null;
  details?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string;
  started_at?: string;
  updated_at?: string;
  completed_at?: string;
  finished_at?: string;
};

export type RuntimeRoundSnapshot = {
  round_id: string;
  thread_id: string;
  current_run_id?: string | null;
  created_at?: string;
  updated_at?: string;
};

type RunWithRound = {
  metadata?: unknown;
  round_id?: unknown;
  status?: unknown;
  terminal_reason?: unknown;
};

export type CommandRoomRun = Pick<Run, "run_id" | "status"> & RunWithRound;

export type CommandRoomReadModel = {
  threadId: string;
  activeRun: CommandRoomRun | null;
  activeRound: RuntimeRoundSnapshot | null;
  taskLanes: TaskLaneSnapshot[];
  legacyTaskLanes: TaskLaneSnapshot[];
};

function stringValue(value: unknown) {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function nonEmptyString(value: unknown) {
  const candidate = stringValue(value);
  return candidate?.trim() ? candidate : undefined;
}

export function parseWakeFactsProjection(
  value: unknown,
  scope: { threadId: string; runId: string; roundId: string },
): WakeFactsProjectionResponse {
  const empty: WakeFactsProjectionResponse = {
    thread_id: scope.threadId,
    run_id: scope.runId,
    round_id: scope.roundId,
    items: [],
  };
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return empty;
  }
  const response = value as Record<string, unknown>;
  if (
    response.thread_id !== scope.threadId ||
    response.run_id !== scope.runId ||
    response.round_id !== scope.roundId ||
    !Array.isArray(response.items)
  ) {
    return empty;
  }

  const items: WakeFactsProjectionItem[] = [];
  for (const value of response.items) {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      continue;
    }
    const item = value as Record<string, unknown>;
    const taskId = nonEmptyString(item.task_id);
    const sourceRunId = nonEmptyString(item.source_run_id);
    const childCompletedAt = item.child_completed_at;
    const updatedAt = nonEmptyString(item.updated_at);
    const reason = item.wake_failure_reason;
    if (
      !taskId ||
      sourceRunId !== scope.runId ||
      item.child_status !== "completed" ||
      (childCompletedAt !== null && !nonEmptyString(childCompletedAt)) ||
      item.wake_state !== "failed" ||
      typeof item.wake_attempts !== "number" ||
      !Number.isInteger(item.wake_attempts) ||
      item.wake_attempts < 0 ||
      !updatedAt ||
      (reason !== undefined &&
        reason !== "retry_exhausted" &&
        reason !== "wake_unavailable")
    ) {
      continue;
    }
    items.push({
      task_id: taskId,
      source_run_id: sourceRunId,
      child_status: "completed",
      child_completed_at: childCompletedAt as string | null,
      wake_state: "failed",
      wake_attempts: item.wake_attempts,
      ...(reason ? { wake_failure_reason: reason } : {}),
      updated_at: updatedAt,
    });
  }
  return { ...empty, items };
}

export function wakeFactForTask(
  projection: WakeFactsProjectionResponse | undefined,
  taskId: string,
) {
  return projection?.items.find((item) => item.task_id === taskId);
}

export function roundIdOfRun(run: Run | CommandRoomRun | undefined) {
  const roundId = (run as RunWithRound | undefined)?.round_id;
  if (typeof roundId === "string") {
    return roundId;
  }
  const metadata = (run as RunWithRound | undefined)?.metadata;
  const metadataRoundId =
    typeof metadata === "object" && metadata !== null
      ? (metadata as Record<string, unknown>).round_id
      : undefined;
  return typeof metadataRoundId === "string" ? metadataRoundId : null;
}

function roundCurrentRunId(round: RuntimeRoundSnapshot) {
  return stringValue(round.current_run_id);
}

export function applyNativeRoundsToSnapshotRuns(
  runs: Run[] | undefined,
  rounds: RuntimeRoundSnapshot[] | undefined,
): Run[] | undefined {
  if (!runs) {
    return undefined;
  }
  if (!rounds || rounds.length === 0) {
    return runs;
  }

  const roundByRunId = new Map<string, RuntimeRoundSnapshot>();
  for (const round of rounds) {
    const runId = roundCurrentRunId(round);
    if (runId) {
      roundByRunId.set(runId, round);
    }
  }

  let changed = false;
  const nextRuns = runs.map((run) => {
    const round = roundByRunId.get(run.run_id);
    if (!round) {
      return run;
    }

    const currentRoundId = roundIdOfRun(run);
    if (currentRoundId === round.round_id) {
      return run;
    }
    changed = true;
    return { ...run, round_id: round.round_id } as Run;
  });

  return changed ? nextRuns : runs;
}

export function mergeRunsWithTerminalPrecedence({
  snapshotRuns,
  queriedRuns,
  rounds,
}: {
  snapshotRuns?: Run[];
  queriedRuns?: Run[];
  rounds?: RuntimeRoundSnapshot[];
}): Run[] | undefined {
  const roundedSnapshotRuns = applyNativeRoundsToSnapshotRuns(
    snapshotRuns,
    rounds,
  );
  const roundedQueriedRuns = applyNativeRoundsToSnapshotRuns(
    queriedRuns,
    rounds,
  );
  if (!roundedQueriedRuns) {
    return roundedSnapshotRuns;
  }
  if (!roundedSnapshotRuns) {
    return roundedQueriedRuns;
  }

  const snapshotByRunId = new Map(
    roundedSnapshotRuns.map((run) => [run.run_id, run]),
  );
  const queriedRunIds = new Set(roundedQueriedRuns.map((run) => run.run_id));
  const mergedRuns = roundedQueriedRuns.map((queriedRun) => {
    const snapshotRun = snapshotByRunId.get(queriedRun.run_id);
    if (!snapshotRun) {
      return queriedRun;
    }
    if (
      isTerminalRunStatus(snapshotRun.status) &&
      isActiveRunStatus(queriedRun.status)
    ) {
      return snapshotRun;
    }
    if (
      isTerminalRunStatus(queriedRun.status) &&
      isActiveRunStatus(snapshotRun.status)
    ) {
      return queriedRun;
    }
    return queriedRun;
  });

  for (const snapshotRun of roundedSnapshotRuns) {
    if (!queriedRunIds.has(snapshotRun.run_id)) {
      mergedRuns.push(snapshotRun);
    }
  }
  const runsWithCreatedAt = mergedRuns.map((run, index) => {
    const createdAt = Reflect.get(run, "created_at");
    const createdAtMs =
      typeof createdAt === "string" ? Date.parse(createdAt) : Number.NaN;
    return { run, index, createdAtMs };
  });
  if (
    runsWithCreatedAt.some(({ createdAtMs }) => !Number.isFinite(createdAtMs))
  ) {
    return mergedRuns;
  }
  const currentRunOrder = new Map<string, number>();
  for (const [index, round] of (rounds ?? []).entries()) {
    const runId = roundCurrentRunId(round);
    if (runId) {
      currentRunOrder.set(runId, index);
    }
  }
  return runsWithCreatedAt
    .sort(
      (left, right) =>
        right.createdAtMs - left.createdAtMs ||
        (currentRunOrder.get(left.run.run_id) ?? Number.MAX_SAFE_INTEGER) -
          (currentRunOrder.get(right.run.run_id) ?? Number.MAX_SAFE_INTEGER) ||
        left.index - right.index,
    )
    .map(({ run }) => run);
}

export function latestRoundIdFromSnapshot(
  runs: Run[] | undefined,
  rounds: RuntimeRoundSnapshot[] | undefined,
) {
  const latestRun = runs?.[0];
  if (!latestRun) {
    return null;
  }
  const latestRunRound = rounds?.find(
    (round) => roundCurrentRunId(round) === latestRun.run_id,
  );
  return latestRunRound?.round_id ?? roundIdOfRun(latestRun);
}

export function taskLanesForLatestRound(
  lanes: TaskLaneSnapshot[] | undefined,
  latestRoundId: string | null,
) {
  const rows = lanes ?? [];
  if (!latestRoundId) {
    return rows;
  }
  return rows.filter(
    (lane) => !lane.round_id || lane.round_id === latestRoundId,
  );
}

export function resolveThreadHistoryReset({
  enabled,
  threadChanged,
  previousRoundId,
  latestRoundId,
}: {
  enabled: boolean;
  threadChanged: boolean;
  previousRoundId: string | null;
  latestRoundId: string | null;
}) {
  if (!enabled || threadChanged) {
    return "clear";
  }
  if (
    previousRoundId !== null &&
    latestRoundId !== null &&
    previousRoundId !== latestRoundId
  ) {
    return "clear";
  }
  return "none";
}

export function buildCommandRoomReadModel({
  threadId,
  runs,
  rounds,
  taskLanes,
}: {
  threadId: string;
  runs?: CommandRoomRun[];
  rounds?: RuntimeRoundSnapshot[];
  taskLanes?: TaskLaneSnapshot[];
}): CommandRoomReadModel {
  const activeRun = runs?.[0] ?? null;
  const activeRoundId = roundIdOfRun(activeRun ?? undefined);
  const activeRound =
    rounds?.find(
      (round) =>
        round.thread_id === threadId &&
        (round.current_run_id === activeRun?.run_id ||
          round.round_id === activeRoundId),
    ) ?? null;
  const effectiveRoundId = activeRound?.round_id ?? activeRoundId;
  const ownedLanes = (taskLanes ?? []).filter(
    (lane) => lane.thread_id === threadId && lane.run_id === activeRun?.run_id,
  );

  return {
    threadId,
    activeRun,
    activeRound,
    taskLanes: effectiveRoundId
      ? ownedLanes.filter(
          (lane) =>
            lane.round_id === effectiveRoundId &&
            hasStrongCommandRoomIdentity({
              threadId: lane.thread_id,
              runId: lane.run_id,
              roundId: lane.round_id,
            }),
        )
      : [],
    legacyTaskLanes: ownedLanes.filter((lane) => !lane.round_id),
  };
}
