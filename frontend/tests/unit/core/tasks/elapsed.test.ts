import { expect, test } from "@rstest/core";

import { formatElapsedMinutesSeconds } from "@/core/tasks/elapsed";

test("formatElapsedMinutesSeconds formats elapsed seconds as minutes and seconds", () => {
  expect(formatElapsedMinutesSeconds(0)).toBe("0:00");
  expect(formatElapsedMinutesSeconds(9)).toBe("0:09");
  expect(formatElapsedMinutesSeconds(75)).toBe("1:15");
  expect(formatElapsedMinutesSeconds(-1)).toBe("0:00");
});
