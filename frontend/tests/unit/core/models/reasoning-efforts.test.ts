import { expect, test } from "@rstest/core";

import {
  getModelReasoningEfforts,
  resolveModelReasoningEffort,
} from "@/core/models/reasoning-efforts";
import type { Model } from "@/core/models/types";

const terra: Model = {
  id: "terra",
  name: "terra",
  model: "gpt-5.6-terra",
  display_name: "GPT-5.6 Terra",
  supports_reasoning_effort: true,
  reasoning_efforts: ["medium", "high", "xhigh", "max"],
  default_reasoning_effort: "max",
};

test("uses exactly the reasoning efforts published by the selected model", () => {
  expect(getModelReasoningEfforts(terra)).toEqual([
    "medium",
    "high",
    "xhigh",
    "max",
  ]);
  expect(resolveModelReasoningEffort(terra, "high")).toBe("high");
});

test("normalizes stale and removed provider values to the model default", () => {
  expect(resolveModelReasoningEffort(terra, "ultra")).toBe("max");
  expect(resolveModelReasoningEffort(terra, "low")).toBe("max");
});

test("keeps the existing three-level picker for legacy models", () => {
  const legacy: Model = {
    id: "legacy",
    name: "legacy",
    model: "legacy-model",
    display_name: "Legacy",
    supports_reasoning_effort: true,
  };

  expect(getModelReasoningEfforts(legacy)).toEqual(["medium", "high", "xhigh"]);
  expect(resolveModelReasoningEffort(legacy, undefined)).toBe("xhigh");
});
