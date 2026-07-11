import { afterEach, expect, rs, test } from "@rstest/core";
import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

test("workspace provider creates a fresh query cache after it remounts", async () => {
  rs.resetModules();
  rs.doMock("@/core/auth/AuthProvider", () => ({
    useAuth: () => ({ user: { id: "user-a" } }),
  }));
  const { QueryClientProvider } =
    await import("@/components/query-client-provider");
  const clients: QueryClient[] = [];

  function CaptureClient() {
    clients.push(useQueryClient());
    return null;
  }

  for (let index = 0; index < 2; index += 1) {
    renderToStaticMarkup(
      createElement(QueryClientProvider, null, createElement(CaptureClient)),
    );
  }

  expect(clients).toHaveLength(2);
  expect(clients[0]).not.toBe(clients[1]);
});

afterEach(() => {
  rs.doUnmock("react");
  rs.doUnmock("@tanstack/react-query");
  rs.doUnmock("@/core/auth/AuthProvider");
  rs.resetModules();
});

test("workspace provider replaces and clears its cache when the authenticated user changes", async () => {
  rs.resetModules();
  let currentUserId = "user-a";
  let memoValue: unknown;
  let memoDeps: readonly unknown[] | undefined;
  let effectCleanup: (() => void) | undefined;
  let effectDeps: readonly unknown[] | undefined;

  const sameDeps = (
    left: readonly unknown[] | undefined,
    right: readonly unknown[],
  ) =>
    left?.length === right.length &&
    left.every((value, index) => Object.is(value, right[index]));

  rs.doMock("react", () => ({
    useMemo<T>(factory: () => T, deps: readonly unknown[]) {
      if (!sameDeps(memoDeps, deps)) {
        memoValue = factory();
        memoDeps = deps;
      }
      return memoValue as T;
    },
    useEffect(effect: () => void | (() => void), deps: readonly unknown[]) {
      if (!sameDeps(effectDeps, deps)) {
        effectCleanup?.();
        effectCleanup = effect() ?? undefined;
        effectDeps = deps;
      }
    },
    useState<T>(factory: T | (() => T)) {
      if (memoValue === undefined) {
        memoValue =
          typeof factory === "function" ? (factory as () => T)() : factory;
      }
      return [memoValue as T, rs.fn()] as const;
    },
  }));

  const clients: Array<{
    cancelQueries: ReturnType<typeof rs.fn>;
    clear: ReturnType<typeof rs.fn>;
  }> = [];
  rs.doMock("@tanstack/react-query", () => ({
    QueryClient: class {
      cancelQueries = rs.fn();
      clear = rs.fn();

      constructor() {
        clients.push(this);
      }
    },
    QueryClientProvider: "query-client-provider",
  }));
  rs.doMock("@/core/auth/AuthProvider", () => ({
    useAuth: () => ({ user: { id: currentUserId } }),
  }));

  const { QueryClientProvider: Provider } =
    await import("@/components/query-client-provider");

  const firstBoundary = Provider({ children: null });
  currentUserId = "user-b";
  const secondBoundary = Provider({ children: null });

  expect(clients).toHaveLength(2);
  expect(clients[0]?.cancelQueries).toHaveBeenCalledTimes(1);
  expect(clients[0]?.clear).toHaveBeenCalledTimes(1);
  expect(firstBoundary.key).toBe("user-a");
  expect(secondBoundary.key).toBe("user-b");
});
