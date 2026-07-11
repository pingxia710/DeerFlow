import { expect, test } from "@rstest/core";

import { canManageSkills } from "@/components/workspace/settings/skill-settings-state";

test("only administrators outside static mode can mutate skills", () => {
  expect(canManageSkills("admin", false)).toBe(true);
  expect(canManageSkills("user", false)).toBe(false);
  expect(canManageSkills(null, false)).toBe(false);
  expect(canManageSkills("admin", true)).toBe(false);
});
