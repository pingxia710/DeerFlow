import { expect, test } from "@rstest/core";

import { formatDateTime, formatDuration } from "@/core/utils/datetime";

test("formatDuration keeps elapsed time unambiguous", () => {
  expect(formatDuration(3_723_000)).toBe("01:02:03");
  expect(formatDuration(-1)).toBeNull();
});

test("formatDateTime rejects invalid timestamps", () => {
  expect(formatDateTime("not-a-date", "en-US")).toBeNull();
});
