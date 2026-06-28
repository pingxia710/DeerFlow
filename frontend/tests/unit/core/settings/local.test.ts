import { expect, test } from "@rstest/core";

import { DEFAULT_LOCAL_SETTINGS } from "@/core/settings/local";

test("defaults token usage to header total plus per-turn breakdown", () => {
  expect(DEFAULT_LOCAL_SETTINGS.tokenUsage).toEqual({
    headerTotal: true,
    inlineMode: "per_turn",
  });
});

test("defaults new chat mode to ultra", () => {
  expect(DEFAULT_LOCAL_SETTINGS.context.mode).toBe("ultra");
});
