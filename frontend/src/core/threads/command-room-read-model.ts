import type { Run } from "@langchain/langgraph-sdk";

import { hasStrongCommandRoomIdentity } from "./owner-scope";
import { isActiveRunStatus, isTerminalRunStatus } from "./run-status";

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
  state: string;
  current_run_id?: string | null;
  created_at?: string;
  updated_at?: string;
  closed_at?: string | null;
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

function terminalRunPatchForRoundState(state: string):
  | {
      status: string;
      terminalReason: string;
    }
  | undefined {
  if (state === "closed") {
    return undefined;
  }
  if (state === "blocked") {
    return { status: "error", terminalReason: "blocked" };
  }
  if (state === "waiting_user") {
    return { status: "interrupted", terminalReason: "waiting_user" };
  }
  return undefined;
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

    const patch = terminalRunPatchForRoundState(round.state);
    const currentRoundId = roundIdOfRun(run);
    const runWithRound = run as RunWithRound;
    if (!patch) {
      if (currentRoundId === round.round_id) {
        return run;
      }
      changed = true;
      return { ...run, round_id: round.round_id } as Run;
    }

    const nextRun = {
      ...run,
      round_id: round.round_id,
      status: patch.status,
      terminal_reason:
        runWithRound.status === patch.status
          ? (stringValue(runWithRound.terminal_reason) ?? patch.terminalReason)
          : patch.terminalReason,
    } as Run;
    if (
      currentRoundId !== round.round_id ||
      runWithRound.status !== patch.status
    ) {
      changed = true;
    }
    return nextRun;
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
  return mergedRuns;
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
