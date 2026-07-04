import type { Message, Thread } from "@langchain/langgraph-sdk";

import type { Todo } from "../todos";

export type ReasoningEffort = "minimal" | "low" | "medium" | "high" | "xhigh";

export type ReasoningSummary = "auto" | "concise" | "detailed";

export type TextVerbosity = "low" | "medium" | "high";

export interface AgentThreadState extends Record<string, unknown> {
  title: string;
  messages: Message[];
  artifacts: string[];
  todos?: Todo[];
}

export interface AgentThreadContext extends Record<string, unknown> {
  thread_id: string;
  model_name: string | undefined;
  thinking_enabled: boolean;
  is_plan_mode: boolean;
  subagent_enabled: boolean;
  reasoning_effort?: ReasoningEffort;
  reasoning_summary?: ReasoningSummary;
  text_verbosity?: TextVerbosity;
  agent_name?: string;
}

export interface AgentThread extends Thread<AgentThreadState> {
  context?: AgentThreadContext;
}

export interface RunMessage {
  run_id: string;
  seq?: number;
  content: Message;
  metadata: {
    caller: string;
    [key: string]: unknown;
  };
  display?: {
    visible_in_chat: boolean;
    reason: string;
  };
  created_at: string;
}

export interface ThreadTokenUsageResponse {
  thread_id: string;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_runs: number;
  by_model: Record<string, { tokens: number; runs: number }>;
  by_caller: {
    lead_agent: number;
    subagent: number;
    middleware: number;
  };
}

export interface ThreadContextUsageSnapshot {
  run_id: string;
  caller: string;
  llm_call_index: number;
  message_count: number;
  tool_schema_count: number;
  char_count: number;
  estimated_tokens: number;
  role_counts: Record<string, number>;
  seq: number;
  created_at: string;
}

export interface ThreadContextUsageResponse {
  thread_id: string;
  latest: ThreadContextUsageSnapshot | null;
  latest_lead: ThreadContextUsageSnapshot | null;
  by_caller: Record<string, ThreadContextUsageSnapshot>;
  recent: ThreadContextUsageSnapshot[];
}
