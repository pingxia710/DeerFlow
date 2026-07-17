import { expect, test } from "@rstest/core";

import { formatDateTime, formatDuration } from "@/core/utils/datetime";

test("formatDuration uses MM:SS and caps at the subtask limit", () => {
  expect(formatDuration(3_599_000)).toBe("59:59");
  expect(formatDuration(3_600_000)).toBe("60:00");
  expect(formatDuration(3_723_000)).toBe("60:00");
  expect(formatDuration(-1)).toBeNull();
});

test("formatDateTime rejects invalid timestamps", () => {
  expect(formatDateTime("not-a-date", "en-US")).toBeNull();
});
