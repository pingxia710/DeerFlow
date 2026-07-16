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

test("does not invent a plan section for ordinary tasks", () => {
  const task = {
    ...planTask("package-a", false),
    containerArtifactKind: "planning-forward" as const,
  };
  const markup = renderPlans([task]);

  expect(markup).not.toContain("1 plan");
  expect(markup).not.toContain("Confirm execution");
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
