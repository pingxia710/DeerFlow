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
    configured: ["naxus-round"],
    loaded: ["naxus-round"],
    missing: [],
    disabled: [],
    delegated_loaded: ["naxus-round", "command-room-planner"],
    role_skills: ["command-room-planner"],
  },
  direct: {
    tool_groups: ["file:read"],
    configured_tools: ["read_file", "task"],
    include_mcp: false,
    mcp_access: "not_configured",
  },
  delegated: {
    tool_groups: null,
    configured_tools: ["read_file", "write_file"],
    include_mcp: true,
    mcp_servers_configured: [],
    mcp_cache: {
      initialized: false,
      stale: false,
      tool_count: 0,
      tool_names: [],
      last_error_type: null,
    },
  },
} as const;

test("capability health stays ready when MCP is intentionally unconfigured", () => {
  expect(getCommandRoomCapabilityHealth(healthyRuntime)).toEqual({
    status: "ready",
    issueCount: 0,
  });
});

test("capability health reports missing skills and MCP load failures", () => {
  expect(
    getCommandRoomCapabilityHealth({
      ...healthyRuntime,
      skills: { ...healthyRuntime.skills, missing: ["missing-skill"] },
      delegated: {
        ...healthyRuntime.delegated,
        mcp_servers_configured: ["github"],
        mcp_cache: {
          ...healthyRuntime.delegated.mcp_cache,
          last_error_type: "ConnectionError",
        },
      },
    }),
  ).toEqual({ status: "warning", issueCount: 2 });
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
