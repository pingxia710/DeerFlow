import type { AIMessage } from "@langchain/langgraph-sdk";

export interface Subtask {
  id: string;
  threadId?: string;
  status: "in_progress" | "completed" | "failed";
  subagent_type: string;
  description: string;
  latestMessage?: AIMessage;
  prompt: string;
  result?: string;
  error?: string;
}
