import type { ReasoningEffort } from "@/core/threads/types";

export interface Agent {
  name: string;
  description: string;
  model: string | null;
  reasoning_effort: ReasoningEffort | null;
  tool_groups: string[] | null;
  skills: string[] | null;
  soul?: string | null;
  system: boolean;
}

export interface CreateAgentRequest {
  name: string;
  description?: string;
  model?: string | null;
  reasoning_effort?: ReasoningEffort | null;
  tool_groups?: string[] | null;
  skills?: string[] | null;
  soul?: string;
}

export interface UpdateAgentRequest {
  description?: string | null;
  model?: string | null;
  reasoning_effort?: ReasoningEffort | null;
  tool_groups?: string[] | null;
  skills?: string[] | null;
  soul?: string | null;
}

export interface Role {
  name: string;
  description: string;
  skill: string;
  model: string | null;
  reasoning_effort: ReasoningEffort | null;
}

export interface UpdateRoleRequest {
  model: string;
  reasoning_effort: ReasoningEffort | null;
}
