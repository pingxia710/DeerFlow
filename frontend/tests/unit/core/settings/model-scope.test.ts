import { afterEach, beforeEach, expect, test, rs } from "@rstest/core";

function makeLocalStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: rs.fn(() => values.clear()),
    getItem: rs.fn((key: string) => values.get(key) ?? null),
    key: rs.fn((index: number) => [...values.keys()][index] ?? null),
    removeItem: rs.fn((key: string) => values.delete(key)),
    setItem: rs.fn((key: string, value: string) => values.set(key, value)),
  } as Storage;
}

let storage: Storage;
let storageListener: ((event: StorageEvent) => void) | undefined;

beforeEach(() => {
  rs.resetModules();
  storage = makeLocalStorage();
  storageListener = undefined;
  rs.stubGlobal("localStorage", storage);
  rs.stubGlobal("window", {
    addEventListener: rs.fn(
      (type: string, listener: (event: StorageEvent) => void) => {
        if (type === "storage") storageListener = listener;
      },
    ),
    localStorage: storage,
  });
});

afterEach(() => {
  rs.unstubAllGlobals();
});

test("same backend thread keeps chat and agent model overrides isolated", async () => {
  const { getThreadModelName, saveThreadModelName } =
    await import("@/core/settings/local");

  saveThreadModelName("chat", "thread:T/with:special", "model-A");
  saveThreadModelName("agent:command-room", "thread:T/with:special", "model-B");
  saveThreadModelName("agent:other/name", "thread:T/with:special", "model-C");

  expect(getThreadModelName("chat", "thread:T/with:special")).toBe("model-A");
  expect(
    getThreadModelName("agent:command-room", "thread:T/with:special"),
  ).toBe("model-B");
  expect(getThreadModelName("agent:other/name", "thread:T/with:special")).toBe(
    "model-C",
  );
  expect(storage.length).toBe(3);
});

test("legacy value migrates deterministically per scope and cannot overwrite scoped value", async () => {
  storage.setItem("deerflow.thread-model.T", "legacy-model");
  const { getThreadModelName, saveThreadModelName } =
    await import("@/core/settings/local");

  expect(getThreadModelName("chat", "T")).toBe("legacy-model");
  saveThreadModelName("chat", "T", "chat-model");
  expect(getThreadModelName("agent:command-room", "T")).toBe("legacy-model");

  storage.setItem("deerflow.thread-model.T", "new-legacy-model");
  expect(getThreadModelName("chat", "T")).toBe("chat-model");
});

test("new display id migrates only the matching scope", async () => {
  const { getThreadModelName, migrateThreadModelName, saveThreadModelName } =
    await import("@/core/settings/local");

  saveThreadModelName("chat", "new", "chat-model");
  saveThreadModelName("agent:command-room", "new", "agent-model");
  migrateThreadModelName("chat", "new", "backend-T");

  expect(getThreadModelName("chat", "backend-T")).toBe("chat-model");
  expect(getThreadModelName("chat", "new")).toBeUndefined();
  expect(getThreadModelName("agent:command-room", "backend-T")).toBeUndefined();
  expect(getThreadModelName("agent:command-room", "new")).toBe("agent-model");
});

test("thread cleanup removes every scoped key and legacy key only for that thread", async () => {
  const { clearThreadModelNames, getThreadModelName, saveThreadModelName } =
    await import("@/core/settings/local");

  saveThreadModelName("chat", "T", "chat-model");
  saveThreadModelName("agent:command-room", "T", "agent-model");
  saveThreadModelName("chat", "other", "other-model");
  storage.setItem("deerflow.thread-model.T", "legacy-model");

  clearThreadModelNames("T");

  expect(getThreadModelName("chat", "T")).toBeUndefined();
  expect(getThreadModelName("agent:command-room", "T")).toBeUndefined();
  expect(storage.getItem("deerflow.thread-model.T")).toBeNull();
  expect(getThreadModelName("chat", "other")).toBe("other-model");
});

test("storage events update only their scope and legacy cannot replace scoped cache", async () => {
  const { getThreadModelStorageKey } = await import("@/core/settings/local");
  const { getThreadModelSnapshot, subscribe } =
    await import("@/core/settings/store");
  const unsubscribe = subscribe(() => undefined);
  expect(storageListener).toBeDefined();

  const chatKey = getThreadModelStorageKey("chat", "T");
  const agentKey = getThreadModelStorageKey("agent:command-room", "T");
  storage.setItem(chatKey, "chat-model");
  storageListener?.({ key: chatKey, storageArea: storage } as StorageEvent);
  storage.setItem(agentKey, "agent-model");
  storageListener?.({ key: agentKey, storageArea: storage } as StorageEvent);

  expect(getThreadModelSnapshot("chat", "T")).toBe("chat-model");
  expect(getThreadModelSnapshot("agent:command-room", "T")).toBe("agent-model");

  storage.setItem("deerflow.thread-model.T", "legacy-model");
  storageListener?.({
    key: "deerflow.thread-model.T",
    storageArea: storage,
  } as StorageEvent);
  expect(getThreadModelSnapshot("chat", "T")).toBe("chat-model");
  expect(getThreadModelSnapshot("agent:command-room", "T")).toBe("agent-model");
  unsubscribe();
});
