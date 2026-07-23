import type { Model } from "@/core/models/types";
import type { Skill } from "@/core/skills";

export type InputMode = "flash" | "thinking" | "pro" | "ultra";

const MAX_SKILL_SUGGESTIONS = 6;
const SUGGESTION_TEMPLATE_PLACEHOLDER_PATTERN =
  /\[(?:主题|来源|topic|source)\]/i;

export function getCompactModelLabel(
  displayName: string | undefined,
  modelName: string | undefined,
) {
  const raw = displayName ?? modelName ?? "";
  const compact = raw
    .replace(/\s*\([^)]*\)\s*$/u, "")
    .replace(/^GPT-/iu, "")
    .trim();

  return compact || raw;
}

export function getModelProviderLabel(
  model: Model,
  fallbackProviderLabel: string,
) {
  const provider = model.provider?.trim();
  if (provider) {
    return provider;
  }
  if (/^gpt-5\.\d+/u.test(model.model)) {
    return "Codex CLI";
  }
  return fallbackProviderLabel;
}

export function groupModelsByProvider(
  models: Model[],
  fallbackProviderLabel: string,
) {
  const groups = new Map<string, Model[]>();
  for (const model of models) {
    const provider = getModelProviderLabel(model, fallbackProviderLabel);
    const providerModels = groups.get(provider);
    if (providerModels) {
      providerModels.push(model);
    } else {
      groups.set(provider, [model]);
    }
  }
  return Array.from(groups, ([provider, providerModels]) => ({
    provider,
    models: providerModels,
  }));
}

export function findSuggestionTemplatePlaceholder(text: string) {
  const match = SUGGESTION_TEMPLATE_PLACEHOLDER_PATTERN.exec(text);
  if (!match) {
    return null;
  }

  return {
    start: match.index,
    end: match.index + match[0].length,
  };
}

export function getLeadingSlashSkillQuery(value: string): string | null {
  if (!value.startsWith("/")) {
    return null;
  }

  const query = value.slice(1);
  if (query.includes("/") || /\s/.test(query)) {
    return null;
  }

  return query;
}

export function getMatchingSkillSuggestions(
  skills: Skill[],
  query: string,
): Skill[] {
  const normalizedQuery = query.toLowerCase();

  return skills
    .map((skill, index) => ({
      skill,
      index,
      name: skill.name.toLowerCase(),
    }))
    .filter(({ skill, name }) => {
      if (!skill.enabled) {
        return false;
      }
      return !normalizedQuery || name.includes(normalizedQuery);
    })
    .sort((a, b) => {
      const aStartsWith = a.name.startsWith(normalizedQuery);
      const bStartsWith = b.name.startsWith(normalizedQuery);
      if (aStartsWith !== bStartsWith) {
        return aStartsWith ? -1 : 1;
      }
      return a.index - b.index;
    })
    .slice(0, MAX_SKILL_SUGGESTIONS)
    .map(({ skill }) => skill);
}

export function getResolvedMode(
  mode: InputMode | undefined,
  supportsThinking: boolean,
): InputMode {
  if (!supportsThinking && mode !== "flash") {
    return "flash";
  }
  if (mode) {
    return mode;
  }
  return supportsThinking ? "ultra" : "flash";
}
