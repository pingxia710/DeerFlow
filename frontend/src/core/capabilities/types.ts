export interface McpCacheStatus {
  initialized: boolean;
  stale: boolean;
  tool_count: number;
  tool_names: readonly string[];
  last_error_type: string | null;
}

export interface CommandRoomRuntimeSnapshot {
  agent_config: {
    status: string;
    error_type: string | null;
    requested_model: string | null;
    resolved_model: string | null;
    model_fallback: boolean;
  };
  skills: {
    configured: readonly string[] | null;
    loaded: readonly string[];
    missing: readonly string[];
    disabled: readonly string[];
    delegated_loaded: readonly string[];
    role_skills: readonly string[];
  };
  direct: {
    tool_groups: readonly string[] | null;
    configured_tools: readonly string[];
    include_mcp: boolean;
    mcp_access: string;
  };
  delegated: {
    tool_groups: readonly string[] | null;
    configured_tools: readonly string[];
    include_mcp: boolean;
    mcp_servers_configured: readonly string[];
    mcp_cache: McpCacheStatus;
  };
}

export interface CapabilitySnapshot {
  version: number;
  thread_id?: string | null;
  updated_at?: string;
  command_room_runtime: CommandRoomRuntimeSnapshot;
}
