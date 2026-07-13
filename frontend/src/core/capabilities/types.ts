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
  };
  direct: {
    tool_groups: readonly string[] | null;
    configured_tools: readonly string[];
    include_mcp: boolean;
    mcp_access: string;
  };
  task_transport: {
    runtime: "codex-cli-one-shot";
    model: string | null;
    configured_model: string | null;
    reasoning_effort: string | null;
    timeout_seconds: number;
    sandbox_mode: "workspace-write" | "danger-full-access";
    workspace_source: string;
    inherits_deerflow_tools: false;
    inherits_deerflow_skills: false;
    inherits_deerflow_mcp: false;
    programmatic_turn_loop: false;
    process_ends_after_result: true;
  };
}

export interface CapabilitySnapshot {
  version: number;
  thread_id?: string | null;
  updated_at?: string;
  command_room_runtime: CommandRoomRuntimeSnapshot;
}
