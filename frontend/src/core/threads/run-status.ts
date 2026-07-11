import type { Run } from "@langchain/langgraph-sdk";

const ACTIVE_RUN_REVALIDATION_STATUSES = new Set([
  "pending",
  "running",
  "cancelling",
  "rolling_back",
]);

const TERMINAL_RUN_REVALIDATION_STATUSES = new Set([
  "success",
  "error",
  "timeout",
  "interrupted",
  "cancelled",
  "timed_out",
  "boundary_stopped",
  "worker_lost",
  "rolled_back",
  "rollback_failed",
]);

export function isActiveRunStatus(status: unknown) {
  return (
    typeof status === "string" && ACTIVE_RUN_REVALIDATION_STATUSES.has(status)
  );
}

export function isTerminalRunStatus(status: unknown) {
  return (
    typeof status === "string" && TERMINAL_RUN_REVALIDATION_STATUSES.has(status)
  );
}

export function getTerminalTransitionRunIds(
  previousActiveRunIds: ReadonlySet<string>,
  runs: Run[],
) {
  return runs
    .filter(
      (run) =>
        previousActiveRunIds.has(run.run_id) && isTerminalRunStatus(run.status),
    )
    .map((run) => run.run_id);
}
