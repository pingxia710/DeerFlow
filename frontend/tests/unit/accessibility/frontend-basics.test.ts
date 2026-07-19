import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), "utf-8");
}

test("shared focus styling remains visible in themes and forced colors", () => {
  const styles = source("src/styles/globals.css");

  expect(styles).not.toContain("--ring: transparent");
  expect(styles).toContain("@media (forced-colors: active)");
  expect(styles).toContain("outline: 2px solid CanvasText");
});

test("composer and authentication forms retain their accessibility links", () => {
  const inputBox = source("src/components/workspace/input-box.tsx");
  const login = source("src/app/(auth)/login/page.tsx");
  const setup = source("src/app/(auth)/setup/page.tsx");

  expect(inputBox).toContain("aria-label={t.inputBox.placeholder}");
  expect(inputBox).toContain("aria-controls={skillSuggestionListId}");
  expect(inputBox).toContain("aria-activedescendant");
  expect(inputBox).toContain('role="listbox"');
  expect(login).toContain('id="login-error"');
  expect(login).toContain('role="alert"');
  expect(login).toContain("aria-busy={loading}");
  expect(setup).toContain('htmlFor="current-password"');
  expect(setup).toContain('id="change-password-error"');
  expect(setup).toContain(
    'aria-describedby={error ? "change-password-error" : undefined}',
  );
});
