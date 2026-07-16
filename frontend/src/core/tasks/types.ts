import type { AIMessage } from "@langchain/langgraph-sdk";

export type CommandRoomContainer =
  | "context"
  | "planning"
  | "technical-design"
  | "execution"
  | "review"
  | "project-steward"
  | "debt-curation"
  | "learning-curation"
  | "collaboration"
  | "evaluation";

export type CommandRoomArtifactKind =
  | "context-discovery"
  | "context"
  | "planning-forward"
  | "planning-opposition"
  | "spec"
  | "technical-forward"
  | "technical-opposition"
  | "technical-plan"
  | "execution"
  | "findings"
  | "project-status"
  | "debt"
  | "learning"
  | "round-note"
  | "evaluation"
  | "chair-decision";

export interface Subtask {
  id: string;
  threadId?: string;
  runId?: string;
  roundId?: string;
  status: "in_progress" | "completed" | "failed";
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
  commandRoomContainer?: CommandRoomContainer;
  workPackageId?: string;
  deliveryCycleIndex?: number;
  collaborationRoundIndex?: number;
  containerArtifactPath?: string;
  containerArtifactWritten?: boolean;
  containerArtifactKind?: CommandRoomArtifactKind;
  metadata?: Record<string, unknown>;
  details?: Record<string, unknown>;
}
