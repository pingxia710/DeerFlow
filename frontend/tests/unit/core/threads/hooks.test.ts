import { expect, test } from "@rstest/core";

test("resolveAssistantId uses the custom agent name when present", async () => {
  const { resolveAssistantId } = await import("@/core/threads/hooks");

  expect(resolveAssistantId("command-room")).toBe("command-room");
});

test("resolveAssistantId falls back to the default lead agent", async () => {
  const { resolveAssistantId } = await import("@/core/threads/hooks");

  expect(resolveAssistantId(undefined)).toBe("lead_agent");
});

test("buildThreadRunContext forces command-room into ultra subagent mode", async () => {
  const { buildThreadRunContext } = await import("@/core/threads/hooks");

  expect(
    buildThreadRunContext(
      {
        agent_name: "command-room",
        model_name: "safe-model",
        mode: "flash",
        reasoning_effort: "xhigh",
        reasoning_summary: "concise",
        text_verbosity: "high",
      },
      "thread-123",
    ),
  ).toMatchObject({
    agent_name: "command-room",
    model_name: "safe-model",
    mode: "ultra",
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: true,
    reasoning_effort: "xhigh",
    reasoning_summary: "concise",
    text_verbosity: "high",
    thread_id: "thread-123",
  });
});

test("buildThreadRunContext defaults ordinary thinking chat to xhigh", async () => {
  const { buildThreadRunContext } = await import("@/core/threads/hooks");

  expect(
    buildThreadRunContext(
      {
        model_name: "safe-model",
        mode: "pro",
      },
      "thread-456",
    ),
  ).toMatchObject({
    model_name: "safe-model",
    mode: "pro",
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: false,
    reasoning_effort: "xhigh",
    thread_id: "thread-456",
  });
});

test("buildThreadRunContext normalizes retired low reasoning effort to xhigh", async () => {
  const { buildThreadRunContext } = await import("@/core/threads/hooks");

  expect(
    buildThreadRunContext(
      {
        model_name: "safe-model",
        mode: "thinking",
        reasoning_effort: "low",
      },
      "thread-789",
    ),
  ).toMatchObject({
    reasoning_effort: "xhigh",
  });
});
