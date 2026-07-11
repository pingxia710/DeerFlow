import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

test("ThreadRuntimeProvider clears process-local state on workspace teardown", () => {
  const source = readFileSync(
    resolve(process.cwd(), "src/core/threads/runtime.tsx"),
    "utf-8",
  );
  const providerSource = source.slice(
    source.indexOf("export function ThreadRuntimeProvider"),
  );

  expect(providerSource).toContain("clearAllThreadRuntimes();");
});

test("workspace provider clears thread and navigation singletons on teardown", () => {
  const source = readFileSync(
    resolve(process.cwd(), "src/app/workspace/workspace-client-providers.tsx"),
    "utf-8",
  );

  expect(source).toContain("clearAllThreadSingletonState();");
  expect(source).toContain("resetThreadChatNavigationIntent();");
});
