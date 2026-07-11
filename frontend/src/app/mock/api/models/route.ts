export function GET() {
  return Response.json({
    models: [
      {
        id: "doubao-seed-1.8",
        name: "doubao-seed-1.8",
        provider: "Volcengine",
        model: "doubao-seed-1-8",
        display_name: "Doubao Seed 1.8",
        supports_thinking: true,
      },
      {
        id: "deepseek-v3.2",
        name: "deepseek-v3.2",
        provider: "DeepSeek",
        model: "deepseek-chat",
        display_name: "DeepSeek v3.2",
        supports_thinking: true,
      },
      {
        id: "gpt-5",
        name: "gpt-5",
        provider: "OpenAI",
        model: "gpt-5",
        display_name: "GPT-5",
        supports_thinking: true,
      },
      {
        id: "gemini-3-pro",
        name: "gemini-3-pro",
        provider: "Google",
        model: "gemini-3-pro",
        display_name: "Gemini 3 Pro",
        supports_thinking: true,
      },
    ],
  });
}
