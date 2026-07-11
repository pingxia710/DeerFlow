import {
  DEFAULT_LOCAL_SETTINGS,
  LOCAL_SETTINGS_KEY,
  THREAD_MODEL_KEY_PREFIX,
  clearThreadModelNames,
  getLocalSettings,
  getThreadModelName,
  migrateThreadModelName as migrateStoredThreadModelName,
  parseThreadModelStorageKey,
  saveLocalSettings,
  saveThreadModelName,
  type LocalSettings,
  type SettingsScope,
} from "./local";

type Listener = () => void;

export type LocalSettingsSetter = <K extends keyof LocalSettings>(
  key: K,
  value: Partial<LocalSettings[K]>,
) => void;

const listeners = new Set<Listener>();
const threadModelNames = new Map<string, string | undefined>();

let baseSettings: LocalSettings = DEFAULT_LOCAL_SETTINGS;
let baseSettingsLoaded = false;
let storageListenerRegistered = false;

function modelCacheKey(scope: SettingsScope, threadId: string) {
  return JSON.stringify([scope, threadId]);
}

function emitChange() {
  for (const listener of listeners) listener();
}

function ensureBaseSettingsLoaded() {
  if (baseSettingsLoaded || typeof window === "undefined") return;
  baseSettings = getLocalSettings();
  baseSettingsLoaded = true;
}

function ensureStorageListenerRegistered() {
  if (storageListenerRegistered || typeof window === "undefined") return;
  window.addEventListener("storage", handleStorage);
  storageListenerRegistered = true;
}

function mergeSettingsSection<K extends keyof LocalSettings>(
  settings: LocalSettings,
  key: K,
  value: Partial<LocalSettings[K]>,
): LocalSettings {
  return {
    ...settings,
    [key]: { ...settings[key], ...value },
  } as LocalSettings;
}

function handleStorage(event: StorageEvent) {
  if (event.storageArea && event.storageArea !== localStorage) return;
  ensureBaseSettingsLoaded();

  if (event.key === null) {
    baseSettings = getLocalSettings();
    threadModelNames.clear();
    emitChange();
    return;
  }
  if (event.key === LOCAL_SETTINGS_KEY) {
    baseSettings = getLocalSettings();
    emitChange();
    return;
  }

  const identity = parseThreadModelStorageKey(event.key);
  if (identity) {
    threadModelNames.set(
      modelCacheKey(identity.scope, identity.threadId),
      getThreadModelName(identity.scope, identity.threadId),
    );
    emitChange();
    return;
  }

  if (!event.key.startsWith(THREAD_MODEL_KEY_PREFIX)) return;
  const threadId = event.key.slice(THREAD_MODEL_KEY_PREFIX.length);
  let changed = false;
  for (const [key] of threadModelNames) {
    const [scope, cachedThreadId] = JSON.parse(key) as [SettingsScope, string];
    if (cachedThreadId !== threadId) continue;
    // A legacy event is relevant only while this scope has no scoped value.
    const next = getThreadModelName(scope, threadId);
    if (threadModelNames.get(key) !== next) {
      threadModelNames.set(key, next);
      changed = true;
    }
  }
  if (changed) emitChange();
}

export function subscribe(listener: Listener): () => void {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getBaseSettingsSnapshot(): LocalSettings {
  ensureBaseSettingsLoaded();
  return baseSettings;
}

export function getThreadModelSnapshot(
  scope: SettingsScope,
  threadId: string,
): string | undefined {
  ensureBaseSettingsLoaded();
  const key = modelCacheKey(scope, threadId);
  if (!threadModelNames.has(key)) {
    threadModelNames.set(key, getThreadModelName(scope, threadId));
  }
  return threadModelNames.get(key);
}

export const updateLocalSettings: LocalSettingsSetter = (key, value) => {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();
  baseSettings = mergeSettingsSection(baseSettings, key, value);
  saveLocalSettings(baseSettings);
  emitChange();
};

export function updateThreadSettings<K extends keyof LocalSettings>(
  scope: SettingsScope,
  threadId: string,
  key: K,
  value: Partial<LocalSettings[K]>,
) {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();
  baseSettings = mergeSettingsSection(baseSettings, key, value);
  saveLocalSettings(baseSettings);

  if (
    key === "context" &&
    Object.prototype.hasOwnProperty.call(value, "model_name")
  ) {
    const threadModelName = (value as Partial<LocalSettings["context"]>)
      .model_name;
    threadModelNames.set(modelCacheKey(scope, threadId), threadModelName);
    saveThreadModelName(scope, threadId, threadModelName);
  }
  emitChange();
}

export function migrateThreadModelName(
  scope: SettingsScope,
  fromThreadId: string,
  toThreadId: string,
) {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();
  migrateStoredThreadModelName(scope, fromThreadId, toThreadId);
  threadModelNames.delete(modelCacheKey(scope, fromThreadId));
  threadModelNames.delete(modelCacheKey(scope, toThreadId));
  emitChange();
}

export function clearThreadModelName(threadId: string) {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();
  clearThreadModelNames(threadId);
  for (const key of [...threadModelNames.keys()]) {
    const [, cachedThreadId] = JSON.parse(key) as [SettingsScope, string];
    if (cachedThreadId === threadId) threadModelNames.delete(key);
  }
  emitChange();
}
