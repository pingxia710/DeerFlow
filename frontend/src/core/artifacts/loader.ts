import type { BaseStream } from "@langchain/langgraph-sdk/react";

import {
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  fetch,
} from "../api/fetcher";
import type { AgentThreadState } from "../threads";

import { buildWriteFileDraftContent } from "./preview";
import { urlOfArtifact } from "./utils";

export class ArtifactRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ArtifactRequestError";
    this.status = status;
  }
}

async function readArtifactError(response: Response) {
  const error = (await response.json().catch(() => ({}))) as {
    detail?: unknown;
  };
  return typeof error.detail === "string"
    ? error.detail
    : `Failed to load artifact: ${response.status}`;
}

export async function loadArtifactContent({
  filepath,
  threadId,
  isMock,
}: {
  filepath: string;
  threadId: string;
  isMock?: boolean;
}) {
  let enhancedFilepath = filepath;
  if (filepath.endsWith(".skill")) {
    enhancedFilepath = filepath + "/SKILL.md";
  }
  const url = urlOfArtifact({ filepath: enhancedFilepath, threadId, isMock });
  const response = await fetch(url, {
    credentials: "include",
    timeoutMs: DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS,
  });
  if (!response.ok) {
    throw new ArtifactRequestError(
      response.status,
      await readArtifactError(response),
    );
  }
  const text = await response.text();
  return { content: text, url };
}

export function loadArtifactContentFromToolCall({
  url: urlString,
  thread,
}: {
  url: string;
  thread: BaseStream<AgentThreadState>;
}) {
  const draftContent = buildWriteFileDraftContent({
    filepath: urlString,
    messages: thread.messages,
  });
  if (draftContent !== undefined) {
    return draftContent;
  }

  const url = new URL(urlString);
  const toolCallId = url.searchParams.get("tool_call_id");
  const messageId = url.searchParams.get("message_id");
  if (messageId && toolCallId) {
    const message = thread.messages.find((message) => message.id === messageId);
    if (message?.type === "ai" && message.tool_calls) {
      const toolCall = message.tool_calls.find(
        (toolCall) => toolCall.id === toolCallId,
      );
      if (toolCall) {
        return toolCall.args.content;
      }
    }
  }
}
