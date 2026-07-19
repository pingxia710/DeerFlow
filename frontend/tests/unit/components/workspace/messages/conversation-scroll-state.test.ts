import { expect, test } from "@rstest/core";

import { getFollowingState } from "@/components/workspace/messages/use-conversation-turn-scroll";

test("a real upward scroll past the threshold pauses following", () => {
  expect(
    getFollowingState({
      current: true,
      distanceFromBottom: 97,
      isProgrammatic: false,
    }),
  ).toBe(false);
});

test("programmatic bottom alignment does not pause following", () => {
  expect(
    getFollowingState({
      current: true,
      distanceFromBottom: 400,
      isProgrammatic: true,
    }),
  ).toBe(true);
});

test("returning to the live edge resumes following", () => {
  expect(
    getFollowingState({
      current: false,
      distanceFromBottom: 0,
      isProgrammatic: false,
    }),
  ).toBe(true);
});
