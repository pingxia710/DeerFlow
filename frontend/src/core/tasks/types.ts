import type { AIMessage } from "@langchain/langgraph-sdk";

export interface Subtask {
  id: string;
  threadId?: string;
  runId?: string;
  roundId?: string;
  status: "in_progress" | "completed" | "failed";
  startedAt?: number;
  subagent_type: string;
  description: string;
  latestMessage?: AIMessage;
  prompt: string;
  result?: string;
  error?: string;
  actionResultStatus?: string;
  terminalReason?: string;
  metadata?: Record<string, unknown>;
  details?: Record<string, unknown>;
}
