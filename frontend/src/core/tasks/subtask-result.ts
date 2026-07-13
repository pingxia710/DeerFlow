import type { Message } from "@langchain/langgraph-sdk";

import type { Subtask } from "./types";

export type SubtaskStatus = Subtask["status"];

export interface SubtaskResultUpdate {
  status: SubtaskStatus;
  result?: string;
  error?: string;
}

/**
 * Structured-status keys the backend stamps onto
 * ``ToolMessage.additional_kwargs`` for every ``task`` tool result.
 *
 * The values mirror the Python contract in
 * ``backend/packages/harness/deerflow/subagents/status_contract.py``
 * (``SUBAGENT_STATUS_KEY`` / ``SUBAGENT_ERROR_KEY``). The cross-language
 * fixture at ``contracts/subagent_status_contract.json`` pins both sides
 * to the same values.
 */
export const SUBAGENT_STATUS_KEY = "subagent_status";
export const SUBAGENT_ERROR_KEY = "subagent_error";

/**
 * Map from the backend ``subagent_status`` value to the frontend
 * {@link SubtaskStatus} enum. The frontend collapses ``cancelled`` /
 * ``timed_out`` / ``polling_timed_out`` into ``failed`` because the
 * subtask card only renders three pill states. The richer backend
 * vocabulary still survives on ``error`` for tooling that wants the
 * detail.
 */
const STRUCTURED_STATUS_TO_SUBTASK: Record<string, SubtaskStatus> = {
  completed: "completed",
  failed: "failed",
  cancelled: "failed",
  timed_out: "failed",
  polling_timed_out: "failed",
};

/**
 * Legacy prefixes retained only for historical threads. Current successful
 * task content is the worker's complete natural-language result unchanged;
 * lifecycle status comes from `additional_kwargs.subagent_status`.
 */
export const SUCCESS_WITH_SUGGESTED_RECEIVER_PREFIX =
  "Task Succeeded. Suggested next receiver";
export const SUCCESS_WITH_RUNTIME_OBSERVED_EVIDENCE_PREFIX =
  "Task Succeeded. Runtime-observed evidence:";
export const SUCCESS_PREFIX = "Task Succeeded. Result:";
export const FAILURE_PREFIX = "Task failed.";
export const TIMEOUT_PREFIX = "Task timed out";
export const CANCELLED_PREFIX = "Task cancelled by user.";
export const POLLING_TIMEOUT_PREFIX = "Task polling timed out";
export const ERROR_WRAPPER_PATTERN = /^Error\b/i;

/**
 * Map a `task` tool result to a {@link SubtaskStatus}.
 *
 * Bytedance/deer-flow issue #3146: prefers the structured
 * ``additional_kwargs.subagent_status`` field the backend task tool now
 * stamps at its producer boundary. Falls back to the legacy prefix
 * matching for messages that pre-date the stamping commit (historical
 * threads, third-party clients, or any tool path that bypasses the
 * middleware). Both shapes converge on the same {@link SubtaskStatus}
 * vocabulary the card UI renders.
 *
 * When the structured field says `completed`, `content` is the result. The
 * legacy parser strips an old success prefix when present; otherwise the raw
 * text is preserved exactly.
 *
 * Returning `in_progress` is the **deliberate** fallback for content that
 * matches none of the known prefixes and carries no structured stamp.
 * LangChain only ever emits a `ToolMessage` once the tool itself has
 * returned (success or wrapped exception), so an unknown shape means
 * "the contract changed underneath us" — surfacing it as still-running
 * prompts the operator to investigate, where eagerly marking it
 * terminal-failed would mask the drift.
 */
export function parseSubtaskResult(
  text: string,
  additionalKwargs?: Record<string, unknown> | null,
): SubtaskResultUpdate {
  const fromText = parseFromText(text.trim());
  const structured = readStructuredStatus(additionalKwargs);
  if (!structured) {
    return fromText;
  }

  const update: SubtaskResultUpdate = { status: structured.status };
  // Structured `subagent_error` wins; otherwise inherit the text-derived
  // error only when both sides agree on the status (so a "Task Succeeded"
  // body can't bleed into a `failed` structured stamp and vice versa).
  if (structured.error) {
    update.error = structured.error;
  } else if (
    fromText.status === structured.status &&
    fromText.error !== undefined
  ) {
    update.error = fromText.error;
  }
  // A structured completed message uses the current raw-text contract. Legacy
  // prefix parsing is only for messages that have no structured status.
  if (structured.status === "completed") {
    update.result = text;
  }
  return update;
}

export function hasSubtaskToolResult(
  toolCallId: string | undefined,
  messages: Message[],
) {
  if (!toolCallId) {
    return false;
  }
  return messages.some(
    (message) => message.type === "tool" && message.tool_call_id === toolCallId,
  );
}

export function derivePendingSubtaskStatus(
  _toolCallId: string | undefined,
  _messages: Message[],
  _isCurrentTurnLoading: boolean,
): SubtaskStatus {
  // A task tool call without its own ToolMessage is not evidence of failure.
  // Command-room subtasks can keep running after the visible parent turn
  // pauses, reconnects, or switches away, so terminal status must come from
  // parseSubtaskResult's explicit backend result path.
  return "in_progress";
}

function parseFromText(trimmed: string): SubtaskResultUpdate {
  if (trimmed.startsWith(SUCCESS_WITH_SUGGESTED_RECEIVER_PREFIX)) {
    const resultMarker = "Result:";
    const resultIndex = trimmed.indexOf(resultMarker);
    return {
      status: "completed",
      result:
        resultIndex >= 0
          ? trimmed.slice(resultIndex + resultMarker.length).trim()
          : "",
    };
  }

  if (trimmed.startsWith(SUCCESS_WITH_RUNTIME_OBSERVED_EVIDENCE_PREFIX)) {
    const resultMarker = "Result:";
    const resultIndex = trimmed.indexOf(resultMarker);
    return {
      status: "completed",
      result:
        resultIndex >= 0
          ? trimmed.slice(resultIndex + resultMarker.length).trim()
          : "",
    };
  }

  if (trimmed.startsWith(SUCCESS_PREFIX)) {
    return {
      status: "completed",
      result: trimmed.slice(SUCCESS_PREFIX.length).trim(),
    };
  }

  if (trimmed.startsWith(FAILURE_PREFIX)) {
    return {
      status: "failed",
      error: trimmed.slice(FAILURE_PREFIX.length).trim(),
    };
  }

  if (trimmed.startsWith(TIMEOUT_PREFIX)) {
    return { status: "failed", error: trimmed };
  }

  if (trimmed.startsWith(CANCELLED_PREFIX)) {
    return { status: "failed", error: trimmed };
  }

  if (trimmed.startsWith(POLLING_TIMEOUT_PREFIX)) {
    return { status: "failed", error: trimmed };
  }

  // ToolErrorHandlingMiddleware-style wrapper, or any other terminal error
  // signal the backend forwards to the lead agent.
  if (ERROR_WRAPPER_PATTERN.test(trimmed)) {
    return { status: "failed", error: trimmed };
  }

  return { status: "in_progress" };
}

interface StructuredStatus {
  status: SubtaskStatus;
  error?: string;
}

function readStructuredStatus(
  additionalKwargs: Record<string, unknown> | null | undefined,
): StructuredStatus | null {
  if (!additionalKwargs) return null;
  const rawStatus = additionalKwargs[SUBAGENT_STATUS_KEY];
  if (typeof rawStatus !== "string") return null;
  const mapped = STRUCTURED_STATUS_TO_SUBTASK[rawStatus];
  if (mapped === undefined) {
    // Unknown future terminal status: surface a visible safe terminal state
    // instead of leaving the card permanently in_progress.
    return {
      status: "failed",
      error: `Unknown subagent_status from backend: ${rawStatus}`,
    };
  }
  const rawError = additionalKwargs[SUBAGENT_ERROR_KEY];
  const result: StructuredStatus = { status: mapped };
  if (typeof rawError === "string" && rawError.trim()) {
    result.error = rawError;
  }
  return result;
}
