import type { ReasoningEffort } from "../threads/types";

export interface Model {
  id: string;
  name: string;
  provider?: string | null;
  model: string;
  display_name: string;
  description?: string | null;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
  reasoning_efforts?: readonly ReasoningEffort[];
  default_reasoning_effort?: ReasoningEffort;
}

export interface TokenUsageSettings {
  enabled: boolean;
}

export interface ModelsResponse {
  models: Model[];
  token_usage: TokenUsageSettings;
}
