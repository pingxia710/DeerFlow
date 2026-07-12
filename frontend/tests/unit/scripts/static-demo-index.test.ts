import { expect, test } from "@rstest/core";

import { staticDemoIndexEntry } from "../../../scripts/static-demo-index.js";

test("static demo index keeps only metadata required for routing and channel UI", () => {
  const entry = staticDemoIndexEntry("thread-a", {
    created_at: "2026-07-10T00:00:00Z",
    context: { agent_name: "researcher" },
    metadata: {
      channel_source: {
        type: "im_channel",
        provider: "feishu",
        secret: "must-not-enter-index",
      },
      run_id: "must-not-enter-index",
    },
    values: {
      title: "Agent channel thread",
      messages: [{ type: "human", content: "private history" }],
      artifacts: ["private.txt"],
    },
  });

  expect(entry).toMatchObject({
    thread_id: "thread-a",
    metadata: {
      agent_name: "researcher",
      channel_source: { type: "im_channel", provider: "feishu" },
    },
    values: {
      title: "Agent channel thread",
      messages: [],
      artifacts: [],
    },
  });
  expect(JSON.stringify(entry)).not.toContain("must-not-enter-index");
  expect(JSON.stringify(entry)).not.toContain("private history");
});
