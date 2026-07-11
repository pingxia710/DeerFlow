import type { CommandRoomRuntimeSnapshot } from "@/core/capabilities";

export function getCommandRoomCapabilityHealth(
  runtime: CommandRoomRuntimeSnapshot,
) {
  let issueCount = 0;
  if (runtime.agent_config.status !== "loaded") issueCount += 1;
  if (runtime.agent_config.model_fallback) issueCount += 1;
  if (runtime.skills.missing.length > 0) issueCount += 1;
  if (runtime.skills.disabled.length > 0) issueCount += 1;
  if (
    runtime.delegated.mcp_servers_configured.length > 0 &&
    runtime.delegated.mcp_cache.last_error_type
  ) {
    issueCount += 1;
  }
  if (runtime.delegated.mcp_cache.stale) issueCount += 1;
  return {
    status: issueCount > 0 ? ("warning" as const) : ("ready" as const),
    issueCount,
  };
}

export function getCommandRoomModelLabel(
  runtime: CommandRoomRuntimeSnapshot,
  requestedModel?: string,
) {
  const configured = runtime.agent_config.requested_model;
  const resolved = runtime.agent_config.resolved_model;
  if (runtime.agent_config.model_fallback) {
    if (configured && resolved && configured !== resolved) {
      return `${configured} → ${resolved}`;
    }
    return resolved ?? configured ?? requestedModel ?? "-";
  }
  return requestedModel ?? resolved ?? configured ?? "-";
}
