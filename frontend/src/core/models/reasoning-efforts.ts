import type { ReasoningEffort } from "../threads/types";

import type { Model } from "./types";

const LEGACY_REASONING_EFFORTS: ReasoningEffort[] = ["medium", "high", "xhigh"];

export function getModelReasoningEfforts(
  model: Model | undefined,
): readonly ReasoningEffort[] {
  if (!model?.supports_reasoning_effort) {
    return [];
  }
  return model.reasoning_efforts?.length
    ? model.reasoning_efforts
    : LEGACY_REASONING_EFFORTS;
}

export function resolveModelReasoningEffort(
  model: Model | undefined,
  requestedEffort: string | undefined,
): ReasoningEffort | undefined {
  const efforts = getModelReasoningEfforts(model);
  if (efforts.length === 0) {
    return undefined;
  }
  if (requestedEffort && efforts.includes(requestedEffort as ReasoningEffort)) {
    return requestedEffort as ReasoningEffort;
  }
  if (
    model?.default_reasoning_effort &&
    efforts.includes(model.default_reasoning_effort)
  ) {
    return model.default_reasoning_effort;
  }
  return efforts.at(-1);
}
