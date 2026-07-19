import { expect, test } from "@rstest/core";

import {
  selectSubtaskArtifacts,
  subtaskArtifactReferences,
} from "@/components/workspace/messages/subtask-card";
import type { Subtask } from "@/core/tasks/types";

function taskFixture(overrides: Partial<Subtask> = {}): Subtask {
  return {
    id: "task-1",
    status: "completed",
    subagent_type: "executor",
    description: "Write report",
    prompt: "Write the report",
    ...overrides,
  };
}

test("collects only explicit artifact references", () => {
  const references = subtaskArtifactReferences(
    taskFixture({
      metadata: {
        refs: {
          artifact_refs: [
            "/mnt/user-data/outputs/report.md",
            { virtual_path: "/mnt/user-data/outputs/chart.csv" },
          ],
          output_refs: { output_ref: "/mnt/user-data/outputs/summary.json" },
        },
      },
      details: { refs: { evidence_refs: ["not-an-artifact-ref"] } },
    }),
  );

  expect(references).toEqual([
    "/mnt/user-data/outputs/report.md",
    "/mnt/user-data/outputs/chart.csv",
    "/mnt/user-data/outputs/summary.json",
  ]);
});

test("shows only accessible artifacts matched to the task or its declared references", () => {
  const task = taskFixture({
    metadata: {
      refs: { artifact_refs: ["/mnt/user-data/outputs/report.md"] },
    },
  });

  expect(
    selectSubtaskArtifacts(
      [
        {
          available: true,
          taskId: "task-1",
          virtualPath: "/mnt/user-data/outputs/report.md",
        },
        {
          available: true,
          taskId: "task-2",
          virtualPath: "/mnt/user-data/outputs/other.md",
        },
        {
          available: false,
          taskId: "task-1",
          virtualPath: "/mnt/user-data/outputs/missing.md",
        },
      ],
      task,
    ),
  ).toEqual({
    available: [
      {
        available: true,
        taskId: "task-1",
        virtualPath: "/mnt/user-data/outputs/report.md",
      },
    ],
    hasUnavailable: true,
  });
});

test("marks a temporary or missing reference unavailable instead of creating a link", () => {
  const task = taskFixture({
    metadata: {
      refs: { artifact_refs: ["/tmp/unfinished-report.md"] },
    },
  });

  expect(selectSubtaskArtifacts([], task)).toEqual({
    available: [],
    hasUnavailable: true,
  });
});

test("keeps an unindexed reference unavailable beside an available task artifact", () => {
  const availablePath = "/mnt/user-data/outputs/report.md";
  const missingPath = "/tmp/unfinished-report.md";
  const task = taskFixture({
    metadata: { refs: { artifact_refs: [missingPath] } },
  });

  expect(
    selectSubtaskArtifacts(
      [
        {
          available: true,
          taskId: "task-1",
          virtualPath: availablePath,
        },
      ],
      task,
    ),
  ).toEqual({
    available: [
      {
        available: true,
        taskId: "task-1",
        virtualPath: availablePath,
      },
    ],
    hasUnavailable: true,
  });
});
