import type { TokenUsageInlineMode } from "../messages/usage-model";
import type {
  AgentThreadContext,
  ReasoningEffort,
  ReasoningSummary,
  TextVerbosity,
} from "../threads";

export const DEFAULT_LOCAL_SETTINGS: LocalSettings = {
  notification: {
    enabled: true,
  },
  tokenUsage: {
    headerTotal: true,
    inlineMode: "per_turn",
  },
  context: {
    model_name: undefined,
    mode: "ultra",
    reasoning_effort: undefined,
    reasoning_summary: undefined,
    text_verbosity: undefined,
  },
};

export const LOCAL_SETTINGS_KEY = "deerflow.local-settings";
export const THREAD_MODEL_KEY_PREFIX = "deerflow.thread-model.";
export const SCOPED_THREAD_MODEL_KEY_PREFIX = "deerflow.scoped-thread-model.";

export type SettingsScope = "chat" | `agent:${string}`;

export interface ThreadModelStorageIdentity {
  scope: SettingsScope;
  threadId: string;
}

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export interface LocalSettings {
  notification: {
    enabled: boolean;
  };
  tokenUsage: {
    headerTotal: boolean;
    inlineMode: TokenUsageInlineMode;
  };
  context: Omit<
    AgentThreadContext,
    | "thread_id"
    | "is_plan_mode"
    | "thinking_enabled"
    | "subagent_enabled"
    | "model_name"
    | "reasoning_effort"
    | "reasoning_summary"
    | "text_verbosity"
  > & {
    model_name?: string | undefined;
    mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
    reasoning_effort?: ReasoningEffort;
    reasoning_summary?: ReasoningSummary;
    text_verbosity?: TextVerbosity;
  };
}

function mergeLocalSettings(settings?: Partial<LocalSettings>): LocalSettings {
  return {
    ...DEFAULT_LOCAL_SETTINGS,
    context: {
      ...DEFAULT_LOCAL_SETTINGS.context,
      ...settings?.context,
    },
    tokenUsage: {
      ...DEFAULT_LOCAL_SETTINGS.tokenUsage,
      ...settings?.tokenUsage,
    },
    notification: {
      ...DEFAULT_LOCAL_SETTINGS.notification,
      ...settings?.notification,
    },
  };
}

function encodeStoragePart(value: string): string {
  return encodeURIComponent(value);
}

function decodeStoragePart(value: string): string | undefined {
  try {
    return decodeURIComponent(value);
  } catch {
    return undefined;
  }
}

export function getThreadModelStorageKey(
  scope: SettingsScope,
  threadId: string,
): string {
  return `${SCOPED_THREAD_MODEL_KEY_PREFIX}${encodeStoragePart(scope)}.${encodeStoragePart(threadId)}`;
}

export function parseThreadModelStorageKey(
  key: string,
): ThreadModelStorageIdentity | undefined {
  if (!key.startsWith(SCOPED_THREAD_MODEL_KEY_PREFIX)) {
    return undefined;
  }
  const encoded = key.slice(SCOPED_THREAD_MODEL_KEY_PREFIX.length);
  const separator = encoded.indexOf(".");
  if (separator < 0) {
    return undefined;
  }
  const scope = decodeStoragePart(encoded.slice(0, separator));
  const threadId = decodeStoragePart(encoded.slice(separator + 1));
  if (
    !scope ||
    !threadId ||
    (scope !== "chat" && !scope.startsWith("agent:"))
  ) {
    return undefined;
  }
  return { scope: scope as SettingsScope, threadId };
}

function getLegacyThreadModelStorageKey(threadId: string): string {
  return `${THREAD_MODEL_KEY_PREFIX}${threadId}`;
}

export function getThreadModelName(
  scope: SettingsScope,
  threadId: string,
): string | undefined {
  if (!isBrowser()) {
    return undefined;
  }
  const scopedValue = localStorage.getItem(
    getThreadModelStorageKey(scope, threadId),
  );
  if (scopedValue !== null) {
    return scopedValue;
  }
  return (
    localStorage.getItem(getLegacyThreadModelStorageKey(threadId)) ?? undefined
  );
}

export function saveThreadModelName(
  scope: SettingsScope,
  threadId: string,
  modelName: string | undefined,
) {
  if (!isBrowser()) {
    return;
  }
  const key = getThreadModelStorageKey(scope, threadId);
  if (!modelName) {
    localStorage.removeItem(key);
    return;
  }
  localStorage.setItem(key, modelName);
}

export function migrateThreadModelName(
  scope: SettingsScope,
  fromThreadId: string,
  toThreadId: string,
) {
  if (!isBrowser() || fromThreadId === toThreadId) {
    return;
  }
  const fromKey = getThreadModelStorageKey(scope, fromThreadId);
  const toKey = getThreadModelStorageKey(scope, toThreadId);
  const sourceValue = localStorage.getItem(fromKey);
  if (sourceValue !== null && localStorage.getItem(toKey) === null) {
    localStorage.setItem(toKey, sourceValue);
  }
  localStorage.removeItem(fromKey);
}

export function clearThreadModelNames(threadId: string) {
  if (!isBrowser()) {
    return;
  }
  const keysToRemove: string[] = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key && parseThreadModelStorageKey(key)?.threadId === threadId) {
      keysToRemove.push(key);
    }
  }
  for (const key of keysToRemove) {
    localStorage.removeItem(key);
  }
  localStorage.removeItem(getLegacyThreadModelStorageKey(threadId));
}

export function applyThreadModelOverride(
  settings: LocalSettings,
  threadModelName: string | undefined,
): LocalSettings {
  if (!threadModelName) {
    return settings;
  }
  return {
    ...settings,
    context: {
      ...settings.context,
      model_name: threadModelName,
    },
  };
}

export function getLocalSettings(): LocalSettings {
  if (!isBrowser()) {
    return DEFAULT_LOCAL_SETTINGS;
  }
  const json = localStorage.getItem(LOCAL_SETTINGS_KEY);
  try {
    if (json) {
      const settings = JSON.parse(json) as Partial<LocalSettings>;
      return mergeLocalSettings(settings);
    }
  } catch {}
  return DEFAULT_LOCAL_SETTINGS;
}

export function saveLocalSettings(settings: LocalSettings) {
  if (!isBrowser()) {
    return;
  }
  localStorage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify(settings));
}
