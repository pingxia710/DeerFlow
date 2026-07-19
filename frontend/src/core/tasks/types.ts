import type { AIMessage } from "@langchain/langgraph-sdk";

export interface Subtask {
  id: string;
  threadId?: string;
  runId?: string;
  roundId?: string;
  status: "queued" | "in_progress" | "completed" | "failed" | "unknown";
  startedAt?: number;
  finishedAt?: number;
  durationMs?: number;
  subagent_type: string;
  description: string;
  latestMessage?: AIMessage;
  prompt: string;
  result?: string;
  error?: string;
  actionResultStatus?: string;
  terminalReason?: string;
  backgroundTask?: boolean;
  metadata?: Record<string, unknown>;
  details?: Record<string, unknown>;
}
