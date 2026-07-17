import { expect, test } from "@rstest/core";
import { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { CommandRoomTrajectory } from "@/components/workspace/messages/command-room-trajectory";
import { I18nProvider } from "@/core/i18n/context";
import type { Subtask } from "@/core/tasks";
import {
  buildCommandRoomTrajectory,
  groupCommandRoomTrajectoryByWorkPackage,
  splitCommandRoomTrajectory,
} from "@/core/threads/command-room-read-model";

const I18nProviderForCreateElement = I18nProvider as ComponentType<
  Omit<Parameters<typeof I18nProvider>[0], "children">
>;

function planTask(workPackageId: string, containerArtifactWritten?: boolean) {
  return {
    id: `${workPackageId}-plan`,
    commandRoomContainer: "planning" as const,
    containerArtifactKind: "spec" as const,
    description: "Recorded plan",
    prompt: "",
    runId: `${workPackageId}-run`,
    startedAt: 1,
    status: "completed" as const,
    subagent_type: "recorder",
    threadId: "thread-1",
    workPackageId,
    ...(containerArtifactWritten === undefined
      ? {}
      : { containerArtifactWritten }),
  };
}

function renderPlans(tasks: Subtask[]) {
  return renderToStaticMarkup(
    createElement(
      I18nProviderForCreateElement,
      { initialLocale: "en-US" },
      createElement(CommandRoomTrajectory, {
        chairMessages: [],
        onNavigate: () => undefined,
        tasks,
      }),
    ),
  );
}

test("renders a plan navigation entry without adding an approval control", () => {
  const task = planTask("package-a", true);
  const markup = renderPlans([task]);

  expect(markup).toContain("Plan proposal");
  expect(markup).toContain("1 plan");
  expect(markup).not.toContain("Confirm execution");
});

test("shows an explicit planning task as planning analysis", () => {
  const task = {
    ...planTask("package-a", false),
    containerArtifactKind: "planning-forward" as const,
  };
  const markup = renderPlans([task]);

  expect(markup).toContain("Planning analysis");
  expect(markup).toContain("1 plan");
  expect(markup).not.toContain("Confirm execution");
});

test("shows elapsed duration for a running task", () => {
  const markup = renderPlans([
    {
      id: "running-task",
      description: "Long-running implementation",
      durationMs: 3_723_000,
      prompt: "",
      runId: "run-running",
      startedAt: 1,
      status: "in_progress",
      subagent_type: "executor",
      threadId: "thread-1",
    },
  ]);

  expect(markup).toContain("Running");
  expect(markup).toContain("60:00");
});

test("orders navigation newest first and exposes execution and review tasks", () => {
  const markup = renderPlans([
    { ...planTask("old-plan"), startedAt: 1, subagent_type: "old-recorder" },
    { ...planTask("new-plan"), startedAt: 2, subagent_type: "new-recorder" },
    {
      id: "execution-1",
      commandRoomContainer: "execution",
      deliveryCycleIndex: 1,
      description: "Implement change",
      prompt: "",
      runId: "run-execution",
      startedAt: 3,
      status: "completed",
      subagent_type: "executor",
      threadId: "thread-1",
      workPackageId: "delivery",
    },
    {
      id: "review-1",
      commandRoomContainer: "review",
      deliveryCycleIndex: 1,
      description: "Inspect change",
      prompt: "",
      runId: "run-review",
      startedAt: 4,
      status: "completed",
      subagent_type: "reviewer",
      threadId: "thread-1",
      workPackageId: "delivery",
    },
    {
      id: "review-without-cycle",
      commandRoomContainer: "review",
      description: "Review without cycle",
      prompt: "",
      runId: "run-review-without-cycle",
      startedAt: 5,
      status: "completed",
      subagent_type: "reviewer",
      threadId: "thread-1",
      workPackageId: "delivery",
    },
  ]);

  expect(markup.indexOf("new-recorder")).toBeLessThan(
    markup.indexOf("old-recorder"),
  );
  const ungroupedReview = markup.indexOf("Review · Review without cycle");
  const cycle = markup.indexOf("Delivery cycle 1");
  const cycleReview = markup.indexOf("Review · Inspect change");
  const cycleExecution = markup.indexOf("Execution · Implement change");
  expect(ungroupedReview).toBeGreaterThan(-1);
  expect(ungroupedReview).toBeLessThan(cycle);
  expect(cycle).toBeLessThan(cycleReview);
  expect(cycleReview).toBeLessThan(cycleExecution);
  expect(markup).not.toContain("Recent tasks");
});

test("keeps plan projections separated by work package", () => {
  const packages = groupCommandRoomTrajectoryByWorkPackage(
    buildCommandRoomTrajectory([
      planTask("package-a", true),
      planTask("package-b", false),
    ]),
  );

  expect(packages.map((workPackage) => workPackage.workPackageId)).toEqual([
    "package-a",
    "package-b",
  ]);
  expect(
    packages.map(
      (workPackage) =>
        splitCommandRoomTrajectory(workPackage.steps).planProposals[0]?.tasks[0]
          ?.id,
    ),
  ).toEqual(["package-a-plan", "package-b-plan"]);
});
