import { expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

test("header context shows complete input character count instead of token estimate", async () => {
  rs.resetModules();
  rs.doMock("@/core/i18n/hooks", () => ({
    useI18n: () => ({
      t: {
        contextUsage: {
          label: "上下文",
          title: "上下文",
          estimated: "估算",
          messages: "消息",
          tools: "工具",
          chars: "字符",
          charUnit: "字",
          byCaller: "来源",
          unavailable: "暂无",
        },
        tokenUsage: {
          label: "Token",
          total: "总计",
          title: "Token",
          input: "输入",
          output: "输出",
          view: "视图",
          note: "",
          callerLeadAgent: "主 AI",
          callerLeadAgentShort: "主 AI",
          callerSubagent: "子 AI",
          callerMiddleware: "中间件",
          callerBreakdown: "来源",
          presets: {
            off: "关闭",
            summary: "汇总",
            perTurn: "逐轮",
            debug: "调试",
          },
          presetDescriptions: {
            off: "",
            summary: "",
            perTurn: "",
            debug: "",
          },
        },
      },
    }),
  }));

  const { TokenUsageIndicator } =
    await import("@/components/workspace/token-usage-indicator");
  const snapshot = {
    run_id: "run-1",
    caller: "lead_agent",
    llm_call_index: 2,
    message_count: 4,
    tool_schema_count: 1,
    char_count: 12_345,
    estimated_tokens: 7,
    role_counts: { system: 1, human: 1, ai: 1, tool: 1 },
    has_full_text: true,
    seq: 5,
    created_at: "2026-07-13T10:00:00+00:00",
  };

  const queryClient = new QueryClient();
  const html = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(TokenUsageIndicator, {
        threadId: "thread-1",
        messages: [],
        backendUsage: {
          inputTokens: 111,
          outputTokens: 888,
          totalTokens: 999,
        },
        contextUsage: {
          thread_id: "thread-1",
          latest: snapshot,
          latest_lead: snapshot,
          by_caller: { lead_agent: snapshot },
          recent: [snapshot],
        },
        enabled: true,
        preferences: { headerTotal: true, inlineMode: "off" },
        onPreferencesChange: () => undefined,
      }),
    ),
  );

  expect(html).toContain("1.2万");
  expect(html).not.toContain(">7<");
  expect(html).not.toContain(">999<");
});

test("context payload view renders the complete text without head-tail shortening", async () => {
  const indicatorModule =
    await import("@/components/workspace/token-usage-indicator");
  const ContextPayloadSection = (
    indicatorModule as typeof indicatorModule & {
      ContextPayloadSection: (props: {
        title: string;
        values: unknown[];
      }) => ReactNode;
    }
  ).ContextPayloadSection;
  const fullText = `START-${"x".repeat(20_000)}-END`;

  expect(typeof ContextPayloadSection).toBe("function");
  const html = renderToStaticMarkup(
    createElement(ContextPayloadSection, {
      title: "完整消息",
      values: [{ role: "tool", name: "task", content: fullText }],
    }),
  );

  expect(html).toContain(fullText);
});
