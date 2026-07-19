import { expect, test } from "@rstest/core";

import {
  getCommandRoomCapabilityHealth,
  getCommandRoomModelLabel,
} from "@/components/workspace/command-room-capability-state";

const healthyRuntime = {
  agent_config: {
    status: "loaded",
    error_type: null,
    requested_model: "main",
    resolved_model: "main",
    model_fallback: false,
  },
  skills: {
    configured: ["command-room-fact-finder"],
    loaded: ["command-room-fact-finder"],
    missing: [],
    disabled: [],
  },
  direct: {
    tool_groups: ["file:read"],
    configured_tools: ["read_file", "task"],
    include_mcp: false,
    mcp_access: "not_configured",
  },
  task_transport: {
    runtime: "codex-cli-one-shot",
    model: "gpt-5.6-terra",
    configured_model: "gpt-5.6-terra",
    reasoning_effort: "xhigh",
    timeout_seconds: 3600,
    sandbox_mode: "workspace-write",
    workspace_source: "thread_data.workspace_path",
    inherits_deerflow_tools: false,
    inherits_deerflow_skills: false,
    inherits_deerflow_mcp: false,
    programmatic_turn_loop: false,
    process_ends_after_result: true,
  },
} as const;

test("capability health stays ready when MCP is intentionally unconfigured", () => {
  expect(getCommandRoomCapabilityHealth(healthyRuntime)).toEqual({
    status: "ready",
    issueCount: 0,
  });
});

test("capability health reports missing skills", () => {
  expect(
    getCommandRoomCapabilityHealth({
      ...healthyRuntime,
      skills: { ...healthyRuntime.skills, missing: ["missing-skill"] },
    }),
  ).toEqual({ status: "warning", issueCount: 1 });
});

test("capability health reports model fallback and names the resolved model", () => {
  const fallbackRuntime = {
    ...healthyRuntime,
    agent_config: {
      ...healthyRuntime.agent_config,
      requested_model: "missing-model",
      resolved_model: "main",
      model_fallback: true,
    },
  } as const;

  expect(getCommandRoomCapabilityHealth(fallbackRuntime)).toEqual({
    status: "warning",
    issueCount: 1,
  });
  expect(getCommandRoomModelLabel(fallbackRuntime, "thread-model")).toBe(
    "missing-model → main",
  );
});

test("capability model label prefers the thread model when no fallback occurred", () => {
  expect(getCommandRoomModelLabel(healthyRuntime, "thread-model")).toBe(
    "thread-model",
  );
});
